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
from collections.abc import Callable

import httpx


@dataclass(frozen=True)
class BedrockDownloadTarget:
    url: str
    sha256: str | None = None


DownloadEventCallback = Callable[[str, dict[str, object]], None]


def list_remote_versions() -> list[str]:
    """Fetch available Linux Bedrock versions from MCJarFiles."""
    url = "https://mcjarfiles.com/api/get-versions/bedrock/latest/linux"
    try:
        res = httpx.get(url, timeout=10.0)
        res.raise_for_status()
        versions = res.json()
        if not isinstance(versions, list):
            return []
        return [str(v) for v in versions]
    except Exception as e:
        print(f"Error fetching Bedrock versions: {e}")
        return []

def get_download_url(version: str) -> str:
    """Get the download URL for a specific Linux Bedrock version."""
    if version.lower() in {"recommended", "latest", "default"}:
        versions = list_remote_versions()
        if not versions:
            raise RuntimeError("Failed to fetch Bedrock versions")
        version = versions[0]
        
    return f"https://mcjarfiles.com/api/get-jar/bedrock/latest/linux/{version}"

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()

def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():

            name = info.filename

            if not name or name.endswith("/"):
                continue

            out_path = (dest_dir / name).resolve()

            # Prevent zip-slip vulnerability
            if not str(out_path).startswith(str(dest_dir.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe path in zip: {name}")

            out_path.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def ensure_version_installed(
    *,
    versions_dir: Path,
    manifest_path: Path,  # Kept for compatibility, though unused
    version: str,
    progress_cb: Callable[[int], None] | None = None,
    event_cb: DownloadEventCallback | None = None,
) -> Path:

    version = version.strip()

    if not version:
        raise ValueError("Version required")

    dest = versions_dir / version

    # Already installed
    if dest.exists() and dest.is_dir():
        return dest

    target_url = get_download_url(version)

    versions_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bedrock-download-") as td:

        tmpdir = Path(td)

        zip_path = tmpdir / f"bedrock-{version}.zip"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }

        if event_cb:
            event_cb(
                "download_start",
                {
                    "version": version,
                    "url": target_url,
                },
            )

        print(f"Downloading from: {target_url}")

        # Download ZIP
        with httpx.stream(
            "GET",
            target_url,
            follow_redirects=True,
            headers=headers,
            timeout=600.0,
        ) as res:

            print("HTTP Status:", res.status_code)

            res.raise_for_status()

            total = int(res.headers.get("content-length", 0))
            downloaded = 0

            with open(zip_path, "wb") as f:

                for chunk in res.iter_bytes(
                    chunk_size=1024 * 1024
                ):

                    if not chunk:
                        continue

                    f.write(chunk)

                    downloaded += len(chunk)

                    if progress_cb:
                        progress_cb(downloaded)

                    if total:
                        percent = downloaded * 100 / total

                        print(
                            f"Downloading {version}: "
                            f"{percent:.1f}%"
                        )

        print("Extracting ZIP...")

        extract_dir = tmpdir / "extract"

        safe_extract_zip(zip_path, extract_dir)

        # Check for server binary
        has_binary = any(
            (extract_dir / name).exists()
            for name in (
                "bedrock_server",
                "bedrock_server.exe",
            )
        )

        # Some zips contain outer folder
        if not has_binary:

            children = [
                p for p in extract_dir.iterdir()
                if p.is_dir()
            ]

            if len(children) == 1:

                inner = children[0]

                has_binary = any(
                    (inner / name).exists()
                    for name in (
                        "bedrock_server",
                        "bedrock_server.exe",
                    )
                )

                if has_binary:
                    extract_dir = inner

        if not has_binary:
            raise RuntimeError(
                "Downloaded ZIP does not contain "
                "bedrock_server binary"
            )

        # Atomic install
        tmp_install = versions_dir / f".tmp-{version}"

        if tmp_install.exists():
            shutil.rmtree(tmp_install)

        shutil.move(
            str(extract_dir),
            str(tmp_install),
        )

        tmp_install.rename(dest)

    if event_cb:
        with contextlib.suppress(Exception):
            event_cb(
                "install_done",
                {
                    "version": version,
                    "path": str(dest),
                },
            )

    print("Install complete!")

    return dest


# Example usage
if __name__ == "__main__":

    install_path = ensure_version_installed(
        versions_dir=Path("./versions"),
        manifest_path=Path("./bedrock_versions.json"),
        version="recommended",
    )

    print("Installed to:", install_path)