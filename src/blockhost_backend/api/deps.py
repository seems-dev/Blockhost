from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.core.security import TokenError, decode_token
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import User
from blockhost_backend.services.rate_limit import check_rate_limit


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    token: str | None = None,
    db: Session = Depends(get_db),
) -> User:
    raw_token = creds.credentials if creds else token
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(raw_token, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid access token")
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def authenticate_ws_user(*, token: str | None, db: Session) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        return None
    return user


def get_ws_user(
    token: str | None = None,
    db: Session = Depends(get_db),
) -> User:
    user = authenticate_ws_user(token=token, db=db)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def auth_rate_limit() -> Callable:
    async def _dep(request: Request) -> None:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        check_rate_limit(
            f"rl:auth:{client_ip}",
            limit=settings.rate_limit_auth_per_minute,
            window_seconds=60,
        )

    return _dep


def upload_rate_limit() -> Callable:
    async def _dep(request: Request) -> None:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"
        check_rate_limit(
            f"rl:upload:{client_ip}",
            limit=settings.rate_limit_upload_per_minute,
            window_seconds=60,
        )

    return _dep
