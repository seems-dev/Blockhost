from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from blockhost_backend.config.config_manager import get_settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _normalize_password_for_bcrypt(password: str) -> str:
    # bcrypt only uses the first 72 bytes; passlib raises if it's longer.
    # Normalize deterministically so we can accept longer user passwords safely.
    password_bytes = password.encode("utf-8")
    if len(password_bytes) <= 72:
        return password
    return hashlib.sha256(password_bytes).hexdigest()


def hash_password(password: str) -> str:
    return pwd_context.hash(_normalize_password_for_bcrypt(password))


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(_normalize_password_for_bcrypt(password), password_hash)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(*, subject: str, expires_seconds: int) -> str:
    settings = get_settings()
    now = _utcnow()
    payload = {
        "sub": subject,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, subject: str, expires_seconds: int) -> str:
    settings = get_settings()
    now = _utcnow()
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class TokenError(Exception):
    pass


def decode_token(token: str, *, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise TokenError("Invalid token") from e
    if payload.get("type") != expected_type:
        raise TokenError("Invalid token type")
    if not payload.get("sub"):
        raise TokenError("Missing subject")
    return payload
