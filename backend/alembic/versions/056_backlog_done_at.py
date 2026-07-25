"""backlog_cards.done_at — когда карточку перевели в «реализовано»

Лента бэклога показывает возраст карточки («заведена 3 дня назад»), а у
реализованных полезнее другая точка отсчёта — когда фича доехала. Отдельная
колонка вместо анализа истории статусов: истории у нас нет, а знать нужно
только последний перевод в done.

Revision ID: 056_backlog_done_at
Revises: 055_user_avatar
Create Date: 2026-07-26
"""

import sqlalchemy as sa

from alembic import op

revision = "056_backlog_done_at"
down_revision = "055_user_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backlog_cards", sa.Column("done_at", sa.DateTime(timezone=True), nullable=True))
    # У уже реализованных карточек точной даты нет — берём момент последнего
    # изменения: ближе к правде, чем NULL «неизвестно когда».
    op.execute("UPDATE backlog_cards SET done_at = updated_at WHERE status = 'done'")


def downgrade() -> None:
    op.drop_column("backlog_cards", "done_at")
