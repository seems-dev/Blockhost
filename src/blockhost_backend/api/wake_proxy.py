import uuid
import logging
from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import AppDeployment, CustomDomain, DeploymentState
from blockhost_backend.services.provisioning import provision_resource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/wake-proxy", tags=["WakeProxy"])

WAKE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Waking Up...</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #f3f4f6;
            color: #1f2937;
        }
        .container {
            text-align: center;
            padding: 2rem;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
        }
        .spinner {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #3b82f6;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 1rem auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="spinner"></div>
        <h2>Waking up application...</h2>
        <p>This deployment was suspended due to inactivity to save resources.</p>
        <p>It is currently booting up and will be ready in a few seconds. This page will auto-refresh.</p>
    </div>
</body>
</html>
"""

@router.get("/{domain}")
async def wake_proxy_handler(
    domain: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Fallback proxy route hit by Caddy when a deployment is suspended."""
    logger.info("Wake proxy accessed for domain: %s", domain)
    
    # 1. Find the deployment by domain
    custom_domain = db.execute(
        select(CustomDomain).where(CustomDomain.domain == domain)
    ).scalar_one_or_none()
    
    if not custom_domain or not custom_domain.deployment_id:
        # Not found, just return a 404
        raise HTTPException(status_code=404, detail="Domain not found or not linked to a deployment")
        
    deployment = db.get(AppDeployment, custom_domain.deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
        
    if deployment.state == DeploymentState.suspended:
        logger.info("Triggering wake for suspended deployment %s", deployment.id)
        # 2. Trigger provision_resource in background
        background_tasks.add_task(provision_resource, "app_deployment", deployment.id)
        
    # If it's already building or running, we still return the waiting page
    # until Caddy updates the route.
    return HTMLResponse(content=WAKE_HTML, status_code=503)
