"""
mods.py — Backend mod management router for Modrinth integration.

Endpoints (all under /api/servers/{server_id}/mods):
  GET  /capability           → What kind of mods does this server support?
  GET  /search?q=...         → Faceted Modrinth search (server-context-aware)
  POST /install              → Resolve + download a mod + its dependencies
  GET  /                     → List installed mods (from Agent disk)
  DELETE /{filename}         → Remove a single mod (from Agent disk)

Design:
  - Backend is the "brain": authenticates, resolves, orchestrates.
  - Agent is the "muscle": downloads CDN URLs, writes to disk.
  - Backend never proxies .jar bytes. Zero memory pressure for large mods.
  - Redis lock on install prevents duplicate concurrent installs per server.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import (
    ModCapabilityResponse,
    ModInstallRequest,
    ModInstallResponse,
    ModListResponse,
    ModProjectDetailsResponse,
    ModSearchResponse,
    ModSearchResult,
    ModVersionSchema,
)
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Node, Server, ServerFlavor, ServerState
from blockhost_backend.minecraft.modrinth_compat import get_loader_profile, is_mod_capable
from blockhost_backend.services.api_cache import get_api_cache
from blockhost_backend.services.modrinth_client import (
    ModrinthUnavailableError,
    ModVersionNotFoundError,
    get_project,
    get_project_versions,
    resolve_install_files,
    search_mods,
)
from blockhost_backend.services.node_provision import (
    delete_mod_on_agent,
    download_mods_on_agent,
    list_mods_on_agent,
)
from blockhost_backend.config.config_manager import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers", tags=["mods"])

# Redis lock TTL: prevents double-install from rapid consecutive clicks
_INSTALL_LOCK_TTL = 300  # seconds

# Allowlist of valid filename extensions (defence-in-depth)
_ALLOWED_EXTENSIONS = {".jar"}

# For local servers (no node_id), the agent is always on localhost
_LOCAL_AGENT_PORT = 8001


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_owned_server(server_id: str, user_id: uuid.UUID, db: Session) -> Server:
    """Fetch and ownership-check a server. Raises 404/403 on failure."""
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")

    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _require_mod_support(server: Server) -> None:
    """Raise a structured 400 if the server flavor doesn't support mods."""
    profile = get_loader_profile(server.flavor)
    if not profile.supported:
        error_code = "coming_soon" if profile.coming_soon else "mods_not_supported"
        raise HTTPException(
            status_code=400,
            detail={
                "error": error_code,
                "flavor": server.flavor.value,
                "reason": profile.unsupported_reason,
            },
        )


def _require_mc_version(server: Server) -> str:
    """Raise 400 if the server has no mc_version set (shouldn't happen for Java)."""
    if not server.mc_version:
        raise HTTPException(
            status_code=400,
            detail="Server has no mc_version; cannot filter Modrinth results",
        )
    return server.mc_version


def _get_node_for_server(server: Server, db: Session) -> Node | None:
    """Return the Node record for a remote server, or None for local."""
    if server.node_id:
        return db.get(Node, server.node_id)
    return None


