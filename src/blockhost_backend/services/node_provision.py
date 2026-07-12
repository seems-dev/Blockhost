"""Two-phase remote node provisioning: version binary once, config sync per start."""

from __future__ import annotations

import hashlib
import os
import stat as _stat
import tarfile
import tempfile
from pathlib import Path

import httpx

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import Node, Server

# Per-server files synced on every start (small).
_CONFIG_FILES = ("server.properties", "allowlist.json", "permissions.json")

# Multipart field names on the agent sync-config endpoint.
_CONFIG_FIELD_NAMES = {
    "server.properties": "server_properties",
    "allowlist.json": "allowlist_json",
    "permissions.json": "permissions_json",
}

_BINARY_NAMES = ("bedrock_server", "bedrock_server.exe")

# Directories excluded from version binary upload (server-specific / grow over time).
_VERSION_EXCLUDE_DIRS = frozenset({"worlds"})


def version_provision_hash(version_dir: Path, version_name: str) -> str:
    """Fast hash: sha256 of bedrock_server binary, else sha256 of version string."""
    for name in _BINARY_NAMES:
        bin_path = version_dir / name
        if bin_path.is_file():
            return hashlib.sha256(bin_path.resolve().read_bytes()).hexdigest()
    return hashlib.sha256(version_name.encode()).hexdigest()


def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = tarinfo.name.split("/")
    if parts and parts[0] in _VERSION_EXCLUDE_DIRS:
        return None
    if tarinfo.type not in (tarfile.REGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE):
        return None
    if _stat.S_ISFIFO(tarinfo.mode) or _stat.S_ISSOCK(tarinfo.mode):
        return None
    return tarinfo


def _agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().worker_agent_token}"}


def _agent_base(node: Node) -> str:
    return f"http://{node.ip_address}:{node.agent_port}"


def ensure_version_on_node(*, node: Node, version_name: str, version_dir: Path) -> None:
    """Upload version template to agent only when binary hash differs."""
    content_hash = version_provision_hash(version_dir, version_name)
    base = _agent_base(node)
    headers = _agent_headers()

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{base}/agent/versions/{version_name}", headers=headers)
        if resp.status_code == 200:
            remote = resp.json()
            if remote.get("hash") == content_hash:
                return

        fd, path = tempfile.mkstemp(suffix=".tar.gz")
        os.close(fd)
        try:
            # dereference=True: expand symlinks in the controller version dir into real files on the agent.
            with tarfile.open(path, "w:gz", dereference=True) as tar:
                tar.add(version_dir, arcname=".", filter=_tar_filter)
            with open(path, "rb") as f:
                resp = client.post(
                    f"{base}/agent/versions/{version_name}/provision",
                    headers=headers,
                    files={"file": ("version.tar.gz", f, "application/gzip")},
                    data={"hash": content_hash},
                    timeout=300.0,
                )
                resp.raise_for_status()
        finally:
            if os.path.exists(path):
                os.unlink(path)


def sync_server_config(*, node: Node, server: Server, server_dir: Path) -> None:
    """Upload config files as multipart form fields (no tar, no dereference)."""
    base = _agent_base(node)
    headers = _agent_headers()
    files: list[tuple[str, tuple[str, object, str]]] = []

    for filename in _CONFIG_FILES:
        cfg_path = server_dir / filename
        if not cfg_path.is_file():
            continue
        field = _CONFIG_FIELD_NAMES[filename]
        files.append(
            (field, (filename, cfg_path.open("rb"), "application/octet-stream"))
        )

    if not files:
        return

    try:
        resp = httpx.post(
            f"{base}/agent/servers/{server.id}/sync-config",
            headers=headers,
            files=files,
            timeout=60.0,
        )
        resp.raise_for_status()
    finally:
        for _, (_, fh, _) in files:
            if hasattr(fh, "close"):
                fh.close()


def provision_remote_server(
    *,
    server: Server,
    server_dir: Path,
    node: Node,
    version_name: str,
    version_dir: Path,
) -> None:
    """Two-phase: ensure version binary on node, then sync config only."""
    ensure_version_on_node(node=node, version_name=version_name, version_dir=version_dir)
    sync_server_config(node=node, server=server, server_dir=server_dir)


def provision_remote_java_server(*, server: Server, server_dir: Path, node: Node) -> None:
    """Upload a Java server directory (jar + config) to the agent."""
    base = _agent_base(node)
    headers = _agent_headers()
    fd, path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(path, "w:gz") as tar:
            for item in server_dir.iterdir():
                if item.name in {"stdin.fifo", "logs"}:
                    continue
                tar.add(item, arcname=item.name)
        with open(path, "rb") as f:
            resp = httpx.post(
                f"{base}/agent/servers/{server.id}/provision",
                headers=headers,
                files={"file": ("server.tar.gz", f, "application/gzip")},
                timeout=600.0,
            )
            resp.raise_for_status()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def sync_remote_java_config(*, server: Server, server_dir: Path, node: Node) -> None:
    """Sync Java server.properties to the agent without re-uploading the JAR."""
    sync_server_config(node=node, server=server, server_dir=server_dir)


# ---------------------------------------------------------------------------
# Mod management helpers (Modrinth)
# ---------------------------------------------------------------------------

def download_mods_on_agent(
    *,
    node: Node,
    server_id: str,
    files: list[dict],
    subdir: str,
) -> None:
    """
    Tell the agent to download mod/plugin JARs directly from Modrinth CDN.

    ``files`` is a list of dicts with keys: url, filename, subdir.
    The agent streams each file to disk; the backend never touches the bytes.
    """
    base = _agent_base(node)
    headers = _agent_headers()

    # Inject subdir into each file entry (agent validates it server-side too)
    enriched = [dict(f, subdir=subdir) for f in files]
    payload = {"files": enriched}

    resp = httpx.post(
        f"{base}/agent/servers/{server_id}/mods/download",
        headers=headers,
        json=payload,
        timeout=300.0,  # Large mod packs can take time; use streaming on agent side
    )
    resp.raise_for_status()


def list_mods_on_agent(
    *,
    node: Node,
    server_id: str,
    subdir: str,
) -> list[str]:
    """
    Return the list of installed mod/plugin filenames from the agent's disk.

    Used both for display (GET /mods) and dedup checks before install.
    """
    base = _agent_base(node)
    resp = httpx.get(
        f"{base}/agent/servers/{server_id}/mods",
        headers=_agent_headers(),
        params={"subdir": subdir},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("files", [])


def delete_mod_on_agent(
    *,
    node: Node,
    server_id: str,
    filename: str,
    subdir: str,
) -> None:
    """Delete a single mod/plugin JAR from the agent's disk."""
    base = _agent_base(node)
    resp = httpx.delete(
        f"{base}/agent/servers/{server_id}/mods/{filename}",
        headers=_agent_headers(),
        params={"subdir": subdir},
        timeout=30.0,
    )
    resp.raise_for_status()
