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


import threading

# Matches: "Player connected: PlayerName, xuid: 1234567890123456" or "Player Spawned: PlayerName, xuid: 1234567890123456"
_PLAYER_CONNECTED_RE = re.compile(
    r"Player (?:connected|Spawned):\s*([^,]+)(?:,\s*xuid:\s*(\d+))?",
    re.IGNORECASE
)
# Matches: "Player disconnected: PlayerName"
_PLAYER_DISCONNECTED_RE = re.compile(r"Player disconnected:\s*([^,]+)", re.IGNORECASE)

class SystemdRuntime:
    """
    systemd-based Bedrock runtime (production-safe).
    systemd = source of truth.
    """
    def __init__(self, *, servers_root: Path | None = None):
        self._servers_root = servers_root
        self._server_dirs: dict[str, Path] = {}
        self._listeners: dict[str, list[LogListener]] = {}
        self._log_procs: dict[str, subprocess.Popen] = {}
        self._threads: dict[str, threading.Thread] = {}
        # server_id -> {player_name: xuid_or_none}
        self._online_players: dict[str, dict[str, str | None]] = {}
        self._lock = threading.Lock()

    # ---------------- START ----------------
    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        # Resolved CWD: Bedrock resolves worlds/ relative to WorkingDirectory, not the binary path.
        server_dir = request.server_dir.resolve()
        self._server_dirs[request.server_id] = server_dir
        executable = self._find_executable(
            server_dir,
            request.executable_name,
        )

        self._ensure_executable(executable)

        unit = self._unit_name(request.server_id)

        self.stop_server(request.server_id)

        # Clear player list for fresh start
        with self._lock:
            self._online_players[request.server_id] = {}

        fifo_path = server_dir / "stdin.fifo"
        if not fifo_path.exists():
            os.mkfifo(fifo_path)

        # Prefer ./binary when the executable lives in server_dir (including symlinks)
        # so systemd WorkingDirectory governs world/config resolution.
        if executable.parent == server_dir:
            exe_cmd = f"./{executable.name}"
        else:
            exe_cmd = f"'{executable.resolve()}'"

        cmd = [
            "systemd-run",
            "--user",
            "--unit", unit,
            "--collect",

            "--property",
            f"WorkingDirectory={server_dir}",

            "--property",
            f"MemoryMax={request.ram_mb}M",

            "--property",
            f"CPUQuota={request.cpu_quota_pct}%",

            "--property",
            "TasksMax=512",

            "/bin/bash", "-c", f"exec 3<> stdin.fifo; exec {exe_cmd} <&3",
        ]

        subprocess.Popen(cmd)

        version = self._wait_for_ready(request.port)

        # Always start background log stream for player tracking
        with self._lock:
            if request.server_id not in self._log_procs:
                self._start_log_stream(request.server_id)

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

        # Clear player list on stop, stop log stream, and clear listeners
        with self._lock:
            self._online_players.pop(server_id, None)
            self._server_dirs.pop(server_id, None)
            self._stop_log_stream(server_id)
            self._listeners.pop(server_id, None)

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

        with self._lock:
            if not running:
                self._stop_log_stream(server_id)
                self._listeners.pop(server_id, None)
            # Convert {player_name: xuid} dict to list of player names
            players_dict = self._online_players.get(server_id, {})
            players = list(players_dict.keys()) if players_dict else []

        return RuntimeStatus(
            running=running,
            runtime_id=unit if running else None,
            online_players=players if running else None,
        )
    # Get players with their XUIDs (dict format: {name: xuid_or_none})
    def get_online_players_with_xuid(self, server_id: str) -> dict[str, str | None]:
        with self._lock:
            return dict(self._online_players.get(server_id, {}))
    # ---------------- STATS (BASIC) ----------------
    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        unit = self._unit_name(server_id)

        # Get memory and all PIDs in the unit's cgroup
        show_result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MemoryCurrent"],
            capture_output=True,
            text=True,
        )

        mem_bytes = None
        for line in show_result.stdout.splitlines():
            if line.startswith("MemoryCurrent="):
                try:
                    mem_bytes = int(line.split("=", 1)[1])
                except ValueError:
                    pass

        # Get all PIDs in the unit cgroup
        pids_result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "MainPID,ControlGroup"],
            capture_output=True,
            text=True,
        )

        main_pid = None
        cgroup_path = None
        for line in pids_result.stdout.splitlines():
            if line.startswith("MainPID="):
                try:
                    main_pid = int(line.split("=", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("ControlGroup="):
                cgroup_path = line.split("=", 1)[1].strip()

        # Collect all PIDs in the cgroup (covers bash wrapper + child bedrock_server)
        all_pids: list[int] = []
        if cgroup_path:
            try:
                cgroup_procs = f"/sys/fs/cgroup{cgroup_path}/cgroup.procs"
                with open(cgroup_procs) as f:
                    for pid_str in f.read().splitlines():
                        try:
                            all_pids.append(int(pid_str.strip()))
                        except ValueError:
                            pass
            except OSError:
                pass

        # Fallback to main PID if cgroup read failed
        if not all_pids and main_pid and main_pid > 0:
            all_pids = [main_pid]

        cpu_usage = None
        if all_pids:
            ps_result = subprocess.run(
                ["ps", "-p", ",".join(str(p) for p in all_pids), "-o", "%cpu="],
                capture_output=True,
                text=True,
            )
            total_cpu = 0.0
            for val in ps_result.stdout.splitlines():
                try:
                    total_cpu += float(val.strip())
                except ValueError:
                    pass
            if total_cpu > 0 or ps_result.stdout.strip():
                cpu_usage = total_cpu

        ram_usage_mb = None
        if mem_bytes is not None:
            if mem_bytes < 1024**4:  # sanity check
                ram_usage_mb = mem_bytes / (1024.0 * 1024.0)

        return RuntimeResourceStats(
            cpu_usage=cpu_usage,
            ram_usage_mb=ram_usage_mb,
        )

    # ---------------- LOGS ----------------
    def read_logs(self, server_id: str, tail: int = 200) -> list[LogEntry]:
        unit = self._unit_name(server_id)

        result = subprocess.run(
            ["journalctl", "--user", "-u", unit, "-n", str(tail), "--no-pager"],
            capture_output=True,
            text=True,
        )

        return [
            LogEntry(ts=None, line=line)
            for line in result.stdout.splitlines()
        ]

    # ---------------- COMMAND ----------------
    def send_command(self, server_id: str, command: str) -> None:
        server_dir = self._server_dirs.get(server_id)
        if server_dir is None:
            from blockhost_backend.config.config_manager import get_settings
            root = self._servers_root or Path(get_settings().bedrock_servers_dir)
            server_dir = (root / server_id).resolve()
        fifo_path = server_dir / "stdin.fifo"

        status = self.get_status(server_id)
        if not status.running:
            raise RuntimeError("Server is not running")

        if not fifo_path.exists():
            raise RuntimeError("Server stdin FIFO not found. Try restarting the server.")

        with open(fifo_path, "w") as f:
            f.write(command + "\n")

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        with self._lock:
            if server_id not in self._listeners:
                self._listeners[server_id] = []
            # Start stream if not already running (e.g. server was started before this call)
            if server_id not in self._log_procs:
                self._start_log_stream(server_id)
            self._listeners[server_id].append(listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        from blockhost_backend.config.config_manager import get_settings
        settings = get_settings()
        
        with self._lock:
            if server_id in self._listeners:
                try:
                    self._listeners[server_id].remove(listener)
                except ValueError:
                    pass
                
                # Optionally stop stream when no listeners remain (if cleanup enabled)
                # Player tracking depends on the stream, so this is optional
                if settings.console_stream_cleanup_enabled and not self._listeners[server_id]:
                    # No more listeners; optionally clean up stream
                    # Note: player tracking will stop, but reconnecting listeners will restart it
                    self._stop_log_stream(server_id)

    def _start_log_stream(self, server_id: str) -> None:
        unit = self._unit_name(server_id)
        proc = subprocess.Popen(
            ["journalctl", "--user", "-u", unit, "-f", "-n", "0", "--no-pager"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._log_procs[server_id] = proc
        
        def tail_logs():
            try:
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        break
                    stripped = line.strip("\n")
                    entry = LogEntry(ts=None, line=stripped)

                    # Parse player connect/disconnect events
                    m_conn = _PLAYER_CONNECTED_RE.search(stripped)
                    m_disc = _PLAYER_DISCONNECTED_RE.search(stripped)
                    with self._lock:
                        if m_conn:
                            player_name = m_conn.group(1).strip()
                            xuid = m_conn.group(2) if m_conn.lastindex >= 2 else None
                            self._online_players.setdefault(server_id, {})[player_name] = xuid
                        elif m_disc:
                            player_name = m_disc.group(1).strip()
                            self._online_players.setdefault(server_id, {}).pop(player_name, None)
                        listeners = list(self._listeners.get(server_id, []))

                    # Harden listener execution: catch exceptions to prevent stream crash
                    for cb in listeners:
                        try:
                            cb(entry)
                        except Exception as e:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.exception(f"Listener callback failed for server {server_id}: {e}")
            finally:
                proc.stdout.close()
                proc.wait()
                with self._lock:
                    self._log_procs.pop(server_id, None)
                    self._threads.pop(server_id, None)
                
        t = threading.Thread(target=tail_logs, daemon=True)
        t.start()
        self._threads[server_id] = t

    def _stop_log_stream(self, server_id: str) -> None:
        proc = self._log_procs.pop(server_id, None)
        if proc:
            proc.terminate()
        self._threads.pop(server_id, None)

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
        candidates: list[str] = []

        if preferred:
            candidates.append(preferred)

        candidates += list(_COMMON_BEDROCK_BINARIES)

        for name in candidates:
            path = Path(name) if Path(name).is_absolute() else server_dir / name
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