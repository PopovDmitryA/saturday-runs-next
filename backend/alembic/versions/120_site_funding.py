"""Донаты: расходы сайта по месяцам и поступившие переводы

Блок «Поддержать проект» на /about показывает месяц: сколько стоил сайт,
куда ушли деньги и кто, когда и сколько перевёл (решения Дмитрия
30–31.10.2026). Переводы приходят на личные карты и по СБП — сайт их не
видит, поэтому всё вносится руками в админке /admin/donations.

site_cost_items — статьи расходов («Домашний сервер: электричество и интернет»,
«AI-агенты» …): название и подсказка ⓘ для сайта. Сид — семь статей
списка Дмитрия.

site_cost_months — сумма статьи в конкретном месяце (всё в рублях, у
AI-агентов сумма меняется от месяца к месяцу). Будущий месяц — тот же план:
на сайте он появится, когда наступит. Пустой месяц читается копией
последнего заполненного — строк заранее никто не пишет.

site_donations — перевод: дата, сумма, имя, как в переводе («Иван П.»), и
«анонимно». Имена на сайте открыты по умолчанию; анонимно — по просьбе в
комментарии к переводу, тогда на сайте «Аноним», а имя остаётся только в
админке. Связи с профилями на сайте нет.

site_funding_settings — одна строка: показывать ли цифры на сайте.

report_ro: site_donations закрыта — у анонимных переводов там видно имя.
Остальные таблицы открываются роли сами (ALTER DEFAULT PRIVILEGES).

Revision ID: 120_site_funding
Revises: 116_run_plans
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "120_site_funding"
down_revision = "116_run_plans"
branch_labels = None
depends_on = None


_AI_HINT = (
    "AI-агенты нужны, чтобы сайт развивался: с ними пишутся новые разделы, "
    "чинятся ошибки и делаются идеи из бэклога. Сумма меняется от месяца к месяцу."
)

_SEED_ITEMS = [
    ("Домашний сервер: электричество и интернет", None),
    ("Арендованный сервер", None),
    ("Почтовый сервис", None),
    ("Резервные копии и хранилище", None),
    ("Домены", None),
    ("VPN-сервисы", None),
    ("AI-агенты", _AI_HINT),
]

_REPORT_RO_REVOKE = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'report_ro') THEN
    REVOKE ALL ON TABLE site_donations FROM report_ro;
  END IF;
END
$$;
"""


def upgrade() -> None:
    items = op.create_table(
        "site_cost_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("hint", sa.String(length=400), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_site_cost_items_title"),
    )

    op.create_table(
        "site_cost_months",
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("site_cost_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("extract(day from month) = 1", name="ck_site_cost_months_first_day"),
        sa.CheckConstraint("amount_rub >= 0", name="ck_site_cost_months_amount"),
    )
    op.create_index("ix_site_cost_months_month", "site_cost_months", ["month"])

    op.create_table(
        "site_donations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column("donor_name", sa.String(length=80), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount_rub > 0", name="ck_site_donations_amount"),
    )
    op.create_index("ix_site_donations_received_on", "site_donations", ["received_on"])

    op.create_table(
        "site_funding_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("show_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_site_funding_settings_single_row"),
    )
    op.execute("INSERT INTO site_funding_settings (id, show_public) VALUES (1, false)")

    op.bulk_insert(
        items,
        [
            {"title": title, "hint": hint, "sort_order": (index + 1) * 10}
            for index, (title, hint) in enumerate(_SEED_ITEMS)
        ],
    )

    op.execute(_REPORT_RO_REVOKE)


def downgrade() -> None:
    op.drop_table("site_funding_settings")
    op.drop_index("ix_site_donations_received_on", table_name="site_donations")
    op.drop_table("site_donations")
    op.drop_index("ix_site_cost_months_month", table_name="site_cost_months")
    op.drop_table("site_cost_months")
    op.drop_table("site_cost_items")
