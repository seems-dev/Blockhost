from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload
from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStartResult,
    RuntimeStatus,
)


_COMMON_BEDROCK_BINARIES = ("bedrock_server", "bedrock_server.exe")
_UNIT_RE = re.compile(r"[^A-Za-z0-9_.@-]+")


class SystemdRuntime:
    """
    Runtime implementation backed by systemd-run and cgroups v2 properties.

    This is intended for worker nodes or single-machine production hosts where
    systemd owns process lifecycle and resource limits.
    """

    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        executable = self._find_executable(request.server_dir, request.executable_name)
        self._ensure_executable(executable)
        unit = self._unit_name(request.server_id)

        self.stop_server(request.server_id)
        self._run(
            [
                "systemd-run",
                "--unit",
                unit,
                "--collect",
                "--property",
                f"WorkingDirectory={request.server_dir}",
                "--property",
                f"MemoryMax={request.ram_mb}M",
                "--property",
                f"CPUQuota={request.cpu_quota_pct}%",
                "--property",
                "TasksMax=512",
                str(executable),
            ]
        )
        actual_version = self._wait_for_ready(port=request.port)
        status = self.get_status(request.server_id)
        return RuntimeStartResult(
            runtime_id=unit,
            port=request.port,
            actual_version=actual_version or status.actual_version,
        )

    def stop_server(self, server_id: str) -> None:
        unit = self._unit_name(server_id)
        subprocess.run(["systemctl", "stop", unit], capture_output=True, text=True)
        subprocess.run(["systemctl", "reset-failed", unit], capture_output=True, text=True)

    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        self.stop_server(request.server_id)
        return self.start_server(request)

    def get_status(self, server_id: str) -> RuntimeStatus:
        unit = self._unit_name(server_id)
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveState", "--property=MainPID", "--value"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return RuntimeStatus(running=False)
        lines = [line.strip() for line in result.stdout.splitlines()]
        active_state = lines[0] if lines else ""
        pid = self._parse_pid(lines[1] if len(lines) > 1 else "")
        running = active_state == "active" and bool(pid)
        return RuntimeStatus(
            running=running,
            runtime_id=unit if running else None,
            pid=pid,
            uptime_seconds=self._uptime_seconds(pid) if pid else None,
            online_players=[],
        )

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        status = self.get_status(server_id)
        if not status.pid:
            return RuntimeResourceStats()
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
        return RuntimeResourceStats(cpu_usage=float(parts[0]), ram_usage_mb=float(parts[1]) / 1024.0)

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        unit = self._unit_name(server_id)
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(tail), "--no-pager", "-o", "short-iso"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        entries: list[LogEntry] = []
        for line in result.stdout.splitlines():
            entries.append({"ts": None, "line": line})
        return entries

    def send_command(self, server_id: str, command: str) -> None:
        raise RuntimeError("systemd runtime does not support console stdin commands yet")

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        raise RuntimeError("systemd runtime does not support live log listeners yet")

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        return None

    def _wait_for_ready(self, *, port: int, timeout_seconds: float = 25.0) -> str | None:
        start = time.time()
        actual_version: str | None = None
        while time.time() - start < timeout_seconds:
            try:
                pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=0.5)
                parsed = parse_bedrock_pong_payload(pong.payload)
                version = parsed.get("version")
                if isinstance(version, str) and version.strip():
                    actual_version = version.strip()
                break
            except Exception:
                time.sleep(0.25)
        return actual_version

    def _find_executable(self, server_dir: Path, preferred_name: str | None) -> Path:
        candidates: list[str] = []
        if preferred_name and preferred_name.strip():
            candidates.append(preferred_name.strip())
        candidates.extend(_COMMON_BEDROCK_BINARIES)
        for name in candidates:
            path = server_dir / name
            if path.exists() and path.is_file():
                return path
        raise FileNotFoundError(f"Bedrock executable not found in {server_dir}: tried {candidates}")

    def _ensure_executable(self, executable: Path) -> None:
        if os.name == "nt":
            return
        mode = os.stat(executable).st_mode
        if mode & 0o111 == 0:
            os.chmod(executable, mode | 0o111)

    def _run(self, args: list[str]) -> str:
        if args and args[0] == "systemd-run":
            args = [shutil.which("systemd-run") or "/usr/bin/systemd-run", *args[1:]]
        result = subprocess.run(args, check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def _unit_name(self, server_id: str) -> str:
        safe_server_id = _UNIT_RE.sub("-", server_id).strip("-")
        return f"blockhost-bedrock-{safe_server_id}.service"

    def _parse_pid(self, raw: str) -> int | None:
        try:
            pid = int(raw)
        except ValueError:
            return None
        return pid if pid > 0 else None

    def _uptime_seconds(self, pid: int) -> int | None:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "etimes=", "--no-headers"], capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None
