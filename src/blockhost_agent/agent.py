from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import stat as _stat
import tarfile
import tempfile
import uuid
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncGenerator
import httpx
import psutil
import websockets
from fastapi import (
    FastAPI,
    Depends,
    Form,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    File,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import shutil

from blockhost_backend.api.files import download_file
from blockhost_backend.runtime.systemd_runtime import SystemdRuntime
from blockhost_backend.runtime.docker_runtime import DockerRuntime
from blockhost_backend.runtime.interface import RuntimeStartRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("blockhost_agent")

app = FastAPI(title="BlockHost Node Agent")
security = HTTPBearer()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "change-me-in-dev")
CONTROL_AGENT_TOKEN = os.environ.get("CONTROL_AGENT_TOKEN", AGENT_TOKEN)
NODE_NAME = os.environ.get("NODE_NAME", "laptop-worker-1")
NODE_PUBLIC_IP = os.environ.get("NODE_PUBLIC_IP", "").strip()
AGENT_PORT = int(os.environ.get("AGENT_PORT", "9000"))
CONTROLLER_WS_URL = os.environ.get(
    "CONTROLLER_WS_URL", "ws://192.168.29.102:8000/api/nodes/ws"
)
SERVERS_ROOT_DIR = Path(
    os.environ.get("SERVERS_ROOT_DIR", f"./agent_storage/{NODE_NAME}")
).resolve()
SERVERS_ROOT_DIR.mkdir(parents=True, exist_ok=True)

VERSIONS_ROOT_DIR = Path(
    os.environ.get("VERSIONS_ROOT_DIR", str(SERVERS_ROOT_DIR.parent / "versions"))
).resolve()
VERSIONS_ROOT_DIR.mkdir(parents=True, exist_ok=True)

DEPLOYMENTS_ROOT_DIR = Path(
    os.environ.get("DEPLOYMENTS_ROOT_DIR", str(SERVERS_ROOT_DIR.parent / "deployments"))
).resolve()
DEPLOYMENTS_ROOT_DIR.mkdir(parents=True, exist_ok=True)

_BINARY_NAMES = ("bedrock_server", "bedrock_server.exe", "bedrock_server_symbols.debug")
_SO_RE = re.compile(r"^lib.*\.so(?:\..*)?$")

# Queue limits to prevent unbounded memory growth
_LOG_STREAM_QUEUE_SIZE = 1000

# Shared systemd runtime instance for this agent
runtime = SystemdRuntime(servers_root=SERVERS_ROOT_DIR)
docker_runtime = DockerRuntime()

# Track background tasks for graceful shutdown
_background_tasks: set[asyncio.Task] = set()

# Health check tracking per deployment
_deployment_health: dict[str, dict] = {}
# Structure: { deployment_id: { "status": "healthy"|"unhealthy"|"crash_loop", "restart_count": 0, "last_healthy_at": None, "consecutive_failures": 0 } }

async def _health_check_loop() -> None:
    """Background loop that checks health of all running deployment containers every 30s."""
    while True:
        await asyncio.sleep(30)
        try:
            containers = docker_runtime.list_managed_containers()
            for c in containers:
                dep_id = c.get("deployment_id")
                if not dep_id or c.get("state") != "running":
                    continue
                
                # Initialize tracking
                if dep_id not in _deployment_health:
                    _deployment_health[dep_id] = {
                        "status": "unknown",
                        "restart_count": 0,
                        "last_healthy_at": None,
                        "consecutive_failures": 0,
                    }
                
                health = _deployment_health[dep_id]
                
                # Try to check health via docker inspect
                try:
                    container_obj = docker_runtime.client.containers.get(c["name"])
                    container_obj.reload()
                    state = container_obj.attrs.get("State", {})
                    
                    if state.get("Running"):
                        restart_count = int(state.get("RestartCount", 0))
                        health["restart_count"] = restart_count
                        
                        # Check if restart count spiked (crash-loop detection)
                        if restart_count > 3:
                            health["consecutive_failures"] += 1
                            if health["consecutive_failures"] >= 3:
                                health["status"] = "crash_loop"
                                logger.warning("Deployment %s detected as crash-looping (%d restarts)", dep_id, restart_count)
                                # Stop the container to prevent resource waste
                                try:
                                    docker_runtime.stop_deployment(dep_id)
                                except Exception:
                                    pass
                                continue
                            else:
                                health["status"] = "unhealthy"
                        else:
                            health["status"] = "healthy"
                            health["consecutive_failures"] = 0
                            health["last_healthy_at"] = datetime.now(timezone.utc).isoformat()
                            
                            # Quota enforcement: check storage limit
                            storage_limit_str = container_obj.labels.get("blockhost.storage_limit_mb")
                            if storage_limit_str:
                                try:
                                    storage_limit_mb = int(storage_limit_str)
                                    dep_dir = (DEPLOYMENTS_ROOT_DIR / dep_id).resolve()
                                    if dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()) and dep_dir.exists():
                                        import subprocess
                                        result = subprocess.run(["du", "-sb", str(dep_dir)], capture_output=True, text=True, check=True)
                                        bytes_str = result.stdout.split()[0]
                                        disk_usage_mb = int(bytes_str) // (1024 * 1024)
                                        if disk_usage_mb > storage_limit_mb:
                                            health["status"] = "quota_exceeded"
                                            logger.warning("Deployment %s exceeded storage quota (%d MB > %d MB). Stopping.", dep_id, disk_usage_mb, storage_limit_mb)
                                            try:
                                                docker_runtime.stop_deployment(dep_id)
                                            except Exception:
                                                pass
                                except Exception as e:
                                    logger.debug("Failed to check storage quota for %s: %s", dep_id, e)
                    else:
                        health["status"] = "unhealthy"
                        health["consecutive_failures"] += 1
                except Exception as e:
                    health["status"] = "unhealthy"
                    health["consecutive_failures"] += 1
                    
        except Exception as e:
            logger.debug("Health check loop error: %s", e)

