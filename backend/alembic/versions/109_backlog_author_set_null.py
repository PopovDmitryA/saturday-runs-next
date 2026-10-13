"""Бэклог переживает удаление профиля автора

Человек может удалить свой профиль на сайте (настройки → «Удаление профиля»).
Его карточки и комментарии в бэклоге при этом остаются (решение Дмитрия
12.10.2026): под ними голоса, ответы и подписки других людей, а у карточки со
статусом «Реализовано» — ещё и история сайта. Автор показывается как «Профиль
удалён». Поэтому:
  * backlog_cards.author_user_id и backlog_comments.author_user_id — NULL
    допустим, FK → ON DELETE SET NULL;
  * backlog_votes.user_id — FK → ON DELETE CASCADE. Голос без человека ничего
    не значит; счётчики карточек сервис удаления пересчитывает сам
    (backlog_service.detach_user_from_backlog), каскад — только страховка.

Имена FK в 052 не задавались явно, поэтому старые ищем в живой схеме по
колонке, а новые создаём с явными именами «<таблица>_<колонка>_fkey».

Revision ID: 109_backlog_author_set_null
Revises: 108_display_name_email
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "109_backlog_author_set_null"
down_revision = "108_display_name_email"
branch_labels = None
depends_on = None


def _users_fk_names(table: str, column: str) -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [
        str(fk["name"])
        for fk in inspector.get_foreign_keys(table)
        if fk.get("name") and fk["referred_table"] == "users" and fk["constrained_columns"] == [column]
    ]


def _swap_users_fk(table: str, column: str, *, ondelete: str | None) -> None:
    for name in _users_fk_names(table, column):
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        f"{table}_{column}_fkey",
        table,
        "users",
        [column],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.alter_column("backlog_cards", "author_user_id", existing_type=sa.Uuid(), nullable=True)
    _swap_users_fk("backlog_cards", "author_user_id", ondelete="SET NULL")

    op.alter_column("backlog_comments", "author_user_id", existing_type=sa.Uuid(), nullable=True)
    _swap_users_fk("backlog_comments", "author_user_id", ondelete="SET NULL")

    _swap_users_fk("backlog_votes", "user_id", ondelete="CASCADE")


def downgrade() -> None:
    # Прежняя схема автора без профиля выразить не может (NOT NULL). Такие
    # реплики и карточки снимаем; голоса, комментарии, подписки и строки фото
    # под карточками уходят по CASCADE card_id. Файлы фото в хранилище при этом
    # остаются сиротами — откат схемы их не трогает.
    op.execute("DELETE FROM backlog_comments WHERE author_user_id IS NULL")
    op.execute("DELETE FROM backlog_cards WHERE author_user_id IS NULL")

    _swap_users_fk("backlog_votes", "user_id", ondelete=None)

    _swap_users_fk("backlog_comments", "author_user_id", ondelete=None)
    op.alter_column("backlog_comments", "author_user_id", existing_type=sa.Uuid(), nullable=False)

    _swap_users_fk("backlog_cards", "author_user_id", ondelete=None)
    op.alter_column("backlog_cards", "author_user_id", existing_type=sa.Uuid(), nullable=False)
