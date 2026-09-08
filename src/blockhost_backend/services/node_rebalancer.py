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
    sid = str(server.id)
    was_running = (server.state == ServerState.running)

    from_agent = _agent_for_node(from_node)
    
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

    # 2. Reassign with a fresh port on the new node
    old_node_id = server.node_id
    old_ip = server.vm_ipv4
    server.node_id = to_node.id
    server.vm_ipv4 = to_node.ip_address
    try:
        from blockhost_backend.api.servers import _allocate_port
        server.vm_port = _allocate_port(db=db, flavor=server.flavor, node_id=to_node.id)
    except Exception as e:
        logger.error("[rebalancer] Port allocation failed for %s on %s: %s", sid, to_node.name, e)
        server.node_id = old_node_id
        server.vm_ipv4 = old_ip
        db.commit()
        return False
    db.commit()

    # Invalidate routing cache
    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    get_server_lifecycle_orchestrator().invalidate_server(sid)

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
    
    # EFS Migration is instant, no data to purge from source.
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


def _node_active_ram_mb(node: Node, db: Session) -> int:
    """Return total RAM used by active (running/provisioning/syncing) servers on this node."""
    active_states = (ServerState.running, ServerState.provisioning, ServerState.syncing)
    active_servers = db.execute(
        select(Server).where(
            Server.node_id == node.id,
            Server.state.in_(active_states),
        )
    ).scalars().all()

    total = 0
    from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits
    for s in active_servers:
        limits = get_effective_server_resource_limits(db=db, server=s)
        total += limits["ram_mb"]
    return total


def _server_ram_mb(server: Server, db: Session) -> int:
    """Return this server's RAM allocation from its billing plan."""
    from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits
    limits = get_effective_server_resource_limits(db=db, server=server)
    return limits.get("ram_mb", 0)


def _backup_server_to_s3(server: Server, node: Node) -> bool:
    """Backup a server's world files to S3 before unassigning it. Returns True on success."""
    sid = str(server.id)
    try:
        require_s3_configured()
    except RuntimeError:
        logger.warning("[rebalancer] S3 not configured, skipping backup for %s", sid)
        return False

    try:
        agent = _agent_for_node(node)
        agent.backup_to_s3(sid)
        logger.info("[rebalancer] Backed up server %s to S3 from node %s", sid, node.name)
        return True
    except Exception as e:
        logger.error("[rebalancer] Failed to backup server %s to S3: %s", sid, e)
        return False


