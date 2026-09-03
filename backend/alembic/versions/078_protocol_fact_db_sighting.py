"""Неподтверждённые факты выгрузки — по моменту попадания протокола в базу

Часовой синк latest заводит сводку и событие в момент, когда протокол уже
лежит на сайте. Если это раньше, чем протокол увидел обходчик, выгрузкой
считаем момент попадания в базу (Дмитрий 03.09.2026). Для 20 площадок
холодного старта 02.09.2026 это возвращает честные даты: Ставрополь
Комсомольский пруд — 29.08 09:00 вместо 02.09 21:00.

Revision ID: 078_protocol_fact_db_sighting
Revises: 077_protocol_fact_confirmed
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op

revision = "078_protocol_fact_db_sighting"
down_revision = "077_protocol_fact_confirmed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE protocol_upload_facts f
        SET first_seen_at = s.seen, first_seen_confirmed = true, source = 'db'
        FROM (
            SELECT e.location_id, e.event_date,
                   MIN(LEAST(e.created_at, COALESCE(es.created_at, e.created_at))) AS seen
            FROM events e
            LEFT JOIN event_summaries es ON es.event_id = e.id
            GROUP BY e.location_id, e.event_date
        ) s
        WHERE f.location_id = s.location_id
          AND f.event_date = s.event_date
          AND f.first_seen_confirmed = false
          AND s.seen < f.first_seen_at
        """
    )


def downgrade() -> None:
    # Обратного пути нет: прежние значения были заведомо ложными.
    pass
