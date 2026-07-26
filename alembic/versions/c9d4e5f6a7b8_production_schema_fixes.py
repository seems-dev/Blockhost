"""Production schema fixes"""

from alembic import op
import sqlalchemy as sa

revision = "c9d4e5f6a7b8"
down_revision = "b8c3d4e5f6a7"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("servers", sa.Column("mc_config", sa.JSON(), server_default='{}', nullable=False))
    
    op.add_column("users", sa.Column("google_sub", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("auth_provider", sa.String(length=32), server_default='local', nullable=False))
    
    op.create_index("ix_users_google_sub", "users", ["google_sub"])
    op.create_index("uq_users_google_sub", "users", ["google_sub"], unique=True, postgresql_where=sa.text("google_sub IS NOT NULL"))
    
    op.create_index("uq_alive_subscription_per_server", "subscriptions", ["server_id"], unique=True, postgresql_where=sa.text("status IN ('active', 'grace_period')"))

def downgrade() -> None:
    op.drop_index("uq_alive_subscription_per_server", table_name="subscriptions")
    op.drop_index("uq_users_google_sub", table_name="users")
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "google_sub")
    op.drop_column("servers", "mc_config")
