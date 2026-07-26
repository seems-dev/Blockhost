"""Shared application bootstrap for API and background worker processes."""

from __future__ import annotations

import logging
from sqlalchemy import text

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.config.production import validate_production_settings
from blockhost_backend.database.db import SessionLocal, engine
from blockhost_backend.database.schema import Base
from blockhost_backend.orchestrator.resources import seed_default_billing_plans
from blockhost_backend.services.startup_reconciliation import reconcile_server_runtime_state

logger = logging.getLogger(__name__)


def bootstrap_database() -> None:
    settings = get_settings()
    validate_production_settings(settings)

    if settings.production_mode and engine.dialect.name != "postgresql":
        raise RuntimeError("Production requires PostgreSQL. Run: alembic upgrade head")

def bootstrap_application_data() -> None:
    db = SessionLocal()
    try:
        seed_default_billing_plans(db)
        reconciled = reconcile_server_runtime_state(db)
        if reconciled:
            logger.warning(
                "Startup reconciliation: marked %d server(s) suspended (process not running)",
                reconciled,
            )
    finally:
        db.close()
