
from __future__ import annotations

from uvicorn import server
from blockhost_backend.database.schema import Node, ServerFlavor
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import asyncio
import json
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import authenticate_ws_user, get_current_user
from blockhost_backend.api.schemas import (
    BanCheckResponse,
    BanCreateRequest,
    BanListOut,
    BanOut,
    BedrockServerStats,
    CommandRequest,
    CreateServerRequest,
    PlayerInfo,
    ServerActionResponse,
    ServerConfigOut,
    ServerConfigUpdateRequest,
    SoftwareSwitchRequest,
    ServerDetail,
    ServerOut,
    ServerStateSnapshot,
)
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database import db
from blockhost_backend.database.db import SessionLocal, get_db
from blockhost_backend.database.schema import Ban, Server, ServerState, User, VMProvider
from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload
from blockhost_backend.minecraft.bedrock_properties import BedrockServerProperties, write_server_properties
from blockhost_backend.minecraft.bedrock_download import recommended_version
from blockhost_backend.minecraft.port_alloc import PortRange, pick_free_udp_port, pick_free_tcp_port
from blockhost_backend.minecraft.java_properties import (
    JavaServerProperties,
    set_java_server_property,
    write_java_server_properties,
)
from blockhost_backend.minecraft.java_compat import is_java_flavor, required_java_version, get_protocol_version
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator
from blockhost_backend.orchestrator.resources import get_effective_server_resource_limits, UNPAID_SERVER_LIMITS
from blockhost_backend.runtime.bedrock_process import materialize_server_dir, resolve_version_dir
from blockhost_backend.services.api_cache import get_api_cache
from blockhost_backend.services.system_health import DiskProtectionError, assert_disk_usage_safe
from blockhost_backend.utils import generate_join_code
from blockhost_backend.services.billing import BillingError
from blockhost_backend.services.node_capacity import refresh_node_allocated_ram, select_best_node
#file_name = servers.py
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/servers", tags=["servers"])

_ORCHESTRATOR = get_server_lifecycle_orchestrator()
_STATS_SNAPSHOT_TTL_SECONDS = 5.0
_STATS_REDIS_TTL_SECONDS = 5
_SERVER_LIST_TTL_SECONDS = 5
_SERVER_DETAIL_TTL_SECONDS = 15
_SERVER_CONFIG_TTL_SECONDS = 30
_PLANS_TTL_SECONDS = 300

# Maximum servers allowed per tier — enforced server-side only, never from client.

# Allowlist for Bedrock version strings: digits and dots only, e.g. "1.21.0"
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")

# Hard cap on log tail to prevent memory DoS
_MAX_LOG_TAIL = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _server_list_cache_key(user_id: uuid.UUID) -> str:
    return f"servers:list:{user_id}"


def _server_detail_cache_key(user_id: uuid.UUID, server_id: uuid.UUID | str) -> str:
    return f"servers:detail:{user_id}:{server_id}"


def _server_config_cache_key(user_id: uuid.UUID, server_id: uuid.UUID | str) -> str:
    return f"servers:config:{user_id}:{server_id}"


def _server_stats_cache_key(server_id: str) -> str:
    return f"servers:stats:{server_id}"


def _plans_cache_key() -> str:
    return "servers:plans:catalog"


def _invalidate_server_read_cache(user_id: uuid.UUID, server_id: uuid.UUID | str | None = None) -> None:
    cache = get_api_cache()
    cache.delete(_server_list_cache_key(user_id))
    if server_id is None:
        return
    cache.delete(_server_detail_cache_key(user_id, server_id))
    cache.delete(_server_config_cache_key(user_id, server_id))





def _guard_disk_for_operation(operation: str) -> None:
    try:
        assert_disk_usage_safe(operation)
    except DiskProtectionError as exc:
        raise HTTPException(status_code=503, detail={"error": exc.message})


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
        flavor=server.flavor,
        mc_version=server.mc_version,
        mc_config=server.mc_config,
    )


def _server_to_detail(server: Server, owner: User | None = None) -> ServerDetail:
    base = _server_to_out(server, owner=owner)
    return ServerDetail(
        **base.model_dump(),
        minecraft_host=(server.vm_ipv4 or get_settings().minecraft_public_host),
        minecraft_port=server.vm_port,
    )


def _ban_to_out(ban: Ban) -> BanOut:
    return BanOut(
        ban_id=ban.id,
        server_id=ban.server_id,
        xuid=ban.xuid,
        player_name=ban.player_name,
        reason=ban.reason,
        active=ban.active,
        banned_at=ban.banned_at,
        expires_at=ban.expires_at,
        unbanned_at=ban.unbanned_at,
        created_by_user_id=ban.created_by_user_id,
        unbanned_by_user_id=ban.unbanned_by_user_id,
    )


def _expire_ban_if_needed(ban: Ban, now: datetime) -> bool:
    if ban.active and ban.expires_at is not None and ban.expires_at <= now:
        ban.active = False
        ban.unbanned_at = ban.expires_at or now
        return True
    return False


