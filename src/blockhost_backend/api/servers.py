from __future__ import annotations

import logging
import re
import socket
import subprocess
import uuid
from contextlib import closing
from pathlib import Path

import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user, get_ws_user
from blockhost_backend.api.schemas import (
    BedrockServerStats,
    CommandRequest,
    CreateServerRequest,
    ServerActionResponse,
    ServerConfigOut,
    ServerConfigUpdateRequest,
    ServerDetail,
    ServerOut,
)
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, ServerState, SubscriptionTier, User, VMProvider
from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload
from blockhost_backend.minecraft.bedrock_properties import BedrockServerProperties, write_server_properties
from blockhost_backend.minecraft.bedrock_download import ensure_version_installed, recommended_version
from blockhost_backend.minecraft.port_alloc import PortRange, pick_free_udp_port
from blockhost_backend.runtime.bedrock_process import (
    BedrockRuntimeRegistry,
    materialize_server_dir,
    resolve_version_dir,
)
from blockhost_backend.runtime.cgroup_limits import apply_limits, remove_limits
from blockhost_backend.utils import generate_join_code

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers", tags=["servers"])

_RUNTIME = BedrockRuntimeRegistry()

# ---------------------------------------------------------------------------
# Tier resource limits — source of truth for RAM/CPU per subscription tier.
# Keep here (not in a separate file) so servers.py is fully self-contained.
# ---------------------------------------------------------------------------
TIER_RESOURCE_LIMITS: dict[SubscriptionTier, dict] = {
    SubscriptionTier.free:    {"ram_mb": 512,  "cpu_quota_pct": 50},
    SubscriptionTier.premium: {"ram_mb": 1024, "cpu_quota_pct": 100},
}

# Maximum servers allowed per tier — enforced server-side only, never from client.
TIER_SERVER_LIMITS: dict[SubscriptionTier, int] = {
    SubscriptionTier.free:    1,
    SubscriptionTier.premium: 3,
}

# Allowlist for Bedrock version strings: digits and dots only, e.g. "1.21.0"
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")

# Hard cap on log tail to prevent memory DoS
_MAX_LOG_TAIL = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_user_tier(user: User) -> SubscriptionTier:
    """
    Resolve a user's SubscriptionTier from the ORM object.
    Never trusts client-supplied values — always reads from the DB-loaded user.
    """
    tier = getattr(user, "subscription_tier", None)
    if isinstance(tier, SubscriptionTier):
        return tier
    if isinstance(tier, str):
        try:
            return SubscriptionTier(tier)
        except ValueError:
            pass
    return SubscriptionTier.free


def _sanitize_version(version: str | None) -> str | None:
    """
    Reject version strings that don't look like semver numbers.
    Prevents shell injection if the version string is ever passed to a subprocess.
    Allows special sentinel values used internally.
    """
    if version is None:
        return None
    v = str(version).strip()
    if not v:
        return None
    if v.upper() in {"LATEST", "PREVIEW", "RECOMMENDED", "DEFAULT"}:
        return v
    if not _VERSION_RE.match(v):
        raise ValueError(f"Invalid version string: {v!r}")
    return v


def _validate_server_dir(server_dir: Path, servers_dir: Path) -> Path:
    """
    Ensure server_dir is inside the expected servers root.
    Prevents path traversal attacks where mc_config["server_dir"] is
    manipulated to point outside the sandbox (e.g. "../../etc").
    """
    try:
        resolved = server_dir.resolve()
        root = servers_dir.resolve()
        resolved.relative_to(root)  # raises ValueError if outside root
        return resolved
    except ValueError:
        raise ValueError(
            f"server_dir {server_dir!r} is outside the allowed servers root {servers_dir!r}"
        )


