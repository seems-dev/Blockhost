"""
modrinth_compat.py — Flavor → Modrinth loader profile registry.

This is the single source of truth for how each ServerFlavor maps to:
  - Modrinth `loaders` filter values
  - Modrinth `project_type` filter
  - The on-disk subdirectory where mod/plugin JARs are installed
  - Whether mod management is supported at all for this flavor

KEY CORRECTNESS NOTE:
  Paper/Purpur servers run *Bukkit plugins*, not Fabric mods.
  On Modrinth, these are separate ecosystems with different `loaders` values.
  Paper plugins live in `plugins/`, Fabric mods live in `mods/`.
  Never cross these two up — the player would get "unknown mod" errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blockhost_backend.database.schema import ServerFlavor


@dataclass(frozen=True)
class ModrinthLoaderProfile:
    """Everything the mod system needs to know about a server flavor."""

    # Modrinth API `loaders` filter (e.g. ["fabric"], ["paper", "purpur"])
    loaders: list[str]

    # Modrinth API `project_type` facet (e.g. "mod", "plugin")
    project_types: list[str]

    # Subdirectory inside the server dir where JARs are stored
    install_subdir: str

    # Whether mod management is supported for this flavor
    supported: bool

    # Human-readable reason shown in the UI when unsupported
    unsupported_reason: str = ""

    # Whether this flavor is supported but deferred to V2
    coming_soon: bool = False


_LOADER_PROFILES: dict[ServerFlavor, ModrinthLoaderProfile] = {
    # ── Fabric: primary mod loader, mods go in `mods/` ───────────────────────
    ServerFlavor.FABRIC: ModrinthLoaderProfile(
        loaders=["fabric"],
        project_types=["mod"],
        install_subdir="mods",
        supported=True,
    ),

    # ── Paper: Bukkit/Spigot plugin ecosystem, plugins go in `plugins/` ───────
    # Do NOT use loaders=["fabric"] here. Paper runs plugins, not Fabric mods.
    ServerFlavor.PAPER: ModrinthLoaderProfile(
        loaders=["paper"],
        project_types=["plugin"],
        install_subdir="plugins",
        supported=True,
    ),

    # ── Purpur: Paper fork — same plugin ecosystem, same Modrinth loaders ─────
    # Modrinth indexes Purpur plugins under both "purpur" and "paper" loaders.
    ServerFlavor.PURPUR: ModrinthLoaderProfile(
        loaders=["purpur", "paper"],
        project_types=["plugin"],
        install_subdir="plugins",
        supported=True,
    ),

    # ── Forge: V2 — dependency trees are complex, deferred ───────────────────
    ServerFlavor.FORGE: ModrinthLoaderProfile(
        loaders=["forge"],
        project_types=["mod"],
        install_subdir="mods",
        supported=False,
        coming_soon=True,
        unsupported_reason="Forge mod management is coming soon.",
    ),

    # ── NeoForge: V2 ─────────────────────────────────────────────────────────
    ServerFlavor.NEOFORGE: ModrinthLoaderProfile(
        loaders=["neoforge"],
        project_types=["mod"],
        install_subdir="mods",
        supported=False,
        coming_soon=True,
        unsupported_reason="NeoForge mod management is coming soon.",
    ),

    # ── Java Vanilla: no mod system ───────────────────────────────────────────
    ServerFlavor.JAVA_VANILLA: ModrinthLoaderProfile(
        loaders=[],
        project_types=[],
        install_subdir="",
        supported=False,
        unsupported_reason="Vanilla servers do not support mods. Switch to Fabric to install mods.",
    ),

    # ── Bedrock: completely different platform, no Modrinth support ───────────
    ServerFlavor.BEDROCK: ModrinthLoaderProfile(
        loaders=[],
        project_types=[],
        install_subdir="",
        supported=False,
        unsupported_reason="Bedrock servers do not support Modrinth mods.",
    ),
}


def get_loader_profile(flavor: ServerFlavor) -> ModrinthLoaderProfile:
    """Return the Modrinth loader profile for a given server flavor.

    Always returns a valid profile (never KeyError) — unsupported flavors
    return a profile with ``supported=False``.
    """
    return _LOADER_PROFILES[flavor]


def is_mod_capable(flavor: ServerFlavor) -> bool:
    """True if mod management is fully supported for this flavor right now."""
    return _LOADER_PROFILES[flavor].supported


def get_install_subdir(flavor: ServerFlavor) -> str:
    """Return the subdirectory name ('mods' or 'plugins') for this flavor."""
    return _LOADER_PROFILES[flavor].install_subdir