def _resolve_active_ban(server_id: uuid.UUID, xuid: str, db: Session) -> Ban | None:
    now = datetime.now(timezone.utc)
    ban = db.scalars(
        select(Ban)
        .where(Ban.server_id == server_id, Ban.xuid == xuid, Ban.active == True)
        .order_by(Ban.banned_at.desc())
    ).one_or_none()
    if ban and _expire_ban_if_needed(ban, now):
        db.add(ban)
        db.commit()
        db.refresh(ban)
        return None
    return ban


def _expire_expired_bans(server_id: uuid.UUID, db: Session) -> None:
    now = datetime.now(timezone.utc)
    expired_bans = db.scalars(
        select(Ban)
        .where(Ban.server_id == server_id, Ban.active == True, Ban.expires_at != None, Ban.expires_at <= now)
    ).all()
    for ban in expired_bans:
        ban.active = False
        ban.unbanned_at = ban.expires_at or now
        db.add(ban)
    if expired_bans:
        db.commit()


def _server_to_detail(server: Server, owner: User | None = None) -> ServerDetail:
    base = _server_to_out(server, owner=owner)
    return ServerDetail(
        **base.model_dump(),
        minecraft_host=(server.vm_ipv4 or get_settings().minecraft_public_host),
        minecraft_port=server.vm_port,
    )


def _compute_server_stats(server: Server, db: Session) -> BedrockServerStats:
    settings = get_settings()
    host = server.vm_ipv4 or settings.minecraft_public_host
    port = server.vm_port or settings.bedrock_port_range_start

    # Get limits strictly from the server's billing subscription
    limits = get_effective_server_resource_limits(db=db, server=server)
    allocated_ram_mb = limits["ram_mb"]
    allocated_cpu_cores = limits["cpu_quota_pct"] / 100.0

    runtime_status = _ORCHESTRATOR.get_status(str(server.id))
    process_running = runtime_status.running
    uptime = runtime_status.uptime_seconds

    # Reconcile only known mismatch pairs (once per transition, not every poll tick).
    if process_running and server.state in (ServerState.suspended, ServerState.created):
        logger.info(
            "Reconciling server %s: runtime=running but db_state=%s — updating to running",
            server.id, server.state,
        )
        server.state = ServerState.running
        try:
            db.add(server)
            db.commit()
            _invalidate_server_read_cache(server.owner_id, server.id)
        except Exception:
            db.rollback()
    elif not process_running and server.state == ServerState.running:
        logger.info(
            "Reconciling server %s: runtime=stopped but db_state=running — updating to suspended",
            server.id,
        )
        server.state = ServerState.suspended
        try:
            db.add(server)
            db.commit()
            _invalidate_server_read_cache(server.owner_id, server.id)
        except Exception:
            db.rollback()

    resource_stats = _ORCHESTRATOR.get_stats(str(server.id))
    cpu_usage = resource_stats.cpu_usage
    ram_usage_mb = resource_stats.ram_usage_mb
    cpu_usage_percent = None
    ram_usage_percent = None
    
    if cpu_usage is not None and allocated_cpu_cores and allocated_cpu_cores > 0:
        cpu_usage_percent = (cpu_usage / 100.0) / allocated_cpu_cores * 100.0
    if ram_usage_mb is not None and allocated_ram_mb and allocated_ram_mb > 0:
        ram_usage_percent = (ram_usage_mb / allocated_ram_mb) * 100.0
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
        is_bedrock = server.flavor == ServerFlavor.BEDROCK
        
        if is_bedrock:
            pong = bedrock_unconnected_ping(
                host=("127.0.0.1" if not server.node_id else host),
                port=port,
                timeout_seconds=1.0,
            )
            parsed = parse_bedrock_pong_payload(pong.payload)
            # Convert online players dict to PlayerInfo list
            players_with_xuid = runtime_status.runtime_id and _ORCHESTRATOR.get_online_players_with_xuid(str(server.id))
            player_info_list = [
                PlayerInfo(name=name, xuid=xuid)
                for name, xuid in (players_with_xuid or {}).items()
            ]
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
                online_players_list=player_info_list,
            )
        else:
            from blockhost_backend.minecraft.java_ping import java_server_ping
            import time
            start = time.monotonic()
            protocol = get_protocol_version(server.mc_version) if server.mc_version else None
            java_pong = java_server_ping(
                host=("127.0.0.1" if not server.node_id else host),
                port=port,
                timeout_seconds=1.0,
                protocol_version=protocol,
            )
            latency = int((time.monotonic() - start) * 1000)
            if java_pong:
                players_node = java_pong.get("players", {})
                version_node = java_pong.get("version", {})
                motd_node = java_pong.get("description", "")
                if isinstance(motd_node, dict):
                    motd_node = motd_node.get("text", str(motd_node))
                
                return BedrockServerStats(
                    **base_stats,
                    reachable=True,
                    latency_ms=latency,
                    edition="Java",
                    motd=motd_node,
                    protocol=version_node.get("protocol"),
                    version=version_node.get("name"),
                    players_online=players_node.get("online"),
                    players_max=players_node.get("max"),
                    online_players_list=[],
                )
            else:
                return BedrockServerStats(
                    **base_stats,
                    reachable=False,
                    online_players_list=[],
                )
    except Exception:
        return BedrockServerStats(
            **base_stats,
            reachable=False,
            online_players_list=[],
        )


