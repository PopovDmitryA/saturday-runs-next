"""Основа уведомлений сайта: каналы, настройки, журнал доставок, подписки на карточки бэклога

Уведомления пока о трёх вещах: своя карточка в бэклоге (принята, комментарии,
статус), новые карточки бэклога для любопытных и новая пробежка — одним
сообщением с рейтингами, челленджами и вехами. Запись на волонтёрство
зарезервирована видом в реестре (app/notification_kinds.py), таблиц под неё
не нужно — виды живут в коде, а не в БД.

Четыре таблицы:

* user_notification_channels — куда доставлять: Telegram (chat_id), VK (id
  пользователя), почта (адрес привязки). Строка появляется, когда человек
  включил уведомления на этом способе входа; нет ни одной включённой строки
  — ничего не шлём (умолчание выключено). Отдельно от users, потому что
  канал — это не «знаем адрес», а «человек включил» плюс результат проверки,
  что доставка возможна (бот не заблокирован, VK разрешил сообщения).
* user_notification_prefs — одна строка на человека: основной канал,
  переключатели видов в JSONB (нет ключа = умолчание реестра), водяные знаки
  и снимки для сканера активности (челленджи, рейтинги, вехи), отметки
  «настройки трогал» и «баннер закрыл».
* notification_deliveries — журнал: одна строка = одно сообщение человеку.
  dedupe_key защищает от повторов (тот же комментарий, та же пробежка),
  channel — куда в итоге ушло после перебора резервных каналов.
* backlog_card_subscriptions — кто следит за какой карточкой.

Все ссылки на users — ON DELETE CASCADE: при объединении профилей настройки
поглощаемого просто исчезают (см. BLOCKING_USER_REFERENCES в
account_merge_service — эти таблицы удаление НЕ держат).

Revision ID: 099_notifications
Revises: 098_course_profile_geometry
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "099_notifications"
down_revision = "098_course_profile_geometry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notification_prefs",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # telegram | vk | email | NULL (порядок по умолчанию).
        sa.Column("primary_channel", sa.String(length=16), nullable=True),
        # {"runs": true, "backlog": false, ...}; отсутствующий ключ — умолчание реестра.
        sa.Column("kinds", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        # Снимок уровней челленджей на момент последнего сканирования:
        # {"seconds": {"easy": "gold", "medium": "silver", "hard": null}, ...}.
        # NULL — снимка ещё не было: первый скан только запоминает, не шлёт.
        sa.Column("challenge_levels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Снимок мест в рейтингах {"runs": 45, "locations": 120, ...} — чтобы
        # написать «поднялись на 3 позиции».
        sa.Column("ratings_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Ключи уже известных вех «Моей истории» — новые относительно них
        # попадают в сообщение о пробежке.
        sa.Column("milestones_seen", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Докуда разобраны run_results.created_at для «новых пробежек».
        sa.Column("runs_notified_through", sa.DateTime(timezone=True), nullable=True),
        # Человек хоть раз менял что-то в настройках уведомлений: колокольчик
        # и баннеры не включают уведомления тому, кто их выключил сам.
        sa.Column("settings_touched_at", sa.DateTime(timezone=True), nullable=True),
        # Баннер «включите уведомления» закрыт — больше не показываем.
        sa.Column("nudge_dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "user_notification_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # telegram | vk | email
        sa.Column("channel", sa.String(length=16), nullable=False),
        # chat_id Telegram, id пользователя VK или нормализованный адрес почты.
        sa.Column("external_id", sa.String(length=256), nullable=False),
        # Человек включил уведомления на этом способе входа.
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Результат последней проверки доставляемости (бот не заблокирован,
        # VK разрешил сообщения): когда проверяли и что вышло. NULL — не проверяли.
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_ok", sa.Boolean(), nullable=True),
        sa.Column("check_error", sa.Text(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", name="uq_user_notification_channels_user_channel"),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        # Ключ повтора внутри (user, kind): id комментария, набор пробежек…
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        # queued | sent | failed | skipped
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        # Канал, куда в итоге доставлено (или последний, куда пытались).
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        # title, text (разметка app/notification_markup.py), url, url_label.
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "dedupe_key", name="uq_notification_deliveries_dedupe"),
    )
    op.create_index("ix_notification_deliveries_user_created", "notification_deliveries", ["user_id", "created_at"])
    op.create_index("ix_notification_deliveries_status_created", "notification_deliveries", ["status", "created_at"])

    op.create_table(
        "backlog_card_subscriptions",
        sa.Column(
            "card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backlog_cards.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_backlog_card_subscriptions_user", "backlog_card_subscriptions", ["user_id"])

    # Автор карточки следит за ней по умолчанию — заводим подписки задним
    # числом, иначе комментарии к старым карточкам пройдут мимо авторов.
    op.execute(
        """
        INSERT INTO backlog_card_subscriptions (card_id, user_id)
        SELECT id, author_user_id FROM backlog_cards
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO backlog_card_subscriptions (card_id, user_id)
        SELECT DISTINCT card_id, author_user_id FROM backlog_comments
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_backlog_card_subscriptions_user", table_name="backlog_card_subscriptions")
    op.drop_table("backlog_card_subscriptions")
    op.drop_index("ix_notification_deliveries_status_created", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_user_created", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("user_notification_channels")
    op.drop_table("user_notification_prefs")
