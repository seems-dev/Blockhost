from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import (
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
    BedrockRuntimeRegistry,
    materialize_server_dir,
    resolve_version_dir,
)
from blockhost_backend.utils import generate_join_code


router = APIRouter(prefix="/api/servers", tags=["servers"])

_RUNTIME = BedrockRuntimeRegistry()


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


def _allocate_port(*, db: Session) -> int:
    settings = get_settings()
    used = set(db.execute(select(Server.vm_port)).scalars().all())
    return pick_free_udp_port(
        port_range=PortRange(start=settings.bedrock_port_range_start, end=settings.bedrock_port_range_end),
        used_ports=used,
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
    db.flush()

    server.join_code = generate_join_code(user.id, server.id)

    versions_dir, servers_dir, _logs_dir = _server_dirs()
    try:
        requested_version = _normalize_requested_version((server.mc_config or {}).get("bedrock_version"))
        _ensure_version_on_disk(versions_dir=versions_dir, requested_version=requested_version)
        version_name, version_dir = resolve_version_dir(versions_dir=versions_dir, requested=requested_version)
        server_dir = materialize_server_dir(version_dir=version_dir, servers_dir=servers_dir, server_id=str(server.id))
        write_server_properties(server_dir / "server.properties", _server_props_from_config(server=server, port=port))

        cfg = dict(server.mc_config or {})
        cfg["runtime_mode"] = "process"
        cfg["template_version"] = version_name
        cfg["server_dir"] = str(server_dir)
        server.mc_config = cfg
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to materialize Bedrock server folder: {e}")

    try:
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
        server.vm_ipv4 = settings.minecraft_public_host
        server.vm_id = str(info.pid)
        server.state = ServerState.running
    except Exception as e:
        server.state = ServerState.suspended
        raise HTTPException(status_code=503, detail=f"Failed to start Bedrock process: {e}")
    db.commit()
    db.refresh(server)
    return ServerOut.model_validate(server, from_attributes=True)


@router.get("", response_model=list[ServerOut])
def list_servers(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ServerOut]:
    servers = db.execute(select(Server).where(Server.owner_id == user.id)).scalars().all()
    return [ServerOut.model_validate(s, from_attributes=True) for s in servers]


@router.get("/{server_id}", response_model=ServerDetail)
def get_server(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ServerDetail:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    base = ServerOut.model_validate(server, from_attributes=True)
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

    if not server.vm_port:
        try:
            server.vm_port = _allocate_port(db=db)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to allocate UDP port: {e}")

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

    try:
        proc = _RUNTIME.ensure_process(
            server_id=str(server.id),
            server_dir=Path(server.mc_config["server_dir"]),
            port=server.vm_port,
            requested_version=str(server.mc_config.get("template_version") or ""),
        )
        proc.start(executable_name=settings.bedrock_executable_name or None)
        info = proc.wait_for_ready()
        server.vm_id = str(info.pid)
        server.vm_ipv4 = settings.minecraft_public_host
        server.state = ServerState.running
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

            proc = _RUNTIME.ensure_process(
                server_id=str(server.id),
                server_dir=Path(server.mc_config["server_dir"]),
                port=server.vm_port,
                requested_version=str(server.mc_config.get("template_version") or ""),
            )
            proc.start(executable_name=settings.bedrock_executable_name or None)
            info = proc.wait_for_ready()
            server.vm_id = str(info.pid)
            server.vm_ipv4 = settings.minecraft_public_host
            server.vm_provider = VMProvider.local_bedrock
            server.state = ServerState.running
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


@router.get("/{server_id}/stats", response_model=BedrockServerStats)
def get_server_stats(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BedrockServerStats:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")

    settings = get_settings()
    host = server.vm_ipv4 or settings.minecraft_public_host
    port = server.vm_port or settings.minecraft_port

    try:
        pong = bedrock_unconnected_ping(host="127.0.0.1", port=port, timeout_seconds=1.0)
        parsed = parse_bedrock_pong_payload(pong.payload)
        return BedrockServerStats(
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
        )
    except Exception:
        return BedrockServerStats(host=host, port=port, reachable=False)


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
