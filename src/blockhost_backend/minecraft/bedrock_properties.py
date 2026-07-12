from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
#bedrock_properties.py

@dataclass(frozen=True)
class BedrockServerProperties:
    server_name: str
    gamemode: str | None = None
    difficulty: str | None = None
    max_players: int | None = None
    allow_cheats: bool | None = None
    online_mode: bool | None = False
    level_name: str | None = None
    level_seed: str | None = None
    enable_lan_visibility: bool = False
    server_port: int = 19132


def write_server_properties(path: Path, props: BedrockServerProperties) -> None:
    """
    Write a Bedrock `server.properties` file.

    We intentionally keep this minimal and compatible with the dedicated server binary.
    """
    lines: list[str] = []

    def put(key: str, value: object | None) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            lines.append(f"{key}={'true' if value else 'false'}")
        else:
            lines.append(f"{key}={value}")

    put("server-name", props.server_name)
    put("gamemode", props.gamemode)
    put("difficulty", props.difficulty)
    put("max-players", props.max_players)
    put("allow-cheats", props.allow_cheats)
    put("online-mode", props.online_mode)
    put("level-name", props.level_name)
    put("level-seed", props.level_seed)

    # Bedrock uses UDP. Set both IPv4 and IPv6 port keys.
    ipv4_port = int(props.server_port)
    ipv6_port = ipv4_port + 1

    # Keep IPv4 and IPv6 ports distinct so two servers never share a UDP port.
    if ipv6_port == ipv4_port:
        ipv6_port = ipv4_port + 1

    put("server-port", ipv4_port)
    put("server-portv6", ipv6_port)
    put("enable-lan-visibility", props.enable_lan_visibility)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
