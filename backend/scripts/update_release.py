#!/usr/bin/env python3
"""Переписать текст уже заведённого релиза, не создавая новый.

add_release.py умеет только заводить, а номер уникален. Когда после релиза
доехала ещё пачка правок и Дмитрий просит «внести в тот же номер, новый релиз
не выделять» (03.09.2026), текст нужно именно обновить.

Видимость не трогаем: скрытый остаётся скрытым, открытый — открытым. Открывает
релиз Дмитрий сам в /admin/releases (см. docs/release_management.md).

    make prod-run ARGS="scripts/update_release.py --version 3.1.0 --body-file /tmp/notes.md --dry-run"
    CONFIRM_PROD=1 make prod-run ARGS="scripts/update_release.py --version 3.1.0 --body-file /tmp/notes.md"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import SiteRelease
from app.services.release_service import ReleaseError, update_release
from scripts.script_runtime import add_bootstrap_args, apply_bootstrap_args, bootstrap_from_env


def main() -> int:
    bootstrap_from_env()
    parser = argparse.ArgumentParser(description="Update an existing site release")
    add_bootstrap_args(parser)
    parser.add_argument("--version", required=True, help="Версия существующего релиза")
    parser.add_argument("--title", help="Новый заголовок (по умолчанию остаётся прежний)")
    parser.add_argument("--body", help="Новый текст релиза")
    parser.add_argument("--body-file", help="Файл с текстом релиза (вместо --body)")
    parser.add_argument("--dry-run", action="store_true", help="Показать, что изменится")
    args = parser.parse_args()
    apply_bootstrap_args(args)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body
    else:
        parser.error("нужен --body или --body-file")

    with get_session_factory()() as db:
        release = db.query(SiteRelease).filter(SiteRelease.version == args.version).one_or_none()
        if release is None:
            print(f"Релиз {args.version} не найден", file=sys.stderr)
            return 1
        title = args.title or release.title
        if args.dry_run:
            print(
                f"{release.version} «{title}» от {release.released_at}, "
                f"{'опубликован' if release.is_published else 'скрыт'}: "
                f"текст {len(release.body)} → {len(body)} символов"
            )
            return 0
        try:
            update_release(
                db,
                release.id,
                version=release.version,
                title=title,
                body=body,
                released_at=None,
                is_published=release.is_published,
            )
        except ReleaseError as exc:
            print(f"Ошибка: {exc.message}", file=sys.stderr)
            return 1

    visibility = "опубликован" if release.is_published else "скрыт (открыть: /admin/releases)"
    print(f"Релиз {release.version} «{title}» обновлён, {visibility}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
