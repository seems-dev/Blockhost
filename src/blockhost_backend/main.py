from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from blockhost_backend.api import admin as admin_routes
from blockhost_backend.api import auth as auth_routes
from blockhost_backend.api import backups as backup_routes
from blockhost_backend.api import servers as server_routes
from blockhost_backend.api import versions as versions_routes
from blockhost_backend.api import files as files_routes
from blockhost_backend.api import billing as billing_routes
from blockhost_backend.database.db import engine
from blockhost_backend.database.schema import Base
from blockhost_backend.services.system_health import start_disk_health_monitor_once


def create_app() -> FastAPI:
    app = FastAPI(title="BlockHost Backend", version="0.1.0")

    # Setup-stage: allow web clients (Flutter Web / browser) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(server_routes.router)
    app.include_router(versions_routes.router)
    app.include_router(files_routes.router)
    app.include_router(billing_routes.router)

    return app


app = create_app()


@app.on_event("startup")
def _create_tables() -> None:
    # Setup-stage convenience: create tables automatically.
    Base.metadata.create_all(bind=engine)

    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_alive_subscription_per_server ON subscriptions (server_id) WHERE status IN ('active', 'grace_period');"))

    # Seed default billing plans
    from blockhost_backend.database.db import SessionLocal
    from blockhost_backend.orchestrator.resources import seed_default_billing_plans
    from blockhost_backend.services.billing import start_subscription_expiration_worker_once

    db = SessionLocal()
    try:
        seed_default_billing_plans(db)
    finally:
        db.close()

    start_subscription_expiration_worker_once()

    # Setup-stage lightweight "migration" for newly added columns.
    # In production this should be handled via Alembic migrations.
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
    start_disk_health_monitor_once()

