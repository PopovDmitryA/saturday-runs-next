"""Координаты площадок parkrun мира из data/parkrun_world_coordinates.json.

Зачем: у зарубежных parkrun-локаций в нашей БД координат нет ни у одной (9 из
2471 на 02.08.2026), потому что мы собираем их из профилей участников, а не из
каталога площадок. Без координат «дальность стартов от дома» считала бы поездку
в Лондон нулём километров — то есть теряла ровно самые дальние старты.

Файл выгружается из соседнего репозитория parkrun-monitoring скриптом
scripts/export_parkrun_world_coordinates.py и коммитится, чтобы бэкфилл на
проде не зависел от доступа к тому репо.

Слаги матчатся без дефисов: в каталоге parkrun площадка называется
«aachenerweiher», а в наших локациях — «aachener-weiher». По сырому слагу
совпадает 1028 площадок из 2551, по нормализованному — 2223.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SERVICES_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = SERVICES_ROOT.parent.parent
REPO_ROOT = BACKEND_ROOT.parent
# В контейнере ./data примонтирована как /data (см. docker-compose), локально
# файл лежит в дереве репозитория.
if Path("/data/parkrun_world_coordinates.json").is_file():
    DEFAULT_PATH = Path("/data/parkrun_world_coordinates.json")
else:
    DEFAULT_PATH = REPO_ROOT / "data" / "parkrun_world_coordinates.json"


def normalize_parkrun_slug(value: str | None) -> str:
    """Слаг площадки без дефисов и регистра — общий ключ двух каталогов."""
    if not value:
        return ""
    return value.strip().lower().replace("-", "").replace("_", "")


@lru_cache(maxsize=1)
def parkrun_world_coordinates(path: Path | None = None) -> dict[str, tuple[float, float]]:
    """Нормализованный слаг → (широта, долгота). Пустой словарь, если файла нет."""
    source = path or DEFAULT_PATH
    if not source.is_file():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    coordinates: dict[str, tuple[float, float]] = {}
    for entry in payload.get("events") or []:
        slug = normalize_parkrun_slug(entry.get("slug"))
        latitude = entry.get("latitude")
        longitude = entry.get("longitude")
        if not slug or latitude is None or longitude is None:
            continue
        coordinates[slug] = (float(latitude), float(longitude))
    return coordinates


def coordinates_for_parkrun_slug(slug: str | None) -> tuple[float, float] | None:
    return parkrun_world_coordinates().get(normalize_parkrun_slug(slug))
