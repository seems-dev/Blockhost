from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import threading
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import (
    Backup,
    BackupConsistencyMethod,
    BackupJob,
    BackupKind,
    BackupSchedule,
    BackupStorageBackend,
    BackupStatus,
    RestoreJob,
    RestoreStatus,
    Server,
    SubscriptionTier,
    User,
    utcnow,
)
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator


_ORCHESTRATOR = get_server_lifecycle_orchestrator()
_LOCKS_GUARD = threading.Lock()
_SERVER_LOCKS: dict[str, threading.Lock] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, get_settings().backup_worker_threads), thread_name_prefix="backup-worker")
_SCHEDULER_STARTED = False
_SCHEDULER_GUARD = threading.Lock()

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


def _server_lock(server_id: uuid.UUID | str) -> threading.Lock:
    key = str(server_id)
    with _LOCKS_GUARD:
        lock = _SERVER_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SERVER_LOCKS[key] = lock
        return lock


@contextmanager
def server_backup_restore_lock(server_id: uuid.UUID | str):
    lock = _server_lock(server_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def backup_storage_root() -> Path:
    root = Path(get_settings().backup_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def backup_tmp_root() -> Path:
    root = Path(get_settings().backup_temp_dir).resolve()
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
    servers_root = Path(settings.bedrock_servers_dir).resolve()
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


def _included_backup_paths(server_dir: Path, world_name: str) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    world_path = server_dir / "worlds" / world_name
    worlds_path = server_dir / "worlds"

    if world_path.exists():
        candidates.append((world_path, f"bedrock/worlds/{world_name}"))
    elif worlds_path.exists():
        candidates.append((worlds_path, "bedrock/worlds"))
    else:
        raise BackupRestoreError("world_missing", "No worlds directory found for this server")

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


def _copy_with_progress(src: Path, dst: Path, job: BackupJob, db: Session, copied_so_far: int) -> int:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_so_far += src.stat().st_size
        _update_backup_job(db, job, BackupStatus.copying, "copying", 10, 45, copied_so_far)
        return copied_so_far

    for child in src.rglob("*"):
        if child.is_symlink():
            continue
        rel = child.relative_to(src)
        target = dst / rel
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)
        copied_so_far += child.stat().st_size
        _update_backup_job(db, job, BackupStatus.copying, "copying", 10, 45, copied_so_far)
    return copied_so_far


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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _atomic_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _stop_server(server: Server) -> None:
    _ORCHESTRATOR.stop_server(server)


def _get_user_tier(user: User) -> SubscriptionTier:
    tier = getattr(user, "subscription_tier", None)
    if isinstance(tier, SubscriptionTier):
        return tier
    if isinstance(tier, str):
        try:
            return SubscriptionTier(tier)
        except ValueError:
            pass
    return SubscriptionTier.free


def _start_server(server: Server, db: Session) -> None:
    settings = get_settings()
    servers_dir = Path(settings.bedrock_servers_dir).resolve()
    _ORCHESTRATOR.start_server(
        server=server,
        settings=settings,
        servers_dir=servers_dir,
        get_user_tier=_get_user_tier,
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
    save_held = False
    server_was_running = False
    stopped_for_backup = False
    staging: Path | None = None
    temp_archive: Path | None = None

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
            includes = _included_backup_paths(server_dir, world_name)
            total = _size_bytes(path for path, _arcname in includes)
            job.total_bytes = total
            backup.uncompressed_size_bytes = total
            db.commit()

            staging = backup_tmp_root() / str(server.id) / "staging" / str(job.id)
            _safe_rmtree(staging)
            staging.mkdir(parents=True, exist_ok=True)

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

            copied = 0
            for source, archive_name in includes:
                copied = _copy_with_progress(source, staging / archive_name, job, db, copied)

            if save_held:
                _save_resume_best_effort(str(server.id))
                save_held = False
            elif stopped_for_backup:
                _start_server(server, db)
                stopped_for_backup = False
                db.commit()

            if not (staging / "bedrock" / "worlds").exists():
                raise BackupRestoreError("invalid_backup_contents", "Backup staging directory has no worlds content")

            backup.consistency_method = consistency_method
            backup.included_paths = [archive_name for _source, archive_name in includes]
            backup.world_name = world_name
            backup.bedrock_version = str((server.mc_config or {}).get("template_version") or "")
            db.commit()

            _update_backup_job(db, job, BackupStatus.compressing, "compressing", 45, 80)
            final_archive = backup_object_path(server.id, backup.id)
            temp_archive = final_archive.with_suffix(final_archive.suffix + ".tmp")
            if temp_archive.exists():
                temp_archive.unlink()

            with tarfile.open(temp_archive, "w:gz") as tar:
                tar.add(staging / "bedrock", arcname="bedrock", recursive=True)

            _update_backup_job(db, job, BackupStatus.verifying, "verifying", 85, 95)
            checksum = _sha256_file(temp_archive)
            size = temp_archive.stat().st_size
            _atomic_replace(temp_archive, final_archive)
            final_archive.with_suffix(final_archive.suffix + ".sha256").write_text(checksum, encoding="utf-8")

            completed_at = utcnow()
            backup.status = BackupStatus.completed
            backup.storage_key = backup_storage_key(server.id, backup.id)
            backup.checksum_sha256 = checksum
            backup.size_bytes = size
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
            except Exception:
                pass

        code = getattr(exc, "code", "backup_failed")
        message = getattr(exc, "message", str(exc))
        if "job" in locals() and job:
            job.status = BackupStatus.failed
            job.phase = "failed"
            job.error_code = code
            job.error_message = message
            job.completed_at = utcnow()
            job.updated_at = utcnow()
        if "backup" in locals() and backup:
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
        if staging:
            _safe_rmtree(staging)
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
            has_world_child = any(m.name.startswith("bedrock/worlds/") for m in tar.getmembers())
            if not has_world_child:
                raise BackupRestoreError("invalid_archive", "Archive does not contain bedrock/worlds")


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

            archive = backup_object_path(server.id, backup.id)
            if not archive.exists():
                raise BackupRestoreError("backup_object_missing", "Backup archive is missing from storage")

            _update_restore_job(db, job, RestoreStatus.validating_backup, "validating_backup", 5)
            if backup.checksum_sha256 and _sha256_file(archive) != backup.checksum_sha256:
                raise BackupRestoreError("checksum_mismatch", "Backup checksum verification failed")
            _validate_archive_members(archive)

            server_dir = _require_server_dir(server)
            active_worlds = server_dir / "worlds"
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

            restored_worlds = restore_tmp / "bedrock" / "worlds"
            if not restored_worlds.exists() or not restored_worlds.is_dir():
                raise BackupRestoreError("restored_world_missing", "Extracted backup has no worlds directory")

            _update_restore_job(db, job, RestoreStatus.validating_restore, "validating_restore", 65)
            rollback_root = runtime_tmp / "rollback" / str(job.id)
            rollback_worlds = rollback_root / "worlds"
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

            for filename in (
                "server.properties",
                "allowlist.json",
                "permissions.json",
                "valid_known_packs.json",
                "world_behavior_packs.json",
                "world_resource_packs.json",
            ):
                restored_file = restore_tmp / "bedrock" / filename
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
            except Exception:
                pass
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
    dispatched = 0
    try:
        now = utcnow()
        schedules = db.execute(
            select(BackupSchedule).where(
                BackupSchedule.enabled.is_(True),
                (BackupSchedule.next_run_at.is_(None)) | (BackupSchedule.next_run_at <= now),
            )
        ).scalars().all()

        for schedule in schedules:
            server = db.get(Server, schedule.server_id)
            if not server:
                schedule.enabled = False
                schedule.updated_at = now
                continue

            active_backup = db.execute(
                select(BackupJob).where(
                    BackupJob.server_id == server.id,
                    BackupJob.status.in_(ACTIVE_BACKUP_STATUSES),
                )
            ).scalars().first()
            active_restore = db.execute(
                select(RestoreJob).where(
                    RestoreJob.server_id == server.id,
                    RestoreJob.status.in_(ACTIVE_RESTORE_STATUSES),
                )
            ).scalars().first()
            if active_backup or active_restore:
                schedule.next_run_at = next_schedule_run(schedule, from_time=now)
                schedule.updated_at = now
                continue

            if schedule.skip_if_server_offline and not _ORCHESTRATOR.is_running(str(server.id)):
                schedule.last_run_at = now
                schedule.next_run_at = next_schedule_run(schedule, from_time=now)
                schedule.updated_at = now
                continue

            backup = Backup(
                server_id=server.id,
                owner_id=server.owner_id,
                created_by_user_id=None,
                schedule_id=schedule.id,
                kind=BackupKind.scheduled,
                status=BackupStatus.pending,
                world_name=_world_name(server),
                storage_backend=BackupStorageBackend.local,
            )
            db.add(backup)
            db.flush()
            backup.storage_key = backup_storage_key(server.id, backup.id)

            job = BackupJob(
                backup_id=backup.id,
                server_id=server.id,
                requested_by_user_id=None,
                status=BackupStatus.pending,
                phase="pending",
                idempotency_key=f"schedule:{schedule.id}:{now.isoformat()}",
            )
            db.add(job)
            schedule.last_run_at = now
            schedule.next_run_at = next_schedule_run(schedule, from_time=now)
            schedule.updated_at = now
            db.commit()

            submit_backup_job(job.id)
            dispatched += 1
    finally:
        db.commit()
        db.close()
    return dispatched


def _scheduler_loop() -> None:
    settings = get_settings()
    poll_seconds = max(5, settings.backup_scheduler_poll_seconds)
    while True:
        try:
            dispatch_due_backup_schedules()
        except Exception:
            pass
        time.sleep(poll_seconds)


def start_backup_scheduler_once() -> None:
    global _SCHEDULER_STARTED
    settings = get_settings()
    if not settings.backup_scheduler_enabled:
        return
    with _SCHEDULER_GUARD:
        if _SCHEDULER_STARTED:
            return
        thread = threading.Thread(target=_scheduler_loop, name="backup-scheduler", daemon=True)
        thread.start()
        _SCHEDULER_STARTED = True