def _store_server_stats_snapshot(server_id: str, stats: BedrockServerStats) -> None:
    get_api_cache().set_json(
        _server_stats_cache_key(server_id),
        stats.model_dump(mode="json"),
        _STATS_REDIS_TTL_SECONDS,
    )


def _drop_server_stats_snapshot(server_id: str) -> None:
    get_api_cache().delete(_server_stats_cache_key(server_id))


def _cached_server_stats_snapshot(server_id: str) -> BedrockServerStats | None:
    cached_payload = get_api_cache().get_json(_server_stats_cache_key(server_id))
    if cached_payload is None:
        return None
    try:
        return BedrockServerStats.model_validate(cached_payload)
    except Exception:
        get_api_cache().delete(_server_stats_cache_key(server_id))
        return None


def _refresh_server_stats_snapshot(server_id: str) -> None:
    # Use a distributed lock to ensure only one worker refreshes stats concurrently
    lock = get_api_cache().lock(f"lock:stats:{server_id}", timeout=5)
    acquired = lock.acquire(blocking=False)
    if not acquired:
        return
    try:
        db = SessionLocal()
        try:
            try:
                server_uuid = uuid.UUID(server_id)
            except ValueError:
                return
            server = db.get(Server, server_uuid)
            if not server:
                return
            _store_server_stats_snapshot(server_id, _compute_server_stats(server, db))
        finally:
            db.close()
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _get_server_stats_snapshot(
    server: Server,
    db: Session,
    background_tasks: BackgroundTasks | None = None,
) -> BedrockServerStats:
    server_id = str(server.id)
    cached_stats = _cached_server_stats_snapshot(server_id)
    if cached_stats is not None:
        return cached_stats

    if background_tasks is None:
        stats = _compute_server_stats(server, db)
        _store_server_stats_snapshot(server_id, stats)
        return stats

    # Cold start (cache is empty):
    # Return placeholder stats immediately and trigger background refresh task.
    limits = get_effective_server_resource_limits(db=db, server=server)
    is_running = server.state == ServerState.running

    placeholder_stats = BedrockServerStats(
        host=server.vm_ipv4 or get_settings().minecraft_public_host,
        port=server.vm_port or get_settings().bedrock_port_range_start,
        allocated_ram_mb=limits["ram_mb"],
        allocated_cpu_cores=limits["cpu_quota_pct"] / 100.0,
        process_running=is_running,
        uptime_seconds=0,
        cpu_usage=0.0,
        ram_usage_mb=0.0,
        cpu_usage_percent=0.0,
        ram_usage_percent=0.0,
        reachable=False,
        online_players_list=[],
    )

    get_api_cache().set_json(
        _server_stats_cache_key(server_id),
        placeholder_stats.model_dump(mode="json"),
        _STATS_REDIS_TTL_SECONDS,
    )

    background_tasks.add_task(_refresh_server_stats_snapshot, server_id)

    return placeholder_stats


def _server_dirs() -> tuple[Path, Path, Path]:
    settings = get_settings()
    from blockhost_backend.config.config_manager import resolve_data_path

    return (
        resolve_data_path(settings.runtime_binaries_dir) / "bedrock",
        resolve_data_path(settings.bedrock_servers_dir),
        resolve_data_path(settings.bedrock_logs_dir),
    )


def _ensure_version_on_disk(*, versions_dir: Path, requested_version: str | None) -> None:
    settings = get_settings()
    if not requested_version or not str(requested_version).strip():
        return
    rv = str(requested_version).strip()
    if rv.lower() in {"recommended", "default"}:
        rv = recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    if rv.upper() in {"LATEST", "PREVIEW"}:
        installed_versions = [p.name for p in versions_dir.iterdir() if p.is_dir()] if versions_dir.exists() else []
        if not installed_versions:
            raise HTTPException(
                status_code=400,
                detail="No Bedrock versions are downloaded. Please download the recommended version first."
            )
        return
    dest = versions_dir / rv
    if not dest.exists() or not dest.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Version {rv} is not downloaded. Please download it via the versions page first."
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


def _collect_reserved_ports(
    *,
    db: Session,
    port_range: PortRange,
    flavor: ServerFlavor,
    node_id: uuid.UUID | None = None,
) -> set[int]:
    reserved: set[int] = set()
    is_bedrock = flavor == ServerFlavor.BEDROCK
    query = select(Server.vm_port, Server.flavor).where(Server.vm_port.between(port_range.start, port_range.end))
    if node_id:
        query = query.where(Server.node_id == node_id)
    for row in db.execute(query).all():
        port = row.vm_port
        server_flavor = row.flavor
        if port is None:
            continue
        if is_bedrock:
            if server_flavor != ServerFlavor.BEDROCK:
                continue
        elif not is_java_flavor(server_flavor):
            continue
        reserved.add(port)
        if server_flavor == ServerFlavor.BEDROCK and port + 1 <= port_range.end:
            reserved.add(port + 1)
    return reserved


