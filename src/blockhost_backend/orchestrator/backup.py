from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import threading
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings, resolve_data_path
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import (
    Backup,
    BackupConsistencyMethod,
    BackupJob,
    BackupKind,
    BackupSchedule,
    BackupStatus,
    BackupStorageBackend,
    RestoreJob,
    RestoreStatus,
    Server,
    utcnow,
)
from blockhost_backend.minecraft.java_compat import is_java_flavor
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.services.api_cache import get_api_cache
from blockhost_backend.services.system_health import assert_disk_usage_safe

_ORCHESTRATOR = get_server_lifecycle_orchestrator()
_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, get_settings().backup_worker_threads), thread_name_prefix="backup-worker")
_SCHEDULER_STARTED = False
_SCHEDULER_GUARD = threading.Lock()
logger = logging.getLogger(__name__)

ACTIVE_BACKUP_STATUSES = {
    BackupStatus.pending,
    BackupStatus.running,
    BackupStatus.held_saves,
    BackupStatus.copying,
    BackupStatus.compressing,
    BackupStatus.verifying,
}

ACTIVE_RESTORE_STATUSES = {
    RestoreStatus.pending,
    RestoreStatus.running,
    RestoreStatus.validating_backup,
    RestoreStatus.stopping_server,
    RestoreStatus.extracting,
    RestoreStatus.validating_restore,
    RestoreStatus.swapping,
    RestoreStatus.starting_server,
}


class BackupRestoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StoredBackupArtifact:
    path: Path
    checksum_sha256: str
    size_bytes: int


def server_backup_restore_lock(server_id: uuid.UUID | str):
    return get_api_cache().lock(f"lock:backup:{server_id}", timeout=3600)


