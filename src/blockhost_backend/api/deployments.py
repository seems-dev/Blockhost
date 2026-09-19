"""
PaaS Deployments API.
"""
import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import AppDeployment, CustomDomain, DeploymentState, User, Node
import httpx
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.node_capacity import select_best_node
from blockhost_backend.services.provisioning import provision_resource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])

class CreateDeploymentRequest(BaseModel):
    name: str = Field(..., max_length=100)
    docker_image: str = Field(..., max_length=200)
    internal_port: int = Field(..., ge=1, le=65535)
    ram_limit_mb: int = Field(..., ge=128)
    cpu_limit: float = Field(0.5, ge=0.1)


class DeploymentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    owner_id: uuid.UUID
    node_id: uuid.UUID | None = None
    name: str
    docker_image: str
    internal_port: int
    ram_limit_mb: int
    cpu_limit: float
    state: str
    error_message: str | None = None
    container_id: str | None = None
    host_port: int | None = None
    volume_path: str | None = None
    volume_mount_path: str | None = None
    last_network_rx: int | None = None
    created_at: datetime
    updated_at: datetime


def _get_deployment(db: Session, deployment_id: uuid.UUID, user: User) -> AppDeployment:
    dep = db.get(AppDeployment, deployment_id)
    if not dep or dep.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return dep


@router.get("", response_model=List[DeploymentOut])
def list_deployments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    deployments = db.execute(
        select(AppDeployment).where(AppDeployment.owner_id == user.id)
    ).scalars().all()
    return deployments


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
def create_deployment(
    payload: CreateDeploymentRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # We could assign a node right now or let provision_resource do it.
    # We'll assign it now to stay consistent with Minecraft servers.
    node = select_best_node(db, required_ram_mb=payload.ram_limit_mb)
    if not node:
        raise HTTPException(status_code=503, detail="No capacity available")

    dep_id = uuid.uuid4()
    dep = AppDeployment(
        id=dep_id,
        owner_id=user.id,
        node_id=node.id,
        name=payload.name,
        docker_image=payload.docker_image,
        internal_port=payload.internal_port,
        ram_limit_mb=payload.ram_limit_mb,
        cpu_limit=payload.cpu_limit,
        state=DeploymentState.created,
        volume_path=f"/var/lib/blockhost/deployments/{dep_id}",
    )
    db.add(dep)
    db.commit()
    db.refresh(dep)

    # Queue provisioning
    background_tasks.add_task(provision_resource, "app_deployment", dep.id)

    return dep


@router.post("/{deployment_id}/start", response_model=DeploymentOut)
def start_deployment(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    
    if dep.state == DeploymentState.running:
        return dep

    dep.state = DeploymentState.building
    db.commit()

    if not dep.node_id:
        node = select_best_node(db, required_ram_mb=dep.ram_limit_mb)
        if not node:
            dep.state = DeploymentState.error
            dep.error_message = "No capacity available"
            db.commit()
            raise HTTPException(status_code=503, detail="No capacity available")
        dep.node_id = node.id
        db.commit()

    # Run synchronously so we can see the error in the logs
    provision_resource("app_deployment", str(dep.id))
    
    db.refresh(dep)
    return dep


@router.post("/{deployment_id}/stop", response_model=DeploymentOut)
def stop_deployment(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    
    if dep.state == DeploymentState.suspended:
        return dep

    if dep.state in (DeploymentState.building, DeploymentState.stopped):
        raise HTTPException(status_code=400, detail="Deployment is currently mutating state")

    dep.state = DeploymentState.stopping
    db.commit()

    if dep.node_id:
        try:
            node = db.get(Node, dep.node_id)
            if node and node.ip_address:
                settings = get_settings()
                agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/stop"
                httpx.post(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        except Exception as e:
            logger.error("Failed to stop deployment %s on agent: %s", dep.id, e)
            
    dep.state = DeploymentState.suspended
    dep.container_id = None
    db.commit()
    db.refresh(dep)
    return dep


@router.get("/{deployment_id}/logs", response_model=List[str])
def get_deployment_logs(
    deployment_id: uuid.UUID,
    tail: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    if dep.state != DeploymentState.running or not dep.node_id:
        return []

    node = db.get(Node, dep.node_id)
    if not node or not node.ip_address:
        return []

    try:
        settings = get_settings()
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/logs?tail={tail}"
        response = httpx.get(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning("Failed to fetch logs for deployment %s: %s", dep.id, e)
        return []
