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

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User, VolunteerResult
from app.services.co_runners_service import _UNKNOWN_PARTICIPANT_NAMES, _is_unknown_participant_name
from app.services.participant_search_service import (
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    TopLocation,
    apply_name_filters,
    load_run_stats,
    load_top_locations,
    load_volunteering_counts,
    normalize_query_text,
    word_start_rank,
)
from app.services.platform_titles import PLATFORM_ORDER
from app.services.user_display_name_service import STYLE_INITIAL, prettify_name

LOCATION_MIN_QUERY_LENGTH = 2
PEOPLE_MIN_QUERY_LENGTH = MIN_QUERY_LENGTH
LOCATION_LIMIT = 8
SIMILAR_LOCATION_LIMIT = 5
PEOPLE_LIMIT = 20
# Кандидатов по имени берём с запасом и уже упорядоченными в SQL (совпадения
# с начала слова — первыми, см. word_start_rank), а дальше ранжируем по
# пробежкам в Python. 500 id по GIN-индексу и один group by по их пробежкам —
# десятки миллисекунд.
PEOPLE_CANDIDATE_LIMIT = 500

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
    """Запрос для журнала: без лишних пробелов, в нижнем регистре, до 100 знаков.

    Нижний регистр — чтобы «Сокольники» и «сокольники» сложились в одну строку
    топа запросов.
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


_TOKEN_SPLIT_RE = re.compile(r"[^\w]+")


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


def _location_entries(db: Session) -> list[dict[str, Any]]:
    """Идентичности каталога локаций — ровно те, у которых есть /locations/{slug}.

    Берём готовый каталог (build_locations_index): он уже склеивает одну
    площадку в нескольких системах в одну строку с одним слагом и лежит в
    Redis (прогревается задачей locations_warm). Собственный запрос по
    locations вернул бы по строке на систему и слаги, которые никуда не ведут.
    """
    from app.services.location_page_service import build_locations_index

    index = build_locations_index(db)
    return [*(index.get("items") or []), *(index.get("series") or [])]  # type: ignore[misc]


def _location_docs(db: Session) -> list[_LocationDoc]:
    docs: list[_LocationDoc] = []
    for entry in _location_entries(db):
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


def _top_locations_for(
    db: Session, participant_ids: list[UUID]
) -> dict[UUID, TopLocation]:
    """Топ-локация по пробежкам, а у кого пробежек нет — по волонтёрствам."""
    by_runs = load_top_locations(db, participant_ids, source="run")
    missing = [participant_id for participant_id in participant_ids if participant_id not in by_runs]
    by_volunteering = load_top_locations(db, missing, source="volunteer") if missing else {}
    return {**by_volunteering, **by_runs}


def _name_match_score(name: str | None, words: list[str]) -> int:
    """То же, что word_start_rank в SQL, но в Python — для ранжирования строк.

    За каждое слово: 2 — совпало слово имени целиком, 1 — с начала слова,
    0 — из середины. «Ким»: Юлия Ким, потом Кимовы, потом Хакимжановы.
    """
    name_tokens = [token for token in re.split(r"[\s-]+", _fold(name)) if token]
    score = 0
    for word in words:
        folded = _fold(word)
        if folded in name_tokens:
            score += 2
        elif any(token.startswith(folded) for token in name_tokens):
            score += 1
    return score


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


def _find_registered(db: Session, words: list[str]) -> dict[UUID, _RegisteredMatch]:
    """Пользователи с открытым профилем, у которых привязанный участник подходит под запрос.

    Отдельным запросом, а не из общих кандидатов: у распространённого имени
    кандидатов тысячи, и зарегистрированный человек за лимит кандидатов
    выпал бы, хотя в выдаче он должен стоять первым.

    Идём от привязок (их на весь сайт около тысячи) и берём только колонки:
    сам SQL — единицы миллисекунд, а поднять тысячу ORM-объектов User и
    Participant с их JSONB стоило 100–300 мс на запрос.
    """
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
    # Та же семантика, что у apply_name_filters: каждое слово — подстрока
    # имени, без различия регистра и «е»/«ё».
    folded_words = [_fold(word) for word in words]
    matches: dict[UUID, _RegisteredMatch] = {}
    participants_by_user: dict[UUID, list[tuple[UUID, str]]] = {}
    for user_id, user_name, user_style, participant_id, participant_name, platform_code in rows:
        participants_by_user.setdefault(user_id, []).append((participant_id, platform_code))
        name = participant_name or ""
        if user_id in matches or _is_placeholder_name(name):
            continue
        folded_name = _fold(name)
        if not all(word in folded_name for word in folded_words):
            continue
        if _initial_style_hides_match(user_style, user_name, words):
            continue
        matches[user_id] = _RegisteredMatch(user_id=user_id, matched_name=name)
    # Суммы — по ВСЕМ привязанным профилям человека, а не только по тем, чьё
    # имя совпало: в С95 он может быть записан латиницей, а пробежки те же.
    for user_id, match in matches.items():
        match.participants = participants_by_user.get(user_id, [])
    return matches


def _registered_payloads(
    db: Session, matches: dict[UUID, _RegisteredMatch], words: list[str]
) -> list[dict[str, Any]]:
    if not matches:
        return []
    run_stats = load_run_stats(db, [pid for match in matches.values() for pid, _code in match.participants])

    def total_runs(match: _RegisteredMatch) -> int:
        return sum(run_stats.get(pid, (0, None))[0] for pid, _code in match.participants)

    def last_run(match: _RegisteredMatch) -> date | None:
        dates = [run_stats[pid][1] for pid, _code in match.participants if pid in run_stats and run_stats[pid][1]]
        return max(dates) if dates else None

    users = {user.id: user for user in db.query(User).filter(User.id.in_(list(matches))).all()}

    def display_name(match: _RegisteredMatch) -> str:
        user = users.get(match.user_id)
        return ((user.display_name if user else None) or "").strip() or prettify_name(match.matched_name.strip())

    ordered = sorted(
        (match for match in matches.values() if match.user_id in users),
        key=lambda match: (
            -_name_match_score(match.matched_name, words),
            -total_runs(match),
            display_name(match).casefold(),
        ),
    )
    shown = ordered[:PEOPLE_LIMIT]
    # Волонтёрства и топ-локации — только для показываемых: у parkrun
    # волонтёрства — отдельный запрос на каждого участника.
    shown_ids = [pid for match in shown for pid, _code in match.participants]
    shown_candidates = (
        db.query(Participant, Platform)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(Participant.id.in_(shown_ids))
        .all()
        if shown_ids
        else []
    )
    volunteering = load_volunteering_counts(db, shown_candidates)
    tops = _top_locations_for(db, shown_ids)

    payloads: list[dict[str, Any]] = []
    for match in shown:
        user = users[match.user_id]
        ids = [pid for pid, _code in match.participants]
        run_tops = [tops[pid] for pid in ids if pid in tops and run_stats.get(pid, (0, None))[0] > 0]
        top = _best_top_location(run_tops) or _best_top_location([tops[pid] for pid in ids if pid in tops])
        codes = sorted({code for _pid, code in match.participants}, key=_platform_sort_key)
        payloads.append(
            {
                "kind": "registered",
                "display_name": display_name(match),
                "href": f"/users/{user_profile_handle(user)}",
                "avatar_url": user.avatar_url,
                "total_runs": total_runs(match),
                "total_volunteering": sum(volunteering.get(pid, 0) for pid in ids),
                "last_run_date": last_run(match),
                "top_location_name": top.name if top else None,
                "platform_codes": codes,
            }
        )
    return payloads


def _participant_rank_key(match_score: int, last_run: date | None, runs: int, name: str) -> tuple[int, int, int, int, str]:
    # Сначала — насколько имя совпало с запросом с начала слова («Ким» выше
    # «Хакимжанова»), дальше как в онбординге: свежепробежавшие выше, «пустые»
    # однофамильцы — внизу. Дата последнего старта видна в строке, поэтому
    # порядок однофамильцев читается.
    return (
        -match_score,
        0 if last_run is not None else 1,
        -(last_run.toordinal() if last_run is not None else 0),
        -runs,
        name.casefold(),
    )


def _participants_seen_at(db: Session, participant_ids: list[UUID], location_ids: set[UUID]) -> set[UUID]:
    """Кто из участников бегал или волонтёрил на этих локациях."""
    if not participant_ids or not location_ids:
        return set()
    seen: set[UUID] = set()
    for model in (RunResult, VolunteerResult):
        seen.update(
            participant_id
            for (participant_id,) in db.query(model.participant_id)
            .join(Event, model.event_id == Event.id)
            .filter(model.participant_id.in_(participant_ids), Event.location_id.in_(list(location_ids)))
            .distinct()
            .all()
        )
    return seen


def search_people(
    db: Session, query_text: str, *, within_locations: set[UUID] | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """(строки выдачи, есть ли ещё совпадения за лимитом).

    within_locations — оставить только тех, кто бегал или волонтёрил на этих
    локациях: так работает «Попов Дмитрий Королёв».
    """
    words = query_text.split()
    if len(query_text) < PEOPLE_MIN_QUERY_LENGTH or not words:
        return [], False

    registered = _find_registered(db, words)
    if within_locations is not None:
        seen = _participants_seen_at(
            db, [pid for match in registered.values() for pid, _code in match.participants], within_locations
        )
        registered = {
            user_id: match
            for user_id, match in registered.items()
            if any(pid in seen for pid, _code in match.participants)
        }
    registered_rows = _registered_payloads(db, registered, words)
    # Участники зарегистрированных уже показаны их строкой — вторым разом не выводим.
    covered_ids = {pid for match in registered.values() for pid, _code in match.participants}

    candidates_query = (
        db.query(Participant, Platform)
        .join(Platform, Participant.platform_id == Platform.id)
        .filter(
            Platform.is_active.is_(True),
            Participant.display_name.isnot(None),
            # Заглушки отсекаем ещё в SQL: иначе на «неизвестн» сотня тысяч
            # «НЕИЗВЕСТНЫЙ» съедала бы весь лимит кандидатов.
            func.lower(func.trim(Participant.display_name)).notin_(sorted(_PLACEHOLDER_NAMES)),
        )
    )
    candidates = (
        apply_name_filters(candidates_query, words)
        .order_by(word_start_rank(words).desc())
        .limit(PEOPLE_CANDIDATE_LIMIT + 1)
        .all()
    )
    truncated = len(candidates) > PEOPLE_CANDIDATE_LIMIT
    candidates = [
        (participant, platform)
        for participant, platform in candidates[:PEOPLE_CANDIDATE_LIMIT]
        if participant.id not in covered_ids and not _is_placeholder_name(participant.display_name)
    ]
    if within_locations is not None:
        seen = _participants_seen_at(db, [participant.id for participant, _platform in candidates], within_locations)
        candidates = [(participant, platform) for participant, platform in candidates if participant.id in seen]
    # Привязанный к открытому профилю, но не попавший в registered (например,
    # «Иван П.», найденный по фамилии), остаётся строкой участника — это
    # ровно то, что видно в любом протоколе, без связи с аккаунтом.

    run_stats = load_run_stats(db, [participant.id for participant, _platform in candidates])
    ranked = sorted(
        candidates,
        key=lambda pair: _participant_rank_key(
            _name_match_score(pair[0].display_name, words),
            run_stats.get(pair[0].id, (0, None))[1],
            run_stats.get(pair[0].id, (0, None))[0],
            pair[0].display_name or "",
        ),
    )
    room = PEOPLE_LIMIT - len(registered_rows)
    shown = ranked[: max(room, 0)]
    truncated = truncated or len(ranked) > len(shown) or len(registered) > len(registered_rows)

    # Волонтёрства и топ-локацию считаем только для показываемых строк:
    # у parkrun волонтёрства — отдельный запрос на человека.
    volunteering = load_volunteering_counts(db, shown)
    tops = _top_locations_for(db, [participant.id for participant, _platform in shown])
    participant_rows: list[dict[str, Any]] = []
    for participant, platform in shown:
        top = tops.get(participant.id)
        runs, last_run = run_stats.get(participant.id, (0, None))
        participant_rows.append(
            {
                "kind": "participant",
                "display_name": prettify_name((participant.display_name or "").strip()),
                "total_runs": runs,
                "total_volunteering": volunteering.get(participant.id, 0),
                "last_run_date": last_run,
                "top_location_name": top.name if top else None,
                "top_location_city": top.city if top else None,
                "platform_codes": [platform.code],
            }
        )
    return [*registered_rows, *participant_rows], truncated


def _fold_column(column: Any) -> Any:
    return func.translate(func.lower(column), "ё", "е")


def _place_filter(db: Session, words: list[str], docs: list[_LocationDoc]) -> tuple[set[UUID], str] | None:
    """Слова запроса — место: город, регион, страна или название локации.

    Возвращает локации (все системы, все эпохи) и подпись места для выдачи.
    Нужен, чтобы подсказка «добавьте город» не обманывала: «Попов Дмитрий
    Королёв» без этого не находил никого — город искался в имени.
    """
    joined = " ".join(_fold(word) for word in words)
    place = _PLACE_ALIASES.get(joined, joined)
    label: str | None = None
    conditions: list[Any] = []
    for doc in docs:
        if place in doc.places:
            label = doc.places[place]
            break
    if label is not None:
        conditions += [
            _fold_column(Location.city) == place,
            _fold_column(Location.region) == place,
            _fold_column(Location.country) == place,
        ]
    else:
        # Не город — может быть, название локации («Попов Сокольники»).
        stems = [_stem(_fold(word)) for word in words]
        if any(len(stem) < 3 for stem in stems):
            return None
        named = [
            doc for doc in docs if all(any(token.startswith(stem) for token in doc.name_tokens) for stem in stems)
        ]
        if not named:
            return None
        label = str(named[0].entry.get("name") or joined) if len(named) == 1 else " ".join(words)
        for stem in stems:
            conditions.append(_fold_column(Location.name).op("~")(f"(^|[^[:alnum:]]){re.escape(stem)}"))
        location_ids = {
            location_id for (location_id,) in db.query(Location.id).filter(and_(*conditions)).limit(2000).all()
        }
        return (location_ids, label) if location_ids else None
    location_ids = {location_id for (location_id,) in db.query(Location.id).filter(or_(*conditions)).limit(5000).all()}
    return (location_ids, label) if location_ids else None


def _people_by_name_and_place(
    db: Session, query_text: str, docs: list[_LocationDoc]
) -> tuple[list[dict[str, Any]], bool, str] | None:
    """«Попов Дмитрий Москва»: имя + место, когда по одному имени — пусто.

    Пробуем считать местом последнее слово, два последних («нижний
    новгород») и первое. Имени должно остаться хотя бы одно слово от 3 букв.
    """
    words = query_text.split()
    if len(words) < 2:
        return None
    splits: list[tuple[list[str], list[str]]] = [(words[:-1], words[-1:])]
    if len(words) >= 3:
        splits.append((words[:-2], words[-2:]))
    splits.append((words[1:], words[:1]))
    for name_words, place_words in splits:
        name_text = " ".join(name_words)
        if len(name_text) < PEOPLE_MIN_QUERY_LENGTH:
            continue
        place = _place_filter(db, place_words, docs)
        if place is None:
            continue
        location_ids, label = place
        people, truncated = search_people(db, name_text, within_locations=location_ids)
        if people:
            return people, truncated, label
    return None


# ---------------------------------------------------------------------------
# Вместе
# ---------------------------------------------------------------------------


def _run_search(db: Session, query_text: str, docs: list[_LocationDoc]) -> dict[str, Any]:
    locations = search_locations_page(db, query_text, docs)
    words = _tokens(query_text)
    if words and all(word in _PLACE_ABBREVIATIONS for word in words):
        people, truncated = [], False
    else:
        people, truncated = search_people(db, query_text)
    people_place: str | None = None
    # Имя + место пробуем, только когда не нашлось ни людей, ни локаций:
    # «парк горького» — это локация, а не люди «Парк…», бегавшие у Горького.
    if not people and not locations["locations"]:
        by_place = _people_by_name_and_place(db, query_text, docs)
        if by_place is not None:
            people, truncated, people_place = by_place
    return {
        "locations": locations["locations"],
        "locations_total": locations["total"],
        "locations_all": locations["all"],
        "people": people,
        "people_truncated": truncated,
        "people_place": people_place,
    }


def site_search(db: Session, raw_query: str) -> dict[str, Any]:
    query_text = normalize_query_text(raw_query)[:MAX_QUERY_LENGTH]
    # Каталог локаций читаем один раз на запрос: он нужен и локациям, и
    # похожим названиям, и поиску людей по месту.
    docs = _location_docs(db) if len(query_text) >= LOCATION_MIN_QUERY_LENGTH else []
    found = _run_search(db, query_text, docs)
    corrected: str | None = None
    similar = False
    if not found["locations"] and not found["people"] and len(query_text) >= LOCATION_MIN_QUERY_LENGTH:
        switched = switch_keyboard_layout(query_text)
        if switched != query_text:
            alt = _run_search(db, switched, docs)
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
    }
