"""Server migration endpoint — moves a server between nodes via S3.

Flow: Stop on old node → Backup to S3 → Reassign node_id → Restore on new node → Start.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.servers import (
    _do_start_server,
    _get_server_for_user,
    _invalidate_server_read_cache,
    _ORCHESTRATOR,
)
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Node, Server, ServerState, User
from blockhost_backend.runtime.agent_runtime import AgentRuntime
from blockhost_backend.services.node_capacity import select_best_node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _agent_for_node(node: Node) -> AgentRuntime:
    """Build an AgentRuntime pointing at the given node."""
    token = get_settings().worker_agent_token
    base = f"http://{node.ip_address}:{node.agent_port}"
    return AgentRuntime(agent_base_url=base, agent_token=token)


@router.post("/{server_id}/migrate")
def migrate_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Migrate a server to another node via S3 backup/restore.

    Steps:
    1. Stop the server on its current node.
    2. Backup world data to S3 from the current node.
    3. Pick a new healthy node (excluding the current one).
    4. Update the DB to point to the new node.
    5. Restore world data from S3 on the new node.
    6. Start the server on the new node.
    """
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")
    sid = str(server.id)

    # ── Resolve the current node ──────────────────────────────────────
    old_node: Node | None = None
    if server.node_id:
        old_node = db.get(Node, server.node_id)

    if not old_node:
        raise HTTPException(
            status_code=400,
            detail="Server is not assigned to a node. Start it first.",
        )

    old_agent = _agent_for_node(old_node)

    # ── Step 1: Stop the server on the old node ───────────────────────
    logger.info("[migrate] Stopping server %s on node %s", sid, old_node.name)
    try:
        old_agent.stop_server(sid)
    except Exception as e:
        logger.warning("[migrate] Stop failed (may already be stopped): %s", e)

    server.state = ServerState.suspended
    db.commit()

    # ── Step 2: Backup world to S3 from old node ──────────────────────
    logger.info("[migrate] Backing up server %s to S3 from node %s", sid, old_node.name)
    try:
        backup_result = old_agent.backup_to_s3(sid)
        logger.info("[migrate] Backup complete: %s", backup_result)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to backup world on old node: {e}",
        )

    # ── Step 3: Select a new node (exclude current) ───────────────────
    candidates = db.execute(
        select(Node).where(Node.id != old_node.id, Node.status == "online")
    ).scalars().all()

    new_node: Node | None = None
    best_free = -1
    for node in candidates:
        free = node.total_ram_mb - node.used_ram_mb
        if free > best_free:
            best_free = free
            new_node = node

    if not new_node:
        raise HTTPException(
            status_code=503,
            detail="No other healthy node available for migration.",
        )

    logger.info(
        "[migrate] Selected new node %s (%s) for server %s",
        new_node.name,
        new_node.ip_address,
        sid,
    )

    # ── Step 4: Update DB to point to new node ────────────────────────
    server.node_id = new_node.id
    server.vm_ipv4 = new_node.ip_address
    # Keep the same port — it will be allocated on the new node during start.
    db.commit()

    # Invalidate the NodeRouter cache so it picks up the new node.
    _ORCHESTRATOR.invalidate_server(sid)

    new_agent = _agent_for_node(new_node)

    # ── Step 5: Restore world from S3 on new node ─────────────────────
    logger.info("[migrate] Restoring server %s on new node %s", sid, new_node.name)
    try:
        new_agent.restore_from_s3(sid)
    except Exception as e:
        # Rollback the node assignment so the server isn't orphaned
        server.node_id = old_node.id
        server.vm_ipv4 = old_node.ip_address
        db.commit()
        _ORCHESTRATOR.invalidate_server(sid)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to restore world on new node: {e}",
        )

    # ── Step 6: Start the server on the new node ──────────────────────
    logger.info("[migrate] Starting server %s on new node %s", sid, new_node.name)
    try:
        _do_start_server(server, db)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[migrate] Failed to start server on new node")
        raise HTTPException(
            status_code=503,
            detail=f"Restore succeeded but failed to start on new node: {e}",
        )

    _invalidate_server_read_cache(user.id, server.id)

    return {
        "status": "ok",
        "server_id": sid,
        "old_node": old_node.name,
        "new_node": new_node.name,
        "new_ip": new_node.ip_address,
    }
