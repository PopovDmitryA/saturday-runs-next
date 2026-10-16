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

Живой каталог знает только действующие площадки, поэтому снимок только
дополняется: закрытые не пропадают. Российские названия (110 площадок, страна
ушла из parkrun в 2022-м) внесены 16.10.2026 из архивных снимков каталога,
которые хранит parkrun-monitoring. До этого «Gorky Park» шёл на транслитерации
`gorky-park`, а у parkrun слаг `gorkypark` — так на сайте завелись 55 пар
локаций-двойников. Слаг parkrun пишет строчными буквами без разделителей,
дефис бывает только в окончании `-juniors`. Транслитерация осталась для того,
чего нет и в архиве.

Рядом — `events_archive.json` (слаг → код страны и точка старта, его собирает
`scripts/backfill_parkrun_from_archive.py --rebuild-index`). По нему импорт
профиля сразу ставит новой площадке координаты, страну и ссылку на домен её
страны (`catalog_event`).

**Снимки отстают сами собой**: площадка, открывшаяся после последнего
обновления, в них не попадает и рождается на транслитерации, без страны и
координат — так 10.10.2026 появилась Okura kaigan koen (`okura-kaigan-koen`
вместо `okurakaigankoen`), и челлендж «Страны» не засчитал Японию. Такие
локации ежедневная сводка админу перечисляет отдельным блоком. Обновить оба
снимка (events.json — с Мака, `curl -s --compressed -o events.json
https://images.parkrun.com/events.json`, либо из копии, которую на проде трижды
в неделю собирает parkrun-monitoring; с домашнего сервера в parkrun не ходить):

    python backend/scripts/parkrun_catalog_sync.py --events-json events.json --dump-catalog
    python backend/scripts/backfill_parkrun_from_archive.py --rebuild-index --no-fetch --events-json events.json

После выката — на проде по снимкам из образа, без сети:
`unify_parkrun_location_keys.py --plan-only`, затем `--apply` (ключи локаций,
протоколов и результатов — к слагам parkrun, двойники сливаются; порядок
запуска — в докстринге скрипта), `parkrun_catalog_sync.py --no-fetch`
(координаты и ссылки) и `backfill_parkrun_from_archive.py --no-fetch --apply`
(страна). `--rename-keys` у сверки делает то же переименование одной длинной
транзакцией, его — только при остановленной очереди профилей.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("data") / "events_slugs.json"
ARCHIVE_PATH = Path(__file__).with_name("data") / "events_archive.json"

# countrycode каталога → домен страны: ссылки parkrun живут на домене своей
# страны, `parkrun.org.uk/kuechenholz/` отдаёт 404. Коды и домены — из
# events.json (`countries`); таблица кодов → названий стран живёт в
# `scripts/backfill_parkrun_from_archive.py`. Франции (31) и России (79) здесь
# нет: страны ушли из parkrun, их доменов больше не существует, и ссылку на
# такую площадку импорт не строит — остаётся прежняя.
COUNTRY_DOMAINS: dict[int, str] = {
    3: "www.parkrun.com.au",
    4: "www.parkrun.co.at",
    14: "www.parkrun.ca",
    23: "www.parkrun.dk",
    30: "www.parkrun.fi",
    32: "www.parkrun.com.de",
    42: "www.parkrun.ie",
    44: "www.parkrun.it",
    46: "www.parkrun.jp",
    54: "www.parkrun.lt",
    57: "www.parkrun.my",
    64: "www.parkrun.co.nl",
    65: "www.parkrun.co.nz",
    67: "www.parkrun.no",
    74: "www.parkrun.pl",
    82: "www.parkrun.sg",
    85: "www.parkrun.co.za",
    88: "www.parkrun.se",
    97: "www.parkrun.org.uk",
    98: "www.parkrun.us",
}


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


@dataclass(frozen=True)
class CatalogEvent:
    """Площадка из снимка каталога: официальный слаг, страна и точка старта."""

    slug: str
    country_code: int
    latitude: float
    longitude: float

    @property
    def domain(self) -> str | None:
        return COUNTRY_DOMAINS.get(self.country_code)

    @property
    def page_url(self) -> str | None:
        return f"https://{self.domain}/{self.slug}/" if self.domain else None

    def results_url(self, event_number: int | None) -> str | None:
        if not self.domain:
            return None
        return f"https://{self.domain}/{self.slug}/results/{event_number or ''}/"


@lru_cache(maxsize=1)
def _official_slugs() -> frozenset[str]:
    return frozenset(_catalog().values())


@lru_cache(maxsize=1)
def _archive_events() -> dict[str, list[float]]:
    try:
        return json.loads(ARCHIVE_PATH.read_text(encoding="utf-8")).get("events") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def catalog_event(slug: str | None) -> CatalogEvent | None:
    """Площадка по слагу, который вернул парсер, — только официальному.

    Транслитерацию сюда не пускаем, даже если без дефисов она совпала с
    площадкой архива: ссылка строится от слага, и `okura-kaigan-koen` на домене
    Японии всё равно 404. У российских площадок домена нет (см. COUNTRY_DOMAINS):
    точка из архива есть, ссылки нет.
    """
    if not slug or slug not in _official_slugs():
        return None
    row = _archive_events().get(normalize_event_name(slug))
    if not row or len(row) < 3:
        return None
    code, latitude, longitude = row[:3]
    return CatalogEvent(slug=slug, country_code=int(code), latitude=float(latitude), longitude=float(longitude))
