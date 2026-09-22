import logging
import uuid
import secrets
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import DatabaseInstance, DatabaseEngine, DeploymentState, User, Node, Project
import httpx
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.node_capacity import select_best_node
from blockhost_backend.services.provisioning import provision_resource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/databases", tags=["databases"])

class CreateDatabaseRequest(BaseModel):
    name: str = Field(..., max_length=100)
    engine: DatabaseEngine
    version: str = Field(..., max_length=32)
    ram_limit_mb: int = Field(512, ge=256)
    cpu_limit: float = Field(0.5, ge=0.1)
    storage_limit_mb: int = Field(1024, ge=512)
    project_id: uuid.UUID

class DatabaseOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    engine: DatabaseEngine
    version: str
    internal_hostname: str
    internal_port: int
    db_name: str
    db_username: str
    ram_limit_mb: int
    cpu_limit: float
    storage_limit_mb: int
    state: str
    created_at: datetime
    updated_at: datetime

class DatabaseCreateOut(DatabaseOut):
    db_password: str
    connection_url: str


def _connection_scheme(engine: DatabaseEngine) -> str:
    if engine == DatabaseEngine.postgresql:
        return "postgresql"
    return engine.value

def _generate_password(length: int = 16) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def _get_database(db: Session, database_id: uuid.UUID, user: User) -> DatabaseInstance:
    db_instance = db.get(DatabaseInstance, database_id)
    if not db_instance or db_instance.project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return db_instance

@router.get("", response_model=List[DatabaseOut])
def list_databases(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    databases = db.execute(
        select(DatabaseInstance).join(DatabaseInstance.project).where(Project.owner_id == user.id)
    ).scalars().all()
    return databases

@router.post("", response_model=DatabaseCreateOut, status_code=status.HTTP_201_CREATED)
def create_database(
    payload: CreateDatabaseRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    node = select_best_node(db, required_ram_mb=payload.ram_limit_mb)
    if not node:
        raise HTTPException(status_code=503, detail="No capacity available")

    db_id = uuid.uuid4()
    
    password = _generate_password(16)
    db_name = payload.name.lower().replace(" ", "_").replace("-", "_") + "_db"
    db_username = payload.name.lower().replace(" ", "_")[:16]

    internal_port = 5432
    if payload.engine == DatabaseEngine.mysql or payload.engine == DatabaseEngine.mariadb:
        internal_port = 3306
    elif payload.engine == DatabaseEngine.redis:
        internal_port = 6379

    internal_hostname = f"db-{db_id.hex[:8]}.internal"
    
    db_instance = DatabaseInstance(
        id=db_id,
        project_id=project.id,
        node_id=node.id,
        name=payload.name,
        engine=payload.engine,
        version=payload.version,
        db_name=db_name,
        db_username=db_username,
        db_password=password,
        internal_hostname=internal_hostname,
        internal_port=internal_port,
        ram_limit_mb=payload.ram_limit_mb,
        cpu_limit=payload.cpu_limit,
        storage_limit_mb=payload.storage_limit_mb,
        volume_path=f"/var/lib/blockhost/databases/{db_id}",
        state=DeploymentState.created,
    )
    db.add(db_instance)
    db.commit()
    db.refresh(db_instance)

    background_tasks.add_task(provision_resource, "database_instance", str(db_instance.id))

    connection_url = f"{_connection_scheme(payload.engine)}://{db_username}:{password}@{internal_hostname}:{internal_port}/{db_name}"
    
    out = DatabaseCreateOut.model_validate(db_instance)
    out.db_password = password
    out.connection_url = connection_url
    return out

@router.get("/{database_id}", response_model=DatabaseOut)
def get_database(
    database_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_database(db, database_id, user)


@router.post("/{database_id}/start", response_model=DatabaseOut)
def start_database(
    database_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_instance = _get_database(db, database_id, user)
    if db_instance.state == DeploymentState.running:
        return db_instance
    db_instance.state = DeploymentState.building
    db.commit()
    provision_resource("database_instance", str(db_instance.id))
    db.refresh(db_instance)
    return db_instance


@router.post("/{database_id}/stop", response_model=DatabaseOut)
def stop_database(
    database_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_instance = _get_database(db, database_id, user)
    if db_instance.node and db_instance.node.ip_address:
        try:
            settings = get_settings()
            agent_url = f"http://{db_instance.node.ip_address}:{db_instance.node.agent_port}/agent/databases/{db_instance.id}/stop"
            httpx.post(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        except Exception as e:
            logger.error("Failed to stop DB on agent: %s", e)
    db_instance.state = DeploymentState.suspended
    db.commit()
    db.refresh(db_instance)
    return db_instance

@router.post("/{database_id}/restart", response_model=DatabaseOut)
def restart_database(
    database_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_instance = _get_database(db, database_id, user)
    db_instance.state = DeploymentState.building
    db.commit()
    provision_resource("database_instance", str(db_instance.id))
    db.refresh(db_instance)
    return db_instance

@router.delete("/{database_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_database(
    database_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_instance = _get_database(db, database_id, user)
    settings = get_settings()
    
    node = db_instance.node
    if node and node.ip_address:
        # 1. Stop the database container
        try:
            stop_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{db_instance.id}/stop"
            httpx.post(stop_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        except Exception as e:
            logger.warning("Failed to stop DB container during delete: %s", e)
        
        # 2. Delete the database container and volume
        try:
            delete_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{db_instance.id}/delete"
            httpx.delete(delete_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        except Exception as e:
            logger.warning("Failed to delete DB on agent: %s", e)
    
    # 3. Cancel any billing subscriptions
    try:
        from blockhost_backend.database.schema import BillingSubscription
        subs = db.execute(
            select(BillingSubscription).where(
                BillingSubscription.resource_type == "database",
                BillingSubscription.resource_id == str(db_instance.id),
            )
        ).scalars().all()
        for sub in subs:
            sub.status = "cancelled"
    except Exception as e:
        logger.warning("Failed to cancel billing for database %s: %s", db_instance.id, e)

    # 4. Delete the record (S3 backups are retained with prefix databases/{id}/backups/)
    db.delete(db_instance)
    db.commit()
    return None

