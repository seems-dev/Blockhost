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


class ServerConfigOut(BaseModel):
    id: uuid.UUID
    mc_config: BedrockConfig


class ServerConfigUpdateRequest(BaseModel):
    config: BedrockConfig
