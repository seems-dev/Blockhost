from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from blockhost_backend.minecraft.bedrock_ping import (
    bedrock_unconnected_ping,
    parse_bedrock_pong_payload,
)
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
    systemd-based Bedrock runtime (production-safe).
    systemd = source of truth.
    """

    # ---------------- START ----------------
    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        executable = self._find_executable(
            request.server_dir,
            request.executable_name,
        )

        self._ensure_executable(executable)

        unit = self._unit_name(request.server_id)

        self.stop_server(request.server_id)

        cmd = [
            "systemd-run",
            "--user",
            "--unit", unit,
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

        subprocess.Popen(cmd)

        version = self._wait_for_ready(request.port)

        return RuntimeStartResult(
            runtime_id=unit,
            port=request.port,
            actual_version=version,
        )

    # ---------------- STOP ----------------
    def stop_server(self, server_id: str) -> None:
        unit = self._unit_name(server_id)

        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            capture_output=True
        )

        subprocess.run(
            ["systemctl", "--user", "reset-failed", unit],
            capture_output=True
        )

    # ---------------- RESTART ----------------
    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        self.stop_server(request.server_id)
        return self.start_server(request)

    # ---------------- STATUS ----------------
    def get_status(self, server_id: str) -> RuntimeStatus:
        unit = self._unit_name(server_id)

        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
        )

        running = result.stdout.strip() == "active"

        return RuntimeStatus(
            running=running,
            runtime_id=unit if running else None,
        )

    # ---------------- STATS (BASIC) ----------------
    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        return RuntimeResourceStats()

    # ---------------- LOGS ----------------
    def read_logs(self, server_id: str, tail: int = 200) -> list[LogEntry]:
        unit = self._unit_name(server_id)

        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(tail), "--no-pager"],
            capture_output=True,
            text=True,
        )

        return [
            LogEntry(ts=None, line=line)
            for line in result.stdout.splitlines()
        ]

    # ---------------- COMMAND (NOT SUPPORTED YET) ----------------
    def send_command(self, server_id: str, command: str) -> None:
        raise RuntimeError("Not supported with systemd runtime yet")

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        raise RuntimeError("Not supported yet")

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        pass

    # ---------------- READY CHECK ----------------
    def _wait_for_ready(self, port: int, timeout: float = 25.0) -> str | None:
        start = time.time()
        version = None

        while time.time() - start < timeout:
            try:
                pong = bedrock_unconnected_ping(
                    host="127.0.0.1",
                    port=port,
                    timeout_seconds=0.5,
                )

                parsed = parse_bedrock_pong_payload(pong.payload)

                v = parsed.get("version")
                if isinstance(v, str):
                    version = v.strip()

                break
            except Exception:
                time.sleep(0.25)

        return version

    # ---------------- EXECUTABLE ----------------
    def _find_executable(self, server_dir: Path, preferred: str | None) -> Path:
        candidates = []

        if preferred:
            candidates.append(preferred)

        candidates += list(_COMMON_BEDROCK_BINARIES)

        for name in candidates:
            path = server_dir / name
            if path.exists():
                return path

        raise FileNotFoundError("Bedrock executable not found")

    def _ensure_executable(self, exe: Path) -> None:
        if os.name == "nt":
            return

        mode = os.stat(exe).st_mode
        if mode & 0o111 == 0:
            os.chmod(exe, mode | 0o111)

    def _unit_name(self, server_id: str) -> str:
        safe = _UNIT_RE.sub("-", server_id).strip("-")
        return f"blockhost-bedrock-{safe}.service"