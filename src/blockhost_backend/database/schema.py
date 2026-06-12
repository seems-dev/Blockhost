from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from blockhost_backend.database.types import GUID, JSONType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SubscriptionTier(str, enum.Enum):
    free = "free"
    premium = "premium"




def get_subscription_str(obj) -> str:
    """
    Return the subscription tier name as a plain string.
    Accepts either a `SubscriptionTier` enum or an object (e.g., `User`)
    with a `subscription_tier` attribute.
    """
    if isinstance(obj, SubscriptionTier):
        return obj.value
    tier = getattr(obj, "subscription_tier", None)
    if isinstance(tier, SubscriptionTier):
        return tier.value
    if isinstance(tier, str):
        return tier
    return str(tier)


class ServerState(str, enum.Enum):
    created = "created"
    provisioning = "provisioning"
    running = "running"
    syncing = "syncing"
    suspended = "suspended"


class VMProvider(str, enum.Enum):
    oracle_free = "oracle_free"
    hetzner = "hetzner"
    local_bedrock = "local_bedrock"


class BackupStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    held_saves = "held_saves"
    copying = "copying"
    compressing = "compressing"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    deleting = "deleting"
    deleted = "deleted"
    expired = "expired"


class RestoreStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    validating_backup = "validating_backup"
    stopping_server = "stopping_server"
    extracting = "extracting"
    validating_restore = "validating_restore"
    swapping = "swapping"
    starting_server = "starting_server"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"
    cancelled = "cancelled"


class BackupKind(str, enum.Enum):
    manual = "manual"
    scheduled = "scheduled"
    system = "system"


class BackupStorageBackend(str, enum.Enum):
    local = "local"
    s3 = "s3"


class BackupConsistencyMethod(str, enum.Enum):
    save_hold = "save_hold"
    server_stop = "server_stop"
    filesystem_snapshot = "filesystem_snapshot"

class User(Base):
    __tablename__ = "users"
 
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auth_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    referrer_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)
    blockcoin_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(Enum(SubscriptionTier), nullable=False, default=SubscriptionTier.free)
 
    # --- OAuth fields ---
    google_sub: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
 
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
 
    servers: Mapped[list["Server"]] = relationship(back_populates="owner", cascade="all, delete-orphan")

class Server(Base):
    __tablename__ = "servers"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    world_name: Mapped[str] = mapped_column(String(128), nullable=False)
    join_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    state: Mapped[ServerState] = mapped_column(Enum(ServerState), nullable=False, default=ServerState.created)

    vm_provider: Mapped[VMProvider] = mapped_column(Enum(VMProvider), nullable=False, default=VMProvider.local_bedrock)
    vm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vm_ipv4: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vm_port: Mapped[int] = mapped_column(Integer, nullable=False, default=19132)

    # Minecraft Bedrock server.properties-like config (stored for production orchestration).
    # Setup-stage note: local docker-compose uses a single shared Bedrock container, so this
    # config may not be applied until orchestration is wired up.
    mc_config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creator_earnings_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    world_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backup_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)

    owner: Mapped[User] = relationship(back_populates="servers")


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("servers.id"), index=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("backup_schedules.id"), nullable=True)

    kind: Mapped[BackupKind] = mapped_column(Enum(BackupKind), nullable=False, default=BackupKind.manual)
    status: Mapped[BackupStatus] = mapped_column(Enum(BackupStatus), index=True, nullable=False, default=BackupStatus.pending)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    world_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bedrock_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consistency_method: Mapped[BackupConsistencyMethod | None] = mapped_column(Enum(BackupConsistencyMethod), nullable=True)
    server_was_running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    storage_backend: Mapped[BackupStorageBackend] = mapped_column(
        Enum(BackupStorageBackend), nullable=False, default=BackupStorageBackend.local
    )
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_format: Mapped[str] = mapped_column(String(32), nullable=False, default="tar.gz")
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    uncompressed_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    included_paths: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)

    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    backup_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("backups.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("servers.id"), index=True, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)

    status: Mapped[BackupStatus] = mapped_column(Enum(BackupStatus), index=True, nullable=False, default=BackupStatus.pending)
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    force_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("servers.id"), index=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cron_expression: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    retention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_if_server_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    defer_if_job_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RestoreJob(Base):
    __tablename__ = "restore_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    backup_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("backups.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("servers.id"), index=True, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)

    status: Mapped[RestoreStatus] = mapped_column(Enum(RestoreStatus), index=True, nullable=False, default=RestoreStatus.pending)
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    restart_after_restore: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    server_was_running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollback_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rollback_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BlockcoinReason(str, enum.Enum):
    referral = "referral"
    ad_view = "ad_view"
    daily_task = "daily_task"
    server_hosting = "server_hosting"
    purchase = "purchase"
    admin_adjustment = "admin_adjustment"


class BlockcoinTransaction(Base):
    __tablename__ = "blockcoin_ledger"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[BlockcoinReason] = mapped_column(Enum(BlockcoinReason), nullable=False)
    server_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("servers.id"), nullable=True)
    # "metadata" is reserved by SQLAlchemy's Declarative API, keep DB column name but rename attribute.
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
