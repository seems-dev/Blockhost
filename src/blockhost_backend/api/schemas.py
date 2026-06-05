from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from blockhost_backend.database.schema import BackupKind, BackupStatus, RestoreStatus, ServerState, SubscriptionTier, VMProvider


class BedrockConfig(BaseModel):
    # Mirrors common `server.properties` keys supported by popular Bedrock server containers.
    # `bedrock_image` allows pinning a per-server container image/tag (version).
    bedrock_image: str | None = Field(default=None, max_length=128)
    # For itzg/minecraft-bedrock-server, set the server version to download at container start.
    # Examples: "LATEST" (default), "PREVIEW", or a specific version like "1.20.1".
    bedrock_version: str | None = Field(default=None, max_length=32)
    server_name: str | None = Field(default=None, max_length=64)
    gamemode: str | None = None  # survival/creative/adventure/spectator
    difficulty: str | None = None  # peaceful/easy/normal/hard
    max_players: int | None = Field(default=None, ge=1, le=200)
    allow_cheats: bool | None = None
    online_mode: bool | None = None
    level_name: str | None = Field(default=None, max_length=64)
    level_seed: str | None = Field(default=None, max_length=64)


class SignupRequest(BaseModel):
    email: EmailStr
    phone: str | None = None
    password: str = Field(min_length=8, max_length=128)
    nickname: str = Field(min_length=1, max_length=64)
    referrer_code: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    nickname: str
    referrer_code: str
    blockcoin_balance: int
    subscription_tier: SubscriptionTier


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class CreateServerRequest(BaseModel):
    world_name: str = Field(min_length=1, max_length=128)
    tier: SubscriptionTier = SubscriptionTier.free
    config: BedrockConfig | None = None


class ServerOut(BaseModel):
    id: uuid.UUID
    world_name: str
    join_code: str
    state: ServerState
    vm_provider: VMProvider
    vm_ipv4: str | None
    vm_port: int
    shareable_address: str | None
    owner_id: uuid.UUID
    owner_nickname: str
    created_at: datetime
    last_activity: datetime
    mc_config: dict


class ServerDetail(ServerOut):
    minecraft_host: str
    minecraft_port: int


class ServerActionResponse(BaseModel):
    id: uuid.UUID
    state: ServerState


class BedrockServerStats(BaseModel):
    host: str
    port: int
    reachable: bool
    latency_ms: int | None = None
    edition: str | None = None
    motd: str | None = None
    motd2: str | None = None
    protocol: int | None = None
    version: str | None = None
    players_online: int | None = None
    players_max: int | None = None
    server_id: int | None = None
    gamemode: str | None = None
    online_players_list: list[str] = Field(default_factory=list)
    cpu_usage: float | None = None
    ram_usage_mb: float | None = None
    allocated_ram_mb: int | None = None
    allocated_cpu_cores: float | None = None
    cpu_usage_percent: float | None = None
    ram_usage_percent: float | None = None
    process_running: bool | None = None
    uptime_seconds: int | None = None


class ServerStateSnapshot(BaseModel):
    server: ServerDetail
    config: BedrockConfig
    stats: BedrockServerStats | None = None


class CommandRequest(BaseModel):
    player: str | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    grant: bool | None = None
    mode: str | None = None
    value: str | None = None
    weather: str | None = None
    message: str | None = None



class ServerConfigOut(BaseModel):
    id: uuid.UUID
    mc_config: BedrockConfig


class ServerConfigUpdateRequest(BaseModel):
    config: BedrockConfig


class CreateBackupRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=128)
    force_offline: bool = False


class BackupJobOut(BaseModel):
    job_id: uuid.UUID
    backup_id: uuid.UUID
    server_id: uuid.UUID
    status: BackupStatus
    phase: str
    progress_percent: int
    progress_bytes: int
    total_bytes: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    already_running: bool = False


class BackupOut(BaseModel):
    backup_id: uuid.UUID
    server_id: uuid.UUID
    name: str | None = None
    description: str | None = None
    kind: BackupKind
    status: BackupStatus
    world_name: str
    archive_format: str
    size_bytes: int
    uncompressed_size_bytes: int
    checksum_sha256: str | None = None
    consistency_method: str | None = None
    server_was_running: bool
    pinned: bool
    created_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class BackupListOut(BaseModel):
    items: list[BackupOut]


class DeleteBackupRequest(BaseModel):
    delete_even_if_pinned: bool = False


class CreateRestoreRequest(BaseModel):
    backup_id: uuid.UUID
    restart_after_restore: bool = True
    rollback_enabled: bool = True
    confirm: str = Field(min_length=1, max_length=32)


class RestoreJobOut(BaseModel):
    restore_job_id: uuid.UUID
    backup_id: uuid.UUID
    server_id: uuid.UUID
    status: RestoreStatus
    phase: str
    progress_percent: int
    progress_bytes: int
    total_bytes: int
    restart_after_restore: bool
    rollback_enabled: bool
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BackupScheduleRequest(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(ge=5, le=60 * 24 * 30)
    retention_count: int = Field(default=7, ge=1, le=100)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    skip_if_server_offline: bool = False
    defer_if_job_active: bool = True


class BackupScheduleOut(BaseModel):
    schedule_id: uuid.UUID
    server_id: uuid.UUID
    enabled: bool
    interval_minutes: int | None
    retention_count: int
    retention_days: int | None
    skip_if_server_offline: bool
    defer_if_job_active: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    next_run_at: datetime | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime
