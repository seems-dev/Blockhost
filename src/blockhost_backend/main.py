from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from blockhost_backend.api import admin as admin_routes
from blockhost_backend.api import auth as auth_routes
from blockhost_backend.api import backups as backup_routes
from blockhost_backend.api import billing as billing_routes
from blockhost_backend.api import files as files_routes
from blockhost_backend.api import legal as legal_routes
from blockhost_backend.api import nodes as nodes_routes
from blockhost_backend.api import mods as mods_routes
from blockhost_backend.api import servers as server_routes
from blockhost_backend.api import versions as versions_routes
from blockhost_backend.api.software import router as software_router
from blockhost_backend.config.config_manager import get_cors_origins, get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.services.background_workers import (
    should_run_workers_in_api,
    start_background_workers,
)
from blockhost_backend.services.health import check_system_health
from blockhost_backend.services.startup import bootstrap_application_data, bootstrap_database


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="BlockHost Backend", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(settings),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,
    )

    @app.get("/health")
    def health(db: Session = Depends(get_db)) -> dict:
        return check_system_health(db)

    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(server_routes.router)
    app.include_router(versions_routes.router)
    app.include_router(files_routes.router)
    app.include_router(billing_routes.router)
    app.include_router(nodes_routes.router)
    app.include_router(legal_routes.router)
    app.include_router(mods_routes.router)
    app.include_router(software_router)

    return app


app = create_app()


@app.on_event("startup")
def _startup() -> None:
    bootstrap_database()
    bootstrap_application_data()
    if should_run_workers_in_api():
        start_background_workers()