def _server_to_out(server: Server, owner: User | None = None) -> ServerOut:
    owner_obj = owner or server.owner
    owner_nickname = owner_obj.nickname if owner_obj else "Unknown"
    settings = get_settings()
    host = server.vm_ipv4 or settings.minecraft_public_host
    shareable_address = None
    if host and server.vm_port:
        shareable_address = f"{host}:{server.vm_port}"
    return ServerOut(
        id=server.id,
        world_name=server.world_name,
        join_code=server.join_code,
        state=server.state,
        vm_provider=server.vm_provider,
        vm_ipv4=server.vm_ipv4,
        vm_port=server.vm_port,
        shareable_address=shareable_address,
        owner_id=server.owner_id,
        owner_nickname=owner_nickname,
        created_at=server.created_at,
        last_activity=server.last_activity,
        mc_config=server.mc_config,
    )


def _server_dirs() -> tuple[Path, Path, Path]:
    settings = get_settings()
    return (
        Path(settings.bedrock_versions_dir).resolve(),
        Path(settings.bedrock_servers_dir).resolve(),
        Path(settings.bedrock_logs_dir).resolve(),
    )


def _ensure_version_on_disk(*, versions_dir: Path, requested_version: str | None) -> None:
    settings = get_settings()
    if not requested_version or not str(requested_version).strip():
        return
    rv = str(requested_version).strip()
    if rv.lower() in {"recommended", "default"}:
        rv = recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    if rv.upper() in {"LATEST", "PREVIEW"}:
        return
    ensure_version_installed(
        versions_dir=versions_dir,
        manifest_path=Path(settings.bedrock_versions_manifest),
        version=rv,
    )


def _normalize_requested_version(requested_version: str | None) -> str | None:
    """
    Normalise UI-friendly aliases and sanitize the version string.
    Returns None to let resolve_version_dir pick the latest installed version.
    """
    if requested_version is None:
        return None
    rv = str(requested_version).strip()
    if not rv:
        return None
    # Sanitize before anything else — raises ValueError on bad input
    rv = _sanitize_version(rv)
    settings = get_settings()
    if rv and rv.lower() in {"recommended", "default"}:
        return recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    return rv


def _is_port_available(port: int) -> bool:
    try:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


def _allocate_port(*, db: Session) -> int:
    settings = get_settings()
    used = set(
        p
        for p in db.execute(select(Server.vm_port)).scalars().all()
        if p is not None
    )
    port_range = PortRange(
        start=settings.bedrock_port_range_start,
        end=settings.bedrock_port_range_end,
    )
    for port in range(port_range.start, port_range.end + 1):
        if port in used:
            continue
        if _is_port_available(port):
            return port
    raise RuntimeError(
        f"No free UDP ports in range {port_range.start}-{port_range.end}. "
        f"Database has {len(used)} allocated: {sorted(used)}"
    )


def _server_props_from_config(*, server: Server, port: int) -> BedrockServerProperties:
    cfg = dict(server.mc_config or {})
    server_name = str(cfg.get("server_name") or server.world_name)
    return BedrockServerProperties(
        server_name=server_name,
        gamemode=cfg.get("gamemode"),
        difficulty=cfg.get("difficulty"),
        max_players=cfg.get("max_players"),
        allow_cheats=cfg.get("allow_cheats"),
        online_mode=cfg.get("online_mode"),
        level_name=cfg.get("level_name") or server.world_name,
        level_seed=cfg.get("level_seed"),
        server_port=port,
    )


