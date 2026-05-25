from __future__ import annotations

import threading
import time
import tempfile
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import User
from blockhost_backend.minecraft.bedrock_download import (
    available_versions,
    ensure_version_installed,
    recommended_version,
    safe_extract_zip,
)


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


def _start_download_in_background(*, version: str, settings) -> None:
    state = _download_state(version)
    if state.get("status") == "downloading":
        return

    def _run() -> None:
        _set_download_state(version, status="downloading", stage="starting", started_at=time.time(), bytes=0, error=None)

        def _progress(n: int) -> None:
            _set_download_state(version, bytes=n, stage="downloading", updated_at=time.time())

        def _event(kind: str, data: dict[str, object]) -> None:
            if kind == "download_start":
                _set_download_state(version, stage="fetching", url=data.get("url"), updated_at=time.time())
                return
            if kind == "response_headers":
                _set_download_state(
                    version,
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
                    version,
                    stage="error",
                    error_type=data.get("error_type"),
                    error=data.get("error"),
                    updated_at=time.time(),
                )
                return
            if kind == "install_done":
                _set_download_state(version, stage="done", path=data.get("path"), updated_at=time.time())

        try:
            _set_download_state(version, stage="installing", updated_at=time.time())
            dest = ensure_version_installed(
                versions_dir=Path(settings.bedrock_versions_dir),
                manifest_path=Path(settings.bedrock_versions_manifest),
                version=version,
                progress_cb=_progress,
                event_cb=_event,
            )
            _set_download_state(version, status="done", path=str(dest), finished_at=time.time())
        except Exception as e:
            _set_download_state(version, status="error", error=str(e), finished_at=time.time())

    t = threading.Thread(target=_run, name=f"bedrock-download-{version}", daemon=True)
    t.start()


@router.get("")
def list_versions(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    installed = []
    if versions_dir.exists():
        installed = sorted([p.name for p in versions_dir.iterdir() if p.is_dir()])
    rec = None
    try:
        rec = recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    except Exception:
        rec = None
    return {"installed": installed, "recommended": rec}


@router.get("/recommended")
def get_recommended_version(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    rec = recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    installed = (versions_dir / rec).exists()
    return {"version": rec, "installed": installed}


@router.get("/catalog")
def get_catalog(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    manifest_path = Path(settings.bedrock_versions_manifest)
    versions = available_versions(manifest_path=manifest_path)
    rec = recommended_version(manifest_path=manifest_path)
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    installed = sorted([p.name for p in versions_dir.iterdir() if p.is_dir()]) if versions_dir.exists() else []
    return {"available": versions, "recommended": rec, "installed": installed}


@router.post("/{version}/download")
def download_version(version: str, user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    # Fast path if installed.
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    if (versions_dir / version).exists():
        return {"status": "ok", "version": version, "installed": True, "path": str(versions_dir / version)}

    # Start in background and return immediately (UI-friendly).
    _start_download_in_background(version=version, settings=settings)
    st = _download_state(version)
    return {"status": st.get("status", "downloading"), "version": version, "installed": False, "bytes": st.get("bytes", 0)}


@router.get("/{version}/download/status")
def download_status(version: str, user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    versions_dir = Path(settings.bedrock_versions_dir).resolve()
    if (versions_dir / version).exists():
        return {"status": "done", "version": version, "installed": True, "path": str(versions_dir / version)}
    st = _download_state(version)
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
