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
    import shutil
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_path = path.with_suffix(".properties.bak")
        shutil.copy2(path, backup_path)

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def read_properties(path: Path) -> dict[str, str | bool | int]:
    """Parse a properties file into a dictionary."""
    if not path.exists():
        return {}

    props = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            
            # Simple type coercion
            if val.lower() == "true":
                props[key] = True
            elif val.lower() == "false":
                props[key] = False
            elif val.isdigit():
                props[key] = int(val)
            else:
                props[key] = val
    return props


def set_bedrock_server_property(path: Path, key: str, value: object) -> None:
    """Update or append a single Bedrock server property in-place."""
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

    import shutil
    import os

    if path.exists():
        backup_path = path.with_suffix(".properties.bak")
        shutil.copy2(path, backup_path)

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
