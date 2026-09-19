"""add_custom_domains

Revision ID: 9f224c5d6e7f
Revises: 8e113b433486
Create Date: 2026-09-18 23:55:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9f224c5d6e7f'
down_revision = '8e113b433486'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'custom_domains',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('deployment_id', sa.Uuid(), nullable=False),
        sa.Column('domain', sa.String(length=253), nullable=False),
        sa.Column('status', sa.Enum('pending_dns', 'active', 'failed', name='domainstatus'), nullable=False),
        sa.Column('verification_token', sa.String(length=64), nullable=False),
        sa.Column('ssl_active', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['deployment_id'], ['app_deployments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_custom_domains_deployment_id'), 'custom_domains', ['deployment_id'], unique=False)
    op.create_index(op.f('ix_custom_domains_domain'), 'custom_domains', ['domain'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_custom_domains_domain'), table_name='custom_domains')
    op.drop_index(op.f('ix_custom_domains_deployment_id'), table_name='custom_domains')
    op.drop_table('custom_domains')
    op.execute("DROP TYPE IF EXISTS domainstatus")
