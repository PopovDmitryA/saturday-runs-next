"""Публичный поиск по сайту (шапка/боковая панель): локации и люди.

Доступен анониму, поэтому правила строже, чем у поиска в онбординге
(participant_search_service), на котором он построен:

- Ссылку получает только участник, привязанный к пользователю сайта с
  открытым профилем, — и ведёт она на НАШ профиль /users/{хендл}. Внешних
  адресов профилей в системах (5 вёрст, С95, RunPark, parkrun) в ответе нет
  вовсе: согласия на то, чтобы мы водили к чужим профилям, у людей нет.
- Закрытый профиль (profile_private) выглядит как обычный участник: ни
  ссылки, ни намёка на аккаунт (решение Дмитрия, 23.09.2026).
- Поиска по штрихкоду/номеру участника здесь нет, только по имени: иначе
  любой, у кого в руках чужой QR, узнавал бы по нему имя человека.
- Если по запросу не нашлось ничего, пробуем тот же запрос в другой раскладке
  («cjrjkmybrb» → «сокольники»): так чаще всего и выглядит «пустой» запрос.
  Не помогло и это — показываем похожие по написанию локации
  («сокольнеки» → «Сокольники»).

Как ищут на самом деле (разбор ревью 25.09.2026): с «ё» и без, по городу и
его сокращению («спб», «питер», «мск», «Подмосковье»), со словом «парк»,
по короткой фамилии («Ким», «Лев»). Всё это разобрано ниже.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar
from uuid import UUID

import redis
from sqlalchemy import and_, any_, bindparam, func, literal, or_, select, true, union, union_all
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import (
    Event,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.co_runners_service import _UNKNOWN_PARTICIPANT_NAMES, _is_unknown_participant_name
from app.services.location_catalog_service import catalog_ids_for_locations
from app.services.participant_search_service import (
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    TopLocation,
    apply_name_filters,
    folded_display_name,
    is_statement_timeout,
    load_run_stats,
    load_volunteering_counts,
    name_candidate_pool,
    name_tier_conditions,
    normalize_query_text,
    prepare_search_transaction,
    significant_words,
    whole_word_filter,
    word_start_all,
    word_start_rank,
    word_start_regex,
)
from app.services.platform_titles import PLATFORM_ORDER
from app.services.user_display_name_service import STYLE_INITIAL, prettify_name

logger = logging.getLogger(__name__)

LOCATION_MIN_QUERY_LENGTH = 2
PEOPLE_MIN_QUERY_LENGTH = MIN_QUERY_LENGTH
# Одно слово из двух букв («Ли», «Ан», «Юн») — тоже люди, но только целым словом.
PEOPLE_SHORT_QUERY_LENGTH = 2
LOCATION_LIMIT = 8
SIMILAR_LOCATION_LIMIT = 5
PEOPLE_LIMIT = 20
# Кандидатов по имени берём с запасом и уже упорядоченными в SQL (совпадения
# с начала слова — первыми, см. word_start_rank), а дальше ранжируем по
# пробежкам в Python. 500 id по GIN-индексу и один group by по их пробежкам —
# десятки миллисекунд.
PEOPLE_CANDIDATE_LIMIT = 500
# Поиск «имя + место» идёт одним из двух путей — по размеру места (замеры на
# копии прода, 27.09.2026; ревью SKEP-1).
#
# От места — для места до стольких финишей (finishers_total каталога): все,
# кто там бегал или волонтёрил, ∩ имя. Точно: проверяется каждый тёзка, и
# стоимость не зависит от того, насколько имя частое. Собрать людей места —
# это все протоколы его стартов: Мытищи (13 тысяч финишей) — 30–100 мс,
# Кузьминки (51 тысяча) — 55–125 мс, Тюмень (98 тысяч) — уже до 200 мс.
PEOPLE_FROM_PLACE_MAX_FINISHERS = 60_000
# От имени — для большого места (Москва, Петербург, страна): тёзки по ярусам
# имени (целиком → с начала слова → в середине), у каждого — «бегал ли здесь»
# по индексу его пробежек, 25–30 мкс на человека; хватило кандидатов —
# остановились. Предел — сколько тёзок проверяем всего: он покрывает любое
# частое женское имя целиком («Анна» целым словом — 5551 человек, «Елена» —
# 5801) и ограничивает запрос ~0,2 с при любом вводе. Упёрлись в предел —
# выдача помечается «есть ещё» (people_truncated), и нижние ярусы не читаем:
# иначе «Сюзанна» вставала бы на место непроверенных Анн.
PEOPLE_PLACE_SCAN_LIMIT = 6000
# Сколько времени весь поиск людей может занять в одном запросе — вместе с
# повтором в другой раскладке и попытками «имя + место». Исчерпали — новых
# попыток не начинаем, локации отдаём как есть.
PEOPLE_TIME_BUDGET_SECONDS = 1.5

# Раскладки qwerty ↔ йцукен: клавиша за клавишей, включая знаки, на которых
# стоят русские буквы (х ъ ж э б ю ё).
_LATIN_KEYS = "`qwertyuiop[]asdfghjkl;'zxcvbnm,."
_CYRILLIC_KEYS = "ёйцукенгшщзхъфывапролджэячсмитьбю"
_LATIN_TO_CYRILLIC = dict(zip(_LATIN_KEYS, _CYRILLIC_KEYS, strict=True))
# Заглавные — только для букв: у знаков верхний регистр тот же символ, и
# «[».upper() затёр бы строчную «х» заглавной.
_LATIN_TO_CYRILLIC.update(
    {lat.upper(): cyr.upper() for lat, cyr in zip(_LATIN_KEYS, _CYRILLIC_KEYS, strict=True) if lat.isalpha()}
)
# Знаки с Shift в латинской раскладке тоже дают русские буквы: «{» → «Х» и т.д.
_LATIN_TO_CYRILLIC.update({"~": "Ё", "{": "Х", "}": "Ъ", ":": "Ж", '"': "Э", "<": "Б", ">": "Ю"})
_CYRILLIC_TO_LATIN = {cyr: lat for lat, cyr in _LATIN_TO_CYRILLIC.items()}


def switch_keyboard_layout(text: str) -> str:
    """Перевести набранное «не в той раскладке»: латиница ↔ кириллица.

    Направление выбираем по большинству букв: «cjrjkmybrb» → «сокольники»,
    «ыщлщдтшлш» → «sokolniki». Смешанный ввод («cjrjkmybrb парк») переводим
    целиком в сторону большинства — второй язык в одном запросе почти
    всегда та же ошибка раскладки.
    """
    latin = sum(1 for char in text if char.isascii() and char.isalpha())
    cyrillic = sum(1 for char in text if char in _CYRILLIC_TO_LATIN and char.isalpha())
    mapping = _LATIN_TO_CYRILLIC if latin >= cyrillic else _CYRILLIC_TO_LATIN
    return "".join(mapping.get(char, char) for char in text)


def normalize_log_query(raw: str | None) -> str:
    """Запрос для журнала: без управляющих символов и лишних пробелов, в нижнем регистре, до 100 знаков.

    Нижний регистр — чтобы «Сокольники» и «сокольники» сложились в одну строку
    топа запросов. Управляющие символы убирает normalize_query_text.
    """
    return normalize_query_text(raw or "").lower()[:MAX_QUERY_LENGTH]


# Заглушка RunPark для бегуна без карточки — «Неизвестный бегун», больше
# десяти тысяч строк. Общий _is_unknown_participant_name её не знает (там
# только «неизвестный» 5 вёрст/С95), а в публичном поиске она забивала бы
# выдачу по «неизвестный» целиком.
_EXTRA_PLACEHOLDER_NAMES = frozenset({"неизвестный бегун"})
_PLACEHOLDER_NAMES = frozenset(_UNKNOWN_PARTICIPANT_NAMES) | _EXTRA_PLACEHOLDER_NAMES


def _is_placeholder_name(name: str | None) -> bool:
    return _is_unknown_participant_name(name) or (name or "").strip().casefold() in _EXTRA_PLACEHOLDER_NAMES


def _fold(text: str | None) -> str:
    """Сравнение без регистра и без различия «е»/«ё» — в названиях они вперемешку."""
    return (text or "").casefold().replace("ё", "е")


# Подчёркивание — тоже разделитель: для \w оно буква, и «___ нижний
# новгород» не узнавался как место из-за «слова» «___».
_TOKEN_SPLIT_RE = re.compile(r"[\W_]+")


def _tokens(text: str | None) -> list[str]:
    """Слова текста для сравнения: «Санкт-Петербург» → «санкт», «петербург»."""
    return [token for token in _TOKEN_SPLIT_RE.split(_fold(text)) if token]


def _stem(word: str) -> str:
    """Грубая основа слова — как stem() в поиске страниц на фронте.

    «сокольниках» → «сокольник», «горький» → «горьк» (и находит «Парк
    Горького»), «москве» → «москв». Настоящий стеммер тут избыточен: слова
    сравниваются только с началом слов названий, ложных совпадений мало.
    """
    if len(word) >= 7:
        return word[:-2]
    if len(word) >= 5:
        return word[:-1]
    return word


# ---------------------------------------------------------------------------
# Локации
# ---------------------------------------------------------------------------

# Слова, которые ничего не говорят о том, КАКАЯ это локация: «бутово парк»
# искал слово «парк» в названии «Бутово» и не находил ничего. Выбрасываем их,
# если после этого в запросе что-то осталось.
_LOCATION_NOISE_WORDS = frozenset(
    {
        "парк",
        "парка",
        "парке",
        "парки",
        "сквер",
        "сквера",
        "сквере",
        "лесопарк",
        "лесопарка",
        "лесопарке",
        "локация",
        "локации",
        "забег",
        "забеги",
        "пробежка",
        "пробежки",
        "город",
        "г",
        "область",
        "обл",
        "край",
        "республика",
        "в",
        "во",
        "на",
        "у",
        "где",
    }
)

# Сокращения и разговорные названия мест → как место записано в каталоге
# (город, регион или страна; уже свёрнуто _fold). Словарь живёт в коде и
# пополняется по журналу поиска (/admin/search, «ничего не нашлось»).
# «Нижний» сюда не входит нарочно: есть ещё Нижний Тагил, а начало слова и
# так находит оба города.
_PLACE_ALIASES: dict[str, str] = {
    "спб": "санкт-петербург",
    "питер": "санкт-петербург",
    "петербург": "санкт-петербург",
    "ленинград": "санкт-петербург",
    "spb": "санкт-петербург",
    "мск": "москва",
    "msk": "москва",
    "moscow": "москва",
    "екб": "екатеринбург",
    "екат": "екатеринбург",
    "ебург": "екатеринбург",
    "нн": "нижний новгород",
    "нск": "новосибирск",
    "новосиб": "новосибирск",
    "подмосковье": "московская",
    "мособласть": "московская",
    "ленобласть": "ленинградская",
    "белоруссия": "беларусь",
    "белорусь": "беларусь",
    "рб": "беларусь",
    "рф": "россия",
    "ростов": "ростов-на-дону",
    "ростове": "ростов-на-дону",
}
# Сокращения, которые не бывают ни именем, ни фамилией: по ним людей не ищем
# («мск» находил Томских и Сумских). «Питер» — бывает, его не трогаем.
_PLACE_ABBREVIATIONS = frozenset({"спб", "spb", "мск", "msk", "екб", "екат", "ебург", "нн", "нск", "рб", "рф"})


def _region_label(region: str) -> str:
    """«Московская» → «Московская область»: в каталоге регион записан коротко."""
    folded = _fold(region)
    if folded.endswith(("ская", "цкая")):
        return f"{region} область"
    if folded.endswith(("ский", "цкий")):
        return f"{region} край"
    return region


@dataclass
class _LocationDoc:
    entry: dict[str, Any]
    name: str
    name_tokens: list[str]
    # Свёрнутое значение города/региона/страны → как оно записано в каталоге.
    places: dict[str, str]
    place_kinds: dict[str, str]
    place_tokens: list[str]
    slug_tokens: list[str]


# Разобранный каталог в памяти процесса: (строка из Redis, её записи) и
# (записи, документы поиска). Разбор JSON каталога (250 КБ) и сборка
# документов — 10–15 мс процессора на каждый запрос поиска, а поиск шлётся на
# каждую паузу в наборе; залп из 15 запросов разом занимал интерпретатор
# на четверть секунды, и соседние запросы сайта ждали (замер 26.09.2026).
# Ключ — сама строка кэша: каталог пересобрали — строка другая, разбираем
# заново. Кортеж меняется одним присваиванием, потокам хватает.
_entries_memo: tuple[str, list[dict[str, Any]]] | None = None
_docs_memo: tuple[list[dict[str, Any]], list[_LocationDoc]] | None = None


def _location_entries(db: Session) -> list[dict[str, Any]]:
    """Идентичности каталога локаций — ровно те, у которых есть /locations/{slug}.

    Берём готовый каталог (build_locations_index): он уже склеивает одну
    площадку в нескольких системах в одну строку с одним слагом и лежит в
    Redis (прогревается задачей locations_warm). Собственный запрос по
    locations вернул бы по строке на систему и слаги, которые никуда не ведут.

    Только из кэша: каталога в Redis нет — локаций в выдаче нет, люди ищутся
    как обычно. Пересчитывать каталог прямо в запросе нельзя: это 6 с на стенде
    и 5–15 с на проде (аудит 13.09.2026), а поиск шлёт запрос на каждую паузу
    в наборе — каждый, кто печатает в окне поиска после сброса кэша, запускал
    бы свой пересчёт и держал соединение из пула (ревью, SRCH-COLD-2). Сброс
    кэша сам ставит прогрев в очередь (flush_location_catalog_caches), а каталог
    /locations при пустом кэше пересчитывает и кладёт обратно первый же его
    посетитель, так что окно без локаций — меньше минуты. То же правило у
    home_location_brief.
    """
    global _entries_memo
    from app.services.location_page_service import LOCATIONS_INDEX_CACHE_KEY

    try:
        raw = get_redis_client().get(LOCATIONS_INDEX_CACHE_KEY)
    except redis.RedisError:
        return []
    if not isinstance(raw, str) or not raw:
        return []
    memo = _entries_memo
    if memo is not None and memo[0] == raw:
        return memo[1]
    try:
        index = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(index, dict):
        return []
    entries = [*(index.get("items") or []), *(index.get("series") or [])]
    _entries_memo = (raw, entries)
    return entries


def _location_docs(db: Session) -> list[_LocationDoc]:
    global _docs_memo
    entries = _location_entries(db)
    memo = _docs_memo
    if memo is not None and memo[0] is entries:
        return memo[1]
    docs = _build_location_docs(entries)
    _docs_memo = (entries, docs)
    return docs


def _build_location_docs(entries: list[dict[str, Any]]) -> list[_LocationDoc]:
    docs: list[_LocationDoc] = []
    for entry in entries:
        slug = str(entry.get("slug") or "")
        name = str(entry.get("name") or "")
        if not slug or not name:
            continue
        places: dict[str, str] = {}
        kinds: dict[str, str] = {}
        for kind in ("city", "region", "country"):
            value = str(entry.get(kind) or "").strip()
            if value:
                places.setdefault(_fold(value), value)
                kinds.setdefault(_fold(value), kind)
        place_tokens = [token for value in places for token in _tokens(value)]
        name_tokens = _tokens(name)
        slug_tokens = _tokens(slug.replace("-", " "))
        docs.append(
            _LocationDoc(
                entry=entry,
                name=_fold(name),
                name_tokens=name_tokens,
                places=places,
                place_kinds=kinds,
                place_tokens=place_tokens,
                slug_tokens=slug_tokens,
            )
        )
    return docs


def _location_payload(entry: dict[str, Any]) -> dict[str, Any]:
    slug = str(entry.get("slug") or "")
    return {
        "slug": slug,
        "name": str(entry.get("name") or ""),
        "city": entry.get("city") or None,
        "platform_codes": list(entry.get("platform_codes") or []),
        "href": f"/locations/{slug}",
    }


def _popularity(doc: _LocationDoc) -> tuple[int, int]:
    return (-int(doc.entry.get("events_count") or 0), -int(doc.entry.get("finishers_total") or 0))


def _match_level(word: str, doc: _LocationDoc) -> int:
    """Насколько слово запроса подходит локации.

    3 — слово целиком (или сокращение города: «спб», «мск»); 2 — с начала
    слова названия, города или адреса («сокол», «горький» → «Горького»);
    0 — не подходит. Из середины слова локации не ищем вовсе: так «минск»
    находил «Котельники Кузьминский», а «топ» — Севастополь. Опечатки ловит
    не это, а похожие названия (similar_locations).
    Сокращение ищем только по месту: «мск» не должен находить Томск.
    """
    alias = _PLACE_ALIASES.get(word)
    if alias is not None:
        return 3 if alias in doc.places else 0
    if word in doc.places:
        return 3
    stem = _stem(word)
    best = 0
    for token in (*doc.name_tokens, *doc.place_tokens, *doc.slug_tokens):
        if token == word:
            return 3
        if token.startswith(word) or (len(stem) >= 4 and token.startswith(stem)):
            best = 2
    return best


def _significant_location_words(query_text: str) -> list[str]:
    words = _tokens(query_text)
    meaningful = [word for word in words if word not in _LOCATION_NOISE_WORDS]
    return meaningful or words


def _resolve_place(words: list[str], docs: list[_LocationDoc]) -> str | None:
    """Весь запрос — это место («москва», «спб», «нижний новгород», «Беларусь»)?

    Возвращает свёрнутое значение города/региона/страны из каталога или None.
    Тогда выдача — локации этого места по популярности, а если их больше,
    чем влезает, — ссылка «Все локации: Москва (41)» в каталог с фильтром.
    """
    if not words:
        return None
    joined = " ".join(words)
    candidate = _PLACE_ALIASES.get(joined, joined)
    if any(candidate in doc.places for doc in docs):
        return candidate
    # «санкт петербург» без дефиса — то же место, что «санкт-петербург».
    for doc in docs:
        for value in doc.places:
            if " ".join(_tokens(value)) == joined:
                return value
    return None


def _catalog_filter_count(docs: list[_LocationDoc], text: str) -> int:
    """Сколько строк покажет каталог /locations?q=text по умолчанию.

    Та же логика, что matchesQuery в LocationsIndexPage: подстрока названия,
    города, региона или страны; серии и локации на паузе или отменённые
    каталог по умолчанию не показывает.
    """
    needle = text.lower()
    count = 0
    for doc in docs:
        entry = doc.entry
        if entry.get("is_series") or entry.get("is_paused") or entry.get("is_cancelled"):
            continue
        haystack = " ".join(
            str(entry.get(key) or "") for key in ("name", "city", "region", "country") if entry.get(key)
        ).lower()
        if needle in haystack:
            count += 1
    return count


def search_locations_page(
    db: Session, query_text: str, docs: list[_LocationDoc] | None = None
) -> dict[str, Any]:
    """Локации по запросу: строки выдачи, сколько всего и ссылка «все» в каталог."""
    empty: dict[str, Any] = {"locations": [], "total": 0, "all": None}
    if len(query_text.strip()) < LOCATION_MIN_QUERY_LENGTH:
        return empty
    words = _significant_location_words(query_text)
    if not words:
        return empty
    if docs is None:
        docs = _location_docs(db)

    place = _resolve_place(words, docs)
    if place is not None:
        in_place = [doc for doc in docs if place in doc.places]
        in_place.sort(key=lambda doc: (*_popularity(doc), doc.name))
        shown = in_place[:LOCATION_LIMIT]
        all_link = None
        if len(in_place) > len(shown):
            value = in_place[0].places[place]
            kind = in_place[0].place_kinds.get(place)
            count = _catalog_filter_count(docs, value)
            if count > len(shown):
                label = _region_label(value) if kind == "region" else value
                all_link = {"label": label, "query": value, "count": count}
        return {"locations": [_location_payload(doc.entry) for doc in shown], "total": len(in_place), "all": all_link}

    first = words[0]
    folded_query = " ".join(words)
    matched: list[tuple[tuple[Any, ...], int, _LocationDoc]] = []
    for doc in docs:
        levels = [_match_level(word, doc) for word in words]
        quality = min(levels)
        if quality == 0:
            continue
        # Совпадение с начала названия — выше всего («Сокольники» на
        # «сокол»), затем с начала любого слова названия, затем остальное.
        if doc.name.startswith(folded_query):
            prefix_rank = 0
        elif any(token.startswith(first) for token in doc.name_tokens):
            prefix_rank = 1
        else:
            prefix_rank = 2
        matched.append(((-quality, prefix_rank, *_popularity(doc), doc.name), quality, doc))
    matched.sort(key=lambda item: item[0])
    return {
        "locations": [_location_payload(doc.entry) for _rank, _quality, doc in matched[:LOCATION_LIMIT]],
        "total": len(matched),
        "all": None,
    }


def search_locations(db: Session, query_text: str) -> list[dict[str, Any]]:
    return search_locations_page(db, query_text)["locations"]


def _trigrams(word: str) -> set[str]:
    # Как pg_trgm: слово дополняется двумя пробелами спереди и одним сзади.
    padded = f"  {word} "
    return {padded[index : index + 3] for index in range(len(padded) - 2)}


def _similarity(left: str, right: str) -> float:
    """Похожесть слов — формула similarity() из pg_trgm (общие триграммы / все).

    Считаем в Python, а не в базе: локаций около трёхсот, и они уже в памяти
    (каталог из Redis), гонять их в Postgres ради одной формулы незачем.
    """
    a, b = _trigrams(left), _trigrams(right)
    union = len(a | b)
    return len(a & b) / union if union else 0.0


SIMILARITY_THRESHOLD = 0.4


def similar_locations(
    db: Session, query_text: str, docs: list[_LocationDoc] | None = None
) -> list[dict[str, Any]]:
    """Похожие по написанию локации — когда не нашлось ничего («сокольнеки»)."""
    words = [word for word in _significant_location_words(query_text) if len(word) >= 4]
    if not words:
        return []
    scored: list[tuple[tuple[Any, ...], _LocationDoc]] = []
    for doc in docs if docs is not None else _location_docs(db):
        tokens = [*doc.name_tokens, *doc.place_tokens]
        if not tokens:
            continue
        per_word = [max(_similarity(word, token) for token in tokens) for word in words]
        score = sum(per_word) / len(per_word)
        if score >= SIMILARITY_THRESHOLD:
            scored.append(((-score, *_popularity(doc), doc.name), doc))
    scored.sort(key=lambda item: item[0])
    return [_location_payload(doc.entry) for _rank, doc in scored[:SIMILAR_LOCATION_LIMIT]]


# ---------------------------------------------------------------------------
# Люди
# ---------------------------------------------------------------------------


def user_profile_handle(user: User) -> str:
    """Хендл публичного профиля — как profileHandle во frontend/src/lib/portalRoutes.ts."""
    slug = (user.public_slug or "").strip()
    return slug or str(user.serial_id)


def _platform_sort_key(code: str) -> int:
    return PLATFORM_ORDER.index(code) if code in PLATFORM_ORDER else len(PLATFORM_ORDER)


def _best_top_location(tops: list[TopLocation]) -> TopLocation | None:
    best: TopLocation | None = None
    for top in tops:
        if best is None or (top.count, top.last_date or date.min) > (best.count, best.last_date or date.min):
            best = top
    return best


def _catalog_place_names(
    db: Session, locations: dict[UUID, tuple[str, str | None]], docs: list[_LocationDoc]
) -> dict[UUID, tuple[str, str | None]]:
    """location_id → (название, город) так, как локацию зовёт каталог сайта.

    В протоколах parkrun-эпохи локация записана латиницей («Izmailovo»,
    «Filatov Lug»), а на сайте у неё давно русское имя — то, что стоит на её
    странице и в каталоге (build_locations_index сводит системы одной точки в
    одну идентичность). Идентичность находим так же, как
    LocationCatalogIndex.canonical_identity_key (catalog_ids_for_locations):
    по связке каталога — через id локации или её слаг в системе (с
    нормализацией «readovsky-park» = «readovskypark» и старым слагом parkrun).
    Весь индекс каталога не строим: он дорогой (все локации и сводки
    стартов), а связок — пара сотен строк.

    locations — id → (код системы, external_key).
    """
    by_identity = {
        str(doc.entry.get("identity_key")): doc.entry for doc in docs if doc.entry.get("identity_key")
    }
    if not locations or not by_identity:
        return {}
    catalog_ids = catalog_ids_for_locations(db, locations)
    names: dict[UUID, tuple[str, str | None]] = {}
    for location_id in locations:
        catalog_id = catalog_ids.get(location_id)
        entry = by_identity.get(f"catalog:{catalog_id}" if catalog_id else f"location:{location_id}")
        name = str((entry or {}).get("name") or "").strip()
        if name:
            names[location_id] = (name, (entry or {}).get("city") or None)
    return names


def _top_locations_by_place(
    db: Session, participant_ids: list[UUID], model: Any, docs: list[_LocationDoc]
) -> dict[UUID, TopLocation]:
    """Как load_top_locations в онбординге, но с названиями из каталога сайта.

    Пробежки на одной точке в разных системах (parkrun-эпоха и 5 вёрст)
    складываются: у точки одно имя — одна строка счёта.
    """
    if not participant_ids:
        return {}
    query = (
        db.query(
            model.participant_id,
            Location.id,
            Location.name,
            Location.city,
            Platform.code,
            Location.external_key,
            func.count(model.id),
            func.max(Event.event_date),
        )
        .join(Event, model.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Location.platform_id == Platform.id)
        .filter(model.participant_id.in_(participant_ids), Event.is_test_event.is_(False))
    )
    if model is VolunteerResult:
        # Сводка ролей parkrun лежит на дате-заглушке — у неё нет настоящей локации.
        query = query.filter(Event.event_date > date(1970, 1, 1))
    rows = query.group_by(model.participant_id, Location.id, Platform.code).all()
    catalog_names = _catalog_place_names(
        db, {row[1]: (row[4], row[5]) for row in rows}, docs
    )
    merged: dict[UUID, dict[tuple[str, str | None], TopLocation]] = {}
    for participant_id, location_id, name, city, _platform_code, _key, count, last_date in rows:
        shown_name, shown_city = catalog_names.get(location_id, (name, city))
        places = merged.setdefault(participant_id, {})
        current = places.get((shown_name, shown_city))
        places[(shown_name, shown_city)] = TopLocation(
            name=shown_name,
            city=shown_city,
            count=count + (current.count if current else 0),
            last_date=max(filter(None, (last_date, current.last_date if current else None)), default=None),
        )
    return {
        participant_id: best
        for participant_id, places in merged.items()
        if (best := _best_top_location(list(places.values()))) is not None
    }


def _top_locations_for(
    db: Session, participant_ids: list[UUID], docs: list[_LocationDoc]
) -> dict[UUID, TopLocation]:
    """Топ-локация по пробежкам, а у кого пробежек нет — по волонтёрствам."""
    by_runs = _top_locations_by_place(db, participant_ids, RunResult, docs)
    missing = [participant_id for participant_id in participant_ids if participant_id not in by_runs]
    by_volunteering = _top_locations_by_place(db, missing, VolunteerResult, docs) if missing else {}
    return {**by_volunteering, **by_runs}


_NAME_SPLIT_RE = re.compile(r"[\s-]+")


def _name_tokens(name: str | None) -> list[str]:
    return [token for token in _NAME_SPLIT_RE.split(_fold(name)) if token]


def _word_levels(name: str | None, words: list[str]) -> list[int]:
    """То же, что word_start_rank в SQL, но в Python и по каждому слову.

    2 — слово имени целиком, 1 — с начала слова имени, 0 — только из середины.
    «Ким»: Юлия Ким — 2, Кимова — 1, Хакимжанов — 0.
    """
    name_tokens = _name_tokens(name)
    levels: list[int] = []
    for word in words:
        folded = _fold(word)
        if folded in name_tokens:
            levels.append(2)
        elif any(token.startswith(folded) for token in name_tokens):
            levels.append(1)
        else:
            levels.append(0)
    return levels


def _name_match_score(name: str | None, words: list[str]) -> int:
    return sum(_word_levels(name, words))


def _is_partial_match(name: str | None, words: list[str]) -> bool:
    """Хоть одно слово запроса нашлось только в середине слова имени: «лев» в «Михалевском»."""
    return 0 in _word_levels(name, words)


def _name_has_words(name: str | None, folded_words: list[str]) -> bool:
    """То же условие, что пул кандидатов и _apply_short_word_filters в SQL, — для строк в Python."""
    folded_name = _fold(name)
    # Сначала дешёвые проверки подстрокой: у тысячи привязок сайта имя почти
    # всегда отсекается на них, и разбирать его на слова незачем.
    if any(word not in folded_name for word in folded_words):
        return False
    tokens: list[str] | None = None
    for word in folded_words:
        if len(word) >= PEOPLE_MIN_QUERY_LENGTH:
            continue
        if tokens is None:
            tokens = _name_tokens(name)
        if len(word) == PEOPLE_SHORT_QUERY_LENGTH:
            if word not in tokens:
                return False
        elif not any(token.startswith(word) for token in tokens):
            return False
    return True


def _people_query_is_searchable(words: list[str]) -> bool:
    """Есть ли в запросе, по чему искать людей: слово от трёх букв или фамилия из двух («Ли»)."""
    return any(
        len(word) >= PEOPLE_MIN_QUERY_LENGTH or (len(word) == PEOPLE_SHORT_QUERY_LENGTH and word.isalpha())
        for word in words
    )


def _apply_short_word_filters(query: Any, words: list[str]) -> Any:
    """Условия на имя для коротких слов публичного поиска.

    Слово от трёх букв — подстрока имени, как в онбординге; его условие
    ставит пул кандидатов (name_candidate_pool), разбивая на ярусы «целиком /
    с начала слова / в середине». Короче — подстрокой быть не может: «ли»
    есть в «Анатолии», и «Ли Москва» находил Анатолия Москву. Поэтому две
    буквы — целое слово имени («Ли», «Ан»), одна — начало слова («Попов Д»).
    Целое слово записано LIKE-шаблонами, которые индекс разбирает на
    триграммы: запрос из одной фамилии «Ли» идёт по индексу; начало слова —
    регуляркой, она только проверяет строки, уже найденные по длинному слову.
    """
    folded = folded_display_name()
    for word in words:
        if len(word) == PEOPLE_SHORT_QUERY_LENGTH:
            query = query.filter(whole_word_filter(word))
        elif len(word) < PEOPLE_SHORT_QUERY_LENGTH:
            query = query.filter(folded.op("~")(word_start_regex(word)))
    return query


@dataclass
class _RegisteredMatch:
    user_id: UUID
    matched_name: str
    # (participant_id, platform_code) всех привязанных профилей человека.
    participants: list[tuple[UUID, str]] = field(default_factory=list)


def _initial_style_hides_match(style: str | None, shown_name: str | None, words: list[str]) -> bool:
    """Человек выбрал показ «Иван П.» — значит, фамилию на сайте прятать.

    Если бы поиск по фамилии приводил на его профиль, «Иван П.» раскрывался бы
    одним запросом. Поэтому такой профиль находим как зарегистрированного
    только по тому, что и так видно на сайте, — по показываемому имени;
    иначе он остаётся обычным участником без ссылки.
    """
    if (style or "") != STYLE_INITIAL:
        return False
    shown = _fold(shown_name)
    return not all(_fold(word) in shown for word in words)


# Ключ в Session.info: привязки открытых профилей, прочитанные одним вызовом
# site_search. Поиск людей за один запрос проходит до нескольких раз (вторая
# раскладка, «имя + место»), а привязки при этом те же — читать их заново
# незачем. Живёт ровно один вызов site_search (см. там).
_REGISTERED_ROWS_KEY = "site_search.registered_rows"


def _registered_link_rows(db: Session) -> list[Any]:
    """Все привязки открытых профилей: (user_id, имя и его стиль на сайте, participant_id, имя в протоколе, система)."""
    holder: dict[str, Any] | None = db.info.get(_REGISTERED_ROWS_KEY)
    if holder is not None and "rows" in holder:
        return holder["rows"]  # type: ignore[no-any-return]
    columns = (
        PlatformLink.user_id,
        User.display_name,
        User.display_name_style,
        Participant.id,
        Participant.display_name,
        Platform.code,
    )
    base = (
        db.query(*columns)
        .select_from(PlatformLink)
        .join(User, PlatformLink.user_id == User.id)
        .filter(User.profile_private.is_(False))
    )
    rows = (
        base.join(Participant, PlatformLink.participant_id == Participant.id)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(Platform.is_active.is_(True))
        .all()
    )
    # Привязка могла появиться раньше строки участника — тогда participant_id
    # пуст, и участник находится по (система, внешний id), как в онбординге.
    # Отдельным запросом: OR в условии соединения ломал план.
    rows += (
        base.join(
            Participant,
            and_(
                PlatformLink.platform_id == Participant.platform_id,
                PlatformLink.external_user_id == Participant.external_user_id,
            ),
        )
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(PlatformLink.participant_id.is_(None), Platform.is_active.is_(True))
        .all()
    )
    if holder is not None:
        holder["rows"] = rows
    return rows


def _find_registered(db: Session, words: list[str]) -> dict[UUID, _RegisteredMatch]:
    """Пользователи с открытым профилем, у которых привязанный участник подходит под запрос.

    Отдельным запросом, а не из общих кандидатов: у распространённого имени
    кандидатов тысячи, и зарегистрированный человек за лимит кандидатов
    выпал бы, хотя в выдаче он должен стоять первым.

    Идём от привязок (их на весь сайт около тысячи) и берём только колонки:
    сам SQL — единицы миллисекунд, а поднять тысячу ORM-объектов User и
    Participant с их JSONB стоило 100–300 мс на запрос.
    """
    rows = _registered_link_rows(db)
    folded_words = [_fold(word) for word in words]
    matches: dict[UUID, _RegisteredMatch] = {}
    participants_by_user: dict[UUID, list[tuple[UUID, str]]] = {}

    def rank(name: str) -> tuple[bool, int]:
        return (not _is_partial_match(name, words), _name_match_score(name, words))

    for user_id, user_name, user_style, participant_id, participant_name, platform_code in rows:
        participants_by_user.setdefault(user_id, []).append((participant_id, platform_code))
        name = participant_name or ""
        if not _name_has_words(name, folded_words) or _is_placeholder_name(name):
            continue
        if _initial_style_hides_match(user_style, user_name, words):
            continue
        # Из нескольких привязанных профилей человека берём тот, чьё имя
        # совпало лучше: по нему строка встаёт в свой ярус выдачи.
        current = matches.get(user_id)
        if current is None or rank(name) > rank(current.matched_name):
            matches[user_id] = _RegisteredMatch(user_id=user_id, matched_name=name)
    # Суммы — по ВСЕМ привязанным профилям человека, а не только по тем, чьё
    # имя совпало: в С95 он может быть записан латиницей, а пробежки те же.
    for user_id, match in matches.items():
        match.participants = participants_by_user.get(user_id, [])
    return matches


@dataclass(frozen=True)
class _Candidate:
    """Участник из протоколов — кандидат выдачи: ровно то, что нужно для ранжирования."""

    id: UUID
    display_name: str
    platform_code: str


@dataclass
class _RegisteredCandidate:
    match: _RegisteredMatch
    user: User
    display_name: str
    total_runs: int
    last_run: date | None
    # participant_id → пробежек: топ-локацию берём у профилей, где человек бегал.
    runs_by_participant: dict[UUID, int]
    partial: bool
    score: int


def _registered_candidates(
    db: Session, matches: dict[UUID, _RegisteredMatch], words: list[str]
) -> list[_RegisteredCandidate]:
    """Зарегистрированные по порядку выдачи: сначала совпавшие с начала слова."""
    if not matches:
        return []
    run_stats = load_run_stats(db, [pid for match in matches.values() for pid, _code in match.participants])
    users = {user.id: user for user in db.query(User).filter(User.id.in_(list(matches))).all()}
    out: list[_RegisteredCandidate] = []
    for match in matches.values():
        user = users.get(match.user_id)
        if user is None:
            continue
        ids = [pid for pid, _code in match.participants]
        dates = [run_stats[pid][1] for pid in ids if pid in run_stats and run_stats[pid][1]]
        out.append(
            _RegisteredCandidate(
                match=match,
                user=user,
                display_name=(user.display_name or "").strip() or prettify_name(match.matched_name.strip()),
                total_runs=sum(run_stats.get(pid, (0, None))[0] for pid in ids),
                last_run=max(dates) if dates else None,
                runs_by_participant={pid: run_stats.get(pid, (0, None))[0] for pid in ids},
                partial=_is_partial_match(match.matched_name, words),
                score=_name_match_score(match.matched_name, words),
            )
        )
    out.sort(key=lambda item: (item.partial, -item.score, -item.total_runs, item.display_name.casefold()))
    return out


def _participant_rank_key(match_score: int, last_run: date | None, runs: int, name: str) -> tuple[int, int, int, int, str]:
    # Сначала — насколько имя совпало с запросом с начала слова («Ким» выше
    # «Кимовой»), дальше как в онбординге: свежепробежавшие выше, «пустые»
    # однофамильцы — внизу. Дата последнего старта видна в строке, поэтому
    # порядок однофамильцев читается.
    return (
        -match_score,
        0 if last_run is not None else 1,
        -(last_run.toordinal() if last_run is not None else 0),
        -runs,
        name.casefold(),
    )


@dataclass(frozen=True)
class _Place:
    """Место из запроса «имя + место»: его локации (все системы и эпохи) и подпись."""

    location_ids: frozenset[UUID]
    label: str
    # Финишей на локациях места по каталогу (сумма finishers_total) — во что
    # обойдётся собрать всех, кто там бегал. None — каталог не знает.
    finishers: int | None
    # Стартов по каталогу (сумма events_count); None — каталог не знает.
    events: int | None = None

    @property
    def is_small(self) -> bool:
        return self.finishers is not None and self.finishers <= PEOPLE_FROM_PLACE_MAX_FINISHERS


def _participants_seen_at(db: Session, participant_ids: list[UUID], location_ids: set[UUID]) -> set[UUID]:
    """Кто из участников бегал или волонтёрил на этих локациях.

    Той же проверкой, что и кандидаты (_seen_at_lateral): по участнику, до
    первого подходящего старта. DISTINCT по всем пробежкам места у «Россия»
    (тысячи локаций) собирал и сортировал все старты страны — 40 мс на каждую
    из двух таблиц.
    """
    if not participant_ids or not location_ids:
        return set()
    return {
        participant_id
        for (participant_id,) in db.query(Participant.id)
        .filter(Participant.id.in_(participant_ids))
        .join(_seen_at_lateral(location_ids), true())
        .all()
    }


def _seen_at_lateral(
    location_ids: set[UUID] | frozenset[UUID],
    event_ids: list[UUID] | None = None,
    participant_id: Any = None,
) -> Any:
    """«Участник бегал или волонтёрил на этих локациях» — LATERAL с LIMIT 1 для JOIN до LIMIT.

    Не EXISTS … OR EXISTS: такое условие Postgres считает «хэшированным
    подпланом» — собирает ВСЕ пробежки места в хэш (у Москвы это полмиллиона
    строк, 0,8 с на «Ли Москва»). LATERAL с LIMIT 1 захэшировать нельзя:
    он выполняется на каждого найденного по имени (их десятки–тысячи) по
    индексу пробежек участника и останавливается на первой подходящей.

    event_ids — все старты места, если их немного (_place_event_ids). Тогда
    старт сверяется со списком прямо в индексе (участник, старт) — без
    чтения таблицы событий на каждую пробежку: 10–15 мкс на человека против
    25–30 (замер на копии прода: 5551 «Анна» в Тюмени — 56 мс против 160).

    participant_id — колонка, по которой сверяем (по умолчанию participants.id).
    """
    who = Participant.id if participant_id is None else participant_id
    if event_ids is not None:
        events = bindparam("place_event_ids", event_ids, type_=ARRAY(Event.id.type))
        runs = select(literal(1).label("hit")).where(RunResult.participant_id == who, RunResult.event_id == any_(events))
        volunteering = select(literal(1).label("hit")).where(
            VolunteerResult.participant_id == who, VolunteerResult.event_id == any_(events)
        )
        return union_all(runs, volunteering).limit(1).lateral("seen_at_place")
    ids = list(location_ids)
    runs = (
        select(literal(1).label("hit"))
        .select_from(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .where(RunResult.participant_id == who, Event.location_id.in_(ids))
    )
    volunteering = (
        select(literal(1).label("hit"))
        .select_from(VolunteerResult)
        .join(Event, VolunteerResult.event_id == Event.id)
        .where(VolunteerResult.participant_id == who, Event.location_id.in_(ids))
    )
    return union_all(runs, volunteering).limit(1).lateral("seen_at_place")


# До скольких стартов место проверяется списком стартов (см. _seen_at_lateral):
# Петербург — 3307, Тюмень — 420. У Москвы (12 тысяч) и страны список уже
# дороже соединения с событиями, а тёзки там бегали почти все, и проверка
# останавливается рано.
PLACE_EVENT_LIST_LIMIT = 4000


def _place_event_ids(db: Session, place: _Place) -> list[UUID] | None:
    """Все старты места, если их не больше PLACE_EVENT_LIST_LIMIT, иначе None.

    Каталог уже знает, сколько у места стартов: у заведомо большого места
    (Москва, страна) список и не читаем — это 20 мс впустую.
    """
    if place.events is None or place.events > PLACE_EVENT_LIST_LIMIT:
        return None
    ids = [
        event_id
        for (event_id,) in db.query(Event.id)
        .filter(Event.location_id.in_(list(place.location_ids)))
        .limit(PLACE_EVENT_LIST_LIMIT + 1)
        .all()
    ]
    return ids if len(ids) <= PLACE_EVENT_LIST_LIMIT else None


def _place_people(location_ids: frozenset[UUID]) -> Any:
    """SELECT всех, кто бегал или волонтёрил на этих локациях, — путь «от места».

    MATERIALIZED: список людей места собирается один раз (по индексу протоколов
    стартов), и дальше Postgres только сверяет с ним найденных по имени, а не
    перебирает протоколы на каждого тёзку.
    """
    place_events = select(Event.id).where(Event.location_id.in_(list(location_ids))).cte("place_events")
    people = (
        union(
            select(RunResult.participant_id).where(RunResult.event_id.in_(select(place_events.c.id))),
            select(VolunteerResult.participant_id).where(VolunteerResult.event_id.in_(select(place_events.c.id))),
        )
        .cte("place_people")
        .prefix_with("MATERIALIZED")
    )
    return select(people.c.participant_id)


def _namesakes_at_big_place(
    db: Session, name_query: Any, substring_words: list[str], place: _Place
) -> tuple[list[UUID], bool]:
    """(id тёзок, бегавших в месте; есть ли ещё) — путь «от имени» для большого места.

    Тёзки идут ярусами имени в том порядке, в каком их ставит выдача, у
    каждого проверяется «бегал ли здесь» (_seen_at_lateral), и проверка
    останавливается, как только кандидатов набралось на выдачу. Всего
    проверяем не больше PEOPLE_PLACE_SCAN_LIMIT тёзок. Если ярус в этот
    предел не влез, дальше не идём и честно говорим «есть ещё»: нижний ярус
    («Сюзанна» на «анна») не должен занимать места непроверенных тёзок из
    верхнего, а «показаны все» при непроверенных — неправда (ревью, SKEP-1).

    Ярус — один запрос: тёзки идут потоком (row_number), место проверяется по
    ходу, и LIMIT по найденным останавливает и чтение имён: в Москве, где
    бегала пятая часть всех «…ова», хватает 2,4 тысячи проверок из 48 тысяч.
    Размер яруса нужен, только если он прочитан до конца, — его несёт
    последняя строка (lead() пуст), и её запрос отдаёт вместе с найденными;
    строка за пределом (rn > limit) значит «ярус в предел не влез».
    """
    need = PEOPLE_CANDIDATE_LIMIT + 1
    budget = PEOPLE_PLACE_SCAN_LIMIT
    event_ids = _place_event_ids(db, place)
    tiers: list[Any] = list(name_tier_conditions(substring_words)) if substring_words else [None]
    hits: list[UUID] = []
    for condition in tiers:
        tier_query = name_query if condition is None else name_query.filter(condition)
        numbered = (
            tier_query.with_entities(
                Participant.id.label("id"),
                func.row_number().over().label("rn"),
                func.lead(Participant.id).over().is_(None).label("is_last"),
            )
            .limit(budget + 1)
            .subquery("namesakes")
        )
        seen = _seen_at_lateral(place.location_ids, event_ids, participant_id=numbered.c.id)
        rows = (
            db.query(numbered.c.id, numbered.c.rn, numbered.c.is_last, seen.c.hit)
            .select_from(numbered)
            .outerjoin(seen, true())
            .filter(or_(seen.c.hit.isnot(None), numbered.c.is_last.is_(True), numbered.c.rn > budget))
            .limit(need - len(hits) + 1)
            .all()
        )
        found = [participant_id for participant_id, rn, _last, hit in rows if hit is not None and rn <= budget]
        hits += found[: need - len(hits)]
        if len(hits) >= need:
            return hits, True
        ends = [rn for _pid, rn, is_last, _hit in rows if is_last or rn > budget]
        if not ends:
            # Пустой ярус: ни строк, ни последней строки.
            continue
        if max(ends) > budget:
            return hits, True
        budget -= max(ends)
    return hits, False


def _namesakes_at_small_place(db: Session, words: list[str], place: _Place) -> tuple[list[Any], bool]:
    """(кандидаты (id, имя, система), есть ли ещё) — путь «от места» для малого места.

    Все, кто бегал или волонтёрил в месте (единицы тысяч человек), приходят
    одним запросом, а имя сверяется уже здесь — теми же правилами, что в SQL
    (_name_has_words), и в том же порядке (_word_levels: сначала все слова с
    начала слова имени, затем по баллу). Сверку имени базе не отдаём: по
    частому сочетанию букв («ова» — 48 тысяч имён) Postgres ждёт сотню строк,
    идёт от имени, и один разбор имён стоил 0,1 с даже для Беларуси, где
    бегали 984 человека.
    """
    rows = (
        db.query(Participant.id, Participant.display_name, Platform.code)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(
            Platform.is_active.is_(True),
            Participant.display_name.isnot(None),
            Participant.id.in_(_place_people(place.location_ids)),
        )
        .all()
    )
    folded_words = [_fold(word) for word in words]
    ranked: list[tuple[tuple[bool, int], Any]] = []
    for row in rows:
        name = row[1]
        if _is_placeholder_name(name) or not _name_has_words(name, folded_words):
            continue
        levels = _word_levels(name, words)
        ranked.append(((0 in levels, -sum(levels)), row))
    ranked.sort(key=lambda item: item[0])
    return [row for _key, row in ranked[: PEOPLE_CANDIDATE_LIMIT + 1]], len(ranked) > PEOPLE_CANDIDATE_LIMIT


def search_people(
    db: Session,
    query_text: str,
    *,
    within: _Place | None = None,
    docs: list[_LocationDoc] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """(строки выдачи, есть ли ещё совпадения за лимитом).

    within — оставить только тех, кто бегал или волонтёрил на локациях этого
    места: так работает «Попов Дмитрий Королёв». Путь — от места или от
    имени, по размеру места (PEOPLE_FROM_PLACE_MAX_FINISHERS).

    Порядок выдачи — два яруса. Сначала все, у кого каждое слово запроса
    совпало с НАЧАЛОМ слова имени (участники сайта, потом люди из
    протоколов), затем те, у кого хоть одно слово нашлось только в середине
    («лев» в «Михалевском»), — в том же порядке и с пометкой partial. Внутри
    первого яруса участник сайта стоит выше человека из протоколов, даже если
    у того имя совпало целиком: «Лев» — это два десятка Львов из протоколов,
    и при строгой сортировке по баллу Александр Левин с открытым профилем
    выпал бы за лимит выдачи вовсе.

    Слово из двух букв («Ли», «Ан», «Юн») ищем только целым словом имени,
    из одной — только началом слова: как подстрока они есть почти в каждом
    имени (см. _apply_short_word_filters).

    Стоимость ограничена при любом запросе: слова без повторов и не больше
    четырёх (significant_words), балл считается только по ограниченному пулу
    кандидатов (name_candidate_pool), место — по людям малого места или по
    ограниченному числу тёзок (_namesakes_at_big_place).
    """
    words = significant_words(query_text.split())
    if not _people_query_is_searchable(words):
        return [], False
    docs = docs if docs is not None else []

    registered = _find_registered(db, words)
    if within is not None:
        seen = _participants_seen_at(
            db, [pid for match in registered.values() for pid, _code in match.participants], set(within.location_ids)
        )
        registered = {
            user_id: match
            for user_id, match in registered.items()
            if any(pid in seen for pid, _code in match.participants)
        }
    registered_ordered = _registered_candidates(db, registered, words)
    # Участники зарегистрированных уже показаны их строкой — вторым разом не выводим.
    covered_ids = {pid for match in registered.values() for pid, _code in match.participants}

    name_query = (
        db.query(Participant.id)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(
            Platform.is_active.is_(True),
            Participant.display_name.isnot(None),
            # Заглушки отсекаем ещё в SQL: иначе на «неизвестн» сотня тысяч
            # «НЕИЗВЕСТНЫЙ» съедала бы весь лимит кандидатов.
            func.lower(func.trim(Participant.display_name)).notin_(sorted(_PLACEHOLDER_NAMES)),
        )
    )
    name_query = _apply_short_word_filters(name_query, words)
    substring_words = [word for word in words if len(word) >= PEOPLE_MIN_QUERY_LENGTH]
    # Кандидаты — только колонки, нужные для ранжирования: поднимать полтысячи
    # ORM-объектов участника (с их JSONB) ради двадцати строк выдачи стоило
    # десятки миллисекунд на запрос. Целиком грузим только показанных.
    candidates_query = db.query(Participant.id, Participant.display_name, Platform.code).join(
        Platform, Participant.platform_id == Platform.id
    )
    if within is not None and within.is_small:
        # Малое место: людей в нём — единицы тысяч, и проверяем их всех, без
        # пула по имени: у «Анна Мытищи» 5551 Анна на все системы, а в
        # Мытищах из них бегали 57 — пул в 1200 тёзок находил семь (SKEP-1).
        # Сначала — есть ли такое имя вообще (по индексу, миллисекунды): «имя +
        # место» перебирает разбиения запроса, и собирать людей места (50–80
        # мс) ради имени «нижний» из «нижний новгород» незачем.
        if apply_name_filters(name_query, substring_words).limit(1).first() is None:
            found, truncated = [], False
        else:
            found, truncated = _namesakes_at_small_place(db, words, within)
    elif within is not None:
        hit_ids, truncated = _namesakes_at_big_place(db, name_query, substring_words, within)
        found = candidates_query.filter(Participant.id.in_(hit_ids)).all() if hit_ids else []
    else:
        pool = name_candidate_pool(
            name_query,
            substring_words,
            tier_limit=PEOPLE_CANDIDATE_LIMIT + 1,
            # Лишние ярусы не нужны, как только верхние набрали лимит.
            stop_when_full=True,
        )
        # Порядок — в SQL, до LIMIT: сначала те, у кого все слова совпали с
        # начала слова имени, затем по баллу (целиком выше, чем с начала).
        found = (
            candidates_query.join(pool, pool.c.id == Participant.id)
            .order_by(word_start_all(words).desc(), word_start_rank(words).desc())
            .limit(PEOPLE_CANDIDATE_LIMIT + 1)
            .all()
        )
        truncated = len(found) > PEOPLE_CANDIDATE_LIMIT
    candidates = [
        _Candidate(participant_id, name or "", platform_code)
        for participant_id, name, platform_code in found[:PEOPLE_CANDIDATE_LIMIT]
        if participant_id not in covered_ids and not _is_placeholder_name(name)
    ]
    # Привязанный к открытому профилю, но не попавший в registered (например,
    # «Иван П.», найденный по фамилии), остаётся строкой участника — это
    # ровно то, что видно в любом протоколе, без связи с аккаунтом.

    run_stats = load_run_stats(db, [candidate.id for candidate in candidates])
    partial_ids = {candidate.id for candidate in candidates if _is_partial_match(candidate.display_name, words)}
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate.id in partial_ids,
            *_participant_rank_key(
                _name_match_score(candidate.display_name, words),
                run_stats.get(candidate.id, (0, None))[1],
                run_stats.get(candidate.id, (0, None))[0],
                candidate.display_name,
            ),
        ),
    )
    ordered: list[tuple[str, Any]] = [
        *(("registered", item) for item in registered_ordered if not item.partial),
        *(("participant", candidate) for candidate in ranked if candidate.id not in partial_ids),
        *(("registered", item) for item in registered_ordered if item.partial),
        *(("participant", candidate) for candidate in ranked if candidate.id in partial_ids),
    ]
    shown = ordered[:PEOPLE_LIMIT]
    truncated = truncated or len(ordered) > len(shown)

    shown_registered: list[_RegisteredCandidate] = [item for kind, item in shown if kind == "registered"]
    shown_participants: list[_Candidate] = [item for kind, item in shown if kind == "participant"]
    registered_ids = [pid for item in shown_registered for pid, _code in item.match.participants]
    shown_ids = [*registered_ids, *(candidate.id for candidate in shown_participants)]
    shown_pairs = (
        db.query(Participant, Platform)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(Participant.id.in_(shown_ids))
        .all()
        if shown_ids
        else []
    )
    # Волонтёрства и топ-локации — только для показываемых строк: у parkrun
    # волонтёрства — отдельный запрос на каждого участника.
    volunteering = load_volunteering_counts(db, shown_pairs)
    tops = _top_locations_for(db, shown_ids, docs)

    rows: list[dict[str, Any]] = []
    for kind, item in shown:
        if kind == "registered":
            ids = [pid for pid, _code in item.match.participants]
            run_tops = [tops[pid] for pid in ids if pid in tops and item.runs_by_participant.get(pid, 0) > 0]
            top = _best_top_location(run_tops) or _best_top_location([tops[pid] for pid in ids if pid in tops])
            rows.append(
                {
                    "kind": "registered",
                    "display_name": item.display_name,
                    "href": f"/users/{user_profile_handle(item.user)}",
                    "avatar_url": item.user.avatar_url,
                    "total_runs": item.total_runs,
                    "total_volunteering": sum(volunteering.get(pid, 0) for pid in ids),
                    "last_run_date": item.last_run,
                    "top_location_name": top.name if top else None,
                    "platform_codes": sorted({code for _pid, code in item.match.participants}, key=_platform_sort_key),
                    "partial": item.partial,
                }
            )
            continue
        top = tops.get(item.id)
        runs, last_run = run_stats.get(item.id, (0, None))
        rows.append(
            {
                "kind": "participant",
                "display_name": prettify_name(item.display_name.strip()),
                "total_runs": runs,
                "total_volunteering": volunteering.get(item.id, 0),
                "last_run_date": last_run,
                "top_location_name": top.name if top else None,
                "top_location_city": top.city if top else None,
                "platform_codes": [item.platform_code],
                "partial": item.id in partial_ids,
            }
        )
    return rows, truncated


def _fold_column(column: Any) -> Any:
    return func.translate(func.lower(column), "ё", "е")


def _catalog_sum(docs: list[_LocationDoc], key: str) -> int | None:
    """Сумма числового поля каталога (finishers_total, events_count); None — хоть у одной локации его нет."""
    total = 0
    for doc in docs:
        value = doc.entry.get(key)
        if value is None:
            return None
        try:
            total += int(value)
        except (TypeError, ValueError):
            return None
    return total


def _place_filter(db: Session, words: list[str], docs: list[_LocationDoc]) -> _Place | None:
    """Слова запроса — место: город, регион, страна или название локации.

    Возвращает локации (все системы, все эпохи), подпись места для выдачи и
    его размер по каталогу. Нужен, чтобы подсказка «добавьте город» не
    обманывала: «Попов Дмитрий Королёв» без этого не находил никого — город
    искался в имени.
    """
    joined = " ".join(_fold(word) for word in words)
    place = _PLACE_ALIASES.get(joined, joined)
    in_place = [doc for doc in docs if place in doc.places]
    if in_place:
        label = in_place[0].places[place]
        conditions = [
            _fold_column(Location.city) == place,
            _fold_column(Location.region) == place,
            _fold_column(Location.country) == place,
        ]
        location_ids = {
            location_id for (location_id,) in db.query(Location.id).filter(or_(*conditions)).limit(5000).all()
        }
        if not location_ids:
            return None
        return _Place(
            frozenset(location_ids), label, _catalog_sum(in_place, "finishers_total"), _catalog_sum(in_place, "events_count")
        )
    # Не город — может быть, название локации («Попов Сокольники»).
    stems = [_stem(_fold(word)) for word in words]
    if any(len(stem) < 3 for stem in stems):
        return None
    named = [doc for doc in docs if all(any(token.startswith(stem) for token in doc.name_tokens) for stem in stems)]
    if not named:
        return None
    label = str(named[0].entry.get("name") or joined) if len(named) == 1 else " ".join(words)
    name_conditions = [
        _fold_column(Location.name).op("~")(f"(^|[^[:alnum:]]){re.escape(stem)}") for stem in stems
    ]
    location_ids = {
        location_id for (location_id,) in db.query(Location.id).filter(and_(*name_conditions)).limit(2000).all()
    }
    if not location_ids:
        return None
    return _Place(frozenset(location_ids), label, _catalog_sum(named, "finishers_total"), _catalog_sum(named, "events_count"))


_T = TypeVar("_T")


# Сколько поисков людей одновременно идёт в одном процессе api и сколько
# запрос ждёт свободного места. Поиск держит соединение из пула (5+10 на
# процесс) и занимает интерпретатор на свою долю разбора выдачи: 15
# одновременных «ова москва» с одного адреса шли по 2–3 с каждый, а соседний
# лёгкий запрос сайта ждал до 1,7 с (замер на стенде 26.09.2026). С потолком
# остальной сайт от залпа поисков не зависит; живым людям трёх мест хватает с
# запасом — поиск занимает десятые доли секунды. Не дождался места —
# людей в выдаче нет (people_skipped), локации есть, как при таймауте.
#
# Место ждут в цикле событий (people_search_slot), а не в потоке: синхронный
# обработчик FastAPI занимает поток из пула anyio (40 на процесс), и залп из
# сорока поисков, ждущих места по секунде, занимал все потоки — /health и
# страницы сайта стояли по 1,5–3 с (ревью, SKEP-2).
PEOPLE_SEARCH_SLOTS = 3
PEOPLE_SLOT_WAIT_SECONDS = 1.0
# Как часто ждущий запрос проверяет, не освободилось ли место. Опрос, а не
# asyncio.Semaphore: тот привязывается к первому циклу событий, а места общие
# на процесс (и тестовый клиент заводит свой цикл).
PEOPLE_SLOT_POLL_SECONDS = 0.02
_people_slots = threading.BoundedSemaphore(PEOPLE_SEARCH_SLOTS)


def _may_search_people(query_text: str) -> bool:
    """Пойдёт ли этот запрос в базу за людьми (иначе и место ему не нужно)."""
    words = significant_words(query_text.split())
    if not _people_query_is_searchable(words):
        return False
    tokens = _tokens(query_text)
    return not (tokens and all(token in _PLACE_ABBREVIATIONS for token in tokens))


def people_search_possible(raw_query: str) -> bool:
    """Может ли site_search пойти за людьми — по запросу или по нему же в другой раскладке."""
    query_text = normalize_query_text(raw_query)[:MAX_QUERY_LENGTH]
    return _may_search_people(query_text) or _may_search_people(switch_keyboard_layout(query_text))


# Пропуски поиска людей (нет места, таймаут) — в лог не чаще раза в минуту и
# со счётчиком: во время залпа их сотни в минуту, и строка на каждый только
# засоряла лог (ревью: 307 строк за три минуты, SKEP-3).
_SKIP_LOG_INTERVAL_SECONDS = 60.0
_skip_log_lock = threading.Lock()
_skip_counts: dict[str, int] = {}
_skip_logged_at: float | None = None


def _note_people_skip(reason: str) -> None:
    global _skip_logged_at
    with _skip_log_lock:
        _skip_counts[reason] = _skip_counts.get(reason, 0) + 1
        now = time.monotonic()
        if _skip_logged_at is not None and now - _skip_logged_at < _SKIP_LOG_INTERVAL_SECONDS:
            return
        summary = ", ".join(f"{key}={count}" for key, count in sorted(_skip_counts.items()))
        _skip_counts.clear()
        _skip_logged_at = now
    logger.warning("site search: people search skipped (%s)", summary)


async def _acquire_people_slot(wait_seconds: float) -> bool:
    deadline = time.monotonic() + wait_seconds
    while not _people_slots.acquire(blocking=False):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(PEOPLE_SLOT_POLL_SECONDS)
    return True


@asynccontextmanager
async def people_search_slot(raw_query: str) -> AsyncIterator[bool]:
    """Место для поиска людей на время запроса; True — место есть.

    Запросу, которому люди не нужны («сп», «мск»), место не достаётся вовсе
    (False): site_search с people_allowed=False такие запросы отрабатывает как
    обычно и пропуском их не считает.
    """
    if not people_search_possible(raw_query):
        yield False
        return
    acquired = await _acquire_people_slot(PEOPLE_SLOT_WAIT_SECONDS)
    if not acquired:
        _note_people_skip("no_slot")
    try:
        yield acquired
    finally:
        if acquired:
            _people_slots.release()


@dataclass
class _PeopleGuard:
    """Потолок на поиск людей в одном запросе: разрешение, время и statement_timeout.

    Поиск людей — единственная часть выдачи, которая ходит в базу по вводу
    анонима, и за один запрос он может пройти несколько раз (вторая
    раскладка, «имя + место»). Место в процессе берёт вызывающий
    (people_search_slot) и передаёт сюда как allowed. Каждый SQL ограничен
    SET LOCAL statement_timeout (prepare_search_transaction), а все попытки
    вместе — бюджетом PEOPLE_TIME_BUDGET_SECONDS. Отмена по таймауту — не
    ошибка сервера: транзакцию откатываем, людей в выдаче нет, локации (они
    из кэша, без базы) отдаём как обычно.

    skipped — поиск людей нужен был, но (целиком или частью) не сделан: нет
    места, кончилось время, отменён по таймауту. Выдача тогда помечается
    people_skipped: пустой список людей в ней — «не искали», а не «не нашли».
    """

    db: Session
    deadline: float
    allowed: bool = True
    skipped: bool = False
    prepared: bool = False

    def has_time(self) -> bool:
        if self.allowed and time.monotonic() < self.deadline:
            return True
        self.skipped = True
        return False

    def run(self, search: Callable[[], _T], default: _T) -> _T:
        if not self.has_time():
            return default
        if not self.prepared:
            prepare_search_transaction(self.db)
            self.prepared = True
        try:
            return search()
        except OperationalError as exc:
            if not is_statement_timeout(exc):
                raise
            # Транзакция после отмены запроса сломана — без отката сессия не
            # выполнит больше ничего. Текст запроса в лог не пишем: это имена.
            self.db.rollback()
            self.prepared = False
            self.allowed = False
            self.skipped = True
            _note_people_skip("statement_timeout")
            return default


def _people_by_name_and_place(
    db: Session, query_text: str, docs: list[_LocationDoc], guard: _PeopleGuard
) -> tuple[list[dict[str, Any]], bool, str] | None:
    """«Попов Дмитрий Москва»: имя + место, когда по одному имени — пусто.

    Пробуем считать местом последнее слово, два последних («нижний
    новгород») и первое. Имени должно остаться хотя бы две буквы («Ли»).
    """
    words = significant_words(query_text.split())
    if len(words) < 2:
        return None
    splits: list[tuple[list[str], list[str]]] = [(words[:-1], words[-1:])]
    if len(words) >= 3:
        splits.append((words[:-2], words[-2:]))
    splits.append((words[1:], words[:1]))
    unchecked: tuple[list[dict[str, Any]], bool, str] | None = None
    for name_words, place_words in splits:
        if not guard.has_time():
            return unchecked
        name_text = " ".join(name_words)
        # «Ли Москва»: имя из двух букв search_people ищет целым словом.
        if len(name_text) < PEOPLE_SHORT_QUERY_LENGTH:
            continue
        place = _place_filter(db, place_words, docs)
        if place is None:
            continue
        people, truncated = search_people(db, name_text, within=place, docs=docs)
        if people:
            return people, truncated, place.label
        if truncated and unchecked is None:
            # Большое место, и тёзок проверили не всех (PEOPLE_PLACE_SCAN_LIMIT):
            # среди проверенных никого, но «никого нет» было бы неправдой.
            unchecked = ([], True, place.label)
    return unchecked


# ---------------------------------------------------------------------------
# Вместе
# ---------------------------------------------------------------------------


def _find_people(
    db: Session, query_text: str, docs: list[_LocationDoc], *, locations_found: bool, guard: _PeopleGuard
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """(люди, есть ли ещё, место из запроса) — всё, что поиск берёт из базы."""
    people, truncated = search_people(db, query_text, docs=docs)
    # Имя + место пробуем, только когда не нашлось ни людей, ни локаций:
    # «парк горького» — это локация, а не люди «Парк…», бегавшие у Горького.
    if not people and not locations_found:
        by_place = _people_by_name_and_place(db, query_text, docs, guard)
        if by_place is not None:
            return by_place
    return people, truncated, None


def _run_search(db: Session, query_text: str, docs: list[_LocationDoc], guard: _PeopleGuard) -> dict[str, Any]:
    locations = search_locations_page(db, query_text, docs)
    people: tuple[list[dict[str, Any]], bool, str | None] = ([], False, None)
    if _may_search_people(query_text):
        people = guard.run(
            lambda: _find_people(db, query_text, docs, locations_found=bool(locations["locations"]), guard=guard),
            people,
        )
    found_people, truncated, people_place = people
    return {
        "locations": locations["locations"],
        "locations_total": locations["total"],
        "locations_all": locations["all"],
        "people": found_people,
        "people_truncated": truncated,
        "people_place": people_place,
    }


def site_search(db: Session, raw_query: str, *, people_allowed: bool = True) -> dict[str, Any]:
    """Поиск по сайту: локации (из кэша каталога) и люди (из базы).

    people_allowed=False — места для поиска людей в процессе не досталось
    (people_search_slot): в базу не ходим, отдаём локации, а выдачу помечаем
    people_skipped, если люди этому запросу были нужны.
    """
    query_text = normalize_query_text(raw_query)[:MAX_QUERY_LENGTH]
    # Каталог локаций читаем один раз на запрос: он нужен и локациям, и
    # похожим названиям, и поиску людей по месту.
    docs = _location_docs(db) if len(query_text) >= LOCATION_MIN_QUERY_LENGTH else []
    guard = _PeopleGuard(db=db, deadline=time.monotonic() + PEOPLE_TIME_BUDGET_SECONDS, allowed=people_allowed)
    db.info[_REGISTERED_ROWS_KEY] = {}
    try:
        return _site_search(db, query_text, docs, guard)
    finally:
        db.info.pop(_REGISTERED_ROWS_KEY, None)


def _site_search(db: Session, query_text: str, docs: list[_LocationDoc], guard: _PeopleGuard) -> dict[str, Any]:
    found = _run_search(db, query_text, docs, guard)
    corrected: str | None = None
    similar = False
    # Другая раскладка и похожие названия — ответ на «не нашлось ничего». Если
    # людей не искали (guard.skipped), это не установлено: «похожие локации»
    # на запрос с именем были бы враньём. К тому же во время залпа эти
    # запасные ветки — 10–15 мс процессора на каждый отказ, и сотня отказов,
    # сработавших разом, занимала пул потоков вместе с /health (SKEP-2).
    if (
        not guard.skipped
        and not found["locations"]
        and not found["people"]
        and len(query_text) >= LOCATION_MIN_QUERY_LENGTH
    ):
        switched = switch_keyboard_layout(query_text)
        if switched != query_text:
            alt = _run_search(db, switched, docs, guard)
            if alt["locations"] or alt["people"]:
                corrected = switched
                found = alt
        if not found["locations"] and not found["people"]:
            suggestions = similar_locations(db, query_text, docs)
            if suggestions:
                similar = True
                found = {**found, "locations": suggestions, "locations_total": len(suggestions)}
    return {
        "query": query_text,
        "corrected_query": corrected,
        "locations": found["locations"],
        "locations_total": found["locations_total"],
        "locations_all": found["locations_all"],
        "locations_similar": similar,
        "people": found["people"],
        "people_truncated": found["people_truncated"],
        "people_place": found["people_place"],
        # Люди были нужны, но не искались (нет места, таймаут): пустой список —
        # это «не искали», а не «не нашли». Фронт так и пишет, а в журнал
        # такой поиск не идёт — иначе он засорял бы «Не нашлось ничего».
        "people_skipped": guard.skipped and not found["people"],
    }
