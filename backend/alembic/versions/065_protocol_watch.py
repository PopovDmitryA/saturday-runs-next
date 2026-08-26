"""Наблюдение за выгрузкой протоколов 5 вёрст + время старта локаций

Четыре изменения одного релиза «скорость протоколов и здоровье локации»
(решения Дмитрия 23.08.2026, аудит легаси date_load_protocol.py):

* protocol_upload_facts — момент ПЕРВОГО появления протокола на
  5verst.ru/results/latest/. Это факт наблюдения за внешним сайтом, а не
  свойство события: событие может попасть в нашу БД позже, чем мы заметили
  протокол, поэтому ключ — (location_id, event_date), не event_id.
  source: 'legacy' — разовый импорт из five_verst_stats (история с 08.2024),
  'site' — собственный поминутный коллектор.

* locations.tz_offset_moscow — смещение локации от Москвы в часах (импорт из
  легаси general_location.tz_from_moscow). Нужно для честной задержки:
  время старта хранится в местном времени, момент фиксации — в UTC.

* location_descriptions.schedule_parsed — распарсенное расписание стартов
  ([{from_month, to_month, time}]) из schedule_text той же строки. Живёт рядом
  с источником и пересчитывается при каждом обновлении описания.

* protocol_revisions — журнал правок протокола: перечитка обнаружила, что
  протокол изменился (замена «Неизвестного» на имя правкой не считается).

Revision ID: 065_protocol_watch
Revises: 064_location_organizer_access
Create Date: 2026-08-23
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "065_protocol_watch"
down_revision = "064_location_organizer_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocol_upload_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="site"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("location_id", "event_date", name="uq_protocol_upload_facts_location_date"),
    )
    op.create_index("ix_protocol_upload_facts_event_date", "protocol_upload_facts", ["event_date"])

    op.add_column("locations", sa.Column("tz_offset_moscow", sa.Integer(), nullable=True))

    op.add_column(
        "location_descriptions",
        sa.Column("schedule_parsed", postgresql.JSONB(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "protocol_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_protocol_revisions_event", "protocol_revisions", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_protocol_revisions_event", table_name="protocol_revisions")
    op.drop_table("protocol_revisions")
    op.drop_column("location_descriptions", "schedule_parsed")
    op.drop_column("locations", "tz_offset_moscow")
    op.drop_index("ix_protocol_upload_facts_event_date", table_name="protocol_upload_facts")
    op.drop_table("protocol_upload_facts")
