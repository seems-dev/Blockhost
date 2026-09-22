import logging
import uuid
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import (
    AppDeployment,
    DatabaseInstance,
    DatabaseEngine,
    DeploymentKind,
    DeploymentState,
    SourceType,
    User,
    Project,
    DeploymentRevision,
    RevisionTrigger,
)
from blockhost_backend.services.provisioning import provision_resource
from blockhost_backend.services.node_capacity import select_best_node

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateDef(BaseModel):
    id: str
    name: str
    description: str
    logo_url: str | None = None
    is_database: bool
    
    # App specific
    docker_image: str | None = None
    internal_port: int | None = None
    default_env_vars: dict[str, str] = {}
    
    # Database specific
    engine: str | None = None
    version: str | None = None


# Static Template Registry
TEMPLATES: List[TemplateDef] = [
    TemplateDef(
        id="redis",
        name="Redis",
        description="An in-memory data structure store, used as a distributed, in-memory key–value database, cache and message broker.",
        logo_url="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/redis.svg",
        is_database=True,
        engine="redis",
        version="latest"
    ),
    TemplateDef(
        id="postgres",
        name="PostgreSQL",
        description="The World's Most Advanced Open Source Relational Database.",
        logo_url="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/postgres.svg",
        is_database=True,
        engine="postgresql",
        version="15"
    ),
    TemplateDef(
        id="mysql",
        name="MySQL",
        description="The world's most popular open source database.",
        logo_url="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/mysql.svg",
        is_database=True,
        engine="mysql",
        version="8.0"
    ),
    TemplateDef(
        id="ghost",
        name="Ghost CMS",
        description="Turn your audience into a business. Publishing, newsletters, subscriptions and memberships.",
        logo_url="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/ghost.svg",
        is_database=False,
        docker_image="ghost:latest",
        internal_port=2368,
        default_env_vars={
            "url": "http://localhost:2368",
            "database__client": "sqlite3",
            "database__connection__filename": "/var/lib/ghost/content/data/ghost.db",
            "database__useNullAsDefault": "true",
            "database__debug": "false"
        }
    ),
    TemplateDef(
        id="wordpress",
        name="WordPress",
        description="WordPress is open source software you can use to create a beautiful website, blog, or app.",
        logo_url="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/svg/wordpress.svg",
        is_database=False,
        docker_image="wordpress:latest",
        internal_port=80,
        default_env_vars={
            "WORDPRESS_DB_HOST": "localhost",
            "WORDPRESS_DB_USER": "example",
            "WORDPRESS_DB_PASSWORD": "example_password",
            "WORDPRESS_DB_NAME": "example"
        }
    )
]


@router.get("", response_model=List[TemplateDef])
def list_templates() -> Any:
    return TEMPLATES


class DeployTemplateRequest(BaseModel):
    project_id: uuid.UUID
    name: str | None = None
    ram_limit_mb: int = 512
    cpu_limit: float = 1.0
    storage_limit_mb: int = 5120
    
    # Optional overrides
    env_vars: dict[str, str] | None = None


@router.post("/{template_id}/deploy", status_code=status.HTTP_201_CREATED)
def deploy_template(
    template_id: str,
    payload: DeployTemplateRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Instantiate a 1-click template in a project."""
    template = next((t for t in TEMPLATES if t.id == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
        
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
        
    node = select_best_node(db, required_ram_mb=payload.ram_limit_mb)
    if not node:
        raise HTTPException(status_code=503, detail="No capacity available")
        
    final_name = payload.name or f"{template.name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
    
    if template.is_database:
        # Create DatabaseInstance
        import secrets
        import string
        def gen_password(length=16):
            chars = string.ascii_letters + string.digits
            return ''.join(secrets.choice(chars) for _ in range(length))
            
        db_username = "root" if template.engine in ("mysql", "mariadb") else "postgres"
        if template.engine == "redis":
            db_username = "default"
            
        db_password = gen_password()
        db_name = template.engine if template.engine != "redis" else "0"
        
        db_id = uuid.uuid4()
        db_instance = DatabaseInstance(
            id=db_id,
            project_id=project.id,
            node_id=node.id,
            name=final_name,
            engine=DatabaseEngine(template.engine),
            version=template.version,
            db_name=db_name,
            db_username=db_username,
            db_password=db_password,
            internal_hostname=f"db-{db_id.hex[:8]}",
            internal_port=6379 if template.engine == "redis" else (5432 if template.engine == "postgresql" else 3306),
            ram_limit_mb=payload.ram_limit_mb,
            cpu_limit=payload.cpu_limit,
            storage_limit_mb=payload.storage_limit_mb,
            volume_path=f"/var/lib/blockhost/databases/{db_id}",
            state=DeploymentState.created,
            template_id=template.id,
        )
        db.add(db_instance)
        db.commit()
        db.refresh(db_instance)
        
        background_tasks.add_task(provision_resource, "database_instance", str(db_instance.id))
        return {"status": "created", "id": db_instance.id, "type": "database"}
        
    else:
        # Create AppDeployment
        dep_id = uuid.uuid4()
        
        final_env = template.default_env_vars.copy()
        if payload.env_vars:
            final_env.update(payload.env_vars)
            
        dep = AppDeployment(
            id=dep_id,
            owner_id=user.id,
            node_id=node.id,
            project_id=project.id,
            name=final_name,
            deployment_kind=DeploymentKind.docker_image,
            source_type=SourceType.docker_image,
            docker_image=template.docker_image,
            internal_port=template.internal_port,
            ram_limit_mb=payload.ram_limit_mb,
            cpu_limit=payload.cpu_limit,
            storage_limit_mb=payload.storage_limit_mb,
            state=DeploymentState.created,
            volume_path=f"/var/lib/blockhost/deployments/{dep_id}",
            env_vars=final_env,
            deploy_webhook_token=uuid.uuid4().hex,
            template_id=template.id,
        )
        db.add(dep)
        
        rev = DeploymentRevision(
            deployment_id=dep_id,
            status=DeploymentState.created,
            is_active=True,
            trigger=RevisionTrigger.manual,
        )
        db.add(rev)
        
        db.commit()
        db.refresh(dep)
        
        background_tasks.add_task(provision_resource, "app_deployment", str(dep.id))
        return {"status": "created", "id": dep.id, "type": "app"}
