"""add_java_server_support

Revision ID: bf438b525946
Revises: c9d4e5f6a7b8
Create Date: 2026-07-05 12:02:10.810671
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bf438b525946"
down_revision = "c9d4e5f6a7b8"
branch_labels = None
depends_on = None

serverflavor = sa.Enum(
    "BEDROCK",
    "JAVA_VANILLA",
    "PAPER",
    "PURPUR",
    "FABRIC",
    "FORGE",
    "NEOFORGE",
    name="serverflavor",
)


def upgrade():
    # Drop server default without recreating the table (Postgres FK-safe).
    op.alter_column("nodes", "approved", server_default=None)

    serverflavor.create(op.get_bind(), checkfirst=True)
    op.add_column("servers", sa.Column("flavor", serverflavor, nullable=True))
    op.add_column("servers", sa.Column("mc_version", sa.String(length=32), nullable=True))
    op.add_column("servers", sa.Column("java_version", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE servers SET flavor = 'BEDROCK' WHERE flavor IS NULL"))
    op.alter_column(
        "servers",
        "flavor",
        nullable=False,
        server_default="BEDROCK",
        existing_type=serverflavor,
    )
    op.create_unique_constraint("uq_servers_node_port", "servers", ["node_id", "vm_port"])

    bind = op.get_bind()
    existing = {idx["name"] for idx in sa.inspect(bind).get_indexes("subscriptions")}
    if "uq_alive_subscription_per_server" in existing:
        op.drop_index("uq_alive_subscription_per_server", table_name="subscriptions")


def downgrade():
    op.create_index(
        "uq_alive_subscription_per_server",
        "subscriptions",
        ["server_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'grace_period')"),
    )
    op.drop_constraint("uq_servers_node_port", "servers", type_="unique")
    op.drop_column("servers", "java_version")
    op.drop_column("servers", "mc_version")
    op.drop_column("servers", "flavor")
    serverflavor.drop(op.get_bind(), checkfirst=True)
    op.alter_column("nodes", "approved", server_default=sa.text("false"))
