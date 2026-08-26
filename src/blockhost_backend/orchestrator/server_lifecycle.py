from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from sqlalchemy.orm import Session
import dataclasses
import httpx

from blockhost_backend.config.config_manager import Settings
from blockhost_backend.database.schema import (
    Node,
    Server,
    ServerState,
    User,
)
from blockhost_backend.minecraft.java_compat import is_java_flavor, required_java_version

from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits
from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    Runtime,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStatus,
)
from blockhost_backend.services.ban_service import ban_service
#file_name = server_lifecycle.py
logger = logging.getLogger(__name__)


class ServerLifecycleOrchestrator:
    """
    Owns server lifecycle transitions.
    Runtime (systemd) handles execution + limits.
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    # ---------------- STATUS ----------------
    def get_status(self, server_id: str) -> RuntimeStatus:
        return self._runtime.get_status(server_id)

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        return self._runtime.get_stats(server_id)

    def is_running(self, server_id: str) -> bool:
        return self.get_status(server_id).running

    def get_online_players_with_xuid(self, server_id: str) -> dict[str, str | None]:
        return self._runtime.get_online_players_with_xuid(server_id)

    def ping_server(self, server_id: str) -> dict[str, object] | None:
        """Proxy Bedrock ping through the agent for remote nodes."""
        if hasattr(self._runtime, "ping_server"):
            return self._runtime.ping_server(server_id)
        return None

    # ---------------- START ----------------
    def start_server(
        self,
        *,
        server: Server,
        settings: Settings,
        servers_dir: Path,
        
        db: Session | None = None,
    ) -> None:

        if db is not None:
            from blockhost_backend.services.billing import ensure_active_subscription_for_start
            ensure_active_subscription_for_start(db=db, server=server)

        if not server.vm_port:

            raise RuntimeError(f"Server {server.id} has no port allocated")

        if not (server.mc_config or {}).get("server_dir"):
            raise RuntimeError(f"Server {server.id} has no server_dir configured")

        server_dir = self._validate_server_dir(
            Path(server.mc_config["server_dir"]),
            servers_dir,
        )

        limits = get_effective_server_resource_limits(db=db, server=server)
        mc_config = server.mc_config or {}
        is_java = is_java_flavor(server.flavor)
        requested_version = (
            str(server.mc_version or "")
            if is_java
            else str(mc_config.get("template_version") or "")
        ) or None
        
        # Ensure binary is installed and get path
        if not requested_version:
            requested_version = "recommended" if not is_java else "1.21.4"
            
        jar_download_url = None
        server_properties_dict = {}

        if is_java:
            from blockhost_backend.minecraft.software_provider import resolve_jar_url
            jar_download_url = resolve_jar_url(server.flavor, requested_version)
            from blockhost_backend.api.servers import _java_server_properties_from_config
            server_properties_dict = {
                k: v for k, v in dataclasses.asdict(
                    _java_server_properties_from_config(server=server, port=server.vm_port)
                ).items() if v is not None
            }
        else:
            from blockhost_backend.api.servers import _server_props_from_config
            server_properties_dict = {
                k: v for k, v in dataclasses.asdict(
                    _server_props_from_config(server=server, port=server.vm_port)
                ).items() if v is not None
            }

        result = self._runtime.start_server(
            RuntimeStartRequest(
                server_id=str(server.id),
                server_dir=server_dir,
                port=server.vm_port,
                requested_version=requested_version,
                executable_path=None,
                ram_mb=limits["ram_mb"],
                cpu_quota_pct=limits["cpu_quota_pct"],
                flavor=server.flavor.value if server.flavor else None,
                jdk_path=self._resolve_jdk_path(server=server, settings=settings, db=db),
                server_properties_dict=server_properties_dict,
                jar_download_url=jar_download_url,
            )
        )

        # If the agent used a different version than requested (e.g. fell back to latest
        # because the stored version wasn't available on MCJarFiles), accept it and
        # update mc_config so future starts also use the working version.
        requested = mc_config.get("template_version")
        if not is_java and result.actual_version and requested and result.actual_version != requested:
            logger.warning(
                "Server %s: requested version %r but agent used %r — updating stored version.",
                server.id, requested, result.actual_version,
            )
            updated_config = dict(server.mc_config or {})
            updated_config["template_version"] = result.actual_version
            server.mc_config = updated_config

        server.vm_id = result.runtime_id
        if server.node_id and server.vm_ipv4:
            pass  # keep node-assigned IP for remote servers
        else:
            server.vm_ipv4 = settings.minecraft_public_host
        server.vm_port = result.port
        server.state = ServerState.running

        # Attach moderation listener
        ban_service.attach_listener(str(server.id), self)

        logger.info("Server %s started (runtime=%s, plan=%s, ram=%sMB, cpu=%s%%)", server.id, result.runtime_id, "unpaid" if limits["ram_mb"] == 0 else "paid", limits["ram_mb"], limits["cpu_quota_pct"])

    # ---------------- STOP ----------------
    def stop_server(self, server: Server) -> None:
        self._runtime.stop_server(str(server.id))
        server.state = ServerState.suspended

    # ---------------- LOGS ----------------
    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        return self._runtime.read_logs(server_id, tail=tail)

    def get_properties(self, server_id: str) -> dict[str, str | bool | int]:
        return self._runtime.get_properties(server_id)

    def update_properties(self, server_id: str, props: dict[str, str | bool | int]) -> None:
        self._runtime.update_properties(server_id, props)

    # ---------------- COMMANDS ----------------
    def send_command(self, server_id: str, command: str) -> None:
        status = self._runtime.get_status(server_id)

        if not status.running:
            raise RuntimeError("Server is not running")

        # IMPORTANT: may not be supported in systemd runtime yet
        self._runtime.send_command(server_id, command)

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        self._runtime.add_log_listener(server_id, listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        self._runtime.remove_log_listener(server_id, listener)

    
    

    def _resolve_jdk_path(
        self,
        *,
        server: Server,
        settings: Settings,
        db: Session | None,
    ) -> Path | None:
        if not is_java_flavor(server.flavor):
            return None

        java_ver = server.java_version or required_java_version(server.mc_version or "1.21")

        if server.node_id and db is not None:
            node = db.get(Node, server.node_id)
            if node:
                return self._ensure_remote_jdk(node=node, java_version=java_ver, settings=settings)

        if settings.java_home_path:
            jdk_path = Path(settings.java_home_path)
            if (jdk_path / "bin" / "java").exists():
                return jdk_path

        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            jdk_path = Path(java_home)
            if (jdk_path / "bin" / "java").exists():
                return jdk_path

        java_bin = shutil.which("java")
        if java_bin:
            return Path(java_bin).resolve().parent.parent

        return None

    def _ensure_remote_jdk(self, *, node: Node, java_version: int, settings: Settings) -> Path:
        base = f"http://{node.ip_address}:{node.agent_port}"
        headers = {"Authorization": f"Bearer {settings.worker_agent_token}"}
        resp = httpx.post(
            f"{base}/agent/jdks/{java_version}/ensure",
            headers=headers,
            timeout=300.0,
        )
        resp.raise_for_status()
        path = resp.json().get("path")
        if not path:
            raise RuntimeError(f"Agent did not return a JDK path for Java {java_version}")
        
        # Return the path directly. The Agent has already verified the file exists.
        # We cannot check .exists() here because the file is on the Agent's filesystem, not the Control Plane's.
        return Path(path)

    # ---------------- VALIDATION ----------------
    def _validate_server_dir(self, server_dir: Path, servers_dir: Path) -> Path:
        try:
            resolved = server_dir.resolve()
            root = servers_dir.resolve()

            resolved.relative_to(root)
            return resolved

        except ValueError:
            raise ValueError(
                f"server_dir {server_dir!r} is outside allowed root {servers_dir!r}"
            )
