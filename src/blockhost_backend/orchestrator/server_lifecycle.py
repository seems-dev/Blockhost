from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import Settings
from blockhost_backend.database.schema import Server, ServerState, SubscriptionTier, User
from blockhost_backend.orchestrator.resources import TIER_RESOURCE_LIMITS
from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    Runtime,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStatus,
)

logger = logging.getLogger(__name__)


class ServerLifecycleOrchestrator:
    """
    Owns server lifecycle transitions and talks to runtime only through Runtime.
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def get_status(self, server_id: str) -> RuntimeStatus:
        return self._runtime.get_status(server_id)

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        return self._runtime.get_stats(server_id)

    def is_running(self, server_id: str) -> bool:
        return self.get_status(server_id).running

    def start_server(
        self,
        *,
        server: Server,
        settings: Settings,
        servers_dir: Path,
        get_user_tier,
        db: Session | None = None,
    ) -> None:
        if not server.vm_port:
            raise RuntimeError(f"Server {server.id} has no port allocated")
        if not (server.mc_config or {}).get("server_dir"):
            raise RuntimeError(f"Server {server.id} has no server_dir configured")

        server_dir = self._validate_server_dir(Path(server.mc_config["server_dir"]), servers_dir)
        tier = self._resolve_tier(server=server, db=db, get_user_tier=get_user_tier)
        limits = TIER_RESOURCE_LIMITS.get(tier, TIER_RESOURCE_LIMITS[SubscriptionTier.free])

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

        if server.mc_config.get("template_version") and result.actual_version:
            requested = str(server.mc_config["template_version"])
            if requested not in {"LATEST", "PREVIEW"} and not str(result.actual_version).startswith(requested):
                self._runtime.stop_server(str(server.id))
                raise RuntimeError(
                    f"Runtime version mismatch (requested={requested}, actual={result.actual_version})"
                )

        logger.info(
            "Server %s started (runtime_id=%s, tier=%s, ram=%sMB, cpu=%s%%)",
            server.id,
            result.runtime_id,
            tier.value,
            limits["ram_mb"],
            limits["cpu_quota_pct"],
        )
        server.vm_id = result.runtime_id
        server.vm_ipv4 = settings.minecraft_public_host
        server.vm_port = result.port
        server.state = ServerState.running

    def stop_server(self, server: Server) -> None:
        self._runtime.stop_server(str(server.id))
        server.state = ServerState.suspended

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        return self._runtime.read_logs(server_id, tail=tail)

    def send_command(self, server_id: str, command: str) -> None:
        status = self._runtime.get_status(server_id)
        if not status.running:
            raise RuntimeError("Server is not running")
        self._runtime.send_command(server_id, command)

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        self._runtime.add_log_listener(server_id, listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        self._runtime.remove_log_listener(server_id, listener)

    def _resolve_tier(self, *, server: Server, db: Session | None, get_user_tier) -> SubscriptionTier:
        try:
            owner = getattr(server, "owner", None)
            if owner and getattr(owner, "subscription_tier", None):
                return get_user_tier(owner)
            if db and server.owner_id:
                db_owner = db.get(User, server.owner_id)
                if db_owner:
                    return get_user_tier(db_owner)
        except Exception as exc:
            logger.warning("Could not resolve tier for server %s: %s — defaulting to free", server.id, exc)
        return SubscriptionTier.free

    def _validate_server_dir(self, server_dir: Path, servers_dir: Path) -> Path:
        try:
            resolved = server_dir.resolve()
            root = servers_dir.resolve()
            resolved.relative_to(root)
            return resolved
        except ValueError:
            raise ValueError(f"server_dir {server_dir!r} is outside the allowed servers root {servers_dir!r}")
