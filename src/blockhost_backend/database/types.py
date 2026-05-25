from __future__ import annotations

import uuid

from sqlalchemy import CHAR, JSON, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """
    Platform-independent UUID type.

    - Postgres: uses native UUID
    - Others (SQLite): stores as 36-char string
    """

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID

            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


JSONType = JSON

