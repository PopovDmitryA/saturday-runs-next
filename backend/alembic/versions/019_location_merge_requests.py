"""Location merge requests for suspected 5verst slug renames."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019_location_merge_requests"
down_revision = "018_runpark_mapping_is_paused"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_merge_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("candidate_slug", sa.String(length=256), nullable=False),
        sa.Column("candidate_name", sa.String(length=256), nullable=False),
        sa.Column("candidate_source_url", sa.String(length=1024), nullable=True),
        sa.Column("matched_location_id", sa.UUID(), nullable=False),
        sa.Column("matched_slug", sa.String(length=256), nullable=False),
        sa.Column("overlap_count", sa.Integer(), nullable=False),
        sa.Column("overlap_event_dates", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status",
            sa.Enum("pending", "confirmed", "rejected", name="location_merge_request_status_enum"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_id",
            "candidate_slug",
            "matched_location_id",
            name="uq_location_merge_requests_candidate_match",
        ),
    )
    op.create_index(
        "ix_location_merge_requests_platform_status",
        "location_merge_requests",
        ["platform_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_location_merge_requests_platform_status", table_name="location_merge_requests")
    op.drop_table("location_merge_requests")
    op.execute("DROP TYPE IF EXISTS location_merge_request_status_enum")
