from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import bearer_scheme, get_current_user
from blockhost_backend.core.security import TokenError, decode_token
from blockhost_backend.api.schemas import (
    BackupJobOut,
    BackupListOut,
    BackupOut,
    BackupScheduleOut,
    BackupScheduleRequest,
    CreateBackupRequest,
    CreateRestoreRequest,
    DeleteBackupRequest,
    RestoreJobOut,
)
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import (
    Backup,
    BackupJob,
    BackupKind,
    BackupSchedule,
    BackupStatus,
    BackupStorageBackend,
    RestoreJob,
    RestoreStatus,
    Server,
    User,
    utcnow,
)
from blockhost_backend.orchestrator.backup import (
    ACTIVE_BACKUP_STATUSES,
    ACTIVE_RESTORE_STATUSES,
    apply_backup_retention,
    backup_object_path,
    backup_storage_key,
    next_schedule_run,
    submit_backup_job,
    submit_restore_job,
)


router = APIRouter(prefix="/api/servers", tags=["backups"])


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")


def _require_server(server_id: str, user: User, db: Session) -> Server:
    server_uuid = _parse_uuid(server_id)
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _require_backup(server: Server, backup_id: str, user: User, db: Session) -> Backup:
    backup_uuid = _parse_uuid(backup_id)
    backup = db.get(Backup, backup_uuid)
    if not backup or backup.server_id != server.id or backup.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Backup not found")
    return backup


def _backup_to_out(backup: Backup) -> BackupOut:
    return BackupOut(
        backup_id=backup.id,
        server_id=backup.server_id,
        name=backup.name,
        description=backup.description,
        kind=backup.kind,
        status=backup.status,
        world_name=backup.world_name,
        archive_format=backup.archive_format,
        size_bytes=backup.size_bytes,
        uncompressed_size_bytes=backup.uncompressed_size_bytes,
        checksum_sha256=backup.checksum_sha256,
        consistency_method=backup.consistency_method.value if backup.consistency_method else None,
        server_was_running=backup.server_was_running,
        pinned=backup.pinned,
        created_at=backup.created_at,
        completed_at=backup.completed_at,
        failure_code=backup.failure_code,
        failure_message=backup.failure_message,
    )


def _backup_job_to_out(job: BackupJob, *, already_running: bool = False) -> BackupJobOut:
    return BackupJobOut(
        job_id=job.id,
        backup_id=job.backup_id,
        server_id=job.server_id,
        status=job.status,
        phase=job.phase,
        progress_percent=job.progress_percent,
        progress_bytes=job.progress_bytes,
        total_bytes=job.total_bytes,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        already_running=already_running,
    )


