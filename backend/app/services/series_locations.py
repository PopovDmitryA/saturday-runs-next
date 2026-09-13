"""Серия стартов — формат, а не площадка. Единственная точка правды про них.

Три строки в `locations` местом не являются:

* «Старты сообществ» 5 вёрст — раздел /starti-soobshchestv/ с разовыми
  тематическими стартами («Зелёные 5 км», «День физкультурника. Тула»);
* «С95 и друзья» и «S95 & Friends» — разъездные серии s95.

У них нет координат, расписания и повторяющейся трассы: каждый старт бежится
в новом месте. Финиши с них системы засчитывают человеку в личный счётчик,
поэтому собирать их надо — без этого наши числа расходятся с источником ровно
на единицу у каждого, кто там бежал.

Правило простое: **считаем в личных итогах человека — не считаем в витринах о
площадках.** Серии нет в туризме («сколько уникальных локаций объехал»), на
карте, в рейтингах по локациям и регионам, в рекордах трасс, в
«Первопроходцах» и в едином протоколе недели. В каталоге локаций она стоит
отдельным блоком «Серии», а не вперемешку с парками.

Все проверки идут через этот модуль, чтобы условие не расползлось по два
десятка запросов и не разъехалось при следующей правке.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Query

from app.models import Location
from app.platform_adapters.five_verst import community_section

#: Локация-серия 5 вёрст: слаг раздела, имя — как раздел зовут сами 5 вёрст.
FIVE_VERST_SERIES_KEY = community_section.SECTION_SLUG
FIVE_VERST_SERIES_NAME = community_section.SECTION_NAME

#: Разъездные серии s95: `s` — российская «С95 и друзья», `bs` — белорусская
#: «S95 & Friends». Список закрытый: остальные строки реестра — площадки.
S95_SERIES_KEYS = frozenset({"s", "bs"})


def is_series(location: Location | None) -> bool:
    """Серия стартов, а не площадка."""
    return bool(location is not None and location.is_series)


def exclude_series(query: Query) -> Query:
    """Отсечь серии из запроса, который перечисляет площадки.

    Ждёт, что `Location` уже участвует в запросе (join или select).
    """
    return query.filter(Location.is_series.is_(False))


def series_key_for_platform(platform_code: str, external_key: str) -> bool:
    """Должна ли строка реестра быть помечена как серия."""
    if platform_code == "s95":
        return external_key in S95_SERIES_KEYS
    if platform_code == "five_verst":
        return external_key == FIVE_VERST_SERIES_KEY
    return False


# «С95 и друзья #11» — служебный заголовок, собранный из имени локации и номера
# (см. upsert._profile_event_title). Имени старта в нём нет.
_NUMBERED_TITLE_RE = re.compile(r"^(?P<name>.+?)\s+#\d+$")


def start_title(location: Location, event_title: str | None) -> str | None:
    """Собственное имя старта внутри серии — или None, если его нет.

    У 5 вёрст оно есть: «Зелёные 5 км», «День физкультурника. Тула». У s95 —
    нет: реестр отдаёт все выезды под одним именем, и заголовок события
    складывается из имени локации и номера. Показывать «С95 и друзья #11»
    второй строкой под «С95 и друзья» — шум, поэтому такие заголовки отсеиваем.
    """
    if not is_series(location):
        return None
    title = (event_title or "").strip()
    if not title:
        return None
    match = _NUMBERED_TITLE_RE.match(title)
    bare = match.group("name").strip() if match else title
    if bare.casefold() == (location.name or "").strip().casefold():
        return None
    return title