class _AgentProxy:
    """
    Thin wrapper that calls agent mod endpoints.

    For remote servers: delegates to node_provision helpers (via the Node record).
    For local servers: calls localhost:8001 directly using the same helpers,
    by constructing a fake Node-like object.
    """

    def __init__(self, server: Server, node: Node | None) -> None:
        self._server_id = str(server.id)
        self._node = node or self._local_node()

    @staticmethod
    def _local_node():
        """Construct a minimal Node-like object pointing at localhost for local servers.

        We use SimpleNamespace instead of Node.__new__(Node) because SQLAlchemy
        ORM instances require _sa_instance_state (set by __init__) — bypassing
        __init__ via __new__ causes AttributeError on the first attribute set.
        node_provision helpers only access .ip_address and .agent_port, so
        a simple namespace is a perfect duck-type substitute.
        """
        from types import SimpleNamespace
        return SimpleNamespace(ip_address="192.168.29.102", agent_port=_LOCAL_AGENT_PORT)

    def list(self, subdir: str) -> list[str]:
        try:
            return list_mods_on_agent(
                node=self._node, server_id=self._server_id, subdir=subdir
            )
        except httpx.HTTPError as exc:
            logger.warning("Agent list_mods failed for %s: %s", self._server_id, exc)
            return []  # Treat as empty — don't crash the install flow

    def download(self, files: list[dict], subdir: str) -> None:
        try:
            download_mods_on_agent(
                node=self._node,
                server_id=self._server_id,
                files=files,
                subdir=subdir,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Agent download_mods failed for %s: %s — %s",
                self._server_id, exc, exc.response.text[:200],
            )
            raise HTTPException(
                status_code=502,
                detail=f"Agent failed to download mods: {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            logger.error("Agent download_mods unreachable for %s: %s", self._server_id, exc)
            raise HTTPException(status_code=502, detail="Agent is unreachable")

    def delete(self, filename: str, subdir: str) -> None:
        try:
            delete_mod_on_agent(
                node=self._node,
                server_id=self._server_id,
                filename=filename,
                subdir=subdir,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Mod not found on agent")
            raise HTTPException(status_code=502, detail="Agent error during deletion")
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Agent is unreachable")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{server_id}/mods/capability", response_model=ModCapabilityResponse)
def get_mod_capability(
    server_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModCapabilityResponse:
    """
    Return whether mod management is supported for this server.

    Flutter uses this to decide whether to show the Mods tab and what label
    to display (Mods vs Plugins vs Coming Soon).
    """
    server = _get_owned_server(server_id, user.id, db)
    profile = get_loader_profile(server.flavor)
    return ModCapabilityResponse(
        supported=profile.supported,
        coming_soon=profile.coming_soon,
        install_subdir=profile.install_subdir,
        loaders=profile.loaders,
        reason=profile.unsupported_reason or None,
    )


@router.get("/{server_id}/mods/search", response_model=ModSearchResponse)
async def search_server_mods(
    server_id: str,
    q: str = Query(default="", max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModSearchResponse:
    """
    Search Modrinth with the server's exact flavor and MC version as facets.

    The user literally cannot see a mod that is incompatible with their server.
    Results are Redis-cached for 60 seconds.
    """
    server = _get_owned_server(server_id, user.id, db)
    _require_mod_support(server)
    mc_version = _require_mc_version(server)

    profile = get_loader_profile(server.flavor)

    try:
        hits, total = await search_mods(
            query=q,
            loaders=profile.loaders,
            project_types=profile.project_types,
            game_version=mc_version,
            limit=limit,
            offset=offset,
        )
    except ModrinthUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return ModSearchResponse(
        hits=[
            ModSearchResult(
                project_id=h.project_id,
                slug=h.slug,
                title=h.title,
                description=h.description,
                icon_url=h.icon_url,
                downloads=h.downloads,
                categories=h.categories,
                latest_version=h.latest_version,
            )
            for h in hits
        ],
        total_hits=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/{server_id}/mods/install",
    response_model=ModInstallResponse,
    status_code=status.HTTP_200_OK,
)
async def install_mod(
    server_id: str,
    payload: ModInstallRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModInstallResponse:
    """
    Install a Modrinth mod (and its required dependencies) onto the server.

    Flow:
      1. Validate server ownership and mod support.
      2. Acquire a per-server Redis lock (prevent concurrent duplicate installs).
      3. Ask the Agent what's already installed (for dedup).
      4. Resolve the dependency graph via Modrinth API.
      5. Tell the Agent to download only the new files, direct from CDN.
      6. Return the result to Flutter.

    The backend never downloads .jar bytes. Memory usage is O(1) regardless
    of mod pack size.
    """
    server = _get_owned_server(server_id, user.id, db)
    _require_mod_support(server)
    mc_version = _require_mc_version(server)
    profile = get_loader_profile(server.flavor)
    node = _get_node_for_server(server, db)
    agent = _AgentProxy(server, node)

    # ── Distributed lock ──────────────────────────────────────────────────
    cache = get_api_cache()
    lock = cache.lock(f"lock:mods:install:{server_id}", timeout=_INSTALL_LOCK_TTL)
    acquired = lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=409,
            detail="A mod install is already in progress for this server. Please wait.",
        )

    try:
        # ── Check what's already on disk ──────────────────────────────────
        already_installed = agent.list(profile.install_subdir)

        # ── Resolve dependency graph ───────────────────────────────────────
        try:
            to_download, already_present = await resolve_install_files(
                project_id=payload.modrinth_project_id,
                loaders=profile.loaders,
                game_version=mc_version,
                already_installed_filenames=already_installed,
                root_version_id=payload.version_id or None,
            )
        except ModVersionNotFoundError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "no_compatible_version",
                    "reason": str(exc),
                },
            )
        except ModrinthUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        if not to_download and not already_present:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "no_files_found",
                    "reason": "Modrinth returned no downloadable files for this project.",
                },
            )

        # ── Tell Agent to download ─────────────────────────────────────────
        if to_download:
            file_list = [f.to_dict() for f in to_download]
            agent.download(file_list, profile.install_subdir)

    finally:
        try:
            lock.release()
        except Exception:
            pass

    return ModInstallResponse(
        installed=[f.filename for f in to_download],
        already_present=already_present,
        requires_restart=True,
        install_subdir=profile.install_subdir,
    )


