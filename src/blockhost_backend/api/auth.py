from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

import requests

from blockhost_backend.api.schemas import AuthResponse, GoogleLoginRequest, LoginRequest, RefreshRequest, SignupRequest, UserOut
from blockhost_backend.config.config_manager import Settings, get_settings
from blockhost_backend.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import BlockcoinReason, BlockcoinTransaction, User
from blockhost_backend.utils import generate_referrer_code


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _unique_referrer_code(db: Session) -> str:
    for _ in range(20):
        code = generate_referrer_code()
        exists = db.execute(select(User).where(User.referrer_code == code)).scalar_one_or_none()
        if not exists:
            return code
    raise RuntimeError("Failed to generate unique referrer code")


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> AuthResponse:
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    referred_by_id: uuid.UUID | None = None
    if payload.referrer_code:
        referrer = db.execute(select(User).where(User.referrer_code == payload.referrer_code)).scalar_one_or_none()
        if referrer:
            referred_by_id = referrer.id

    user = User(
        email=str(payload.email).lower(),
        phone=payload.phone,
        nickname=payload.nickname,
        auth_hash=hash_password(payload.password),
        referrer_code=_unique_referrer_code(db),
        referred_by_id=referred_by_id,
        blockcoin_balance=0,
    )
    db.add(user)
    db.flush()

    # Minimal referral bonus as described in agent.md
    if referred_by_id is not None:
        db.add(
            BlockcoinTransaction(
                user_id=user.id,
                amount=100,
                reason=BlockcoinReason.referral,
                meta={"referred_by": str(referred_by_id)},
            )
        )
        user.blockcoin_balance += 100

    db.commit()
    db.refresh(user)

    settings = get_settings()
    access_token = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    refresh_token = create_refresh_token(subject=str(user.id), expires_seconds=settings.jwt_refresh_token_expire_seconds)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
            subscription_tier=user.subscription_tier,
        ),
    )


def _verify_google_id_token(id_token: str, settings: Settings) -> dict:
    resp = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"id_token": id_token},
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Google ID token")
    payload = resp.json()
    if payload.get("email_verified") not in ("true", True):
        raise HTTPException(status_code=401, detail="Google email is not verified")
    if settings.google_client_id:
        audience = payload.get("aud")
        if audience != settings.google_client_id:
            raise HTTPException(status_code=401, detail="Google ID token audience mismatch")
    return payload


@router.post("/google", response_model=AuthResponse)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    settings = get_settings()
    info = _verify_google_id_token(payload.id_token, settings)
    email = str(info.get("email", "")).lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google token did not contain email")

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        user = User(
            email=email,
            phone=None,
            nickname=info.get("name") or email.split("@")[0],
            auth_hash=hash_password(uuid.uuid4().hex),
            referrer_code=_unique_referrer_code(db),
            blockcoin_balance=0,
        )
        db.add(user)
        db.flush()
        db.commit()
        db.refresh(user)

    access_token = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    refresh_token = create_refresh_token(subject=str(user.id), expires_seconds=settings.jwt_refresh_token_expire_seconds)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
            subscription_tier=user.subscription_tier,
        ),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.execute(select(User).where(User.email == str(payload.email).lower())).scalar_one_or_none()
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user.auth_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    settings = get_settings()
    access_token = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    refresh_token = create_refresh_token(subject=str(user.id), expires_seconds=settings.jwt_refresh_token_expire_seconds)
    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
            subscription_tier=user.subscription_tier,
        ),
    )


@router.post("/refresh")
def refresh(payload: RefreshRequest) -> dict:
    settings = get_settings()
    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    new_access = create_access_token(subject=decoded["sub"], expires_seconds=settings.jwt_access_token_expire_seconds)
    return {"access_token": new_access}
