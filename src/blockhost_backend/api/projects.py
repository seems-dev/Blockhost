import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Project, DatabaseInstance, AppDeployment, User
from blockhost_backend.services.provisioning import provision_resource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

class ConnectDatabaseRequest(BaseModel):
    database_id: uuid.UUID
    app_id: uuid.UUID


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class ProjectOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime


def _get_project(project_id: uuid.UUID, user: User, db: Session) -> Project:
    project = db.get(Project, project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=List[ProjectOut])
def list_projects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())
    ).scalars().all()


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: CreateProjectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = Project(owner_id=user.id, name=payload.name.strip())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_project(project_id, user, db)

@router.post("/{project_id}/connect-database", status_code=status.HTTP_200_OK)
def connect_database(
    project_id: uuid.UUID,
    payload: ConnectDatabaseRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project(project_id, user, db)

    db_instance = db.get(DatabaseInstance, payload.database_id)
    if not db_instance or db_instance.project_id != project.id:
        raise HTTPException(status_code=404, detail="Database not found in this project")

    app = db.get(AppDeployment, payload.app_id)
    if not app or app.project_id != project.id:
        raise HTTPException(status_code=404, detail="App not found in this project")

    # Generate connection URL or specific variables
    password = db_instance.db_password or ""
    scheme = "postgresql" if db_instance.engine.value == "postgresql" else db_instance.engine.value
    url = f"{scheme}://{db_instance.db_username}:{password}@{db_instance.internal_hostname}:{db_instance.internal_port}/{db_instance.db_name}"
    
    # Inject variables
    env_vars = app.env_vars.copy() if app.env_vars else {}
    env_vars["DATABASE_URL"] = url
    
    # Optional: engine specific
    if db_instance.engine.value == "redis":
        env_vars["REDIS_URL"] = url
        
    app.env_vars = env_vars
    db.commit()

    # Trigger redeploy
    provision_resource("app_deployment", str(app.id))
    
    return {"status": "success", "message": "Database connected and app redeployment triggered"}
