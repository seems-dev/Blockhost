from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings, resolve_data_path
from blockhost_backend.database.schema import RuntimeBinary, ServerFlavor
from blockhost_backend.minecraft.software_provider import resolve_jar_url, download_file

logger = logging.getLogger(__name__)

def _get_binaries_dir() -> Path:
    settings = get_settings()
    return resolve_data_path(settings.runtime_binaries_dir)

def get_installed_binary(db: Session, flavor: ServerFlavor, version: str) -> RuntimeBinary | None:
    return db.scalars(
        select(RuntimeBinary)
        .where(
            RuntimeBinary.flavor == flavor,
            RuntimeBinary.version == version,
            RuntimeBinary.installed == True,
        )
    ).first()

def register_binary(
    db: Session,
    flavor: ServerFlavor,
    version: str,
    executable_path: str,
    checksum_sha256: str | None = None,
) -> RuntimeBinary:
    binary = db.scalars(
        select(RuntimeBinary).where(
            RuntimeBinary.flavor == flavor,
            RuntimeBinary.version == version,
        )
    ).first()

    if not binary:
        binary = RuntimeBinary(
            id=uuid.uuid4(),
            game="minecraft",
            edition="bedrock" if flavor == ServerFlavor.BEDROCK else "java",
            flavor=flavor,
            version=version,
        )
        db.add(binary)

    binary.executable_path = executable_path
    binary.checksum_sha256 = checksum_sha256
    binary.installed = True
    db.commit()
    db.refresh(binary)
    return binary

def ensure_binary_installed(db: Session, flavor: ServerFlavor, version: str, progress_cb=None, event_cb=None) -> RuntimeBinary:
    binary = get_installed_binary(db, flavor, version)
    if binary and Path(binary.executable_path).exists():
        return binary

    binaries_dir = _get_binaries_dir()
    flavor_dir = binaries_dir / flavor.value / version

    if flavor == ServerFlavor.BEDROCK:
        # --- Check if already on disk before trying to download ---
        for exe_name in ("bedrock_server", "bedrock_server.exe"):
            candidate = flavor_dir / exe_name
            if candidate.exists():
                logger.info("Bedrock binary %s found on disk, registering in DB.", version)
                return register_binary(db=db, flavor=flavor, version=version, executable_path=str(candidate.resolve()))

        from blockhost_backend.minecraft.bedrock_download import ensure_version_installed
        settings = get_settings()
        manifest_path = resolve_data_path(settings.bedrock_versions_manifest)

        # We install bedrock in the flavor_dir
        dest = ensure_version_installed(
            versions_dir=binaries_dir / flavor.value,
            manifest_path=manifest_path,
            version=version,
            progress_cb=progress_cb,
            event_cb=event_cb,
        )
        # Find the executable
        exe_path = dest / "bedrock_server"
        if not exe_path.exists():
            exe_path = dest / "bedrock_server.exe"

        return register_binary(
            db=db,
            flavor=flavor,
            version=version,
            executable_path=str(exe_path.resolve())
        )
    else:
        # Java download logic
        flavor_dir.mkdir(parents=True, exist_ok=True)
        jar_path = flavor_dir / f"server-{version}.jar"

        # --- Check if already on disk before trying to download ---
        if jar_path.exists():
            logger.info("Java binary %s found on disk, registering in DB.", version)
            return register_binary(db=db, flavor=flavor, version=version, executable_path=str(jar_path.resolve()))

        url = resolve_jar_url(flavor, version)
        if event_cb:
            event_cb("download_start", {"version": version, "url": url})

        # Download the jar directly
        with tempfile.TemporaryDirectory() as td:
            tmp_jar = Path(td) / "server.jar"
            download_file(url, tmp_jar)

            # Atomic move
            shutil.move(str(tmp_jar), str(jar_path))

        if event_cb:
            event_cb("install_done", {"version": version, "path": str(jar_path)})

        return register_binary(
            db=db,
            flavor=flavor,
            version=version,
            executable_path=str(jar_path.resolve())
        )
