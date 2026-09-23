"""Годность трека для паспорта трассы и расстояние старта от локации

Класс записи говорит о качестве GPS, а этот флаг — можно ли верить треку как
измерению трассы. Забраковываются пробежки в другой местности, аномальные
длина, скорость и набор высоты, а также случаи, где время протокола правили
руками (Щёлково 16.05.2026: запись класса A, расхождение 83 секунды).

Revision ID: 096_run_track_course_fitness
Revises: 095_admin_track_imports
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "096_run_track_course_fitness"
down_revision = "095_admin_track_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_tracks",
        sa.Column("is_course_eligible", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("run_tracks", sa.Column("exclusion_reason", sa.String(length=48), nullable=True))
    op.add_column("run_tracks", sa.Column("exclusion_note", sa.Text(), nullable=True))
    # Насколько далеко от локации начался трек: и признак брака, и диагностика.
    op.add_column("run_tracks", sa.Column("start_distance_m", sa.Float(), nullable=True))
    op.create_index(
        "ix_run_tracks_location_eligible",
        "run_tracks",
        ["location_id", "is_course_eligible"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_tracks_location_eligible", table_name="run_tracks")
    op.drop_column("run_tracks", "start_distance_m")
    op.drop_column("run_tracks", "exclusion_note")
    op.drop_column("run_tracks", "exclusion_reason")
    op.drop_column("run_tracks", "is_course_eligible")
