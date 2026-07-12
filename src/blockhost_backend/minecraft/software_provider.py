from __future__ import annotations

import logging
from pathlib import Path

import httpx

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import ServerFlavor

logger = logging.getLogger(__name__)

class SoftwareProvider:
    name: str = "base"

    def get_download_url(self, mc_version: str, build: str | None = None) -> str:
        raise NotImplementedError

def download_file(url: str, dest_path: Path) -> None:
    """Download a file to a specific path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s to %s", url, dest_path.name)
    with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)

class VanillaProvider(SoftwareProvider):
    name = "vanilla"

    def get_download_url(self, mc_version: str, **kwargs) -> str:
        # Mojang version manifest
        manifest_url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
        resp = httpx.get(manifest_url)
        resp.raise_for_status()
        
        for v in resp.json().get("versions", []):
            if v["id"] == mc_version:
                return v["url"]
                
        raise ValueError(f"Vanilla version {mc_version} not found in Mojang manifest.")

    def get_jar_url_from_version_json(self, version_json_url: str) -> str:
        resp = httpx.get(version_json_url)
        resp.raise_for_status()
        return resp.json()["downloads"]["server"]["url"]

class PaperProvider(SoftwareProvider):
    name = "paper"

    def get_download_url(self, mc_version: str, build: str | None = None) -> str:
        api_url = f"https://fill.papermc.io/v3/projects/paper/versions/{mc_version}/builds"
        
        resp = httpx.get(api_url)
        if resp.status_code == 404:
            raise ValueError(f"Paper version {mc_version} not found.")
        resp.raise_for_status()
        
        builds = resp.json()
        if not builds:
            raise ValueError(f"No Paper builds found for {mc_version}")
            
        if build:
            target_build = next((b for b in builds if str(b["id"]) == str(build)), None)
            if not target_build:
                raise ValueError(f"Specific Paper build {build} not found.")
        else:
            stable_builds = [b for b in builds if b.get("channel") == "STABLE"]
            target_build = stable_builds[-1] if stable_builds else builds[-1]
            
        return target_build["downloads"]["server:default"]["url"]

class PurpurProvider(SoftwareProvider):
    name = "purpur"
    # Purpur API is identical to PaperMC API but uses project "purpur"
    def get_download_url(self, mc_version: str, build: str | None = None) -> str:
        api_url = f"https://api.purpurmc.org/v2/purpur/{mc_version}"
        resp = httpx.get(api_url)
        if resp.status_code == 404:
            raise ValueError(f"Purpur version {mc_version} not found.")
        resp.raise_for_status()
        
        data = resp.json()
        builds = data.get("builds", {"all": []}).get("all", [])
        if not builds:
            raise ValueError(f"No Purpur builds found for {mc_version}")
            
        target_build = builds[-1] if not build else next((b for b in builds if b == int(build)), None)
        if not target_build:
            raise ValueError(f"Specific Purpur build {build} not found.")
            
        return f"https://api.purpurmc.org/v2/purpur/{mc_version}/{target_build}/download"

_PROVIDER_REGISTRY: dict[ServerFlavor, SoftwareProvider] = {
    ServerFlavor.JAVA_VANILLA: VanillaProvider(),
    ServerFlavor.PAPER: PaperProvider(),
    ServerFlavor.PURPUR: PurpurProvider(),
}

def get_provider(flavor: ServerFlavor) -> SoftwareProvider:
    provider = _PROVIDER_REGISTRY.get(flavor)
    if not provider:
        raise NotImplementedError(f"No software provider implemented for {flavor.value}")
    return provider

def resolve_jar_url(flavor: ServerFlavor, mc_version: str) -> str:
    """Helper to get the direct JAR download URL."""
    provider = get_provider(flavor)
    url = provider.get_download_url(mc_version)
    
    # Vanilla requires a second API call to get the actual JAR link
    if isinstance(provider, VanillaProvider):
        url = provider.get_jar_url_from_version_json(url)
        
    return url