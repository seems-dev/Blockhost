from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


# -------------------------
# LOGGING
# -------------------------
@dataclass(frozen=True)
class LogEntry:
    ts: str | None
    line: str


LogListener = Callable[[LogEntry], None]


# -------------------------
# START REQUEST
# -------------------------
@dataclass(frozen=True)
class RuntimeStartRequest:
    server_id: str
    server_dir: Path
    port: int
    requested_version: str | None
    executable_name: str | None
    ram_mb: int
    cpu_quota_pct: int


# -------------------------
# START RESULT
# -------------------------
@dataclass(frozen=True)
class RuntimeStartResult:
    runtime_id: str        # systemd unit name
    port: int
    actual_version: str | None


# -------------------------
# STATUS (SYSTEMD BASED)
# -------------------------
@dataclass(frozen=True)
class RuntimeStatus:
    running: bool
    runtime_id: str | None = None
    active_state: str | None = None


# -------------------------
# RESOURCES
# -------------------------
@dataclass(frozen=True)
class RuntimeResourceStats:
    cpu_usage: float | None = None
    ram_usage_mb: float | None = None


# -------------------------
# RUNTIME INTERFACE
# -------------------------
class Runtime(Protocol):

    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        ...

    def stop_server(self, server_id: str) -> None:
        ...

    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        ...

    def get_status(self, server_id: str) -> RuntimeStatus:
        ...

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        ...

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        ...

    def send_command(self, server_id: str, command: str) -> None:
        ...

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        ...

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        ...