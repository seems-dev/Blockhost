from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from blockhost_backend.api.deps import auth_rate_limit, get_current_user
from blockhost_backend.api.schemas import AuthResponse, LoginRequest, RefreshRequest, SignupRequest, UserOut
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.core.security import (
    TokenError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from blockhost_backend.database.db import get_db
from blockhost_backend.database.schema import BlockcoinReason, BlockcoinTransaction, User
from blockhost_backend.services.auth_tokens import (
    issue_refresh_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    validate_refresh_token,
)
from blockhost_backend.utils import generate_referrer_code


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _unique_referrer_code(db: Session) -> str:
    for _ in range(20):
        code = generate_referrer_code()
        exists = db.execute(select(User).where(User.referrer_code == code)).scalar_one_or_none()
        if not exists:
            return code
    raise RuntimeError("Failed to generate unique referrer code")


class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
    _: None = Depends(auth_rate_limit()),
) -> AuthResponse:
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

    settings = get_settings()
    access_token = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    refresh_token = issue_refresh_token(db=db, user=user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
        ),
    )


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    _: None = Depends(auth_rate_limit()),
) -> AuthResponse:
    user = db.execute(select(User).where(User.email == str(payload.email).lower())).scalar_one_or_none()
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user.auth_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    settings = get_settings()
    access_token = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    refresh_token = issue_refresh_token(db=db, user=user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
        ),
    )


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        user = validate_refresh_token(db=db, token=payload.refresh_token)
    except TokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    new_access = create_access_token(subject=str(user.id), expires_seconds=settings.jwt_access_token_expire_seconds)
    return {"access_token": new_access}


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> dict:
    revoke_refresh_token(db=db, token=payload.refresh_token)
    db.commit()
    return {"status": "ok"}


@router.post("/logout-all")
def logout_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    count = revoke_all_refresh_tokens(db=db, user_id=user.id)
    db.commit()
    return {"status": "ok", "revoked": count}


class GoogleLoginRequest(BaseModel):
    id_token: str
    referrer_code: str | None = None


@router.post("/google", response_model=AuthResponse)
def google_login(
    payload: GoogleLoginRequest,
    db: Session = Depends(get_db),
    _: None = Depends(auth_rate_limit()),
) -> AuthResponse:
    settings = get_settings()

    try:
        token_info = id_token.verify_oauth2_token(
            payload.id_token,
            google_requests.Request(),
            audience=settings.google_client_id,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    if "sub" not in token_info or "email" not in token_info:
        raise HTTPException(status_code=401, detail="Incomplete Google token payload")

    if not token_info.get("email_verified", False):
        raise HTTPException(status_code=401, detail="Google email not verified")

    email: str = str(token_info["email"]).lower()
    google_sub: str = token_info["sub"]
    nickname: str = token_info.get("name") or email.split("@")[0]

    user = db.execute(
        select(User).where(User.google_sub == google_sub)
    ).scalar_one_or_none()

    if user is None:
        user = db.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if user is not None:
            user.google_sub = google_sub
            user.auth_provider = "google"
            db.commit()
            db.refresh(user)
        else:
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
                auth_hash="",
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

    if user.deleted_at is not None:
        raise HTTPException(status_code=403, detail="Account suspended")

    access_token = create_access_token(
        subject=str(user.id),
        expires_seconds=settings.jwt_access_token_expire_seconds,
    )
    refresh_token = issue_refresh_token(db=db, user=user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=user.blockcoin_balance,
        ),
    )
