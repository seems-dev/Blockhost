from __future__ import annotations

import logging
from pathlib import Path
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import Settings
from blockhost_backend.database.schema import (
    Server,
    ServerState,
   
    User,
)
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
            from blockhost_backend.services.billing import ensure_server_not_billing_suspended
            ensure_server_not_billing_suspended(db=db, server=server)

        if not server.vm_port:

            raise RuntimeError(f"Server {server.id} has no port allocated")

        if not (server.mc_config or {}).get("server_dir"):
            raise RuntimeError(f"Server {server.id} has no server_dir configured")

        server_dir = self._validate_server_dir(
            Path(server.mc_config["server_dir"]),
            servers_dir,
        )

        limits = get_effective_server_resource_limits(db=db, server=server)

        result = self._runtime.start_server(
            RuntimeStartRequest(
                server_id=str(server.id),
                server_dir=server_dir,
                port=server.vm_port,
                requested_version=str(server.mc_config.get("template_version") or ""),
                executable_name=settings.bedrock_executable_name or None,
                ram_mb=limits["ram_mb"],
                cpu_quota_pct=limits["cpu_quota_pct"],
            )
        )

        # OPTIONAL VERSION CHECK
        requested = server.mc_config.get("template_version")
        if requested and result.actual_version:
            if requested not in {"LATEST", "PREVIEW"}:
                if not str(result.actual_version).startswith(str(requested)):
                    self._runtime.stop_server(str(server.id))
                    raise RuntimeError(
                        f"Version mismatch (requested={requested}, actual={result.actual_version})"
                    )

        server.vm_id = result.runtime_id
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
