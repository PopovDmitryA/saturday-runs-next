#!/usr/bin/env python3
"""Разовый сбор описаний площадок (трасса + как добраться) по 5 вёрст и S95.

Регулярный сбор идёт сам: у 5 вёрст описание снимается с уже загружаемой
страницы `/{slug}/course/` в ротации локаций (раз в 4 часа по одной площадке),
у S95 — своим обходом `s95_sync.sync_location_descriptions`. Полный круг 5 вёрст
при такой скорости — больше месяца, поэтому первый заход делаем этим скриптом.

Про темп. Завалить чужой сайт скрипт не может, и дело не только в `--delay`:
каждая загрузка идёт через штатный координатор платформы, тот же, которым
пользуется весь регулярный синк. Он держит межпроцессный Redis-лок и не
выпускает следующий запрос, пока не пройдёт минимальный интервал —
20–30 секунд у 5 вёрст (`five_verst_fetch_*_interval_seconds`) и 15–30 у S95
плюс детект бана с часовым cooldown. То есть даже с `--delay 0` темп будет
не быстрее одной страницы в ~20 секунд, а `--delay` добавляется сверху.

Считать так: одна площадка 5 вёрст — это две страницы (главная и «О трассе»),
то есть ~45–70 секунд; 214 площадок — примерно 3–4 часа. S95 — одна страница
на площадку, 35 площадок ≈ 15 минут. Прерывать безопасно: собранное
перезаписывается только при изменении текста, а `--skip-existing` продолжает
с места обрыва.

    python scripts/backfill_location_descriptions.py --platform five_verst --limit 20
    python scripts/backfill_location_descriptions.py --platform s95
    python scripts/backfill_location_descriptions.py --skip-existing

Посмотреть, где текст реально менялся (а не просто проверялся):

    SELECT l.external_key, d.content_updated_at, d.revision, d.fetched_at
    FROM location_descriptions d JOIN locations l ON l.id = d.location_id
    WHERE d.revision > 0 ORDER BY d.content_updated_at DESC;
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory  # noqa: E402
from app.models import Location, LocationDescription, Platform  # noqa: E402
from app.platform_adapters.canonical import CanonicalLocationDescription  # noqa: E402
from app.platform_adapters.five_verst import bulk_parser  # noqa: E402
from app.s95.errors import S95BanDetected  # noqa: E402
from app.s95.fetch import fetch_page_html as s95_fetch_page_html  # noqa: E402
from app.s95.parsers.location import parse_location_description  # noqa: E402
from app.sync import upsert  # noqa: E402
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env  # noqa: E402

PLATFORMS = ("five_verst", "s95")


@dataclass(frozen=True)
class _Target:
    """Что нужно для загрузки — без живой ORM-сессии за спиной."""

    location_id: UUID
    external_key: str
    source_url: str | None


def _targets(platform_code: str, *, skip_existing: bool, limit: int | None) -> list[_Target]:
    db = get_session_factory()()
    try:
        platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
        if platform is None:
            return []
        query = db.query(Location.id, Location.external_key, Location.source_url).filter(
            Location.platform_id == platform.id,
            Location.is_cancelled.is_(False),
        )
        if skip_existing:
            query = query.outerjoin(
                LocationDescription, LocationDescription.location_id == Location.id
            ).filter(LocationDescription.id.is_(None))
        query = query.order_by(Location.external_key.asc())
        if limit is not None:
            query = query.limit(limit)
        return [_Target(location_id=row[0], external_key=row[1], source_url=row[2]) for row in query.all()]
    finally:
        db.close()


def _five_verst_description(target: _Target) -> CanonicalLocationDescription:
    # Тот же путь, что у регулярного синка: главная (там «Где и когда?») плюс
    # страница «О трассе». Отдельного «облегчённого» пути нет намеренно —
    # иначе бэкфилл и ротация начали бы собирать разное.
    course_url = f"{bulk_parser.BASE_URL}/{target.external_key}/course/"
    data, _html = bulk_parser.fetch_location(target.external_key)
    return data.description or CanonicalLocationDescription(source_url=course_url)


def _s95_description(target: _Target) -> CanonicalLocationDescription:
    url = target.source_url or f"https://s95.ru/events/{target.external_key}"
    html = s95_fetch_page_html(url, reason="location_description")
    return parse_location_description(html, url)


def _save(target: _Target, description: CanonicalLocationDescription) -> bool:
    """Короткая сессия на одну запись.

    Держать одну сессию на весь прогон нельзя: между загрузками страниц скрипт
    ждёт минутами (координатор разводит запросы на 20–30 секунд), и соединение
    успевает получить idle-in-transaction timeout — прогон падал на самом
    безобидном месте, на закрытии сессии.
    """

    db = get_session_factory()()
    try:
        location = db.query(Location).filter(Location.id == target.location_id).one()
        _, changed = upsert.upsert_location_description(db, location, description)
        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Backfill location descriptions (5 вёрст, S95)")
    add_bootstrap_args(parser)
    parser.add_argument("--platform", choices=[*PLATFORMS, "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="Сколько локаций взять на платформу")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Только те локации, у которых описания ещё нет ни одного (продолжить после обрыва)",
    )
    parser.add_argument("--delay", type=float, default=3.0, help="Пауза между страницами, сек")
    parser.add_argument("--dry-run", action="store_true", help="Разобрать и показать, но не писать в БД")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    codes = list(PLATFORMS) if args.platform == "all" else [args.platform]
    summary: dict[str, object] = {}
    for code in codes:
        targets = _targets(code, skip_existing=args.skip_existing, limit=args.limit)
        stats: dict[str, object] = {"total": len(targets), "saved": 0, "changed": 0, "empty": 0, "errors": []}
        errors = cast("list[str]", stats["errors"])
        summary[code] = stats
        for index, target in enumerate(targets, start=1):
            try:
                description = (
                    _five_verst_description(target) if code == "five_verst" else _s95_description(target)
                )
            except S95BanDetected as exc:
                errors.append(f"{target.external_key}: {exc}")
                print(f"[{code}] бан S95 — останавливаюсь", file=sys.stderr)
                break
            except Exception as exc:
                errors.append(f"{target.external_key}: {exc}")
                print(f"[{code}] {target.external_key}: ошибка — {exc}", file=sys.stderr)
                continue

            if description.is_empty():
                stats["empty"] = cast("int", stats["empty"]) + 1
            else:
                stats["saved"] = cast("int", stats["saved"]) + 1

            if not args.dry_run:
                try:
                    if _save(target, description):
                        stats["changed"] = cast("int", stats["changed"]) + 1
                except Exception as exc:
                    errors.append(f"{target.external_key}: запись — {exc}")

            mark = "—" if description.is_empty() else "✓"
            print(f"[{code}] {index}/{len(targets)} {mark} {target.external_key}", file=sys.stderr)
            if args.delay and index < len(targets):
                time.sleep(args.delay)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
