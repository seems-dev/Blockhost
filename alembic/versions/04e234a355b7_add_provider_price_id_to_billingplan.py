"""add provider_price_id to BillingPlan

Revision ID: 04e234a355b7
Revises: 1868fb794670
Create Date: 2026-09-12 06:07:32.601136
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '04e234a355b7'
down_revision = '1868fb794670'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('plans', sa.Column('provider_price_id', sa.String(length=64), nullable=True))

def downgrade():
    op.drop_column('plans', 'provider_price_id')
