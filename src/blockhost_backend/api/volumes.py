import logging
import uuid
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import AppDeployment, DeploymentState, User
from blockhost_backend.services.s3_storage import list_s3_objects

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps", tags=["app_volumes"])

def _get_app(db: Session, deployment_id: uuid.UUID, user: User) -> AppDeployment:
    app_dep = db.get(AppDeployment, deployment_id)
    if not app_dep or app_dep.owner_id != user.id:
        raise HTTPException(status_code=404, detail="App not found")
    return app_dep


class VolumeSizeOut(BaseModel):
    size_bytes: int
    size_mb: int


@router.get("/{deployment_id}/volume", response_model=VolumeSizeOut)
def get_volume_info(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    app_dep = _get_app(db, deployment_id, user)
    node = app_dep.node
    if not node or not node.ip_address:
        return {"size_bytes": 0, "size_mb": 0}

    try:
        settings = get_settings()
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{app_dep.id}/volume-size"
        resp = httpx.get(agent_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, timeout=10.0)
        resp.raise_for_status()
        size_bytes = resp.json().get("size_bytes", 0)
        return {"size_bytes": size_bytes, "size_mb": size_bytes // (1024 * 1024)}
    except Exception as e:
        logger.warning("Failed to get volume size from agent: %s", e)
        return {"size_bytes": 0, "size_mb": 0}


class SnapshotRequest(BaseModel):
    name: str | None = None

class SnapshotResponse(BaseModel):
    status: str
    s3_key: str


class SnapshotItem(BaseModel):
    s3_key: str
    timestamp: int
    created_at: str

@router.get("/{deployment_id}/volume/snapshots", response_model=List[SnapshotItem])
def list_volume_snapshots(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app_dep = _get_app(db, deployment_id, user)
    prefix = f"deployments/{app_dep.id}/snapshots/"
    keys = list_s3_objects(prefix)
    
    results = []
    for k in keys:
        basename = k.split("/")[-1]
        ts_str = basename.replace(".tar.gz", "")
        try:
            ts = int(ts_str)
            from datetime import datetime
            import pytz
            created_at = datetime.fromtimestamp(ts, pytz.utc).isoformat()
        except Exception:
            ts = 0
            created_at = ""
        results.append({
            "s3_key": k,
            "timestamp": ts,
            "created_at": created_at
        })
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    return results


@router.post("/{deployment_id}/volume/snapshot", response_model=SnapshotResponse)
def create_volume_snapshot(
    deployment_id: uuid.UUID,
    payload: SnapshotRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    app_dep = _get_app(db, deployment_id, user)
    node = app_dep.node
    if not node or not node.ip_address:
        raise HTTPException(status_code=400, detail="App is not assigned to a node")
        
    if app_dep.state == DeploymentState.running:
        raise HTTPException(status_code=400, detail="Please stop the app before taking a snapshot to prevent data corruption")

    try:
        settings = get_settings()
        # Format: deployments/{id}/snapshots/{timestamp}.tar.gz
        import time
        ts = int(time.time())
        s3_key = f"deployments/{app_dep.id}/snapshots/{ts}.tar.gz"
        
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{app_dep.id}/volume-snapshot"
        resp = httpx.post(
            agent_url, 
            json={"s3_key": s3_key},
            headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, 
            timeout=300.0
        )
        resp.raise_for_status()
        return {"status": "success", "s3_key": s3_key}
    except Exception as e:
        logger.error("Failed to create snapshot via agent: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create volume snapshot")


class RestoreRequest(BaseModel):
    s3_key: str

@router.post("/{deployment_id}/volume/restore")
def restore_volume_snapshot(
    deployment_id: uuid.UUID,
    payload: RestoreRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    app_dep = _get_app(db, deployment_id, user)
    node = app_dep.node
    if not node or not node.ip_address:
        raise HTTPException(status_code=400, detail="App is not assigned to a node")
        
    if app_dep.state == DeploymentState.running:
        raise HTTPException(status_code=400, detail="Please stop the app before restoring a snapshot")

    try:
        settings = get_settings()
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{app_dep.id}/volume-restore"
        resp = httpx.post(
            agent_url, 
            json={"s3_key": payload.s3_key},
            headers={"Authorization": f"Bearer {settings.worker_agent_token}"}, 
            timeout=300.0
        )
        resp.raise_for_status()
        return {"status": "success"}
    except Exception as e:
        logger.error("Failed to restore snapshot via agent: %s", e)
        raise HTTPException(status_code=500, detail="Failed to restore volume snapshot")
