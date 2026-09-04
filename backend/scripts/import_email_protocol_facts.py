"""Импорт моментов выгрузки протокола из почтовых уведомлений 5 вёрст.

Зачем: легаси-коллектор `date_load_protocol.py` собирал выгрузки только по
площадкам из `general_link_all_location`, а новых площадок 2026 года там нет —
у Видного и ещё пяти точек история моментов публикации пустая (см. память
«Скорость протоколов»). Письма из почтового ящика штаба закрывают эту дыру:
время письма — момент отправки уведомления системой, то есть публикации.

Вход — CSV, выгруженный из ящика: разделитель `;`, колонки `№;Дата;Время`,
дата `ГГГГ.ММ.ДД`, время МОСКОВСКОЕ наивное (`ЧЧ:ММ:СС`).

Пишет protocol_upload_facts (source='email', first_seen_at в UTC = МСК − 3ч,
first_seen_confirmed=true — время точное по определению). Даты, которым нет
события у этой площадки, пропускаются с предупреждением: молча заводить факт
на несуществующий старт нельзя.

По умолчанию — сухой прогон, показывает, что будет сделано. Запись только
с `--apply`. Существующий факт перетирается только с `--overwrite`
(письмо точнее наблюдения 'site'/'db', но решение осознанное).

Запуск (изнутри контейнера api):
    python scripts/import_email_protocol_facts.py \
        --slug vysota --csv /tmp/5v_letters_dates.csv [--apply] [--overwrite]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_session_factory  # noqa: E402
from app.models import Event, Location, Platform, ProtocolUploadFact  # noqa: E402

MSK = timezone(timedelta(hours=3))
SOURCE = "email"


def _read_csv(path: str) -> list[tuple[datetime, datetime]]:
    """Читает выгрузку → [(дата события, момент письма в UTC)]."""
    rows: list[tuple[datetime, datetime]] = []
    # utf-8-sig: выгрузка из почты приходит с BOM.
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            raw_date = (row.get("Дата") or "").strip()
            raw_time = (row.get("Время") or "").strip()
            if not raw_date or not raw_time:
                continue
            local = datetime.strptime(f"{raw_date} {raw_time}", "%Y.%m.%d %H:%M:%S")
            rows.append((local.date(), local.replace(tzinfo=MSK).astimezone(timezone.utc)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True, help="external_key площадки 5 вёрст, например vysota")
    parser.add_argument("--csv", required=True, help="путь к выгрузке из почтового ящика")
    parser.add_argument("--apply", action="store_true", help="писать в базу (иначе сухой прогон)")
    parser.add_argument("--overwrite", action="store_true", help="перетирать факты других источников")
    parser.add_argument(
        "--tz-offset-moscow",
        type=int,
        default=None,
        help="заодно проставить площадке смещение от Москвы в часах (для Видного 0)",
    )
    args = parser.parse_args()

    rows = _read_csv(args.csv)
    if not rows:
        raise SystemExit("В выгрузке нет строк")

    session_factory = get_session_factory()
    with session_factory() as db:
        location = db.execute(
            sa.select(Location)
            .join(Platform, Platform.id == Location.platform_id)
            .where(Platform.code == "five_verst", Location.external_key == args.slug)
        ).scalar_one_or_none()
        if location is None:
            raise SystemExit(f"Площадка 5 вёрст со слагом {args.slug!r} не найдена")

        if args.tz_offset_moscow is not None and location.tz_offset_moscow != args.tz_offset_moscow:
            print(
                f"  tz: {location.tz_offset_moscow} → {args.tz_offset_moscow} "
                f"(смещение от Москвы, часов)"
            )
            if args.apply:
                location.tz_offset_moscow = args.tz_offset_moscow

        event_dates = set(
            db.execute(
                sa.select(Event.event_date).where(Event.location_id == location.id)
            ).scalars()
        )
        existing = {
            fact.event_date: fact
            for fact in db.execute(
                sa.select(ProtocolUploadFact).where(ProtocolUploadFact.location_id == location.id)
            ).scalars()
        }

        inserted = updated = skipped_existing = 0
        no_event: list[str] = []
        for event_date, first_seen in rows:
            if event_date not in event_dates:
                no_event.append(event_date.isoformat())
                continue

            fact = existing.get(event_date)
            if fact is not None:
                was = fact.first_seen_at.astimezone(MSK).strftime("%d.%m %H:%M:%S")
                now = first_seen.astimezone(MSK).strftime("%H:%M:%S")
                if not args.overwrite:
                    print(f"  = {event_date}: уже есть факт ({fact.source}, {was}) — пропуск")
                    skipped_existing += 1
                    continue
                print(f"  ~ {event_date}: {fact.source} {was} → email {now}")
                if args.apply:
                    fact.first_seen_at = first_seen
                    fact.first_seen_confirmed = True
                    fact.source = SOURCE
                updated += 1
                continue

            print(f"  + {event_date}: {first_seen.astimezone(MSK).strftime('%H:%M:%S')} МСК")
            if args.apply:
                db.execute(
                    pg_insert(ProtocolUploadFact)
                    .values(
                        location_id=location.id,
                        event_date=event_date,
                        first_seen_at=first_seen,
                        first_seen_confirmed=True,
                        source=SOURCE,
                    )
                    .on_conflict_do_nothing(constraint="uq_protocol_upload_facts_location_date")
                )
            inserted += 1

        if args.apply:
            db.commit()

        print(
            f"\n{location.name} ({args.slug}): строк в выгрузке {len(rows)}, "
            f"новых {inserted}, обновлено {updated}, пропущено существующих {skipped_existing}"
        )
        if no_event:
            print(f"Нет старта в базе на даты ({len(no_event)}): {', '.join(no_event)}")
        if not args.apply:
            print("Сухой прогон — ничего не записано. Повторить с --apply.")


if __name__ == "__main__":
    main()
