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

# Matches Bedrock connect events:
#   "Player connected: Name, xuid: 12345"   (comma-separated)
#   "Player Spawned: Name xuid: 12345"       (space-separated, newer BDS)
# Also matches Java: "]: Name joined the game"
_PLAYER_CONNECTED_RE = re.compile(
    r"(?:Player (?:connected|Spawned):\s*(.+?)(?:[,\s]+xuid:\s*(\d+))?(?:\s*$|,))"
    r"|(?:\]:\s*([A-Za-z0-9_]+)\s+joined the game)",
    re.IGNORECASE,
)
# Matches Bedrock disconnect:
#   "Player disconnected: Name, xuid: 12345"
#   "Player disconnected: Name, xuid: 12345, pfid: ..."
# Also matches Java: "]: Name left the game"
_PLAYER_DISCONNECTED_RE = re.compile(
    r"(?:Player disconnected:\s*(.+?)(?:[,\s]+xuid:\s*(\d+))?(?:\s*$|,))"
    r"|(?:\]:\s*([A-Za-z0-9_]+)\s+left the game)",
    re.IGNORECASE,
)

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

    def _systemctl_available(self) -> bool:
        return shutil.which("systemctl") is not None

    # ---------------- START ----------------
    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        if shutil.which("systemd-run") is None:
            raise RuntimeError("Local systemd runtime is unavailable; assign this server to a worker node.")

        server_dir = request.server_dir.resolve()
        self._server_dirs[request.server_id] = server_dir
        executable = request.executable_path.resolve()
        self._ensure_executable(executable)

        unit = self._unit_name(request.server_id)
        self.stop_server(request.server_id)

        with self._lock:
            self._online_players[request.server_id] = {}

        fifo_path = server_dir / "stdin.fifo"
        if not fifo_path.exists():
            os.mkfifo(fifo_path)

        is_java = executable.name.endswith(".jar")
        
        if is_java:
            # --- JAVA STARTUP LOGIC ---
            jdk_path = request.jdk_path
            if not jdk_path:
                from blockhost_backend.config.config_manager import get_settings
                jdk_path = Path(get_settings().java_home_path) if get_settings().java_home_path else None
                
            if not jdk_path or not (jdk_path / "bin" / "java").exists():
                raise RuntimeError(f"JDK not found at {jdk_path}. Cannot start Java server.")

            java_bin = jdk_path / "bin" / "java"
            jvm_flags = [
                f"-Xms{request.ram_mb}M",
                f"-Xmx{request.ram_mb}M",
                "-Donline-mode=false",
                "-Dfile.encoding=UTF-8",
                "-XX:+UseG1GC",
                "-XX:+ParallelRefProcEnabled",
                "-XX:MaxGCPauseMillis=200",
                "-XX:+UnlockExperimentalVMOptions",
                "-XX:+DisableExplicitGC",
                "-XX:G1NewSizePercent=30",
                "-XX:G1MaxNewSizePercent=40",
                "-XX:G1HeapRegionSize=8M",
                "-XX:G1ReservePercent=20",
                "-XX:G1HeapWastePercent=5",
                "-XX:G1MixedGCCountTarget=4",
                "-XX:InitiatingHeapOccupancyPercent=15",
                "-XX:G1MixedGCLiveThresholdPercent=90",
                "-XX:G1RSetUpdatingPauseTimePercent=5",
                "-XX:SurvivorRatio=32",
                "-XX:+PerfDisableSharedMem",
                "-XX:MaxTenuringThreshold=1",
                "-Dusing.aikars.flags=https://mcflags.emc.gs",
                "-Daikars.new.flags=true",
            ]
            
            # Flavor specific JVM optimizations
            if request.flavor in ("paper", "purpur") and request.ram_mb > 2048:
                jvm_flags.extend([
                    "-XX:+UseStringDeduplication",
                    "-XX:+OptimizeStringConcat",
                ])
            
            flags_str = " ".join(jvm_flags)
            exe_cmd = f"exec 3<> stdin.fifo; exec '{java_bin}' {flags_str} -jar '{executable}' nogui <&3"
        else:
            # --- BEDROCK STARTUP LOGIC ---
            exe_cmd = f"exec 3<> stdin.fifo; exec '{executable}' <&3"
        import pwd
        import grp
        user_name = pwd.getpwuid(os.getuid()).pw_name
        group_name = grp.getgrgid(os.getgid()).gr_name

        cmd = [
            "systemd-run",
            "--unit", unit,
            "--collect",
            "--property", f"WorkingDirectory={server_dir}",
            "--property", f"User={user_name}",
            "--property", f"Group={group_name}",
            "--property", f"MemoryMax={request.ram_mb + 512}M" if is_java else f"MemoryMax={request.ram_mb}M",
            "--property", f"CPUQuota={request.cpu_quota_pct}%",
            "--property", "MemoryAccounting=yes",
            "--property", "CPUAccounting=yes",
            "--property", "TasksMax=512",
            "/bin/bash", "-c", exe_cmd,
        ]

        subprocess.Popen(cmd)

        # Wait for ready (Java uses TCP, Bedrock uses UDP ping)
        timeout = 120.0 if is_java else 25.0
        version = self._wait_for_ready(request.port, timeout=timeout, is_java=is_java, requested_version=request.requested_version)

        with self._lock:
            if request.server_id not in self._log_procs:
                self._start_log_stream(request.server_id)

        return RuntimeStartResult(
            runtime_id=unit,
            port=request.port,
            actual_version=version or request.requested_version,
        )

    # ---------------- STOP ----------------
    def stop_server(self, server_id: str) -> None:
        if not self._systemctl_available():
            return

        unit = self._unit_name(server_id)

        subprocess.run(
            ["systemctl",  "stop", unit],
            capture_output=True
        )

        subprocess.run(
            ["systemctl",  "reset-failed", unit],
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
        if not self._systemctl_available():
            return RuntimeStatus(running=False)

        unit = self._unit_name(server_id)

        result = subprocess.run(
            ["systemctl",  "is-active", unit],
            capture_output=True,
            text=True,
        )

        running = result.stdout.strip() == "active"
        uptime_seconds = None

        if running:
            show_result = subprocess.run(
                ["systemctl", "show", unit, "-p", "ActiveEnterTimestampMonotonic"],
                capture_output=True,
                text=True,
            )
            for line in show_result.stdout.splitlines():
                if line.startswith("ActiveEnterTimestampMonotonic="):
                    try:
                        active_enter_us = int(line.split("=", 1)[1])
                        if active_enter_us > 0:
                            with open("/proc/uptime", "r") as f:
                                current_uptime_s = float(f.read().split()[0])
                                uptime_seconds = int(current_uptime_s - (active_enter_us / 1000000.0))
                    except (ValueError, FileNotFoundError, IndexError):
                        pass

        with self._lock:
            if not running:
                self._stop_log_stream(server_id)
                self._listeners.pop(server_id, None)
            else:
                if server_id not in self._log_procs:
                    self._start_log_stream(server_id)
            # Convert {player_name: xuid} dict to list of player names
            players_dict = self._online_players.get(server_id, {})
            players = list(players_dict.keys()) if players_dict else []

        return RuntimeStatus(
            running=running,
            runtime_id=unit if running else None,
            uptime_seconds=uptime_seconds,
            online_players=players if running else None,
        )
    # Get players with their XUIDs (dict format: {name: xuid_or_none})
    def get_online_players_with_xuid(self, server_id: str) -> dict[str, str | None]:
        # Try RCON for Java servers first
        server_dir = self._server_dirs.get(server_id)
        if server_dir is None:
            from blockhost_backend.config.config_manager import get_settings
            root = self._servers_root or Path(get_settings().bedrock_servers_dir)
            server_dir = (root / server_id).resolve()
        
        is_java = not (server_dir / "bedrock_server").exists()
        if is_java:
            props = self.get_properties(server_id)
            if props.get("enable-rcon") is True:
                rcon_port = props.get("rcon.port", 25575)
                rcon_password = props.get("rcon.password", "")
                try:
                    from blockhost_backend.minecraft.rcon_client import RconClient
                    with RconClient("127.0.0.1", int(rcon_port), str(rcon_password)) as rcon:
                        resp = rcon.command("/list")
                        # Java /list response: "There are X of a max of Y players online: player1, player2"
                        # Or simply "player1, player2" in some variants.
                        import re
                        m = re.search(r"online:\s*(.*)", resp, re.IGNORECASE)
                        if m:
                            players_str = m.group(1).strip()
                        else:
                            players_str = resp.strip()
                        
                        players = {}
                        if players_str:
                            for name in players_str.split(","):
                                name = name.strip()
                                if name:
                                    players[name] = None
                        return players
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"RCON /list failed for Java server {server_id}: {e}")

        # Fallback to log parsing
        with self._lock:
            return dict(self._online_players.get(server_id, {}))
    # ---------------- STATS (BASIC) ----------------
    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        if not self._systemctl_available():
            return RuntimeResourceStats(cpu_usage=None, ram_usage_mb=None)

        unit = self._unit_name(server_id)

        # Get memory and all PIDs in the unit's cgroup
        show_result = subprocess.run(
            ["systemctl",  "show", unit, "-p", "MemoryCurrent"],
            capture_output=True,
            text=True,
        )

        mem_bytes = None
        for line in show_result.stdout.splitlines():
            if line.startswith("MemoryCurrent="):
                try:
                    val = line.split("=", 1)[1]
                    if val not in ("[not set]", "infinity"):
                        mem_bytes = int(val)
                except ValueError:
                    pass

        # Get all PIDs in the unit cgroup
        pids_result = subprocess.run(
            ["systemctl",  "show", unit, "-p", "MainPID,ControlGroup"],
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

        # Fallback for memory if MemoryCurrent was [not set]
        if mem_bytes is None and all_pids:
            try:
                with open(f"/proc/{all_pids[0]}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            mem_kb = int(line.split()[1])
                            mem_bytes = mem_kb * 1024
                            break
            except (OSError, ValueError, IndexError):
                pass

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

    def get_properties(self, server_id: str) -> dict[str, str | bool | int]:
        server_dir = self._server_dirs.get(server_id)
        if server_dir is None:
            from blockhost_backend.config.config_manager import get_settings
            root = self._servers_root or Path(get_settings().bedrock_servers_dir)
            server_dir = (root / server_id).resolve()
        props_path = server_dir / "server.properties"
        from blockhost_backend.minecraft.java_properties import read_properties
        return read_properties(props_path)

    def update_properties(self, server_id: str, props: dict[str, str | bool | int]) -> None:
        server_dir = self._server_dirs.get(server_id)
        if server_dir is None:
            from blockhost_backend.config.config_manager import get_settings
            root = self._servers_root or Path(get_settings().bedrock_servers_dir)
            server_dir = (root / server_id).resolve()
        props_path = server_dir / "server.properties"
        is_bedrock = (server_dir / "bedrock_server").exists()
        
        if is_bedrock:
            from blockhost_backend.minecraft.bedrock_properties import set_bedrock_server_property
            for k, v in props.items():
                set_bedrock_server_property(props_path, k, v)
        else:
            from blockhost_backend.minecraft.java_properties import set_java_server_property
            for k, v in props.items():
                set_java_server_property(props_path, k, v)

    # ---------------- LOGS ----------------
    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        if shutil.which("journalctl") is None:
            return []

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

        is_java = not (server_dir / "bedrock_server").exists()
        if is_java:
            props = self.get_properties(server_id)
            if props.get("enable-rcon") is True:
                rcon_port = props.get("rcon.port", 25575)
                rcon_password = props.get("rcon.password", "")
                try:
                    from blockhost_backend.minecraft.rcon_client import RconClient
                    with RconClient("127.0.0.1", int(rcon_port), str(rcon_password)) as rcon:
                        rcon.command(command)
                        return
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"RCON command failed for Java server {server_id}: {e}. Falling back to stdin FIFO.")

        if not fifo_path.exists():
            import os
            os.mkfifo(fifo_path)
            # Make sure it's accessible by the user/group that systemd runs as
            # By default it inherits from the python process (root/ubuntu), but systemd-run uses User/Group.
            # But we can at least try creating it.
            # actually if we are the agent, we own the directory so it's fine.

        with open(fifo_path, "w") as f:
            f.write(command + "\n")

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        if shutil.which("journalctl") is None:
            return

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
        if shutil.which("journalctl") is None:
            return

        unit = self._unit_name(server_id)
        proc = subprocess.Popen(
            ["journalctl", "-u", unit, "-f", "-n", "200", "--no-pager"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
                            player_name = (m_conn.group(1) or m_conn.group(3)).strip()
                            xuid = m_conn.group(2) if m_conn.group(2) else None
                            self._online_players.setdefault(server_id, {})[player_name] = xuid
                        elif m_disc:
                            player_name = (m_disc.group(1) or m_disc.group(3)).strip()
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
    def _wait_for_ready(self, port: int, timeout: float = 25.0, is_java: bool = False, requested_version: str | None = None) -> str | None:
        import socket
        start = time.time()
        version = None

        while time.time() - start < timeout:
            if is_java:
                # Simple TCP connect check for Java servers
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(0.5)
                        s.connect(("127.0.0.1", port))
                        return requested_version # Return requested version since we skip SLP for speed
                except Exception:
                    pass
            else:
                # Existing Bedrock UDP Ping logic
                try:
                    pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=0.5)
                    parsed = parse_bedrock_pong_payload(pong.payload)
                    v = parsed.get("version")
                    if isinstance(v, str):
                        version = v.strip()
                    break
                except Exception:
                    pass
            time.sleep(0.25)

        return version

    # ---------------- EXECUTABLE ----------------

    def _ensure_executable(self, exe: Path) -> None:
        if os.name == "nt" or exe.suffix == ".jar":
            return

        mode = os.stat(exe).st_mode
        if mode & 0o111 == 0:
            os.chmod(exe, mode | 0o111)

    def _unit_name(self, server_id: str) -> str:
        safe = _UNIT_RE.sub("-", server_id).strip("-")
        return f"blockhost-server-{safe}.service"
