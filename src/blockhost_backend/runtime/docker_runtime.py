"""
DockerRuntime — manages Docker containers for PaaS web-app / database deployments.

This is intentionally NOT an implementation of the Minecraft-specific ``Runtime``
Protocol.  It has its own interface tailored to container lifecycle management
(image pull, create, start, stop, remove, logs).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import docker
import docker.errors

logger = logging.getLogger(__name__)

_CONTAINER_PREFIX = "blockhost-deploy"
_LABEL_MANAGED = "blockhost.managed"
_LABEL_DEPLOYMENT_ID = "blockhost.deployment_id"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeploymentStartResult:
    container_id: str
    container_name: str
    host_port: int


@dataclass(frozen=True)
class DeploymentStatus:
    running: bool
    container_id: str | None = None
    state: str | None = None      # docker state: running, exited, paused, …
    host_port: int | None = None
    started_at: str | None = None
    exit_code: int | None = None


# ---------------------------------------------------------------------------
# DockerRuntime
# ---------------------------------------------------------------------------

class DockerRuntime:
    """Thin wrapper around the Docker SDK for managing PaaS containers."""

    def __init__(self) -> None:
        self.client: docker.DockerClient = docker.from_env()

    # ---- helpers ---------------------------------------------------------

    def _container_name(self, deployment_id: str) -> str:
        return f"{_CONTAINER_PREFIX}-{deployment_id}"

    def _find_container(self, deployment_id: str) -> docker.models.containers.Container | None:
        name = self._container_name(deployment_id)
        try:
            return self.client.containers.get(name)
        except docker.errors.NotFound:
            return None

    @staticmethod
    def _extract_host_port(container: docker.models.containers.Container, internal_port: int) -> int | None:
        """Read the ephemeral host port Docker assigned for *internal_port*."""
        container.reload()  # refresh attrs after start
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
        key = f"{internal_port}/tcp"
        bindings = ports.get(key)
        if bindings:
            try:
                return int(bindings[0]["HostPort"])
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        return None

    # ---- public API ------------------------------------------------------

    def start_deployment(
        self,
        deployment_id: str,
        docker_image: str,
        internal_port: int,
        env_vars: dict[str, str] | None = None,
        volume_path: str | None = None,
        volume_mount_path: str = "/data",
        ram_limit_mb: int = 512,
        cpu_limit: float = 1.0,
    ) -> DeploymentStartResult:
        """Create and start a container for *deployment_id*.

        If a container with the same name already exists, it is stopped and
        removed first (idempotent restart).
        """
        # Clean up any stale container with the same name
        existing = self._find_container(deployment_id)
        if existing is not None:
            logger.info("Removing existing container %s before re-creating", existing.name)
            try:
                existing.stop(timeout=15)
            except docker.errors.APIError:
                pass
            try:
                existing.remove(force=True)
            except docker.errors.APIError:
                pass

        container_name = self._container_name(deployment_id)

        # Build volumes dict
        volumes: dict[str, dict[str, str]] = {}
        if volume_path:
            volumes[volume_path] = {"bind": volume_mount_path, "mode": "rw"}

        # Build env list (Docker SDK accepts a dict)
        env = dict(env_vars) if env_vars else {}

        # Pull image (will no-op if already cached)
        logger.info("Pulling image %s for deployment %s", docker_image, deployment_id)
        try:
            self.client.images.pull(docker_image)
        except docker.errors.APIError as exc:
            logger.error("Failed to pull image %s: %s", docker_image, exc)
            raise RuntimeError(f"Failed to pull Docker image '{docker_image}': {exc}") from exc

        logger.info("Starting container %s (image=%s, port=%d)", container_name, docker_image, internal_port)

        bind_address = "0.0.0.0"
        container = self.client.containers.run(
            image=docker_image,
            name=container_name,
            detach=True,
            ports={f"{internal_port}/tcp": (bind_address, None)},
            environment=env,
            volumes=volumes or None,
            mem_limit=f"{ram_limit_mb}m",
            nano_cpus=int(cpu_limit * 1e9),
            restart_policy={"Name": "unless-stopped"},
            labels={
                _LABEL_MANAGED: "true",
                _LABEL_DEPLOYMENT_ID: str(deployment_id),
            },
        )

        host_port = self._extract_host_port(container, internal_port)
        if host_port is None:
            logger.warning(
                "Could not determine host port for container %s — port bindings: %s",
                container_name,
                container.attrs.get("NetworkSettings", {}).get("Ports"),
            )

        logger.info(
            "Container %s running (id=%s, host_port=%s)",
            container_name,
            container.short_id,
            host_port,
        )

        return DeploymentStartResult(
            container_id=container.id,
            container_name=container_name,
            host_port=host_port or 0,
        )

    def stop_deployment(self, deployment_id: str) -> None:
        """Stop and remove the container for *deployment_id*."""
        container = self._find_container(deployment_id)
        if container is None:
            logger.info("No container found for deployment %s — nothing to stop", deployment_id)
            return

        logger.info("Stopping container %s", container.name)
        try:
            container.stop(timeout=30)
        except docker.errors.APIError as exc:
            logger.warning("Error stopping container %s: %s", container.name, exc)

        try:
            container.remove(force=True)
            logger.info("Removed container %s", container.name)
        except docker.errors.APIError as exc:
            logger.warning("Error removing container %s: %s", container.name, exc)

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        """Return current status of the container for *deployment_id*."""
        container = self._find_container(deployment_id)
        if container is None:
            return DeploymentStatus(running=False)

        container.reload()
        state_info = container.attrs.get("State", {})
        state_str = state_info.get("Status", "unknown")
        running = state_str == "running"

        host_port: int | None = None
        if running:
            # Try to read host port from port bindings
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            for _key, bindings in ports.items():
                if bindings:
                    try:
                        host_port = int(bindings[0]["HostPort"])
                        break
                    except (KeyError, IndexError, TypeError, ValueError):
                        pass

        return DeploymentStatus(
            running=running,
            container_id=container.id,
            state=state_str,
            host_port=host_port,
            started_at=state_info.get("StartedAt"),
            exit_code=state_info.get("ExitCode"),
        )

    def get_logs(self, deployment_id: str, tail: int = 200) -> list[str]:
        """Return the last *tail* log lines from the container."""
        container = self._find_container(deployment_id)
        if container is None:
            return []

        try:
            raw = container.logs(tail=tail, timestamps=True)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace").splitlines()
            return list(raw)
        except docker.errors.APIError as exc:
            logger.warning("Failed to read logs for deployment %s: %s", deployment_id, exc)
            return []

    def remove_deployment(self, deployment_id: str) -> None:
        """Force-remove the container (even if running)."""
        container = self._find_container(deployment_id)
        if container is None:
            return
        try:
            container.remove(force=True)
            logger.info("Force-removed container for deployment %s", deployment_id)
        except docker.errors.APIError as exc:
            logger.warning("Failed to force-remove container for %s: %s", deployment_id, exc)

    def list_managed_containers(self) -> list[dict[str, Any]]:
        """List all containers managed by BlockHost (by label)."""
        containers = self.client.containers.list(
            all=True,
            filters={"label": f"{_LABEL_MANAGED}=true"},
        )
        results = []
        for c in containers:
            c.reload()
            state_info = c.attrs.get("State", {})
            results.append({
                "container_id": c.id,
                "name": c.name,
                "deployment_id": c.labels.get(_LABEL_DEPLOYMENT_ID),
                "state": state_info.get("Status", "unknown"),
                "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:19],
            })
        return results
