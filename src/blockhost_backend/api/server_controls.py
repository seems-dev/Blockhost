from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import (
    BlocklistOut,
    GamemodeRequest,
    OpRequest,
    PlayerActionRequest,
    SayRequest,
    ServerCommandResponse,
    TeleportRequest,
    TimeRequest,
    WeatherRequest,
)
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Server, ServerState, User
from blockhost_backend.runtime.bedrock_process import BedrockProcess, get_runtime_registry

router = APIRouter(prefix="/api/servers", tags=["server-controls"])

_RUNTIME = get_runtime_registry()

_PLAYER_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def _get_owned_server(server_id: str, user: User, db: Session) -> Server:
    try:
        server_uuid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Server not found")
    server = db.get(Server, server_uuid)
    if not server or server.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _sanitize_player(name: str) -> str:
    player = name.strip()
    if not _PLAYER_NAME_RE.match(player):
        raise HTTPException(status_code=400, detail="Invalid player name")
    return player


def _require_running_process(server: Server) -> BedrockProcess:
    if server.state != ServerState.running:
        raise HTTPException(status_code=503, detail="Server is not running")
    proc = _RUNTIME.get(str(server.id))
    if proc is None or not proc.is_running():
        raise HTTPException(status_code=503, detail="Server process is not running")
    return proc


def _run_command(server: Server, command: str) -> ServerCommandResponse:
    proc = _require_running_process(server)
    try:
        proc.send_command(command)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return ServerCommandResponse(ok=True, command=command)


def _get_blocklist(server: Server) -> list[str]:
    raw = (server.mc_config or {}).get("blocklist") or []
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str)]


def _add_blocklist(db: Session, server: Server, player: str) -> None:
    cfg = dict(server.mc_config or {})
    blocklist = _get_blocklist(server)
    if player not in blocklist:
        blocklist.append(player)
    cfg["blocklist"] = blocklist
    server.mc_config = cfg
    db.commit()


@router.post("/{server_id}/teleport", response_model=ServerCommandResponse)
def teleport_player(
    server_id: str,
    payload: TeleportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    return _run_command(server, f"tp {player} {payload.x} {payload.y} {payload.z}")


@router.post("/{server_id}/clear-inventory", response_model=ServerCommandResponse)
def clear_inventory(
    server_id: str,
    payload: PlayerActionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    return _run_command(server, f"clear {player}")


@router.post("/{server_id}/ban", response_model=ServerCommandResponse)
def ban_player(
    server_id: str,
    payload: PlayerActionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    result = _run_command(server, f"kick {player} Banned by host")
    _add_blocklist(db, server, player)
    return result


@router.post("/{server_id}/kick", response_model=ServerCommandResponse)
def kick_player(
    server_id: str,
    payload: PlayerActionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    return _run_command(server, f"kick {player} Kicked by host")


@router.post("/{server_id}/op", response_model=ServerCommandResponse)
def op_player(
    server_id: str,
    payload: OpRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    cmd = "op" if payload.grant else "deop"
    return _run_command(server, f"{cmd} {player}")


@router.post("/{server_id}/gamemode", response_model=ServerCommandResponse)
def set_gamemode(
    server_id: str,
    payload: GamemodeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    player = _sanitize_player(payload.player)
    return _run_command(server, f"gamemode {payload.mode} {player}")


@router.post("/{server_id}/time", response_model=ServerCommandResponse)
def set_time(
    server_id: str,
    payload: TimeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    value = payload.value.strip()
    if not value.replace(".", "", 1).isdigit() and value not in {"day", "noon", "sunset", "night", "midnight", "sunrise"}:
        raise HTTPException(status_code=400, detail="Invalid time value")
    return _run_command(server, f"time set {value}")


@router.post("/{server_id}/weather", response_model=ServerCommandResponse)
def set_weather(
    server_id: str,
    payload: WeatherRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    return _run_command(server, f"weather {payload.weather}")


@router.post("/{server_id}/say", response_model=ServerCommandResponse)
def say_message(
    server_id: str,
    payload: SayRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ServerCommandResponse:
    server = _get_owned_server(server_id, user, db)
    message = payload.message.strip().replace("\n", " ").replace("\r", " ")
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    return _run_command(server, f"say {message}")


@router.get("/{server_id}/blocklist", response_model=BlocklistOut)
def get_blocklist(
    server_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BlocklistOut:
    server = _get_owned_server(server_id, user, db)
    return BlocklistOut(players=_get_blocklist(server))
