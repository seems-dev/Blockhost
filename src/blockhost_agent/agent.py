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

_BINARY_NAMES = ("bedrock_server", "bedrock_server.exe", "bedrock_server_symbols.debug")
_SO_RE = re.compile(r"^lib.*\.so(?:\..*)?$")

# Queue limits to prevent unbounded memory growth
_LOG_STREAM_QUEUE_SIZE = 1000

# Shared systemd runtime instance for this agent
runtime = SystemdRuntime(servers_root=SERVERS_ROOT_DIR)

# Track background tasks for graceful shutdown
_background_tasks: set[asyncio.Task] = set()


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


class CommandPayload(BaseModel):
    command: str


class WritePayload(BaseModel):
    content: str


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
    is_java = bool(payload.jar_download_url) or (payload.executable_path and payload.executable_path.endswith(".jar"))

    if payload.server_properties_dict is not None:
        props_path = server_dir / "server.properties"
        lines = []
        for k, v in payload.server_properties_dict.items():
            v_str = "true" if v is True else "false" if v is False else str(v)
            lines.append(f"{k}={v_str}")
        props_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if is_java:
        (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        if payload.jar_download_url:
            version_str = payload.requested_version or "latest"
            jar_path = server_dir / f"server-{version_str}.jar"
            if not jar_path.exists():
                logger.info(f"Downloading Java jar from {payload.jar_download_url} to {jar_path}")
                try:
                    with httpx.Client(follow_redirects=True, timeout=300.0) as client:
                        with client.stream("GET", payload.jar_download_url) as resp:
                            resp.raise_for_status()
                            with open(jar_path, "wb") as f:
                                for chunk in resp.iter_bytes(1024*1024):
                                    f.write(chunk)
                except Exception as e:
                    if jar_path.exists():
                        jar_path.unlink()
                    raise HTTPException(status_code=500, detail=f"Failed to download jar: {e}")
            executable_path = jar_path
        else:
            executable_path = Path(payload.executable_path) if payload.executable_path else None
            if not executable_path or not executable_path.is_file():
                raise HTTPException(status_code=404, detail="Java executable not found on agent")
    else:
        version_name = (payload.requested_version or "").strip()
        if not version_name:
            raise HTTPException(status_code=400, detail="requested_version is required on agent")
        _ensure_server_layout(server_id, version_name)
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
        requested_version=payload.requested_version,
        executable_path=executable_path,
        ram_mb=payload.ram_mb,
        cpu_quota_pct=payload.cpu_quota_pct,
        jdk_path=Path(payload.jdk_path) if payload.jdk_path else None, # NEW
    )
    result = runtime.start_server(req)
    return {
        "runtime_id": result.runtime_id,
        "port": result.port,
        "actual_version": result.actual_version,
    }


@app.post("/agent/servers/{server_id}/stop")
def stop_server(
    server_id: str,
    _token: str = Depends(verify_token),
) -> Any:
    _validate_server_id(server_id)
    runtime.stop_server(server_id)
    return {"status": "ok"}


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


def _ensure_server_layout(server_id: str, version_name: str) -> Path:
    """Link static version assets into the per-server dir; preserve worlds/config."""
    version_dir = VERSIONS_ROOT_DIR / version_name
    if not version_dir.is_dir():
        raise HTTPException(
            status_code=503,
            detail=f"Version {version_name!r} not provisioned on this node",
        )

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

    return server_dir


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


@app.post("/agent/servers/{server_id}/files/upload")
async def agent_upload_file(
    server_id: str,
    path: str,
    allowed_roots: str | None = None,
    allowed_files: str | None = None,
    file: UploadFile = File(...),
    _token: str = Depends(verify_token),
) -> Any:
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

    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target is not a directory")
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = os.path.basename(file.filename or "uploaded_file")
    filename = filename.lstrip(".")
    if not filename or filename in (".", ".."):
        filename = "uploaded_file"

    target_file = target_dir / filename
    _validate_safe_path(server_root, target_file.relative_to(server_root).as_posix(), allowed_roots=roots, allowed_files=files)

    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".upload")
    try:
        total_size = 0
        with os.fdopen(fd, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
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
# Controller Heartbeat
# ---------------------------------------------------------------------------


async def heartbeat_task() -> None:
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
