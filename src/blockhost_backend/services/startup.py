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

    if not settings.production_mode:
        Base.metadata.create_all(bind=engine)

        if engine.dialect.name == "postgresql":
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE servers "
                        "ADD COLUMN IF NOT EXISTS mc_config JSONB NOT NULL DEFAULT '{}'::jsonb"
                    )
                )
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(128)"
                ))
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(32) NOT NULL DEFAULT 'local'"
                ))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub ON users (google_sub) WHERE google_sub IS NOT NULL"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)"
                ))
                conn.execute(text(
                    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS approved BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                conn.execute(text(
                    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS agent_token_hash VARCHAR(64)"
                ))
    elif engine.dialect.name != "postgresql":
        raise RuntimeError("Production requires PostgreSQL. Run: alembic upgrade head")

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_alive_subscription_per_server "
            "ON subscriptions (server_id) WHERE status IN ('active', 'grace_period')"
        ))


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