def _start_bedrock_process(*, server: Server, settings, db: Session | None = None) -> None:
    """
    Start a Bedrock process and apply cgroup resource limits based on the
    owner's subscription tier.  Tier is always resolved from the database
    (via the loaded relationship or a fresh DB lookup) — never from the request.
    """
    if not server.vm_port:
        raise RuntimeError(f"Server {server.id} has no port allocated")
    if not (server.mc_config or {}).get("server_dir"):
        raise RuntimeError(f"Server {server.id} has no server_dir configured")

    # Validate server_dir is inside the allowed root before using it
    _, servers_dir, _ = _server_dirs()
    safe_server_dir = _validate_server_dir(
        Path(server.mc_config["server_dir"]), servers_dir
    )

    proc = _RUNTIME.ensure_process(
        server_id=str(server.id),
        server_dir=safe_server_dir,
        port=server.vm_port,
        requested_version=str(server.mc_config.get("template_version") or ""),
    )
    proc.start(executable_name=settings.bedrock_executable_name or None)
    info = proc.wait_for_ready()

    if server.mc_config.get("template_version") and info.actual_version:
        requested = str(server.mc_config["template_version"])
        if requested not in {"LATEST", "PREVIEW"} and not str(info.actual_version).startswith(requested):
            proc.stop()
            raise RuntimeError(
                f"Runtime version mismatch (requested={requested}, actual={info.actual_version})"
            )

    # Resolve tier from DB — never trust a client-supplied value
    tier: SubscriptionTier = SubscriptionTier.free
    try:
        owner = getattr(server, "owner", None)
        if owner and getattr(owner, "subscription_tier", None):
            tier = _get_user_tier(owner)
        elif db and server.owner_id:
            db_owner = db.get(User, server.owner_id)
            if db_owner:
                tier = _get_user_tier(db_owner)
    except Exception as exc:
        logger.warning("Could not resolve tier for server %s: %s — defaulting to free", server.id, exc)

    if info.pid:
        limits = TIER_RESOURCE_LIMITS.get(tier, TIER_RESOURCE_LIMITS[SubscriptionTier.free])
        apply_limits(
            pid=info.pid,
            server_id=str(server.id),
            ram_mb=limits["ram_mb"],
            cpu_quota_pct=limits["cpu_quota_pct"],
        )
        logger.info(
            "Server %s started (pid=%s, tier=%s, ram=%sMB, cpu=%s%%)",
            server.id, info.pid, tier.value, limits["ram_mb"], limits["cpu_quota_pct"],
        )

    server.vm_id = str(info.pid)
    server.vm_ipv4 = settings.minecraft_public_host
    server.state = ServerState.running


def _stop_server_process(server: Server) -> None:
    """Stop the BDS process and clean up cgroup resources."""
    try:
        _RUNTIME.stop(server_id=str(server.id))
    except Exception as exc:
        logger.warning("RUNTIME.stop failed for server %s: %s", server.id, exc)
    finally:
        # Always clean up cgroup even if stop raised
        remove_limits(server_id=str(server.id))
    server.state = ServerState.suspended


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    payload: CreateServerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerOut:
    settings = get_settings()

    # Always derive tier from the authenticated user record — never from payload.
    user_tier = _get_user_tier(user)
    server_limit = TIER_SERVER_LIMITS.get(user_tier, TIER_SERVER_LIMITS[SubscriptionTier.free])
    count = db.execute(
        select(func.count()).select_from(Server).where(Server.owner_id == user.id)
    ).scalar_one()
    if count >= server_limit:
        raise HTTPException(
            status_code=403,
            detail=f"{user_tier.value.capitalize()} tier limited to {server_limit} server(s)",
        )

    config = payload.config.model_dump(exclude_none=True) if payload.config else {}
    config.pop("bedrock_image", None)  # Docker-only field, never accepted

    # Sanitize version from config before touching disk
    raw_version = config.get("bedrock_version")
    try:
        config["bedrock_version"] = _sanitize_version(raw_version)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        port = _allocate_port(db=db)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

    server = Server(
        owner_id=user.id,
        world_name=payload.world_name,
        join_code="placeholder",
        state=ServerState.created,
        vm_provider=VMProvider.local_bedrock,
        vm_ipv4=None,
        vm_port=port,
        mc_config=config,
    )
    db.add(server)
    db.flush()  # Generate server.id

    server.join_code = generate_join_code(user.id, server.id)

    versions_dir, servers_dir, _logs_dir = _server_dirs()
    try:
        requested_version = _normalize_requested_version(
            (server.mc_config or {}).get("bedrock_version")
        )
        _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
        version_name, version_dir = resolve_version_dir(
            versions_dir=versions_dir, requested=requested_version
        )
        server_dir = materialize_server_dir(
            version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id)
        )
        # Validate the produced path is still inside the root
        _validate_server_dir(server_dir, servers_dir)

        write_server_properties(
            server_dir / "server.properties",
            _server_props_from_config(server=server, port=server.vm_port),
        )

        cfg = dict(server.mc_config or {})
        cfg["runtime_mode"] = "process"
        cfg["template_version"] = version_name
        cfg["server_dir"] = str(server_dir)
        server.mc_config = cfg
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=503, detail=f"Failed to materialize Bedrock server folder: {e}"
        )

    try:
        if not server.vm_port:
            raise RuntimeError("Port not allocated before process startup")
        _start_bedrock_process(server=server, settings=settings, db=db)
    except Exception as e:
        server.state = ServerState.suspended
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")

    db.commit()
    db.refresh(server)
    return _server_to_out(server, owner=user)


