"""Refresh token persistence and revocation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.core.security import create_refresh_token, decode_token, TokenError
from blockhost_backend.database.schema import RefreshToken, User, utcnow


def store_refresh_token(*, db: Session, user_id: uuid.UUID, jti: str, expires_at) -> None:
    db.add(
        RefreshToken(
            user_id=user_id,
            jti=jti,
            expires_at=expires_at,
        )
    )


def issue_refresh_token(*, db: Session, user: User) -> str:
    settings = get_settings()
    token = create_refresh_token(
        subject=str(user.id),
        expires_seconds=settings.jwt_refresh_token_expire_seconds,
    )
    decoded = decode_token(token, expected_type="refresh")
    from datetime import datetime, timezone

    expires_at = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    store_refresh_token(db=db, user_id=user.id, jti=str(decoded["jti"]), expires_at=expires_at)
    return token


def validate_refresh_token(*, db: Session, token: str) -> User:
    try:
        decoded = decode_token(token, expected_type="refresh")
    except TokenError as exc:
        raise TokenError("Invalid refresh token") from exc

    jti = str(decoded["jti"])
    row = db.execute(
        select(RefreshToken).where(RefreshToken.jti == jti)
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None:
        raise TokenError("Refresh token revoked or unknown")
    if row.expires_at <= utcnow():
        raise TokenError("Refresh token expired")

    user = db.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        raise TokenError("User not found")
    return user


def revoke_refresh_token(*, db: Session, token: str) -> None:
    try:
        decoded = decode_token(token, expected_type="refresh")
    except TokenError:
        return
    row = db.execute(
        select(RefreshToken).where(RefreshToken.jti == str(decoded["jti"]))
    ).scalar_one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.add(row)


def revoke_all_refresh_tokens(*, db: Session, user_id: uuid.UUID) -> int:
    rows = db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).scalars().all()
    now = utcnow()
    for row in rows:
        row.revoked_at = now
        db.add(row)
    return len(rows)
