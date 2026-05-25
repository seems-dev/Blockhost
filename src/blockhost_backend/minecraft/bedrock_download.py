from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from collections.abc import Callable


@dataclass(frozen=True)
class BedrockDownloadTarget:
    url: str
    sha256: str | None = None


DownloadEventCallback = Callable[[str, dict[str, object]], None]


def _platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()

    is_x64 = machine in {"x86_64", "amd64"}
    if system.startswith("linux") and is_x64:
        return "linux_x86_64"
    if system.startswith("windows") and is_x64:
        return "windows_x86_64"
    if system.startswith("darwin") and is_x64:
        # Bedrock dedicated server binaries are generally distributed for Windows/Linux.
        return "macos_x86_64"
    return f"{system}_{machine}"


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Bedrock versions manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or name.endswith("/"):
                continue
            # Prevent zip-slip
            out_path = (dest_dir / name).resolve()
            if not str(out_path).startswith(str(dest_dir.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe path in zip: {name}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_download_target(*, manifest_path: Path, version: str) -> BedrockDownloadTarget:
    manifest = _read_manifest(manifest_path)
    if version.strip().lower() in {"recommended", "default"}:
        version = str(manifest.get("recommended_version") or "").strip()
        if not version:
            raise KeyError("Manifest missing `recommended_version`")
    versions = (manifest or {}).get("versions") or {}
    entry = versions.get(version)
    if not isinstance(entry, dict):
        raise KeyError(f"Version not found in manifest: {version}")

    plat = _platform_key()
    target = entry.get(plat)
    if not isinstance(target, dict) or not target.get("url"):
        raise KeyError(f"No download target for version={version} platform={plat}")

    sha256 = target.get("sha256") or None
    url = str(target["url"]).strip()
    if not url or url.startswith("REPLACE_WITH_"):
        raise ValueError(f"Manifest URL not configured for version={version} platform={plat}")
    return BedrockDownloadTarget(url=url, sha256=str(sha256).strip() or None)


def recommended_version(*, manifest_path: Path) -> str:
    manifest = _read_manifest(manifest_path)
    version = str(manifest.get("recommended_version") or "").strip()
    if not version:
        raise KeyError("Manifest missing `recommended_version`")
    return version


def available_versions(*, manifest_path: Path) -> list[str]:
    manifest = _read_manifest(manifest_path)
    versions = (manifest.get("versions") or {}) if isinstance(manifest, dict) else {}
    keys = [k for k in versions.keys() if isinstance(k, str) and k.strip()]
    return sorted(keys)


def ensure_version_installed(
    *,
    versions_dir: Path,
    manifest_path: Path,
    version: str,
    progress_cb: Callable[[int], None] | None = None,
    event_cb: DownloadEventCallback | None = None,
) -> Path:
    """
    Ensure `versions/<version>/` exists by downloading and extracting an official zip.
    """
    version = version.strip()
    if not version:
        raise ValueError("Version is required")

    dest = versions_dir / version
    if dest.exists() and dest.is_dir():
        return dest

    target = resolve_download_target(manifest_path=manifest_path, version=version)
    versions_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="blockhost-bedrock-") as td:
        tmpdir = Path(td)
        zip_path = tmpdir / f"bedrock-{version}.zip"

        if event_cb:
            event_cb("download_start", {"version": version, "url": target.url})

        # Force HTTP/1.1. Some minecraft.net download endpoints can be flaky over HTTP/2
        # on certain networks/proxies.
        #
        # Use a short "first byte" read timeout to fail fast when the server never starts
        # sending data, then increase it once data starts flowing.
        timeout = httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=30.0)
        transport = httpx.HTTPTransport(retries=3)
        headers = {"User-Agent": "BlockHost/0.1 (+https://blockhost.local)"}
        with httpx.Client(
            http2=False,
            follow_redirects=True,
            timeout=timeout,
            transport=transport,
            headers=headers,
        ) as client:
            try:
                with client.stream("GET", target.url) as res:
                    if event_cb:
                        event_cb(
                            "response_headers",
                            {
                                "status_code": res.status_code,
                                "final_url": str(res.url),
                                "content_length": res.headers.get("content-length"),
                                "content_type": res.headers.get("content-type"),
                            },
                        )
                    res.raise_for_status()
                    downloaded = 0
                    with open(zip_path, "wb") as f:
                        for chunk in res.iter_bytes():
                            if downloaded == 0 and len(chunk) > 0:
                                # Once the stream is flowing, allow long gaps while downloading.
                                client.timeout = httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0)
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb:
                                progress_cb(downloaded)
            except Exception as e:
                if event_cb:
                    event_cb(
                        "download_error",
                        {
                            "error_type": type(e).__name__,
                            "error": str(e),
                        },
                    )
                raise

        if target.sha256:
            actual = _sha256_file(zip_path)
            if actual.lower() != target.sha256.lower():
                raise RuntimeError(f"Checksum mismatch for {version} (expected={target.sha256}, actual={actual})")

        extract_dir = tmpdir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(zip_path, extract_dir)

        # Basic sanity check: extracted content should contain a Bedrock binary somewhere at top-level.
        has_binary = any((extract_dir / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
        if not has_binary:
            # Some zips include an outer folder; allow exactly one nesting level.
            children = [p for p in extract_dir.iterdir() if p.is_dir()]
            if len(children) == 1:
                inner = children[0]
                has_binary = any((inner / name).exists() for name in ("bedrock_server", "bedrock_server.exe"))
                if has_binary:
                    extract_dir = inner
        if not has_binary:
            raise RuntimeError("Downloaded zip did not contain bedrock_server/bedrock_server.exe at expected location")

        # Atomic-ish install: move into place.
        tmp_install = versions_dir / f".tmp-{version}"
        if tmp_install.exists():
            shutil.rmtree(tmp_install)
        shutil.move(str(extract_dir), str(tmp_install))
        tmp_install.rename(dest)

    if event_cb:
        with contextlib.suppress(Exception):
            event_cb("install_done", {"version": version, "path": str(dest)})
    return dest
