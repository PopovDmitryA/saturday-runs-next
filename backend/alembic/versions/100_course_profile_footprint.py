"""Пятачок трассы в паспорте локации

Самый тесный прямоугольник, в который влезает трасса: короткая сторона,
длинная и площадь. Метрика отвечает на вопрос «на каком клочке земли
умещаются пять километров» — у Александрова это 150 × 300 м.

Revision ID: 100_course_profile_footprint
Revises: 099_notifications
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "100_course_profile_footprint"
down_revision = "099_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("box_short_m", "box_long_m", "box_area_m2"):
        op.add_column("location_course_profiles", sa.Column(column, sa.Float(), nullable=True))


def downgrade() -> None:
    for column in ("box_area_m2", "box_long_m", "box_short_m"):
        op.drop_column("location_course_profiles", column)
