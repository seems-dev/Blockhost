from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BillingSubscriptionStatus(str, enum.Enum):
    active = "active"
    grace_period = "grace_period"
    suspended = "suspended"
    cancelled = "cancelled"


class BillingTransactionStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"


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
    local_java = "local_java"


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


class BlockcoinReason(str, enum.Enum):
    referral = "referral"
    ad_view = "ad_view"
    daily_task = "daily_task"
    server_hosting = "server_hosting"
    purchase = "purchase"
    admin_adjustment = "admin_adjustment"


class NodeState(str, enum.Enum):
    online = "online"
    offline = "offline"
    draining = "draining"


class ServerFlavor(str, enum.Enum):
    BEDROCK = "bedrock"
    JAVA_VANILLA = "java_vanilla"
    PAPER = "paper"
    PURPUR = "purpur"
    FABRIC = "fabric"
    FORGE = "forge"
    NEOFORGE = "neoforge"



class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auth_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    referrer_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    referred_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    blockcoin_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- OAuth fields ---
    google_sub: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verification_otp_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    servers: Mapped[list["Server"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    jti: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (
        UniqueConstraint("node_id", "vm_port", name="uq_servers_node_port"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("nodes.id"),
        index=True,
        nullable=True,
    )

    node: Mapped["Node"] = relationship(back_populates="servers")
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    world_name: Mapped[str] = mapped_column(String(128), nullable=False)
    join_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    state: Mapped[ServerState] = mapped_column(Enum(ServerState), nullable=False, default=ServerState.created)

    vm_provider: Mapped[VMProvider] = mapped_column(Enum(VMProvider), nullable=False, default=VMProvider.local_bedrock)
    vm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vm_ipv4: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vm_port: Mapped[int] = mapped_column(Integer, nullable=False, default=19132)

    mc_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creator_earnings_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    world_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backup_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)

    owner: Mapped[User] = relationship(back_populates="servers")
    bans: Mapped[list["Ban"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    collaborators: Mapped[list["ServerCollaborator"]] = relationship(back_populates="server", cascade="all, delete-orphan")
    flavor: Mapped[ServerFlavor] = mapped_column(Enum(ServerFlavor), default=ServerFlavor.BEDROCK)
    mc_version: Mapped[str | None] = mapped_column(String(32)) # e.g., "1.21.4"
    java_version: Mapped[int | None] = mapped_column(Integer, nullable=True) # e.g., 21


class ServerCollaborator(Base):
    __tablename__ = "server_collaborators"
    __table_args__ = (
        UniqueConstraint("server_id", "user_id", name="uq_server_collaborators_server_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    
    # List of string permissions, e.g. ["start_stop", "console", "files", "config"]
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    server: Mapped["Server"] = relationship(back_populates="collaborators")
    user: Mapped["User"] = relationship()


class BillingPlan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    player_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subscriptions: Mapped[list["BillingSubscription"]] = relationship(back_populates="plan")


class BillingSubscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("plans.id"), index=True, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[BillingSubscriptionStatus] = mapped_column(
        Enum(BillingSubscriptionStatus), index=True, nullable=False, default=BillingSubscriptionStatus.active
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    plan: Mapped[BillingPlan] = relationship(back_populates="subscriptions")


class BillingTransaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("subscriptions.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[BillingTransactionStatus] = mapped_column(
        Enum(BillingTransactionStatus), index=True, nullable=False, default=BillingTransactionStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("plans.id"), nullable=False)

    subscription: Mapped["BillingSubscription | None"] = relationship(
        "BillingSubscription",
        foreign_keys=[subscription_id],
        lazy="select",
    )


class BillingAuditLog(Base):
    __tablename__ = "billing_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("transactions.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Ban(Base):
    __tablename__ = "bans"
    __table_args__ = (
        UniqueConstraint("server_id", "xuid", "active", name="uq_bans_server_xuid_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    xuid: Mapped[str] = mapped_column(String(64), nullable=False)
    player_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unbanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    unbanned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)

    server: Mapped["Server"] = relationship(back_populates="bans")


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("backup_schedules.id"), nullable=True)

    kind: Mapped[BackupKind] = mapped_column(Enum(BackupKind), nullable=False, default=BackupKind.manual)
    status: Mapped[BackupStatus] = mapped_column(Enum(BackupStatus), index=True, nullable=False, default=BackupStatus.pending)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    world_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bedrock_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mc_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    included_paths: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backup_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("backups.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)

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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)

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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backup_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("backups.id"), index=True, nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("servers.id"), index=True, nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

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


class BlockcoinTransaction(Base):
    __tablename__ = "blockcoin_ledger"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[BlockcoinReason] = mapped_column(Enum(BlockcoinReason), nullable=False)
    server_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("servers.id"), nullable=True)
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_port: Mapped[int] = mapped_column(Integer, nullable=False, default=9000)

    status: Mapped[NodeState] = mapped_column(Enum(NodeState), nullable=False, default=NodeState.offline)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    total_ram_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_ram_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpu_cores: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cpu_usage_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    servers: Mapped[list["Server"]] = relationship(back_populates="node")


class RuntimeBinary(Base):
    __tablename__ = "runtime_binaries"
    __table_args__ = (
        UniqueConstraint("flavor", "version", name="uq_runtime_binaries_flavor_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    game: Mapped[str] = mapped_column(String(64), nullable=False, default="minecraft")
    edition: Mapped[str] = mapped_column(String(64), nullable=False)
    flavor: Mapped[ServerFlavor] = mapped_column(Enum(ServerFlavor), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    executable_path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    installed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
