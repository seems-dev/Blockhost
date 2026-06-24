from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from blockhost_backend.api.schemas import AuthResponse, LoginRequest, RefreshRequest, SignupRequest, UserOut
from blockhost_backend.config.config_manager import get_settings
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
from blockhost_backend.api.google_auth import router as google_router


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




class GoogleLoginRequest(BaseModel):
    id_token: str
    referrer_code: str | None = None


def _unique_referrer_code(db: Session) -> str:
    from blockhost_backend.utils import generate_referrer_code
    for _ in range(20):
        code = generate_referrer_code()
        exists = db.execute(select(User).where(User.referrer_code == code)).scalar_one_or_none()
        if not exists:
            return code
    raise RuntimeError("Failed to generate unique referrer code")


@router.post("/google", response_model=AuthResponse)
def google_login(
    payload: GoogleLoginRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    settings = get_settings()

    # Verify the ID token with Google
    try:
        token_info = id_token.verify_oauth2_token(
            payload.id_token,
            google_requests.Request(),
            audience=settings.google_client_id,  # validates aud claim
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    # Validate token has required fields
    if "sub" not in token_info or "email" not in token_info:
        raise HTTPException(status_code=401, detail="Incomplete Google token payload")

    email: str = str(token_info["email"]).lower()
    google_sub: str = token_info["sub"]
    nickname: str = token_info.get("name") or email.split("@")[0]

    is_new_user = False

    # 1. Try to find by stable google_sub first
    user = db.execute(
        select(User).where(User.google_sub == google_sub)
    ).scalar_one_or_none()

    if user is None:
        # 2. Fall back to email — existing email/password account
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if user is not None:
            # Link Google to existing account
            user.google_sub = google_sub
            user.auth_provider = "google"
            db.commit()
            db.refresh(user)
        else:
            # 3. Brand new user — create account
            is_new_user = True

            referred_by_id: uuid.UUID | None = None
            if payload.referrer_code:
                referrer = db.execute(
                    select(User).where(User.referrer_code == payload.referrer_code)
                ).scalar_one_or_none()
                if referrer:
                    referred_by_id = referrer.id

            user = User(
                email=email,
                nickname=nickname,
                auth_hash="",  # no password for Google-only accounts
                google_sub=google_sub,
                auth_provider="google",
                referrer_code=_unique_referrer_code(db),
                referred_by_id=referred_by_id,
                blockcoin_balance=0,
            )
            db.add(user)
            db.flush()

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

    # Soft-deleted account check
    if user.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Account suspended")

    access_token = create_access_token(
        subject=str(user.id),
        expires_seconds=settings.jwt_access_token_expire_seconds,
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        expires_seconds=settings.jwt_refresh_token_expire_seconds,
    )

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