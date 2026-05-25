from __future__ import annotations

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.orchestrator.orchestrator import MinecraftEndpoint, Orchestrator


def get_orchestrator() -> Orchestrator:
    settings = get_settings()
    return Orchestrator(minecraft_host=settings.minecraft_public_host, minecraft_port=settings.minecraft_port)


def get_minecraft_endpoint() -> MinecraftEndpoint:
    return get_orchestrator().get_endpoint()
