"""Phase 3 security tests."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest

from blockhost_backend.config.config_manager import Settings
from blockhost_backend.config.production import validate_production_settings
from blockhost_backend.services.node_auth import generate_agent_token, hash_agent_token, verify_node_agent_token
from blockhost_backend.database.schema import Node


def test_production_rejects_auto_node_registration() -> None:
    settings = Settings(
        production_mode=True,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="a" * 32,
        worker_agent_token="b" * 32,
        smtp_host="smtp.example.com",
        smtp_from_email="noreply@example.com",
        cors_origins="https://app.example.com",
        allow_auto_node_registration=True,
    )
    with pytest.raises(RuntimeError, match="ALLOW_AUTO_NODE_REGISTRATION"):
        validate_production_settings(settings)


def test_production_requires_smtp_for_email_verification() -> None:
    settings = Settings(
        production_mode=True,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="a" * 32,
        worker_agent_token="b" * 32,
        cors_origins="https://app.example.com",
        allow_auto_node_registration=False,
        minecraft_public_host="203.0.113.10",
        smtp_host="",
        smtp_user="",
        smtp_from_email="",
    )
    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        validate_production_settings(settings)


def test_per_node_token_verification() -> None:
    token = generate_agent_token()
    node = Node(name="worker-1", ip_address="10.0.0.1", agent_token_hash=hash_agent_token(token))
    assert verify_node_agent_token(node=node, token=token) is True
    assert verify_node_agent_token(node=node, token="wrong") is False


def test_approved_node_without_hash_accepts_shared_worker_token(monkeypatch) -> None:
    token = generate_agent_token()
    settings = Settings(
        production_mode=True,
        worker_agent_token=token,
        smtp_host="smtp.example.com",
        smtp_from_email="noreply@example.com",
    )
    monkeypatch.setattr("blockhost_backend.services.node_auth.get_settings", lambda: settings)

    node = Node(name="worker-1", ip_address="10.0.0.1", approved=True, agent_token_hash=None)

    assert verify_node_agent_token(node=node, token=token) is True
    assert verify_node_agent_token(node=node, token="wrong") is False


def test_unapproved_node_without_hash_rejects_shared_worker_token(monkeypatch) -> None:
    token = generate_agent_token()
    settings = Settings(
        production_mode=True,
        worker_agent_token=token,
        smtp_host="smtp.example.com",
        smtp_from_email="noreply@example.com",
    )
    monkeypatch.setattr("blockhost_backend.services.node_auth.get_settings", lambda: settings)

    node = Node(name="worker-1", ip_address="10.0.0.1", approved=False, agent_token_hash=None)

    assert verify_node_agent_token(node=node, token=token) is False


def test_local_password_login_requires_verified_email(monkeypatch) -> None:
    from blockhost_backend.api.auth import login
    from blockhost_backend.api.schemas import LoginRequest
    from blockhost_backend.database.schema import User

    user = User(
        email="user@example.com",
        nickname="User",
        auth_hash="hash",
        referrer_code="ABC123",
        auth_provider="local",
        email_verified_at=None,
    )
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = user
    monkeypatch.setattr("blockhost_backend.api.auth.verify_password", lambda password, password_hash: True)

    with pytest.raises(Exception) as exc:
        login(LoginRequest(email="user@example.com", password="password123"), db=db, _=None)

    assert getattr(exc.value, "status_code", None) == 403


def test_email_verification_otp_is_hashed_with_email() -> None:
    from blockhost_backend.api.auth import _verification_otp_hash

    otp = "123456"
    otp_hash = _verification_otp_hash(email="USER@example.com", otp=otp)

    assert otp_hash != otp
    assert otp_hash == _verification_otp_hash(email="user@example.com", otp=otp)
    assert len(otp_hash) == 64


def test_verify_email_accepts_correct_otp_and_clears_it(monkeypatch) -> None:
    from blockhost_backend.api.auth import _verification_otp_hash, verify_email
    from blockhost_backend.api.schemas import AuthResponse, UserOut, VerifyEmailRequest
    from blockhost_backend.database.schema import User, utcnow

    email = "user@example.com"
    otp = "123456"
    user = User(
        id=uuid.uuid4(),
        email=email,
        nickname="User",
        auth_hash="hash",
        referrer_code="ABC123",
        auth_provider="local",
        email_verified_at=None,
        email_verification_otp_hash=_verification_otp_hash(email=email, otp=otp),
        email_verification_expires_at=utcnow() + timedelta(minutes=10),
    )
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = user
    response = AuthResponse(
        access_token="access",
        refresh_token="refresh",
        user=UserOut(
            id=user.id,
            email=user.email,
            nickname=user.nickname,
            referrer_code=user.referrer_code,
            blockcoin_balance=0,
            email_verified=True,
        ),
    )
    monkeypatch.setattr("blockhost_backend.api.auth._issue_auth_response", lambda db, user: response)

    result = verify_email(VerifyEmailRequest(email=email, otp=otp), db=db, _=None)

    assert result is response
    assert user.email_verified_at is not None
    assert user.email_verification_otp_hash is None
    assert user.email_verification_expires_at is None
    db.commit.assert_called_once()
