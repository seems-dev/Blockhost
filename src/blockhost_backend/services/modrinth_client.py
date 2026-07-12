"""
modrinth_client.py — Async Modrinth API v2 client with dependency resolution.

Architecture:
  - Uses a shared httpx.AsyncClient (connection pooling, keep-alive).
  - Search results are cached in Redis via the existing api_cache service (60s TTL).
  - Dependency graph is resolved via BFS, visiting each project_id at most once.
  - Only "required" dependency types are followed (optional/incompatible/embedded skipped).
  - On Modrinth 429 (rate limit), backs off for 1 second and retries once.

Security:
  - Only URLs starting with MODRINTH_CDN_ORIGIN are returned to the agent.
  - This prevents SSRF if a rogue Modrinth response ever includes a non-CDN URL.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any

import httpx

from blockhost_backend.services.api_cache import get_api_cache

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_CDN_ORIGIN = "https://cdn.modrinth.com/"

# Modrinth requires a descriptive User-Agent per their API guidelines.
_USER_AGENT = "BlockHost/1.0 (https://blockhost.app; contact@blockhost.app)"

_SEARCH_CACHE_TTL = 60       # seconds — search results are short-lived
_PROJECT_CACHE_TTL = 300     # seconds — project metadata is more stable

# Filename version-suffix pattern for dedup comparison.
# Matches: sodium-0.5.8+mc1.21.1-fabric.jar  →  slug = "sodium"
_VERSION_SUFFIX_RE = re.compile(r"-[\d.]+[a-z0-9+._-]*\.jar$", re.IGNORECASE)

# Only allow Modrinth CDN URLs to be sent to the agent (SSRF guard).
def _is_safe_cdn_url(url: str) -> bool:
    return url.startswith(MODRINTH_CDN_ORIGIN)


# ── Shared HTTP client ─────────────────────────────────────────────────────────

_client: httpx.AsyncClient | None = None


def get_modrinth_client() -> httpx.AsyncClient:
    """Return (or lazily create) the shared async httpx client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=MODRINTH_API_BASE,
            headers={"User-Agent": _USER_AGENT},
            timeout=15.0,
            follow_redirects=True,
        )
    return _client


async def _modrinth_get(path: str, params: dict | None = None) -> Any:
    """GET from Modrinth, with one automatic retry on 429 rate-limit."""
    import asyncio

    client = get_modrinth_client()
    try:
        resp = await client.get(path, params=params)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "1"))
            logger.warning("Modrinth rate limited; retrying after %ss", retry_after)
            await asyncio.sleep(retry_after)
            resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException as exc:
        raise ModrinthUnavailableError("Modrinth API timed out") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise ModrinthUnavailableError(
                f"Modrinth API error {exc.response.status_code}"
            ) from exc
        raise


# ── Custom exceptions ──────────────────────────────────────────────────────────

class ModrinthUnavailableError(Exception):
    """Raised when Modrinth is unreachable or returning 5xx."""


class ModVersionNotFoundError(Exception):
    """Raised when no compatible version exists for a given project + context."""


# ── Data structures ────────────────────────────────────────────────────────────

class ModSearchHit:
    __slots__ = (
        "project_id", "slug", "title", "description",
        "icon_url", "downloads", "categories", "latest_version",
    )

    def __init__(self, raw: dict) -> None:
        self.project_id: str = raw["project_id"]
        self.slug: str = raw["slug"]
        self.title: str = raw.get("title", "")
        self.description: str = raw.get("description", "")
        self.icon_url: str | None = raw.get("icon_url")
        self.downloads: int = raw.get("downloads", 0)
        self.categories: list[str] = raw.get("categories", [])
        self.latest_version: str | None = (
            raw.get("versions", [None])[-1] if raw.get("versions") else None
        )

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "icon_url": self.icon_url,
            "downloads": self.downloads,
            "categories": self.categories,
            "latest_version": self.latest_version,
        }


