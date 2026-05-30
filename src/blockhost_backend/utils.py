from __future__ import annotations

import secrets
import string
import uuid


_ALPHANUM = string.ascii_lowercase + string.digits


def generate_referrer_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHANUM) for _ in range(length))


def _check_digit(payload: str) -> str:
    # Simple checksum: sum bytes mod 36 (0-9a-z)
    value = sum(payload.encode("utf-8")) % 36
    alphabet = string.digits + string.ascii_uppercase[:26]
    return alphabet[value]


def generate_join_code(user_id: uuid.UUID, server_id: uuid.UUID) -> str:
    user_short = str(user_id).split("-")[0]
    server_short = str(server_id).split("-")[0]
    payload = f"BLOCKHOST-{user_short}-{server_short}"
    return f"{payload}-{_check_digit(payload)}"


def generate_subdomain(server_id: uuid.UUID) -> str:
    """Generate a unique subdomain for a server (e.g., 'srv-abc123def4').
    
    Format: srv-{8 random alphanumeric chars}
    This allows users to access their server at: srv-abc123def4.blockhost.com:port
    """
    random_suffix = "".join(secrets.choice(_ALPHANUM) for _ in range(8))
    return f"srv-{random_suffix}"