def _validate_server_port_pairs(
    *,
    db: Session,
    port_range: PortRange,
    flavor: ServerFlavor,
    node_id: uuid.UUID | None = None,
) -> None:
    port_map: dict[int, list[str]] = {}
    errors: list[str] = []
    is_bedrock = flavor == ServerFlavor.BEDROCK

    query = select(Server.id, Server.vm_port, Server.flavor).where(Server.vm_port.between(port_range.start, port_range.end))
    if node_id:
        query = query.where(Server.node_id == node_id)
    for row in db.execute(query).all():
        server_id, port, server_flavor = row.id, row.vm_port, row.flavor
        if port is None:
            continue

        if is_bedrock:
            if server_flavor != ServerFlavor.BEDROCK:
                continue
        elif not is_java_flavor(server_flavor):
            continue

        if port < port_range.start or port > port_range.end:
            errors.append(
                f"Server {server_id} has vm_port={port} outside configured range {port_range.start}-{port_range.end}"
            )

        ports_to_check = [port]
        if server_flavor == ServerFlavor.BEDROCK:
            ipv6_port = port + 1
            if ipv6_port > port_range.end:
                errors.append(
                    f"Server {server_id} has vm_port={port} with invalid server-portv6={ipv6_port} beyond range end {port_range.end}"
                )
            ports_to_check.append(ipv6_port)

        for p in ports_to_check:
            if p <= port_range.end:
                port_map.setdefault(p, []).append(str(server_id))

    for p, servers in port_map.items():
        if len(servers) > 1:
            errors.append(
                f"Port {p} is reserved by multiple servers: {', '.join(servers)}"
            )

    if errors:
        raise RuntimeError("Invalid port assignments: " + "; ".join(errors))


def _allocate_port(*, db: Session, flavor: ServerFlavor, node_id: uuid.UUID | None = None) -> int:
    settings = get_settings()
    is_bedrock = flavor == ServerFlavor.BEDROCK
    port_range = PortRange(
        start=settings.bedrock_port_range_start if is_bedrock else settings.java_port_range_start,
        end=settings.bedrock_port_range_end if is_bedrock else settings.java_port_range_end,
    )
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(8742031)"))
    _validate_server_port_pairs(db=db, port_range=port_range, flavor=flavor, node_id=node_id)
    used = _collect_reserved_ports(db=db, port_range=port_range, flavor=flavor, node_id=node_id)
    if is_bedrock:
        return pick_free_udp_port(port_range=port_range, used_ports=used)
    else:
        return pick_free_tcp_port(port_range=port_range, used_ports=used)


def _server_props_from_config(*, server: Server, port: int) -> BedrockServerProperties:
    cfg = dict(server.mc_config or {})
    server_name = str(cfg.get("server_name") or server.world_name)
    return BedrockServerProperties(
        server_name=server_name,
        gamemode=cfg.get("gamemode"),
        difficulty=cfg.get("difficulty"),
        max_players=cfg.get("max_players"),
        allow_cheats=cfg.get("allow_cheats"),
        online_mode=False,
        level_name=cfg.get("level_name") or server.world_name,
        level_seed=cfg.get("level_seed"),
        enable_lan_visibility=False,
        server_port=port,
    )


def _java_server_properties_from_config(*, server: Server, port: int) -> JavaServerProperties:
    cfg = dict(server.mc_config or {})
    online_mode = cfg.get("online_mode")
    if online_mode is None:
        online_mode = False

    return JavaServerProperties(
        server_port=port,
        motd=cfg.get("motd") or server.world_name,
        max_players=cfg.get("max_players") or 20,
        gamemode=cfg.get("gamemode") or "survival",
        difficulty=cfg.get("difficulty") or "normal",
        online_mode=False,  # Enforced via JVM flag; property rewritten by server anyway
        level_name=cfg.get("level_name") or server.world_name,
        level_seed=cfg.get("level_seed") or "",
    )


def _prepare_java_server_for_start(*, server: Server, db: Session) -> Path:
    if server.state == ServerState.provisioning:
        raise HTTPException(
            status_code=409,
            detail="Server is still provisioning; check /provision-status and try again",
        )
    if not server.vm_port:
        try:
            server.vm_port = _allocate_port(db=db, flavor=server.flavor, node_id=server.node_id)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to allocate TCP port: {e}")

    _, servers_dir, _ = _server_dirs()
    
    server_dir = servers_dir / str(server.id)
    cfg = dict(server.mc_config or {})
    cfg["runtime_mode"] = "process"
    cfg["server_dir"] = str(server_dir)
    server.mc_config = cfg
    if db:
        db.commit()
    
    return server_dir


