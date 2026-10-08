"""«Не сейчас» в призыве включить уведомления — на 60 дней, а не до закрытия вкладки

Раньше «Не сейчас» пряталось в sessionStorage, и окно всплывало при каждом
новом открытии сайта: на телефоне почти каждый переход по ссылке — новая
вкладка. Дмитрий Дьяченко пять дней подряд отказывался и завёл карточку
«Навязчивый пуш включить уведомления» (05.10.2026). Теперь отказ живёт на
сервере и действует на всех устройствах.

Revision ID: 107_notification_nudge_snooze
Revises: 106_nearby_query_log
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "107_notification_nudge_snooze"
down_revision = "106_nearby_query_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_notification_prefs",
        sa.Column("nudge_snoozed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_notification_prefs", "nudge_snoozed_at")
