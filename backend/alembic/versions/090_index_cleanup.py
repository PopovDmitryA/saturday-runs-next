"""Индексы: недостающий на event_summaries.event_id, минус дубли и мёртвые

Аудит 13.09.2026, находки SCHEMA-02/03/07/08. Все операции с индексами идут
CONCURRENTLY в autocommit-блоке: прод деплоится с работающими воркерами, и
блокировать events или participants на время перестройки нельзя.

1. SCHEMA-02. `event_summaries.event_id` — FK на events и колонка, по которой
   ищут страница протокола (location_protocol_service), витрина главной и три
   пути синка 5 вёрст (watch раз в минуту по субботам, reconcile, week sweep).
   Индекса не было: EXPLAIN на dev-копии (36 479 строк) — Seq Scan, 1905 буферов,
   7.7 мс на точечный поиск. Плюс FK без индекса заставлял каждый DELETE события
   (чистка осиротевших в дедупе) сканировать таблицу целиком.

2. SCHEMA-03. `ix_participants_platform_external_user_id` — побайтовый дубль
   уникалки `uq_participants_platform_external_user_id` (те же колонки в том же
   порядке). Планировщик его не выбирал ни разу (idx_scan=0 против 248 936 у
   уникалки), а поддерживался он на каждом upsert участника — самом горячем
   пути записи. 19 МБ на dev.

3. SCHEMA-07. `ix_event_summaries_summary_hash` — хэш саммари сравнивается в
   Python уже после выборки строки по (platform_id, external_event_key), в
   WHERE он не попадает никогда (idx_scan=0). `ix_page_view_events_is_bot` —
   частичный индекс WHERE is_bot, а все читатели фильтруют ровно наоборот
   (is_bot IS FALSE), такой индекс им не годится (idx_scan=0).

4. SCHEMA-08. На events было два перекрывающихся индекса по (локация, дата):
   уникальный (platform_id, location_id, event_date) из миграции 009 и обычный
   (location_id, event_date), которым и пользуются сервисы (1.68М обращений).
   Локация принадлежит ровно одной системе (locations.platform_id), поэтому
   уникальность по (location_id, event_date) — то же ограничение, и один индекс
   закрывает обе роли. Инвариант проверен на dev: событий с platform_id, не
   совпадающим с платформой своей локации, — ноль; дублей (location_id,
   event_date) — ноль. Перед созданием уникалки проверяем это и на живой базе:
   лучше остановить миграцию с понятным сообщением, чем получить INVALID индекс.

5. SCHEMA-04 (кусок про типы). `location_merge_requests.overlap_event_dates`
   создавался как JSON (миграция 019), а модель объявляет JSONB — из-за этого
   autogenerate всегда видел «изменение типа». Приводим БД к модели.

Revision ID: 090_index_cleanup
Revises: 089_start_weather_forecast
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "090_index_cleanup"
down_revision = "089_start_weather_forecast"
branch_labels = None
depends_on = None


def _assert_events_unique_by_location_date() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ("
                "  SELECT location_id, event_date FROM events"
                "  GROUP BY 1, 2 HAVING count(*) > 1"
                ") d"
            )
        )
        .scalar()
    )
    if duplicates:
        raise RuntimeError(
            f"events: {duplicates} пар (location_id, event_date) встречаются больше раза — "
            "уникальный индекс uq_events_location_event_date не построить. "
            "Разберите дубли (обычно это кросслинк, заведённый второй строкой события) и повторите."
        )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE location_merge_requests "
        "ALTER COLUMN overlap_event_dates TYPE JSONB USING overlap_event_dates::jsonb"
    )
    # ALTER TYPE не трогает DEFAULT: без этой строки в колонке jsonb остаётся
    # умолчание '[]'::json, и сравнение умолчаний (compare_server_default в
    # env.py) падает — у типа json нет оператора равенства.
    op.execute(
        "ALTER TABLE location_merge_requests "
        "ALTER COLUMN overlap_event_dates SET DEFAULT '[]'::jsonb"
    )
    _assert_events_unique_by_location_date()

    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_summaries_event_id "
            "ON event_summaries (event_id)"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_events_location_event_date "
            "ON events (location_id, event_date)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_events_platform_location_event_date")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_events_location_event_date")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_participants_platform_external_user_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_event_summaries_summary_hash")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_page_view_events_is_bot")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_page_view_events_is_bot "
            "ON page_view_events (is_bot) WHERE is_bot"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_event_summaries_summary_hash "
            "ON event_summaries (summary_hash)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_participants_platform_external_user_id "
            "ON participants (platform_id, external_user_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_location_event_date "
            "ON events (location_id, event_date)"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_events_platform_location_event_date "
            "ON events (platform_id, location_id, event_date)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_events_location_event_date")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_event_summaries_event_id")

    op.execute(
        "ALTER TABLE location_merge_requests "
        "ALTER COLUMN overlap_event_dates TYPE JSON USING overlap_event_dates::json"
    )
    op.execute(
        "ALTER TABLE location_merge_requests "
        "ALTER COLUMN overlap_event_dates SET DEFAULT '[]'::json"
    )
