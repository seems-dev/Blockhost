"""add node tier

Revision ID: 2203070946a4
Revises: b10c569de78d
Create Date: 2026-09-22 12:57:13.984786
"""

from alembic import op
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2203070946a4'
down_revision = 'b10c569de78d'
branch_labels = None
depends_on = None

def upgrade():
    # We use postgresql.ENUM to explicitly create the type if needed, 
    # but sa.Enum with create_type=False is safer if the enum is managed elsewhere,
    # however since we are adding it, we should ensure the type is created.
    node_tier_enum = postgresql.ENUM('shared', 'dedicated', name='nodetier', create_type=False)
    node_tier_enum.create(op.get_bind(), checkfirst=True)
    
    op.add_column('nodes', sa.Column('tier', sa.Enum('shared', 'dedicated', name='nodetier'), server_default='shared', nullable=False))

def downgrade():
    op.drop_column('nodes', 'tier')
    node_tier_enum = postgresql.ENUM('shared', 'dedicated', name='nodetier', create_type=False)
    node_tier_enum.drop(op.get_bind(), checkfirst=True)
