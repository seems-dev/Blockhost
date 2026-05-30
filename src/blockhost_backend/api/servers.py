from __future__ import annotations

import asyncio
import json
import socket
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import (
    BedrockLogEventOut,
    BedrockServerStats,
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
    get_runtime_registry,
    materialize_server_dir,
    resolve_version_dir,
)
from blockhost_backend.utils import generate_join_code, generate_subdomain


router = APIRouter(prefix="/api/servers", tags=["servers"])

_RUNTIME = get_runtime_registry()


def _server_to_out(server: Server, owner: User | None = None) -> ServerOut:
    """Convert a Server object to ServerOut, including owner information."""
    # If owner not provided, use the relationship (assumes it's loaded)
    owner_obj = owner or server.owner
    owner_nickname = owner_obj.nickname if owner_obj else "Unknown"
    
    # Build shareable address. Cloud-hosted servers present a subdomain-based
    # address (e.g. srv-abc.blockhost.com:19132). Local-hosted servers expose
    # an IP + port that other players on the same LAN can use (e.g. 192.168.1.5:19132).
    # For backward compatibility, generate subdomain if missing.
    subdomain = server.subdomain or generate_subdomain(server.id)

    settings = get_settings()
    is_cloud = server.vm_provider != VMProvider.local_bedrock

    if is_cloud:
        base_domain = settings.minecraft_public_domain or "blockhost.com"
        shareable_address = f"{subdomain}.{base_domain}:{server.vm_port}"
    else:
        # Local servers: prefer recorded vm_ipv4, fallback to configured public host
        host = server.vm_ipv4 or settings.minecraft_public_host or "127.0.0.1"
        shareable_address = f"{host}:{server.vm_port}"
    
    return ServerOut(
        id=server.id,
        world_name=server.world_name,
        join_code=server.join_code,
        subdomain=subdomain,
        state=server.state,
        vm_provider=server.vm_provider,
        vm_ipv4=server.vm_ipv4,
        vm_port=server.vm_port,
        owner_id=server.owner_id,
        owner_nickname=owner_nickname,
        created_at=server.created_at,
        last_activity=server.last_activity,
        mc_config=server.mc_config,
        shareable_address=shareable_address,
    )


def _server_dirs() -> tuple[Path, Path, Path]:
    settings = get_settings()
    # Use absolute paths so runtime still works even if the backend is started from a different CWD.
    return (
        Path(settings.bedrock_versions_dir).resolve(),
        Path(settings.bedrock_servers_dir).resolve(),
        Path(settings.bedrock_logs_dir).resolve(),
    )


