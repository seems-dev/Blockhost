from pathlib import Path
import uuid
import os
import httpx

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user, upload_rate_limit
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, User, Node
from blockhost_backend.api.schemas import FileInfo, FileWriteRequest
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.services.system_health import (
    DiskProtectionError,
    WorldSizeLimitError,
    assert_disk_usage_safe,
    assert_world_size_within_plan,
)

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])

ALLOWED_ROOTS = {
    "worlds",
    "development_behavior_packs",
    "development_resource_packs",
    "development_skin_packs",
}

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


def _guard_world_size_after_upload(server_root: Path, user: User) -> None:
    worlds_root = server_root / "worlds"
    try:
        assert_world_size_within_plan(worlds_root)
    except WorldSizeLimitError as exc:
        raise HTTPException(status_code=413, detail={"error": exc.message})


def _get_server(server_id: str, user: User, db: Session) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")

    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
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


def _validate_safe_path(server_root: Path, requested_path: str) -> Path:
    requested_path = requested_path.lstrip("/")
    resolved = (server_root / requested_path).resolve()

    if not resolved.is_relative_to(server_root):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    is_allowed = False
    for allowed in ALLOWED_ROOTS:
        allowed_path = (server_root / allowed).resolve()
        if resolved.is_relative_to(allowed_path) or resolved == allowed_path:
            is_allowed = True
            break

    if not is_allowed:
        raise HTTPException(status_code=403, detail="Access denied to this folder")

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
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            return _proxy_get(node, str(server.id), "list", {"path": path})

    # Local fallback
    server_root = _get_local_server_root(server)
    if not path or path.strip() == "/":
        results = []
        for folder in ALLOWED_ROOTS:
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
        return results

    target_dir = _validate_safe_path(server_root, path)
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
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            return _proxy_get(node, str(server.id), "read", {"path": path})

    server_root = _get_local_server_root(server)
    target_file = _validate_safe_path(server_root, path)

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
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            return _proxy_put(node, str(server.id), "write", {"path": path}, {"content": payload.content})

    server_root = _get_local_server_root(server)
    target_file = _validate_safe_path(server_root, path)

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
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            url = _get_agent_url(node, str(server.id), "download")
            headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
            client = httpx.Client()
            req = client.build_request("GET", url, params={"path": path}, headers=headers)
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
    target_file = _validate_safe_path(server_root, path)

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
    
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            url = _get_agent_url(node, str(server.id), "upload")
            headers = {"Authorization": f"Bearer {get_settings().worker_agent_token}"}
            async with httpx.AsyncClient() as client:
                files_payload = {"file": (file.filename, file.file, file.content_type)}
                resp = await client.post(url, params={"path": path}, headers=headers, files=files_payload, timeout=300.0)
                if resp.status_code >= 400:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                return resp.json()

    # Local fallback
    server_root = _get_local_server_root(server)
    _guard_disk_for_operation("upload_file")
    
    target_dir = _validate_safe_path(server_root, path)
    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    target_dir.mkdir(parents=True, exist_ok=True)
    
    filename = os.path.basename(file.filename or "uploaded_file")
    target_file = target_dir / filename
    _validate_safe_path(server_root, target_file.relative_to(server_root).as_posix())

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
            _guard_world_size_after_upload(server_root, user)
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
    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            return _proxy_delete(node, str(server.id), "delete", {"path": path})

    import shutil
    server_root = _get_local_server_root(server)
    target = _validate_safe_path(server_root, path)

    for allowed in ALLOWED_ROOTS:
        allowed_path = (server_root / allowed).resolve()
        if target == allowed_path:
            raise HTTPException(status_code=403, detail="Cannot delete root folders")

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