async def _docker_prune_loop() -> None:
    """Background loop that cleans up old unused Docker images every 24 hours."""
    while True:
        try:
            logger.info("Running automatic Docker image prune...")
            import subprocess
            result = subprocess.run(
                ["docker", "image", "prune", "-a", "-f", "--filter", "until=24h"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info("Docker prune successful: %s", result.stdout.strip())
            else:
                logger.error("Docker prune failed: %s", result.stderr.strip())
        except Exception as e:
            logger.error("Error running docker prune loop: %s", e)
        
        # Sleep for 24 hours
        await asyncio.sleep(24 * 60 * 60)

from datetime import timezone


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    if credentials.credentials not in {AGENT_TOKEN, CONTROL_AGENT_TOKEN}:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


def _validate_server_id(server_id: str) -> str:
    """Ensure server_id is a valid UUID to prevent path traversal."""
    try:
        uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server ID format")
    return server_id


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------


class StartPayload(BaseModel):
    server_dir_rel: str
    port: int
    requested_version: str | None = None
    executable_path: str | None = None
    ram_mb: int
    cpu_quota_pct: int
    jdk_path: str | None = None  # NEW
    server_properties_dict: dict[str, str | int | bool] | None = None
    jar_download_url: str | None = None
    storage_mb: int = 0


class CommandPayload(BaseModel):
    command: str


class WritePayload(BaseModel):
    content: str


class DeploymentStartPayload(BaseModel):
    docker_image: str
    internal_port: int
    env_vars: dict[str, str] = {}
    volume_path: str | None = None
    volume_mount_path: str = "/data"
    ram_limit_mb: int = 512
    cpu_limit: float = 1.0
    storage_limit_mb: int = 5120
    project_id: str | None = None
    health_check_path: str | None = None
    command: list[str] | str | None = None
    network_aliases: list[str] | None = None

class DatabaseStartPayload(BaseModel):
    docker_image: str
    internal_port: int
    env_vars: dict[str, str] = {}
    volume_path: str | None = None
    volume_mount_path: str = "/var/lib/postgresql/data"
    ram_limit_mb: int = 512
    cpu_limit: float = 1.0
    storage_limit_mb: int = 5120
    project_id: str | None = None
    command: list[str] | str | None = None
    network_aliases: list[str] | None = None

class DatabaseBackupPayload(BaseModel):
    engine: str
    username: str = ""
    password: str = ""
    database: str = ""
    s3_key: str

class DatabaseRestorePayload(BaseModel):
    engine: str
    username: str = ""
    password: str = ""
    database: str = ""
    s3_key: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/agent/health")
def health_check() -> Any:
    return {"status": "ok", "node_name": NODE_NAME}


# ---------------------------------------------------------------------------
# Server Lifecycle
# ---------------------------------------------------------------------------
JDK_CACHE_DIR = Path(os.environ.get("JDK_CACHE_DIR", f"./agent_storage/jdks")).resolve()
JDK_CACHE_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/agent/jdks/{java_version}/ensure")
async def ensure_jdk(java_version: int, _token: str = Depends(verify_token)) -> Any:
    jdk_dir = JDK_CACHE_DIR / str(java_version)
    if (jdk_dir / "bin" / "java").exists():
        return {"status": "already_installed", "path": str(jdk_dir)}

    import httpx
    download_url = f"https://api.adoptium.net/v3/binary/latest/{java_version}/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk"
    
    tar_path = JDK_CACHE_DIR / f"{java_version}.tar.gz"
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", download_url, follow_redirects=True, timeout=300.0) as resp:
                if resp.status_code != 200:
                    raise HTTPException(status_code=400, detail=f"Failed to fetch JDK: {resp.status_code}")
                with open(tar_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        f.write(chunk)
                        
        shutil.unpack_archive(tar_path, JDK_CACHE_DIR)
        
        # Adoptium extracts to a folder like `jdk-21.0.4+7`. Rename it to just the version.
        extracted = None
        for item in JDK_CACHE_DIR.iterdir():
            if item.is_dir() and item.name.startswith(f"jdk-{java_version}"):
                extracted = item
                break
                
        if extracted and extracted != jdk_dir:
            if jdk_dir.exists():
                shutil.rmtree(jdk_dir)
            extracted.rename(jdk_dir)
            
        if not (jdk_dir / "bin" / "java").exists():
            raise HTTPException(status_code=500, detail="JDK extraction failed")
            
        return {"status": "installed", "path": str(jdk_dir)}
    finally:
        if tar_path.exists():
            tar_path.unlink()

@app.post("/agent/servers/{server_id}/start")
def start_server(
    server_id: str,
    payload: StartPayload,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    server_dir.mkdir(parents=True, exist_ok=True)
    
    # Enforce XFS Project Quota if storage limits are provided
    if payload.storage_mb > 0:
        try:
            import subprocess
            project_id = uuid.UUID(server_id).int & 0xFFFFFFFF
            quota_bytes = payload.storage_mb * 1024 * 1024
            subprocess.run(["xfs_quota", "-x", "-c", f"project -s -p {server_dir} {project_id}", "/"], check=False)
            subprocess.run(["xfs_quota", "-x", "-c", f"limit -p bsoft={quota_bytes} bhard={quota_bytes} {project_id}", "/"], check=False)
            logger.info("Enforced XFS quota of %sMB for server %s", payload.storage_mb, server_id)
        except Exception as e:
            logger.warning("Failed to apply XFS quota for %s: %s", server_id, e)

    is_java = bool(payload.jar_download_url) or (payload.executable_path and payload.executable_path.endswith(".jar"))

    if payload.server_properties_dict is not None:
        props_path = server_dir / "server.properties"
        if not props_path.exists():
            lines = []
            for k, v in payload.server_properties_dict.items():
                safe_k = k.replace("_", "-")
                v_str = "true" if v is True else "false" if v is False else str(v)
                lines.append(f"{safe_k}={v_str}")
            props_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            if is_java:
                from blockhost_backend.minecraft.java_properties import set_java_server_property
                set_java_server_property(props_path, "server-port", payload.port)
            else:
                from blockhost_backend.minecraft.bedrock_properties import set_bedrock_server_property
                set_bedrock_server_property(props_path, "server-port", payload.port)
                set_bedrock_server_property(props_path, "server-portv6", payload.port + 1)

    actual_version = payload.requested_version

    if is_java:
        (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        if payload.jar_download_url:
            version_str = payload.requested_version or "latest"
            
            # 1. Define a global cache directory for Java JARs
            java_jar_cache_dir = VERSIONS_ROOT_DIR / "java-jars"
            java_jar_cache_dir.mkdir(parents=True, exist_ok=True)
            cached_jar_path = java_jar_cache_dir / f"{version_str}.jar"
            
            server_jar_path = server_dir / f"server-{version_str}.jar"

            # 2. Download ONLY if it doesn't exist in the global cache
            if not cached_jar_path.exists():
                logger.info(f"Downloading Java jar from {payload.jar_download_url} to global cache {cached_jar_path}")
                try:
                    with httpx.Client(follow_redirects=True, timeout=300.0) as client:
                        with client.stream("GET", payload.jar_download_url) as resp:
                            resp.raise_for_status()
                            with open(cached_jar_path, "wb") as f:
                                for chunk in resp.iter_bytes(1024*1024):
                                    f.write(chunk)
                except Exception as e:
                    if cached_jar_path.exists():
                        cached_jar_path.unlink()
                    raise HTTPException(status_code=500, detail=f"Failed to download jar: {e}")
            
            # 3. Clean up any old jar symlinks/files in the server directory to prevent version conflicts
            for item in server_dir.glob("server-*.jar"):
                try:
                    item.unlink()
                except OSError:
                    pass

            # 4. Create a relative symlink in the server folder pointing to the cached JAR
            if not server_jar_path.exists():
                rel_path = os.path.relpath(cached_jar_path, start=server_dir)
                os.symlink(rel_path, server_jar_path)
                
            executable_path = server_jar_path
        else:
            executable_path = Path(payload.executable_path) if payload.executable_path else None
            if not executable_path or not executable_path.is_file():
                raise HTTPException(status_code=404, detail="Java executable not found on agent")
    else:
        version_name = (payload.requested_version or "").strip()
        if not version_name:
            raise HTTPException(status_code=400, detail="requested_version is required on agent")
        server_dir, actual_version = _ensure_server_layout(server_id, version_name)
        if payload.executable_path:
            executable_path = Path(payload.executable_path)
        else:
            executable_path = server_dir / "bedrock_server"

    if not server_dir.resolve().is_relative_to(SERVERS_ROOT_DIR):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    req = RuntimeStartRequest(
        server_id=server_id,
        server_dir=server_dir,
        port=payload.port,
        requested_version=actual_version,
        executable_path=executable_path,
        ram_mb=payload.ram_mb,
        cpu_quota_pct=payload.cpu_quota_pct,
        jdk_path=Path(payload.jdk_path) if payload.jdk_path else None,
    )
    result = runtime.start_server(req)
    return {
        "runtime_id": result.runtime_id,
        "port": result.port,
        "actual_version": result.actual_version,
    }

@app.post("/agent/servers/{server_id}/clear-jars")
def clear_jars(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Deletes all server-*.jar files/symlinks in the server directory."""
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    if not server_dir.exists():
        return {"status": "ok", "deleted": 0}
    
    deleted = 0
    for item in server_dir.glob("server-*.jar"):
        try:
            item.unlink()
            deleted += 1
        except OSError:
            pass
    return {"status": "ok", "deleted": deleted}
@app.post("/agent/halt-all")
def agent_halt_all(
    _token: str = Depends(verify_token),
) -> Any:
    """Halt all running servers forcefully (Split-Brain handling)."""
    running_counts = runtime.get_all_running_player_counts()
    for sid in running_counts.keys():
        try:
            logger.info("Halting server %s due to split-brain recovery", sid)
            runtime.stop_server(sid)
        except Exception as e:
            logger.error("Failed to halt server %s: %s", sid, e)
    return {"status": "ok", "halted": len(running_counts)}


@app.post("/agent/servers/{server_id}/stop")
def stop_server(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    runtime.stop_server(server_id)
    return {"status": "ok"}


@app.delete("/agent/servers/{server_id}/data")
def purge_server_data(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Stop the process (if any) and delete the local server directory.

    Used after a successful migration away from this node, or when the
    control plane permanently deletes a world.
    """
    _validate_server_id(server_id)
    server_dir = (SERVERS_ROOT_DIR / server_id).resolve()
    if not server_dir.is_relative_to(SERVERS_ROOT_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid server path")

    try:
        runtime.stop_server(server_id)
    except Exception as e:
        logger.warning("Stop before purge for %s failed (continuing): %s", server_id, e)

    if server_dir.exists():
        shutil.rmtree(server_dir, ignore_errors=True)
        logger.info("Purged local server data for %s at %s", server_id, server_dir)
        return {"status": "ok", "deleted": True}

    return {"status": "ok", "deleted": False}


@app.get("/agent/servers/{server_id}/status")
def get_status(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    status_obj = runtime.get_status(server_id)
    return {
        "running": status_obj.running,
        "runtime_id": status_obj.runtime_id,
        "active_state": status_obj.active_state,
        "uptime_seconds": status_obj.uptime_seconds,
        "online_players": status_obj.online_players,
    }


@app.get("/agent/servers/{server_id}/stats")
def get_stats(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    stats_obj = runtime.get_stats(server_id)
    return {
        "cpu_usage": stats_obj.cpu_usage,
        "ram_usage_mb": stats_obj.ram_usage_mb,
    }


@app.get("/agent/servers/{server_id}/online_players")
def get_online_players(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    return runtime.get_online_players_with_xuid(server_id)


@app.get("/agent/servers/{server_id}/logs")
def get_logs(
    server_id: str,
    tail: int = Query(default=200, ge=1, le=2000),
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    logs = runtime.read_logs(server_id, tail=tail)
    return [{"ts": log.ts, "line": log.line} for log in logs]


@app.post("/agent/servers/{server_id}/command")
def send_command(
    server_id: str,
    payload: CommandPayload,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    runtime.send_command(server_id, payload.command)
    return {"status": "ok"}


@app.get("/agent/servers/{server_id}/ping")
def ping_server(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Perform a local Bedrock UDP ping (RakNet) on behalf of the control plane.

    The control plane cannot reliably send UDP across Docker NAT / AWS
    security groups, so we ping 127.0.0.1 here on the agent node and
    return the parsed pong payload.
    """
    _validate_server_id(server_id)
    status = runtime.get_status(server_id)
    if not status.running:
        return {"reachable": False}

    from blockhost_backend.minecraft.bedrock_ping import (
        bedrock_unconnected_ping,
        parse_bedrock_pong_payload,
    )

    # Determine the port from server.properties (fall back to 19132)
    server_dir = SERVERS_ROOT_DIR / server_id
    port = 19132
    props_path = server_dir / "server.properties"
    if props_path.is_file():
        for line in props_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("server-port="):
                try:
                    port = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
                break

    try:
        pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=2.0)
        parsed = parse_bedrock_pong_payload(pong.payload)
        return {
            "reachable": True,
            "latency_ms": pong.latency_ms,
            **parsed,
        }
    except Exception as e:
        logger.warning("Bedrock ping failed for server %s on port %d: %s", server_id, port, e)
        return {"reachable": False}


@app.get("/agent/servers/{server_id}/properties")
def get_server_properties(
    server_id: str,
    _token: str = Depends(verify_token),
) -> dict[str, str | bool | int]:
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    props_path = server_dir / "server.properties"
    
    # We can use either bedrock or java read_properties since they are identical
    from blockhost_backend.minecraft.java_properties import read_properties
    return read_properties(props_path)


@app.put("/agent/servers/{server_id}/properties")
def update_server_properties(
    server_id: str,
    props: dict[str, str | bool | int],
    _token: str = Depends(verify_token),
) -> dict[str, str]:
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    props_path = server_dir / "server.properties"
    
    # Determine flavor by checking if it's a bedrock server directory structure
    # (simplest way on the agent is checking if bedrock_server binary exists)
    is_bedrock = (server_dir / "bedrock_server").exists()
    
    if is_bedrock:
        from blockhost_backend.minecraft.bedrock_properties import set_bedrock_server_property
        for k, v in props.items():
            set_bedrock_server_property(props_path, k, v)
    else:
        from blockhost_backend.minecraft.java_properties import set_java_server_property
        for k, v in props.items():
            set_java_server_property(props_path, k, v)
            
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Log Streaming (WebSocket)
# ---------------------------------------------------------------------------


@app.websocket("/agent/servers/{server_id}/logs/stream")
async def stream_logs(
    websocket: WebSocket,
    server_id: str,
    token: str = Query(default=""),
) -> None:
    if token not in {AGENT_TOKEN, CONTROL_AGENT_TOKEN}:
        await websocket.close(code=1008, reason="Invalid token")
        return

    _validate_server_id(server_id)
    await websocket.accept()

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_LOG_STREAM_QUEUE_SIZE)
    dropped_count = 0

    def log_listener(entry: Any) -> None:
        nonlocal dropped_count
        try:
            queue.put_nowait(entry)
            dropped_count = 0
        except asyncio.QueueFull:
            dropped_count += 1
            if dropped_count <= 3:
                logger.warning(
                    "Log stream queue full for %s, dropping entries (dropped=%d)",
                    server_id,
                    dropped_count,
                )

    runtime.add_log_listener(server_id, log_listener)

    try:
        while True:
            try:
                entry = await queue.get()
                await websocket.send_json({"ts": entry.ts, "line": entry.line})
            except asyncio.CancelledError:
                break
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error("Error sending log entry: %s", e)
                break
    finally:
        runtime.remove_log_listener(server_id, log_listener)


# ---------------------------------------------------------------------------
# Server Provisioning
# ---------------------------------------------------------------------------


def _tar_extract_filter(tarinfo: tarfile.TarInfo, dest_path: str) -> tarfile.TarInfo | None:
    """
    Filter for tar extraction that:
    - Prevents path traversal attacks
    - Skips FIFOs, sockets, and device files that cause errors
    - Only allows regular files, directories, and symlinks
    """
    # Security: Block absolute paths and path traversal
    if tarinfo.name.startswith(("/", "\\")) or ".." in tarinfo.name:
        logger.warning("Blocked path traversal in tar: %s", tarinfo.name)
        return None

    # Skip non-regular file types (FIFOs, sockets, block/char devices)
    allowed_types = {tarfile.REGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE}
    if tarinfo.type not in allowed_types:
        logger.debug("Skipping non-regular file in tar: %s (type=%d)", tarinfo.name, tarinfo.type)
        return None

    # Double-check with mode bits as fallback
    if _stat.S_ISFIFO(tarinfo.mode) or _stat.S_ISSOCK(tarinfo.mode):
        return None

    return tarinfo


def _version_meta_path(version: str) -> Path:
    return VERSIONS_ROOT_DIR / version / ".blockhost_version_meta.json"


def _read_version_hash(version: str) -> str | None:
    meta_path = _version_meta_path(version)
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("hash")
    except (json.JSONDecodeError, OSError):
        return None


def _write_version_hash(version: str, content_hash: str) -> None:
    meta_path = _version_meta_path(version)
    meta_path.write_text(json.dumps({"hash": content_hash}), encoding="utf-8")


def _compute_version_binary_hash(version_dir: Path) -> str | None:
    for name in _BINARY_NAMES:
        bin_path = version_dir / name
        if bin_path.is_file():
            return hashlib.sha256(bin_path.read_bytes()).hexdigest()
    return None


def _fetch_latest_bedrock_version() -> str:
    """Fetch the latest available Bedrock version from MCJarFiles."""
    resp = httpx.get(
        "https://mcjarfiles.com/api/get-versions/bedrock/latest/linux",
        follow_redirects=True,
        timeout=30.0,
    )
    resp.raise_for_status()
    versions = resp.json()
    if not versions:
        raise HTTPException(status_code=502, detail="MCJarFiles returned empty version list")
    return versions[0]


def _download_bedrock_version(version_name: str) -> Path:
    """Download and extract a Bedrock version into VERSIONS_ROOT_DIR. Returns the installed version dir."""
    import tempfile, zipfile, stat

    url = f"https://mcjarfiles.com/api/get-jar/bedrock/latest/linux/{version_name}"
    VERSIONS_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    version_dir = VERSIONS_ROOT_DIR / version_name

    # Mojang CDN requires a browser-like User-Agent to serve the file
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    with tempfile.TemporaryDirectory(prefix="bedrock-agent-dl-") as td:
        tmpdir = Path(td)
        zip_path = tmpdir / "bedrock.zip"

        logger.info(f"Downloading Bedrock {version_name} from MCJarFiles...")
        try:
            with httpx.Client(follow_redirects=True, timeout=600.0, headers=headers) as client:
                with client.stream("GET", url) as resp:
                    if resp.status_code == 404:
                        raise ValueError(f"Version {version_name!r} not found on MCJarFiles (HTTP 404)")
                    if resp.status_code != 200:
                        raise ValueError(f"MCJarFiles returned HTTP {resp.status_code} for {version_name!r}")
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_bytes(1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                pct = downloaded * 100 // total
                                if downloaded % (10 * 1024 * 1024) < 1024 * 1024:
                                    logger.info(f"Bedrock {version_name}: {pct}% ({downloaded // (1024*1024)}MB / {total // (1024*1024)}MB)")
                    logger.info(f"Download complete for Bedrock {version_name} ({downloaded // (1024*1024)}MB)")
        except ValueError:
            raise  # re-raise our descriptive errors
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Network error downloading Bedrock {version_name}: {e}")

        extract_dir = tmpdir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Extracting Bedrock {version_name}...")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    # Extract the file
                    extracted_path = extract_dir / info.filename
                    zf.extract(info, extract_dir)
                    # CRITICAL: Restore Unix permissions from the ZIP's external_attr
                    # Python's extractall() silently drops them — bedrock_server would land as 0644 (not executable)
                    unix_perms = (info.external_attr >> 16) & 0xFFFF
                    if unix_perms:
                        try:
                            extracted_path.chmod(unix_perms & 0o7777)
                        except OSError:
                            pass
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Invalid zip for Bedrock {version_name}: {e}")

        # Locate the bedrock_server binary (may be in a subdirectory)
        has_binary = any((extract_dir / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
        if not has_binary:
            children = [p for p in extract_dir.iterdir() if p.is_dir()]
            if len(children) == 1:
                inner = children[0]
                has_binary = any((inner / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
                if has_binary:
                    extract_dir = inner

        if not has_binary:
            raise HTTPException(status_code=500, detail=f"Downloaded ZIP for {version_name} does not contain bedrock_server binary")

        # Ensure bedrock_server is executable regardless of what was in the ZIP
        for bin_name in ("bedrock_server", "bedrock_server.exe"):
            bin_path = extract_dir / bin_name
            if bin_path.exists():
                current = bin_path.stat().st_mode
                bin_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                logger.info(f"Set executable bit on {bin_path}")

        tmp_install = VERSIONS_ROOT_DIR / f".tmp-{version_name}"
        if tmp_install.exists():
            shutil.rmtree(tmp_install)
        shutil.move(str(extract_dir), str(tmp_install))
        tmp_install.rename(version_dir)
        logger.info(f"Successfully installed Bedrock version {version_name} to {version_dir}")

    return version_dir


def _ensure_server_layout(server_id: str, version_name: str) -> tuple[Path, str]:
    """Link static version assets into the per-server dir; preserve worlds/config.
    
    Returns (server_dir, actual_version_used) — actual version may differ from
    requested if the requested version is unavailable and we fell back to latest.
    """
    version_dir = VERSIONS_ROOT_DIR / version_name
    actual_version = version_name

    if not version_dir.is_dir():
        try:
            version_dir = _download_bedrock_version(version_name)
        except ValueError as e:
            # Version not available — fall back to latest
            logger.warning(f"{e}. Falling back to latest Bedrock version.")
            try:
                actual_version = _fetch_latest_bedrock_version()
            except Exception as fetch_err:
                raise HTTPException(status_code=502, detail=f"Requested version unavailable and failed to fetch latest: {fetch_err}")
            version_dir = VERSIONS_ROOT_DIR / actual_version
            if not version_dir.is_dir():
                version_dir = _download_bedrock_version(actual_version)
            logger.info(f"Using Bedrock version {actual_version} (requested: {version_name})")

    server_dir = SERVERS_ROOT_DIR / server_id
    server_dir.mkdir(parents=True, exist_ok=True)

    for child in version_dir.iterdir():
        if child.name.startswith(".blockhost"):
            continue
        target = server_dir / child.name
        if target.exists():
            continue
        try:
            if child.is_dir():
                rel = os.path.relpath(child, start=server_dir)
                os.symlink(rel, target)
            elif child.is_file():
                name = child.name
                if name in _BINARY_NAMES or _SO_RE.match(name):
                    rel = os.path.relpath(child, start=server_dir)
                    os.symlink(rel, target)
                else:
                    shutil.copy2(child, target)
        except OSError as e:
            logger.warning("Failed to link/copy %s into server dir: %s", child.name, e)

    return server_dir, actual_version


@app.get("/agent/bedrock-versions")
def list_bedrock_versions(_token: str = Depends(verify_token)) -> Any:
    """List Bedrock versions locally cached on the agent."""
    if not VERSIONS_ROOT_DIR.exists():
        return {"versions": []}
        
    versions = []
    for item in VERSIONS_ROOT_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            versions.append(item.name)
            
    return {"versions": sorted(versions)}


@app.get("/agent/versions/{version}")
def get_version_status(
    version: str,
    _token: str = Depends(verify_token),
) -> Any:
    version_dir = VERSIONS_ROOT_DIR / version
    if not version_dir.is_dir():
        raise HTTPException(status_code=404, detail="Version not provisioned")
    return {"version": version, "hash": _read_version_hash(version), "provisioned": True}


@app.post("/agent/versions/{version}/provision")
async def provision_version(
    version: str,
    file: UploadFile = File(...),
    hash: str = Form(default=""),
    _token: str = Depends(verify_token),
) -> Any:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    version_dir = VERSIONS_ROOT_DIR / version
    if version_dir.exists():
        shutil.rmtree(version_dir, ignore_errors=True)
    version_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(fileobj=BytesIO(content), mode="r:gz") as tar:
            tar.extractall(version_dir, filter=_tar_extract_filter)

        for binary_name in _BINARY_NAMES:
            bin_path = version_dir / binary_name
            if bin_path.exists() and bin_path.is_file():
                try:
                    st = bin_path.stat()
                    bin_path.chmod(st.st_mode | 0o111)
                except OSError as e:
                    logger.warning("Failed to chmod %s: %s", bin_path, e)

        if hash:
            _write_version_hash(version, hash)
        else:
            computed = _compute_version_binary_hash(version_dir)
            if computed:
                _write_version_hash(version, computed)
        return {"status": "ok", "version": version}

    except tarfile.TarError as e:
        shutil.rmtree(version_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Invalid tar archive: {e}")
    except Exception as e:
        shutil.rmtree(version_dir, ignore_errors=True)
        logger.exception("Version provisioning failed for %s", version)
        raise HTTPException(status_code=500, detail=f"Version provisioning failed: {e}")


@app.post("/agent/servers/{server_id}/sync-config")
async def sync_server_config(
    server_id: str,
    server_properties: UploadFile | None = File(default=None),
    allowlist_json: UploadFile | None = File(default=None),
    permissions_json: UploadFile | None = File(default=None),
    _token: str = Depends(verify_token),
) -> Any:
    """Write config files directly into the server dir (no tar — avoids dereference/symlink issues)."""
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    server_dir.mkdir(parents=True, exist_ok=True)

    uploads = (
        (server_properties, "server.properties"),
        (allowlist_json, "allowlist.json"),
        (permissions_json, "permissions.json"),
    )
    written = 0
    for upload, dest_name in uploads:
        if upload is None:
            continue
        content = await upload.read()
        if not content:
            continue
        (server_dir / dest_name).write_bytes(content)
        written += 1

    if written == 0:
        raise HTTPException(status_code=400, detail="No config files provided")
    return {"status": "ok", "written": written}


@app.post("/agent/servers/{server_id}/provision")
async def provision_server(
    server_id: str,
    file: UploadFile = File(...),
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    if server_dir.exists():
        shutil.rmtree(server_dir, ignore_errors=True)
    server_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(fileobj=BytesIO(content), mode="r:gz") as tar:
            tar.extractall(server_dir, filter=_tar_extract_filter)

        for binary_name in ("bedrock_server", "bedrock_server.exe"):
            bin_path = server_dir / binary_name
            if bin_path.exists() and bin_path.is_file():
                try:
                    st = bin_path.stat()
                    bin_path.chmod(st.st_mode | 0o111)
                except OSError as e:
                    logger.warning("Failed to chmod %s: %s", bin_path, e)

        return {"status": "ok"}

    except tarfile.TarError as e:
        shutil.rmtree(server_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Invalid tar archive: {e}")
    except Exception as e:
        shutil.rmtree(server_dir, ignore_errors=True)
        logger.exception("Provisioning failed for server %s", server_id)
        raise HTTPException(status_code=500, detail=f"Provisioning failed: {e}")


# ---------------------------------------------------------------------------
# S3 World Backup & Restore (for durability and cross-node migration)
# ---------------------------------------------------------------------------

# Files/dirs to EXCLUDE from world backup zips — large binaries that are
# already cached separately via the version provisioning system.
_BACKUP_EXCLUDE_NAMES = {
    "bedrock_server", "bedrock_server.exe", "bedrock_server_symbols.debug",
    "__pycache__",
}
_BACKUP_EXCLUDE_EXTS = {".so", ".debug", ".jar"}


def _should_exclude(name: str) -> bool:
    if name in _BACKUP_EXCLUDE_NAMES:
        return True
    if name.startswith("."):
        return True
    for ext in _BACKUP_EXCLUDE_EXTS:
        if name.endswith(ext):
            return True
    return False


@app.post("/agent/servers/{server_id}/backup")
def backup_server_to_s3(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Zip the server directory (world data + config) and upload to S3."""
    import zipfile
    from blockhost_backend.services.s3_storage import (
        s3_key_for_server,
        upload_file_to_s3,
    )

    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id

    if not server_dir.is_dir():
        raise HTTPException(status_code=404, detail="Server directory not found")

    s3_key = s3_key_for_server(server_id)

    with tempfile.TemporaryDirectory(prefix="blockhost-backup-") as td:
        zip_path = Path(td) / "world.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(server_dir, followlinks=True):
                # Prune excluded directories in-place
                dirs[:] = [d for d in dirs if not _should_exclude(d)]

                for fname in files:
                    if _should_exclude(fname):
                        continue
                    full_path = Path(root) / fname
                    # Skip symlinks pointing outside the server dir
                    if full_path.is_symlink():
                        target = full_path.resolve()
                        if not target.is_relative_to(server_dir):
                            continue
                    arc_name = full_path.relative_to(server_dir)
                    zf.write(full_path, arc_name)

        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        logger.info("Backup zip for %s: %.1f MB", server_id, zip_size_mb)

        upload_file_to_s3(str(zip_path), s3_key)

    return {
        "status": "ok",
        "s3_key": s3_key,
        "size_mb": round(zip_size_mb, 2),
    }


@app.post("/agent/servers/{server_id}/restore")
def restore_server_from_s3(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Download the world zip from S3 and extract into the server directory."""
    import zipfile
    from blockhost_backend.services.s3_storage import (
        s3_key_for_server,
        download_file_from_s3,
        s3_key_exists,
    )

    _validate_server_id(server_id)
    server_dir = SERVERS_ROOT_DIR / server_id
    s3_key = s3_key_for_server(server_id)

    if not s3_key_exists(s3_key):
        raise HTTPException(status_code=404, detail="No S3 backup found for this server")

    with tempfile.TemporaryDirectory(prefix="blockhost-restore-") as td:
        zip_path = Path(td) / "world.zip"
        download_file_from_s3(s3_key, str(zip_path))

        # Wipe existing world data but keep the directory
        if server_dir.exists():
            shutil.rmtree(server_dir, ignore_errors=True)
        server_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                # Security: block path traversal
                if info.filename.startswith(("/", "\\")) or ".." in info.filename:
                    logger.warning("Blocked path traversal in restore zip: %s", info.filename)
                    continue
                zf.extract(info, server_dir)

    logger.info("Restore complete for %s from s3://%s", server_id, s3_key)
    return {"status": "ok", "s3_key": s3_key}


# ---------------------------------------------------------------------------
# File Management
# ---------------------------------------------------------------------------

ALLOWED_ROOTS = {
    "world",
    "world_nether",
    "world_the_end",
    "worlds",
    "mods",
    "plugins",
    "config",
    "behavior_packs",
    "resource_packs",
    "skin_packs",
    "world_templates",
    "development_behavior_packs",
    "development_resource_packs",
    "development_skin_packs",
}
ALLOWED_ROOT_FILES = {
    "server.properties",
    "allowlist.json",
    "permissions.json",
    "valid_known_packs.json",
    "world_behavior_packs.json",
    "world_resource_packs.json",
    "eula.txt",
    "ops.json",
    "whitelist.json",
    "banned-players.json",
    "banned-ips.json",
}
MAX_EDIT_SIZE = 5 * 1024 * 1024
MAX_UPLOAD_SIZE = 100 * 1024 * 1024


def _parse_allowed_csv(raw: str | None, allowed_values: set[str]) -> set[str]:
    if not raw:
        return set(allowed_values)
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    return requested & allowed_values


def _parse_relative_parts(requested_path: str) -> tuple[str, ...]:
    clean = requested_path.strip().lstrip("/")
    if not clean:
        return ()
    parts = tuple(part for part in clean.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=403, detail="Path traversal detected")
    return parts


def _validate_safe_path(
    server_root: Path,
    requested_path: str,
    *,
    allowed_roots: set[str] | None = None,
    allowed_files: set[str] | None = None,
    allow_root: bool = False,
    allow_root_file: bool = True,
) -> Path:
    roots = allowed_roots or set(ALLOWED_ROOTS)
    files = allowed_files or set(ALLOWED_ROOT_FILES)
    parts = _parse_relative_parts(requested_path)
    if not parts:
        if not allow_root:
            raise HTTPException(status_code=403, detail="Access denied to this folder")
    elif len(parts) == 1 and parts[0] in files:
        if not allow_root_file:
            raise HTTPException(status_code=403, detail="Access denied to this folder")
    elif parts[0] not in roots:
        raise HTTPException(status_code=403, detail="Access denied to this folder")

    requested_path = "/".join(parts)
    resolved = (server_root / requested_path).resolve()

    if not resolved.is_relative_to(server_root):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    return resolved


@app.get("/agent/servers/{server_id}/files/list")
def agent_list_files(
    server_id: str,
    path: str = "",
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)

    if not server_root.exists():
        return []

    if not path or path.strip() == "/":
        results = []
        for folder in sorted(roots):
            folder_path = server_root / folder
            if folder_path.is_dir():
                results.append(
                    {
                        "name": folder,
                        "path": folder,
                        "is_dir": True,
                        "size": None,
                        "last_modified": folder_path.stat().st_mtime,
                    }
                )
        for filename in sorted(files):
            file_path = server_root / filename
            if file_path.is_file():
                stat = file_path.stat()
                results.append(
                    {
                        "name": filename,
                        "path": filename,
                        "is_dir": False,
                        "size": stat.st_size,
                        "last_modified": stat.st_mtime,
                    }
                )
        return results

    target_dir = _validate_safe_path(server_root, path, allowed_roots=roots, allowed_files=files)
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    results = []
    try:
        for item in target_dir.iterdir():
            is_dir = item.is_dir()
            try:
                st = item.stat()
                results.append({
                    "name": item.name,
                    "path": item.relative_to(server_root).as_posix(),
                    "is_dir": is_dir,
                    "size": None if is_dir else st.st_size,
                    "last_modified": st.st_mtime,
                })
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    results.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return results


@app.get("/agent/servers/{server_id}/files/read")
def agent_read_file(
    server_id: str,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)
    target_file = _validate_safe_path(server_root, path, allowed_roots=roots, allowed_files=files)

    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target_file.stat().st_size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="File too large to edit")

    try:
        return {"content": target_file.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")


@app.put("/agent/servers/{server_id}/files/write")
def agent_write_file(
    server_id: str,
    payload: WritePayload,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)
    target_file = _validate_safe_path(server_root, path, allowed_roots=roots, allowed_files=files)

    content_bytes = payload.content.encode("utf-8")
    if len(content_bytes) > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail="Content too large")

    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=target_file.parent, suffix=".tmp")
        try:
            os.write(fd, content_bytes)
            os.close(fd)
            fd = -1
            os.replace(tmp_path, target_file)
        except BaseException:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")

    return {"status": "ok"}


@app.get("/agent/servers/{server_id}/files/download")
def agent_download_file(
    server_id: str,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    _token: str = Depends(verify_token),
) -> FileResponse:
    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)
    target_file = _validate_safe_path(server_root, path, allowed_roots=roots, allowed_files=files)

    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        target_file,
        filename=target_file.name,
        content_disposition_type="attachment",
    )


def _get_dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += _get_dir_size(Path(entry.path))
    except OSError:
        pass
    return total


@app.post("/agent/servers/{server_id}/files/upload")
async def agent_upload_file(
    server_id: str,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    storage_limit_mb: int | None = Query(default=None),
    file: UploadFile = File(...),
    _token: str = Depends(verify_token),
) -> Any:
    if storage_limit_mb is None:
        raise HTTPException(status_code=403, detail="No active subscription: storage limit is undefined.")

    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)
    target_dir = _validate_safe_path(
        server_root,
        path,
        allowed_roots=roots,
        allowed_files=files,
        allow_root_file=False,
    )

    current_dir_size = _get_dir_size(server_root) if server_root.exists() else 0
    limit_bytes = storage_limit_mb * 1024 * 1024

    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target is not a directory")
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = os.path.basename(file.filename or "uploaded_file")
    filename = filename.lstrip(".")
    if not filename or filename in (".", ".."):
        filename = "uploaded_file"

    target_file = target_dir / filename
    _validate_safe_path(server_root, target_file.relative_to(server_root).as_posix(), allowed_roots=roots, allowed_files=files)

    if target_file.is_file():
        current_dir_size -= target_file.stat().st_size

    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".upload")
    try:
        total_size = 0
        with os.fdopen(fd, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if (current_dir_size + total_size) > limit_bytes:
                    raise HTTPException(status_code=413, detail="Disk quota exceeded for this server.")
                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="File too large")
                buffer.write(chunk)
        os.replace(tmp_path, target_file)
    except (HTTPException, Exception) as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    return {"status": "ok", "filename": filename, "size": total_size}


@app.delete("/agent/servers/{server_id}/files/delete")
def agent_delete_file(
    server_id: str,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    server_root = SERVERS_ROOT_DIR / server_id
    roots = _parse_allowed_csv(allowed_roots, ALLOWED_ROOTS)
    files = _parse_allowed_csv(allowed_files, ALLOWED_ROOT_FILES)
    target = _validate_safe_path(server_root, path, allowed_roots=roots, allowed_files=files)

    for allowed in roots:
        if target == (server_root / allowed).resolve():
            raise HTTPException(status_code=403, detail="Cannot delete root folders")
    if target.parent == server_root and target.name in files:
        raise HTTPException(status_code=403, detail="Cannot delete protected server config files")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Mod / Plugin Management (Modrinth)
# ---------------------------------------------------------------------------

# Only these subdirectories are valid install targets.
# This is a hard security allowlist — prevents path traversal even if
# the Backend sends a crafted subdir value.
_ALLOWED_MOD_SUBDIRS: frozenset[str] = frozenset({"mods", "plugins"})

# Maximum size of a single mod JAR to accept (500 MB).
# Configurable via environment variable MOD_MAX_SIZE_BYTES.
_MOD_MAX_SIZE_BYTES: int = int(
    os.environ.get("MOD_MAX_SIZE_BYTES", str(500 * 1024 * 1024))
)

# Only allow downloads from the official Modrinth CDN (SSRF guard).
_ALLOWED_MOD_URL_PREFIX = "https://cdn.modrinth.com/"


class _ModFile(BaseModel):
    url: str
    filename: str
    subdir: str  # validated against _ALLOWED_MOD_SUBDIRS


class _ModDownloadRequest(BaseModel):
    files: list[_ModFile]


def _validate_mod_subdir(subdir: str) -> str:
    if subdir not in _ALLOWED_MOD_SUBDIRS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid subdir {subdir!r}. Allowed: {sorted(_ALLOWED_MOD_SUBDIRS)}",
        )
    return subdir


def _validate_mod_filename(filename: str) -> str:
    """Ensure the filename is a plain .jar with no path components."""
    safe = Path(filename).name  # strips any directory components
    if safe != filename:
        raise HTTPException(status_code=400, detail="Filename must not contain path separators")
    if not safe.endswith(".jar"):
        raise HTTPException(status_code=400, detail="Only .jar files are permitted")
    # Block hidden files and names starting with a dot
    if safe.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe


def _validate_mod_url(url: str) -> str:
    """Block non-CDN URLs to prevent SSRF attacks."""
    if not url.startswith(_ALLOWED_MOD_URL_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=f"URL must start with {_ALLOWED_MOD_URL_PREFIX!r} (SSRF guard)",
        )
    return url


@app.post("/agent/servers/{server_id}/mods/download")
async def agent_download_mods(
    server_id: str,
    payload: _ModDownloadRequest,
    _token: str = Depends(verify_token),
) -> Any:
    """
    Download a list of mod/plugin JARs directly from Modrinth CDN to disk.

    This is the "muscle" endpoint. The Backend resolves which files are needed;
    this endpoint does the actual fetching. Files are streamed chunk-by-chunk
    so the process never loads a full JAR into RAM regardless of file size.

    Security controls:
      - Token-gated (agent bearer token).
      - URL must start with https://cdn.modrinth.com/ (SSRF guard).
      - subdir must be in {"mods", "plugins"} (path traversal guard).
      - filename must be a plain *.jar with no path separators.
      - File size is capped at _MOD_MAX_SIZE_BYTES (default 500 MB).
    """
    _validate_server_id(server_id)

    if not payload.files:
        return {"status": "ok", "downloaded": 0}

    # Pre-validate all entries before touching the network
    for mod_file in payload.files:
        _validate_mod_url(mod_file.url)
        _validate_mod_filename(mod_file.filename)
        _validate_mod_subdir(mod_file.subdir)

    downloaded = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "BlockHostAgent/1.0 (https://blockhost.app)"},
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=5.0),
        follow_redirects=True,
    ) as client:
        for mod_file in payload.files:
            dest_dir = SERVERS_ROOT_DIR / server_id / mod_file.subdir
            # Path traversal double-check after joining
            if not dest_dir.resolve().is_relative_to(SERVERS_ROOT_DIR):
                raise HTTPException(status_code=403, detail="Path traversal detected")
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_path = dest_dir / mod_file.filename
            # Final path traversal check on the full destination
            if not dest_path.resolve().is_relative_to(dest_dir.resolve()):
                raise HTTPException(status_code=403, detail="Path traversal detected in filename")

            logger.info(
                "Downloading mod %s from %s → %s/%s",
                mod_file.filename, mod_file.url, mod_file.subdir, mod_file.filename,
            )

            total_bytes = 0
            try:
                async with client.stream("GET", mod_file.url) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(1024 * 1024):  # 1 MB chunks
                            total_bytes += len(chunk)
                            if total_bytes > _MOD_MAX_SIZE_BYTES:
                                # Truncate and abort — remove partial file
                                f.close()
                                dest_path.unlink(missing_ok=True)
                                raise HTTPException(
                                    status_code=413,
                                    detail=(
                                        f"Mod {mod_file.filename!r} exceeds size limit "
                                        f"({_MOD_MAX_SIZE_BYTES // (1024*1024)} MB)"
                                    ),
                                )
                            f.write(chunk)
            except httpx.HTTPStatusError as exc:
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"CDN returned {exc.response.status_code} "
                        f"for {mod_file.filename!r}"
                    ),
                )
            except httpx.HTTPError as exc:
                logger.error("HTTPError downloading %s from %s: %s", mod_file.filename, mod_file.url, exc)
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=502,
                    detail=f"Network error downloading {mod_file.filename!r}: {exc}",
                )

            downloaded += 1
            logger.info("Saved %s (%d bytes)", mod_file.filename, total_bytes)

    return {"status": "ok", "downloaded": downloaded}


@app.get("/agent/servers/{server_id}/mods")
def agent_list_mods(
    server_id: str,
    subdir: str = Query(default="mods"),
    _token: str = Depends(verify_token),
) -> Any:
    """
    List all .jar files in the server's mods/ or plugins/ directory.

    Returns filenames only (not full paths). The Backend uses this both
    to display the installed mods list and to check for already-installed
    files before downloading (dedup).
    """
    _validate_server_id(server_id)
    _validate_mod_subdir(subdir)

    mods_dir = SERVERS_ROOT_DIR / server_id / subdir
    if not mods_dir.exists():
        return {"files": []}

    files = sorted(
        entry.name
        for entry in mods_dir.iterdir()
        if entry.is_file() and entry.suffix == ".jar" and not entry.name.startswith(".")
    )
    return {"files": files}


@app.delete("/agent/servers/{server_id}/mods/{filename}")
def agent_delete_mod(
    server_id: str,
    filename: str,
    subdir: str = Query(default="mods"),
    _token: str = Depends(verify_token),
) -> Any:
    """
    Delete a single .jar from the server's mods/ or plugins/ directory.

    Validates subdir and filename for path traversal before deleting.
    """
    _validate_server_id(server_id)
    _validate_mod_subdir(subdir)
    safe_filename = _validate_mod_filename(filename)

    target = SERVERS_ROOT_DIR / server_id / subdir / safe_filename
    # Path traversal final check
    if not target.resolve().is_relative_to((SERVERS_ROOT_DIR / server_id / subdir).resolve()):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Mod {safe_filename!r} not found")

    target.unlink()
    logger.info("Deleted mod %s/%s for server %s", subdir, safe_filename, server_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Deployment Lifecycle (Docker-based PaaS)
# ---------------------------------------------------------------------------

_build_status: dict[str, str] = {}
_build_results: dict[str, dict[str, str]] = {}

class BuildGithubPayload(BaseModel):
    repo_url: str
    branch: str = "main"
    deployment_kind: str = "dockerfile"
    internal_port: int = 8080
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    runtime_version: str | None = None

def _run_github_build(deployment_id: str, payload: BuildGithubPayload) -> None:
    try:
        _build_status[deployment_id] = "building"
        _build_results.pop(deployment_id, None)
        image_tag = docker_runtime.build_from_github(
            payload.repo_url,
            payload.branch,
            deployment_id,
            deployment_kind=payload.deployment_kind,
            internal_port=payload.internal_port,
            install_command=payload.install_command,
            build_command=payload.build_command,
            start_command=payload.start_command,
            output_directory=payload.output_directory,
            runtime_version=payload.runtime_version,
        )
        _build_results[deployment_id] = {"image_tag": image_tag}
        _build_status[deployment_id] = "success"
    except Exception as exc:
        logger.error("GitHub build failed for %s: %s", deployment_id, exc)
        _build_results[deployment_id] = {"error": str(exc)}
        _build_status[deployment_id] = "error"

@app.post("/agent/deployments/{deployment_id}/build-github")
def build_github(
    deployment_id: str,
    payload: BuildGithubPayload,
    background_tasks: BackgroundTasks,
    _token: str = Depends(verify_token),
) -> Any:
    """Clone a GitHub repository and build a Docker image in the background."""
    _validate_server_id(deployment_id)
    background_tasks.add_task(_run_github_build, deployment_id, payload)
    return {"status": "accepted"}

@app.get("/agent/deployments/{deployment_id}/build-status")
def get_build_status(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Return the current GitHub build status."""
    _validate_server_id(deployment_id)
    status = _build_status.get(deployment_id, "unknown")
    return {"status": status, **_build_results.get(deployment_id, {})}



@app.post("/agent/deployments/{deployment_id}/start")
def start_deployment(
    deployment_id: str,
    payload: DeploymentStartPayload,
    _token: str = Depends(verify_token),
) -> Any:
    """Pull image and start a Docker container for the given deployment."""
    _validate_server_id(deployment_id)  # reuse UUID validation
    
    # Securely derive host volume path from deployment_id
    safe_volume_path = None
    if payload.volume_mount_path:
        dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
        # Prevent path traversal
        if not dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()):
            raise HTTPException(status_code=403, detail="Path traversal detected")
        dep_dir.mkdir(parents=True, exist_ok=True)
        safe_volume_path = str(dep_dir)
        
    try:
        result = docker_runtime.start_deployment(
            deployment_id=deployment_id,
            docker_image=payload.docker_image,
            internal_port=payload.internal_port,
            env_vars=payload.env_vars,
            volume_path=safe_volume_path,
            volume_mount_path=payload.volume_mount_path,
            ram_limit_mb=payload.ram_limit_mb,
            cpu_limit=payload.cpu_limit,
            storage_limit_mb=payload.storage_limit_mb,
            project_id=payload.project_id,
            health_check_path=payload.health_check_path,
            command=payload.command,
            network_aliases=payload.network_aliases,
        )
        return {
            "status": "running",
            "container_id": result.container_id,
            "container_name": result.container_name,
            "host_port": result.host_port,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/agent/deployments/{deployment_id}/stop")
def stop_deployment(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Stop and remove the Docker container for the given deployment."""
    _validate_server_id(deployment_id)
    docker_runtime.stop_deployment(deployment_id)
    _deployment_health.pop(deployment_id, None)
    return {"status": "ok"}


@app.post("/agent/deployments/{deployment_id}/delete-volume")
def delete_deployment_volume(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Delete the persistent volume directory for a deployment."""
    _validate_server_id(deployment_id)
    dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
    if dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()) and dep_dir.exists():
        import shutil
        shutil.rmtree(dep_dir, ignore_errors=True)
        logger.info("Deleted volume for deployment %s at %s", deployment_id, dep_dir)
    return {"status": "ok"}


@app.get("/agent/deployments/{deployment_id}/volume-size")
def get_deployment_volume_size(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Get the size of the persistent volume in bytes."""
    _validate_server_id(deployment_id)
    dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
    if not dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()) or not dep_dir.exists():
        return {"size_bytes": 0}
        
    import subprocess
    try:
        result = subprocess.run(["du", "-sb", str(dep_dir)], capture_output=True, text=True, check=True)
        bytes_str = result.stdout.split()[0]
        return {"size_bytes": int(bytes_str)}
    except Exception as e:
        logger.warning("Failed to get volume size for %s: %s", deployment_id, e)
        return {"size_bytes": 0}


class VolumeSnapshotPayload(BaseModel):
    s3_key: str

@app.post("/agent/deployments/{deployment_id}/volume-snapshot")
def create_deployment_volume_snapshot(
    deployment_id: str,
    payload: VolumeSnapshotPayload,
    _token: str = Depends(verify_token),
) -> Any:
    """Create a tar.gz snapshot of the volume and upload to S3."""
    _validate_server_id(deployment_id)
    dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
    if not dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()) or not dep_dir.exists():
        raise HTTPException(status_code=404, detail="Volume directory not found")

    import subprocess
    import tempfile
    from blockhost_backend.services.s3_storage import upload_file_to_s3

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tar_path = tmp.name

    try:
        subprocess.run(["tar", "-czf", tar_path, "-C", str(dep_dir), "."], check=True, capture_output=True)
        upload_file_to_s3(tar_path, payload.s3_key)
        return {"status": "success", "s3_key": payload.s3_key}
    except subprocess.CalledProcessError as e:
        logger.error("Failed to tar volume %s: %s", deployment_id, e.stderr)
        raise HTTPException(status_code=500, detail="Failed to create snapshot archive")
    except Exception as e:
        logger.error("Failed to upload volume snapshot to S3: %s", e)
        raise HTTPException(status_code=500, detail="Failed to upload snapshot to S3")
    finally:
        if os.path.exists(tar_path):
            os.remove(tar_path)


class VolumeRestorePayload(BaseModel):
    s3_key: str

@app.post("/agent/deployments/{deployment_id}/volume-restore")
def restore_deployment_volume_snapshot(
    deployment_id: str,
    payload: VolumeRestorePayload,
    _token: str = Depends(verify_token),
) -> Any:
    """Download a tar.gz snapshot from S3 and extract it into the volume."""
    _validate_server_id(deployment_id)
    dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
    if not dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Path traversal detected")
        
    dep_dir.mkdir(parents=True, exist_ok=True)

    import subprocess
    import tempfile
    import urllib.request
    from blockhost_backend.services.s3_storage import get_s3_presigned_url

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        tar_path = tmp.name

    try:
        # Get presigned URL for download
        url = get_s3_presigned_url(payload.s3_key, expiration=3600)
        urllib.request.urlretrieve(url, tar_path)
        
        # Clear existing contents before extraction
        import shutil
        for item in os.listdir(dep_dir):
            item_path = os.path.join(dep_dir, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
                
        # Extract archive
        subprocess.run(["tar", "-xzf", tar_path, "-C", str(dep_dir)], check=True, capture_output=True)
        return {"status": "success"}
    except Exception as e:
        logger.error("Failed to restore volume snapshot from %s: %s", payload.s3_key, e)
        raise HTTPException(status_code=500, detail="Failed to restore volume snapshot")
    finally:
        if os.path.exists(tar_path):
            os.remove(tar_path)


@app.get("/agent/deployments/{deployment_id}/status")
def get_deployment_status(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Return the current status of a deployment container."""
    _validate_server_id(deployment_id)
    status = docker_runtime.get_status(deployment_id)
    return {
        "running": status.running,
        "container_id": status.container_id,
        "state": status.state,
        "host_port": status.host_port,
        "started_at": status.started_at,
        "exit_code": status.exit_code,
    }


@app.get("/agent/deployments/{deployment_id}/network-rx")
def get_deployment_network_rx(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    """Return the current network rx bytes of a deployment container."""
    _validate_server_id(deployment_id)
    rx_bytes = docker_runtime.get_network_rx_bytes(deployment_id)
    return {"rx_bytes": rx_bytes}


@app.get("/agent/deployments/{deployment_id}/logs")
def get_deployment_logs(
    deployment_id: str,
    tail: int = Query(default=200, ge=1, le=5000),
    _token: str = Depends(verify_token),
) -> Any:
    """Return the last N log lines from the deployment container."""
    _validate_server_id(deployment_id)
    lines = docker_runtime.get_logs(deployment_id, tail=tail)
    return {"lines": lines}


@app.websocket("/agent/deployments/{deployment_id}/logs/ws")
async def ws_deployment_logs(
    websocket: WebSocket,
    deployment_id: str,
    token: str = Query(...),
):
    """Stream live logs from the deployment container."""
    if token not in {AGENT_TOKEN, CONTROL_AGENT_TOKEN}:
        await websocket.close(code=1008, reason="Invalid token")
        return

    _validate_server_id(deployment_id)
    container = docker_runtime._find_active_container(deployment_id)
    if not container:
        await websocket.close(code=4004, reason="Container not found or not running")
        return

    await websocket.accept()

    # Send history first
    try:
        lines = docker_runtime.get_logs(deployment_id, tail=200)
        history_logs = [{"line": line} for line in lines]
        await websocket.send_json({"type": "history", "logs": history_logs})
    except Exception as e:
        logger.warning(f"Failed to fetch log history: {e}")

    queue = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_running_loop()

    def _tail_logs():
        try:
            for line in container.logs(stream=True, tail=0, timestamps=True):
                decoded = line.decode("utf-8", errors="replace").strip()
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, {"line": decoded})
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        loop.call_soon_threadsafe(queue.put_nowait, {"line": decoded})
                    except asyncio.QueueEmpty:
                        pass
        except Exception as e:
            logger.warning(f"Streaming logs failed for {deployment_id}: {e}")

    import threading
    t = threading.Thread(target=_tail_logs, daemon=True)
    t.start()

    try:
        while True:
            log_entry = await queue.get()
            await websocket.send_json({"type": "log", "log": log_entry})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for {deployment_id}: {e}")


@app.get("/agent/deployments/{deployment_id}/metrics")
def get_deployment_metrics(
    deployment_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(deployment_id)
    metrics = docker_runtime.get_metrics(deployment_id)
    
    # Calculate disk usage
    disk_usage_mb = 0
    dep_dir = (DEPLOYMENTS_ROOT_DIR / deployment_id).resolve()
    if dep_dir.is_relative_to(DEPLOYMENTS_ROOT_DIR.resolve()) and dep_dir.exists():
        import subprocess
        try:
            result = subprocess.run(["du", "-sb", str(dep_dir)], capture_output=True, text=True, check=True)
            bytes_str = result.stdout.split()[0]
            disk_usage_mb = int(bytes_str) // (1024 * 1024)
        except Exception as e:
            logger.warning("Failed to get disk usage for %s: %s", deployment_id, e)
    
    metrics["disk_usage_mb"] = disk_usage_mb
    return metrics


DATABASES_ROOT_DIR = Path(
    os.environ.get("DATABASES_ROOT_DIR", str(SERVERS_ROOT_DIR.parent / "databases"))
).resolve()
DATABASES_ROOT_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/agent/databases/{db_id}/start")
def start_database(
    db_id: str,
    payload: DatabaseStartPayload,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(db_id)
    safe_volume_path = None
    if payload.volume_mount_path:
        db_dir = (DATABASES_ROOT_DIR / db_id).resolve()
        if not db_dir.is_relative_to(DATABASES_ROOT_DIR.resolve()):
            raise HTTPException(status_code=403, detail="Path traversal detected")
        db_dir.mkdir(parents=True, exist_ok=True)
        safe_volume_path = str(db_dir)
        
    try:
        result = docker_runtime.start_deployment(
            deployment_id=db_id,
            docker_image=payload.docker_image,
            internal_port=payload.internal_port,
            env_vars=payload.env_vars,
            volume_path=safe_volume_path,
            volume_mount_path=payload.volume_mount_path,
            ram_limit_mb=payload.ram_limit_mb,
            cpu_limit=payload.cpu_limit,
            storage_limit_mb=payload.storage_limit_mb,
            project_id=payload.project_id,
            command=payload.command,
            network_aliases=payload.network_aliases,
            is_database=True,
        )
        return {
            "status": "running",
            "container_id": result.container_id,
            "container_name": result.container_name,
            "host_port": result.host_port,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/agent/databases/{db_id}/stop")
def stop_database(db_id: str, _token: str = Depends(verify_token)) -> Any:
    _validate_server_id(db_id)
    docker_runtime.stop_deployment(db_id)
    return {"status": "ok"}

@app.delete("/agent/databases/{db_id}/delete")
def delete_database(db_id: str, _token: str = Depends(verify_token)) -> Any:
    _validate_server_id(db_id)
    docker_runtime.stop_deployment(db_id)
    db_dir = (DATABASES_ROOT_DIR / db_id).resolve()
    if db_dir.is_relative_to(DATABASES_ROOT_DIR.resolve()) and db_dir.exists():
        import shutil
        shutil.rmtree(db_dir, ignore_errors=True)
    return {"status": "ok"}

@app.post("/agent/databases/{db_id}/backup")
def backup_database(db_id: str, payload: DatabaseBackupPayload, _token: str = Depends(verify_token)) -> Any:
    _validate_server_id(db_id)
    import tempfile
    from blockhost_backend.services.s3_storage import upload_file_to_s3
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        local_path = tmp.name
        
    try:
        if payload.engine in ("postgres", "postgresql"):
            cmd = ["pg_dump", f"postgresql://{payload.username}:{payload.password}@127.0.0.1/{payload.database}"]
        elif payload.engine in ("mysql", "mariadb"):
            cmd = ["mysqldump", "-u", payload.username, f"-p{payload.password}", payload.database]
        elif payload.engine == "redis":
            cmd = ["redis-cli", "-a", payload.password, "--rdb", "/dev/stdout"] if payload.password else ["redis-cli", "--rdb", "/dev/stdout"]
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported engine: {payload.engine}")

        logger.info("Starting backup for %s (%s)", db_id, payload.engine)
        exit_code = docker_runtime.run_docker_exec(db_id, cmd, stdout_file=local_path)
        if exit_code != 0:
            raise RuntimeError(f"Backup command failed with exit code {exit_code}")
            
        logger.info("Uploading backup to %s", payload.s3_key)
        upload_file_to_s3(local_path, payload.s3_key)
        return {"status": "ok"}
    except Exception as e:
        logger.error("Backup failed for %s: %s", db_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        import os
        if os.path.exists(local_path):
            os.remove(local_path)

@app.post("/agent/databases/{db_id}/restore")
def restore_database(db_id: str, payload: DatabaseRestorePayload, _token: str = Depends(verify_token)) -> Any:
    _validate_server_id(db_id)
    import tempfile
    from blockhost_backend.services.s3_storage import download_file_from_s3
    
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        local_path = tmp.name
        
    try:
        logger.info("Downloading backup from %s", payload.s3_key)
        download_file_from_s3(payload.s3_key, local_path)
        
        if payload.engine in ("postgres", "postgresql"):
            cmd = ["psql", f"postgresql://{payload.username}:{payload.password}@127.0.0.1/{payload.database}"]
        elif payload.engine in ("mysql", "mariadb"):
            cmd = ["mysql", "-u", payload.username, f"-p{payload.password}", payload.database]
        elif payload.engine == "redis":
            raise HTTPException(status_code=400, detail="Redis restore must be done manually via volume replace for now.")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported engine: {payload.engine}")

        logger.info("Restoring backup for %s (%s)", db_id, payload.engine)
        exit_code = docker_runtime.run_docker_exec(db_id, cmd, stdin_file=local_path)
        if exit_code != 0:
            raise RuntimeError(f"Restore command failed with exit code {exit_code}")
            
        return {"status": "ok"}
    except Exception as e:
        logger.error("Restore failed for %s: %s", db_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        import os
        if os.path.exists(local_path):
            os.remove(local_path)



# ---------------------------------------------------------------------------
# Controller Heartbeat
# ---------------------------------------------------------------------------


async def _discover_public_ip() -> str:
    """Attempt to discover the public IP via AWS IMDS or a public API."""
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            # Try AWS IMDSv2 first (fastest on EC2)
            token_resp = await client.put(
                "http://169.254.169.254/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"}
            )
            if token_resp.status_code == 200:
                token = token_resp.text
                ip_resp = await client.get(
                    "http://169.254.169.254/latest/meta-data/public-ipv4",
                    headers={"X-aws-ec2-metadata-token": token}
                )
                if ip_resp.status_code == 200:
                    return ip_resp.text.strip()
        except Exception:
            pass
        
        # Fallback to ipify for non-AWS environments
        try:
            resp = await client.get("https://api.ipify.org")
            if resp.status_code == 200:
                return resp.text.strip()
        except Exception:
            pass
            
    return ""


async def heartbeat_task() -> None:
    global NODE_PUBLIC_IP
    if not NODE_PUBLIC_IP:
        discovered_ip = await _discover_public_ip()
        if discovered_ip:
            logger.info("Dynamically discovered public IP: %s", discovered_ip)
            NODE_PUBLIC_IP = discovered_ip
        else:
            logger.warning("Failed to dynamically discover public IP. Will rely on Control Plane resolution.")

    retry_delay = 1.0
    max_retry_delay = 30.0
    ws_url = CONTROLLER_WS_URL
    separator = "&" if "?" in ws_url else "?"
    ws_url = f"{ws_url}{separator}token={AGENT_TOKEN}"

    while True:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=60,
                close_timeout=5,
            ) as ws:
                await ws.send(
                    json.dumps({
                        "type": "register",
                        "data": {
                            "name": NODE_NAME,
                            "agent_port": AGENT_PORT,
                            **({"ip_address": NODE_PUBLIC_IP} if NODE_PUBLIC_IP else {}),
                        },
                    })
                )
                logger.info("Connected to Controller at %s", CONTROLLER_WS_URL)

                while True:
                    await asyncio.sleep(5)
                    ram = psutil.virtual_memory()
                    cpu = psutil.cpu_percent(interval=1)
                    await ws.send(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "data": {
                                    "total_ram_mb": ram.total // (1024 * 1024),
                                    "used_ram_mb": ram.used // (1024 * 1024),
                                    "cpu_usage_percent": cpu,
                                    "cpu_cores": psutil.cpu_count(),
                                    "running_servers": runtime.get_all_running_player_counts(),
                                    "running_deployments": docker_runtime.list_managed_containers(),
                                    "deployment_health": dict(_deployment_health),
                                },
                            }
                        )
                    )
                    retry_delay = 1.0

        except asyncio.CancelledError:
            logger.info("Heartbeat task cancelled")
            break
        except Exception as e:
            logger.warning(
                "Disconnected from Controller: %s. Retrying in %.1fs...",
                e,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    task = asyncio.create_task(heartbeat_task())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    health_task = asyncio.create_task(_health_check_loop())
    _background_tasks.add(health_task)
    health_task.add_done_callback(_background_tasks.discard)

    prune_task = asyncio.create_task(_docker_prune_loop())
    _background_tasks.add(prune_task)
    prune_task.add_done_callback(_background_tasks.discard)

    yield

    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()
    logger.info("Agent shutdown complete")


app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT)
