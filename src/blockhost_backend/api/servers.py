from __future__ import annotations

from blockhost_backend.database.schema import Node, ServerCollaborator, ServerFlavor
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
    ServerCollaboratorCreate,
    ServerCollaboratorOut,
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
from blockhost_backend.database.schema import Node, NodeState, Server, ServerState, User, VMProvider
from blockhost_backend.minecraft.bedrock_ping import bedrock_unconnected_ping, parse_bedrock_pong_payload
from blockhost_backend.minecraft.bedrock_properties import BedrockServerProperties, write_server_properties
from blockhost_backend.minecraft.bedrock_download import list_remote_versions
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
from blockhost_backend.services.plan_limits import clamp_max_players
from blockhost_backend.services.s3_storage import delete_migration_snapshot
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






def _get_server_for_user(server_id: str, user: User, db: Session, required_permission: str | None = None) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if server.owner_id == user.id:
        return server
        
    # Check collaborators
    from blockhost_backend.database.schema import ServerCollaborator
    from sqlalchemy import select
    collab = db.execute(
        select(ServerCollaborator).where(
            ServerCollaborator.server_id == server.id,
            ServerCollaborator.user_id == user.id
        )
    ).scalars().first()
    
    if not collab:
        raise HTTPException(status_code=404, detail="Server not found")
        
    if required_permission and required_permission not in collab.permissions:
        raise HTTPException(status_code=403, detail=f"Missing required permission: {required_permission}")
        
    return server

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
    if settings.proxy_enabled and settings.proxy_host and server.proxy_port:
        shareable_address = f"{settings.proxy_host}:{server.proxy_port}"
    elif host and server.vm_port:
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
            # For remote nodes, proxy the Bedrock UDP ping through the agent
            # (the control plane can't reliably send UDP across Docker NAT / AWS SGs).
            # For local nodes, ping directly.
            parsed: dict[str, object] = {}
            ping_latency: int | None = None
            ping_reachable = False

            if server.node_id:
                # Remote node: ask the agent to ping locally
                pong_data = _ORCHESTRATOR.ping_server(str(server.id))
                if pong_data and pong_data.get("reachable"):
                    ping_reachable = True
                    ping_latency = pong_data.get("latency_ms")
                    parsed = pong_data
            else:
                # Local node: ping directly
                try:
                    pong = bedrock_unconnected_ping(
                        host="127.0.0.1", port=port, timeout_seconds=1.0,
                    )
                    parsed = parse_bedrock_pong_payload(pong.payload)
                    ping_latency = pong.latency_ms
                    ping_reachable = True
                except Exception:
                    pass  # ping failed — still return base stats below

            # Always try to get the player list from the runtime (agent HTTP),
            # independent of whether the UDP ping succeeded.
            players_with_xuid = runtime_status.runtime_id and _ORCHESTRATOR.get_online_players_with_xuid(str(server.id))
            player_info_list = [
                PlayerInfo(name=name, xuid=xuid)
                for name, xuid in (players_with_xuid or {}).items()
            ]
            return BedrockServerStats(
                **base_stats,
                reachable=ping_reachable,
                latency_ms=ping_latency,
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
            ping_host = "127.0.0.1" if not server.node_id else host
            java_pong = java_server_ping(
                host=ping_host,
                port=port,
                timeout_seconds=1.0,
                protocol_version=protocol,
            )
            latency = int((time.monotonic() - start) * 1000)

            # Get player list from runtime (journal log stream parsing)
            players_with_xuid = runtime_status.runtime_id and _ORCHESTRATOR.get_online_players_with_xuid(str(server.id))
            player_info_list = [
                PlayerInfo(name=name, xuid=xuid)
                for name, xuid in (players_with_xuid or {}).items()
            ]

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
                    online_players_list=player_info_list,
                )
            else:
                return BedrockServerStats(
                    **base_stats,
                    reachable=False,
                    online_players_list=player_info_list,
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

    # Cold cache must compute real stats. Caching a placeholder here causes the
    # panel to show data briefly, then replace it with empty values for the
    # 5-second stats TTL.
    stats = _compute_server_stats(server, db)
    _store_server_stats_snapshot(server_id, stats)
    return stats


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
        rvs = list_remote_versions()
        rv = rvs[0] if rvs else "1.21"
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
        rvs = list_remote_versions()
        return rvs[0] if rvs else "1.21"
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


def _allocate_proxy_port(db: Session) -> int | None:
    """Allocate a globally unique proxy port from the dedicated range.

    This port stays with the server forever — it is the stable address
    players use to connect through the proxy, regardless of which node
    the server is currently running on.
    Returns None if the proxy feature is disabled.
    """
    settings = get_settings()
    if not settings.proxy_enabled:
        return None

    start = settings.proxy_port_range_start
    end = settings.proxy_port_range_end

    # Lock to prevent races in Postgres
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(8742032)"))

    used = set(
        db.execute(
            select(Server.proxy_port).where(
                Server.proxy_port.is_not(None),
                Server.proxy_port.between(start, end),
            )
        ).scalars().all()
    )

    for candidate in range(start, end + 1):
        if candidate not in used:
            return candidate

    raise RuntimeError(
        f"No free proxy port in range {start}–{end} ({len(used)} ports used)"
    )


def _server_props_from_config(*, server: Server, port: int, db: Session | None = None) -> BedrockServerProperties:
    cfg = dict(server.mc_config or {})
    server_name = str(cfg.get("server_name") or server.world_name)
    max_players = clamp_max_players(cfg.get("max_players"), db=db, server=server)
    return BedrockServerProperties(
        server_name=server_name,
        gamemode=cfg.get("gamemode"),
        difficulty=cfg.get("difficulty"),
        max_players=max_players,
        allow_cheats=cfg.get("allow_cheats"),
        online_mode=False,
        level_name=cfg.get("level_name") or server.world_name,
        level_seed=cfg.get("level_seed"),
        enable_lan_visibility=False,
        server_port=port,
    )


def _java_server_properties_from_config(*, server: Server, port: int, db: Session | None = None) -> JavaServerProperties:
    cfg = dict(server.mc_config or {})
    max_players = clamp_max_players(cfg.get("max_players"), db=db, server=server, default=20) or 1

    return JavaServerProperties(
        server_port=port,
        motd=cfg.get("motd") or server.world_name,
        max_players=max_players,
        gamemode=cfg.get("gamemode") or "survival",
        difficulty=cfg.get("difficulty") or "normal",
        online_mode=False,  # Enforced via JVM flag; property rewritten by server anyway
        level_name=cfg.get("level_name") or server.world_name,
        level_seed=cfg.get("level_seed") or "",
        enable_rcon=True,
        rcon_port=25575,  # We can just use 25575 since it only binds to localhost
        rcon_password=uuid.uuid4().hex,
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
    
    version_name = requested_version or "latest"

    _, servers_dir, _ = _server_dirs()
    server_dir = servers_dir / str(server.id)
    
    cfg = dict(server.mc_config or {})
    cfg["runtime_mode"] = "process"
    cfg["template_version"] = version_name
    cfg["server_dir"] = str(server_dir)
    server.mc_config = cfg
    if db:
        db.commit()

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

    max_servers = settings.max_servers_per_user
    if max_servers > 0:
        owned = db.execute(
            select(func.count()).select_from(Server).where(Server.owner_id == user.id)
        ).scalar_one()
        if owned >= max_servers:
            raise HTTPException(
                status_code=403,
                detail=f"Server limit reached ({max_servers}). Delete an existing server or contact support.",
            )

    config = payload.config.model_dump(exclude_none=True) if payload.config else {}
    config.pop("bedrock_image", None)  # Docker-only field, never accepted

    # Sanitize version from config before touching disk
    raw_version = config.get("bedrock_version")
    try:
        config["bedrock_version"] = _sanitize_version(raw_version)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Unpaid servers reserve 0 RAM until a plan is active; still place on freest node.
    node = select_best_node(db)
    if not node:
        # All online nodes are full! Try to wake up an offline node.
        from blockhost_backend.services.node_capacity import auto_wakeup_offline_node
        woken = auto_wakeup_offline_node(db)
        if woken:
            raise HTTPException(
                status_code=202,
                detail="Network is scaling up! A new server node is booting for you. Please wait 60 seconds and try creating your server again."
            )
        elif settings.production_mode:
            raise HTTPException(
                status_code=503,
                detail="All server nodes are full and no offline nodes are available to scale up.",
            )
            
    node_id = node.id if node else None

    try:
        port = _allocate_port(db=db, flavor=payload.flavor, node_id=node_id)
    except Exception as e:
        proto = "UDP" if payload.flavor == ServerFlavor.BEDROCK else "TCP"
        raise HTTPException(status_code=503, detail=f"Failed to allocate {proto} port: {e}")

    proxy_port = _allocate_proxy_port(db)

    server = Server(
        owner_id=user.id,
        node_id=node_id,
        world_name=payload.world_name,
        join_code="placeholder",
        state=ServerState.created,
        vm_provider=VMProvider.local_bedrock if payload.flavor == ServerFlavor.BEDROCK else VMProvider.local_java, 
        vm_ipv4=node.ip_address if node else None,
        vm_port=port,
        proxy_port=proxy_port,
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

    # Free Trial Logic: grant a 7-day cardless trial if there's a free plan
    from blockhost_backend.database.schema import BillingPlan, BillingSubscription, BillingSubscriptionStatus
    from datetime import timedelta
    from blockhost_backend.database.schema import utcnow
    
    free_plan = db.execute(
        select(BillingPlan).where(BillingPlan.price == 0, BillingPlan.active == True).limit(1)
    ).scalar_one_or_none()
    
    if free_plan:
        now = utcnow()
        trial_sub = BillingSubscription(
            user_id=user.id,
            server_id=server.id,
            plan_id=free_plan.id,
            starts_at=now,
            expires_at=now + timedelta(days=7),
            status=BillingSubscriptionStatus.active,
        )
        db.add(trial_sub)
        db.flush()
        
        # Apply limits to mc_config
        cfg = dict(server.mc_config or {})
        cfg["billing_plan_id"] = free_plan.id
        cfg["resource_limits"] = {
            "ram_mb": free_plan.ram_mb,
            "cpu_quota_pct": free_plan.cpu_limit,
            "storage_mb": free_plan.storage_mb,
            "player_limit": free_plan.player_limit,
        }
        server.mc_config = cfg
        db.commit()

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
                props = _java_server_properties_from_config(server=server, port=port, db=db)
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
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")
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
    server = _get_server_for_user(server_id, user, db, required_permission="config")

    is_currently_java = is_java_flavor(server.flavor)
    target_is_java = is_java_flavor(payload.target_flavor)

    if is_currently_java != target_is_java:
        raise HTTPException(status_code=400, detail="Cross-platform switching (Java <-> Bedrock) is not supported.")

    # Stop the server if it's currently running
    if server.state == ServerState.running:
        _stop_server_process(server)

    server.flavor = payload.target_flavor
    
    if target_is_java:
        server.mc_version = payload.target_mc_version
        
        # Tell the Agent to delete any existing JARs so the new version starts fresh
        if server.node_id:
            try:
                node = db.get(Node, server.node_id)
                if node:
                    settings = get_settings()
                    base = f"http://{node.ip_address}:{node.agent_port}"
                    headers = {"Authorization": f"Bearer {settings.worker_agent_token}"}
                    import httpx
                    httpx.post(f"{base}/agent/servers/{server.id}/clear-jars", headers=headers, timeout=10.0)
            except Exception as e:
                logger.warning(f"Failed to clear old jars on agent for server {server.id}: {e}")

        cfg = dict(server.mc_config or {})
        cfg.pop("provision_error", None)
        server.mc_config = cfg
        server.state = ServerState.suspended
        db.commit()

    else:
        # Bedrock
        cfg = dict(server.mc_config or {})
        try:
            cfg["bedrock_version"] = _sanitize_version(payload.target_mc_version)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        
        server.mc_config = cfg
        server.state = ServerState.suspended
        db.commit()

    _invalidate_server_read_cache(user.id, server.id)
    return ServerActionResponse(id=server.id, state=server.state)





@router.get("", response_model=list[ServerOut])
def list_servers(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ServerOut]:
    cache = get_api_cache()
    cache_key = _server_list_cache_key(user.id)
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

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
    server = _get_server_for_user(server_id, user, db)
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

def _do_start_server(server: Server, db: Session) -> None:
    settings = get_settings()
    
    from blockhost_backend.services.billing import ensure_active_subscription_for_start, BillingError
    try:
        ensure_active_subscription_for_start(db=db, server=server)
    except BillingError as e:
        server.state = ServerState.suspended
        _drop_server_stats_snapshot(str(server.id))
        raise HTTPException(status_code=402, detail={"error": e.code, "message": e.message})

    # Automatic Failover: find the right node to start this server on
    needs_node_assignment = False

    if server.node_id:
        node = db.get(Node, server.node_id)
        # Check if node is dead
        is_dead = not node or node.status in (NodeState.offline, NodeState.starting)
        
        # Check if node is full
        is_full = False
        if node:
            from blockhost_backend.services.node_capacity import compute_node_allocated_ram_mb
            active_ram = compute_node_allocated_ram_mb(db, node.id)
            limits = get_effective_server_resource_limits(db=db, server=server)
            required_ram = int(limits.get("ram_mb") or 0)
            if node.total_ram_mb - active_ram < required_ram:
                is_full = True
                
        if is_dead or is_full:
            logger.info(f"Node {node.name if node else 'Unknown'} is dead or full. Detaching server {server.id} to find a new node.")
            server.node_id = None
            server.vm_ipv4 = None
            # DO NOT set server.vm_port = None because the DB schema enforces NOT NULL on vm_port.
            db.commit()
            needs_node_assignment = True
    else:
        needs_node_assignment = True

    if needs_node_assignment:
        limits = get_effective_server_resource_limits(db=db, server=server)
        required_ram = int(limits.get("ram_mb") or 0)
        new_node = select_best_node(db, required_ram_mb=required_ram)
        if new_node:
            server.node_id = new_node.id
            server.vm_ipv4 = new_node.ip_address
            server.state = ServerState.provisioning
            refresh_node_allocated_ram(db, new_node.id)
            try:
                server.vm_port = _allocate_port(db=db, flavor=server.flavor, node_id=server.node_id)
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"Failed to allocate port on new node: {e}")
            db.commit()
            logger.info("Assigned server %s to node %s (EFS storage)", server.id, new_node.name)
        elif settings.production_mode:
            from blockhost_backend.services.node_capacity import auto_wakeup_offline_node
            if auto_wakeup_offline_node(db):
                # Keep the server on its current node (don't set to None!)
                # Phase 0 or the next retry will handle it once the new node is online.
                raise HTTPException(
                    status_code=503,
                    detail="Network is scaling up! A new node is booting. Please wait 60 seconds and try again.",
                )
            else:
                raise HTTPException(
                    status_code=503,
                    detail="No capacity is currently available. Please try again shortly.",
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

    # Update activity so Auto-Sleeper doesn't instantly kill it
    from blockhost_backend.database.schema import utcnow
    server.last_activity = utcnow()
    db.add(server)
    db.commit()

    try:
        _start_server_process(server=server, settings=settings, db=db)
    except BillingError as e:
        server.state = ServerState.suspended
        _drop_server_stats_snapshot(str(server.id))
        raise HTTPException(status_code=402, detail={"error": e.code, "message": e.message})
    except Exception as e:
        server.state = ServerState.suspended
        _drop_server_stats_snapshot(str(server.id))
        label = "Java" if is_java else "Bedrock"
        logger.exception(f"Failed to start {label} process")
        raise HTTPException(status_code=503, detail=f"Failed to start {label} process: {e}")


@router.post("/{server_id}/start", response_model=ServerActionResponse)
def start_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerActionResponse:
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")

    is_actually_running = False
    if server.state == ServerState.running:
        is_actually_running = _ORCHESTRATOR.is_running(str(server.id))

    if is_actually_running:
        return ServerActionResponse(id=server.id, state=server.state)

    _do_start_server(server, db)

    db.commit()
    if server.node_id:
        from blockhost_backend.services.node_capacity import refresh_node_allocated_ram
        refresh_node_allocated_ram(db, server.node_id)
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
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")

    _stop_server_process(server)
    db.commit()
    if server.node_id:
        from blockhost_backend.services.node_capacity import refresh_node_allocated_ram
        refresh_node_allocated_ram(db, server.node_id)
        db.commit()
        
    _drop_server_stats_snapshot(str(server.id))
    _invalidate_server_read_cache(user.id, server.id)
    return ServerActionResponse(id=server.id, state=server.state)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Stop, purge agent files, remove DB row, and delete the S3 migration snapshot."""
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")
    sid = str(server.id)
    server_uuid = server.id
    node_id = server.node_id
    owner_id = server.owner_id

    try:
        if server.state == ServerState.running:
            _stop_server_process(server)
    except Exception:
        logger.exception("Failed to stop server %s before delete", sid)

    if node_id:
        node = db.get(Node, node_id)
        if node and node.ip_address:
            try:
                from blockhost_backend.runtime.agent_runtime import AgentRuntime

                AgentRuntime(
                    agent_base_url=f"http://{node.ip_address}:{node.agent_port}",
                    agent_token=get_settings().worker_agent_token,
                ).purge_server_data(sid)
            except Exception:
                logger.exception("Failed to purge agent data for %s on node %s", sid, node.name)

    db.delete(server)
    db.commit()

    if node_id:
        refresh_node_allocated_ram(db, node_id)
        db.commit()

    delete_migration_snapshot(sid)
    _drop_server_stats_snapshot(sid)
    _invalidate_server_read_cache(owner_id, server_uuid)


@router.post("/{server_id}/toggle", response_model=ServerActionResponse)
def toggle_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerActionResponse:
    server = _get_server_for_user(server_id, user, db, required_permission="start_stop")

    is_actually_running = False
    if server.state == ServerState.running:
        is_actually_running = _ORCHESTRATOR.is_running(str(server.id))

    if is_actually_running:
        _stop_server_process(server)
    else:
        try:
            from sqlalchemy.exc import IntegrityError
            _do_start_server(server, db)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Port allocation conflict. Please try again in 2 seconds.")

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
    server = _get_server_for_user(server_id, user, db, required_permission="config")
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
    server = _get_server_for_user(server_id, user, db, required_permission="config")

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

    if "max_players" in update:
        capped = clamp_max_players(update.get("max_players"), db=db, server=server)
        if capped is None:
            raise HTTPException(
                status_code=402,
                detail="An active subscription is required to set max players.",
            )
        update["max_players"] = capped

    current.update(update)
    server.mc_config = current

    if server.state == ServerState.running:
        try:
            port = server.vm_port
            props_dict: dict[str, object] = {}
            if server.flavor == ServerFlavor.BEDROCK:
                props_dict = {
                    "server-name": update.get("server_name") or server.world_name,
                    "gamemode": update.get("gamemode"),
                    "difficulty": update.get("difficulty"),
                    "max-players": update.get("max_players"),
                    "allow-cheats": update.get("allow_cheats"),
                    "level-name": update.get("level_name") or server.world_name,
                    "level-seed": update.get("level_seed"),
                }
            else:
                props_dict = {
                    "motd": update.get("motd") or server.world_name,
                    "max-players": update.get("max_players") or clamp_max_players(None, db=db, server=server, default=20),
                    "gamemode": update.get("gamemode") or "survival",
                    "difficulty": update.get("difficulty") or "normal",
                    "level-name": update.get("level_name") or server.world_name,
                    "level-seed": update.get("level_seed") or "",
                }
            # Remove None values
            props_dict = {k: v for k, v in props_dict.items() if v is not None}
            if props_dict:
                _ORCHESTRATOR.update_properties(str(server.id), props_dict)
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Failed to update server properties: {e}"
            )

    db.commit()
    db.refresh(server)
    _invalidate_server_read_cache(user.id, server.id)
    return ServerConfigOut(id=server.id, mc_config=server.mc_config or {})


@router.get("/{server_id}/properties")
def get_server_properties(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | bool | int]:
    server = _get_server_for_user(server_id, user, db, required_permission="config")
    
    # We must try to reach the node. If offline, this will fail.
    try:
        return _ORCHESTRATOR.get_properties(str(server.id))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch properties from server node: {e}")


@router.put("/{server_id}/properties")
def update_server_properties(
    server_id: str,
    props: dict[str, str | bool | int],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str | bool]:
    # Properties that must never be changed from the UI
    BLOCKED_KEYS = {"server-port", "server-portv6"}
    blocked = BLOCKED_KEYS & props.keys()
    if blocked:
        raise HTTPException(status_code=400, detail=f"Cannot modify protected properties: {', '.join(sorted(blocked))}")

    server = _get_server_for_user(server_id, user, db, required_permission="config")

    sanitized = dict(props)
    for key in ("max-players", "max_players"):
        if key in sanitized:
            try:
                requested = int(sanitized[key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"Invalid {key} value")
            capped = clamp_max_players(requested, db=db, server=server)
            if capped is None:
                raise HTTPException(
                    status_code=402,
                    detail="An active subscription is required to set max players.",
                )
            sanitized[key] = capped

    try:
        _ORCHESTRATOR.update_properties(str(server.id), sanitized)
        return {"status": "ok", "restart_required": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to update properties on server node: {e}")


@router.get("/{server_id}/stats", response_model=BedrockServerStats)
def get_server_stats(
    server_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BedrockServerStats:
    server = _get_server_for_user(server_id, user, db)
    return _get_server_stats_snapshot(server, db, background_tasks)



@router.get("/{server_id}/logs", response_model=list[str])
def get_server_logs(
    server_id: str,
    tail: int = 200,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    server = _get_server_for_user(server_id, user, db, required_permission="console")

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
                from blockhost_backend.services.rate_limit import _redis_check, _local_check
                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        if msg.get("type") == "command" and "command" in msg:
                            command = str(msg["command"]).replace("\r", "").replace("\n", " ").strip()
                            if command and len(command) <= 512:
                                allowed = _redis_check(f"cmd_{user.id}", limit=5, window_seconds=1)
                                if allowed is None:
                                    allowed = _local_check(f"cmd_{user.id}", limit=5, window_seconds=1)
                                if not allowed:
                                    continue
                                _ORCHESTRATOR.send_command(server_id, command)
                                from blockhost_backend.database.schema import utcnow
                                server.last_activity = utcnow()
                                db.add(server)
                                db.commit()
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





def _send_cmd(server: Server, cmd: str, db: Session) -> dict:
    try:
        _ORCHESTRATOR.send_command(str(server.id), cmd)
        from blockhost_backend.database.schema import utcnow
        server.last_activity = utcnow()
        db.add(server)
        db.commit()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok"}




@router.get("/{server_id}/blocklist")
def get_blocklist(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_server_for_user(server_id, user, db, required_permission="console")
    # Mocking blocklist for now, since vanilla BDS doesn't track this neatly via command
    return {"players": []}

