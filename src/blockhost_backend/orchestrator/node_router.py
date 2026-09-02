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
        self._cache: dict[str, tuple[Runtime, float]] = {}
        self._cache_ttl = 10.0  # seconds

    def _get_agent_runtime(self, node: Node) -> AgentRuntime:
        agent_token = get_settings().worker_agent_token
        agent_base_url = f"http://{node.ip_address}:{node.agent_port}"
        return AgentRuntime(agent_base_url=agent_base_url, agent_token=agent_token)

    def _get_runtime(self, server_id: str) -> Runtime:
        import time
        now = time.monotonic()
        if server_id in self._cache:
            runtime, expiry = self._cache[server_id]
            if now < expiry:
                return runtime

        with SessionLocal() as db:
            try:
                server = db.get(Server, uuid.UUID(server_id))
            except ValueError:
                runtime = self._local
            else:
                if not server or not server.node_id:
                    runtime = self._local
                else:
                    node = db.get(Node, server.node_id)
                    if not node:
                        runtime = self._local
                    else:
                        runtime = self._get_agent_runtime(node)
                        
        self._cache[server_id] = (runtime, now + self._cache_ttl)
        return runtime

    def invalidate_server(self, server_id: str) -> None:
        self._cache.pop(server_id, None)

    def invalidate_node(self, node_id: uuid.UUID) -> None:
        self._cache.clear()

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

    def ping_server(self, server_id: str) -> dict[str, object] | None:
        """Proxy Bedrock UDP ping through the agent for remote nodes.
        Returns None for local servers (caller should ping directly).
        """
        rt = self._get_runtime(server_id)
        if hasattr(rt, "ping_server"):
            return rt.ping_server(server_id)
        return None

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        return self._get_runtime(server_id).read_logs(server_id, tail=tail)

    def get_properties(self, server_id: str) -> dict[str, str | bool | int]:
        return self._get_runtime(server_id).get_properties(server_id)

    def update_properties(self, server_id: str, props: dict[str, str | bool | int]) -> None:
        self._get_runtime(server_id).update_properties(server_id, props)

    def send_command(self, server_id: str, command: str) -> None:
        return self._get_runtime(server_id).send_command(server_id, command)

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        return self._get_runtime(server_id).add_log_listener(server_id, listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        return self._get_runtime(server_id).remove_log_listener(server_id, listener)
