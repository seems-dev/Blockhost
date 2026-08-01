"""Production schema fixes

Idempotent: baseline already includes mc_config / google auth columns for
fresh installs; this revision only adds them when upgrading older DBs.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "c9d4e5f6a7b8"
down_revision = "b8c3d4e5f6a7"
branch_labels = None
depends_on = None


def _column_names(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _index_names(inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if "mc_config" not in _column_names(inspector, "servers"):
        op.add_column(
            "servers",
            sa.Column("mc_config", sa.JSON(), server_default="{}", nullable=False),
        )

    user_cols = _column_names(inspector, "users")
    if "google_sub" not in user_cols:
        op.add_column("users", sa.Column("google_sub", sa.String(length=128), nullable=True))
    if "auth_provider" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "auth_provider",
                sa.String(length=32),
                server_default="local",
                nullable=False,
            ),
        )

    inspector = inspect(conn)
    user_indexes = _index_names(inspector, "users")
    if "ix_users_google_sub" not in user_indexes:
        op.create_index("ix_users_google_sub", "users", ["google_sub"])
    if "uq_users_google_sub" not in user_indexes:
        op.create_index(
            "uq_users_google_sub",
            "users",
            ["google_sub"],
            unique=True,
            postgresql_where=sa.text("google_sub IS NOT NULL"),
        )

    if "uq_alive_subscription_per_server" not in _index_names(inspector, "subscriptions"):
        op.create_index(
            "uq_alive_subscription_per_server",
            "subscriptions",
            ["server_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('active', 'grace_period')"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if "uq_alive_subscription_per_server" in _index_names(inspector, "subscriptions"):
        op.drop_index("uq_alive_subscription_per_server", table_name="subscriptions")

    user_indexes = _index_names(inspector, "users")
    if "uq_users_google_sub" in user_indexes:
        op.drop_index("uq_users_google_sub", table_name="users")

    # Columns and ix_users_google_sub may come from baseline; leave them in place.
