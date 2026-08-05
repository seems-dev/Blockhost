"""Phase 3 security tests."""

from __future__ import annotations

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
        cors_origins="https://app.example.com",
        allow_auto_node_registration=True,
    )
    with pytest.raises(RuntimeError, match="ALLOW_AUTO_NODE_REGISTRATION"):
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
    )
    monkeypatch.setattr("blockhost_backend.services.node_auth.get_settings", lambda: settings)

    node = Node(name="worker-1", ip_address="10.0.0.1", approved=False, agent_token_hash=None)

    assert verify_node_agent_token(node=node, token=token) is False
