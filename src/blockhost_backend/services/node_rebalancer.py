"""Node rebalancer — periodic background worker that optimises server placement.

Handles two scenarios:
1. **Overflow protection**: When a node's RAM usage exceeds a threshold,
   migrate its least-active server to the node with the most free capacity.
2. **Consolidation**: When multiple nodes are lightly loaded, pack servers
   onto fewer nodes so idle nodes can eventually be drained/shut down.

Runs as a daemon thread alongside the other background workers.
"""

from __future__ import annotations

from blockhost_backend.database.schema import utcnow
import logging
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Node, NodeState, Server, ServerState
from blockhost_backend.runtime.agent_runtime import AgentRuntime
from blockhost_backend.services.cloud.cloud_provider import get_cloud_provider
from blockhost_backend.services.migration_guards import (
    can_run_migration_now,
    mark_server_migrated,
    record_migration,
    server_in_migration_cooldown,
)
from blockhost_backend.services.node_capacity import is_node_heartbeat_fresh
from blockhost_backend.services.s3_storage import delete_migration_snapshot, require_s3_configured

logger = logging.getLogger(__name__)

# ── Tunable thresholds ────────────────────────────────────────────────
# If a node's RAM usage ratio exceeds this, try to shed its least-active server.
NODE_OVERLOADED_RATIO = 0.85  # 85%

# If ALL nodes are below this ratio, try to consolidate onto fewer nodes.
NODE_IDLE_RATIO = 0.30  # 30%

# Minimum seconds between rebalance cycles (prevents thrashing).
REBALANCE_INTERVAL_SECONDS = 120


def _node_ram_ratio(node: Node) -> float:
    """Return the fraction of RAM currently used (0.0 – 1.0)."""
    if node.total_ram_mb <= 0:
        return 1.0  # treat unconfigured nodes as full
    return node.used_ram_mb / node.total_ram_mb


def _node_active_ram_ratio(node: Node, db: Session) -> float:
    """Return the fraction of RAM currently used by active servers (running/provisioning)."""
    if node.total_ram_mb <= 0:
        return 1.0
    active_states = (ServerState.running, ServerState.provisioning, ServerState.syncing)
    active_servers = db.execute(
        select(Server).where(
            Server.node_id == node.id,
            Server.state.in_(active_states),
        )
    ).scalars().all()
    
    active_ram = 0
    from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits
    for s in active_servers:
        limits = get_effective_server_resource_limits(db=db, server=s)
        active_ram += limits["ram_mb"]
        
    return active_ram / node.total_ram_mb


def _agent_for_node(node: Node) -> AgentRuntime:
    token = get_settings().worker_agent_token
    return AgentRuntime(
        agent_base_url=f"http://{node.ip_address}:{node.agent_port}",
        agent_token=token,
    )


def _healthy_online_nodes(db: Session) -> list[Node]:
    """Return all online nodes with fresh heartbeats."""
    now = utcnow()
    nodes = db.execute(
        select(Node).where(Node.status == NodeState.online)
    ).scalars().all()
    return [n for n in nodes if is_node_heartbeat_fresh(n, now=now)]


def _running_servers_on_node(db: Session, node_id: uuid.UUID) -> list[Server]:
    return db.execute(
        select(Server).where(
            Server.node_id == node_id,
            Server.state == ServerState.running,
        ).order_by(Server.last_activity.asc())  # least-active first
    ).scalars().all()

def _all_servers_on_node(db: Session, node_id: uuid.UUID) -> list[Server]:
    return db.execute(
        select(Server).where(
            Server.node_id == node_id
        ).order_by(Server.last_activity.asc())
    ).scalars().all()


