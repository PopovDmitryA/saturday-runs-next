"""Паспорт трассы локации: агрегаты по трекам и версии трассы

Из годных треков локации собирается один профиль: длина, рельеф, извилистость,
кругов. Трассы иногда меняют — тогда заводится новая версия профиля, а прежняя
остаётся историей.

Revision ID: 097_location_course_profiles
Revises: 096_run_track_course_fitness
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "097_location_course_profiles"
down_revision = "096_run_track_course_fitness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_course_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Версия трассы: 1, 2, … Текущая одна, остальные — история.
        sa.Column("course_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tracks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_user_count", sa.Integer(), nullable=False, server_default="0"),
        # Медианы по годным трекам версии.
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("distance_min_m", sa.Float(), nullable=True),
        sa.Column("distance_max_m", sa.Float(), nullable=True),
        sa.Column("elevation_gain_m", sa.Float(), nullable=True),
        sa.Column("elevation_span_m", sa.Float(), nullable=True),
        sa.Column("turn_sum_deg", sa.Float(), nullable=True),
        sa.Column("u_turn_count", sa.Float(), nullable=True),
        sa.Column("longest_straight_m", sa.Float(), nullable=True),
        sa.Column("lap_count", sa.Integer(), nullable=True),
        sa.Column("uphill_share", sa.Float(), nullable=True),
        sa.Column("downhill_share", sa.Float(), nullable=True),
        sa.Column("climb_length_m", sa.Float(), nullable=True),
        sa.Column("climb_rise_m", sa.Float(), nullable=True),
        sa.Column("climb_grade_percent", sa.Float(), nullable=True),
        # Медианный профиль высот версии: [[метры от старта, высота], ...]
        sa.Column("elevation_profile", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Отпечаток геометрии: ячейки сетки 50 м, по ним узнаём смену трассы.
        sa.Column("geometry_cells", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("first_track_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_track_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("location_id", "course_version", name="uq_location_course_version"),
    )
    op.create_index("ix_location_course_current", "location_course_profiles", ["location_id", "is_current"])

    # К какой версии трассы относится трек.
    op.add_column("run_tracks", sa.Column("course_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_tracks", "course_version")
    op.drop_index("ix_location_course_current", table_name="location_course_profiles")
    op.drop_table("location_course_profiles")
