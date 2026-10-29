"""Планировщик забегов: планы участников, постоянные планы и особые даты стартов

План — человек × локация × дата: «планирую пробежать». Волонтёров
планировщик не ведёт — они записываются в кабинетах систем (решение Дмитрия
25.10.2026), поэтому намерения в строке нет. Локация хранится ключом
идентичности (`catalog:<id>` / `location:<id>`), как домашняя локация и
отзывы: одна физическая точка на всех системах. Рядом — опорная строка
`location_id` (система, пояс, ≈№): ключ со временем дрейфует (`location:`
становится `catalog:`, когда локацию заводят в каталог), и чтение ищет план
по `location_key = ключ OR location_id IN (строки идентичности)`.

Отметка «Не бегаю» (`rest`, решение Дмитрия 26.10.2026) — та же таблица без
локации: строка дня, одна на дату. Строки дня без отметки не бывает, у плана с
локацией отметки не бывает — CHECK ниже держит оба вида строк в своих рамках.
Заметок нет — ни к плану, ни на день (решение Дмитрия 27.10.2026: перегружали
интерфейс, вернём планомерно).

Лимит «1 план на обычную дату, до 3 на особую» CHECK-ом не выразить — его
держит сервис (run_plan_service) под блокировкой строки пользователя.

Особые даты (Новый год, праздничные старты в будни) ведёт админ: даты
календаря локации — субботы плюс эти. Сид — 01.01.2027 для трёх систем.

Публичность — выбор человека на каждой строке (`is_public` у плана, у
строки дня — для отметки «Не бегаю») и один на весь постоянный план (решение
Дмитрия 27.10.2026: «эту неделю публично, следующую приватно»; отдельного
согласия на показ планов нет — абзац о планах в общем согласии). Умолчание —
как профиль: открыт — публично. Итог считается при чтении: закрытый профиль
и возраст до 15 лет прячут имя поверх выбора.

Постоянный план («каждую субботу», решение Дмитрия 25.10.2026) — правило и
исключения, как повторяющаяся встреча в календаре: одна строка
`run_plan_series` (человек × точка × с какой даты и до какой), строк на
каждую неделю нет и ночного дописывания нет. Вхождения — даты календаря
точки — считаются при чтении одной функцией (planned_starts_service). Исключения
— обычные строки run_plans: «Не бегаю» на день убирает вхождение, разовый план
в другой локации его вытесняет. Активная серия у человека одна (частичный
уникальный индекс по `stopped_at IS NULL`). Остановленные не удаляются:
будущие субботы уходят, прошлые остаются в истории «Планов» и в «пришли M из N»
оргкалендаря. Сон («ещё ходите?» после трёх стартов дома без человека) — на
самой серии: с какой даты спит, какие старты решили «пропуск», закрытые сны.
`users.plans_hint_snoozed_until` — «Не сейчас» у подсказки «вы бегаете здесь
каждую субботу», одна на человека на всех устройствах.

report_ro: планы людей закрыты целиком (решено 28.10.2026): run_plans и
run_plan_series — кто, куда и когда собирается, включая приватные планы и сон
постоянного плана («когда не приходил»). Строки notification_deliveries видов
plan_reminders, plan_cancellations и plan_series (в payload тексты с названием
локации) скрыты политикой row-level security: остальные виды роль читает как
раньше. Роль report_ro есть только на проде, блок срабатывает там. Новые
колонки закрытых таблиц открывать не нужно.

Revision ID: 116_run_plans
Revises: 115_challenge_ratings
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "116_run_plans"
down_revision = "115_challenge_ratings"
branch_labels = None
depends_on = None


_PLAN_DELIVERY_KINDS = "'plan_reminders', 'plan_cancellations', 'plan_series'"

_REPORT_RO_GRANTS = f"""
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_ro') THEN
    REVOKE ALL ON TABLE run_plans FROM report_ro;
    REVOKE ALL ON TABLE run_plan_series FROM report_ro;
    ALTER TABLE notification_deliveries ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS report_ro_no_plan_messages ON notification_deliveries;
    CREATE POLICY report_ro_no_plan_messages ON notification_deliveries
      FOR SELECT TO report_ro USING (kind NOT IN ({_PLAN_DELIVERY_KINDS}));
  END IF;
