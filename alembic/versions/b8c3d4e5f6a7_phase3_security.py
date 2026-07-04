"""Phase 3 security schema: refresh tokens and node approval."""

from alembic import op
import sqlalchemy as sa


revision = "b8c3d4e5f6a7"
down_revision = "a88e30983665"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.add_column("nodes", sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("nodes", sa.Column("agent_token_hash", sa.String(length=64), nullable=True))
    op.alter_column("nodes", "approved", server_default=None)


def downgrade() -> None:
    op.drop_column("nodes", "agent_token_hash")
    op.drop_column("nodes", "approved")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
