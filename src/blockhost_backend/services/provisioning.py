from __future__ import annotations

import logging
import uuid
import httpx
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Server, AppDeployment, DeploymentState, ServerState, Node
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.services.node_capacity import select_best_node

logger = logging.getLogger(__name__)


def provision_resource(resource_type: str, resource_id: uuid.UUID) -> None:
    """
    Asynchronously provision a paid resource (Minecraft server or PaaS deployment).
    This runs in a background task to keep webhooks responsive.
    """
    logger.info("Starting background provisioning for %s %s", resource_type, resource_id)
    with SessionLocal() as db:
        try:
            if resource_type == "minecraft_server":
                _provision_minecraft_server(db, resource_id)
            elif resource_type == "app_deployment":
                _provision_app_deployment(db, resource_id)
            else:
                logger.error("Unknown resource type: %s", resource_type)
        except Exception:
            logger.exception("Provisioning failed for %s %s", resource_type, resource_id)
            # The individual functions handle updating the DB state on failure,
            # but this catch-all ensures the background worker never crashes.


def _provision_minecraft_server(db: Session, server_id: uuid.UUID) -> None:
    server = db.get(Server, server_id)
    if not server:
        logger.error("Minecraft server %s not found for provisioning", server_id)
        return

    orchestrator = get_server_lifecycle_orchestrator()
    settings = get_settings()
    
    # Needs a dummy path, as it relies on SERVERS_ROOT_DIR internally
    # For now, start_server handles validation based on mc_config.
    import pathlib
    servers_dir = pathlib.Path(settings.servers_root_dir) if hasattr(settings, 'servers_root_dir') else pathlib.Path("/var/lib/blockhost/servers")

    try:
        orchestrator.start_server(
            server=server,
            settings=settings,
            servers_dir=servers_dir,
            db=db,
        )
        db.commit()
    except Exception as exc:
        logger.exception("Failed to provision minecraft server %s", server_id)
        server.state = ServerState.error
        db.add(server)
        db.commit()


def _provision_app_deployment(db: Session, deployment_id: uuid.UUID) -> None:
    deployment = db.get(AppDeployment, deployment_id)
    if not deployment:
        logger.error("App deployment %s not found for provisioning", deployment_id)
        return

    try:
        # Step 1: Assign a Node if it doesn't have one
        if not deployment.node_id:
            logger.info("Selecting node for deployment %s", deployment_id)
            # Find best node with enough RAM
            node = select_best_node(db, required_ram_mb=deployment.ram_limit_mb)
            if not node:
                raise RuntimeError("No available nodes with sufficient capacity")
            
            deployment.node_id = node.id
            deployment.state = DeploymentState.building
            db.commit()
            db.refresh(deployment)
        else:
            node = db.get(Node, deployment.node_id)
            if not node:
                raise RuntimeError(f"Assigned node {deployment.node_id} no longer exists")

        # Step 1.5: Trigger GitHub Build if needed
        settings = get_settings()
        if deployment.github_repo_url:
            logger.info("Triggering GitHub build for %s on node %s", deployment.id, node.id)
            build_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{deployment.id}/build-github"
            build_payload = {
                "repo_url": deployment.github_repo_url,
                "branch": deployment.github_branch
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    build_url,
                    headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                    json=build_payload
                )
                resp.raise_for_status()

            # Poll for build status
            status_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{deployment.id}/build-status"
            import time
            while True:
                # Ensure the UI knows we are building
                if deployment.state != DeploymentState.building:
                    deployment.state = DeploymentState.building
                    db.commit()

                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(
                        status_url,
                        headers={"Authorization": f"Bearer {settings.worker_agent_token}"}
                    )
                    resp.raise_for_status()
                    status_data = resp.json()
                    build_status = status_data.get("status")

                if build_status == "success":
                    logger.info("GitHub build succeeded for %s", deployment.id)
                    break
                elif build_status == "error":
                    raise RuntimeError("GitHub build failed on the agent. Check agent logs.")
                elif build_status == "unknown":
                    raise RuntimeError("GitHub build status unknown.")
                
                logger.info("Build for %s still in progress... waiting 5 seconds.", deployment.id)
                time.sleep(5)


        # Step 2: Make HTTP call to Agent to start container
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{deployment.id}/start"
        
        logger.info("Sending start request to agent at %s for deployment %s", agent_url, deployment.id)
        
        payload = {
            "docker_image": deployment.docker_image,
            "internal_port": deployment.internal_port,
            "env_vars": deployment.env_vars,
            "volume_path": deployment.volume_path,
            "volume_mount_path": deployment.volume_mount_path,
            "ram_limit_mb": deployment.ram_limit_mb,
            "cpu_limit": deployment.cpu_limit,
        }

        with httpx.Client(timeout=300.0) as client: # Generous timeout for docker pull
            resp = client.post(
                agent_url,
                headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                json=payload
            )
            resp.raise_for_status()
            
            data = resp.json()
            
        # Step 3: Update DB with successful start
        deployment.container_id = data.get("container_id")
        deployment.host_port = data.get("host_port")
        deployment.state = DeploymentState.running
        deployment.error_message = None
        db.commit()
        logger.info("Successfully provisioned deployment %s on node %s (host_port=%s)", deployment.id, node.id, deployment.host_port)

        # Step 4: Register active custom domain routes with Ingress Proxy
        from blockhost_backend.database.schema import CustomDomain, DomainStatus
        from blockhost_backend.services.ingress import ingress_service
        from sqlalchemy import select
        active_domains = db.execute(
            select(CustomDomain).where(
                CustomDomain.deployment_id == deployment.id,
                CustomDomain.status == DomainStatus.active,
            )
        ).scalars().all()
        for d in active_domains:
            try:
                import asyncio
                asyncio.run(ingress_service.register_route(d.domain, node.ip_address, deployment.host_port))
            except Exception as e:
                logger.warning("Failed to register dynamic route for domain %s: %s", d.domain, e)

    except Exception as exc:
        logger.exception("Failed to provision app deployment %s", deployment_id)
        deployment.state = DeploymentState.error
        deployment.error_message = str(exc)
        db.commit()
