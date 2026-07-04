import threading
import time
import uuid
from dataclasses import dataclass

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Node, Server
from blockhost_backend.runtime.agent_runtime import AgentRuntime
from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    Runtime,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStartResult,
    RuntimeStatus,
)
from blockhost_backend.runtime.systemd_runtime import SystemdRuntime

_RUNTIME_CACHE_TTL_SECONDS = 30.0


@dataclass
class _CachedRuntime:
    runtime: Runtime
    expires_at: float


class NodeRouter(Runtime):
    """
    A transparent router that implements the Runtime interface.
    It inspects the server's node_id and dispatches the call to either
    the local SystemdRuntime or a remote AgentRuntime.
    """

    def __init__(self):
        self._local = SystemdRuntime()
        self._agent_cache: dict[uuid.UUID, AgentRuntime] = {}
        self._server_cache: dict[str, _CachedRuntime] = {}
        self._lock = threading.Lock()

    def _get_agent_runtime(self, node: Node) -> AgentRuntime:
        if node.id not in self._agent_cache:
            agent_token = get_settings().worker_agent_token
            agent_base_url = f"http://{node.ip_address}:{node.agent_port}"
            self._agent_cache[node.id] = AgentRuntime(
                agent_base_url=agent_base_url, agent_token=agent_token
            )
        return self._agent_cache[node.id]

    def _resolve_runtime(self, server_id: str) -> Runtime:
        with SessionLocal() as db:
            server = db.get(Server, uuid.UUID(server_id))
            if not server or not server.node_id:
                return self._local

            node = db.get(Node, server.node_id)
            if not node:
                return self._local

            return self._get_agent_runtime(node)

    def _get_runtime(self, server_id: str) -> Runtime:
        now = time.monotonic()
        with self._lock:
            cached = self._server_cache.get(server_id)
            if cached is not None and cached.expires_at > now:
                return cached.runtime

        runtime = self._resolve_runtime(server_id)
        with self._lock:
            self._server_cache[server_id] = _CachedRuntime(
                runtime=runtime,
                expires_at=now + _RUNTIME_CACHE_TTL_SECONDS,
            )
        return runtime

    def invalidate_server(self, server_id: str) -> None:
        with self._lock:
            self._server_cache.pop(server_id, None)

    def invalidate_node(self, node_id: uuid.UUID) -> None:
        with self._lock:
            self._agent_cache.pop(node_id, None)
            # Drop any server entries pointing at this node's agent (conservative: clear all).
            self._server_cache.clear()

    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        return self._get_runtime(request.server_id).start_server(request)

    def stop_server(self, server_id: str) -> None:
        return self._get_runtime(server_id).stop_server(server_id)

    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        return self._get_runtime(request.server_id).restart_server(request)

    def get_status(self, server_id: str) -> RuntimeStatus:
        return self._get_runtime(server_id).get_status(server_id)

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        return self._get_runtime(server_id).get_stats(server_id)

    def get_online_players_with_xuid(self, server_id: str) -> dict[str, str | None]:
        return self._get_runtime(server_id).get_online_players_with_xuid(server_id)

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        return self._get_runtime(server_id).read_logs(server_id, tail=tail)

    def send_command(self, server_id: str, command: str) -> None:
        return self._get_runtime(server_id).send_command(server_id, command)

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        return self._get_runtime(server_id).add_log_listener(server_id, listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        return self._get_runtime(server_id).remove_log_listener(server_id, listener)