def _migrate_server(
    server: Server,
    from_node: Node,
    to_node: Node,
    db: Session,
) -> bool:
    """Execute a migration: stop → S3 backup → reassign → S3 restore → start.

    Returns True on success, False on any failure.
    """
    sid = str(server.id)
    was_running = (server.state == ServerState.running)

    try:
        require_s3_configured()
    except RuntimeError as e:
        logger.error("[rebalancer] Skipping migrate for %s: %s", sid, e)
        return False

    from_agent = _agent_for_node(from_node)
    to_agent = _agent_for_node(to_node)

    logger.info(
        "[rebalancer] Migrating server %s from %s → %s",
        sid, from_node.name, to_node.name,
    )

    # 1. Stop on old node
    try:
        from_agent.stop_server(sid)
    except Exception as e:
        logger.warning("[rebalancer] Stop failed for %s (proceeding): %s", sid, e)

    server.state = ServerState.suspended
    db.commit()

    # 2. Backup to S3
    try:
        from_agent.backup_to_s3(sid)
    except Exception as e:
        logger.error("[rebalancer] Backup failed for %s: %s", sid, e)
        return False

    # 3. Reassign
    old_node_id = server.node_id
    old_ip = server.vm_ipv4
    server.node_id = to_node.id
    server.vm_ipv4 = to_node.ip_address
    db.commit()

    # Invalidate routing cache
    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    get_server_lifecycle_orchestrator().invalidate_server(sid)

    # 4. Restore on new node
    try:
        to_agent.restore_from_s3(sid)
    except Exception as e:
        logger.error("[rebalancer] Restore failed for %s, rolling back: %s", sid, e)
        server.node_id = old_node_id
        server.vm_ipv4 = old_ip
        db.commit()
        get_server_lifecycle_orchestrator().invalidate_server(sid)
        return False

    # 5. Start on new node if it was running
    if was_running:
        try:
            from blockhost_backend.api.servers import _do_start_server
            _do_start_server(server, db)
            mark_server_migrated(db, server)
            db.commit()
        except Exception as e:
            logger.error("[rebalancer] Start failed for %s on new node: %s", sid, e)
            # Server data is on new node but not running — leave it for manual fix
            return False
    else:
        server.state = ServerState.suspended
        mark_server_migrated(db, server)
        db.commit()

    record_migration()
    delete_migration_snapshot(sid)

    # Drop orphaned world files on the source node (best-effort).
    try:
        from_agent.purge_server_data(sid)
    except Exception as e:
        logger.warning(
            "[rebalancer] Failed to purge source data for %s on %s: %s",
            sid, from_node.name, e,
        )

    logger.info(
        "[rebalancer] Migration complete: %s → %s (node %s)",
        sid, to_node.name, to_node.ip_address,
    )
    return True


def _pick_migratable_server(servers: list[Server]) -> Server | None:
    for server in servers:
        if server_in_migration_cooldown(server):
            logger.info(
                "[rebalancer] Skipping server %s — still in migration cooldown",
                server.id,
            )
            continue
        return server
    return None


