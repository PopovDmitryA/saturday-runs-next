"""Системы для плитки «Куда дальше»

Некоторые смотрят туризм только по одной системе — например, коллекционируют
площадки 5 вёрст и parkrun им не нужен. Список кодов платформ, по которым
считается ближайшая непосещённая площадка на плитке кабинета; пустой список —
все системы, как раньше.

Revision ID: 085_user_tourism_platforms
Revises: 084_location_community_event
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "085_user_tourism_platforms"
down_revision = "084_location_community_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tourism_platforms", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("users", "tourism_platforms")
