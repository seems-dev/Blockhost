"""Tests for production settings validation and subscription enforcement."""

from __future__ import annotations

import pytest

from blockhost_backend.config.config_manager import Settings
from blockhost_backend.config.production import validate_production_settings


def test_production_mode_rejects_default_secrets() -> None:
    settings = Settings(
        production_mode=True,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="change-me",
        worker_agent_token="change-me-in-dev",
    )
    with pytest.raises(RuntimeError, match="Production configuration invalid"):
        validate_production_settings(settings)


def test_production_mode_rejects_sqlite() -> None:
    settings = Settings(
        production_mode=True,
        database_url="sqlite+pysqlite:///./test.db",
        jwt_secret_key="a" * 32,
        worker_agent_token="b" * 32,
    )
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        validate_production_settings(settings)


def test_dev_mode_allows_defaults() -> None:
    settings = Settings(production_mode=False)
    validate_production_settings(settings)


def test_production_rejects_wildcard_cors() -> None:
    settings = Settings(
        production_mode=True,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="a" * 32,
        worker_agent_token="b" * 32,
        cors_origins="*",
        allow_auto_node_registration=False,
    )
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        validate_production_settings(settings)
