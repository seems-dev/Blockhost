from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import CommandRequest
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import User
from blockhost_backend.api.servers import _get_server_for_user, _send_cmd
from blockhost_backend.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/servers", tags=["servers"])

def command_rate_limiter(user: User = Depends(get_current_user)) -> User:
    check_rate_limit(f"cmd_{user.id}", limit=5, window_seconds=1)
    return user


@router.post("/{server_id}/teleport")
def command_teleport(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server, f"tp {req.player} {req.x} {req.y} {req.z}", db)

@router.post("/{server_id}/clear-inventory")
def command_clear_inventory(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server, f"clear {req.player}", db)

@router.post("/{server_id}/ban")
def command_ban(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = f"ban {req.player}"
    if req.message:
        cmd += f" {req.message}"
    return _send_cmd(server, cmd, db)

@router.post("/{server_id}/kick")
def command_kick(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = f"kick {req.player}"
    if req.message:
        cmd += f" {req.message}"
    return _send_cmd(server, cmd, db)

@router.post("/{server_id}/op")
def command_op(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    cmd = "op" if req.grant else "deop"
    return _send_cmd(server, f"{cmd} {req.player}", db)

@router.post("/{server_id}/gamemode")
def command_gamemode(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    if not req.player:
        raise HTTPException(status_code=400, detail="Target player is required")
    return _send_cmd(server, f"gamemode {req.mode} {req.player}", db)

@router.post("/{server_id}/time")
def command_time(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    return _send_cmd(server, f"time set {req.value}", db)

@router.post("/{server_id}/weather")
def command_weather(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    return _send_cmd(server, f"weather {req.weather}", db)

@router.post("/{server_id}/say")
def command_say(server_id: str, req: CommandRequest, user: User = Depends(command_rate_limiter), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    return _send_cmd(server, f"say {req.message}", db)