def _discover_filesystem_servers(*, servers_dir: Path, db: Session) -> list[Server]:
    """
    Discover server folders on the filesystem that have no database entry.

    SECURITY: Orphan dirs are NOT automatically assigned to any user.
    They are recorded with owner_id=None (or skipped if the schema requires
    a non-null owner) so an admin can review them.  A regular user can never
    claim a server they didn't create just by being the first to call list_servers.
    """
    discovered = []
    if not servers_dir.exists():
        return discovered

    existing_ids = {
        str(s) for s in db.execute(select(Server.id)).scalars().all()
    }

    for server_dir in servers_dir.iterdir():
        if not server_dir.is_dir():
            continue

        server_id_str = server_dir.name
        try:
            server_uuid = uuid.UUID(server_id_str)
        except (ValueError, AttributeError):
            continue

        if server_id_str in existing_ids:
            continue

        # Path traversal guard: confirm the dir is truly inside servers_dir
        try:
            server_dir.resolve().relative_to(servers_dir.resolve())
        except ValueError:
            logger.warning("Skipping suspicious server dir: %s", server_dir)
            continue

        props_file = server_dir / "server.properties"
        world_name = "Unnamed Server"
        port = 19132

        if props_file.exists():
            try:
                content = props_file.read_text(encoding="utf-8")
                for line in content.split("\n"):
                    if line.startswith("level-name="):
                        world_name = line.split("=", 1)[1].strip()
                    elif line.startswith("server-port="):
                        try:
                            port = int(line.split("=", 1)[1].strip())
                        except ValueError:
                            pass
            except Exception:
                pass

        # Record without an owner — do NOT auto-assign to requesting user.
        # owner_id=None means only admins can see/claim this server.
        # If your schema requires owner_id to be non-null, log and skip instead.
        logger.warning(
            "Discovered orphan server dir %s (id=%s) — not auto-assigned to any user",
            server_dir, server_uuid,
        )
        # Uncomment below only if your schema allows nullable owner_id:
        # new_server = Server(
        #     id=server_uuid,
        #     owner_id=None,
        #     world_name=world_name,
        #     join_code="",
        #     state=ServerState.suspended,
        #     vm_provider=VMProvider.local_bedrock,
        #     vm_ipv4=get_settings().minecraft_public_host,
        #     vm_port=port,
        #     mc_config={"server_dir": str(server_dir)},
        # )
        # db.add(new_server)
        # discovered.append(new_server)

    if discovered:
        db.commit()

    return discovered


@router.get("", response_model=list[ServerOut])
def list_servers(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ServerOut]:
    # Run discovery first (logs orphans, does not assign them to this user)
    _, servers_dir, _ = _server_dirs()
    _discover_filesystem_servers(servers_dir=servers_dir, db=db)

    # Fetch after discovery so the query reflects any newly committed rows
    servers = db.execute(
        select(Server).where(Server.owner_id == user.id)
    ).scalars().all()
    return [_server_to_out(s, owner=user) for s in servers]


