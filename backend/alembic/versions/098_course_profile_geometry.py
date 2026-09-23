"""Геометрия трассы в паспорте локации

Чтобы на странице локации рисовать саму трассу, а не только профиль высот,
храним представительную линию — прореженный трек с медианной длиной.

Revision ID: 098_course_profile_geometry
Revises: 097_location_course_profiles
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "098_course_profile_geometry"
down_revision = "097_location_course_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "location_course_profiles",
        sa.Column("geometry", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("location_course_profiles", "geometry")
