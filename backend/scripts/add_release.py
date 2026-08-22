#!/usr/bin/env python3
"""Внести релиз в таблицу site_releases (шаг деплой-протокола).

Запуск внутри контейнера api (локально или на проде):
    docker compose exec api python scripts/add_release.py --suggest
    docker compose exec api python scripts/add_release.py \
        --version 2.4.0 --title "Страница обновлений" --body-file /tmp/notes.md

Релиз создаётся СКРЫТЫМ (is_published=false): администратор правит текст и
открывает его на сайте через /admin/releases. Протокол присвоения версий —
docs/release_management.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.services.release_service import ReleaseError, create_release, suggest_next_versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suggest", action="store_true", help="Показать кандидатов следующей версии и выйти")
    parser.add_argument("--version", help="Версия релиза: X.Y.Z или X.Y.Z-fixN")
    parser.add_argument("--title", help="Короткий заголовок релиза")
    parser.add_argument("--body", help="Текст релиза (абзацы пустой строкой, пункты строками «- …»)")
    parser.add_argument("--body-file", help="Файл с текстом релиза (вместо --body)")
    parser.add_argument("--date", dest="released_at", help="Дата релиза YYYY-MM-DD (по умолчанию сегодня)")
    parser.add_argument("--publish", action="store_true", help="Сразу показать на сайте (по умолчанию скрыт)")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        if args.suggest:
            suggestions = suggest_next_versions(db)
            print(f"Текущая версия (включая скрытые): {suggestions['current']}")
            for kind, label in (("major", "X"), ("minor", "Y"), ("patch", "Z"), ("fix", "fix")):
                print(f"  {label:>3} → {suggestions[kind]}")
            return 0

        if not args.version or not args.title:
            parser.error("нужны --version и --title (или --suggest)")
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        elif args.body:
            body = args.body
        else:
            parser.error("нужен --body или --body-file")

        released_at = date.fromisoformat(args.released_at) if args.released_at else None
        try:
            release = create_release(
                db,
                version=args.version,
                title=args.title,
                body=body,
                released_at=released_at,
                is_published=args.publish,
            )
        except ReleaseError as exc:
            print(f"Ошибка: {exc.message}", file=sys.stderr)
            return 1

    visibility = "опубликован" if release.is_published else "скрыт (открыть: /admin/releases)"
    print(f"Релиз {release.version} «{release.title}» от {release.released_at} создан, {visibility}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
