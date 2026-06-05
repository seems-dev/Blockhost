from __future__ import annotations

import logging
import subprocess

from blockhost_backend.runtime.bedrock_process import BedrockRuntimeRegistry
from blockhost_backend.runtime.cgroup_limits import apply_limits, remove_limits
from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStartResult,
    RuntimeStatus,
)

logger = logging.getLogger(__name__)


class LocalProcessRuntime:
    """
    Runtime implementation backed by local Bedrock processes.

    This keeps subprocess and cgroup details out of the API/orchestrator layers.
    It is the current MVP runtime and can be replaced by systemd, Docker, or a
    remote worker runtime without changing route/business logic.
    """

    def __init__(self, registry: BedrockRuntimeRegistry | None = None) -> None:
        self._registry = registry or BedrockRuntimeRegistry()

    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        proc = self._registry.ensure_process(
            server_id=request.server_id,
            server_dir=request.server_dir,
            port=request.port,
            requested_version=request.requested_version,
        )
        proc.start(executable_name=request.executable_name)
        info = proc.wait_for_ready()

        if info.pid:
            apply_limits(
                pid=info.pid,
                server_id=request.server_id,
                ram_mb=request.ram_mb,
                cpu_quota_pct=request.cpu_quota_pct,
            )

        return RuntimeStartResult(
            runtime_id=str(info.pid),
            port=info.port,
            actual_version=info.actual_version,
        )

    def stop_server(self, server_id: str) -> None:
        try:
            self._registry.stop(server_id=server_id)
        except Exception as exc:
            logger.warning("Runtime stop failed for server %s: %s", server_id, exc)
        finally:
            remove_limits(server_id=server_id)

    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        self.stop_server(request.server_id)
        return self.start_server(request)

    def get_status(self, server_id: str) -> RuntimeStatus:
        proc = self._registry.get(server_id)
        if not proc or not proc.is_running():
            return RuntimeStatus(running=False)
        pid = proc.pid()
        return RuntimeStatus(
            running=True,
            runtime_id=str(pid) if pid else None,
            pid=pid,
            uptime_seconds=proc.uptime_seconds(),
            actual_version=proc.actual_version(),
            online_players=list(proc.online_players),
        )

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        status = self.get_status(server_id)
        if not status.pid:
            return RuntimeResourceStats()
        try:
            result = subprocess.run(
                ["ps", "-p", str(status.pid), "-o", "%cpu,rss", "--no-headers"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return RuntimeResourceStats()
            parts = result.stdout.strip().split()
            if len(parts) < 2:
                return RuntimeResourceStats()
            return RuntimeResourceStats(
                cpu_usage=float(parts[0]),
                ram_usage_mb=float(parts[1]) / 1024.0,
            )
        except Exception:
            return RuntimeResourceStats()

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        proc = self._registry.get(server_id)
        if not proc:
            return []
        return proc.read_logs(tail=tail)

    def send_command(self, server_id: str, command: str) -> None:
        self._registry.send_command(server_id=server_id, cmd=command)

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        proc = self._registry.get(server_id)
        if not proc or not proc.is_running():
            raise RuntimeError("Server is not running")
        proc.add_log_listener(listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        proc = self._registry.get(server_id)
        if proc:
            proc.remove_log_listener(listener)
