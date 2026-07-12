from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from blockhost_backend.database.schema import ServerFlavor

router = APIRouter(prefix="/api/software", tags=["software"])

@router.get("/flavors")
def list_flavors() -> list[dict]:
    """Returns available server flavors for the UI."""
    return [
        {"id": ServerFlavor.JAVA_VANILLA.value, "name": "Vanilla", "type": "java", "icon": "grass"},
        {"id": ServerFlavor.PAPER.value, "name": "Paper", "type": "java", "icon": "paper"},
        {"id": ServerFlavor.PURPUR.value, "name": "Purpur", "type": "java", "icon": "purpur"},
        {"id": ServerFlavor.FABRIC.value, "name": "Fabric", "type": "java", "icon": "fabric"},
        {"id": ServerFlavor.FORGE.value, "name": "Forge", "type": "java", "icon": "forge"},
        {"id": ServerFlavor.NEOFORGE.value, "name": "NeoForge", "type": "java", "icon": "neoforge"},
        {"id": ServerFlavor.BEDROCK.value, "name": "Bedrock", "type": "bedrock", "icon": "bedrock"},
    ]

@router.get("/{flavor}/versions")
def list_versions(flavor: str) -> list[str]:
    """Fetches valid versions dynamically from upstream APIs."""
    try:
        flavor_enum = ServerFlavor(flavor)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid flavor")

    if flavor_enum == ServerFlavor.BEDROCK:
        # Return some common bedrock versions or fetch from manifest
        return ["1.21.44", "1.21.43", "1.21.42", "1.21.41", "1.21.40"]

    # Java flavors
    if flavor_enum == ServerFlavor.JAVA_VANILLA:
        url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
        resp = httpx.get(url)
        return [v["id"] for v in resp.json().get("versions", []) if v["type"] == "release"][:20] # Last 20 releases

    if flavor_enum in (ServerFlavor.PAPER, ServerFlavor.PURPUR):
        if flavor_enum == ServerFlavor.PAPER:
            resp = httpx.get("https://api.papermc.io/v2/projects/paper", timeout=15.0)
            resp.raise_for_status()
            return resp.json().get("versions", [])[-20:]
        resp = httpx.get("https://api.purpurmc.org/v2/purpur", timeout=15.0)
        resp.raise_for_status()
        return resp.json().get("versions", [])[-20:]

    return [] # Fabric/Forge can be added later