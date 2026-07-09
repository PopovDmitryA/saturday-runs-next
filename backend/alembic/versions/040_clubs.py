"""Add clubs, club_members and club catalog tables for 5verst clubs pipeline

Revision ID: 040_clubs
Revises: 039_ratings_volunteer
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "040_clubs"
down_revision = "039_ratings_volunteer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clubs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("external_key", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.String(length=1024), nullable=True),
        sa.Column("members_count", sa.Integer(), nullable=True),
        sa.Column("finishes_count", sa.Integer(), nullable=True),
        sa.Column("volunteerings_count", sa.Integer(), nullable=True),
        sa.Column("avg_time_seconds", sa.Integer(), nullable=True),
        sa.Column("best_female_time_seconds", sa.Integer(), nullable=True),
        sa.Column("best_male_time_seconds", sa.Integer(), nullable=True),
        sa.Column("best_time_seconds", sa.Integer(), nullable=True),
        sa.Column("avg_finishes_per_member", sa.Numeric(10, 2), nullable=True),
        sa.Column("avg_volunteerings_per_member", sa.Numeric(10, 2), nullable=True),
        sa.Column("external_links", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("stats_extra", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("list_row_hash", sa.String(length=64), nullable=True),
        sa.Column("list_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("needs_detail_sync", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("detail_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail_source_hash", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.UniqueConstraint("platform_id", "external_key", name="uq_clubs_platform_external_key"),
    )
    op.create_index(
        "ix_clubs_rotation",
        "clubs",
        ["platform_id", "is_active", "needs_detail_sync", "detail_synced_at"],
    )

    op.create_table(
        "club_members",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("participant_id", sa.UUID(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("club_id", "participant_id", name="uq_club_members_club_participant"),
    )
    op.create_index("ix_club_members_participant_id", "club_members", ["participant_id"])

    op.create_table(
        "club_catalog",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_club_catalog_normalized_name", "club_catalog", ["normalized_name"])

    op.create_table(
        "club_catalog_links",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("catalog_id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.UUID(), nullable=False),
        sa.Column("external_key", sa.String(length=256), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=True),
        sa.Column("link_source", sa.String(length=16), server_default="auto_name", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["catalog_id"], ["club_catalog.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("platform_id", "external_key", name="uq_club_catalog_links_platform_external_key"),
    )
    op.create_index("ix_club_catalog_links_catalog_id", "club_catalog_links", ["catalog_id"])


def downgrade() -> None:
    op.drop_table("club_catalog_links")
    op.drop_table("club_catalog")
    op.drop_table("club_members")
    op.drop_table("clubs")
