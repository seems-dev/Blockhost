"""Node capacity tracking and placement helpers."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.database.schema import Node, NodeState, Server, ServerState, utcnow
from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_SECONDS = 30

# Server states that reserve RAM on a node (Active RAM).
_RAM_RESERVING_STATES = (
    ServerState.provisioning,
    ServerState.running,
    ServerState.syncing,
)


def is_node_heartbeat_fresh(node: Node, *, now=None) -> bool:
    if node.last_heartbeat is None:
        return False
    now = now or utcnow()
    last_heartbeat = node.last_heartbeat
    if last_heartbeat.tzinfo is None:
        last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_heartbeat) <= timedelta(seconds=HEARTBEAT_STALE_SECONDS)


def compute_node_allocated_ram_mb(db: Session, node_id: uuid.UUID) -> int:
    """Sum of plan RAM for all servers assigned to this node."""
    total = 0
    servers = db.execute(
        select(Server).where(
            Server.node_id == node_id,
            Server.state.in_(_RAM_RESERVING_STATES),
        )
    ).scalars().all()
    for server in servers:
        limits = get_effective_server_resource_limits(db=db, server=server)
        total += limits["ram_mb"]
    return total


def refresh_node_allocated_ram(db: Session, node_id: uuid.UUID) -> None:
    node = db.get(Node, node_id)
    if node:
        node.used_ram_mb = compute_node_allocated_ram_mb(db, node_id)
        db.add(node)


def refresh_all_nodes_allocated_ram(db: Session) -> None:
    for node_id in db.execute(select(Node.id)).scalars().all():
        refresh_node_allocated_ram(db, node_id)


def select_best_node(db: Session, *, required_ram_mb: int = 0) -> Node | None:
    """Pick the online, non-draining node with the most free active RAM."""
    now = utcnow()
    candidates = db.execute(
        select(Node).where(
            Node.status == NodeState.online,
        ).with_for_update()
    ).scalars().all()

    best: Node | None = None
    best_free = -1
    for node in candidates:
        if not is_node_heartbeat_fresh(node, now=now):
            continue
            
        # ONLY calculate the RAM of active servers (running + provisioning)
        active_servers = db.execute(
            select(Server).where(
                Server.node_id == node.id,
                Server.state.in_([ServerState.running, ServerState.provisioning])
            )
        ).scalars().all()
        
        # FIX: Use get_effective_server_resource_limits instead of s.ram_mb
        active_ram = 0
        for s in active_servers:
            limits = get_effective_server_resource_limits(db=db, server=s)
            active_ram += limits.get("ram_mb", 0)
            
        free = node.total_ram_mb - active_ram
        
        if free < required_ram_mb:
            continue
        if free > best_free:
            best_free = free
            best = node

    # Optional: ensure it has at least 512MB free
    if best_free < 512:
        return None
        
    return best


def auto_wakeup_offline_node(db: Session) -> bool:
    """Attempt to start one offline node that has a configured cloud provider. Returns True if a wake signal was sent."""
    # Check if a node is already starting to prevent AWS API rate limits (Thundering Herd)
    is_starting = db.execute(
        select(Node).where(Node.status == NodeState.starting)
    ).scalars().first()
    if is_starting:
        return True

    offline_node = db.execute(
        select(Node).where(
            Node.status == NodeState.offline,
            Node.provider.is_not(None),
            Node.provider_instance_id.is_not(None)
        )
    ).scalars().first()

    if not offline_node:
        return False
        
    try:
        from blockhost_backend.services.cloud.cloud_provider import get_cloud_provider
        provider = get_cloud_provider(offline_node.provider)
        provider.start_instance(offline_node.provider_instance_id)
        
        # Mark as starting so we don't boot it again on the next cycle
        offline_node.status = NodeState.starting
        offline_node.status_updated_at = utcnow()
        db.commit()
        return True
    except Exception as e:
        logger.error("Failed to wake up offline node %s: %s", offline_node.name, e)
        return False


def suspend_running_servers_on_node(db: Session, node_id: uuid.UUID) -> list[uuid.UUID]:
    """Stop running processes and suspend servers on a failed node."""
    running_servers = db.execute(
        select(Server).where(
            Server.node_id == node_id,
            Server.state == ServerState.running,
        )
    ).scalars().all()

    if not running_servers:
        return []

    from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
    from blockhost_backend.orchestrator.runtime_cache import (
        invalidate_node_runtime_cache,
        invalidate_server_runtime_cache,
    )

    orchestrator = get_server_lifecycle_orchestrator()
    suspended_ids: list[uuid.UUID] = []

    for server in running_servers:
        try:
            orchestrator.stop_server(server)
        except Exception:
            logger.exception("Failed to stop server %s during node failover", server.id)
            server.state = ServerState.suspended
            db.add(server)
        suspended_ids.append(server.id)
        invalidate_server_runtime_cache(server.id)

    invalidate_node_runtime_cache(node_id)
    return suspended_ids


def mark_stale_nodes_offline(db: Session) -> list[uuid.UUID]:
    """Mark nodes with stale heartbeats as offline; suspend their running servers. Also recovers deadlocked starting nodes."""
    now = utcnow()
    cutoff = now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    stale_nodes = db.execute(
        select(Node).where(
            Node.status == NodeState.online,
            Node.last_heartbeat.is_not(None),
            Node.last_heartbeat < cutoff,
        )
    ).scalars().all()

    affected: list[uuid.UUID] = []
    for node in stale_nodes:
        node.status = NodeState.offline
        node.status_updated_at = now
        db.add(node)
        affected.append(node.id)
        suspend_running_servers_on_node(db, node.id)
    
    # Check for Boot Deadlock (starting > 5 mins)
    deadlock_cutoff = now - timedelta(minutes=5)
    stuck_nodes = db.execute(
        select(Node).where(
            Node.status == NodeState.starting,
            Node.status_updated_at < deadlock_cutoff
        )
    ).scalars().all()
    
    for node in stuck_nodes:
        logger.warning("Node '%s' stuck in starting state for > 5 mins. Reverting to offline.", node.id)
        node.status = NodeState.offline
        node.status_updated_at = now
        db.add(node)
        affected.append(node.id)

    if affected:
        db.commit()
    return affected