def _restore_job_to_out(job: RestoreJob) -> RestoreJobOut:
    return RestoreJobOut(
        restore_job_id=job.id,
        backup_id=job.backup_id,
        server_id=job.server_id,
        status=job.status,
        phase=job.phase,
        progress_percent=job.progress_percent,
        progress_bytes=job.progress_bytes,
        total_bytes=job.total_bytes,
        restart_after_restore=job.restart_after_restore,
        rollback_enabled=job.rollback_enabled,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _schedule_to_out(schedule: BackupSchedule) -> BackupScheduleOut:
    return BackupScheduleOut(
        schedule_id=schedule.id,
        server_id=schedule.server_id,
        enabled=schedule.enabled,
        interval_minutes=schedule.interval_minutes,
        retention_count=schedule.retention_count,
        retention_days=schedule.retention_days,
        skip_if_server_offline=schedule.skip_if_server_offline,
        defer_if_job_active=schedule.defer_if_job_active,
        last_run_at=schedule.last_run_at,
        last_success_at=schedule.last_success_at,
        next_run_at=schedule.next_run_at,
        consecutive_failures=schedule.consecutive_failures,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _active_backup(db: Session, server_id: uuid.UUID) -> BackupJob | None:
    return db.execute(
        select(BackupJob)
        .where(BackupJob.server_id == server_id, BackupJob.status.in_(ACTIVE_BACKUP_STATUSES))
        .order_by(BackupJob.created_at.desc())
    ).scalars().first()


def _active_restore(db: Session, server_id: uuid.UUID) -> RestoreJob | None:
    return db.execute(
        select(RestoreJob)
        .where(RestoreJob.server_id == server_id, RestoreJob.status.in_(ACTIVE_RESTORE_STATUSES))
        .order_by(RestoreJob.created_at.desc())
    ).scalars().first()


@router.get("/{server_id}/backup-schedule", response_model=BackupScheduleOut)
def get_backup_schedule(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupScheduleOut:
    server = _require_server(server_id, user, db)
    schedule = db.execute(
        select(BackupSchedule).where(BackupSchedule.server_id == server.id)
    ).scalars().first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    return _schedule_to_out(schedule)


@router.put("/{server_id}/backup-schedule", response_model=BackupScheduleOut)
def upsert_backup_schedule(
    server_id: str,
    payload: BackupScheduleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupScheduleOut:
    server = _require_server(server_id, user, db)
    now = utcnow()
    schedule = db.execute(
        select(BackupSchedule).where(BackupSchedule.server_id == server.id)
    ).scalars().first()
    if not schedule:
        schedule = BackupSchedule(
            server_id=server.id,
            owner_id=user.id,
            enabled=payload.enabled,
            interval_minutes=payload.interval_minutes,
            retention_count=payload.retention_count,
            retention_days=payload.retention_days,
            skip_if_server_offline=payload.skip_if_server_offline,
            defer_if_job_active=payload.defer_if_job_active,
            next_run_at=now if payload.enabled else None,
        )
        db.add(schedule)
    else:
        schedule.enabled = payload.enabled
        schedule.interval_minutes = payload.interval_minutes
        schedule.retention_count = payload.retention_count
        schedule.retention_days = payload.retention_days
        schedule.skip_if_server_offline = payload.skip_if_server_offline
        schedule.defer_if_job_active = payload.defer_if_job_active
        schedule.updated_at = now
        if payload.enabled and schedule.next_run_at is None:
            schedule.next_run_at = next_schedule_run(schedule, from_time=now)
        if not payload.enabled:
            schedule.next_run_at = None
    db.commit()
    db.refresh(schedule)
    return _schedule_to_out(schedule)


@router.delete("/{server_id}/backup-schedule", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup_schedule(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    server = _require_server(server_id, user, db)
    schedule = db.execute(
        select(BackupSchedule).where(BackupSchedule.server_id == server.id)
    ).scalars().first()
    if not schedule:
        return None
    db.delete(schedule)
    db.commit()
    return None


@router.post("/{server_id}/backups", response_model=BackupJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_backup(
    server_id: str,
    payload: CreateBackupRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupJobOut:
    server = _require_server(server_id, user, db)

    if _active_restore(db, server.id):
        raise HTTPException(status_code=409, detail="Restore already active for this server")

    existing = _active_backup(db, server.id)
    if existing:
        return _backup_job_to_out(existing, already_running=True)

    if payload.idempotency_key:
        idempotent = db.execute(
            select(BackupJob).where(
                BackupJob.server_id == server.id,
                BackupJob.idempotency_key == payload.idempotency_key,
            )
        ).scalars().first()
        if idempotent:
            return _backup_job_to_out(idempotent, already_running=idempotent.status in ACTIVE_BACKUP_STATUSES)

    world_name = str((server.mc_config or {}).get("level_name") or server.world_name)
    backup = Backup(
        server_id=server.id,
        owner_id=user.id,
        created_by_user_id=user.id,
        kind=BackupKind.manual,
        status=BackupStatus.pending,
        name=payload.name,
        description=payload.description,
        world_name=world_name,
        storage_backend=BackupStorageBackend.local,
        storage_key=backup_storage_key(server.id, uuid.uuid4()),
    )
    db.add(backup)
    db.flush()
    backup.storage_key = backup_storage_key(server.id, backup.id)

    job = BackupJob(
        backup_id=backup.id,
        server_id=server.id,
        requested_by_user_id=user.id,
        status=BackupStatus.pending,
        phase="pending",
        idempotency_key=payload.idempotency_key,
        force_offline=payload.force_offline,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    submit_backup_job(job.id)
    return _backup_job_to_out(job)


@router.get("/{server_id}/backups", response_model=BackupListOut)
def list_backups(
    server_id: str,
    status_filter: BackupStatus | None = Query(default=None, alias="status"),
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupListOut:
    server = _require_server(server_id, user, db)
    limit = max(1, min(limit, 100))
    query = select(Backup).where(Backup.server_id == server.id, Backup.deleted_at.is_(None))
    if status_filter:
        query = query.where(Backup.status == status_filter)
    backups = db.execute(query.order_by(Backup.created_at.desc()).limit(limit)).scalars().all()
    return BackupListOut(items=[_backup_to_out(backup) for backup in backups])


@router.get("/{server_id}/backups/{backup_id}", response_model=BackupOut)
def get_backup(
    server_id: str,
    backup_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupOut:
    server = _require_server(server_id, user, db)
    backup = _require_backup(server, backup_id, user, db)
    return _backup_to_out(backup)


def _resolve_user_for_download(
    creds: HTTPAuthorizationCredentials | None,
    token: str | None,
    db: Session,
) -> User:
    if creds is not None:
        return get_current_user(creds=creds, db=db)

    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_token(token, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid access token")

    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.get("/{server_id}/backups/{backup_id}/download")
def download_backup(
    server_id: str,
    backup_id: str,
    token: str | None = Query(default=None),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> FileResponse:
    user = _resolve_user_for_download(creds, token, db)
    server = _require_server(server_id, user, db)
    backup = _require_backup(server, backup_id, user, db)
    if backup.status != BackupStatus.completed:
        raise HTTPException(status_code=409, detail="Backup is not completed")
    path = backup_object_path(server.id, backup.id)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Backup archive is no longer available")
    filename = f"erex-{server.id}-{backup.id}.tar.gz"
    return FileResponse(path=path, filename=filename, media_type="application/gzip")


@router.delete("/{server_id}/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(
    server_id: str,
    backup_id: str,
    payload: DeleteBackupRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    server = _require_server(server_id, user, db)
    backup = _require_backup(server, backup_id, user, db)
    if backup.status in ACTIVE_BACKUP_STATUSES:
        raise HTTPException(status_code=409, detail="Backup job is still active")
    if backup.pinned and not (payload and payload.delete_even_if_pinned):
        raise HTTPException(status_code=409, detail="Backup is pinned")

    path = backup_object_path(server.id, backup.id)
    temp_paths = {
        path.with_suffix(path.suffix + ".tmp"),
        Path(str(path) + ".tmp"),
    }
    checksum_path = path.with_suffix(path.suffix + ".sha256")

    for artifact in (path, checksum_path, *temp_paths):
        artifact.unlink(missing_ok=True)

    parent_dir = path.parent
    if parent_dir.exists() and not any(parent_dir.iterdir()):
        parent_dir.rmdir()
    server_dir = parent_dir.parent
    if server_dir.exists() and not any(server_dir.iterdir()):
        server_dir.rmdir()

    backup.status = BackupStatus.deleted
    backup.deleted_at = utcnow()
    db.commit()
    return None


@router.post("/{server_id}/restores", response_model=RestoreJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_restore(
    server_id: str,
    payload: CreateRestoreRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RestoreJobOut:
    if payload.confirm != "RESTORE":
        raise HTTPException(status_code=400, detail="Restore confirmation is required")

    server = _require_server(server_id, user, db)
    backup = _require_backup(server, str(payload.backup_id), user, db)
    if backup.status != BackupStatus.completed:
        raise HTTPException(status_code=409, detail="Backup is not completed")
    if _active_backup(db, server.id):
        raise HTTPException(status_code=409, detail="Backup already active for this server")
    if _active_restore(db, server.id):
        raise HTTPException(status_code=409, detail="Restore already active for this server")

    job = RestoreJob(
        backup_id=backup.id,
        server_id=server.id,
        requested_by_user_id=user.id,
        status=RestoreStatus.pending,
        phase="pending",
        restart_after_restore=payload.restart_after_restore,
        rollback_enabled=payload.rollback_enabled,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    submit_restore_job(job.id)
    return _restore_job_to_out(job)


@router.get("/{server_id}/backup-jobs/{job_id}", response_model=BackupJobOut)
def get_backup_job(
    server_id: str,
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BackupJobOut:
    server = _require_server(server_id, user, db)
    job_uuid = _parse_uuid(job_id)
    job = db.get(BackupJob, job_uuid)
    if not job or job.server_id != server.id:
        raise HTTPException(status_code=404, detail="Backup job not found")
    return _backup_job_to_out(job)


@router.get("/{server_id}/restore-jobs/{job_id}", response_model=RestoreJobOut)
def get_restore_job(
    server_id: str,
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RestoreJobOut:
    server = _require_server(server_id, user, db)
    job_uuid = _parse_uuid(job_id)
    job = db.get(RestoreJob, job_uuid)
    if not job or job.server_id != server.id:
        raise HTTPException(status_code=404, detail="Restore job not found")
    return _restore_job_to_out(job)


@router.post("/{server_id}/backups/retention/apply", status_code=status.HTTP_204_NO_CONTENT)
def apply_retention_now(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    server = _require_server(server_id, user, db)
    apply_backup_retention(db, server.id)
    return None
