"""Add location_contacts (multiple Telegram links per location) and location_announce_settings

Revision ID: 048_location_contacts
Revises: 047_user_history_disabled_kinds
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "048_location_contacts"
down_revision = "047_user_history_disabled_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_contacts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_url", sa.String(length=512), nullable=False),
        sa.Column("label", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_location_contacts_location_id", "location_contacts", ["location_id"])

    op.create_table(
        "location_announce_settings",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "location_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("do_not_disturb", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("location_id", name="uq_location_announce_settings_location_id"),
    )


def downgrade() -> None:
    op.drop_table("location_announce_settings")
    op.drop_index("ix_location_contacts_location_id", table_name="location_contacts")
    op.drop_table("location_contacts")
