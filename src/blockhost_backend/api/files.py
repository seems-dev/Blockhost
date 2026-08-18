import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user, upload_rate_limit
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, User, Node
from blockhost_backend.api.schemas import FileInfo, FileWriteRequest
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.minecraft.java_compat import is_java_flavor
from blockhost_backend.services.system_health import (
    DiskProtectionError,
    WorldSizeLimitError,
    assert_disk_usage_safe,
    assert_world_size_within_plan,
)

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])

@dataclass(frozen=True)
class FileAccessPolicy:
    roots: frozenset[str]
    root_files: frozenset[str]


BEDROCK_FILE_POLICY = FileAccessPolicy(
    roots=frozenset(
        {
            "worlds",
            "behavior_packs",
            "resource_packs",
            "skin_packs",
            "world_templates",
            "development_behavior_packs",
            "development_resource_packs",
            "development_skin_packs",
        }
    ),
    root_files=frozenset(
        {
            "server.properties",
            "allowlist.json",
            "permissions.json",
            "valid_known_packs.json",
            "world_behavior_packs.json",
            "world_resource_packs.json",
        }
    ),
)

JAVA_FILE_POLICY = FileAccessPolicy(
    roots=frozenset(
        {
            "world",
            "world_nether",
            "world_the_end",
            "worlds",
            "mods",
            "plugins",
            "config",
        }
    ),
    root_files=frozenset(
        {
            "server.properties",
            "eula.txt",
            "ops.json",
            "whitelist.json",
            "banned-players.json",
            "banned-ips.json",
        }
    ),
)

TEXT_EXTENSIONS = {
    ".json", ".txt", ".mcfunction", ".properties", ".yml", ".yaml", ".ini", ".cfg", ".lang", ".mcmeta",
}

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
MAX_EDIT_SIZE = 5 * 1024 * 1024      # 5MB


def _guard_disk_for_operation(operation: str) -> None:
    try:
        assert_disk_usage_safe(operation)
    except DiskProtectionError as exc:
        raise HTTPException(status_code=503, detail={"error": exc.message})


def _guard_world_size_after_upload(server: Server, server_root: Path, user: User) -> None:
    worlds_root = server_root / "worlds"
    try:
        assert_world_size_within_plan(worlds_root, server)
    except WorldSizeLimitError as exc:
        raise HTTPException(status_code=413, detail={"error": exc.message})


def _file_access_policy(server: Server) -> FileAccessPolicy:
    return JAVA_FILE_POLICY if is_java_flavor(server.flavor) else BEDROCK_FILE_POLICY


