"""Add suspended to DeploymentState enum

Revision ID: 21c9f518d116
Revises: 6a01c3cf3030
Create Date: 2026-09-19 20:22:20.860197
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '21c9f518d116'
down_revision = '6a01c3cf3030'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE deploymentstate ADD VALUE IF NOT EXISTS 'suspended'")

def downgrade():
    pass