def _prepare_bedrock_server_for_start(*, server: Server, db: Session) -> Path:
    if not server.vm_port:
        try:
            server.vm_port = _allocate_port(db=db, flavor=ServerFlavor.BEDROCK, node_id=server.node_id)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

    requested_version = _normalize_requested_version(
        (server.mc_config or {}).get("bedrock_version")
    )
    
    # We MUST ensure the binary is installed on the Control Plane and synced to the Agent
    # because the Agent cannot download Bedrock binaries itself.
    from blockhost_backend.minecraft.binary_manager import ensure_binary_installed
    binary = ensure_binary_installed(db, ServerFlavor.BEDROCK, requested_version)
    version_dir = Path(binary.executable_path).parent
    version_name = binary.version

    _, servers_dir, _ = _server_dirs()
    server_dir = servers_dir / str(server.id)
    
    cfg = dict(server.mc_config or {})
    cfg["runtime_mode"] = "process"
    cfg["template_version"] = version_name
    cfg["server_dir"] = str(server_dir)
    server.mc_config = cfg
    if db:
        db.commit()

    if server.node_id:
        node = db.get(Node, server.node_id)
        if node:
            from blockhost_backend.services.node_provision import ensure_version_on_node
            ensure_version_on_node(node=node, version_name=version_name, version_dir=version_dir)

    return server_dir


def _start_server_process(*, server: Server, settings, db: Session | None = None) -> None:
    from blockhost_backend.orchestrator.runtime_cache import invalidate_server_runtime_cache

    invalidate_server_runtime_cache(server.id)
    _, servers_dir, _ = _server_dirs()

    if is_java_flavor(server.flavor):
        mc_config = server.mc_config or {}
        server_dir = (Path(mc_config.get("server_dir", "")) if mc_config.get("server_dir") else None)
        if server_dir is not None:
            props_path = server_dir / "server.properties"
            if props_path.exists():
                pass # Handled by JVM flag

    _ORCHESTRATOR.start_server(server=server, settings=settings, servers_dir=servers_dir, db=db)

def _stop_server_process(server: Server) -> None:
    """Delegate process lifecycle to the orchestrator/runtime abstraction."""
    _ORCHESTRATOR.stop_server(server)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    payload: CreateServerRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerOut:
    settings = get_settings()
    _guard_disk_for_operation("create_server")

    
    

    config = payload.config.model_dump(exclude_none=True) if payload.config else {}
    config.pop("bedrock_image", None)  # Docker-only field, never accepted

    # Sanitize version from config before touching disk
    raw_version = config.get("bedrock_version")
    try:
        config["bedrock_version"] = _sanitize_version(raw_version)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    node = select_best_node(db)
    if settings.production_mode and not node:
        raise HTTPException(
            status_code=503,
            detail="No online worker node is available. Check agent registration and heartbeat.",
        )
    node_id = node.id if node else None

    try:
        port = _allocate_port(db=db, flavor=payload.flavor, node_id=node_id)
    except Exception as e:
        proto = "UDP" if payload.flavor == ServerFlavor.BEDROCK else "TCP"
        raise HTTPException(status_code=503, detail=f"Failed to allocate {proto} port: {e}")

    server = Server(
        owner_id=user.id,
        node_id=node_id,
        world_name=payload.world_name,
        join_code="placeholder",
        state=ServerState.created,
        vm_provider=VMProvider.local_bedrock if payload.flavor == ServerFlavor.BEDROCK else VMProvider.local_java, 
        vm_ipv4=node.ip_address if node else None,
        vm_port=port,
        mc_config=config,
        flavor=payload.flavor,
        mc_version=payload.mc_version,
        java_version=required_java_version(payload.mc_version) if is_java_flavor(payload.flavor) else None,
    )
    db.add(server)
    db.flush() 

    server.join_code = generate_join_code(user.id, server.id)

    versions_dir, servers_dir, _logs_dir = _server_dirs()
    try:
        cfg = dict(server.mc_config or {})
        server_dir = servers_dir / str(server.id)
        
        if payload.flavor == ServerFlavor.BEDROCK:
            requested_version = _normalize_requested_version(cfg.get("bedrock_version"))
            cfg["runtime_mode"] = "process"
            cfg["template_version"] = requested_version or "latest"
            cfg["server_dir"] = str(server_dir)
            server.mc_config = cfg
        else:
            cfg["runtime_mode"] = "process"
            cfg["server_dir"] = str(server_dir)
            server.mc_config = cfg

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=503, detail=f"Failed to setup server config: {e}"
        )

    # Server is created suspended — user must subscribe via Billing, then POST /start.
    if server.state != ServerState.provisioning:
        server.state = ServerState.created
    db.commit()
    db.refresh(server)
    if server.node_id:
        refresh_node_allocated_ram(db, server.node_id)
        db.commit()
    _invalidate_server_read_cache(user.id, server.id)
    return _server_to_out(server, owner=user)