@router.get("/{server_id}/mods/project/{project_id}", response_model=ModProjectDetailsResponse)
async def get_mod_project_details(
    server_id: str,
    project_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModProjectDetailsResponse:
    """
    Fetch mod project metadata and all compatible versions.

    Used by the Flutter Mod Detail screen to show description,
    changelog, and a version picker.
    """
    server = _get_owned_server(server_id, user.id, db)
    _require_mod_support(server)
    mc_version = _require_mc_version(server)
    profile = get_loader_profile(server.flavor)

    try:
        project, versions = await asyncio.gather(
            get_project(project_id),
            get_project_versions(
                project_id=project_id,
                loaders=profile.loaders,
                game_version=mc_version,
            ),
        )
    except ModrinthUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return ModProjectDetailsResponse(
        project_id=project.get("id", project_id),
        slug=project.get("slug", ""),
        title=project.get("title", ""),
        description=project.get("description", ""),
        body=project.get("body", ""),
        icon_url=project.get("icon_url"),
        downloads=project.get("downloads", 0),
        categories=project.get("categories", []),
        versions=[
            ModVersionSchema(
                id=v["id"],
                name=v.get("name", v["id"]),
                version_number=v.get("version_number", ""),
                date_published=v.get("date_published", ""),
                downloads=v.get("downloads", 0),
            )
            for v in versions
        ],
    )


@router.get("/{server_id}/mods", response_model=ModListResponse)
def list_installed_mods(
    server_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModListResponse:
    """
    List all installed mod/plugin JARs from the Agent's disk.

    Works for any supported flavor — returns correct subdir in response
    so Flutter knows the display label ("Mods" vs "Plugins").
    """
    server = _get_owned_server(server_id, user.id, db)
    _require_mod_support(server)
    profile = get_loader_profile(server.flavor)
    node = _get_node_for_server(server, db)
    agent = _AgentProxy(server, node)

    files = agent.list(profile.install_subdir)

    return ModListResponse(
        files=sorted(files),
        install_subdir=profile.install_subdir,
        flavor=server.flavor.value,
    )


@router.delete(
    "/{server_id}/mods/{filename}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_installed_mod(
    server_id: str,
    filename: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """
    Remove a single mod/plugin JAR from the server.

    Validates:
      - Server ownership
      - Flavor supports mods
      - Filename is a safe .jar name (no path traversal)
    """
    server = _get_owned_server(server_id, user.id, db)
    _require_mod_support(server)

    # Security: strip path components and validate extension
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not any(safe_name.endswith(ext) for ext in _ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Only .jar files can be removed")

    profile = get_loader_profile(server.flavor)
    node = _get_node_for_server(server, db)
    agent = _AgentProxy(server, node)
    agent.delete(safe_name, profile.install_subdir)
