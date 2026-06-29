"""Add sync_watermarks table for incremental sync progress markers

Revision ID: 032_sync_watermarks
Revises: 031_users_serial_id
Create Date: 2026-06-29
"""

import sqlalchemy as sa
from alembic import op

revision = "032_sync_watermarks"
down_revision = "031_users_serial_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_watermarks",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("platform_id", "key", name="uq_sync_watermarks_platform_key"),
    )


def downgrade() -> None:
    op.drop_table("sync_watermarks")