@router.get("/{server_id}", response_model=ServerDetail)
def get_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerDetail:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    base = _server_to_out(server, owner=user)
    return ServerDetail(
        **base.model_dump(),
        minecraft_host=(server.vm_ipv4 or get_settings().minecraft_public_host),
        minecraft_port=server.vm_port,
    )


@router.post("/{server_id}/start", response_model=ServerActionResponse)
def start_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    is_actually_running = False
    if server.state == ServerState.running:
        proc = _RUNTIME.get(str(server.id))
        if proc and proc.is_running():
            is_actually_running = True

    if is_actually_running:
        # Idempotent — already running, nothing to do
        return ServerActionResponse(id=server.id, state=server.state)

    settings = get_settings()
    server.vm_provider = VMProvider.local_bedrock

    if not server.vm_port:
        try:
            server.vm_port = _allocate_port(db=db)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

    if not (server.mc_config or {}).get("server_dir"):
        versions_dir, servers_dir, _logs_dir = _server_dirs()
        try:
            requested_version = _normalize_requested_version(
                (server.mc_config or {}).get("bedrock_version")
            )
            _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
            version_name, version_dir = resolve_version_dir(
                versions_dir=versions_dir, requested=requested_version
            )
            server_dir = materialize_server_dir(
                version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id)
            )
            _validate_server_dir(server_dir, servers_dir)
            write_server_properties(
                server_dir / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )
            cfg = dict(server.mc_config or {})
            cfg["runtime_mode"] = "process"
            cfg["template_version"] = version_name
            cfg["server_dir"] = str(server_dir)
            server.mc_config = cfg
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Failed to materialize Bedrock server folder: {e}"
            )
    else:
        try:
            _, servers_dir, _ = _server_dirs()
            server_dir = _validate_server_dir(
                Path(server.mc_config["server_dir"]), servers_dir
            )
            write_server_properties(
                server_dir / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Failed to update server.properties: {e}"
            )

    try:
        _start_bedrock_process(server=server, settings=settings, db=db)
    except Exception as e:
        server.state = ServerState.suspended
        raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")

    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.post("/{server_id}/stop", response_model=ServerActionResponse)
def stop_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    _stop_server_process(server)
    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.post("/{server_id}/toggle", response_model=ServerActionResponse)
def toggle_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    is_actually_running = False
    if server.state == ServerState.running:
        proc = _RUNTIME.get(str(server.id))
        if proc and proc.is_running():
            is_actually_running = True

    settings = get_settings()
    if is_actually_running:
        _stop_server_process(server)
    else:
        try:
            if not server.vm_port:
                server.vm_port = _allocate_port(db=db)

            if not (server.mc_config or {}).get("server_dir"):
                versions_dir, servers_dir, _logs_dir = _server_dirs()
                requested_version = _normalize_requested_version(
                    (server.mc_config or {}).get("bedrock_version")
                )
                _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
                version_name, version_dir = resolve_version_dir(
                    versions_dir=versions_dir, requested=requested_version
                )
                server_dir = materialize_server_dir(
                    version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id)
                )
                _validate_server_dir(server_dir, servers_dir)
                cfg = dict(server.mc_config or {})
                cfg["runtime_mode"] = "process"
                cfg["template_version"] = version_name
                cfg["server_dir"] = str(server_dir)
                server.mc_config = cfg
            else:
                _, servers_dir, _ = _server_dirs()
                _validate_server_dir(Path(server.mc_config["server_dir"]), servers_dir)

            write_server_properties(
                Path(server.mc_config["server_dir"]) / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )
            _start_bedrock_process(server=server, settings=settings, db=db)
        except Exception as e:
            server.state = ServerState.suspended
            raise HTTPException(
                status_code=503, detail=f"Failed to start Bedrock process: {e}"
            )

    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.get("/{server_id}/config", response_model=ServerConfigOut)