def _provision_java_server(server_id: str, jar_url: str, jar_path_str: str, server_dir_str: str, port: int) -> None:
    from blockhost_backend.database.db import SessionLocal
    from blockhost_backend.minecraft.software_provider import download_file

    server_uuid = uuid.UUID(server_id)
    server_dir = Path(server_dir_str)
    jar_path = Path(jar_path_str)
    try:
        download_file(jar_url, jar_path)
        (server_dir / "eula.txt").write_text("eula=true\n")

        with SessionLocal() as db:
            server = db.get(Server, server_uuid)
            if server:
                props = _java_server_properties_from_config(server=server, port=port)
                props_path = server_dir / "server.properties"
                write_java_server_properties(props_path, props)
                set_java_server_property(props_path, "online-mode", False)
                cfg = dict(server.mc_config or {})
                cfg.pop("provision_error", None)
                server.mc_config = cfg
                server.state = ServerState.created
                db.commit()
                if server.node_id:
                    node = db.get(Node, server.node_id)
                    if node:
                        from blockhost_backend.services.node_provision import provision_remote_java_server

                        provision_remote_java_server(server=server, server_dir=server_dir, node=node)
    except Exception as exc:
        logger.exception("Failed to provision java server %s", server_id)
        with SessionLocal() as db:
            server = db.get(Server, server_uuid)
            if server:
                cfg = dict(server.mc_config or {})
                cfg["provision_error"] = str(exc)
                server.mc_config = cfg
                server.state = ServerState.suspended
                db.commit()


