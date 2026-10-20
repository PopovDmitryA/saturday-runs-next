"""Каталог parkrun в боевой базе: площадки, имена, страны, журнал сверок

Этап 2 (решение Дмитрия 18.10.2026): все данные о площадках parkrun живут в
базе сайта, а не в SQLite parkrun-monitoring на VPS. Сайт сам сверяется с
официальным каталогом events.json, сам лечит свои parkrun-локации и сам
пишет админу сводку в Telegram.

* parkrun_countries — код страны parkrun → домен её сайта. Засеяна здесь же:
  20 действующих стран и две ушедшие (Россия, Франция) без домена.
* parkrun_events — площадки: слаг как у parkrun, id площадки у parkrun, имена,
  страна, серия (5 км / юниоры), точка старта, действует ли, когда появилась
  и пропала.
* parkrun_event_names — все имена площадки (короткое, длинное, местное и
  старые), по ним импорт профиля узнаёт площадку.
* parkrun_catalog_runs, parkrun_event_changes — прогоны сверки и журнал
  изменений каталога.

Описания трасс и история забегов остаются в базе мирового обхода
(parkrun_world на домашнем сервере) — решение Дмитрия 21.10.2026.

Связи с locations нет: площадка находит локацию по слагу. Остальное засевает
scripts/seed_parkrun_catalog.py.

Revision ID: 112_parkrun_catalog
Revises: 111_user_awards
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "112_parkrun_catalog"
down_revision = "111_user_awards"
branch_labels = None
depends_on = None

# Код → (домен, название, действует). Россия и Франция ушли из parkrun:
# домена нет, страна неактивна.
COUNTRIES: list[tuple[int, str | None, str, bool]] = [
    (3, "www.parkrun.com.au", "Australia", True),
    (4, "www.parkrun.co.at", "Austria", True),
    (14, "www.parkrun.ca", "Canada", True),
    (23, "www.parkrun.dk", "Denmark", True),
    (30, "www.parkrun.fi", "Finland", True),
    (31, None, "France", False),
    (32, "www.parkrun.com.de", "Germany", True),
    (42, "www.parkrun.ie", "Ireland", True),
    (44, "www.parkrun.it", "Italy", True),
    (46, "www.parkrun.jp", "Japan", True),
    (54, "www.parkrun.lt", "Lithuania", True),
    (57, "www.parkrun.my", "Malaysia", True),
    (64, "www.parkrun.co.nl", "Netherlands", True),
    (65, "www.parkrun.co.nz", "New Zealand", True),
    (67, "www.parkrun.no", "Norway", True),
    (74, "www.parkrun.pl", "Poland", True),
    (79, None, "Russia", False),
    (82, "www.parkrun.sg", "Singapore", True),
    (85, "www.parkrun.co.za", "South Africa", True),
    (88, "www.parkrun.se", "Sweden", True),
    (97, "www.parkrun.org.uk", "United Kingdom", True),
    (98, "www.parkrun.us", "United States", True),
]


def upgrade() -> None:
    countries = op.create_table(
        "parkrun_countries",
        sa.Column("code", sa.SmallInteger(), primary_key=True),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.bulk_insert(
        countries,
        [{"code": code, "domain": domain, "name": name, "is_active": active} for code, domain, name, active in COUNTRIES],
    )

    op.create_table(
        "parkrun_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parkrun_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("short_name", sa.String(length=256), nullable=True),
        sa.Column("long_name", sa.String(length=256), nullable=True),
        sa.Column("local_name", sa.String(length=256), nullable=True),
        sa.Column("country_code", sa.SmallInteger(), nullable=True),
        sa.Column("series_id", sa.SmallInteger(), nullable=True),
        sa.Column("place", sa.String(length=256), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="live"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_parkrun_events_slug"),
    )
    op.create_index(
        "uq_parkrun_events_parkrun_id",
        "parkrun_events",
        ["parkrun_id"],
        unique=True,
        postgresql_where=sa.text("parkrun_id IS NOT NULL"),
    )
    op.create_index("ix_parkrun_events_country_active", "parkrun_events", ["country_code", "is_active"])

    op.create_table(
        "parkrun_event_names",
        sa.Column("name_norm", sa.String(length=256), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("parkrun_events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
    )
    op.create_index("ix_parkrun_event_names_event", "parkrun_event_names", ["event_id"])

    op.create_table(
        "parkrun_catalog_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=True),
        sa.Column("active_total", sa.Integer(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_parkrun_catalog_runs_started", "parkrun_catalog_runs", ["started_at"])

    op.create_table(
        "parkrun_event_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("parkrun_catalog_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("parkrun_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_parkrun_event_changes_event", "parkrun_event_changes", ["event_id"])
    op.create_index("ix_parkrun_event_changes_detected", "parkrun_event_changes", ["detected_at"])


def downgrade() -> None:
    op.drop_table("parkrun_event_changes")
    op.drop_table("parkrun_catalog_runs")
    op.drop_table("parkrun_event_names")
    op.drop_table("parkrun_events")
    op.drop_table("parkrun_countries")