def get_server_config(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerConfigOut:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    return ServerConfigOut(id=server.id, mc_config=server.mc_config or {})


@router.patch("/{server_id}/config", response_model=ServerConfigOut)
def update_server_config(
    server_id: str,
    payload: ServerConfigUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerConfigOut:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    current = dict(server.mc_config or {})
    update = payload.config.model_dump(exclude_none=True)

    # Strip fields the client must never be allowed to set directly
    for protected in ("server_dir", "runtime_mode", "template_version"):
        update.pop(protected, None)

    # Sanitize any version supplied via config update
    if "bedrock_version" in update:
        try:
            update["bedrock_version"] = _sanitize_version(update["bedrock_version"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    current.update(update)
    server.mc_config = current

    if server.state == ServerState.running and (server.mc_config or {}).get("server_dir"):
        try:
            _, servers_dir, _ = _server_dirs()
            server_dir = _validate_server_dir(
                Path(server.mc_config["server_dir"]), servers_dir
            )
            write_server_properties(
                server_dir / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Failed to update server.properties: {e}"
            )

    db.commit()
    db.refresh(server)
    return ServerConfigOut(id=server.id, mc_config=server.mc_config or {})


@router.get("/{server_id}/stats", response_model=BedrockServerStats)
def get_server_stats(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BedrockServerStats:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    settings = get_settings()
    host = server.vm_ipv4 or settings.minecraft_public_host
    port = server.vm_port or settings.bedrock_port_range_start

    tier = _get_user_tier(user)
    limits = TIER_RESOURCE_LIMITS.get(tier, TIER_RESOURCE_LIMITS[SubscriptionTier.free])
    allocated_ram_mb = limits["ram_mb"]
    allocated_cpu_cores = limits["cpu_quota_pct"] / 100.0

    proc = _RUNTIME.get(str(server.id))
    process_running = proc is not None and proc.is_running()
    pid = proc.pid() if process_running and proc else None
    uptime = proc.uptime_seconds() if process_running and proc else None

    cpu_usage = None
    ram_usage_mb = None
    cpu_usage_percent = None
    ram_usage_percent = None
    if pid:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "%cpu,rss", "--no-headers"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    cpu_usage = float(parts[0])
                    ram_usage_mb = float(parts[1]) / 1024.0
                    # Normalize CPU and RAM against allocated caps
                    if allocated_cpu_cores and allocated_cpu_cores > 0:
                        cpu_usage_percent = (cpu_usage / 100.0) / allocated_cpu_cores * 100.0
                    if allocated_ram_mb and allocated_ram_mb > 0:
                        ram_usage_percent = (ram_usage_mb / allocated_ram_mb) * 100.0
        except Exception:
            pass

    base_stats = {
        "host": host,
        "port": port,
        "allocated_ram_mb": allocated_ram_mb,
        "allocated_cpu_cores": allocated_cpu_cores,
        "process_running": process_running,
        "uptime_seconds": uptime,
        "cpu_usage": cpu_usage,
        "ram_usage_mb": ram_usage_mb,
        "cpu_usage_percent": cpu_usage_percent,
        "ram_usage_percent": ram_usage_percent,
    }

    try:
        pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=1.0)
        parsed = parse_bedrock_pong_payload(pong.payload)
        return BedrockServerStats(
            **base_stats,
            reachable=True,
            latency_ms=pong.latency_ms,
            edition=parsed.get("edition"),
            motd=parsed.get("motd"),
            motd2=parsed.get("motd2"),
            protocol=parsed.get("protocol"),
            version=parsed.get("version"),
            players_online=parsed.get("players_online"),
            players_max=parsed.get("players_max"),
            server_id=parsed.get("server_id"),
            gamemode=parsed.get("gamemode"),
            online_players_list=list(proc.online_players) if proc else [],
        )
    except Exception:
        return BedrockServerStats(**base_stats, reachable=False, online_players_list=list(proc.online_players) if proc else [])



@router.get("/{server_id}/logs", response_model=list[str])
def get_server_logs(
    server_id: str,
    tail: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    # Clamp tail to prevent memory DoS from huge values
    tail = max(1, min(tail, _MAX_LOG_TAIL))

    proc = _RUNTIME.get(str(server.id))
    if not proc:
        return []
    # Strip dict structure to string for backwards compatibility, or clients that just want strings
    return [entry.get("line", "") for entry in proc.read_logs(tail=tail)]

@router.websocket("/{server_id}/console/ws")
async def console_websocket(
    websocket: WebSocket,
    server_id: str,
    token: str | None = None,
    db: Session = Depends(get_db),
):
    await websocket.accept()
    
    try:
        user = get_ws_user(token=token, db=db)
        try:
            server_uuid = uuid.UUID(server_id)
        except ValueError:
            await websocket.send_json({"type": "error", "error": "Invalid server ID"})
            await websocket.close(code=4004)
            return
        
        server = db.get(Server, server_uuid)
        if not server or server.owner_id != user.id:
            await websocket.send_json({"type": "error", "error": "Not authorized"})
            await websocket.close(code=4003)
            return

        proc = _RUNTIME.get(server_id)
        if not proc or not proc.is_running():
            await websocket.send_json({"type": "error", "error": "Server is not running"})
            await websocket.close(code=4000)
            return

        # Send last 500 lines as history
        history = proc.read_logs(tail=500)
        await websocket.send_json({"type": "history", "logs": history})

        # Queue to bridge sync listener to async websocket
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_log(entry: dict):
            loop.call_soon_threadsafe(queue.put_nowait, entry)

        proc.add_log_listener(on_log)

        async def send_logs():
            try:
                while True:
                    entry = await queue.get()
                    await websocket.send_json({"type": "log", "log": entry})
            except asyncio.CancelledError:
                pass

        async def receive_commands():
            try:
                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        if msg.get("type") == "command" and "command" in msg:
                            proc.send_command(msg["command"])
                    except json.JSONDecodeError:
                        pass
            except WebSocketDisconnect:
                pass

        send_task = asyncio.create_task(send_logs())
        receive_task = asyncio.create_task(receive_commands())

        done, pending = await asyncio.wait(
            [send_task, receive_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except HTTPException:
        await websocket.send_json({"type": "error", "error": "Authentication failed"})
        await websocket.close(code=4001)
    finally:
        if 'proc' in locals() and proc and 'on_log' in locals():
            proc.remove_log_listener(on_log)



def _require_server(server_id: str, user: User, db: Session) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

def _send_cmd(server_id: str, cmd: str) -> dict:
    proc = _RUNTIME.get(server_id)
    if not proc or not proc.is_running():
        raise HTTPException(status_code=400, detail="Server is not running")
    proc.send_command(cmd)
    return {"status": "ok"}


@router.post("/{server_id}/teleport")
def command_teleport(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server_id, f"tp {req.player} {req.x} {req.y} {req.z}")

@router.post("/{server_id}/clear-inventory")
def command_clear_inventory(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server_id, f"clear {req.player}")

@router.post("/{server_id}/ban")
def command_ban(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = f"ban {req.player}"
    if req.message:
        cmd += f" {req.message}"
    return _send_cmd(server_id, cmd)

@router.post("/{server_id}/kick")
def command_kick(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = f"kick {req.player}"
    if req.message:
        cmd += f" {req.message}"
    return _send_cmd(server_id, cmd)

@router.post("/{server_id}/op")
def command_op(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = "op" if req.grant else "deop"
    return _send_cmd(server_id, f"{cmd} {req.player}")

@router.post("/{server_id}/gamemode")
def command_gamemode(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server_id, f"gamemode {req.mode} {req.player}")

@router.post("/{server_id}/time")
def command_time(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    return _send_cmd(server_id, f"time set {req.value}")

@router.post("/{server_id}/weather")
def command_weather(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    return _send_cmd(server_id, f"weather {req.weather}")

@router.post("/{server_id}/say")
def command_say(server_id: str, req: CommandRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    return _send_cmd(server_id, f"say {req.message}")

@router.get("/{server_id}/blocklist")
def get_blocklist(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    # Mocking blocklist for now, since vanilla BDS doesn't track this neatly via command
    return {"players": []}