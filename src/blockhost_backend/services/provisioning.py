from __future__ import annotations

import logging
import uuid
import httpx
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import (
    AppDeployment,
    DatabaseEngine,
    DatabaseInstance,
    DeploymentRevision,
    DeploymentState,
    HealthStatus,
    Node,
    Server,
    ServerState,
)
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.services.node_capacity import select_best_node

logger = logging.getLogger(__name__)


def _latest_revision(db: Session, deployment_id: uuid.UUID) -> DeploymentRevision | None:
    return db.execute(
        select(DeploymentRevision)
        .where(DeploymentRevision.deployment_id == deployment_id)
        .order_by(DeploymentRevision.created_at.desc())
    ).scalars().first()


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
            elif resource_type == "database_instance":
                _provision_database_instance(db, resource_id)
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
        revision = _latest_revision(db, deployment.id)
        if deployment.github_repo_url:
            logger.info("Triggering GitHub build for %s on node %s", deployment.id, node.id)
            build_url = f"http://{node.ip_address}:{node.agent_port}/agent/deployments/{deployment.id}/build-github"
            build_payload = {
                "repo_url": deployment.github_repo_url,
                "branch": deployment.github_branch,
                "deployment_kind": deployment.deployment_kind.value if deployment.deployment_kind else "dockerfile",
                "internal_port": deployment.internal_port,
                "install_command": deployment.install_command,
                "build_command": deployment.build_command,
                "start_command": deployment.start_command,
                "output_directory": deployment.output_directory,
                "runtime_version": deployment.runtime_version,
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
                    image_tag = status_data.get("image_tag")
                    build_log_text = status_data.get("build_logs", "")
                    if image_tag:
                        deployment.docker_image = image_tag
                        if revision:
                            revision.image_tag = image_tag
                            revision.status = DeploymentState.building
                            revision.build_logs = build_log_text or f"Build succeeded. Image: {image_tag}"
                        db.commit()
                    break
                elif build_status == "error":
                    error_msg = status_data.get("error") or "GitHub build failed on the agent. Check agent logs."
                    if revision:
                        revision.build_logs = error_msg
                        revision.error_message = error_msg
                        db.commit()
                    raise RuntimeError(error_msg)
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
            "storage_limit_mb": getattr(deployment, "storage_limit_mb", 5120),
            "project_id": str(deployment.project_id) if deployment.project_id else None,
            "health_check_path": deployment.health_check_path,
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
        deployment.health_status = HealthStatus.unknown
        deployment.restart_count = 0
        if revision:
            revision.status = DeploymentState.running
            revision.image_tag = deployment.docker_image
            revision.deployed_at = datetime.now(timezone.utc)
            revision.is_active = True
        db.commit()
        logger.info("Successfully provisioned deployment %s on node %s (host_port=%s)", deployment.id, node.id, deployment.host_port)

        # Step 4: Register default and active custom domain routes with Ingress Proxy
        from blockhost_backend.database.schema import CustomDomain, DomainStatus
        from blockhost_backend.services.ingress import IngressService, ingress_service
        from sqlalchemy import select
        default_domain = IngressService.default_app_domain(str(deployment.id), settings.public_app_base_domain)
        if default_domain and deployment.host_port:
            try:
                import asyncio
                asyncio.run(ingress_service.register_route(default_domain, node.ip_address, deployment.host_port))
            except Exception as e:
                logger.warning("Failed to register default route for %s: %s", default_domain, e)
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
        revision = _latest_revision(db, deployment.id)
        if revision:
            revision.status = DeploymentState.error
            revision.error_message = str(exc)
            revision.build_logs = (revision.build_logs or "") + "\n" + str(exc) if revision.build_logs else str(exc)
        db.commit()


def _database_image_and_env(database: DatabaseInstance) -> tuple[str, dict[str, str], str, list[str] | None]:
    password = database.db_password or ""
    if database.engine == DatabaseEngine.postgresql:
        return (
            f"postgres:{database.version}",
            {
                "POSTGRES_DB": database.db_name,
                "POSTGRES_USER": database.db_username,
                "POSTGRES_PASSWORD": password,
            },
            "/var/lib/postgresql/data",
            None,
        )
    if database.engine == DatabaseEngine.mysql:
        return (
            f"mysql:{database.version}",
            {
                "MYSQL_DATABASE": database.db_name,
                "MYSQL_USER": database.db_username,
                "MYSQL_PASSWORD": password,
                "MYSQL_ROOT_PASSWORD": password,
            },
            "/var/lib/mysql",
            None,
        )
    if database.engine == DatabaseEngine.mariadb:
        return (
            f"mariadb:{database.version}",
            {
                "MARIADB_DATABASE": database.db_name,
                "MARIADB_USER": database.db_username,
                "MARIADB_PASSWORD": password,
                "MARIADB_ROOT_PASSWORD": password,
            },
            "/var/lib/mysql",
            None,
        )
    if database.engine == DatabaseEngine.redis:
        command = ["redis-server", "--appendonly", "yes"]
        if password:
            command.extend(["--requirepass", password])
        return (f"redis:{database.version}", {}, "/data", command)
    raise RuntimeError(f"Unsupported database engine: {database.engine}")


def _provision_database_instance(db: Session, database_id: uuid.UUID) -> None:
    database = db.get(DatabaseInstance, database_id)
    if not database:
        logger.error("Database instance %s not found for provisioning", database_id)
        return

    try:
        if not database.node_id:
            node = select_best_node(db, required_ram_mb=database.ram_limit_mb)
            if not node:
                raise RuntimeError("No available nodes with sufficient capacity")
            database.node_id = node.id
            database.state = DeploymentState.building
            db.commit()
            db.refresh(database)
        else:
            node = db.get(Node, database.node_id)
            if not node:
                raise RuntimeError(f"Assigned node {database.node_id} no longer exists")

        settings = get_settings()
        image, env_vars, mount_path, command = _database_image_and_env(database)
        agent_url = f"http://{node.ip_address}:{node.agent_port}/agent/databases/{database.id}/start"
        payload = {
            "docker_image": image,
            "internal_port": database.internal_port,
            "env_vars": env_vars,
            "volume_path": database.volume_path,
            "volume_mount_path": mount_path,
            "ram_limit_mb": database.ram_limit_mb,
            "cpu_limit": database.cpu_limit,
            "storage_limit_mb": getattr(database, "storage_limit_mb", 5120),
            "project_id": str(database.project_id),
            "command": command,
            "network_aliases": [database.internal_hostname],
        }

        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                agent_url,
                headers={"Authorization": f"Bearer {settings.worker_agent_token}"},
                json=payload,
            )
            resp.raise_for_status()

        database.state = DeploymentState.running
        db.commit()
        logger.info("Successfully provisioned database %s on node %s", database.id, node.id)
    except Exception as exc:
        logger.exception("Failed to provision database %s", database_id)
        database.state = DeploymentState.error
        db.commit()
