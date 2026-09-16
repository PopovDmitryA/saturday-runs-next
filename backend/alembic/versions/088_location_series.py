"""Серия стартов вместо однодневок: «Старты сообществ» одной локацией

Было: каждый тематический старт 5 вёрст («Зелёные 5 км», «День физкультурника.
Тула») заводился своей локацией. Каталог заполнялся однодневками — страница на
один старт, с «рекордом трассы» по нему же, — а в профиле у человека локация
каждый раз называлась по-новому.

Стало: одна локация-серия «Старты сообществ» (слаг `starti-soobshchestv` — это
настоящий адрес раздела на 5verst.ru), а собственное имя старта живёт
заголовком события. Ровно так у s95 устроены «С95 и друзья»: одна строка, а
внутри — старты разных лет.

Заодно:
* `is_community_event` → `is_series`: признак теперь общий для 5 вёрст и s95;
* «С95 и друзья» (`s`) и «S95 & Friends» (`bs`) помечаются как серии — до сих
  пор они считались обычными площадками и попадали в туризм;
* адрес события чинится на `/starti-soobshchestv/{слаг}`. Прежний
  `/{слаг}/results/{дата}/` синтезировался по общему правилу 5 вёрст и отдавал
  404 — и в «Источнике» на странице протокола, и в журнале стартов.

Revision ID: 088_location_series
Revises: 087_start_weather_source
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "088_location_series"
down_revision = "087_start_weather_source"
branch_labels = None
depends_on = None

SERIES_SLUG = "starti-soobshchestv"
SERIES_NAME = "Старты сообществ"
SERIES_URL = "https://5verst.ru/starti-soobshchestv/"

#: Всё, что ссылается на locations.location_id помимо events/event_summaries
#: и оценок. Для серии эти строки смысла не имеют: описание трассы, расписание
#: анонсов, координаты, «Первопроходцы» (серии там нет по правилу), погода на
#: старте (её собирают только площадкам с координатами).
DEPENDENT_TABLES = (
    "location_announce_settings",
    "location_catalog_links",
    "location_contacts",
    "location_coordinate_requests",
    "location_descriptions",
    "location_openings",
    "protocol_upload_facts",
    "start_weather",
)


def upgrade() -> None:
    op.alter_column("locations", "is_community_event", new_column_name="is_series")

    conn = op.get_bind()

    # Разъездные серии s95 — те же «формат, а не место».
    conn.execute(
        sa.text(
            """
            UPDATE locations
               SET is_series = true
             WHERE external_key IN ('s', 'bs')
               AND platform_id = (SELECT id FROM platforms WHERE code = 's95')
            """
        )
    )

    platform_id = conn.execute(
        sa.text("SELECT id FROM platforms WHERE code = 'five_verst'")
    ).scalar()
    if platform_id is None:
        return

    old_rows = conn.execute(
        sa.text(
            """
            SELECT id, external_key, name
              FROM locations
             WHERE platform_id = :platform_id
               AND is_series
               AND external_key <> :series_slug
            """
        ),
        {"platform_id": platform_id, "series_slug": SERIES_SLUG},
    ).all()

    series_id = conn.execute(
        sa.text(
            "SELECT id FROM locations WHERE platform_id = :platform_id AND external_key = :slug"
        ),
        {"platform_id": platform_id, "slug": SERIES_SLUG},
    ).scalar()

    if series_id is None:
        if not old_rows:
            # Стартов сообществ в этой базе ещё не было — пустую серию не заводим,
            # её создаст первый же прогон синка раздела.
            return
        series_id = conn.execute(
            sa.text(
                """
                INSERT INTO locations (platform_id, external_key, name, country, source_url,
                                       is_series, is_official_map)
                VALUES (:platform_id, :slug, :name, 'Россия', :url, true, false)
                RETURNING id
                """
            ),
            {"platform_id": platform_id, "slug": SERIES_SLUG, "name": SERIES_NAME, "url": SERIES_URL},
        ).scalar()

    for old_id, old_slug, old_name in old_rows:
        # Заголовок события — имя старта, каким его видит человек в профиле.
        conn.execute(
            sa.text(
                """
                UPDATE events
                   SET location_id = :series_id,
                       title = :title,
                       source_url = :source_url
                 WHERE location_id = :old_id
                """
            ),
            {
                "series_id": series_id,
                "title": old_name,
                "source_url": f"https://5verst.ru/{SERIES_SLUG}/{old_slug}",
                "old_id": old_id,
            },
        )
        conn.execute(
            sa.text("UPDATE event_summaries SET location_id = :series_id WHERE location_id = :old_id"),
            {"series_id": series_id, "old_id": old_id},
        )
        # Оценки людей переносим, а не сносим: человек оценивал старт, на
        # котором был, и этот финиш никуда не делся. location_key в них —
        # идентичность локации; у серии без каталожной связки это «location:id».
        conn.execute(
            sa.text(
                """
                UPDATE location_ratings
                   SET location_id = :series_id,
                       location_key = :series_key
                 WHERE location_id = :old_id
                """
            ),
            {"series_id": series_id, "series_key": f"location:{series_id}", "old_id": old_id},
        )
        for table in DEPENDENT_TABLES:
            conn.execute(
                sa.text(f"DELETE FROM {table} WHERE location_id = :old_id"),
                {"old_id": old_id},
            )
        conn.execute(sa.text("DELETE FROM locations WHERE id = :old_id"), {"old_id": old_id})


def downgrade() -> None:
    # Разъезд обратно на локацию-однодневку не восстанавливаем: имя старта
    # осталось в events.title, и при откате серия просто снова станет обычной
    # строкой каталога.
    op.alter_column("locations", "is_series", new_column_name="is_community_event")