def _ensure_version_on_disk(*, versions_dir: Path, requested_version: str | None) -> None:
    settings = get_settings()
    if not requested_version or not str(requested_version).strip():
        return
    # Only auto-download explicit versions (not "LATEST"/"PREVIEW").
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
    UI-friendly aliases:
    - null/empty: let resolve_version_dir pick latest installed
    - "recommended": use manifest recommended_version
    """
    if requested_version is None:
        return None
    rv = str(requested_version).strip()
    if not rv:
        return None
    settings = get_settings()
    if rv.lower() in {"recommended", "default"}:
        return recommended_version(manifest_path=Path(settings.bedrock_versions_manifest))
    return rv


def _is_port_available(port: int) -> bool:
    """Check if a UDP port is actually available on the system.
    
    This verifies the port can be bound to, catching the case where:
    - Database says port is free
    - But system has a process listening on it
    """
    try:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', port))
            return True
    except OSError:
        return False


def _allocate_port(*, db: Session) -> int:
    """Allocate a free UDP port for Bedrock server.
    
    FIXED: Checks both database allocation AND system availability.
    This prevents "port in use" errors from other processes.
    """
    settings = get_settings()
    
    # Get database-allocated ports (filter out None)
    used = set(
        p for p in db.execute(select(Server.vm_port)).scalars().all() 
        if p is not None
    )
    
    port_range = PortRange(
        start=settings.bedrock_port_range_start, 
        end=settings.bedrock_port_range_end
    )
    
    # Try each port in the range
    for port in range(port_range.start, port_range.end + 1):
        # Skip ports already in database
        if port in used:
            continue
        
        # Check if port is actually available on the system
        if _is_port_available(port):
            return port
    
    # No available ports found
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


def _start_bedrock_process(*, server: Server, settings) -> None:
    """Start a Bedrock process and update server state.
    
    Extracted common logic to avoid duplication across create/start/toggle endpoints.
    """
    if not server.vm_port:
        raise RuntimeError(f"Server {server.id} has no port allocated")
    
    if not (server.mc_config or {}).get("server_dir"):
        raise RuntimeError(f"Server {server.id} has no server_dir configured")
    
    proc = _RUNTIME.ensure_process(
        server_id=str(server.id),
        server_dir=Path(server.mc_config["server_dir"]),
        port=server.vm_port,
        requested_version=str(server.mc_config.get("template_version") or ""),
    )
    proc.start(executable_name=settings.bedrock_executable_name or None)
    info = proc.wait_for_ready()
    
    if server.mc_config.get("template_version") and info.actual_version:
        requested = str(server.mc_config["template_version"])
        if requested not in {"LATEST", "PREVIEW"} and not str(info.actual_version).startswith(requested):
            proc.stop()
            raise RuntimeError(f"Runtime version mismatch (requested={requested}, actual={info.actual_version})")
    
    server.vm_id = str(info.pid)
    server.vm_ipv4 = settings.minecraft_public_host
    server.state = ServerState.running


@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
def create_server(
    payload: CreateServerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerOut:
    settings = get_settings()
    if payload.tier == SubscriptionTier.free:
        count = db.execute(select(func.count()).select_from(Server).where(Server.owner_id == user.id)).scalar_one()
        if count >= 1:
            raise HTTPException(status_code=403, detail="Free tier limited to 1 server")

    config = payload.config.model_dump(exclude_none=True) if payload.config else {}
    config.pop("bedrock_image", None)  # Docker-only

    # Allocate port FIRST with proper error handling
    try:
        port = _allocate_port(db=db)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

    # Create server with port set immediately
    server = Server(
        owner_id=user.id,
        world_name=payload.world_name,
        join_code="placeholder",
        subdomain="placeholder",  # Will be set after flush
        state=ServerState.created,
        vm_provider=VMProvider.local_bedrock,
        vm_ipv4=None,
        vm_port=port,  # Port is set here
        mc_config=config,
    )
    db.add(server)
    db.flush()  # Generate server.id

    # Now set join code and subdomain after server.id is available
    server.join_code = generate_join_code(user.id, server.id)
    server.subdomain = generate_subdomain(server.id)

    versions_dir, servers_dir, _logs_dir = _server_dirs()
    try:
        requested_version = _normalize_requested_version((server.mc_config or {}).get("bedrock_version"))
        _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
        version_name, version_dir = resolve_version_dir(versions_dir=versions_dir, requested=requested_version)
        server_dir = materialize_server_dir(version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id))
        
        # Write properties with the allocated port
        write_server_properties(
            server_dir / "server.properties", 
            _server_props_from_config(server=server, port=server.vm_port)
        )

        cfg = dict(server.mc_config or {})
        cfg["runtime_mode"] = "process"
        cfg["template_version"] = version_name
        cfg["server_dir"] = str(server_dir)
        server.mc_config = cfg
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Failed to materialize Bedrock server folder: {e}")

    try:
        # Ensure port is set before passing to runtime
        if not server.vm_port:
            raise RuntimeError("Port not allocated before process startup")
        
        _start_bedrock_process(server=server, settings=settings)
    except Exception as e:
        server.state = ServerState.suspended
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")
    
    db.commit()
    db.refresh(server)
    return _server_to_out(server, owner=user)


def _discover_filesystem_servers(*, servers_dir: Path, user: User, db: Session) -> list[Server]:
    """
    Discover server folders on the filesystem that aren't in the database.
    Creates database entries for newly discovered servers.
    """
    discovered = []
    if not servers_dir.exists():
        return discovered

    # Get existing server IDs in the database
    existing_ids = {str(s) for s in db.execute(select(Server.id)).scalars().all()}

    # Scan filesystem for server folders
    for server_dir in servers_dir.iterdir():
        if not server_dir.is_dir():
            continue

        server_id_str = server_dir.name
        try:
            # Validate it's a valid UUID
            server_uuid = uuid.UUID(server_id_str)
        except (ValueError, AttributeError):
            continue

        # Skip if already in database
        if server_id_str in existing_ids:
            continue

        # Extract world name and port from server.properties
        props_file = server_dir / "server.properties"
        world_name = "Unnamed Server"
        port = 19132  # Default port
        
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

        # Create database entry for discovered server. Filesystem-discovered
        # servers are local-hosted, so do NOT set a global public host here.
        new_server = Server(
            id=server_uuid,
            owner_id=user.id,
            world_name=world_name,
            join_code=generate_join_code(user.id, server_uuid),
            state=ServerState.suspended,  # Mark as suspended since it's pre-existing
            vm_provider=VMProvider.local_bedrock,
            vm_ipv4=None,
            vm_port=port,
            mc_config={"server_dir": str(server_dir)},
        )
        db.add(new_server)
        discovered.append(new_server)

    if discovered:
        db.commit()

    return discovered


@router.get("", response_model=list[ServerOut])
def list_servers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ServerOut]:
    # Get servers from database
    servers = db.execute(select(Server).where(Server.owner_id == user.id)).scalars().all()
    
    # Discover and add any servers from the filesystem that aren't in the database
    versions_dir, servers_dir, _logs_dir = _server_dirs()
    discovered = _discover_filesystem_servers(servers_dir=servers_dir, user=user, db=db)
    
    # Combine database servers and newly discovered ones
    all_servers = list(servers) + discovered
    
    return [_server_to_out(s, owner=user) for s in all_servers]


@router.get("/{server_id}", response_model=ServerDetail)
def get_server(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ServerDetail:
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
def start_server(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    settings = get_settings()
    server.vm_provider = VMProvider.local_bedrock

    # Ensure port is allocated
    if not server.vm_port:
        try:
            server.vm_port = _allocate_port(db=db)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

    # Ensure server directory exists
    if not (server.mc_config or {}).get("server_dir"):
        versions_dir, servers_dir, _logs_dir = _server_dirs()
        try:
            requested_version = _normalize_requested_version((server.mc_config or {}).get("bedrock_version"))
            _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
            version_name, version_dir = resolve_version_dir(versions_dir=versions_dir, requested=requested_version)
            server_dir = materialize_server_dir(version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id))
            write_server_properties(
                server_dir / "server.properties", _server_props_from_config(server=server, port=server.vm_port)
            )
            cfg = dict(server.mc_config or {})
            cfg["runtime_mode"] = "process"
            cfg["template_version"] = version_name
            cfg["server_dir"] = str(server_dir)
            server.mc_config = cfg
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to materialize Bedrock server folder: {e}")
    else:
        # For pre-existing servers, update server.properties with the current port
        try:
            server_dir = Path(server.mc_config["server_dir"])
            write_server_properties(
                server_dir / "server.properties", _server_props_from_config(server=server, port=server.vm_port)
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to update server.properties: {e}")

    try:
        _start_bedrock_process(server=server, settings=settings)
    except Exception as e:
        server.state = ServerState.suspended
        raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")
    
    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.post("/{server_id}/stop", response_model=ServerActionResponse)
def stop_server(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        _RUNTIME.stop(server_id=str(server.id))
    except Exception:
        pass
    server.state = ServerState.suspended
    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.post("/{server_id}/toggle", response_model=ServerActionResponse)
def toggle_server(
    server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ServerActionResponse:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    settings = get_settings()
    if server.state == ServerState.running:
        try:
            _RUNTIME.stop(server_id=str(server.id))
        except Exception:
            pass
        server.state = ServerState.suspended
    else:
        try:
            # Allocate port if missing
            if not server.vm_port:
                server.vm_port = _allocate_port(db=db)

            if not (server.mc_config or {}).get("server_dir"):
                versions_dir, servers_dir, _logs_dir = _server_dirs()
                requested_version = _normalize_requested_version((server.mc_config or {}).get("bedrock_version"))
                _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
                version_name, version_dir = resolve_version_dir(versions_dir=versions_dir, requested=requested_version)
                server_dir = materialize_server_dir(
                    version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id)
                )
                cfg = dict(server.mc_config or {})
                cfg["runtime_mode"] = "process"
                cfg["template_version"] = version_name
                cfg["server_dir"] = str(server_dir)
                server.mc_config = cfg

            write_server_properties(
                Path(server.mc_config["server_dir"]) / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )

            _start_bedrock_process(server=server, settings=settings)
        except Exception as e:
            server.state = ServerState.suspended
            raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")
    
    db.commit()
    return ServerActionResponse(id=server.id, state=server.state)


@router.get("/{server_id}/config", response_model=ServerConfigOut)
def get_server_config(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ServerConfigOut:
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
    current.update(update)
    server.mc_config = current

    # Apply config to local runtime by regenerating server.properties.
    if server.state == ServerState.running and (server.mc_config or {}).get("server_dir"):
        try:
            write_server_properties(
                Path(server.mc_config["server_dir"]) / "server.properties",
                _server_props_from_config(server=server, port=server.vm_port),
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to update server.properties: {e}")
    db.commit()
    db.refresh(server)
    return ServerConfigOut(id=server.id, mc_config=server.mc_config or {})


def _get_owned_server(server_id: str, user: User, db: Session) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _collect_server_stats(server: Server) -> BedrockServerStats:
    settings = get_settings()
    host = server.vm_ipv4 or settings.minecraft_public_host
    port = server.vm_port or settings.bedrock_port_range_start

    proc = _RUNTIME.get(str(server.id))
    process_running = proc is not None and proc.is_running()
    live = proc.get_live_stats() if proc else None

    try:
        pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=1.0)
        parsed = parse_bedrock_pong_payload(pong.payload)
        ping_stats = BedrockServerStats(
            host=host,
            port=port,
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
            process_running=process_running,
        )
    except Exception:
        ping_stats = BedrockServerStats(host=host, port=port, reachable=False, process_running=process_running)

    if live is None:
        return ping_stats

    recent = [
        BedrockLogEventOut(
            raw=e.raw,
            level=e.level,
            event_type=e.event_type,
            player_name=e.player_name,
        )
        for e in live.recent_events[-10:]
    ]

    players_online = live.players_online
    if ping_stats.reachable and ping_stats.players_online is not None:
        players_online = max(players_online, ping_stats.players_online)

    return ping_stats.model_copy(
        update={
            "players_online": players_online,
            "online_players": live.online_players,
            "tps": live.tps,
            "tick_ms": live.tick_ms,
            "cpu_usage": live.cpu_usage_percent,
            "ram_usage_mb": live.ram_usage_mb,
            "uptime_seconds": live.uptime_seconds(),
            "log_lines_total": live.log_lines_total,
            "last_log_line": live.last_log_line,
            "last_event": live.last_event,
            "recent_events": recent,
            "process_running": process_running,
            "reachable": ping_stats.reachable or process_running,
        }
    )


@router.get("/{server_id}/stats", response_model=BedrockServerStats)
def get_server_stats(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BedrockServerStats:
    server = _get_owned_server(server_id, user, db)
    return _collect_server_stats(server)


@router.get("/{server_id}/stats/stream")
async def stream_server_stats(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events stream of live stats (stdout + ping), ~2s interval."""
    server = _get_owned_server(server_id, user, db)

    async def event_generator():
        while True:
            stats = _collect_server_stats(server)
            yield f"data: {json.dumps(stats.model_dump(mode='json'))}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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

    proc = _RUNTIME.get(str(server.id))
    if not proc:
        return []
    return proc.read_logs(tail=tail)