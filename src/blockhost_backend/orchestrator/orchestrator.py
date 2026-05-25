from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MinecraftEndpoint:
    host: str
    port: int


class Orchestrator:
    """
    Setup-stage orchestrator.

    In production, this will provision VMs (Oracle/Hetzner) and manage lifecycle.
    For local development we assume a Bedrock server is already running (e.g. via docker-compose).
    """

    def __init__(self, *, minecraft_host: str, minecraft_port: int) -> None:
        self._endpoint = MinecraftEndpoint(host=minecraft_host, port=minecraft_port)

    def get_endpoint(self) -> MinecraftEndpoint:
        return self._endpoint
