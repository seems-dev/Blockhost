"""add_email_verification

Revision ID: 1f2a3b4c5d6e
Revises: 6ee7c4a578e9
Create Date: 2026-08-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "1f2a3b4c5d6e"
down_revision = "6ee7c4a578e9"
branch_labels = None
depends_on = None


def _column_names(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _index_names(inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    user_cols = _column_names(inspector, "users")

    if "email_verified_at" not in user_cols:
        op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        conn.execute(sa.text("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP"))

    if "email_verification_otp_hash" not in user_cols:
        op.add_column("users", sa.Column("email_verification_otp_hash", sa.String(length=64), nullable=True))

    if "email_verification_expires_at" not in user_cols:
        op.add_column("users", sa.Column("email_verification_expires_at", sa.DateTime(timezone=True), nullable=True))

    inspector = inspect(conn)
    if "ix_users_email_verification_otp_hash" not in _index_names(inspector, "users"):
        op.create_index(
            "ix_users_email_verification_otp_hash",
            "users",
            ["email_verification_otp_hash"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if "ix_users_email_verification_otp_hash" in _index_names(inspector, "users"):
        op.drop_index("ix_users_email_verification_otp_hash", table_name="users")

    user_cols = _column_names(inspector, "users")
    if "email_verification_expires_at" in user_cols:
        op.drop_column("users", "email_verification_expires_at")
    if "email_verification_otp_hash" in user_cols:
        op.drop_column("users", "email_verification_otp_hash")
    if "email_verified_at" in user_cols:
        op.drop_column("users", "email_verified_at")
