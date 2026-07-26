"""Reconcile DB server state with actual runtime on backend startup."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.database.schema import Server, ServerState
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator

logger = logging.getLogger(__name__)


def reconcile_server_runtime_state(db: Session) -> int:
    """
    Mark DB 'running' servers as suspended when no process is active.
    Handles reboots where systemd units were lost but DB still says running.
    """
    orchestrator = get_server_lifecycle_orchestrator()
    fixed = 0

    running_servers = db.execute(
        select(Server).where(Server.state == ServerState.running)
    ).scalars().all()

    for server in running_servers:
        if orchestrator.is_running(str(server.id)):
            continue
        logger.warning(
            "Reconciling server %s on startup: DB=running but process stopped — marking suspended",
            server.id,
        )
        server.state = ServerState.suspended
        db.add(server)
        fixed += 1

    if fixed:
        db.commit()
    return fixed
