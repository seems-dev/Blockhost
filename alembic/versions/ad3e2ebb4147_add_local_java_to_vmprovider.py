"""add local_java to vmprovider

Revision ID: ad3e2ebb4147
Revises: 879603959721
Create Date: 2026-08-02 13:33:19.228391
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ad3e2ebb4147'
down_revision = '879603959721'
branch_labels = None
depends_on = None

def upgrade():
    # In PostgreSQL, adding an enum value inside a transaction is allowed in PG 12+,
    # but to be perfectly safe across configurations, we commit, alter, and begin.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("COMMIT")
        op.execute("ALTER TYPE vmprovider ADD VALUE IF NOT EXISTS 'local_java'")
        op.execute("BEGIN")


def downgrade():
    # PostgreSQL does not support removing values from ENUMs easily.
    pass
