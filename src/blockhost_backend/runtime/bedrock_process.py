from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload
from blockhost_backend.minecraft.port_alloc import is_udp_port_free

#bedrock_process.py
_COMMON_BEDROCK_BINARIES = ("bedrock_server", "bedrock_server.exe")



_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")


def resolve_version_dir(*, versions_dir: Path, requested: str | None) -> tuple[str, Path]:
    """FIXED: Better error messages."""
    versions_dir.mkdir(parents=True, exist_ok=True)

    if requested and requested.strip() and requested.strip().upper() not in {"LATEST", "PREVIEW"}:
        ver = requested.strip()
        path = versions_dir / ver
        if not path.exists() or not path.is_dir():
            available = [d.name for d in versions_dir.iterdir() if d.is_dir()]
            raise FileNotFoundError(
                f"Requested Bedrock version template not found: {path}\n"
                f"Available versions: {available if available else 'NONE'}"
            )
        return ver, path

    # Pick "latest" by semver sort (fallback to lexical).
    candidates: list[tuple[tuple[int, int, int] | None, str]] = []
    for child in versions_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        m = _SEMVER_RE.match(name)
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        candidates.append((key, name))
    
    if not candidates:
        raise FileNotFoundError(
            f"No Bedrock versions found in {versions_dir}\n"
            f"Expected structure: {versions_dir}/<version>/bedrock_server\n"
            f"Available directories: {list(versions_dir.iterdir())}"
        )

    candidates.sort(key=lambda item: (item[0] is None, item[0] or (0, 0, 0), item[1]))
    chosen = candidates[-1][1]
    return chosen, versions_dir / chosen


def materialize_server_dir(*, version_dir: Path, servers_dir: Path, server_id: str) -> Path:
    """FIXED: Better error handling and cleanup."""
    servers_dir.mkdir(parents=True, exist_ok=True)
    server_dir = servers_dir / server_id
    
    if server_dir.exists():
        # If it already exists, that's OK - return it
        return server_dir
    
    if not version_dir.exists():
        raise FileNotFoundError(f"Version directory does not exist: {version_dir}")
    
    try:
        shutil.copytree(version_dir, server_dir)
    except Exception as e:
        # Clean up partial copy if something went wrong
        if server_dir.exists():
            shutil.rmtree(server_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to copy version template to {server_dir}: {e}")
    
    return server_dir