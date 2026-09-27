"""Снимок счётчиков челленджей для уведомлений

До этого в уведомлении о пробежке появлялись только взятые уровни, а человеку
интересен и обычный прогресс: «Алфавит +1». Чтобы отличить рост счётчика от
неизменного состояния, рядом со снимком уровней храним снимок самих значений.

Revision ID: 104_notify_challenge_counts
Revises: 103_participant_name_fold_trgm
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "104_notify_challenge_counts"
down_revision = "103_participant_name_fold_trgm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_notification_prefs",
        sa.Column("challenge_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_notification_prefs", "challenge_counts")
