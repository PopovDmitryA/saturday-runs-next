"""Отметка последнего визита пользователя

Журнал входов отвечает на вопрос «когда человек авторизовался», но не на
вопрос «когда он последний раз пользовался сайтом»: сессия живёт долго, и
между двумя входами могут лежать месяцы ежедневных заходов. Просмотры страниц
у нас уже пишутся (page_view_events.viewer_user_id), но сырые события живут
ограниченный срок — поэтому последний визит дублируем в users.last_seen_at,
чтобы он не терялся вместе с событиями.

Revision ID: 076_user_last_seen
Revises: 075_page_view_is_bot
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "076_user_last_seen"
down_revision = "075_page_view_is_bot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    # Журнал визитов читает события одного человека от свежих к старым.
    op.create_index(
        "ix_page_view_events_viewer_ts",
        "page_view_events",
        ["viewer_user_id", "ts"],
        postgresql_where=sa.text("viewer_user_id IS NOT NULL"),
    )
    # Задним числом: последний визит есть в уже накопленных событиях.
    op.execute(
        """
        UPDATE users
           SET last_seen_at = latest.ts
          FROM (
                SELECT viewer_user_id, MAX(ts) AS ts
                  FROM page_view_events
                 WHERE viewer_user_id IS NOT NULL AND NOT is_bot
                 GROUP BY viewer_user_id
               ) AS latest
         WHERE latest.viewer_user_id = users.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_page_view_events_viewer_ts", table_name="page_view_events")
    op.drop_column("users", "last_seen_at")
