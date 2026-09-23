"""Уведомления об отменах стартов: выбор систем

Отмена ближайшего старта касается конкретной площадки, и человеку интересны
только те системы, где он бегает: тому, кто не бывает на S95, её отмены не
нужны. Список кодов платформ хранится рядом с остальными настройками
уведомлений; пустой список = все системы (умолчание).

Revision ID: 101_notify_cancel_platforms
Revises: 100_course_profile_footprint
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "101_notify_cancel_platforms"
down_revision = "100_course_profile_footprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_notification_prefs",
        sa.Column(
            "cancellation_platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_notification_prefs", "cancellation_platforms")
