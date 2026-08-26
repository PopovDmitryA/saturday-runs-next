"""Разовый импорт истории выгрузки протоколов из легаси five_verst_stats.

Источник — легаси-БД коллектора date_load_protocol.py (root-крон на проде):
* general_date_load_protocol: name_point, date_event, date_load (МОСКОВСКОЕ
  наивное время первого обнаружения протокола), start_time (местное время
  старта — здесь НЕ импортируется: расписание живёт в schedule_parsed);
* general_link_all_location: name_point → https://5verst.ru/{slug}/;
* general_location: tz_from_moscow (смещение локации от Москвы в часах).

Пишет protocol_upload_facts (source='legacy', first_seen_at в UTC = МСК − 3ч)
и locations.tz_offset_moscow. Повторный запуск безопасен: конфликтные факты
пропускаются, наблюдения собственного коллектора ('site') не перетираются.

Запуск (изнутри контейнера api, DSN легаси передаётся через окружение):
    LEGACY_DSN='postgresql://user:pass@host:5432/five_verst_stats' \
        python scripts/import_legacy_protocol_facts.py
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_session_factory  # noqa: E402
from app.models import Location, Platform, ProtocolUploadFact  # noqa: E402

MSK = timezone(timedelta(hours=3))


def _slug_from_link(link: str) -> str | None:
    marker = "5verst.ru/"
    if marker not in link:
        return None
    slug = link.split(marker, 1)[1].strip("/")
    return slug or None


def main() -> None:
    legacy_dsn = os.environ.get("LEGACY_DSN")
    if not legacy_dsn:
        raise SystemExit("Нужен LEGACY_DSN с доступом к five_verst_stats")
    # В образе api стоит psycopg3 — голую схему postgresql:// приводим к нему.
    if legacy_dsn.startswith("postgresql://"):
        legacy_dsn = legacy_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    legacy = sa.create_engine(legacy_dsn)
    with legacy.connect() as conn:
        links = {
            name: _slug_from_link(link)
            for name, link in conn.execute(
                sa.text("select name_point, link_point from general_link_all_location")
            )
        }
        tz_rows = {
            name: tz
            for name, tz in conn.execute(
                sa.text(
                    "select name_point, tz_from_moscow from general_location "
                    "where tz_from_moscow is not null"
                )
            )
        }
        facts = conn.execute(
            sa.text(
                "select name_point, date_event::date, date_load "
                "from general_date_load_protocol where date_load is not null"
            )
        ).all()

    db = get_session_factory()()
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()
    locations = {
        row.external_key: row
        for row in db.query(Location).filter(Location.platform_id == platform.id).all()
    }

    tz_updated = 0
    for name, tz in tz_rows.items():
        slug = links.get(name)
        location = locations.get(slug) if slug else None
        if location is not None and location.tz_offset_moscow != int(tz):
            location.tz_offset_moscow = int(tz)
            tz_updated += 1

    inserted = 0
    skipped_no_slug: set[str] = set()
    for name, event_date, date_load in facts:
        slug = links.get(name)
        location = locations.get(slug) if slug else None
        if location is None:
            skipped_no_slug.add(name)
            continue
        first_seen = date_load.replace(tzinfo=MSK).astimezone(timezone.utc)
        result = db.execute(
            pg_insert(ProtocolUploadFact)
            .values(
                location_id=location.id,
                event_date=event_date,
                first_seen_at=first_seen,
                source="legacy",
            )
            .on_conflict_do_nothing(constraint="uq_protocol_upload_facts_location_date")
            # RETURNING отдаёт строку только при реальной вставке (rowcount
            # у psycopg3 здесь -1 и для подсчёта бесполезен).
            .returning(ProtocolUploadFact.id)
        )
        inserted += 1 if result.first() is not None else 0

    db.commit()
    print(f"Фактов импортировано: {inserted} из {len(facts)}")
    print(f"Поясов обновлено: {tz_updated}")
    if skipped_no_slug:
        print(f"Локации без соответствия ({len(skipped_no_slug)}): {sorted(skipped_no_slug)[:10]}")


if __name__ == "__main__":
    main()
