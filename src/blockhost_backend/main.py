from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from blockhost_backend.api import auth as auth_routes
from blockhost_backend.api import servers as server_routes
from blockhost_backend.api import versions as versions_routes
from blockhost_backend.database.db import engine
from blockhost_backend.database.schema import Base


def create_app() -> FastAPI:
    app = FastAPI(title="BlockHost Backend", version="0.1.0")

    # Setup-stage: allow web clients (Flutter Web / browser) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(auth_routes.router)
    app.include_router(server_routes.router)
    app.include_router(versions_routes.router)

    return app


app = create_app()


@app.on_event("startup")
def _create_tables() -> None:
    # Setup-stage convenience: create tables automatically.
    Base.metadata.create_all(bind=engine)

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
