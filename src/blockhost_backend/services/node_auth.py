"""Node agent token generation and verification."""

from __future__ import annotations

import hashlib
import secrets

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.schema import Node


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_agent_token() -> str:
    return secrets.token_urlsafe(32)


def verify_node_agent_token(*, node: Node, token: str) -> bool:
    if not token:
        return False
    if node.agent_token_hash:
        expected = node.agent_token_hash
        return secrets.compare_digest(hash_agent_token(token), expected)
    # Legacy/manual registration path: approved nodes without a per-node token
    # use the shared control-plane worker token.
    settings = get_settings()
    return bool(node.approved) and secrets.compare_digest(token, settings.worker_agent_token)
