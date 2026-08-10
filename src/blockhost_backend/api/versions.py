from __future__ import annotations

import threading
import time
import tempfile
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
import httpx

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import User
from blockhost_backend.minecraft.bedrock_download import (
    list_remote_versions,
    safe_extract_zip,
)
from blockhost_backend.minecraft.binary_manager import ensure_binary_installed, get_installed_binary
from blockhost_backend.database.schema import ServerFlavor
from blockhost_backend.database.db import get_db
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/versions", tags=["versions"])

_dl_lock = threading.Lock()
_downloads: dict[str, dict] = {}


def _download_state(version: str) -> dict:
    with _dl_lock:
        return dict(_downloads.get(version, {}))


def _set_download_state(version: str, **updates: object) -> None:
    with _dl_lock:
        cur = _downloads.get(version) or {}
        cur.update(updates)
        _downloads[version] = cur


def _start_download_in_background(*, version: str, flavor: ServerFlavor, settings, db: Session) -> None:
    task_key = f"{flavor.value}-{version}"
    state = _download_state(task_key)
    if state.get("status") == "downloading":
        return

    def _run() -> None:
        _set_download_state(task_key, status="downloading", stage="starting", started_at=time.time(), bytes=0, error=None)

        def _progress(n: int) -> None:
            _set_download_state(task_key, bytes=n, stage="downloading", updated_at=time.time())

        def _event(kind: str, data: dict[str, object]) -> None:
            if kind == "download_start":
                _set_download_state(task_key, stage="fetching", url=data.get("url"), updated_at=time.time())
                return
            if kind == "response_headers":
                _set_download_state(
                    task_key,
                    stage="downloading",
                    http_status=data.get("status_code"),
                    final_url=data.get("final_url"),
                    content_length=data.get("content_length"),
                    content_type=data.get("content_type"),
                    updated_at=time.time(),
                )
                return
            if kind == "download_error":
                _set_download_state(
                    task_key,
                    stage="error",
                    error_type=data.get("error_type"),
                    error=data.get("error"),
                    updated_at=time.time(),
                )
                return
            if kind == "install_done":
                _set_download_state(task_key, stage="done", path=data.get("path"), updated_at=time.time())

        try:
            _set_download_state(task_key, stage="installing", updated_at=time.time())
            
            binary = ensure_binary_installed(
                db=db,
                flavor=flavor,
                version=version,
                progress_cb=_progress,
                event_cb=_event,
            )
            _set_download_state(task_key, status="done", path=binary.executable_path, finished_at=time.time())
        except Exception as e:
            _set_download_state(task_key, status="error", error=str(e), finished_at=time.time())

    t = threading.Thread(target=_run, name=f"binary-download-{task_key}", daemon=True)
    t.start()


_remote_bedrock_cache = []
_remote_bedrock_cache_time = 0.0

def _get_remote_bedrock_versions() -> list[str]:
    global _remote_bedrock_cache, _remote_bedrock_cache_time
    if time.time() - _remote_bedrock_cache_time > 300:
        _remote_bedrock_cache = list_remote_versions()
        _remote_bedrock_cache_time = time.time()
    return _remote_bedrock_cache

@router.get("")
def list_versions(flavor: str = Query(default=ServerFlavor.BEDROCK.value), user: User = Depends(get_current_user)) -> dict:
    flavor_enum = ServerFlavor(flavor)
    
    # List installed from registry directory
    from blockhost_backend.minecraft.binary_manager import _get_binaries_dir
    versions_dir = _get_binaries_dir() / flavor_enum.value
    installed = []
    if versions_dir.exists():
        installed = sorted([p.name for p in versions_dir.iterdir() if p.is_dir() or p.name.endswith(".jar")])
        if flavor_enum != ServerFlavor.BEDROCK:
            installed = [name.replace("server-", "").replace(".jar", "") for name in installed if name.endswith(".jar")]
    
    rec = None
    if flavor_enum == ServerFlavor.BEDROCK:
        remote = _get_remote_bedrock_versions()
        rec = remote[0] if remote else None
    return {"installed": installed, "recommended": rec}


