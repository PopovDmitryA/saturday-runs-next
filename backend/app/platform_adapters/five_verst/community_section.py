"""Раздел 5 вёрст «Старты сообществ» — один адрес на весь код.

Тематические старты («Зелёные 5 км», «День физкультурника. Тула») живут в
разделе /starti-soobshchestv/ и площадками не являются: ни расписания, ни
координат, ни второго старта на той же трассе. У нас они собираются в одну
локацию-серию, а собственное имя старта уходит в заголовок события.

Модуль намеренно без зависимостей: адрес раздела нужен и парсерам, и сборке
внешних ссылок (app/activity_url.py), и синку — раньше он был размазан по трём
файлам, и профильный парсер о нём просто не знал.
"""

from __future__ import annotations

import re

BASE_URL = "https://5verst.ru"

#: Слаг раздела. Он же — external_key нашей локации-серии: адрес
#: https://5verst.ru/starti-soobshchestv/ реальный и ведёт на список стартов.
SECTION_SLUG = "starti-soobshchestv"
#: Имя локации-серии на сайте. Как раздел называется у самих 5 вёрст.
SECTION_NAME = "Старты сообществ"

SECTION_URL_RE = re.compile(rf"/{SECTION_SLUG}/([a-z0-9-]+)/?", re.I)


def section_url() -> str:
    return f"{BASE_URL}/{SECTION_SLUG}/"


def event_url(slug: str) -> str:
    return f"{BASE_URL}/{SECTION_SLUG}/{slug}"


def parse_event_link(href: str | None) -> str | None:
    """Слаг тематического старта из ссылки — или None, если ссылка не про раздел.

    Нужна профильному парсеру: в таблице человека такой старт подписан
    «Зелёные 5 км #1» и ведёт сюда, а не на /{slug}/results/{дата}/, как
    обычная площадка.
    """
    if not href:
        return None
    match = SECTION_URL_RE.search(href)
    if match is None:
        return None
    slug = match.group(1).lower()
    return slug or None


def is_event_url(url: str | None) -> bool:
    return parse_event_link(url) is not None
