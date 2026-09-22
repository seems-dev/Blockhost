from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import uuid
import httpx
from datetime import datetime
import logging

from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import DatabaseInstance, Node, Project, User
from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.s3_storage import list_s3_objects

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/databases", tags=["database-backups"])

def _get_s3_prefix(db_id: uuid.UUID) -> str:
    return f"databases/{db_id}/backups/"


def _database_password(db_instance: DatabaseInstance) -> str:
    return db_instance.db_password or ""

@router.get("/{db_id}/backups")
def list_backups(db_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db_instance = db.get(DatabaseInstance, db_id)
    if not db_instance:
        raise HTTPException(status_code=404, detail="Database not found")
    project = db.get(Project, db_instance.project_id)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    prefix = _get_s3_prefix(db_id)
    keys = list_s3_objects(prefix)
    
    # Strip prefix and return
    backups = []
    for key in keys:
        if key.startswith(prefix):
            name = key[len(prefix):]
            if name:
                backups.append({"id": name, "s3_key": key})
    return {"backups": backups}

@router.post("/{db_id}/backup")
def trigger_backup(db_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db_instance = db.get(DatabaseInstance, db_id)
    if not db_instance:
        raise HTTPException(status_code=404, detail="Database not found")
    project = db.get(Project, db_instance.project_id)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    node = db.get(Node, db_instance.node_id)
    if not node:
        raise HTTPException(status_code=400, detail="Database is not assigned to a node")
        
    settings = get_settings()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    s3_key = f"{_get_s3_prefix(db_id)}{timestamp}.backup"
    
    agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{db_id}/backup"
    
    payload = {
        "engine": db_instance.engine.value,
        "username": db_instance.db_username,
        "password": _database_password(db_instance),
        "database": db_instance.db_name,
        "s3_key": s3_key
    }
    
    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                agent_url,
                headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                json=payload
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to trigger backup for %s: %s", db_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to trigger backup on agent: {e}")
        
    return {"status": "ok", "backup_id": f"{timestamp}.backup"}

@router.post("/{db_id}/restore")
def restore_backup(db_id: uuid.UUID, backup_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db_instance = db.get(DatabaseInstance, db_id)
    if not db_instance:
        raise HTTPException(status_code=404, detail="Database not found")
    project = db.get(Project, db_instance.project_id)
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    node = db.get(Node, db_instance.node_id)
    if not node:
        raise HTTPException(status_code=400, detail="Database is not assigned to a node")
        
    settings = get_settings()
    s3_key = f"{_get_s3_prefix(db_id)}{backup_id}"
    
    agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{db_id}/restore"
    
    payload = {
        "engine": db_instance.engine.value,
        "username": db_instance.db_username,
        "password": _database_password(db_instance),
        "database": db_instance.db_name,
        "s3_key": s3_key
    }
    
    try:
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                agent_url,
                headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                json=payload
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to restore backup for %s: %s", db_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to restore backup on agent: {e}")
        
    return {"status": "ok"}