@router.get("/recommended")
def get_recommended_version(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    remote = _get_remote_bedrock_versions()
    rec = remote[0] if remote else None
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    installed = (versions_dir / rec).exists() if rec else False
    return {"version": rec, "installed": installed}


@router.get("/catalog")
def get_catalog(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    versions = _get_remote_bedrock_versions()
    rec = versions[0] if versions else None
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    installed = sorted([p.name for p in versions_dir.iterdir() if p.is_dir()]) if versions_dir.exists() else []
    return {"available": versions, "recommended": rec, "installed": installed}


@router.get("/catalog/remote")
def get_catalog_remote(user: User = Depends(get_current_user)) -> dict:
    """Dedicated endpoint for UI to fetch live Bedrock versions."""
    versions = _get_remote_bedrock_versions()
    rec = versions[0] if versions else None
    return {"available": versions, "recommended": rec, "installed": []}


@router.get("/java-catalog")
def get_java_catalog(user: User = Depends(get_current_user)) -> dict:
    """
    Returns a list of Java edition versions available from Mojang's manifest.
    Only returns 'release' type versions (not snapshots).
    """
    try:
        manifest = httpx.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", timeout=10.0)
        manifest.raise_for_status()
        versions = [
            v["id"] for v in manifest.json()["versions"]
            if v["type"] == "release"
        ]
        recommended = versions[0] if versions else None
        return {"available": versions, "recommended": recommended, "installed": []}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Java versions: {e}")


@router.post("/{version}/download")
def download_version(
    version: str,
    flavor: str = Query(default=ServerFlavor.BEDROCK.value),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> dict:
    settings = get_settings()
    flavor_enum = ServerFlavor(flavor)
    task_key = f"{flavor_enum.value}-{version}"
    
    # Fast path if installed.
    binary = get_installed_binary(db, flavor_enum, version)
    if binary and Path(binary.executable_path).exists():
        return {"status": "ok", "version": version, "installed": True, "path": binary.executable_path}

    # Start in background and return immediately (UI-friendly).
    _start_download_in_background(version=version, flavor=flavor_enum, settings=settings, db=db)
    st = _download_state(task_key)
    return {"status": st.get("status", "downloading"), "version": version, "installed": False, "bytes": st.get("bytes", 0)}


@router.get("/{version}/download/status")
def download_status(
    version: str,
    flavor: str = Query(default=ServerFlavor.BEDROCK.value),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> dict:
    flavor_enum = ServerFlavor(flavor)
    task_key = f"{flavor_enum.value}-{version}"

    binary = get_installed_binary(db, flavor_enum, version)
    if binary and Path(binary.executable_path).exists():
        return {"status": "done", "version": version, "installed": True, "path": binary.executable_path}
    st = _download_state(task_key)
    if not st:
        return {"status": "not_started", "version": version, "installed": False}
    return {"version": version, "installed": False, **st}


@router.post("/{version}/import")
async def import_version_zip(
    version: str,
    file: UploadFile = File(...),
    overwrite: bool = Query(default=False),
    user: User = Depends(get_current_user),
) -> dict:
    """
    Fallback installer: upload a Bedrock server zip and install it into `versions/<version>/`.
    This avoids DNS/proxy issues on some networks.
    """
    settings = get_settings()
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    dest = versions_dir / version

    if dest.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"Version already installed: {version}")

    versions_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blockhost-import-") as td:
        tmpdir = Path(td)
        zip_path = tmpdir / "upload.zip"
        with open(zip_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

        extract_dir = tmpdir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            safe_extract_zip(zip_path, extract_dir)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid zip: {e}")

        # Same sanity check logic as downloader.
        has_binary = any((extract_dir / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
        if not has_binary:
            children = [p for p in extract_dir.iterdir() if p.is_dir()]
            if len(children) == 1:
                inner = children[0]
                has_binary = any((inner / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
                if has_binary:
                    extract_dir = inner
        if not has_binary:
            raise HTTPException(status_code=400, detail="Zip did not contain bedrock_server/bedrock_server.exe")

        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(extract_dir), str(dest))

    return {"status": "ok", "version": version, "path": str(dest)}
