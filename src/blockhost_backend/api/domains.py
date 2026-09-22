"""
Custom Domain Management API for PaaS Web App Deployments.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import AppDeployment, CustomDomain, DeploymentState, DomainStatus, Node, User
from blockhost_backend.services.ingress import ingress_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["custom_domains"])

# RFC 1035 / RFC 1123 domain regex
_DOMAIN_REGEX = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateDomainRequest(BaseModel):
    domain: str = Field(..., json_schema_extra={"example": "app.example.com"})

    @field_validator("domain")
    @classmethod
    def validate_domain_name(cls, v: str) -> str:
        domain = v.strip().lower()
        if not _DOMAIN_REGEX.match(domain):
            raise ValueError("Invalid domain name format. Must be a valid hostname like app.example.com")
        return domain


class DomainOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    deployment_id: uuid.UUID
    domain: str
    status: DomainStatus
    verification_token: str
    ssl_active: bool
    error_message: str | None = None
    dns_instructions: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_deployment_for_user(deployment_id: uuid.UUID, user: User, db: Session) -> AppDeployment:
    deployment = db.get(AppDeployment, deployment_id)
    if not deployment or deployment.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return deployment


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/deployments/{deployment_id}/domains",
    response_model=DomainOut,
    status_code=status.HTTP_201_CREATED,
)
def attach_custom_domain(
    deployment_id: uuid.UUID,
    payload: CreateDomainRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DomainOut:
    """Attach a new custom domain to a deployment in pending_dns status."""
    deployment = _get_deployment_for_user(deployment_id, user, db)

    # Check if domain already attached to any deployment
    existing = db.execute(
        select(CustomDomain).where(CustomDomain.domain == payload.domain)
    ).scalars().first()
    if existing:
        if existing.deployment_id == deployment.id:
            raise HTTPException(status_code=409, detail="Domain is already attached to this deployment")
        raise HTTPException(status_code=409, detail="Domain is already registered to another deployment")

    token = uuid.uuid4().hex
    domain_rec = CustomDomain(
        deployment_id=deployment.id,
        domain=payload.domain,
        status=DomainStatus.pending_dns,
        verification_token=token,
        ssl_active=False,
    )
    db.add(domain_rec)
    db.commit()
    db.refresh(domain_rec)
    logger.info("Attached domain %s (token=%s) to deployment %s", payload.domain, token, deployment.id)
    
    out = DomainOut.model_validate(domain_rec)
    settings = get_settings()
    cname_target = getattr(settings, "public_ingress_domain", getattr(settings, "public_ingress_ipv4", "ingress.example.com"))
    txt_target = f"blockhost-verify={token}"
    out.dns_instructions = f"Option 1: Create a CNAME record for {payload.domain} pointing to {cname_target}\nOption 2: Create a TXT record for {payload.domain} containing '{txt_target}'"
    return out


@router.get(
    "/deployments/{deployment_id}/domains",
    response_model=List[DomainOut],
)
def list_custom_domains(
    deployment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[DomainOut]:
    """List all custom domains attached to a deployment."""
    deployment = _get_deployment_for_user(deployment_id, user, db)
    domains = db.execute(
        select(CustomDomain).where(CustomDomain.deployment_id == deployment.id).order_by(CustomDomain.created_at.desc())
    ).scalars().all()
    return [DomainOut.model_validate(d) for d in domains]


@router.post(
    "/domains/{domain_id}/verify",
    response_model=DomainOut,
)
async def verify_custom_domain(
    domain_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DomainOut:
    """Verify CNAME / DNS records for a domain and activate reverse proxy routing if container is running."""
    domain_rec = db.get(CustomDomain, domain_id)
    if not domain_rec:
        raise HTTPException(status_code=404, detail="Domain not found")

    deployment = _get_deployment_for_user(domain_rec.deployment_id, user, db)

    # Verify DNS
    settings = get_settings()
    expected_ip = getattr(settings, "public_ingress_ipv4", "127.0.0.1")
    expected_cname = getattr(settings, "public_ingress_domain", None)
    expected_txt = f"blockhost-verify={domain_rec.verification_token}"
    
    dns_valid = ingress_service.verify_dns(
        domain_rec.domain, 
        expected_ip=expected_ip, 
        expected_cname=expected_cname, 
        expected_txt=expected_txt
    )
    if not dns_valid:
        domain_rec.status = DomainStatus.failed
        domain_rec.error_message = f"DNS record for {domain_rec.domain} does not point to ingress host."
        db.commit()
        db.refresh(domain_rec)
        raise HTTPException(
            status_code=400,
            detail=f"DNS verification failed for {domain_rec.domain}. Ensure CNAME points to ingress target.",
        )

    # DNS verified! Update domain state
    domain_rec.status = DomainStatus.active
    domain_rec.ssl_active = True
    domain_rec.error_message = None

    # If the deployment container is currently running and has a node, register route in Caddy immediately
    if deployment.state == DeploymentState.running and deployment.host_port and deployment.node_id:
        node = db.get(Node, deployment.node_id)
        if node and node.ip_address:
            await ingress_service.register_route(
                domain=domain_rec.domain,
                node_ip=node.ip_address,
                host_port=deployment.host_port,
            )

    db.commit()
    db.refresh(domain_rec)
    logger.info("Successfully verified and activated domain %s", domain_rec.domain)
    return DomainOut.model_validate(domain_rec)


@router.delete(
    "/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_custom_domain(
    domain_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Detach a custom domain and remove its dynamic route from the proxy."""
    domain_rec = db.get(CustomDomain, domain_id)
    if not domain_rec:
        raise HTTPException(status_code=404, detail="Domain not found")

    _get_deployment_for_user(domain_rec.deployment_id, user, db)

    # Unregister route from Caddy
    await ingress_service.unregister_route(domain_rec.domain)

    db.delete(domain_rec)
    db.commit()
    logger.info("Detached and purged domain %s (id=%s)", domain_rec.domain, domain_id)


@router.get(
    "/domains/{domain_id}/ssl-status",
)
async def get_domain_ssl_status(
    domain_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return the SSL provisioning status for a domain."""
    domain_rec = db.get(CustomDomain, domain_id)
    if not domain_rec:
        raise HTTPException(status_code=404, detail="Domain not found")
        
    _get_deployment_for_user(domain_rec.deployment_id, user, db)
    
    return {
        "domain": domain_rec.domain,
        "ssl_active": domain_rec.ssl_active,
        "status": "active" if domain_rec.ssl_active else "pending"
    }
