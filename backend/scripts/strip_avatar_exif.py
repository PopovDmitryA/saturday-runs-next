#!/usr/bin/env python3
"""Разовая уборка: снять EXIF с аватарок, залитых до миграции 092.

Аудит 09.2026 (SEC-04): оригинал аватарки клали в публичный бакет байт-в-байт,
вместе с координатами съёмки, моделью телефона и временем. С 092 новые загрузки
чистятся, а файлы, лежащие в бакете с прошлого времени, остались как есть.

Скрипт проходит по пользователям с загруженной аватаркой: качает файл по
публичной ссылке, забирает теги в приватную колонку users.avatar_exif и кладёт
обратно ту же картинку без метаданных. Ключ не меняется, поэтому ссылка у
человека прежняя и внешне ничего не происходит.

    docker compose exec -T api python scripts/strip_avatar_exif.py          # разбор
    docker compose exec -T api python scripts/strip_avatar_exif.py --apply  # с записью
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.image_processing import (  # noqa: E402
    ImageProcessingError,
    extract_image_metadata,
    has_exif,
    strip_image_metadata,
)
from app.core.media_storage import MediaStorageError, content_type_for, get_media_storage  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models import User  # noqa: E402

DOWNLOAD_TIMEOUT_SEC = 30.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать изменения (без флага — только отчёт)")
    parser.add_argument("--limit", type=int, help="обработать не больше N аватарок")
    args = parser.parse_args()

    storage = get_media_storage()
    session_factory = get_session_factory()
    cleaned = skipped = failed = 0

    with session_factory() as db, httpx.Client(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
        users = db.query(User).filter(User.avatar_full_path.isnot(None)).order_by(User.serial_id).all()
        print(f"аватарок-файлов: {len(users)}")

        for user in (users[: args.limit] if args.limit else users):
            key = user.avatar_full_path
            label = f"№{user.serial_id} {user.display_name or ''}".strip()
            try:
                url = storage.public_url(key)
                raw = client.get(url).content if url.startswith("http") else Path(url).read_bytes()
            except (httpx.HTTPError, OSError, MediaStorageError) as exc:
                print(f"  x {label}: файл не скачался - {type(exc).__name__}")
                failed += 1
                continue

            if not has_exif(raw):
                skipped += 1
                continue

            metadata = extract_image_metadata(raw)
            gps = (metadata or {}).get("gps") or {}
            marks = []
            if gps.get("lat") is not None:
                marks.append("координаты")
            if metadata.get("model"):
                marks.append(str(metadata["model"]))
            print(f"  * {label}: EXIF есть{' (' + ', '.join(marks) + ')' if marks else ''}")

            if not args.apply:
                cleaned += 1
                continue

            try:
                clean_bytes, _fmt = strip_image_metadata(raw)
            except ImageProcessingError as exc:
                print(f"    x не удалось перекодировать: {exc}")
                failed += 1
                continue
            try:
                storage.put(key, clean_bytes, content_type_for(key))
            except MediaStorageError as exc:
                print(f"    x не удалось положить обратно: {exc}")
                failed += 1
                continue

            # Теги сохраняем у себя: наружу колонка не отдаётся.
            user.avatar_exif = metadata or None
            db.flush()
            cleaned += 1

        if args.apply:
            db.commit()

    verb = "очищено" if args.apply else "нашлось с EXIF"
    print(f"\n{verb}: {cleaned} | без EXIF: {skipped} | ошибок: {failed}")
    if not args.apply and cleaned:
        print("Это был разбор. Чтобы записать, запустите с --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
