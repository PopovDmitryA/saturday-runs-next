"""Runpark location correspondence table and runpark platform."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "017_runpark_location_mappings"
down_revision = "016_location_is_official_map"
branch_labels = None
depends_on = None

RUNPARK_CAPABILITIES = {
    "validate_profile_url": False,
    "fetch_profile_preview": False,
    "fetch_user_profile": False,
    "fetch_user_runs": False,
    "fetch_user_volunteering": False,
    "fetch_locations": False,
    "fetch_events": False,
    "fetch_event_results": False,
    "fetch_event_volunteers": False,
}


def upgrade() -> None:
    op.create_table(
        "runpark_location_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("runpark_location_id", sa.String(length=64), nullable=False),
        sa.Column("runpark_slug", sa.String(length=256), nullable=True),
        sa.Column("runpark_name", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("show_on_map", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_transitional", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=128), nullable=True),
        sa.Column("matched_platform", sa.String(length=32), nullable=True),
        sa.Column("matched_external_key", sa.String(length=256), nullable=True),
        sa.Column("matched_location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("legacy_parkrun_slug", sa.String(length=256), nullable=True),
        sa.Column("runpark_location_row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("public_url", sa.String(length=1024), nullable=True),
        sa.Column("duplicate_match_text", sa.Text(), nullable=True),
        sa.Column("decision_raw", sa.Text(), nullable=True),
        sa.Column("source_batch", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runpark_location_row_id"], ["locations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runpark_location_id", name="uq_runpark_location_mappings_runpark_location_id"),
    )
    op.create_index(
        "ix_runpark_location_mappings_decision",
        "runpark_location_mappings",
        ["decision"],
    )
    op.create_index(
        "ix_runpark_location_mappings_show_on_map",
        "runpark_location_mappings",
        ["show_on_map"],
    )

    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT 1 FROM platforms WHERE code = 'runpark' LIMIT 1")
    ).scalar()
    if not exists:
        platforms = sa.table(
            "platforms",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("base_url", sa.String),
            sa.column("capabilities", postgresql.JSONB),
            sa.column("is_active", sa.Boolean),
        )
        op.bulk_insert(
            platforms,
            [
                {
                    "code": "runpark",
                    "name": "Runpark",
                    "base_url": "https://runpark.ru",
                    "capabilities": RUNPARK_CAPABILITIES,
                    "is_active": False,
                }
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_runpark_location_mappings_show_on_map", table_name="runpark_location_mappings")
    op.drop_index("ix_runpark_location_mappings_decision", table_name="runpark_location_mappings")
    op.drop_table("runpark_location_mappings")
    op.execute(sa.text("DELETE FROM platforms WHERE code = 'runpark'"))
