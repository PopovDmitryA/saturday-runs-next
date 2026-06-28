"""Add serial_id to users for human-readable public profile URLs

Revision ID: 031_users_serial_id
Revises: 030_normalize_region_names
Create Date: 2026-06-27
"""

import sqlalchemy as sa
from alembic import op

revision = "031_users_serial_id"
down_revision = "030_normalize_region_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS users_serial_id_seq START 1")
    op.add_column(
        "users",
        sa.Column(
            "serial_id",
            sa.BigInteger(),
            server_default=sa.text("nextval('users_serial_id_seq')"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_serial_id", "users", ["serial_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_serial_id", table_name="users")
    op.drop_column("users", "serial_id")
    op.execute("DROP SEQUENCE IF EXISTS users_serial_id_seq")
