from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class JavaServerProperties:
    server_port: int = 25565
    max_players: int = 20
    gamemode: str = "survival"
    difficulty: str = "normal"
    online_mode: bool = False
    level_name: str = "world"
    level_seed: str = ""
    motd: str = "A Minecraft Server"
    # Java-specific:
    enable_rcon: bool = False
    spawn_protection: int = 0
    view_distance: int = 10
    simulation_distance: int = 10
    enable_command_block: bool = False
    allow_flight: bool = True
    white_list: bool = False
    enforce_whitelist: bool = False
    max_tick_time: int = 60000
    network_compression_threshold: int = 256
    prevent_proxy_connections: bool = False


def set_java_server_property(path: Path, key: str, value: object) -> None:
    """Update or append a single Java server property in-place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    seen = False

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            new_lines.append(line)
            continue
        if line.split("=", 1)[0].strip() == key:
            if isinstance(value, bool):
                new_lines.append(f"{key}={'true' if value else 'false'}")
            else:
                new_lines.append(f"{key}={value}")
            seen = True
        else:
            new_lines.append(line)

    if not seen:
        if isinstance(value, bool):
            new_lines.append(f"{key}={'true' if value else 'false'}")
        else:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def write_java_server_properties(path: Path, props: JavaServerProperties) -> None:
    """
    Write a Java `server.properties` file.
    """
    lines: list[str] = []

    def put(key: str, value: object | None) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            lines.append(f"{key}={'true' if value else 'false'}")
        else:
            lines.append(f"{key}={value}")

    put("server-port", props.server_port)
    put("max-players", props.max_players)
    put("gamemode", props.gamemode)
    put("difficulty", props.difficulty)
    put("online-mode", props.online_mode)
    put("level-name", props.level_name)
    put("level-seed", props.level_seed)
    put("motd", props.motd)
    
    put("enable-rcon", props.enable_rcon)
    put("spawn-protection", props.spawn_protection)
    put("view-distance", props.view_distance)
    put("simulation-distance", props.simulation_distance)
    put("enable-command-block", props.enable_command_block)
    put("allow-flight", props.allow_flight)
    put("white-list", props.white_list)
    put("enforce-whitelist", props.enforce_whitelist)
    put("max-tick-time", props.max_tick_time)
    put("network-compression-threshold", props.network_compression_threshold)
    put("prevent-proxy-connections", props.prevent_proxy_connections)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