class ModFile:
    """A resolved, downloadable file from Modrinth."""
    __slots__ = ("url", "filename", "size_bytes", "sha512")

    def __init__(self, url: str, filename: str, size_bytes: int, sha512: str | None) -> None:
        self.url = url
        self.filename = filename
        self.size_bytes = size_bytes
        self.sha512 = sha512

    def to_dict(self) -> dict:
        return {"url": self.url, "filename": self.filename}


# ── Facet encoding ─────────────────────────────────────────────────────────────

def _build_facets(
    loaders: list[str],
    project_types: list[str],
    game_version: str,
) -> str:
    """
    Encode Modrinth facets as a JSON array-of-arrays.

    Modrinth facet format:
      AND of groups: each group is an OR of conditions.
      [["versions:1.21.1"], ["categories:fabric"], ["project_type:mod"]]

    Multiple loaders (e.g. Paper + Purpur) become one OR-group:
      [["categories:paper","categories:purpur"]]
    """
    groups: list[list[str]] = []

    # Loader group — OR across loaders for the same group
    if loaders:
        groups.append([f"categories:{loader}" for loader in loaders])

    # Version — exact match
    groups.append([f"versions:{game_version}"])

    # Project type group — OR across types
    if project_types:
        groups.append([f"project_type:{pt}" for pt in project_types])

    return json.dumps(groups)


# ── Search ─────────────────────────────────────────────────────────────────────

async def search_mods(
    *,
    query: str,
    loaders: list[str],
    project_types: list[str],
    game_version: str,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ModSearchHit], int]:
    """
    Search Modrinth with server-specific facets.

    Returns (hits, total_hits). Results are cached for _SEARCH_CACHE_TTL seconds.
    """
    cache_key = (
        f"modrinth:search:{query}:{','.join(sorted(loaders))}"
        f":{game_version}:{limit}:{offset}"
    )

    cache = get_api_cache()
    cached = cache.get_json(cache_key)
    if cached:
        hits = [ModSearchHit(h) for h in cached.get("hits", [])]
        return hits, cached.get("total_hits", 0)

    facets = _build_facets(loaders, project_types, game_version)
    params = {
        "query": query,
        "facets": facets,
        "limit": min(limit, 50),
        "offset": offset,
        "index": "relevance",
    }

    data = await _modrinth_get("/search", params=params)
    hits = [ModSearchHit(h) for h in data.get("hits", [])]
    total = data.get("total_hits", 0)

    cache.set_json(cache_key, {"hits": [h.to_dict() for h in hits], "total_hits": total}, _SEARCH_CACHE_TTL)

    return hits, total


# ── Version resolution ─────────────────────────────────────────────────────────

async def get_project(project_id: str) -> dict:
    """Fetch project metadata."""
    cache_key = f"modrinth:project:{project_id}"
    cache = get_api_cache()
    cached = cache.get_json(cache_key)
    if cached:
        return cached

    data = await _modrinth_get(f"/project/{project_id}")
    cache.set_json(cache_key, data, _PROJECT_CACHE_TTL)
    return data


async def get_project_versions(
    *,
    project_id: str,
    loaders: list[str],
    game_version: str,
) -> list[dict]:
    """Fetch all compatible versions for a project."""
    params: dict[str, Any] = {
        "loaders": json.dumps(loaders),
        "game_versions": json.dumps([game_version]),
    }
    return await _modrinth_get(f"/project/{project_id}/version", params=params)


async def get_version(version_id: str) -> dict:
    """Fetch a specific version by its ID."""
    return await _modrinth_get(f"/version/{version_id}")


async def get_best_version(
    *,
    project_id: str,
    loaders: list[str],
    game_version: str,
) -> dict:
    """
    Fetch the latest compatible version object for a project.

    Modrinth returns versions newest-first, so we take index 0.
    Raises ModVersionNotFoundError if no compatible version exists.
    """
    versions = await get_project_versions(
        project_id=project_id,
        loaders=loaders,
        game_version=game_version,
    )

    if not versions:
        raise ModVersionNotFoundError(
            f"No compatible version for project '{project_id}' "
            f"with loaders={loaders} game_version={game_version}"
        )

    return versions[0]  # newest first


