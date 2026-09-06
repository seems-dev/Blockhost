"""Helpers to apply billing plan limits to Minecraft server config."""

from __future__ import annotations

from sqlalchemy.orm import Session

from blockhost_backend.database.schema import Server
from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits


def plan_player_limit(*, db: Session | None, server: Server) -> int:
    limits = get_effective_server_resource_limits(db=db, server=server)
    return max(0, int(limits.get("player_limit") or 0))


def clamp_max_players(
    requested: int | None,
    *,
    db: Session | None,
    server: Server,
    default: int | None = None,
) -> int | None:
    """Cap max_players to the active plan. Returns None if unpaid (limit 0)."""
    limit = plan_player_limit(db=db, server=server)
    if limit <= 0:
        return None
    base = requested if requested is not None else default
    if base is None:
        return limit
    return max(1, min(int(base), limit))