def _rebalance_cycle() -> None:
    """Run one rebalance evaluation cycle."""
    with SessionLocal() as db:
        # ── Scenario 0: Auto-Shutdown Empty Nodes ────────────────────────
        # Shut down any node (healthy or not) that is online but has no servers.
        # This handles nodes that were recently emptied, or nodes with stale heartbeats.
        all_online_nodes = db.execute(
            select(Node).where(Node.status == NodeState.online)
        ).scalars().all()
        
        for node in all_online_nodes:
            servers = _all_servers_on_node(db, node.id)
            if not servers and node.provider and node.provider_instance_id:
                logger.info("[rebalancer] Auto-Shutdown: Node %s is completely empty. Shutting down.", node.name)
                try:
                    provider = get_cloud_provider(node.provider)
                    provider.stop_instance(node.provider_instance_id)
                    node.status = NodeState.offline
                    db.commit()
                except Exception as e:
                    logger.error("[rebalancer] Failed to shut down provider for node %s: %s", node.name, e)
                
                # Mark as offline so we don't attempt to shut it down again next cycle
                

        # Fetch only healthy nodes for the actual migrations
        nodes = _healthy_online_nodes(db)

        if not can_run_migration_now():
            return

        # ── Scenario 1: Evacuate "Empty" Nodes ──────────────────────────
        # If a node has 0 running servers but has suspended servers, migrate them
        # so the node can eventually be shut down.
        for node in nodes:
            running = _running_servers_on_node(db, node.id)
            if len(running) == 0:
                all_servers = _all_servers_on_node(db, node.id)
                if len(all_servers) > 0:
                    candidates = [n for n in nodes if n.id != node.id]
                    if not candidates:
                        continue
                    # Sort by used_ram_mb descending to pack tightly
                    target_node = sorted(candidates, key=lambda n: n.used_ram_mb, reverse=True)[0]
                    victim = _pick_migratable_server(all_servers)
                    if victim:
                        logger.info("[rebalancer] Evacuating suspended server from node %s with 0 running servers to %s", node.name, target_node.name)
                        # We skip RAM capacity checks because the server is suspended!
                        _migrate_server(victim, node, target_node, db)
                        return  # one migration per cycle

        # ── Scenario 2: Overflow Protection ──────────────────────────
        # Find overloaded nodes and shed their least-active server to
        # the node with the most free RAM.
        for node in nodes:
            ratio = _node_ram_ratio(node)
            if ratio < NODE_OVERLOADED_RATIO:
                continue

            logger.info(
                "[rebalancer] Node %s is overloaded (%.0f%% RAM used)",
                node.name, ratio * 100,
            )

            # Find the target node with the most free capacity
            best_target: Node | None = None
            best_free = -1
            for candidate in nodes:
                if candidate.id == node.id:
                    continue
                free = candidate.total_ram_mb - candidate.used_ram_mb
                if free > best_free and _node_ram_ratio(candidate) < NODE_OVERLOADED_RATIO:
                    best_free = free
                    best_target = candidate

            if not best_target:
                logger.warning("[rebalancer] No suitable target node for overflow from %s", node.name)
                # Auto-Wakeup: Try to start an offline node
                from blockhost_backend.services.node_capacity import auto_wakeup_offline_node
                if auto_wakeup_offline_node(db):
                    logger.info("[rebalancer] Auto-Wakeup: Triggering start for offline node due to overflow on %s", node.name)
                return

            # Pick the least-active running server not in cooldown
            victim = _pick_migratable_server(_running_servers_on_node(db, node.id))
            if not victim:
                continue

            _migrate_server(victim, node, best_target, db)
            return  # one migration per cycle to avoid thrashing

        if len(nodes) < 2:
            return  # nothing to consolidate with a single node

        # ── Scenario 3: Consolidation ────────────────────────────────
        # If ALL nodes are below the idle threshold (based on ACTIVE servers),
        # pack servers from the least-loaded node onto the most-loaded one.
        all_idle = all(_node_active_ram_ratio(n, db) < NODE_IDLE_RATIO for n in nodes)
        if not all_idle:
            return

        # Sort by used_ram ascending — the "emptiest" node is the source
        sorted_nodes = sorted(nodes, key=lambda n: n.used_ram_mb)
        source_node = sorted_nodes[0]
        target_node = sorted_nodes[-1]

        if source_node.id == target_node.id:
            return

        servers = _all_servers_on_node(db, source_node.id)
        if not servers:
            return  # handled by Scenario 0 now

        victim = _pick_migratable_server(servers)
        if not victim:
            return

        if victim.state == ServerState.running:
            # Check that the target has enough room ONLY for running servers
            free_on_target = target_node.total_ram_mb - target_node.used_ram_mb
            if free_on_target < 512:  # need at least 512 MB free to accept a transfer
                return

        logger.info(
            "[rebalancer] Consolidating: moving server from %s → %s (all nodes idle)",
            source_node.name, target_node.name,
        )
        _migrate_server(victim, source_node, target_node, db)


def _rebalancer_loop() -> None:
    """Background daemon loop."""
    logger.info("Node rebalancer started (interval=%ds)", REBALANCE_INTERVAL_SECONDS)
    while True:
        try:
            time.sleep(REBALANCE_INTERVAL_SECONDS)
            _rebalance_cycle()
        except Exception:
            logger.exception("Rebalancer cycle failed")


_rebalancer_started = False


def start_node_rebalancer_once() -> None:
    global _rebalancer_started
    if _rebalancer_started:
        return
    _rebalancer_started = True
    threading.Thread(
        target=_rebalancer_loop,
        daemon=True,
        name="node-rebalancer",
    ).start()