def _pick_primary_file(version: dict) -> ModFile:
    """
    Extract the primary downloadable file from a Modrinth version object.

    Modrinth marks exactly one file as primary=true. Fall back to first file.
    """
    files = version.get("files", [])
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary:
        raise ModVersionNotFoundError(
            f"No downloadable file in version '{version.get('id')}'"
        )

    url = primary["url"]
    if not _is_safe_cdn_url(url):
        raise ValueError(
            f"Unexpected non-CDN URL in Modrinth response: {url!r}. "
            "Refusing to send to agent (SSRF guard)."
        )

    return ModFile(
        url=url,
        filename=primary["filename"],
        size_bytes=primary.get("size", 0),
        sha512=primary.get("hashes", {}).get("sha512"),
    )


# ── Filename deduplication ─────────────────────────────────────────────────────

def mod_slug_from_filename(filename: str) -> str:
    """
    Strip the version suffix from a JAR filename for dedup comparison.

    Examples:
      sodium-0.5.8+mc1.21.1-fabric.jar  →  "sodium"
      fabric-api-0.92.2+1.21.1.jar      →  "fabric-api"
      myplugin.jar                        →  "myplugin"
    """
    stem = filename.removesuffix(".jar") if filename.endswith(".jar") else filename
    # Strip everything after the last hyphen that's followed by a version number
    cleaned = _VERSION_SUFFIX_RE.sub("", filename)
    if cleaned.endswith(".jar"):
        cleaned = cleaned[:-4]
    return cleaned.lower() if cleaned else stem.lower()


# ── Dependency resolver ────────────────────────────────────────────────────────

async def resolve_install_files(
    *,
    project_id: str,
    loaders: list[str],
    game_version: str,
    already_installed_filenames: list[str],
    root_version_id: str | None = None,
) -> tuple[list[ModFile], list[str]]:
    """
    Resolve the full set of files to download for a mod install, including
    required dependencies (BFS, cycle-safe).

    Returns:
        (to_download, already_present)
        - to_download: ModFile list of new files to fetch
        - already_present: filenames that were already on disk (skipped)
    """
    # Build a set of installed mod slugs for dedup (version-agnostic)
    installed_slugs = {mod_slug_from_filename(f) for f in already_installed_filenames}

    to_download: list[ModFile] = []
    already_present: list[str] = []
    visited_projects: set[str] = set()

    queue: deque[str] = deque([project_id])

    while queue:
        pid = queue.popleft()
        if pid in visited_projects:
            continue
        visited_projects.add(pid)

        try:
            if pid == project_id and root_version_id:
                version = await get_version(root_version_id)
            else:
                version = await get_best_version(
                    project_id=pid,
                    loaders=loaders,
                    game_version=game_version,
                )
        except ModVersionNotFoundError:
            logger.warning(
                "Skipping dependency '%s' — no compatible version for loaders=%s game=%s",
                pid, loaders, game_version,
            )
            continue

        mod_file = _pick_primary_file(version)
        slug = mod_slug_from_filename(mod_file.filename)

        if slug in installed_slugs:
            already_present.append(mod_file.filename)
            logger.debug("Skipping '%s' — already installed", mod_file.filename)
        else:
            to_download.append(mod_file)
            installed_slugs.add(slug)  # prevent re-adding via another dep path

        # Enqueue required dependencies only
        for dep in version.get("dependencies", []):
            dep_type = dep.get("dependency_type", "")
            dep_project_id = dep.get("project_id")
            if dep_type == "required" and dep_project_id and dep_project_id not in visited_projects:
                queue.append(dep_project_id)

    return to_download, already_present