def backup_storage_root() -> Path:
    root = resolve_data_path(get_settings().backup_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_tmp_root() -> Path:
    root = resolve_data_path(get_settings().backup_temp_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_object_path(server_id: uuid.UUID, backup_id: uuid.UUID) -> Path:
    path = backup_storage_root() / str(server_id) / "objects" / f"{backup_id}.tar.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def backup_storage_key(server_id: uuid.UUID, backup_id: uuid.UUID) -> str:
    return f"{server_id}/objects/{backup_id}.tar.gz"


def submit_backup_job(job_id: uuid.UUID) -> None:
    _EXECUTOR.submit(run_backup_job, job_id)


def submit_restore_job(job_id: uuid.UUID) -> None:
    _EXECUTOR.submit(run_restore_job, job_id)


def _require_server_dir(server: Server) -> Path:
    raw = (server.mc_config or {}).get("server_dir")
    if not raw:
        raise BackupRestoreError("server_dir_missing", "Server directory is not configured")

    settings = get_settings()
    server_dir = Path(str(raw)).resolve()
    servers_root = resolve_data_path(settings.bedrock_servers_dir)
    try:
        server_dir.relative_to(servers_root)
    except ValueError:
        raise BackupRestoreError("invalid_server_dir", "Server directory is outside the configured servers root")
    if not server_dir.exists():
        raise BackupRestoreError("server_dir_missing", "Server directory does not exist")
    return server_dir


def _world_name(server: Server) -> str:
    cfg = dict(server.mc_config or {})
    return str(cfg.get("level_name") or server.world_name)


def _resolve_existing_world_path(server_dir: Path, world_name: str, *, java_flavor: bool) -> Path | None:
    candidates: list[Path] = []
    if world_name:
        candidates.append(server_dir / world_name)
    if java_flavor:
        candidates.append(server_dir / "world")
    else:
        candidates.append(server_dir / "worlds" / world_name)
        candidates.append(server_dir / "worlds")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _included_backup_paths(server: Server, server_dir: Path, world_name: str) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    if is_java_flavor(server.flavor):
        world_path = _resolve_existing_world_path(server_dir, world_name, java_flavor=True)
        if world_path is not None:
            candidates.append((world_path, "java/world"))
        for filename in (
            "server.properties",
            "eula.txt",
            "ops.json",
            "banned-players.json",
            "banned-ips.json",
            "whitelist.json",
        ):
            path = server_dir / filename
            if path.exists() and path.is_file():
                candidates.append((path, f"java/{filename}"))
        return candidates

    world_path = _resolve_existing_world_path(server_dir, world_name, java_flavor=False)
    worlds_path = server_dir / "worlds"

    if world_path is not None:
        if world_path == worlds_path:
            candidates.append((worlds_path, "bedrock/worlds"))
        else:
            candidates.append((world_path, f"bedrock/worlds/{world_name}"))
    elif worlds_path.exists():
        candidates.append((worlds_path, "bedrock/worlds"))

    for filename in (
        "server.properties",
        "allowlist.json",
        "permissions.json",
        "valid_known_packs.json",
        "world_behavior_packs.json",
        "world_resource_packs.json",
    ):
        path = server_dir / filename
        if path.exists() and path.is_file():
            candidates.append((path, f"bedrock/{filename}"))
    return candidates


def _backup_world_size_path(server: Server, server_dir: Path, world_name: str) -> Path:
    if is_java_flavor(server.flavor):
        world_path = _resolve_existing_world_path(server_dir, world_name, java_flavor=True)
        if world_path is not None:
            return world_path
        return server_dir / "world"
    world_path = _resolve_existing_world_path(server_dir, world_name, java_flavor=False)
    if world_path is not None:
        return world_path
    return server_dir / "worlds"


def _active_world_path(server: Server, server_dir: Path) -> Path:
    if is_java_flavor(server.flavor):
        world_path = _resolve_existing_world_path(server_dir, str((server.mc_config or {}).get("level_name") or server.world_name), java_flavor=True)
        if world_path is not None:
            return world_path
        return server_dir / "world"
    world_path = _resolve_existing_world_path(server_dir, str((server.mc_config or {}).get("level_name") or server.world_name), java_flavor=False)
    if world_path is not None:
        return world_path
    return server_dir / "worlds"


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and not child.is_symlink():
                    yield child


def _size_bytes(paths: Iterable[Path]) -> int:
    total = 0
    for file_path in _iter_files(paths):
        try:
            total += file_path.stat().st_size
        except FileNotFoundError:
            pass
    return total


def _add_to_tar_with_progress(
    tar: tarfile.TarFile,
    src: Path,
    arcname: str,
    job: BackupJob,
    db: Session,
    processed_so_far: int,
) -> int:
    if src.is_file():
        tar.add(src, arcname=arcname)
        processed_so_far += src.stat().st_size
        _update_backup_job(db, job, BackupStatus.compressing, "compressing", 10, 80, processed_so_far)
        return processed_so_far

    # Add directory entry non-recursively
    tar.add(src, arcname=arcname, recursive=False)

    for child in src.rglob("*"):
        if child.is_symlink():
            continue
        rel = child.relative_to(src)
        target_arcname = f"{arcname}/{rel.as_posix()}"
        if child.is_dir():
            tar.add(child, arcname=target_arcname, recursive=False)
            continue
        tar.add(child, arcname=target_arcname)
        processed_so_far += child.stat().st_size
        _update_backup_job(db, job, BackupStatus.compressing, "compressing", 10, 80, processed_so_far)
    return processed_so_far


def _update_backup_job(
    db: Session,
    job: BackupJob,
    status: BackupStatus,
    phase: str,
    min_percent: int,
    max_percent: int,
    progress_bytes: int | None = None,
) -> None:
    if progress_bytes is not None:
        job.progress_bytes = progress_bytes
    if job.total_bytes > 0 and progress_bytes is not None:
        span = max_percent - min_percent
        job.progress_percent = min(max_percent, min_percent + int(span * progress_bytes / job.total_bytes))
    else:
        job.progress_percent = max(job.progress_percent, min_percent)
    job.status = status
    job.phase = phase
    job.heartbeat_at = utcnow()
    job.updated_at = utcnow()
    db.commit()


def _update_restore_job(
    db: Session,
    job: RestoreJob,
    status: RestoreStatus,
    phase: str,
    progress_percent: int,
    progress_bytes: int | None = None,
) -> None:
    job.status = status
    job.phase = phase
    job.progress_percent = max(job.progress_percent, min(100, progress_percent))
    if progress_bytes is not None:
        job.progress_bytes = progress_bytes
    job.heartbeat_at = utcnow()
    job.updated_at = utcnow()
    db.commit()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        logger.debug("Could not fsync file %s", path, exc_info=True)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        logger.debug("Could not open directory for fsync: %s", path, exc_info=True)
        return
    try:
        os.fsync(fd)
    except OSError:
        logger.debug("Could not fsync directory %s", path, exc_info=True)
    finally:
        os.close(fd)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        _fsync_file(tmp)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_backup_staging(staging: Path | None) -> None:
    if not staging:
        return

    _safe_rmtree(staging)
    for parent in (staging.parent, staging.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            pass


def _atomic_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _fsync_file(src)
    os.replace(src, dst)
    _fsync_dir(dst.parent)


def _verify_local_backup_artifact(path: Path, *, expected_checksum: str, expected_size: int) -> StoredBackupArtifact:
    if not path.exists() or not path.is_file():
        raise BackupRestoreError("backup_object_missing", "Backup archive was not created in storage")

    size = path.stat().st_size
    if size <= 0:
        raise BackupRestoreError("backup_object_empty", "Backup archive is empty")
    if size != expected_size:
        raise BackupRestoreError("backup_object_size_mismatch", "Backup archive size changed after storage commit")

    checksum = _sha256_file(path)
    if checksum != expected_checksum:
        raise BackupRestoreError("checksum_mismatch", "Backup archive checksum verification failed after storage commit")

    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if not checksum_path.exists() or checksum_path.read_text(encoding="utf-8").strip() != expected_checksum:
        raise BackupRestoreError("checksum_sidecar_missing", "Backup checksum sidecar was not created correctly")

    return StoredBackupArtifact(path=path, checksum_sha256=checksum, size_bytes=size)


def verify_backup_artifact(backup: Backup) -> StoredBackupArtifact:
    path = backup_object_path(backup.server_id, backup.id)
    if not path.exists() or not path.is_file():
        raise BackupRestoreError("backup_object_missing", "Backup archive is missing from storage")

    size = path.stat().st_size
    if size <= 0:
        raise BackupRestoreError("backup_object_empty", "Backup archive is empty")
    if backup.size_bytes and size != backup.size_bytes:
        raise BackupRestoreError("backup_object_size_mismatch", "Backup archive size does not match metadata")

    checksum = _sha256_file(path)
    if backup.checksum_sha256 and checksum != backup.checksum_sha256:
        raise BackupRestoreError("checksum_mismatch", "Backup archive checksum verification failed")

    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if backup.checksum_sha256:
        if not checksum_path.exists():
            raise BackupRestoreError("checksum_sidecar_missing", "Backup checksum sidecar is missing")
        if checksum_path.read_text(encoding="utf-8").strip() != backup.checksum_sha256:
            raise BackupRestoreError("checksum_sidecar_mismatch", "Backup checksum sidecar does not match metadata")

    return StoredBackupArtifact(
        path=path,
        checksum_sha256=backup.checksum_sha256 or checksum,
        size_bytes=size,
    )


def _commit_local_backup_artifact(temp_archive: Path, final_archive: Path) -> StoredBackupArtifact:
    if not temp_archive.exists() or not temp_archive.is_file():
        raise BackupRestoreError("backup_temp_missing", "Backup archive was not created in temporary storage")

    checksum = _sha256_file(temp_archive)
    size = temp_archive.stat().st_size
    if size <= 0:
        raise BackupRestoreError("backup_temp_empty", "Backup archive is empty")

    _atomic_replace(temp_archive, final_archive)
    _write_text_atomic(final_archive.with_suffix(final_archive.suffix + ".sha256"), f"{checksum}\n")
    return _verify_local_backup_artifact(final_archive, expected_checksum=checksum, expected_size=size)


def _stop_server(server: Server) -> None:
    _ORCHESTRATOR.stop_server(server)





def _start_server(server: Server, db: Session) -> None:
    settings = get_settings()
    servers_dir = resolve_data_path(settings.bedrock_servers_dir)
    _ORCHESTRATOR.start_server(
        server=server,
        settings=settings,
        servers_dir=servers_dir,
       
        db=db,
    )


def _try_save_hold(server_id: str) -> bool:
    settings = get_settings()
    try:
        _ORCHESTRATOR.send_command(server_id, "save hold")
        time.sleep(max(0.0, settings.backup_save_hold_seconds))
        _ORCHESTRATOR.send_command(server_id, "save query")
        return True
    except Exception:
        return False


def _save_resume_best_effort(server_id: str) -> None:
    try:
        _ORCHESTRATOR.send_command(server_id, "save resume")
    except Exception:
        pass


def run_backup_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    job: BackupJob | None = None
    backup: Backup | None = None
    server: Server | None = None
    save_held = False
    server_was_running = False
    stopped_for_backup = False
    temp_archive: Path | None = None
    final_archive: Path | None = None
    completed = False

    try:
        job = db.get(BackupJob, job_id)
        if not job:
            return
        backup = db.get(Backup, job.backup_id)
        server = db.get(Server, job.server_id)
        if not backup or not server:
            return

        with server_backup_restore_lock(server.id):
            active_restore = db.execute(
                select(RestoreJob).where(
                    RestoreJob.server_id == server.id,
                    RestoreJob.status.in_(ACTIVE_RESTORE_STATUSES),
                )
            ).scalars().first()
            if active_restore:
                raise BackupRestoreError("restore_active", "A restore is already active for this server")

            now = utcnow()
            job.status = BackupStatus.running
            job.phase = "running"
            job.started_at = now
            job.locked_at = now
            job.heartbeat_at = now
            job.attempts += 1
            backup.status = BackupStatus.running
            db.commit()

            server_dir = _require_server_dir(server)
            world_name = _world_name(server)
            assert_disk_usage_safe("run_backup_job")
            
            includes = _included_backup_paths(server, server_dir, world_name)
            total = _size_bytes(path for path, _arcname in includes)
            job.total_bytes = total
            backup.uncompressed_size_bytes = total
            db.commit()

            server_was_running = _ORCHESTRATOR.is_running(str(server.id))
            backup.server_was_running = server_was_running

            consistency_method = BackupConsistencyMethod.server_stop
            if server_was_running and not job.force_offline:
                save_held = _try_save_hold(str(server.id))

            if save_held:
                consistency_method = BackupConsistencyMethod.save_hold
                _update_backup_job(db, job, BackupStatus.held_saves, "held_saves", 8, 10)
            elif server_was_running:
                _stop_server(server)
                stopped_for_backup = True
                db.commit()

            _update_backup_job(db, job, BackupStatus.compressing, "compressing", 10, 80)
            final_archive = backup_object_path(server.id, backup.id)
            temp_archive = Path(str(final_archive) + ".tmp")
            if temp_archive.exists():
                temp_archive.unlink(missing_ok=True)

            if not includes:
                raise BackupRestoreError("invalid_backup_contents", "Backup contents are empty")

            processed = 0
            with tarfile.open(temp_archive, "w:gz") as tar:
                for source, archive_name in includes:
                    processed = _add_to_tar_with_progress(tar, source, archive_name, job, db, processed)

            if save_held:
                _save_resume_best_effort(str(server.id))
                save_held = False
            elif stopped_for_backup:
                _start_server(server, db)
                stopped_for_backup = False
                db.commit()

            backup.consistency_method = consistency_method
            backup.included_paths = [archive_name for _source, archive_name in includes]
            backup.world_name = world_name
            backup.bedrock_version = str((server.mc_config or {}).get("template_version") or "")
            db.commit()

            _update_backup_job(db, job, BackupStatus.verifying, "verifying", 85, 95)
            artifact = _commit_local_backup_artifact(temp_archive, final_archive)
            temp_archive = None

            completed_at = utcnow()
            backup.status = BackupStatus.completed
            backup.storage_key = backup_storage_key(server.id, backup.id)
            backup.checksum_sha256 = artifact.checksum_sha256
            backup.size_bytes = artifact.size_bytes
            backup.completed_at = completed_at
            backup.failure_code = None
            backup.failure_message = None
            job.status = BackupStatus.completed
            job.phase = "completed"
            job.progress_percent = 100
            job.progress_bytes = job.total_bytes
            job.completed_at = completed_at
            job.updated_at = completed_at
            server.last_backup_at = completed_at
            server.backup_count = (server.backup_count or 0) + 1
            db.commit()
            completed = True

            retention_count = None
            if backup.schedule_id:
                schedule = db.get(BackupSchedule, backup.schedule_id)
                if schedule:
                    schedule.last_success_at = completed_at
                    schedule.consecutive_failures = 0
                    retention_count = schedule.retention_count
                    db.commit()

            apply_backup_retention(db, server.id, keep_count=retention_count)

    except Exception as exc:
        if save_held and job:
            _save_resume_best_effort(str(job.server_id))
        if stopped_for_backup and server:
            try:
                _start_server(server, db)
            except Exception as exc:
                logger.warning("Failed to restart server %s after backup failure: %s", server.id, exc)

        code = getattr(exc, "code", "backup_failed")
        message = getattr(exc, "message", str(exc))
        if job:
            job.status = BackupStatus.failed
            job.phase = "failed"
            job.error_code = code
            job.error_message = message
            job.completed_at = utcnow()
            job.updated_at = utcnow()
        if backup:
            backup.status = BackupStatus.failed
            backup.failure_code = code
            backup.failure_message = message
            if backup.schedule_id:
                schedule = db.get(BackupSchedule, backup.schedule_id)
                if schedule:
                    schedule.consecutive_failures += 1
                    schedule.updated_at = utcnow()
        db.commit()
    finally:
        if temp_archive and temp_archive.exists():
            temp_archive.unlink(missing_ok=True)
        if final_archive and not completed and backup and backup.status != BackupStatus.completed:
            final_archive.unlink(missing_ok=True)
            final_archive.with_suffix(final_archive.suffix + ".sha256").unlink(missing_ok=True)
        db.close()


def _validate_archive_members(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            member_path = Path(name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise BackupRestoreError("unsafe_archive", f"Unsafe archive member: {name}")
            if member.issym() or member.islnk():
                raise BackupRestoreError("unsafe_archive", f"Archive links are not allowed: {name}")
        try:
            tar.getmember("bedrock/worlds")
        except KeyError:
            try:
                tar.getmember("java/world")
            except KeyError:
                has_world_child = any(
                    m.name.startswith("bedrock/worlds/") or m.name.startswith("java/world")
                    for m in tar.getmembers()
                )
                if not has_world_child:
                    raise BackupRestoreError("invalid_archive", "Archive does not contain a world directory")


def _extract_safely(archive: Path, target: Path) -> None:
    _validate_archive_members(archive)
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            destination = (target / member.name).resolve()
            try:
                destination.relative_to(target_root)
            except ValueError:
                raise BackupRestoreError("unsafe_archive", f"Unsafe archive member: {member.name}")
        tar.extractall(target)


def run_restore_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    restore_tmp: Path | None = None
    rollback_worlds: Path | None = None
    active_worlds: Path | None = None
    server_was_running = False

    try:
        job = db.get(RestoreJob, job_id)
        if not job:
            return
        backup = db.get(Backup, job.backup_id)
        server = db.get(Server, job.server_id)
        if not backup or not server:
            return

        with server_backup_restore_lock(server.id):
            active_backup = db.execute(
                select(BackupJob).where(
                    BackupJob.server_id == server.id,
                    BackupJob.status.in_(ACTIVE_BACKUP_STATUSES),
                )
            ).scalars().first()
            if active_backup:
                raise BackupRestoreError("backup_active", "A backup is already active for this server")

            now = utcnow()
            job.status = RestoreStatus.running
            job.phase = "running"
            job.started_at = now
            job.locked_at = now
            job.heartbeat_at = now
            job.attempts += 1
            db.commit()

            if backup.status != BackupStatus.completed:
                raise BackupRestoreError("backup_not_completed", "Backup is not completed")
            if backup.server_id != server.id:
                raise BackupRestoreError("backup_server_mismatch", "Backup does not belong to this server")

            _update_restore_job(db, job, RestoreStatus.validating_backup, "validating_backup", 5)
            archive = verify_backup_artifact(backup).path
            _validate_archive_members(archive)

            server_dir = _require_server_dir(server)
            active_worlds = _active_world_path(server, server_dir)
            server_was_running = _ORCHESTRATOR.is_running(str(server.id))
            job.server_was_running = server_was_running
            db.commit()

            _update_restore_job(db, job, RestoreStatus.stopping_server, "stopping_server", 15)
            if server_was_running:
                _stop_server(server)
                db.commit()

            runtime_tmp = server_dir / "runtime" / "tmp"
            restore_tmp = runtime_tmp / "restore" / str(job.id)
            _safe_rmtree(restore_tmp)
            restore_tmp.mkdir(parents=True, exist_ok=True)

            _update_restore_job(db, job, RestoreStatus.extracting, "extracting", 25)
            _extract_safely(archive, restore_tmp)

            restored_worlds = restore_tmp / ("java/world" if is_java_flavor(server.flavor) else "bedrock/worlds")
            if not restored_worlds.exists() or not restored_worlds.is_dir():
                raise BackupRestoreError("restored_world_missing", "Extracted backup has no world directory")

            _update_restore_job(db, job, RestoreStatus.validating_restore, "validating_restore", 65)
            rollback_root = runtime_tmp / "rollback" / str(job.id)
            rollback_worlds = rollback_root / active_worlds.name
            _safe_rmtree(rollback_root)
            rollback_root.mkdir(parents=True, exist_ok=True)
            job.rollback_path = str(rollback_worlds)
            db.commit()

            _update_restore_job(db, job, RestoreStatus.swapping, "swapping", 75)
            if active_worlds.exists():
                os.replace(active_worlds, rollback_worlds)
            try:
                os.replace(restored_worlds, active_worlds)
            except Exception:
                if rollback_worlds.exists() and not active_worlds.exists():
                    os.replace(rollback_worlds, active_worlds)
                raise

            config_prefix = "java" if is_java_flavor(server.flavor) else "bedrock"
            for filename in (
                "server.properties",
                "allowlist.json",
                "permissions.json",
                "valid_known_packs.json",
                "world_behavior_packs.json",
                "world_resource_packs.json",
                "eula.txt",
                "ops.json",
                "banned-players.json",
                "banned-ips.json",
                "whitelist.json",
            ):
                restored_file = restore_tmp / config_prefix / filename
                if restored_file.exists() and restored_file.is_file():
                    shutil.copy2(restored_file, server_dir / filename)

            if job.restart_after_restore:
                _update_restore_job(db, job, RestoreStatus.starting_server, "starting_server", 90)
                _start_server(server, db)

            completed_at = utcnow()
            job.status = RestoreStatus.completed
            job.phase = "completed"
            job.progress_percent = 100
            job.completed_at = completed_at
            job.updated_at = completed_at
            db.commit()

            if rollback_worlds and rollback_worlds.exists():
                _safe_rmtree(rollback_worlds.parent)

    except Exception as exc:
        code = getattr(exc, "code", "restore_failed")
        message = getattr(exc, "message", str(exc))

        try:
            if job and job.rollback_enabled and active_worlds and rollback_worlds and rollback_worlds.exists():
                if active_worlds.exists():
                    failed_path = active_worlds.with_name(f"{active_worlds.name}.failed-restore-{job.id}")
                    if failed_path.exists():
                        _safe_rmtree(failed_path)
                    os.replace(active_worlds, failed_path)
                os.replace(rollback_worlds, active_worlds)
                job.status = RestoreStatus.rolled_back
                job.phase = "rolled_back"
            elif job:
                job.status = RestoreStatus.failed
                job.phase = "failed"
        except Exception as rollback_exc:
            code = "rollback_failed"
            message = f"{message}; rollback failed: {rollback_exc}"
            if job:
                job.status = RestoreStatus.failed
                job.phase = "failed"

        if "job" in locals() and job:
            job.error_code = code
            job.error_message = message
            job.completed_at = utcnow()
            job.updated_at = utcnow()
        db.commit()

        if server_was_running and "server" in locals() and server and not _ORCHESTRATOR.is_running(str(server.id)):
            try:
                _start_server(server, db)
                db.commit()
            except Exception as exc:
                logger.warning("Failed to restart server %s after restore failure: %s", server.id, exc)
    finally:
        if restore_tmp:
            _safe_rmtree(restore_tmp)
        db.close()


def apply_backup_retention(db: Session, server_id: uuid.UUID, *, keep_count: int | None = None) -> None:
    settings = get_settings()
    keep = max(1, int(keep_count or settings.backup_retention_count))
    backups = db.execute(
        select(Backup)
        .where(
            Backup.server_id == server_id,
            Backup.status == BackupStatus.completed,
            Backup.deleted_at.is_(None),
            Backup.pinned.is_(False),
        )
        .order_by(Backup.created_at.desc())
    ).scalars().all()

    for backup in backups[keep:]:
        path = backup_object_path(server_id, backup.id)
        checksum_path = path.with_suffix(path.suffix + ".sha256")
        path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        backup.status = BackupStatus.deleted
        backup.deleted_at = utcnow()
    db.commit()


def next_schedule_run(schedule: BackupSchedule, *, from_time=None):
    base = from_time or utcnow()
    if schedule.interval_minutes:
        return base + timedelta(minutes=max(1, schedule.interval_minutes))
    return None


def dispatch_due_backup_schedules() -> int:
    db = SessionLocal()
    try:
        now = utcnow()
        due_schedules = db.execute(
            select(BackupSchedule)
            .where(
                BackupSchedule.enabled.is_(True),
                BackupSchedule.next_run_at <= now,
            )
        ).scalars().all()
        
        dispatched_count = 0
        for schedule in due_schedules:
            server = db.get(Server, schedule.server_id)
            if not server:
                schedule.enabled = False
                continue

            if schedule.skip_if_server_offline:
                is_running = _ORCHESTRATOR.is_running(str(server.id))
                if not is_running:
                    schedule.next_run_at = next_schedule_run(schedule, from_time=now)
                    continue

            if schedule.defer_if_job_active:
                active_job = db.execute(
                    select(BackupJob).where(
                        BackupJob.server_id == server.id,
                        BackupJob.status.in_(ACTIVE_BACKUP_STATUSES)
                    )
                ).scalars().first()
                if active_job:
                    # Defer for a bit (e.g. 5 minutes) to try again
                    schedule.next_run_at = now + timedelta(minutes=5)
                    continue

            world_name = str((server.mc_config or {}).get("level_name") or server.world_name)
            backup = Backup(
                server_id=server.id,
                owner_id=schedule.owner_id,
                created_by_user_id=schedule.owner_id,
                kind=BackupKind.scheduled,
                status=BackupStatus.pending,
                name=f"Automated Backup - {now.strftime('%Y-%m-%d %H:%M:%S')}",
                description="Automatically created by backup schedule",
                world_name=world_name,
                storage_backend=BackupStorageBackend.local,
                storage_key=backup_storage_key(server.id, uuid.uuid4()),
                schedule_id=schedule.id,
            )
            db.add(backup)
            db.flush()
            backup.storage_key = backup_storage_key(server.id, backup.id)

            job = BackupJob(
                backup_id=backup.id,
                server_id=server.id,
                requested_by_user_id=schedule.owner_id,
                status=BackupStatus.pending,
                phase="pending",
                force_offline=False,
            )
            db.add(job)
            
            schedule.last_run_at = now
            schedule.next_run_at = next_schedule_run(schedule, from_time=now)
            db.commit()
            db.refresh(job)

            submit_backup_job(job.id)
            dispatched_count += 1
            
        return dispatched_count
    except Exception as exc:
        logger.error("Error in dispatch_due_backup_schedules: %s", exc)
        return 0
    finally:
        db.close()


def _scheduler_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        dispatch_due_backup_schedules()
        # Sleep for a bit before checking again
        stop_event.wait(60.0)


def run_backup_scheduler_forever(*, stop_event: threading.Event | None = None) -> None:
    logger.info("Backup scheduler is starting")
    evt = stop_event or threading.Event()
    _scheduler_loop(evt)
    logger.info("Backup scheduler stopped")


def start_backup_scheduler_once() -> None:
    global _SCHEDULER_STARTED
    with _SCHEDULER_GUARD:
        if _SCHEDULER_STARTED:
            return
        _SCHEDULER_STARTED = True
    
    t = threading.Thread(target=run_backup_scheduler_forever, name="backup-scheduler", daemon=True)
    t.start()
