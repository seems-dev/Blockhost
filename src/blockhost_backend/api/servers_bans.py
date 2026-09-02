from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from blockhost_backend.api.deps import get_current_user
from blockhost_backend.api.schemas import BanCheckResponse, BanCreateRequest, BanListOut, BanOut
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import Ban, User
from blockhost_backend.api.servers import _get_server_for_user, _ORCHESTRATOR

router = APIRouter(prefix="/api/servers", tags=["servers"])


def _ban_to_out(ban: Ban) -> BanOut:
    return BanOut(
        id=ban.id,
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


def _resolve_active_ban(server_id: uuid.UUID, xuid: str, db: Session) -> Ban | None:
    ban = db.scalars(
        select(Ban)
        .where(Ban.server_id == server_id, Ban.xuid == xuid, Ban.active == True)
        .limit(1)
    ).one_or_none()
    if not ban:
        return None
    if ban.expires_at and ban.expires_at <= datetime.now(timezone.utc):
        ban.active = False
        ban.unbanned_at = ban.expires_at
        db.add(ban)
        db.commit()
        db.refresh(ban)
        return None
    return ban


def _expire_expired_bans(server_id: uuid.UUID, db: Session) -> None:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Ban)
        .where(Ban.server_id == server_id, Ban.active == True, Ban.expires_at != None, Ban.expires_at <= now)
        .values(active=False, unbanned_at=now)
    )
    if result.rowcount > 0:
        db.commit()


@router.get("/{server_id}/bans", response_model=BanListOut)
def list_bans(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    _expire_expired_bans(server.id, db)
    bans = db.scalars(
        select(Ban)
        .where(Ban.server_id == server.id, Ban.active == True)
        .order_by(Ban.banned_at.desc())
    ).all()
    return BanListOut(items=[_ban_to_out(ban) for ban in bans])


@router.get("/{server_id}/bans/history", response_model=BanListOut)
def list_ban_history(server_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
    _expire_expired_bans(server.id, db)
    bans = db.scalars(
        select(Ban)
        .where(Ban.server_id == server.id)
        .order_by(Ban.banned_at.desc())
    ).all()
    return BanListOut(items=[_ban_to_out(ban) for ban in bans])


@router.get("/{server_id}/bans/check", response_model=BanCheckResponse)
def check_ban(server_id: str, xuid: str = Query(..., min_length=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    server = _get_server_for_user(server_id, user, db, required_permission="console")
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
    server = _get_server_for_user(server_id, user, db, required_permission="console")
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
    server = _get_server_for_user(server_id, user, db, required_permission="console")
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