def _rebalance_cycle() -> None:
    """Run one rebalance evaluation cycle.

    Three phases, executed in order:
      Phase 1 — Consolidation: Pack running servers onto the fewest possible nodes.
      Phase 2 — Cold Storage: Backup & unassign suspended servers from nodes with 0 running.
      Phase 3 — Shutdown: Power off any node with 0 assigned servers.
    """
    with SessionLocal() as db:
        nodes = _healthy_online_nodes(db)
        if not nodes:
            return

        # ── Phase 0: Rescue orphaned servers ─────────────────────────────
        # Reassign servers stuck on offline nodes (or node_id=NULL) to an online node.
        # With EFS, files are shared, so we just update the DB pointer.
        online_node = nodes[0]
        offline_nodes = db.execute(
            select(Node).where(Node.status != NodeState.online)
        ).scalars().all()
        offline_ids = [n.id for n in offline_nodes]

        orphaned = db.execute(
            select(Server).where(
                (Server.node_id.is_(None)) | (Server.node_id.in_(offline_ids))
            )
        ).scalars().all() if offline_ids else db.execute(
            select(Server).where(Server.node_id.is_(None))
        ).scalars().all()

        if orphaned:
            from blockhost_backend.api.servers import _allocate_port
            rescued = 0
            for s in orphaned:
                old = s.node_id
                s.node_id = online_node.id
                s.vm_ipv4 = online_node.ip_address
                # Allocate a fresh port to avoid UniqueViolation on (node_id, vm_port)
                try:
                    s.vm_port = _allocate_port(db=db, flavor=s.flavor, node_id=online_node.id)
                except Exception as e:
                    logger.error("[rebalancer] Failed to allocate port for orphaned server %s: %s", s.id, e)
                    db.rollback()
                    continue
                logger.info("[rebalancer] Rescued orphaned server %s (was node=%s) → %s port=%d", s.id, old, online_node.name, s.vm_port)
                rescued += 1
            if rescued:
                db.commit()
                logger.info("[rebalancer] Rescued %d orphaned server(s)", rescued)

        # ── Phase 1: Consolidate running servers ─────────────────────────
        # Goal: If node-2 has 1 running server but node-1 has spare capacity,
        # migrate that server to node-1 so node-2 becomes empty.
        if len(nodes) >= 2 and can_run_migration_now():
            # Sort by active RAM ascending — emptiest node is the migration source
            sorted_nodes = sorted(nodes, key=lambda n: _node_active_ram_mb(n, db))
            source_node = sorted_nodes[0]
            source_running = _running_servers_on_node(db, source_node.id)

            if source_running:
                # Try to move the least-active running server to a node with capacity
                for server in source_running:
                    if server_in_migration_cooldown(server):
                        continue

                    server_ram = _server_ram_mb(server, db)

                    # Find a target with enough free ACTIVE RAM
                    best_target: Node | None = None
                    best_free = -1
                    for candidate in sorted_nodes:
                        if candidate.id == source_node.id:
                            continue
                        active_ram = _node_active_ram_mb(candidate, db)
                        free = candidate.total_ram_mb - active_ram
                        if free >= server_ram and free > best_free:
                            best_free = free
                            best_target = candidate

                    if best_target:
                        logger.info(
                            "[rebalancer] Consolidating: server %s (%dMB) from %s → %s (free: %dMB)",
                            server.id, server_ram, source_node.name, best_target.name, best_free,
                        )
                        _migrate_server(server, source_node, best_target, db)
                        return  # One migration per cycle to avoid thrashing

        # ── Phase 2: Overflow Protection ─────────────────────────────────
        # If a node's active RAM exceeds 85%, shed its least-active server.
        if can_run_migration_now():
            for node in nodes:
                ratio = _node_active_ram_ratio(node, db)
                if ratio < NODE_OVERLOADED_RATIO:
                    continue

                logger.info(
                    "[rebalancer] Node %s is overloaded (%.0f%% active RAM)",
                    node.name, ratio * 100,
                )

                # Find target with most free active RAM
                best_target: Node | None = None
                best_free = -1
                for candidate in nodes:
                    if candidate.id == node.id:
                        continue
                    active_ram = _node_active_ram_mb(candidate, db)
                    free = candidate.total_ram_mb - active_ram
                    if free > best_free and _node_active_ram_ratio(candidate, db) < NODE_OVERLOADED_RATIO:
                        best_free = free
                        best_target = candidate

                if not best_target:
                    # No room anywhere — boot a new node
                    from blockhost_backend.services.node_capacity import auto_wakeup_offline_node
                    if auto_wakeup_offline_node(db):
                        logger.info("[rebalancer] Auto-Wakeup: booting offline node due to overflow on %s", node.name)
                    return

                victim = _pick_migratable_server(_running_servers_on_node(db, node.id))
                if not victim:
                    continue

                _migrate_server(victim, node, best_target, db)
                return  # One migration per cycle

        # ── Phase 3: Cold Storage + Shutdown ─────────────────────────────
        # For any node with 0 running servers:
        #   a) Backup all suspended servers to S3
        #   b) Unassign them (node_id = None → cold storage)
        #   c) Shut down the EC2
        # Keep at least 1 node online for instant server creation.
        all_online = db.execute(
            select(Node).where(Node.status == NodeState.online)
        ).scalars().all()

        for node in all_online:
            if not node.provider or not node.provider_instance_id:
                continue

            running = _running_servers_on_node(db, node.id)
            if running:
                continue  # Node has active servers — don't touch it

            # Keep at least 1 node online
            remaining = [n for n in all_online if n.status == NodeState.online and n.id != node.id]
            if not remaining:
                continue

            # Reassign all suspended servers to another online node (EFS = files are already there)
            from blockhost_backend.api.servers import _allocate_port
            suspended = _all_servers_on_node(db, node.id)
            target_node = remaining[0]  # Pick the first remaining online node
            reassign_failed = False
            for s in suspended:
                s.node_id = target_node.id
                s.vm_ipv4 = target_node.ip_address
                # Allocate a fresh port to avoid UniqueViolation on (node_id, vm_port)
                try:
                    s.vm_port = _allocate_port(db=db, flavor=s.flavor, node_id=target_node.id)
                except Exception as e:
                    logger.error("[rebalancer] Failed to allocate port for server %s on %s: %s", s.id, target_node.name, e)
                    reassign_failed = True
                    break
                logger.info("[rebalancer] Reassigned suspended server %s → %s port=%d (EFS)", s.id, target_node.name, s.vm_port)

            if reassign_failed:
                db.rollback()
                continue

            # All servers reassigned — shut down the node
            logger.info("[rebalancer] Shutting down node %s (0 running servers, %d suspended → reassigned to %s)", node.name, len(suspended), target_node.name)
            try:
                provider = get_cloud_provider(node.provider)
                provider.stop_instance(node.provider_instance_id)
                node.status = NodeState.offline
                db.commit()
            except Exception as e:
                logger.error("[rebalancer] Failed to shut down node %s: %s", node.name, e)
                db.rollback()


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
