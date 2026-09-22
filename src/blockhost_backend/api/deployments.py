"""
PaaS Deployments API.
"""
import logging
import re
import uuid
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel, Field, computed_field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user, authenticate_ws_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import (
    AppDeployment, CustomDomain, DomainStatus, DeploymentState, HealthStatus,
    User, Node, DeploymentKind, SourceType, DeploymentRevision, RevisionTrigger,
    BillingSubscription,
)
import httpx
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.node_capacity import select_best_node
from blockhost_backend.services.provisioning import provision_resource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps", tags=["apps"])
legacy_router = APIRouter(prefix="/api/deployments", tags=["deployments"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateDeploymentRequest(BaseModel):
    name: str = Field(..., max_length=100)
    deployment_kind: DeploymentKind = DeploymentKind.docker_image
    source_type: SourceType = SourceType.docker_image
    
    docker_image: str | None = Field(None, max_length=200)
    github_repo_url: str | None = Field(None, max_length=512)
    github_branch: str = Field("main", max_length=128)
    
    internal_port: int | None = Field(None, ge=1, le=65535)
    ram_limit_mb: int = Field(..., ge=128)
    cpu_limit: float = Field(0.5, ge=0.1)
    
    build_command: str | None = None
    start_command: str | None = None
    install_command: str | None = None
    output_directory: str | None = None
    runtime_version: str | None = None
    health_check_path: str | None = None
    startup_timeout_seconds: int = 120
    project_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def normalize_source(self) -> "CreateDeploymentRequest":
        if self.github_repo_url and self.source_type == SourceType.docker_image and not self.docker_image:
            self.source_type = SourceType.github
        if self.source_type == SourceType.github and not self.github_repo_url:
            raise ValueError("github_repo_url is required for GitHub deployments")
        if self.source_type == SourceType.docker_image and not self.docker_image:
            raise ValueError("docker_image is required for Docker image deployments")
        return self

class DeploymentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    owner_id: uuid.UUID
    node_id: uuid.UUID | None = None
    node_ip: str | None = None
    project_id: uuid.UUID | None = None
    name: str
    
    deployment_kind: DeploymentKind
    source_type: SourceType
    
    docker_image: str | None = None
    internal_port: int
    ram_limit_mb: int
    cpu_limit: float
    storage_limit_mb: int = 5120
    state: str
    health_status: str = "unknown"
    restart_count: int = 0
    last_healthy_at: datetime | None = None
    error_message: str | None = None
    container_id: str | None = None
    host_port: int | None = None
    volume_path: str | None = None
    volume_mount_path: str | None = None
    last_network_rx: int | None = None
    github_repo_url: str | None = None
    github_branch: str = "main"
    
    build_command: str | None = None
    start_command: str | None = None
    install_command: str | None = None
    output_directory: str | None = None
    runtime_version: str | None = None
    health_check_path: str | None = None
    startup_timeout_seconds: int = 120

    created_at: datetime
    updated_at: datetime
    
    deploy_webhook_token: str | None = None
    template_id: str | None = None

    @computed_field
    @property
    def default_domain(self) -> str | None:
        from blockhost_backend.config.config_manager import get_settings
        from blockhost_backend.services.ingress import IngressService
        return IngressService.default_app_domain(str(self.id), get_settings().public_app_base_domain)

    @computed_field
    @property
    def public_url(self) -> str | None:
        from blockhost_backend.config.config_manager import get_settings
        from blockhost_backend.services.ingress import IngressService
        url = IngressService.default_app_url(str(self.id), get_settings().public_app_base_domain)
        if url:
            return url
        if self.node_ip and self.host_port:
            return f"http://{self.node_ip}:{self.host_port}"
        return None

class RevisionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    image_tag: str | None = None
    commit_sha: str | None = None
    status: DeploymentState
    is_active: bool = False
    trigger: str = "manual"
    error_message: str | None = None
    deployed_at: datetime | None = None
    created_at: datetime

class EnvVarOut(BaseModel):
    key: str
    value: str

class RollbackRequest(BaseModel):
    revision_id: uuid.UUID | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = re.compile(
    r"(password|secret|token|key|private|credential|auth)",
    re.IGNORECASE,
)

def _mask_value(key: str, value: str) -> str:
    """Mask sensitive env var values unless they look safe."""
    if _SECRET_PATTERNS.search(key):
        if len(value) <= 4:
            return "****"
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
    # Show non-secret values in full
    return value

def _get_deployment(db: Session, deployment_id: uuid.UUID, user: User) -> AppDeployment:
    dep = db.get(AppDeployment, deployment_id)
    if not dep or dep.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return dep

def _deactivate_old_revisions(db: Session, deployment_id: uuid.UUID) -> None:
    """Mark all revisions for this deployment as inactive."""
    old_revisions = db.execute(
        select(DeploymentRevision).where(
            DeploymentRevision.deployment_id == deployment_id,
            DeploymentRevision.is_active == True,
        )
    ).scalars().all()
    for rev in old_revisions:
        rev.is_active = False

# ---------------------------------------------------------------------------
# CRUD Routes
# ---------------------------------------------------------------------------

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
    node = select_best_node(db, required_ram_mb=payload.ram_limit_mb)
    if not node:
        raise HTTPException(status_code=503, detail="No capacity available")

    dep_id = uuid.uuid4()
    
    resolved_image = payload.docker_image
    if payload.source_type == SourceType.github:
        resolved_image = f"blockhost-build-{dep_id}"

    # Default framework settings
    build_cmd = payload.build_command
    start_cmd = payload.start_command
    install_cmd = payload.install_command
    port = payload.internal_port
    
    if payload.deployment_kind == DeploymentKind.nextjs:
        if not build_cmd: build_cmd = "npm run build"
        if not install_cmd: install_cmd = "npm install"
        if not start_cmd: start_cmd = "npm start"
        if not port: port = 3000
    elif payload.deployment_kind == DeploymentKind.fastapi:
        if not start_cmd: start_cmd = "uvicorn main:app --host 0.0.0.0 --port $PORT"
        if not port: port = 8000
    elif payload.deployment_kind == DeploymentKind.static_site:
        if not port: port = 80
        
    if not port:
        port = 8080 # default fallback

    dep = AppDeployment(
        id=dep_id,
        owner_id=user.id,
        node_id=node.id,
        project_id=payload.project_id,
        name=payload.name,
        deployment_kind=payload.deployment_kind,
        source_type=payload.source_type,
        docker_image=resolved_image,
        internal_port=port,
        ram_limit_mb=payload.ram_limit_mb,
        cpu_limit=payload.cpu_limit,
        state=DeploymentState.created,
        volume_path=f"/opt/blockhost/agent_storage/deployments/{dep_id}",
        github_repo_url=payload.github_repo_url,
        github_branch=payload.github_branch,
        build_command=build_cmd,
        start_command=start_cmd,
        install_command=install_cmd,
        output_directory=payload.output_directory,
        runtime_version=payload.runtime_version,
        health_check_path=payload.health_check_path,
        startup_timeout_seconds=payload.startup_timeout_seconds,
        env_vars={},
        deploy_webhook_token=uuid.uuid4().hex
    )
    db.add(dep)
    
    revision = DeploymentRevision(
        deployment_id=dep_id,
        status=DeploymentState.created,
        is_active=True,
        trigger=RevisionTrigger.manual,
    )
    db.add(revision)
    
    db.commit()
    db.refresh(dep)

    background_tasks.add_task(provision_resource, "app_deployment", str(dep.id))

    return dep

@router.get("/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_deployment(db, deployment_id, user)


@router.patch("/{deployment_id}", response_model=DeploymentOut)
def update_deployment(
    deployment_id: uuid.UUID,
    payload: CreateDeploymentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    # Update mutable fields
    for field in ("name", "build_command", "start_command", "install_command",
                  "output_directory", "runtime_version", "health_check_path",
                  "internal_port", "ram_limit_mb", "cpu_limit", "github_branch",
                  "startup_timeout_seconds"):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(dep, field, val)
    db.commit()
    db.refresh(dep)
    return dep


# ---------------------------------------------------------------------------
# Deploy / Stop / Rollback
# ---------------------------------------------------------------------------

@router.post("/{deployment_id}/deploy", response_model=DeploymentOut)
@router.post("/{deployment_id}/restart", response_model=DeploymentOut)
def restart_deployment(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    dep.state = DeploymentState.building
    
    _deactivate_old_revisions(db, dep.id)
    revision = DeploymentRevision(
        deployment_id=dep.id,
        status=DeploymentState.building,
        is_active=True,
        trigger=RevisionTrigger.manual,
        env_snapshot=dict(dep.env_vars) if dep.env_vars else None,
    )
    db.add(revision)
    db.commit()

    if not dep.node_id:
        node = select_best_node(db, required_ram_mb=dep.ram_limit_mb)
        if not node:
            raise HTTPException(status_code=503, detail="No capacity available")
        dep.node_id = node.id
        db.commit()

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
    dep.state = DeploymentState.stopped
    db.commit()
    if dep.node_id:
        try:
            node = db.get(Node, dep.node_id)
            if node and node.ip_address:
                settings = get_settings()
                agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/stop"
                httpx.post(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        except Exception as e:
            logger.error("Failed to stop deployment %s: %s", dep.id, e)
    dep.state = DeploymentState.suspended
    db.commit()
    db.refresh(dep)
    return dep

@router.post("/{deployment_id}/rollback", response_model=DeploymentOut)
def rollback_deployment(
    deployment_id: uuid.UUID,
    payload: RollbackRequest = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    
    target_revision = None
    if payload and payload.revision_id:
        # Rollback to a specific revision
        target_revision = db.get(DeploymentRevision, payload.revision_id)
        if not target_revision or target_revision.deployment_id != dep.id:
            raise HTTPException(status_code=404, detail="Revision not found")
        if not target_revision.image_tag:
            raise HTTPException(status_code=400, detail="Revision has no image tag to roll back to")
    else:
        # Rollback to the last successful revision that is not the current active one
        revisions = db.execute(
            select(DeploymentRevision).where(
                DeploymentRevision.deployment_id == dep.id,
                DeploymentRevision.status == DeploymentState.running,
                DeploymentRevision.is_active == False,
                DeploymentRevision.image_tag.isnot(None),
            ).order_by(DeploymentRevision.created_at.desc())
        ).scalars().all()
        if not revisions:
            raise HTTPException(status_code=400, detail="No previous successful revision to roll back to")
        target_revision = revisions[0]

    # Set the deployment image to the target revision's image
    dep.docker_image = target_revision.image_tag
    dep.state = DeploymentState.building
    
    # Restore env vars from snapshot if available
    if target_revision.env_snapshot:
        dep.env_vars = target_revision.env_snapshot
    
    _deactivate_old_revisions(db, dep.id)
    new_revision = DeploymentRevision(
        deployment_id=dep.id,
        image_tag=target_revision.image_tag,
        status=DeploymentState.building,
        is_active=True,
        trigger=RevisionTrigger.rollback,
        env_snapshot=dict(dep.env_vars) if dep.env_vars else None,
    )
    db.add(new_revision)
    db.commit()

    provision_resource("app_deployment", str(dep.id))
    db.refresh(dep)
    return dep

# ---------------------------------------------------------------------------
# Complete Delete Flow
# ---------------------------------------------------------------------------

@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    settings = get_settings()
    
    # 1. Stop the container on the agent
    if dep.node_id:
        node = db.get(Node, dep.node_id)
        if node and node.ip_address:
            try:
                agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/stop"
                httpx.post(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
            except Exception as e:
                logger.warning("Failed to stop container for deployment %s during delete: %s", dep.id, e)
            
            # Clean up the volume on the agent
            try:
                vol_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/delete-volume"
                httpx.post(vol_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
            except Exception as e:
                logger.warning("Failed to delete volume for deployment %s: %s", dep.id, e)

    # 2. Unregister all custom domains from Caddy
    from blockhost_backend.services.ingress import ingress_service
    import asyncio
    domains = db.execute(
        select(CustomDomain).where(CustomDomain.deployment_id == dep.id)
    ).scalars().all()
    for d in domains:
        try:
            asyncio.run(ingress_service.unregister_route(d.domain))
        except Exception as e:
            logger.warning("Failed to unregister domain %s: %s", d.domain, e)

    # 3. Cancel any billing subscriptions linked to this resource
    try:
        subs = db.execute(
            select(BillingSubscription).where(
                BillingSubscription.resource_type == "app_deployment",
                BillingSubscription.resource_id == str(dep.id),
            )
        ).scalars().all()
        for sub in subs:
            sub.status = "cancelled"
    except Exception as e:
        logger.warning("Failed to cancel billing for deployment %s: %s", dep.id, e)

    # 4. Delete all revisions, domains, and the deployment itself (cascade handles revisions + domains)
    db.delete(dep)
    db.commit()
    return None

# ---------------------------------------------------------------------------
# Revisions & Deploy History
# ---------------------------------------------------------------------------

@router.get("/{deployment_id}/revisions", response_model=List[RevisionOut])
def get_revisions(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    revisions = db.execute(
        select(DeploymentRevision).where(
            DeploymentRevision.deployment_id == dep.id
        ).order_by(DeploymentRevision.created_at.desc())
    ).scalars().all()
    return revisions

# ---------------------------------------------------------------------------
# Environment Variables (Encrypted + Masked)
# ---------------------------------------------------------------------------

@router.get("/{deployment_id}/env")
def get_env_vars(
    deployment_id: uuid.UUID,
    reveal: bool = Query(False, description="Set to true to show unmasked values"),
    decrypt: bool = Query(False, description="Backward-compatible alias for reveal"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    env = dep.env_vars or {}
    if reveal or decrypt:
        return {"env_vars": env}
    # Mask sensitive values
    masked = {k: _mask_value(k, v) for k, v in env.items()}
    return {"env_vars": masked}

@router.put("/{deployment_id}/env")
def update_env_vars(
    deployment_id: uuid.UUID,
    env_vars: Dict[str, str],
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    dep.env_vars = env_vars
    
    # Auto-redeploy when env vars change (like Render/Railway)
    if dep.state == DeploymentState.running and dep.node_id:
        dep.state = DeploymentState.building
        _deactivate_old_revisions(db, dep.id)
        revision = DeploymentRevision(
            deployment_id=dep.id,
            image_tag=dep.docker_image,
            status=DeploymentState.building,
            is_active=True,
            trigger=RevisionTrigger.env_change,
            env_snapshot=dict(env_vars),
        )
        db.add(revision)
        db.commit()
        background_tasks.add_task(provision_resource, "app_deployment", str(dep.id))
    else:
        db.commit()
    
    return {"env_vars": env_vars, "redeploying": dep.state == DeploymentState.building}

@router.delete("/{deployment_id}/env/{key}")
def delete_env_var(
    deployment_id: uuid.UUID,
    key: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    env = dict(dep.env_vars or {})
    if key not in env:
        raise HTTPException(status_code=404, detail=f"Env var '{key}' not found")
    del env[key]
    dep.env_vars = env
    
    if dep.state == DeploymentState.running and dep.node_id:
        dep.state = DeploymentState.building
        _deactivate_old_revisions(db, dep.id)
        revision = DeploymentRevision(
            deployment_id=dep.id,
            image_tag=dep.docker_image,
            status=DeploymentState.building,
            is_active=True,
            trigger=RevisionTrigger.env_change,
            env_snapshot=dict(env),
        )
        db.add(revision)
        db.commit()
        background_tasks.add_task(provision_resource, "app_deployment", str(dep.id))
    else:
        db.commit()
    
    return {"status": "deleted", "key": key}

# ---------------------------------------------------------------------------
# Logs (Build / Runtime / Crash)
# ---------------------------------------------------------------------------

@router.get("/{deployment_id}/logs")
def get_deployment_logs(
    deployment_id: uuid.UUID,
    type: str = Query("runtime", description="Log type: runtime, build, or crash"),
    tail: int = Query(100, ge=1, le=5000),
    revision_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    
    if type == "build":
        # Return build logs from the latest (or specified) revision
        if revision_id:
            rev = db.get(DeploymentRevision, revision_id)
        else:
            rev = db.execute(
                select(DeploymentRevision).where(
                    DeploymentRevision.deployment_id == dep.id
                ).order_by(DeploymentRevision.created_at.desc())
            ).scalars().first()
        if not rev or not rev.build_logs:
            return {"lines": [], "type": "build"}
        lines = rev.build_logs.splitlines()[-tail:]
        return {"lines": lines, "type": "build"}
    
    if type == "crash":
        # Return logs from the most recent failed revision
        rev = db.execute(
            select(DeploymentRevision).where(
                DeploymentRevision.deployment_id == dep.id,
                DeploymentRevision.status.in_([DeploymentState.error, DeploymentState.crash_loop]),
            ).order_by(DeploymentRevision.created_at.desc())
        ).scalars().first()
        if not rev:
            return {"lines": [], "type": "crash"}
        logs = rev.build_logs or rev.runtime_logs or rev.error_message or ""
        lines = logs.splitlines()[-tail:]
        return {"lines": lines, "type": "crash"}

    # type == "runtime": fetch live logs from agent
    if not dep.node_id:
        return {"lines": [], "type": "runtime"}

    node = db.get(Node, dep.node_id)
    if not node or not node.ip_address:
        return {"lines": [], "type": "runtime"}

    try:
        settings = get_settings()
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/logs?type={type}&tail={tail}"
        response = httpx.get(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        # Normalize response format
        if isinstance(data, dict) and isinstance(data.get("lines"), list):
            return {"lines": [str(line) for line in data["lines"]], "type": "runtime"}
        if isinstance(data, list):
            return {"lines": [str(line) for line in data], "type": "runtime"}
        return {"lines": [], "type": "runtime"}
    except Exception as e:
        logger.warning("Failed to fetch logs for deployment %s: %s", dep.id, e)
        return {"lines": [], "type": "runtime", "error": str(e)}

@router.websocket("/{deployment_id}/logs/ws")
@legacy_router.websocket("/{deployment_id}/logs/ws")
async def ws_deployment_logs_proxy(
    websocket: WebSocket,
    deployment_id: str,
    token: str | None = None,
    db: Session = Depends(get_db),
):
    user = authenticate_ws_user(token=token, db=db)
    if user is None:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    try:
        dep_uuid = uuid.UUID(deployment_id)
    except ValueError:
        await websocket.close(code=4004, reason="Invalid deployment ID")
        return

    dep = db.get(AppDeployment, dep_uuid)
    if not dep or dep.owner_id != user.id:
        await websocket.close(code=4003, reason="Not authorized")
        return

    if not dep.node_id:
        await websocket.close(code=4000, reason="Deployment is not assigned to a node")
        return

    node = db.get(Node, dep.node_id)
    if not node or not node.ip_address:
        await websocket.close(code=4000, reason="Node is offline or missing IP")
        return

    await websocket.accept()
    settings = get_settings()

    agent_ws_url = f"ws://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/logs/ws?token={settings.worker_agent_token}"

    import websockets
    try:
        async with websockets.connect(agent_ws_url, ping_interval=20, ping_timeout=20) as agent_ws:
            import asyncio
            async def forward_from_agent():
                try:
                    async for message in agent_ws:
                        await websocket.send_text(message)
                except websockets.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.warning(f"Error forwarding from agent for dep {dep.id}: {e}")

            async def forward_from_client():
                try:
                    while True:
                        await websocket.receive_text()
                except WebSocketDisconnect:
                    await agent_ws.close()
                except Exception:
                    pass

            task1 = asyncio.create_task(forward_from_agent())
            task2 = asyncio.create_task(forward_from_client())
            done, pending = await asyncio.wait([task1, task2], return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()

    except Exception as e:
        logger.warning(f"Failed to connect to agent WS for {dep.id}: {e}")
        try:
            await websocket.send_json({"type": "error", "error": "Agent connection failed"})
            await websocket.close(code=1011, reason="Agent connection failed")
        except:
            pass

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/{deployment_id}/metrics")
def get_metrics(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dep = _get_deployment(db, deployment_id, user)
    if not dep.node_id:
        return {"running": False}
    node = db.get(Node, dep.node_id)
    if not node or not node.ip_address:
        return {"running": False}
    try:
        settings = get_settings()
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{dep.id}/metrics"
        response = httpx.get(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=5.0)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning("Failed to fetch metrics for deployment %s: %s", dep.id, e)
        return {"running": False, "error": str(e)}

@router.post("/{deployment_id}/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    deployment_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    token: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Receive GitHub webhook push events and trigger a deployment.
    Requires ?token= match to deploy_webhook_token.
    """
    dep = db.get(AppDeployment, deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Not Found")
        
    if not dep.deploy_webhook_token:
        raise HTTPException(status_code=400, detail="Webhook not configured for this app")
        
    if token != dep.deploy_webhook_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    
    # Check if this is a push event (GitHub sets x-github-event header, or we can just infer by ref)
    event_type = request.headers.get("x-github-event", "push")
    if event_type == "ping":
        return {"status": "pong"}
        
    if event_type != "push":
        return {"status": "ignored", "reason": f"ignoring {event_type} event"}

    ref = payload.get("ref", "")
    target_ref = f"refs/heads/{dep.github_branch}"
    if ref and ref != target_ref:
        return {"status": "ignored", "reason": f"push to {ref} ignored, expected {target_ref}"}
    
    # Extract commit sha if available
    commit_sha = payload.get("after")

    # Trigger deployment
    _deactivate_old_revisions(db, deployment_id)
    
    rev = DeploymentRevision(
        deployment_id=dep.id,
        status=DeploymentState.created,
        is_active=True,
        trigger=RevisionTrigger.github_push,
        commit_sha=commit_sha
    )
    db.add(rev)
    dep.state = DeploymentState.created
    db.commit()
    db.refresh(dep)
    
    background_tasks.add_task(provision_resource, "app_deployment", str(dep.id))
    logger.info("Triggered auto-deploy for %s via webhook (commit: %s)", dep.id, commit_sha)
    
    return {"status": "accepted", "message": "Deployment triggered"}

# ---------------------------------------------------------------------------
# Legacy router aliases
# ---------------------------------------------------------------------------

legacy_router.add_api_route("", list_deployments, methods=["GET"], response_model=List[DeploymentOut])
legacy_router.add_api_route("", create_deployment, methods=["POST"], response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
legacy_router.add_api_route("/{deployment_id}", get_deployment, methods=["GET"], response_model=DeploymentOut)
legacy_router.add_api_route("/{deployment_id}/start", restart_deployment, methods=["POST"], response_model=DeploymentOut)
legacy_router.add_api_route("/{deployment_id}/stop", stop_deployment, methods=["POST"], response_model=DeploymentOut)
legacy_router.add_api_route("/{deployment_id}/logs", get_deployment_logs, methods=["GET"])
legacy_router.add_api_route("/{deployment_id}/env", get_env_vars, methods=["GET"])
legacy_router.add_api_route("/{deployment_id}/env", update_env_vars, methods=["PUT"])
legacy_router.add_api_route("/{deployment_id}/env/{key}", delete_env_var, methods=["DELETE"])
