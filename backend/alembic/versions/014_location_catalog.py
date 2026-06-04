"""Location catalog for cross-platform display names."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014_location_catalog"
down_revision = "013_run_result_pace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("canonical_name", sa.String(length=256), nullable=False),
        sa.Column("legacy_parkrun_slug", sa.String(length=256), nullable=True),
        sa.Column("active_platform", sa.String(length=32), nullable=True),
        sa.Column("is_closed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("legacy_parkrun_slug", name="uq_location_catalog_legacy_parkrun_slug"),
    )
    op.create_index("ix_location_catalog_canonical_name", "location_catalog", ["canonical_name"])

    op.create_table(
        "location_catalog_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_key", sa.String(length=256), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["catalog_id"], ["location_catalog.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_id", "external_key", name="uq_location_catalog_links_platform_external_key"),
    )
    op.create_index("ix_location_catalog_links_catalog_id", "location_catalog_links", ["catalog_id"])


def downgrade() -> None:
    op.drop_index("ix_location_catalog_links_catalog_id", table_name="location_catalog_links")
    op.drop_table("location_catalog_links")
    op.drop_index("ix_location_catalog_canonical_name", table_name="location_catalog")
    op.drop_table("location_catalog")