def _parse_relative_parts(requested_path: str) -> tuple[str, ...]:
    clean = requested_path.strip().lstrip("/")
    if not clean:
        return ()
    parts = tuple(part for part in clean.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise HTTPException(status_code=403, detail="Path traversal detected")
    return parts


def _validate_file_policy_path(
    requested_path: str,
    policy: FileAccessPolicy,
    *,
    allow_root: bool = False,
    allow_root_file: bool = True,
) -> tuple[str, ...]:
    parts = _parse_relative_parts(requested_path)
    if not parts:
        if allow_root:
            return parts
        raise HTTPException(status_code=403, detail="Access denied to this folder")

    if len(parts) == 1 and parts[0] in policy.root_files:
        if allow_root_file:
            return parts
        raise HTTPException(status_code=403, detail="Access denied to this folder")

    if parts[0] not in policy.roots:
        raise HTTPException(status_code=403, detail="Access denied to this folder")
    return parts


def _agent_policy_params(policy: FileAccessPolicy, extra: dict | None = None) -> dict:
    params = dict(extra or {})
    params["allowed_roots"] = ",".join(sorted(policy.roots))
    params["allowed_files"] = ",".join(sorted(policy.root_files))
    return params


def _get_server(server_id: str, user: User, db: Session, required_permission: str = "files") -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")

    server = db.get(Server, server_uuid)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if server.owner_id == user.id:
        return server
        
    from sqlalchemy import select
    from blockhost_backend.database.schema import ServerCollaborator
    
    collab = db.execute(
        select(ServerCollaborator).where(
            ServerCollaborator.server_id == server.id,
            ServerCollaborator.user_id == user.id
        )
    ).scalars().first()
    
    if not collab:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if required_permission not in collab.permissions:
        raise HTTPException(status_code=403, detail=f"Missing required permission: {required_permission}")
        
    return server


def _get_local_server_root(server: Server) -> Path:
    mc_config = server.mc_config or {}
    server_dir_str = mc_config.get("server_dir")
    if not server_dir_str:
        raise HTTPException(status_code=400, detail="Server directory not configured")

    server_root = Path(server_dir_str).resolve()
    if not server_root.exists() or not server_root.is_dir():
        raise HTTPException(status_code=404, detail="Server root directory does not exist")

    return server_root


def _validate_safe_path(
    server_root: Path,
    requested_path: str,
    policy: FileAccessPolicy,
    *,
    allow_root: bool = False,
    allow_root_file: bool = True,
) -> Path:
    _validate_file_policy_path(
        requested_path,
        policy,
        allow_root=allow_root,
        allow_root_file=allow_root_file,
    )
    requested_path = requested_path.lstrip("/")
    resolved = (server_root / requested_path).resolve()

    if not resolved.is_relative_to(server_root):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    return resolved


# --- PROXY HELPERS ---
def _get_agent_url(node: Node, server_id: str, endpoint: str) -> str:
    return f"http://{node.ip_address}:{node.agent_port}/agent/servers/{server_id}/files/{endpoint}"

def _proxy_get(node: Node, server_id: str, endpoint: str, params: dict):
    url = _get_agent_url(node, server_id, endpoint)
    headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
    with httpx.Client() as client:
        resp = client.get(url, params=params, headers=headers, timeout=60.0)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

def _proxy_put(node: Node, server_id: str, endpoint: str, params: dict, json_data: dict):
    url = _get_agent_url(node, server_id, endpoint)
    headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
    with httpx.Client() as client:
        resp = client.put(url, params=params, json=json_data, headers=headers, timeout=60.0)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

def _proxy_delete(node: Node, server_id: str, endpoint: str, params: dict):
    url = _get_agent_url(node, server_id, endpoint)
    headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
    with httpx.Client() as client:
        resp = client.delete(url, params=params, headers=headers, timeout=60.0)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@router.get("/list", response_model=list[FileInfo])
def list_files(
    server_id: str,
    path: str = Query("", description="Relative path inside the server"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy, allow_root=True)
            return _proxy_get(node, str(server.id), "list", _agent_policy_params(policy, {"path": path}))

    # Local fallback
    server_root = _get_local_server_root(server)
    if not path or path.strip() == "/":
        results = []
        for folder in sorted(policy.roots):
            folder_path = server_root / folder
            if folder_path.exists() and folder_path.is_dir():
                stat = folder_path.stat()
                results.append(
                    FileInfo(
                        name=folder,
                        path=folder,
                        is_dir=True,
                        size=None,
                        last_modified=stat.st_mtime,
                    )
                )
        for filename in sorted(policy.root_files):
            file_path = server_root / filename
            if file_path.exists() and file_path.is_file():
                stat = file_path.stat()
                results.append(
                    FileInfo(
                        name=filename,
                        path=filename,
                        is_dir=False,
                        size=stat.st_size,
                        last_modified=stat.st_mtime,
                    )
                )
        return results

    target_dir = _validate_safe_path(server_root, path, policy)
    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    results = []
    for item in target_dir.iterdir():
        is_dir = item.is_dir()
        stat = item.stat()
        rel_path = item.relative_to(server_root).as_posix()
        results.append(
            FileInfo(
                name=item.name,
                path=rel_path,
                is_dir=is_dir,
                size=None if is_dir else stat.st_size,
                last_modified=stat.st_mtime,
            )
        )

    results.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    return results


@router.get("/read")
def read_file(
    server_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy)
            return _proxy_get(node, str(server.id), "read", _agent_policy_params(policy, {"path": path}))

    server_root = _get_local_server_root(server)
    target_file = _validate_safe_path(server_root, path, policy)

    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    size = target_file.stat().st_size
    if size > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large to edit (max {MAX_EDIT_SIZE//1024//1024}MB)")

    try:
        content = target_file.read_text(encoding="utf-8")
        return {"content": content}
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="File is not valid UTF-8 text")


@router.put("/write")
def write_file(
    server_id: str,
    payload: FileWriteRequest,
    path: str = Query(..., description="Relative path to the file"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy)
            return _proxy_put(node, str(server.id), "write", _agent_policy_params(policy, {"path": path}), {"content": payload.content})

    server_root = _get_local_server_root(server)
    target_file = _validate_safe_path(server_root, path, policy)

    content_bytes = payload.content.encode("utf-8")
    if len(content_bytes) > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail=f"Content too large (max {MAX_EDIT_SIZE//1024//1024}MB)")

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(content_bytes)
    return {"status": "ok"}


@router.get("/download")
def download_file(
    server_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy)
            url = _get_agent_url(node, str(server.id), "download")
            headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
            client = httpx.Client()
            req = client.build_request("GET", url, params=_agent_policy_params(policy, {"path": path}), headers=headers)
            resp = client.send(req, stream=True)
            if resp.status_code >= 400:
                resp.close()
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

            return StreamingResponse(
                resp.iter_bytes(),
                media_type=resp.headers.get("content-type"),
                headers={"Content-Disposition": resp.headers.get("content-disposition", f"attachment; filename={os.path.basename(path)}")},
                background=resp.close
            )

    server_root = _get_local_server_root(server)
    target_file = _validate_safe_path(server_root, path, policy)

    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=target_file,
        filename=target_file.name,
        content_disposition_type="attachment"
    )


@router.post("/upload")
async def upload_file(
    server_id: str,
    path: str = Query(..., description="Target directory path"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(upload_rate_limit()),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)

    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy, allow_root_file=False)
            url = _get_agent_url(node, str(server.id), "upload")
            headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
            async with httpx.AsyncClient() as client:
                files_payload = {"file": (file.filename, file.file, file.content_type)}
                resp = await client.post(url, params=_agent_policy_params(policy, {"path": path}), headers=headers, files=files_payload, timeout=300.0)
                if resp.status_code >= 400:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                return resp.json()

    # Local fallback
    server_root = _get_local_server_root(server)
    _guard_disk_for_operation("upload_file")

    target_dir = _validate_safe_path(server_root, path, policy, allow_root_file=False)
    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    target_dir.mkdir(parents=True, exist_ok=True)

    filename = os.path.basename(file.filename or "uploaded_file")
    target_file = target_dir / filename
    _validate_safe_path(server_root, target_file.relative_to(server_root).as_posix(), policy)

    total_size = 0
    with open(target_file, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                target_file.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_SIZE//1024//1024}MB)")
            buffer.write(chunk)
            try:
                assert_disk_usage_safe("upload_file")
            except DiskProtectionError as exc:
                target_file.unlink(missing_ok=True)
                raise HTTPException(status_code=503, detail={"error": exc.message})

    if target_file.is_relative_to(server_root / "worlds"):
        try:
            _guard_world_size_after_upload(server, server_root, user)
        except HTTPException:
            target_file.unlink(missing_ok=True)
            raise

    return {"status": "ok", "filename": filename, "size": total_size}


@router.delete("/delete")
def delete_file_or_folder(
    server_id: str,
    path: str = Query(..., description="Relative path to delete"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server = _get_server(server_id, user, db)
    policy = _file_access_policy(server)
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            _validate_file_policy_path(path, policy)
            return _proxy_delete(node, str(server.id), "delete", _agent_policy_params(policy, {"path": path}))

    import shutil
    server_root = _get_local_server_root(server)
    target = _validate_safe_path(server_root, path, policy)

    for allowed in policy.roots:
        allowed_path = (server_root / allowed).resolve()
        if target == allowed_path:
            raise HTTPException(status_code=403, detail="Cannot delete root folders")
    if target.parent == server_root and target.name in policy.root_files:
        raise HTTPException(status_code=403, detail="Cannot delete protected server config files")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {str(e)}")
