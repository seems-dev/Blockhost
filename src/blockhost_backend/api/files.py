from pathlib import Path
import uuid
import os

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, User
from blockhost_backend.api.schemas import FileInfo, FileWriteRequest

router = APIRouter(prefix="/api/servers/{server_id}/files", tags=["files"])

ALLOWED_ROOTS = {
    "worlds",
    "development_behavior_packs",
    "development_resource_packs",
    "development_skin_packs",
}

TEXT_EXTENSIONS = {
    ".json",
    ".txt",
    ".mcfunction",
    ".properties",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".lang",
    ".mcmeta",
}

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
MAX_EDIT_SIZE = 5 * 1024 * 1024      # 5MB


def _get_server_and_root(server_id: str, user: User, db: Session) -> tuple[Server, Path]:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")

    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    mc_config = server.mc_config or {}
    server_dir_str = mc_config.get("server_dir")
    if not server_dir_str:
        raise HTTPException(status_code=400, detail="Server directory not configured")

    server_root = Path(server_dir_str).resolve()
    if not server_root.exists() or not server_root.is_dir():
        raise HTTPException(status_code=404, detail="Server root directory does not exist")

    return server, server_root


def _validate_safe_path(server_root: Path, requested_path: str) -> Path:
    # Remove leading slashes so the path is evaluated as relative
    requested_path = requested_path.lstrip("/")
    resolved = (server_root / requested_path).resolve()

    if not resolved.is_relative_to(server_root):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    # Check against allowed roots
    is_allowed = False
    for allowed in ALLOWED_ROOTS:
        allowed_path = (server_root / allowed).resolve()
        if resolved.is_relative_to(allowed_path) or resolved == allowed_path:
            is_allowed = True
            break

    if not is_allowed:
        raise HTTPException(status_code=403, detail="Access denied to this folder")

    return resolved


@router.get("/list", response_model=list[FileInfo])
def list_files(
    server_id: str,
    path: str = Query("", description="Relative path inside the server"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FileInfo]:
    server, server_root = _get_server_and_root(server_id, user, db)

    # Special case: listing the root directory itself
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

    # Normal directory listing
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
    
    # Sort: folders first, then alphabetically
    results.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    return results


@router.get("/read")
def read_file(
    server_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    server, server_root = _get_server_and_root(server_id, user, db)
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
    server, server_root = _get_server_and_root(server_id, user, db)
    target_file = _validate_safe_path(server_root, path)

    content_bytes = payload.content.encode("utf-8")
    if len(content_bytes) > MAX_EDIT_SIZE:
        raise HTTPException(status_code=413, detail=f"Content too large (max {MAX_EDIT_SIZE//1024//1024}MB)")

    # Ensure parent directory exists
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
    server, server_root = _get_server_and_root(server_id, user, db)
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
):
    server, server_root = _get_server_and_root(server_id, user, db)
    
    # We expect path to be the destination directory
    target_dir = _validate_safe_path(server_root, path)
    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    target_dir.mkdir(parents=True, exist_ok=True)
    
    filename = file.filename or "uploaded_file"
    # Basic filename sanitization
    filename = os.path.basename(filename)
    target_file = target_dir / filename

    # Just double-checking the resulting file is still safe
    _validate_safe_path(server_root, target_file.relative_to(server_root).as_posix())

    total_size = 0
    with open(target_file, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                target_file.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_SIZE//1024//1024}MB)")
            buffer.write(chunk)

    return {"status": "ok", "filename": filename, "size": total_size}


@router.delete("/delete")
def delete_file_or_folder(
    server_id: str,
    path: str = Query(..., description="Relative path to delete"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import shutil
    server, server_root = _get_server_and_root(server_id, user, db)
    target = _validate_safe_path(server_root, path)

    # Prevent deleting the allowed roots themselves
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

