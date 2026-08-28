"""Причина отмены ближайшего старта словами организатора

s95 пишет её на странице площадки красной плашкой («Отмена забега 29 августа.
Увидимся на Набережной 5 сентября» — Иваново, 27.08.2026). Флаг is_cancelled
отвечает на вопрос «побегут ли в субботу», а человеку у экрана нужно ещё и
«почему и когда снова» — это здесь.

Revision ID: 074_location_cancel_reason
Revises: 073_platform_link_method
Create Date: 2026-08-27
"""

import sqlalchemy as sa

from alembic import op

revision = "074_location_cancel_reason"
down_revision = "073_platform_link_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("cancel_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("locations", "cancel_reason")
