"""Журнал запросов кода на почту — воронка «письмо ушло → человек вошёл»

Вход по коду упирается в доставку: письмо от нового отправителя почтовики
охотно кладут в спам, и человек, не найдя его, просто уходит. Доказать это
было нечем — в базе оставались только состоявшиеся входы, а несостоявшиеся не
оставляли следа вовсе.

Одна строка = одно отправленное письмо с кодом. Дошло ли оно, почтовик нам не
скажет, но разрыв между числом писем и числом входов — и есть цена доставки.
Домен получателя в отдельной колонке: если у gmail конверсия вдвое ниже, чем у
яндекса, вопрос уже не «люди ленивые», а «письма не долетают до Google».

Самого адреса здесь нет: лежит sha256 нормализованного ящика и домен. По хэшу
можно найти строки конкретного человека (посчитав хэш от его адреса), но дамп
таблицы не превращается в список чужих почт.

Revision ID: 082_email_login_requests
Revises: 081_volunteer_display_name
Create Date: 2026-09-06
"""

import sqlalchemy as sa

from alembic import op

revision = "082_email_login_requests"
down_revision = "081_volunteer_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_login_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # sha256 нормализованного адреса: связывает письма одного ящика между
        # собой, не храня сам ящик.
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        # Домен уже нормализован (googlemail → gmail.com, ya.ru → yandex.ru),
        # иначе один почтовик размазался бы по нескольким строкам отчёта.
        sa.Column("domain", sa.String(length=64), nullable=False, server_default=""),
        # login — вход на /login, link — привязка почты к готовому профилю.
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default="login"),
        # Ящик сайту уже знаком (входил раньше или подтверждён Яндексом).
        # Новичок и возвращающийся ищут письмо по-разному, и конверсия у них
        # разная — без этой колонки они складываются в одно среднее.
        sa.Column("known_mailbox", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Момент, когда именно этот код сработал. NULL — письмо ушло, а вход
        # не состоялся.
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        # Неверно введённые коды. Отличают «письмо не дошло» (ноль попыток) от
        # «дошло, но человек ошибся или взял код из старого письма».
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index("ix_email_login_requests_requested_at", "email_login_requests", ["requested_at"])
    op.create_index("ix_email_login_requests_email_hash", "email_login_requests", ["email_hash", "requested_at"])


def downgrade() -> None:
    op.drop_index("ix_email_login_requests_email_hash", table_name="email_login_requests")
    op.drop_index("ix_email_login_requests_requested_at", table_name="email_login_requests")
    op.drop_table("email_login_requests")
