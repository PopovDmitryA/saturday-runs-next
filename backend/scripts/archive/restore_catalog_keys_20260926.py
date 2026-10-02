#!/usr/bin/env python3
"""Вернуть ключи «catalog:<id>», осиротевшие после перезаливки каталога 26.09.2026.

26.09.2026 в 00:41 импорт каталога локаций (DELETE + INSERT) выдал всем 121
узлу новые id. Ручные гранты кабинета организатора, выбранные руками
домашние локации, оценки локаций и гео-пинги остались со старыми ключами и
перестали работать — первой пожаловалась Ольга Арисова (Пермь Балатово).

Старый id → новый — по связкам (система, slug) каталога из ночного дампа
25.09.2026 23:30, снятого за час до перезаливки:

    pg_restore -a -t location_catalog_links -f - \\
        /var/backups/pg/saturday_runs_lk_2026-09-25_2330.dump > old_links.sql

У оценок локаций есть location_id — их ключ пересчитывается из него тем же
canonical_identity_key, что пишет rating_service; так чинятся и оценки,
осиротевшие ещё при прошлых перезаливках (до дампа).

Запуск (по умолчанию — сухой прогон):

    python scripts/archive/restore_catalog_keys_20260926.py --old-links old_links.sql [--apply]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_ROOT) not in sys.path and (BACKEND_ROOT / "app").is_dir():
    sys.path.insert(0, str(BACKEND_ROOT))


# Снимок CATALOG_KEY_REFERENCES (app/services/location_catalog_service.py) на
# 29.09.2026: скрипт гонялся на проде до выката этой константы.
CATALOG_KEY_PREFIX = "catalog:"
CATALOG_KEY_REFERENCES: tuple[tuple[str, str], ...] = (
    ("location_organizer_access", "location_key"),
    ("users", "home_location_key"),
    ("location_ratings", "location_key"),
    ("user_geo_pings", "nearest_identity_key"),
    ("user_geo_pings", "home_identity_key"),
)


def read_old_links(path: Path) -> list[tuple[str, str, str]]:
    """(catalog_id, platform_id, external_key) из COPY-блока pg_restore."""
    rows: list[tuple[str, str, str]] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("COPY public.location_catalog_links "):
            inside = True
            continue
        if inside and line == "\\.":
            break
        if inside:
            _id, catalog_id, platform_id, external_key, *_rest = line.split("\t")
            rows.append((catalog_id, platform_id, external_key))
    if not rows:
        raise SystemExit(f"{path}: нет COPY-блока location_catalog_links")
    return rows


def build_mapping(db, old_links: list[tuple[str, str, str]]) -> dict[str, str]:
    from sqlalchemy import text

    new_by_link = {
        (platform_id, external_key): catalog_id
        for catalog_id, platform_id, external_key in db.execute(
            text("SELECT catalog_id::text, platform_id::text, external_key FROM location_catalog_links")
        )
    }
    votes: dict[str, Counter] = defaultdict(Counter)
    for old_catalog_id, platform_id, external_key in old_links:
        new_catalog_id = new_by_link.get((platform_id, external_key))
        if new_catalog_id is not None:
            votes[old_catalog_id][new_catalog_id] += 1
    mapping: dict[str, str] = {}
    for old_catalog_id, counter in votes.items():
        if len(counter) > 1:
            print(f"  неоднозначно, пропущен: {old_catalog_id} → {dict(counter)}")
            continue
        mapping[old_catalog_id] = next(iter(counter))
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old-links", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.db.session import get_session_factory
    from app.models import Location, LocationOrganizerAccess, LocationRating, Platform
    from app.services.location_catalog_service import LocationCatalogIndex
    from app.services.organizer_access_service import invalidate_organizer_locations_cache

    db = get_session_factory()()
    try:
        live_ids = {str(row[0]) for row in db.execute(text("SELECT id FROM location_catalog"))}

        def orphan(key: str | None) -> bool:
            return bool(key) and key.startswith(CATALOG_KEY_PREFIX) and key[len(CATALOG_KEY_PREFIX) :] not in live_ids

        mapping = build_mapping(db, read_old_links(args.old_links))
        print(f"Соответствий старый → новый: {len(mapping)}")

        def remap(key: str) -> str | None:
            new_id = mapping.get(key[len(CATALOG_KEY_PREFIX) :])
            return f"{CATALOG_KEY_PREFIX}{new_id}" if new_id else None

        # 1. Оценки локаций — из location_id, это надёжнее любого сопоставления.
        catalog_index = LocationCatalogIndex(db)
        ratings_fixed = ratings_left = 0
        for rating, location, platform_code in (
            db.query(LocationRating, Location, Platform.code)
            .join(Location, LocationRating.location_id == Location.id)
            .join(Platform, Location.platform_id == Platform.id)
        ):
            if not orphan(rating.location_key):
                continue
            fresh = catalog_index.canonical_identity_key(location, platform_code)
            if orphan(fresh):
                ratings_left += 1
                continue
            rating.location_key = fresh
            ratings_fixed += 1
        print(f"location_ratings.location_key: исправлено {ratings_fixed}, осталось {ratings_left}")

        # 2. Гранты кабинета организатора. Если админ уже перевыдал грант на ту
        # же локацию, оставляем исходный (с заметкой и датой выдачи), дубль снимаем.
        affected_users = set()
        grants = db.query(LocationOrganizerAccess).all()
        by_user_key = {(g.user_id, g.location_key): g for g in grants}
        for grant in grants:
            if not orphan(grant.location_key):
                continue
            target = remap(grant.location_key)
            if target is None:
                print(f"  грант {grant.id}: ключ {grant.location_key} не сопоставлен")
                continue
            duplicate = by_user_key.get((grant.user_id, target))
            if duplicate is not None:
                print(f"  грант {grant.id}: снят дубль {duplicate.id}, выданный повторно {duplicate.created_at:%d.%m.%Y}")
                db.delete(duplicate)
                db.flush()
            print(f"  грант {grant.id} (user {grant.user_id}): {grant.location_key} → {target}")
            grant.location_key = target
            affected_users.add(grant.user_id)

        # 3. Остальные колонки — по сопоставлению из дампа.
        for table, column in CATALOG_KEY_REFERENCES:
            if (table, column) in {("location_ratings", "location_key"), ("location_organizer_access", "location_key")}:
                continue
            rows = db.execute(
                text(f'SELECT id, "{column}" FROM "{table}" WHERE "{column}" LIKE :prefix'),
                {"prefix": f"{CATALOG_KEY_PREFIX}%"},
            ).all()
            fixed = left = 0
            for row_id, key in rows:
                if not orphan(key):
                    continue
                target = remap(key)
                if target is None:
                    left += 1
                    continue
                db.execute(
                    text(f'UPDATE "{table}" SET "{column}" = :key WHERE id = :id'),
                    {"key": target, "id": row_id},
                )
                fixed += 1
            print(f"{table}.{column}: исправлено {fixed}, осталось {left}")

        if args.apply:
            db.commit()
            for user_id in affected_users:
                invalidate_organizer_locations_cache(user_id)
            print("Записано.")
        else:
            db.rollback()
            print("Сухой прогон — ничего не записано (--apply для записи).")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
