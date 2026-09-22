"""
DockerRuntime — manages Docker containers for PaaS web-app / database deployments.

Supports safe rollouts, project-level bridge networks for isolation, and resource limits.
"""

from __future__ import annotations

import logging
import uuid
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
import docker.errors

logger = logging.getLogger(__name__)

_CONTAINER_PREFIX = "blockhost-deploy"
_LABEL_MANAGED = "blockhost.managed"
_LABEL_DEPLOYMENT_ID = "blockhost.deployment_id"
_LABEL_PROJECT_ID = "blockhost.project_id"


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

    def ensure_project_network(self, project_id: str) -> str:
        """Ensure a bridge network exists for the given project_id."""
        network_name = f"blockhost-net-{project_id}"
        try:
            self.client.networks.get(network_name)
        except docker.errors.NotFound:
            logger.info("Creating project network %s", network_name)
            try:
                self.client.networks.create(network_name, driver="bridge")
            except docker.errors.APIError as e:
                # Might have been created concurrently
                if "already exists" not in str(e):
                    raise
        return network_name

    def _find_containers(self, deployment_id: str) -> list[docker.models.containers.Container]:
        return self.client.containers.list(
            all=True,
            filters={"label": f"{_LABEL_DEPLOYMENT_ID}={deployment_id}"},
        )

    def _find_active_container(self, deployment_id: str) -> docker.models.containers.Container | None:
        containers = self._find_containers(deployment_id)
        if not containers:
            return None
        # Return the most recently created container that is running, or the most recent overall
        containers.sort(key=lambda c: c.attrs.get("Created", ""), reverse=True)
        running = [c for c in containers if c.status == "running"]
        if running:
            return running[0]
        return containers[0]

    @staticmethod
    def _extract_host_port(container: docker.models.containers.Container, internal_port: int) -> int | None:
        """Read the ephemeral host port Docker assigned for *internal_port*."""
        container.reload()
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
        storage_limit_mb: int = 5120,
        project_id: str | None = None,
        health_check_path: str | None = None,
        command: list[str] | str | None = None,
        network_aliases: list[str] | None = None,
        is_database: bool = False,
    ) -> DeploymentStartResult:
        """Create and start a container for *deployment_id*."""
        old_containers = self._find_containers(deployment_id)

        revision_id = uuid.uuid4().hex[:8]
        container_name = f"{_CONTAINER_PREFIX}-{deployment_id}-{revision_id}"

        # Build volumes dict
        volumes: dict[str, dict[str, str]] = {}
        if volume_path:
            volumes[volume_path] = {"bind": volume_mount_path, "mode": "rw"}

        env = dict(env_vars) if env_vars else {}

        # Pull image
        logger.info("Pulling image %s for deployment %s", docker_image, deployment_id)
        try:
            self.client.images.pull(docker_image)
        except docker.errors.APIError as exc:
            logger.error("Failed to pull image %s: %s", docker_image, exc)
            raise RuntimeError(f"Failed to pull Docker image '{docker_image}': {exc}") from exc

        logger.info("Starting container %s (image=%s, port=%d)", container_name, docker_image, internal_port)

        import os
        bind_address = os.environ.get("AGENT_BIND_IP", "127.0.0.1")
        
        network_name = None
        if project_id:
            network_name = self.ensure_project_network(project_id)

        labels = {
            _LABEL_MANAGED: "true",
            _LABEL_DEPLOYMENT_ID: str(deployment_id),
            "blockhost.storage_limit_mb": str(storage_limit_mb),
        }
        if project_id:
            labels[_LABEL_PROJECT_ID] = str(project_id)

        run_kwargs: dict[str, Any] = {
            "image": docker_image,
            "name": container_name,
            "detach": True,
            "ports": {f"{internal_port}/tcp": (bind_address, None)},
            "environment": env,
            "volumes": volumes or None,
            "mem_limit": f"{ram_limit_mb}m",
            "nano_cpus": int(cpu_limit * 1e9),
            "restart_policy": {"Name": "unless-stopped"},
            "labels": labels,
        }
        
        # Apply security hardening
        if not is_database:
            run_kwargs.update({
                "security_opt": ["no-new-privileges:true"],
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE"],
                "pids_limit": 256,
                "mem_swappiness": 0,
                "user": "1000:1000",
            })
        else:
            run_kwargs.update({
                "pids_limit": 512,
                "mem_swappiness": 0,
            })

        if command:
            run_kwargs["command"] = command
        if network_name:
            run_kwargs["network"] = network_name
            if network_aliases:
                run_kwargs["networking_config"] = self.client.api.create_networking_config({
                    network_name: self.client.api.create_endpoint_config(aliases=network_aliases)
                })

        container = self.client.containers.run(
            **run_kwargs,
        )

        host_port = self._extract_host_port(container, internal_port)
        
        # Wait a short bit to ensure it doesn't immediately crash
        time.sleep(2.0)
        container.reload()
        if container.status != "running":
            logger.error("Container %s failed to start. Status: %s", container_name, container.status)
            logs = container.logs(tail=50).decode('utf-8', errors='replace')
            logger.error("Container logs: %s", logs)
            raise RuntimeError(f"Container failed to start: {logs}")

        if health_check_path and host_port:
            self._wait_for_http_health(container, bind_address, host_port, health_check_path)

        # Remove old containers after new one is confirmed running
        for old_c in old_containers:
            logger.info("Removing old container %s for deployment %s", old_c.name, deployment_id)
            try:
                old_c.stop(timeout=10)
            except docker.errors.APIError:
                pass
            try:
                old_c.remove(force=True)
            except docker.errors.APIError:
                pass

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

    def _wait_for_http_health(
        self,
        container: docker.models.containers.Container,
        bind_address: str,
        host_port: int,
        health_check_path: str,
        *,
        timeout_seconds: int = 45,
    ) -> None:
        import httpx

        path = health_check_path if health_check_path.startswith("/") else f"/{health_check_path}"
        url = f"http://{bind_address}:{host_port}{path}"
        deadline = time.time() + timeout_seconds
        last_error = ""
        while time.time() < deadline:
            container.reload()
            if container.status != "running":
                logs = container.logs(tail=50).decode("utf-8", errors="replace")
                raise RuntimeError(f"Container exited before health check passed: {logs}")
            try:
                resp = httpx.get(url, timeout=2.0)
                if 200 <= resp.status_code < 500:
                    logger.info("Health check passed for %s via %s", container.name, url)
                    return
                last_error = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2.0)
        logs = container.logs(tail=50).decode("utf-8", errors="replace")
        raise RuntimeError(f"Health check failed at {url}: {last_error}; logs: {logs}")

    def stop_deployment(self, deployment_id: str) -> None:
        """Stop and remove all containers for *deployment_id*."""
        containers = self._find_containers(deployment_id)
        if not containers:
            logger.info("No container found for deployment %s — nothing to stop", deployment_id)
            return

        for container in containers:
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
        container = self._find_active_container(deployment_id)
        if container is None:
            return DeploymentStatus(running=False)

        container.reload()
        state_info = container.attrs.get("State", {})
        state_str = state_info.get("Status", "unknown")
        running = state_str == "running"

        host_port: int | None = None
        if running:
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

    def get_network_rx_bytes(self, deployment_id: str) -> int:
        container = self._find_active_container(deployment_id)
        if container is None:
            return 0
        try:
            stats = container.stats(stream=False)
            networks = stats.get("networks", {})
            rx_bytes = sum(net.get("rx_bytes", 0) for net in networks.values())
            return rx_bytes
        except docker.errors.APIError as exc:
            logger.warning("Failed to read stats for deployment %s: %s", deployment_id, exc)
            return 0

    def get_metrics(self, deployment_id: str) -> dict[str, Any]:
        container = self._find_active_container(deployment_id)
        if container is None:
            return {"running": False}
        try:
            container.reload()
            stats = container.stats(stream=False)
            memory = stats.get("memory_stats", {})
            networks = stats.get("networks", {})
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})

            cpu_delta = (
                cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            )
            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
            online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", []) or []) or 1
            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta >= 0:
                cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0

            pids_stats = stats.get("pids_stats", {})
            return {
                "running": container.status == "running",
                "state": container.status,
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage_mb": memory.get("usage", 0) // (1024 * 1024),
                "memory_limit_mb": memory.get("limit", 0) // (1024 * 1024),
                "network_rx_bytes": sum(net.get("rx_bytes", 0) for net in networks.values()),
                "network_tx_bytes": sum(net.get("tx_bytes", 0) for net in networks.values()),
                "pids_current": pids_stats.get("current", 0),
                "restart_count": container.attrs.get("RestartCount", 0),
            }
        except docker.errors.APIError as exc:
            logger.warning("Failed to read metrics for deployment %s: %s", deployment_id, exc)
            return {"running": False, "error": str(exc)}

    def get_logs(self, deployment_id: str, tail: int = 200) -> list[str]:
        container = self._find_active_container(deployment_id)
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

    @staticmethod
    def _dockerfile_shell(command: str) -> str:
        import json
        return json.dumps(command)

    def _write_generated_dockerfile(
        self,
        build_dir: "Path",
        *,
        deployment_kind: str,
        internal_port: int,
        install_command: str | None,
        build_command: str | None,
        start_command: str | None,
        output_directory: str | None,
        runtime_version: str | None,
    ) -> None:
        dockerfile = build_dir / "Dockerfile"
        if dockerfile.exists():
            return

        kind = (deployment_kind or "dockerfile").lower()
        port = int(internal_port or 8080)
        if kind == "nextjs":
            node_version = runtime_version or "18"
            install = install_command or "npm install"
            build = build_command or "npm run build"
            start = start_command or "npm start"
            dockerfile.write_text(
                "\n".join([
                    f"FROM node:{node_version}-alpine",
                    "WORKDIR /app",
                    "COPY . .",
                    f"RUN {install}",
                    f"RUN {build}",
                    f"ENV PORT={port}",
                    f"EXPOSE {port}",
                    f"CMD [\"sh\", \"-c\", {self._dockerfile_shell(start)}]",
                    "",
                ]),
                encoding="utf-8",
            )
            return

        if kind == "fastapi":
            python_version = runtime_version or "3.12"
            install = install_command or (
                "if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; "
                "elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; "
                "else pip install --no-cache-dir fastapi uvicorn; fi"
            )
            start = start_command or f"uvicorn main:app --host 0.0.0.0 --port {port}"
            dockerfile.write_text(
                "\n".join([
                    f"FROM python:{python_version}-slim",
                    "WORKDIR /app",
                    "COPY . .",
                    f"RUN {install}",
                    f"ENV PORT={port}",
                    f"EXPOSE {port}",
                    f"CMD [\"sh\", \"-c\", {self._dockerfile_shell(start)}]",
                    "",
                ]),
                encoding="utf-8",
            )
            return

        if kind == "static_site":
            out_dir = output_directory or "."
            if build_command:
                node_version = runtime_version or "18"
                install = install_command or "if [ -f package.json ]; then npm install; fi"
                dockerfile.write_text(
                    "\n".join([
                        f"FROM node:{node_version}-alpine AS build",
                        "WORKDIR /app",
                        "COPY . .",
                        f"RUN {install}",
                        f"RUN {build_command}",
                        "FROM nginx:alpine",
                        f"COPY --from=build /app/{out_dir} /usr/share/nginx/html",
                        "EXPOSE 80",
                        "",
                    ]),
                    encoding="utf-8",
                )
            else:
                dockerfile.write_text(
                    "\n".join([
                        "FROM nginx:alpine",
                        f"COPY {out_dir} /usr/share/nginx/html",
                        "EXPOSE 80",
                        "",
                    ]),
                    encoding="utf-8",
                )
            return

    def build_from_github(
        self,
        repo_url: str,
        branch: str,
        deployment_id: str,
        *,
        deployment_kind: str = "dockerfile",
        internal_port: int = 8080,
        install_command: str | None = None,
        build_command: str | None = None,
        start_command: str | None = None,
        output_directory: str | None = None,
        runtime_version: str | None = None,
    ) -> str:
        import subprocess
        import tempfile
        import shutil
        from pathlib import Path

        tag = f"blockhost-build-{deployment_id}"
        logger.info("Starting GitHub build for %s (branch: %s) -> %s", repo_url, branch, tag)

        build_dir = Path(tempfile.mkdtemp(prefix=f"build-{deployment_id}-"))
        try:
            logger.info("Cloning %s into %s", repo_url, build_dir)
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(build_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.error("Git clone failed:\nSTDOUT:\n%s\nSTDERR:\n%s", result.stdout, result.stderr)
                raise RuntimeError(f"Git clone failed: {result.stderr}")

            self._write_generated_dockerfile(
                build_dir,
                deployment_kind=deployment_kind,
                internal_port=internal_port,
                install_command=install_command,
                build_command=build_command,
                start_command=start_command,
                output_directory=output_directory,
                runtime_version=runtime_version,
            )

            logger.info("Building Docker image %s from %s", tag, build_dir)
            try:
                image, build_logs = self.client.images.build(path=str(build_dir), tag=tag, rm=True)
                logger.info("Successfully built image %s", tag)
            except docker.errors.BuildError as exc:
                build_log_text = "\n".join([line.get('stream', '') for line in exc.build_log if 'stream' in line])
                logger.error("Docker build failed for %s:\n%s", tag, build_log_text)
                raise RuntimeError(f"Docker build failed: {exc}")
            except docker.errors.APIError as exc:
                logger.error("Docker API error during build for %s: %s", tag, exc)
                raise RuntimeError(f"Docker API error: {exc}")

            return tag
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)

    def remove_deployment(self, deployment_id: str) -> None:
        self.stop_deployment(deployment_id)

    def list_managed_containers(self) -> list[dict[str, Any]]:
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

    def run_docker_exec(self, deployment_id: str, cmd: list[str], stdout_file: str | None = None, stdin_file: str | None = None) -> int:
        """Run docker exec using subprocess to allow streaming to/from disk files."""
        container = self._find_active_container(deployment_id)
        if container is None:
            raise RuntimeError(f"No active container found for deployment {deployment_id}")
            
        import subprocess
        full_cmd = ["docker", "exec"]
        if stdin_file:
            full_cmd.append("-i")
        full_cmd.extend([container.name])
        full_cmd.extend(cmd)
        
        logger.info("Running %s", full_cmd)
        
        stdin_f = open(stdin_file, 'rb') if stdin_file else None
        stdout_f = open(stdout_file, 'wb') if stdout_file else None
        
        try:
            result = subprocess.run(full_cmd, stdin=stdin_f, stdout=stdout_f)
            return result.returncode
        finally:
            if stdin_f: stdin_f.close()
            if stdout_f: stdout_f.close()
