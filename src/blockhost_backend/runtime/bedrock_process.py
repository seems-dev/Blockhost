from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload


_COMMON_BEDROCK_BINARIES = ("bedrock_server", "bedrock_server.exe")
_BASE_PORT = 19132
_PORT_RANGE = range(_BASE_PORT, _BASE_PORT + 100)  # Support up to 100 servers


def _is_port_in_use(port: int) -> bool:
    """Check if a port is in use at the socket level (UDP - what Bedrock uses)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if os.name != "nt":  # Unix/Linux/macOS
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("127.0.0.1", port))
        sock.close()
        return False  # Port is free
    except OSError:
        return True  # Port is in use


def _find_available_port(preferred_port: int | None = None) -> int:
    """Find an available port, starting with preferred_port if given."""
    if preferred_port and not _is_port_in_use(preferred_port):
        return preferred_port
    
    for port in _PORT_RANGE:
        if not _is_port_in_use(port):
            return port
    
    raise RuntimeError(f"No available ports in range {_PORT_RANGE.start}-{_PORT_RANGE.stop}")


def _kill_stale_bedrock_processes() -> None:
    """Kill any lingering bedrock_server processes."""
    try:
        if os.name == "nt":  # Windows
            os.system("taskkill /F /IM bedrock_server.exe 2>nul")
        else:  # Unix/Linux/macOS
            os.system("pkill -9 bedrock_server 2>/dev/null")
    except Exception:
        pass


def _update_server_properties(server_dir: Path, port: int) -> None:
    """Update server.properties with the assigned port."""
    props_file = server_dir / "server.properties"
    if not props_file.exists():
        return
    
    try:
        content = props_file.read_text()
        # Replace both server-port and server-portv6
        content = re.sub(r"server-port=\d+", f"server-port={port}", content)
        content = re.sub(r"server-portv6=\d+", f"server-portv6={port}", content)
        props_file.write_text(content)
    except Exception as e:
        print(f"[blockhost] warning: failed to update server.properties: {e}")


@dataclass(frozen=True)
class BedrockRuntimeInfo:
    pid: int
    port: int
    server_dir: str
    requested_version: str | None
    actual_version: str | None


class BedrockProcess:
    def __init__(self, *, server_id: str, server_dir: Path, port: int, requested_version: str | None) -> None:
        self.server_id = server_id
        self.server_dir = server_dir
        self.port = port
        self.requested_version = requested_version

        self._proc: subprocess.Popen[str] | None = None
        self._log_lock = threading.Lock()
        self._logs: deque[str] = deque(maxlen=4000)
        self._reader_thread: threading.Thread | None = None
        self._actual_version: str | None = None
        self._assigned_port: int | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def actual_version(self) -> str | None:
        return self._actual_version

    def assigned_port(self) -> int | None:
        """Return the actual port assigned to this server (may differ from requested port)."""
        return self._assigned_port

    def read_logs(self, *, tail: int = 200) -> list[str]:
        with self._log_lock:
            if tail <= 0:
                return list(self._logs)
            return list(self._logs)[-tail:]

    def _append_log(self, line: str) -> None:
        with self._log_lock:
            self._logs.append(line.rstrip("\n"))

    def _find_executable(self, *, preferred_name: str | None = None) -> Path:
        candidates: list[str] = []
        if preferred_name and preferred_name.strip():
            candidates.append(preferred_name.strip())
        candidates.extend(_COMMON_BEDROCK_BINARIES)

        for name in candidates:
            p = self.server_dir / name
            if p.exists() and p.is_file():
                return p

        raise FileNotFoundError(
            f"Bedrock executable not found in server dir: tried {candidates}. "
            f"server_dir={self.server_dir}"
        )

    def start(self, *, executable_name: str | None = None) -> BedrockRuntimeInfo:
        if self.is_running():
            raise RuntimeError("Bedrock server already running")

        exe = self._find_executable(preferred_name=executable_name)

        # Ensure the binary is executable on Linux.
        try:
            st = os.stat(exe)
            if st.st_mode & 0o111 == 0:
                os.chmod(exe, st.st_mode | 0o111)
        except Exception:
            pass

        # Clean up stale processes
        self._append_log("[blockhost] cleaning up stale bedrock_server processes")
        _kill_stale_bedrock_processes()
        time.sleep(0.5)  # Give OS time to release the port

        # Find an available port
        self._append_log(f"[blockhost] checking port availability (preferred: {self.port})")
        assigned_port = _find_available_port(preferred_port=self.port)
        self._assigned_port = assigned_port
        
        if assigned_port != self.port:
            self._append_log(f"[blockhost] port {self.port} in use, assigned {assigned_port} instead")
        else:
            self._append_log(f"[blockhost] port {self.port} is available")
        
        # Update server.properties with the assigned port
        self._append_log(f"[blockhost] updating server.properties with port {assigned_port}")
        _update_server_properties(self.server_dir, assigned_port)

        self._append_log(f"[blockhost] starting bedrock_server: {exe.name} (port={assigned_port})")
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(self.server_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._proc = proc

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._append_log(line)
            self._append_log("[blockhost] process output closed")

        t = threading.Thread(target=_reader, name=f"bedrock-log-{self.server_id}", daemon=True)
        t.start()
        self._reader_thread = t

        return BedrockRuntimeInfo(
            pid=proc.pid,
            port=assigned_port,
            server_dir=str(self.server_dir),
            requested_version=self.requested_version,
            actual_version=self._actual_version,
        )

    def stop(self, *, timeout_seconds: float = 30.0) -> None:
        if not self._proc:
            return
        proc = self._proc
        if proc.poll() is not None:
            return

        self._append_log("[blockhost] stopping bedrock_server (sending `stop`)")
        try:
            if proc.stdin is not None:
                proc.stdin.write("stop\n")
                proc.stdin.flush()
        except Exception:
            pass

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                self._append_log("[blockhost] bedrock_server exited cleanly")
                return
            time.sleep(0.1)

        self._append_log("[blockhost] bedrock_server did not exit; terminating")
        try:
            proc.terminate()
        except Exception:
            pass

    def wait_for_ready(self, *, timeout_seconds: float = 25.0) -> BedrockRuntimeInfo:
        """
        Wait until the server responds to RakNet unconnected ping.
        Also captures the runtime-reported version.
        """
        if self._assigned_port is None:
            raise RuntimeError("Server not started yet (assigned_port is None)")

        start = time.time()
        last_error: str | None = None
        while time.time() - start < timeout_seconds:
            if self._proc and self._proc.poll() is not None:
                tail = "\n".join(self.read_logs(tail=80))
                raise RuntimeError(f"Bedrock process exited during startup.\n\nLast logs:\n{tail}")
            try:
                pong = bedrock_unconnected_ping(host="127.0.0.1", port=self._assigned_port, timeout_seconds=0.5)
                parsed = parse_bedrock_pong_payload(pong.payload)
                version = parsed.get("version")
                if isinstance(version, str) and version.strip():
                    self._actual_version = version.strip()
                    self._append_log(f"[blockhost] detected runtime version: {self._actual_version}")
                break
            except Exception as e:
                last_error = str(e)
                time.sleep(0.25)

        if self._actual_version is None and last_error:
            self._append_log(f"[blockhost] bedrock ping not ready yet: {last_error}")

        pid = self.pid()
        if pid is None:
            raise RuntimeError("Bedrock process not running")

        return BedrockRuntimeInfo(
            pid=pid,
            port=self._assigned_port,
            server_dir=str(self.server_dir),
            requested_version=self.requested_version,
            actual_version=self._actual_version,
        )


class BedrockRuntimeRegistry:
    """
    In-memory registry of running Bedrock processes.

    This is intentionally simple for the MVP. Persisted runtime state can be added later.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[str, BedrockProcess] = {}

    def get(self, server_id: str) -> BedrockProcess | None:
        with self._lock:
            return self._procs.get(server_id)

    def ensure_process(self, *, server_id: str, server_dir: Path, port: int, requested_version: str | None) -> BedrockProcess:
        with self._lock:
            existing = self._procs.get(server_id)
            if existing:
                return existing
            proc = BedrockProcess(server_id=server_id, server_dir=server_dir, port=port, requested_version=requested_version)
            self._procs[server_id] = proc
            return proc

    def stop(self, *, server_id: str) -> None:
        with self._lock:
            proc = self._procs.get(server_id)
        if proc:
            proc.stop()

    def remove(self, *, server_id: str) -> None:
        with self._lock:
            proc = self._procs.pop(server_id, None)
        if proc:
            proc.stop()


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")


def resolve_version_dir(*, versions_dir: Path, requested: str | None) -> tuple[str, Path]:
    versions_dir.mkdir(parents=True, exist_ok=True)

    if requested and requested.strip() and requested.strip().upper() not in {"LATEST", "PREVIEW"}:
        ver = requested.strip()
        path = versions_dir / ver
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"Requested Bedrock version template not found: {path}")
        return ver, path

    # Pick "latest" by semver sort (fallback to lexical).
    candidates: list[tuple[tuple[int, int, int] | None, str]] = []
    for child in versions_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        m = _SEMVER_RE.match(name)
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        candidates.append((key, name))
    if not candidates:
        raise FileNotFoundError(f"No Bedrock versions found in {versions_dir} (expected versions/<version>/)")

    candidates.sort(key=lambda item: (item[0] is None, item[0] or (0, 0, 0), item[1]))
    chosen = candidates[-1][1]
    return chosen, versions_dir / chosen


def materialize_server_dir(*, version_dir: Path, servers_dir: Path, server_id: str) -> Path:
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_dir = servers_dir / server_id
    if server_dir.exists():
        return server_dir
    shutil.copytree(version_dir, server_dir)
    return server_dir