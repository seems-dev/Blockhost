"""Guards for cross-node migrations (cooldown + hourly rate limit)."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import Server, utcnow

logger = logging.getLogger(__name__)

_LAST_MIGRATED_KEY = "last_migrated_at"

_migration_times: deque[datetime] = deque()
_migration_lock = Lock()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def server_last_migrated_at(server: Server) -> datetime | None:
    cfg = server.mc_config or {}
    return _parse_iso(cfg.get(_LAST_MIGRATED_KEY))


def mark_server_migrated(db: Session, server: Server) -> None:
    """Record migration time on the server for per-server cooldown."""
    cfg = dict(server.mc_config or {})
    cfg[_LAST_MIGRATED_KEY] = utcnow().isoformat()
    server.mc_config = cfg
    db.add(server)


def server_in_migration_cooldown(server: Server) -> bool:
    settings = get_settings()
    cooldown = max(0, settings.migration_cooldown_seconds)
    if cooldown <= 0:
        return False
    last = server_last_migrated_at(server)
    if last is None:
        return False
    return utcnow() - last < timedelta(seconds=cooldown)


def can_run_migration_now(*, record: bool = False) -> bool:
    """Global hourly migration budget (process-local; one worker assumed)."""
    settings = get_settings()
    limit = max(0, settings.max_migrations_per_hour)
    if limit <= 0:
        return True

    now = utcnow()
    cutoff = now - timedelta(hours=1)
    with _migration_lock:
        while _migration_times and _migration_times[0] < cutoff:
            _migration_times.popleft()
        if len(_migration_times) >= limit:
            logger.warning(
                "Migration hourly cap reached (%d/%d in last hour)",
                len(_migration_times),
                limit,
            )
            return False
        if record:
            _migration_times.append(now)
        return True


def record_migration() -> None:
    can_run_migration_now(record=True)
