from __future__ import annotations

import logging
import re
from functools import lru_cache

import httpx

from blockhost_backend.database.schema import ServerFlavor

logger = logging.getLogger(__name__)

_SUPPORTED_JAVA_FLAVORS = frozenset(
    {ServerFlavor.JAVA_VANILLA, ServerFlavor.PAPER, ServerFlavor.PURPUR}
)

# Offline-friendly protocol ids for Server List Ping (Minecraft Java).
_KNOWN_PROTOCOL_VERSIONS: dict[str, int] = {
    "1.18": 757,
    "1.18.1": 757,
    "1.18.2": 758,
    "1.19": 759,
    "1.19.1": 760,
    "1.19.2": 760,
    "1.19.3": 761,
    "1.19.4": 762,
    "1.20": 763,
    "1.20.1": 763,
    "1.20.2": 764,
    "1.20.3": 765,
    "1.20.4": 765,
    "1.20.5": 766,
    "1.20.6": 766,
    "1.21": 767,
    "1.21.1": 767,
    "1.21.2": 768,
    "1.21.3": 768,
    "1.21.4": 769,
    "1.21.5": 770,
}


def is_java_flavor(flavor: ServerFlavor) -> bool:
    return flavor != ServerFlavor.BEDROCK


def is_supported_java_flavor(flavor: ServerFlavor) -> bool:
    return flavor in _SUPPORTED_JAVA_FLAVORS


def required_java_version(mc_version: str) -> int:
    """Map a Minecraft release to the minimum supported Temurin major version."""
    parts: list[int] = []
    for part in mc_version.split(".")[:3]:
        if part.isdigit():
            parts.append(int(part))
    while len(parts) < 3:
        parts.append(0)
    major, minor, _patch = parts
    if major > 1 or (major == 1 and minor >= 21):
        return 21
    if major == 1 and minor >= 18:
        return 17
    if major == 1 and minor == 17:
        return 16
    return 8


def _normalize_mc_version(mc_version: str) -> str:
    return mc_version.strip().lstrip("v")


def _protocol_from_local_table(mc_version: str) -> int | None:
    version = _normalize_mc_version(mc_version)
    if version in _KNOWN_PROTOCOL_VERSIONS:
        return _KNOWN_PROTOCOL_VERSIONS[version]

    # Match prefixes like 1.20.1-rc1 -> 1.20.1
    match = re.match(r"^(\d+\.\d+(?:\.\d+)?)", version)
    if match and match.group(1) in _KNOWN_PROTOCOL_VERSIONS:
        return _KNOWN_PROTOCOL_VERSIONS[match.group(1)]

    return None


def _protocol_from_remote(mc_version: str) -> int | None:
    manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    resp = httpx.get(manifest_url, timeout=5.0)
    resp.raise_for_status()
    version_url = None
    normalized = _normalize_mc_version(mc_version)
    for entry in resp.json().get("versions", []):
        if entry["id"] == normalized:
            version_url = entry["url"]
            break
    if not version_url:
        return None
    vresp = httpx.get(version_url, timeout=5.0)
    vresp.raise_for_status()
    protocol = vresp.json().get("protocol_version")
    return int(protocol) if protocol is not None else None


@lru_cache(maxsize=128)
def get_protocol_version(mc_version: str) -> int:
    """Resolve the Minecraft protocol id for Server List Ping."""
    local = _protocol_from_local_table(mc_version)
    if local is not None:
        return local

    try:
        remote = _protocol_from_remote(mc_version)
        if remote is not None:
            return remote
    except Exception as exc:
        logger.debug(
            "Could not fetch protocol version for %s from Mojang (%s); using fallback",
            mc_version,
            exc,
        )

    logger.debug("Protocol version unknown for %s; using fallback 765", mc_version)
    return 765