@router.get("/{server_id}/provision-status")
def get_provision_status(
    server_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    if server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    cfg = server.mc_config or {}
    server_dir = cfg.get("server_dir")
    jar_ready = False
    if server_dir:
        jar_ready = (Path(server_dir) / "server.jar").is_file()

    return {
        "state": server.state.value,
        "server_dir": server_dir,
        "jar_ready": jar_ready,
        "provision_error": cfg.get("provision_error"),
    }


@router.post("/{server_id}/reprovision", response_model=ServerActionResponse)
def reprovision_java_server(
    server_id: str,
    background_tasks: BackgroundTasks,
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
    if not is_java_flavor(server.flavor):
        raise HTTPException(status_code=400, detail="Only Java servers can be reprovisioned")
    if not server.mc_version:
        raise HTTPException(status_code=400, detail="Server has no mc_version")

    _, servers_dir, _ = _server_dirs()
    server_dir = servers_dir / str(server.id)
    server_dir.mkdir(parents=True, exist_ok=True)
    jar_path = server_dir / "server.jar"

    from blockhost_backend.minecraft.software_provider import resolve_jar_url

    try:
        jar_url = resolve_jar_url(server.flavor, server.mc_version)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to resolve JAR URL: {e}")

    cfg = dict(server.mc_config or {})
    cfg["server_dir"] = str(server_dir)
    cfg["executable_name"] = "server.jar"
    cfg.pop("provision_error", None)
    server.mc_config = cfg
    server.state = ServerState.provisioning
    db.commit()

    background_tasks.add_task(
        _provision_java_server,
        str(server.id),
        jar_url,
        str(jar_path),
        str(server_dir),
        server.vm_port or 25565,
    )
    _invalidate_server_read_cache(user.id, server.id)
    return ServerActionResponse(id=server.id, state=server.state)

@router.post("/{server_id}/switch-software", response_model=ServerActionResponse)
def switch_server_software(
    server_id: str,
    payload: SoftwareSwitchRequest,
    background_tasks: BackgroundTasks,
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

    is_currently_java = is_java_flavor(server.flavor)
    target_is_java = is_java_flavor(payload.target_flavor)

    if is_currently_java != target_is_java:
        raise HTTPException(status_code=400, detail="Cross-platform switching (Java <-> Bedrock) is not supported.")

    server.flavor = payload.target_flavor
    
    if target_is_java:
        server.mc_version = payload.target_mc_version
        
        _, servers_dir, _ = _server_dirs()
        server_dir = servers_dir / str(server.id)
        server_dir.mkdir(parents=True, exist_ok=True)
        jar_path = server_dir / "server.jar"

        from blockhost_backend.minecraft.software_provider import resolve_jar_url

        try:
            jar_url = resolve_jar_url(server.flavor, server.mc_version)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to resolve JAR URL: {e}")

        cfg = dict(server.mc_config or {})
        cfg["server_dir"] = str(server_dir)
        cfg["executable_name"] = "server.jar"
        cfg.pop("provision_error", None)
        server.mc_config = cfg
        server.state = ServerState.provisioning
        db.commit()

        background_tasks.add_task(
            _provision_java_server,
            str(server.id),
            jar_url,
            str(jar_path),
            str(server_dir),
            server.vm_port or 25565,
        )
    else:
        # Bedrock
        cfg = dict(server.mc_config or {})
        try:
            cfg["bedrock_version"] = _sanitize_version(payload.target_mc_version)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        
        server.mc_config = cfg
        db.commit()

    _invalidate_server_read_cache(user.id, server.id)
    return ServerActionResponse(id=server.id, state=server.state)


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
    cache = get_api_cache()
    cache_key = _server_list_cache_key(user.id)
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    # Run discovery first (logs orphans, does not assign them to this user)
    _, servers_dir, _ = _server_dirs()
    _discover_filesystem_servers(servers_dir=servers_dir, db=db)

    # Fetch after discovery so the query reflects any newly committed rows
    servers = db.execute(
        select(Server).where(Server.owner_id == user.id)
    ).scalars().all()
    payload = [_server_to_out(s, owner=user).model_dump(mode="json") for s in servers]
    cache.set_json(cache_key, payload, _SERVER_LIST_TTL_SECONDS)
    return payload





@router.get("/{server_id}", response_model=ServerStateSnapshot)
def get_server(
    server_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerStateSnapshot:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    cache = get_api_cache()
    cache_key = _server_detail_cache_key(user.id, server.id)
    cached_detail = cache.get_json(cache_key)
    if cached_detail is None:
        cached_detail = {
            "server": _server_to_detail(server, owner=user).model_dump(mode="json"),
            "config": server.mc_config or {},
        }
        cache.set_json(cache_key, cached_detail, _SERVER_DETAIL_TTL_SECONDS)
    return ServerStateSnapshot(
        server=cached_detail["server"],
        config=cached_detail["config"],
        stats=_get_server_stats_snapshot(server, db, background_tasks),
    )


def _provision_remote_server(server: Server, server_dir: Path, db: Session, version_name: str, version_dir: Path) -> None:
    if not server.node_id:
        return
    node = db.get(Node, server.node_id)
    if not node:
        return

    from blockhost_backend.services.node_provision import provision_remote_server

    provision_remote_server(
        server=server,
        server_dir=server_dir,
        node=node,
        version_name=version_name,
        version_dir=version_dir,
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
        is_actually_running = _ORCHESTRATOR.is_running(str(server.id))

    if is_actually_running:
        return ServerActionResponse(id=server.id, state=server.state)

    settings = get_settings()
    if not server.node_id:
        node = select_best_node(db)
        if node:
            server.node_id = node.id
            server.vm_ipv4 = node.ip_address
            db.commit()
        elif settings.production_mode:
            raise HTTPException(
                status_code=503,
                detail="Server is not assigned to a worker node. Recreate it after the agent is online.",
            )
    is_java = is_java_flavor(server.flavor)

    try:
        if is_java:
            _prepare_java_server_for_start(server=server, db=db)
        else:
            server.vm_provider = VMProvider.local_bedrock
            _prepare_bedrock_server_for_start(server=server, db=db)
    except HTTPException:
        raise
    except Exception as e:
        detail = "Failed to prepare Java server" if is_java else "Failed to materialize Bedrock server folder"
        raise HTTPException(status_code=503, detail=f"{detail}: {e}")

    try:
        _start_server_process(server=server, settings=settings, db=db)
    except BillingError as e:
        server.state = ServerState.suspended
        _drop_server_stats_snapshot(str(server.id))
        raise HTTPException(status_code=402, detail={"error": e.code, "message": e.message})
    except Exception as e:
        import traceback
        logger.error("start_server FAILED for %s: %s\n%s", server_id, e, traceback.format_exc())
        server.state = ServerState.suspended
        _drop_server_stats_snapshot(str(server.id))
        label = "Java" if is_java else "Bedrock"
        raise HTTPException(status_code=503, detail=f"Failed to start {label} process: {e}")

    db.commit()
    _drop_server_stats_snapshot(str(server.id))
    _invalidate_server_read_cache(user.id, server.id)
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
    _drop_server_stats_snapshot(str(server.id))
    _invalidate_server_read_cache(user.id, server.id)
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
        is_actually_running = _ORCHESTRATOR.is_running(str(server.id))

    settings = get_settings()
    if is_actually_running:
        _stop_server_process(server)
    else:
        if not server.node_id:
            node = select_best_node(db)
            if node:
                server.node_id = node.id
                server.vm_ipv4 = node.ip_address
                db.commit()
            elif settings.production_mode:
                raise HTTPException(
                    status_code=503,
                    detail="Server is not assigned to a worker node. Recreate it after the agent is online.",
                )
        is_java = is_java_flavor(server.flavor)
        try:
            if is_java:
                _prepare_java_server_for_start(server=server, db=db)
            else:
                _prepare_bedrock_server_for_start(server=server, db=db)
            _start_server_process(server=server, settings=settings, db=db)
        except BillingError as e:
            server.state = ServerState.suspended
            _drop_server_stats_snapshot(str(server.id))
            raise HTTPException(
                status_code=402, detail={"error": e.code, "message": e.message}
            )
        except HTTPException:
            raise
        except Exception as e:
            import traceback
            logger.error("toggle_server FAILED for %s: %s\n%s", server_id, e, traceback.format_exc())
            server.state = ServerState.suspended
            _drop_server_stats_snapshot(str(server.id))
            label = "Java" if is_java else "Bedrock"
            raise HTTPException(
                status_code=503, detail=f"Failed to start {label} process: {e}"
            )

    db.commit()
    _drop_server_stats_snapshot(str(server.id))
    _invalidate_server_read_cache(user.id, server.id)
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
    cache = get_api_cache()
    cache_key = _server_config_cache_key(user.id, server.id)
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached
    payload = ServerConfigOut(id=server.id, mc_config=server.mc_config or {}).model_dump(mode="json")
    cache.set_json(cache_key, payload, _SERVER_CONFIG_TTL_SECONDS)
    return payload


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
    _invalidate_server_read_cache(user.id, server.id)
    return ServerConfigOut(id=server.id, mc_config=server.mc_config or {})


@router.get("/{server_id}/stats", response_model=BedrockServerStats)
def get_server_stats(
    server_id: str,
    background_tasks: BackgroundTasks,
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
    return _get_server_stats_snapshot(server, db, background_tasks)



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

    # Strip dict structure to string for backwards compatibility, or clients that just want strings
    return [entry.get("line", "") for entry in _ORCHESTRATOR.read_logs(str(server.id), tail=tail)]

@router.websocket("/{server_id}/console/ws")
async def console_websocket(
    websocket: WebSocket,
    server_id: str,
    token: str | None = None,
    db: Session = Depends(get_db),
):
    user = authenticate_ws_user(token=token, db=db)
    if user is None:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        await websocket.close(code=4004, reason="Invalid server ID")
        return

    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        await websocket.close(code=4003, reason="Not authorized")
        return

    if not _ORCHESTRATOR.is_running(server_id):
        await websocket.close(code=4000, reason="Server is not running")
        return

    await websocket.accept()

    listener_callback = None
    try:
        import dataclasses
        from blockhost_backend.config.config_manager import get_settings

        settings = get_settings()

        history = _ORCHESTRATOR.read_logs(server_id, tail=500)
        await websocket.send_json({"type": "history", "logs": [dataclasses.asdict(e) for e in history]})

        queue = asyncio.Queue(maxsize=settings.console_queue_max_size)
        loop = asyncio.get_running_loop()

        def listener_callback(entry):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, dataclasses.asdict(entry))
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    loop.call_soon_threadsafe(queue.put_nowait, dataclasses.asdict(entry))
                except asyncio.QueueEmpty:
                    pass

        _ORCHESTRATOR.add_log_listener(server_id, listener_callback)

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
                            command = str(msg["command"]).replace("\r", "").replace("\n", " ").strip()
                            if command and len(command) <= 512:
                                _ORCHESTRATOR.send_command(server_id, command)
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

    finally:
        if listener_callback is not None:
            _ORCHESTRATOR.remove_log_listener(server_id, listener_callback)



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
    try:
        _ORCHESTRATOR.send_command(server_id, cmd)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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


@router.get("/{server_id}/bans", response_model=BanListOut)
def list_bans(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _require_server(server_id, user, db)
    _expire_expired_bans(server.id, db)
    bans = db.scalars(
        select(Ban)
        .where(Ban.server_id == server.id, Ban.active == True)
        .order_by(Ban.banned_at.desc())
    ).all()
    return BanListOut(items=[_ban_to_out(ban) for ban in bans])


@router.get("/{server_id}/bans/history", response_model=BanListOut)
def list_ban_history(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _require_server(server_id, user, db)
    _expire_expired_bans(server.id, db)
    bans = db.scalars(
        select(Ban)
        .where(Ban.server_id == server.id)
        .order_by(Ban.banned_at.desc())
    ).all()
    return BanListOut(items=[_ban_to_out(ban) for ban in bans])


@router.get("/{server_id}/bans/check", response_model=BanCheckResponse)
def check_ban(server_id: str, xuid: str = Query(..., min_length=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _require_server(server_id, user, db)
    ban = _resolve_active_ban(server.id, xuid, db)
    if not ban:
        return BanCheckResponse(banned=False)
    return BanCheckResponse(
        banned=True,
        ban_id=ban.id,
        xuid=ban.xuid,
        player_name=ban.player_name,
        reason=ban.reason,
        expires_at=ban.expires_at,
        active=ban.active,
    )


@router.post("/{server_id}/bans", response_model=BanOut)
def create_ban(server_id: str, payload: BanCreateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _require_server(server_id, user, db)
    expires_at = payload.expires_at
    if expires_at is None and payload.duration_seconds is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=payload.duration_seconds)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Expiration must be in the future")

    existing = db.scalars(
        select(Ban)
        .where(Ban.server_id == server.id, Ban.xuid == payload.xuid, Ban.active == True)
        .limit(1)
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Player is already banned")

    ban = Ban(
        server_id=server.id,
        xuid=payload.xuid,
        player_name=payload.player_name,
        reason=payload.reason,
        active=True,
        expires_at=expires_at,
        created_by_user_id=user.id,
    )
    db.add(ban)
    db.commit()
    db.refresh(ban)

    from blockhost_backend.services.ban_service import ban_service
    ban_service.kick_player_if_online(str(server.id), payload.xuid, payload.player_name, payload.reason, _ORCHESTRATOR)

    return _ban_to_out(ban)


@router.delete("/{server_id}/bans/{ban_id}", response_model=BanOut)
def delete_ban(server_id: str, ban_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _require_server(server_id, user, db)
    try:
        ban_uuid = uuid.UUID(ban_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ban not found")
    ban = db.get(Ban, ban_uuid)
    if not ban or ban.server_id != server.id:
        raise HTTPException(status_code=404, detail="Ban not found")
    if ban.active:
        ban.active = False
        ban.unbanned_at = datetime.now(timezone.utc)
        ban.unbanned_by_user_id = user.id
        db.add(ban)
        db.commit()
        db.refresh(ban)
    return _ban_to_out(ban)


@router.get("/{server_id}/blocklist")
def get_blocklist(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_server(server_id, user, db)
    # Mocking blocklist for now, since vanilla BDS doesn't track this neatly via command
    return {"players": []}
