"""Официальные слаги площадок parkrun — по названию из протокола профиля.

Слаг раньше выводился прямо из названия (`[^a-z0-9]+` → дефис), и всё не-ASCII
терялось: «Küchenholz» превращался в `k-chenholz`, «Amager Fælled» — в
`amager-f-lled`. От слага строятся ссылка на площадку и сопоставление с мировым
каталогом, поэтому такие площадки оставались без координат и с мёртвой ссылкой
(репорт пользователя через Дмитрия, 11.08.2026).

Здесь лежит снимок официального каталога parkrun (`events_slugs.json`, имя без
разделителей → eventname), собранный `scripts/parkrun_catalog_sync.py
--dump-catalog`. Обновлять снимок вместе с прогоном сверки: тогда и парсер, и
база говорят об одной площадке одним и тем же слагом.

Каталог знает только действующие площадки. Для закрытых (весь российский
parkrun) остаётся прежняя транслитерация — она и в базе такая же.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("data") / "events_slugs.json"


def normalize_event_name(value: str) -> str:
    """«Abbey Park» → «abbeypark»: ключ, одинаковый для названия и слага."""
    return re.sub(r"[^0-9a-zа-яё]+", "", value.strip().lower())


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Снимка нет или он битый — работаем как раньше, на транслитерации.
        return {}


def official_event_slug(name: str) -> str | None:
    """Слаг площадки из официального каталога или None, если её там нет."""
    if not name:
        return None
    return _catalog().get(normalize_event_name(name))
