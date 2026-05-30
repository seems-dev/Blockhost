from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from blockhost_backend.database.schema import ServerState, SubscriptionTier, VMProvider


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


class GoogleLoginRequest(BaseModel):
    id_token: str


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
    subdomain: str  # e.g., "srv-abc123xyz"
    state: ServerState
    vm_provider: VMProvider
    vm_ipv4: str | None
    vm_port: int
    owner_id: uuid.UUID
    owner_nickname: str
    created_at: datetime
    last_activity: datetime
    mc_config: dict
    shareable_address: str  # e.g., "srv-abc123xyz.blockhost.com:19132"


class ServerDetail(ServerOut):
    minecraft_host: str
    minecraft_port: int


class ServerActionResponse(BaseModel):
    id: uuid.UUID
    state: ServerState


class BedrockLogEventOut(BaseModel):
    raw: str
    level: str | None = None
    event_type: str | None = None
    player_name: str | None = None


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
    # Live stats from stdout parsing + process sampling
    online_players: list[str] = []
    tps: float | None = None
    tick_ms: float | None = None
    cpu_usage: float | None = None
    ram_usage_mb: float | None = None
    uptime_seconds: float | None = None
    log_lines_total: int = 0
    last_log_line: str | None = None
    last_event: str | None = None
    recent_events: list[BedrockLogEventOut] = []
    process_running: bool = False


class ServerConfigOut(BaseModel):
    id: uuid.UUID
    mc_config: BedrockConfig


class ServerConfigUpdateRequest(BaseModel):
    config: BedrockConfig


class PlayerActionRequest(BaseModel):
    player: str = Field(min_length=1, max_length=64)


class TeleportRequest(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    x: float
    y: float
    z: float


class OpRequest(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    grant: bool = True


class GamemodeRequest(BaseModel):
    player: str = Field(min_length=1, max_length=64)
    mode: str = Field(pattern=r"^(survival|creative|adventure|spectator)$")


class TimeRequest(BaseModel):
    value: str = Field(min_length=1, max_length=32)


class WeatherRequest(BaseModel):
    weather: str = Field(pattern=r"^(clear|rain|thunder)$")


class SayRequest(BaseModel):
    message: str = Field(min_length=1, max_length=256)


class ServerCommandResponse(BaseModel):
    ok: bool
    command: str


class BlocklistOut(BaseModel):
    players: list[str]
