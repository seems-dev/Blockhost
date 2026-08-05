"""Default node agent port to 9000."""

from alembic import op
import sqlalchemy as sa


revision = "f2a6b8c9d0e1"
down_revision = "ad3e2ebb4147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("nodes", "agent_port", existing_type=sa.Integer(), server_default=sa.text("9000"))
    op.execute("UPDATE nodes SET agent_port = 9000 WHERE agent_port = 8001")


def downgrade() -> None:
    op.execute("UPDATE nodes SET agent_port = 8001 WHERE agent_port = 9000")
    op.alter_column("nodes", "agent_port", existing_type=sa.Integer(), server_default=sa.text("8001"))