END $$;
"""

_REPORT_RO_REVERT = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_ro') THEN
    DROP POLICY IF EXISTS report_ro_no_plan_messages ON notification_deliveries;
    ALTER TABLE notification_deliveries DISABLE ROW LEVEL SECURITY;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "run_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("location_key", sa.String(length=255), nullable=True),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform_code", sa.String(length=32), nullable=True),
        sa.Column("rest", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(location_key IS NULL AND rest) OR (location_key IS NOT NULL AND NOT rest)",
            name="ck_run_plans_kind",
        ),
    )
    op.create_index(
        "uq_run_plans_user_date_location",
        "run_plans",
        ["user_id", "plan_date", "location_key"],
        unique=True,
        postgresql_where=sa.text("location_key IS NOT NULL"),
    )
    op.create_index(
        "uq_run_plans_user_date_day",
        "run_plans",
        ["user_id", "plan_date"],
        unique=True,
        postgresql_where=sa.text("location_key IS NULL"),
    )
    op.create_index("ix_run_plans_location_date", "run_plans", ["location_key", "plan_date"])
    op.create_index("ix_run_plans_location_id_date", "run_plans", ["location_id", "plan_date"])
    op.create_index("ix_run_plans_user_date", "run_plans", ["user_id", "plan_date"])
    op.create_index("ix_run_plans_date", "run_plans", ["plan_date"])

    op.create_table(
        "start_special_dates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("platforms", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("start_date", name="uq_start_special_dates_date"),
    )
    op.execute(
        """
        INSERT INTO start_special_dates (start_date, title, platforms)
        VALUES ('2027-01-01', 'Новогодний старт', '["five_verst", "s95", "runpark"]'::jsonb)
        ON CONFLICT (start_date) DO NOTHING
        """
    )

    op.add_column("users", sa.Column("plans_hint_snoozed_until", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "run_plan_series",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location_key", sa.String(length=255), nullable=False),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("until", sa.Date(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resumed_on", sa.Date(), nullable=True),
        sa.Column("sleeping_since", sa.Date(), nullable=True),
        sa.Column("sleep_missed", postgresql.ARRAY(sa.Date()), nullable=True),
        sa.Column("sleep_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sleep_history", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("until IS NULL OR until >= starts_on", name="ck_run_plan_series_until"),
    )
    op.create_index(
        "uq_run_plan_series_user_active",
        "run_plan_series",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("stopped_at IS NULL"),
    )
    op.create_index("ix_run_plan_series_location", "run_plan_series", ["location_key"])
    op.create_index("ix_run_plan_series_location_id", "run_plan_series", ["location_id"])
    op.create_index("ix_run_plan_series_user", "run_plan_series", ["user_id"])

    op.execute(_REPORT_RO_GRANTS)


def downgrade() -> None:
    op.execute(_REPORT_RO_REVERT)
    op.drop_index("ix_run_plan_series_user", table_name="run_plan_series")
    op.drop_index("ix_run_plan_series_location_id", table_name="run_plan_series")
    op.drop_index("ix_run_plan_series_location", table_name="run_plan_series")
    op.drop_index("uq_run_plan_series_user_active", table_name="run_plan_series")
    op.drop_table("run_plan_series")
    op.drop_column("users", "plans_hint_snoozed_until")
    op.drop_table("start_special_dates")
    op.drop_index("ix_run_plans_date", table_name="run_plans")
    op.drop_index("ix_run_plans_user_date", table_name="run_plans")
    op.drop_index("ix_run_plans_location_id_date", table_name="run_plans")
    op.drop_index("ix_run_plans_location_date", table_name="run_plans")
    op.drop_index("uq_run_plans_user_date_day", table_name="run_plans")
    op.drop_index("uq_run_plans_user_date_location", table_name="run_plans")
    op.drop_table("run_plans")
