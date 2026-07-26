"""Deep health checks for load balancers and monitoring."""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.api_cache import _get_redis_client


def check_system_health(db: Session) -> dict[str, Any]:
    settings = get_settings()
    components: dict[str, dict[str, Any]] = {}
    overall = "ok"

    # Database
    try:
        db.execute(text("SELECT 1"))
        components["database"] = {"status": "ok"}
    except Exception as exc:
        components["database"] = {"status": "down", "error": str(exc)}
        overall = "down"

    # Redis (optional — degraded if configured but unreachable)
    try:
        client = _get_redis_client()
        if client is None:
            components["redis"] = {"status": "skipped", "reason": "not configured or unavailable"}
        else:
            client.ping()
            components["redis"] = {"status": "ok"}
    except Exception as exc:
        components["redis"] = {"status": "degraded", "error": str(exc)}
        if overall == "ok":
            overall = "degraded"

    # Disk
    try:
        usage = shutil.disk_usage("/")
        used_pct = (usage.used / usage.total) * 100.0 if usage.total else 0.0
        disk_status = "ok"
        if used_pct >= settings.disk_critical_threshold_percent:
            disk_status = "down"
            overall = "down"
        elif used_pct >= settings.disk_warning_threshold_percent:
            disk_status = "degraded"
            if overall == "ok":
                overall = "degraded"
        components["disk"] = {
            "status": disk_status,
            "used_percent": round(used_pct, 1),
        }
    except Exception as exc:
        components["disk"] = {"status": "degraded", "error": str(exc)}
        if overall == "ok":
            overall = "degraded"

    return {
        "status": overall,
        "production_mode": settings.production_mode,
        "components": components,
    }
