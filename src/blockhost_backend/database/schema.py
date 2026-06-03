from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
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
