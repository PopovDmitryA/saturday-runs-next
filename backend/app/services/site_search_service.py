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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
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
)
from app.services.platform_titles import PLATFORM_ORDER
from app.services.user_display_name_service import STYLE_INITIAL, prettify_name

LOCATION_MIN_QUERY_LENGTH = 2
PEOPLE_MIN_QUERY_LENGTH = MIN_QUERY_LENGTH
LOCATION_LIMIT = 8
PEOPLE_LIMIT = 20
# Кандидатов по имени берём с запасом: ранжируем по пробежкам уже в Python, и
# при распространённой фамилии первые попавшиеся 20 строк — случайные люди.
# 500 id по GIN-индексу и один group by по их пробежкам — десятки миллисекунд.
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


# ---------------------------------------------------------------------------
# Локации
# ---------------------------------------------------------------------------


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


def search_locations(db: Session, query_text: str) -> list[dict[str, Any]]:
    words = [_fold(word) for word in query_text.split()]
    if len(query_text) < LOCATION_MIN_QUERY_LENGTH or not words:
        return []
    folded_query = _fold(query_text)
    matched: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for entry in _location_entries(db):
        slug = str(entry.get("slug") or "")
        name = str(entry.get("name") or "")
        if not slug or not name:
            continue
        folded_name = _fold(name)
        haystack = " ".join((folded_name, _fold(entry.get("city")), _fold(slug)))
        if not all(word in haystack for word in words):
            continue
        # Совпадение с начала названия — выше всего («Сокольники» на
        # «сокол»), затем с начала любого слова названия, затем остальное.
        if folded_name.startswith(folded_query):
            prefix_rank = 0
        elif any(part.startswith(words[0]) for part in folded_name.replace("-", " ").split()):
            prefix_rank = 1
        else:
            prefix_rank = 2
        rank = (
            prefix_rank,
            -int(entry.get("events_count") or 0),
            -int(entry.get("finishers_total") or 0),
            folded_name,
        )
        matched.append(
            (
                rank,
                {
                    "slug": slug,
                    "name": name,
                    "city": entry.get("city") or None,
                    "platform_codes": list(entry.get("platform_codes") or []),
                    "href": f"/locations/{slug}",
                },
            )
        )
    matched.sort(key=lambda item: item[0])
    return [payload for _rank, payload in matched[:LOCATION_LIMIT]]


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
    folded_words = [word.lower() for word in words]
    matches: dict[UUID, _RegisteredMatch] = {}
    participants_by_user: dict[UUID, list[tuple[UUID, str]]] = {}
    for user_id, user_name, user_style, participant_id, participant_name, platform_code in rows:
        participants_by_user.setdefault(user_id, []).append((participant_id, platform_code))
        name = participant_name or ""
        if user_id in matches or _is_placeholder_name(name):
            continue
        # Та же семантика, что у apply_name_filters: каждое слово — подстрока lower(имени).
        lowered = name.lower()
        if not all(word in lowered for word in folded_words):
            continue
        if _initial_style_hides_match(user_style, user_name, words):
            continue
        matches[user_id] = _RegisteredMatch(user_id=user_id, matched_name=name)
    # Суммы — по ВСЕМ привязанным профилям человека, а не только по тем, чьё
    # имя совпало: в С95 он может быть записан латиницей, а пробежки те же.
    for user_id, match in matches.items():
        match.participants = participants_by_user.get(user_id, [])
    return matches


def _registered_payloads(db: Session, matches: dict[UUID, _RegisteredMatch]) -> list[dict[str, Any]]:
    if not matches:
        return []
    run_stats = load_run_stats(db, [pid for match in matches.values() for pid, _code in match.participants])

    def total_runs(match: _RegisteredMatch) -> int:
        return sum(run_stats.get(pid, (0, None))[0] for pid, _code in match.participants)

    users = {user.id: user for user in db.query(User).filter(User.id.in_(list(matches))).all()}

    def display_name(match: _RegisteredMatch) -> str:
        user = users.get(match.user_id)
        return ((user.display_name if user else None) or "").strip() or prettify_name(match.matched_name.strip())

    ordered = sorted(
        (match for match in matches.values() if match.user_id in users),
        key=lambda match: (-total_runs(match), display_name(match).casefold()),
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
                "top_location_name": top.name if top else None,
                "platform_codes": codes,
            }
        )
    return payloads


def _participant_rank_key(last_run: date | None, runs: int, name: str) -> tuple[int, int, int, str]:
    # Как в онбординге: свежепробежавшие выше, «пустые» однофамильцы — внизу.
    return (
        0 if last_run is not None else 1,
        -(last_run.toordinal() if last_run is not None else 0),
        -runs,
        name.casefold(),
    )


def search_people(db: Session, query_text: str) -> tuple[list[dict[str, Any]], bool]:
    """(строки выдачи, есть ли ещё совпадения за лимитом)."""
    words = query_text.split()
    if len(query_text) < PEOPLE_MIN_QUERY_LENGTH or not words:
        return [], False

    registered = _find_registered(db, words)
    registered_rows = _registered_payloads(db, registered)
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
    candidates = apply_name_filters(candidates_query, words).limit(PEOPLE_CANDIDATE_LIMIT + 1).all()
    truncated = len(candidates) > PEOPLE_CANDIDATE_LIMIT
    candidates = [
        (participant, platform)
        for participant, platform in candidates[:PEOPLE_CANDIDATE_LIMIT]
        if participant.id not in covered_ids and not _is_placeholder_name(participant.display_name)
    ]
    # Привязанный к открытому профилю, но не попавший в registered (например,
    # «Иван П.», найденный по фамилии), остаётся строкой участника — это
    # ровно то, что видно в любом протоколе, без связи с аккаунтом.

    run_stats = load_run_stats(db, [participant.id for participant, _platform in candidates])
    ranked = sorted(
        candidates,
        key=lambda pair: _participant_rank_key(
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
        participant_rows.append(
            {
                "kind": "participant",
                "display_name": prettify_name((participant.display_name or "").strip()),
                "total_runs": run_stats.get(participant.id, (0, None))[0],
                "total_volunteering": volunteering.get(participant.id, 0),
                "top_location_name": top.name if top else None,
                "top_location_city": top.city if top else None,
                "platform_codes": [platform.code],
            }
        )
    return [*registered_rows, *participant_rows], truncated


# ---------------------------------------------------------------------------
# Вместе
# ---------------------------------------------------------------------------


def _run_search(db: Session, query_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    locations = search_locations(db, query_text)
    people, truncated = search_people(db, query_text)
    return locations, people, truncated


def site_search(db: Session, raw_query: str) -> dict[str, Any]:
    query_text = normalize_query_text(raw_query)[:MAX_QUERY_LENGTH]
    locations, people, truncated = _run_search(db, query_text)
    corrected: str | None = None
    if not locations and not people and len(query_text) >= LOCATION_MIN_QUERY_LENGTH:
        switched = switch_keyboard_layout(query_text)
        if switched != query_text:
            alt_locations, alt_people, alt_truncated = _run_search(db, switched)
            if alt_locations or alt_people:
                corrected = switched
                locations, people, truncated = alt_locations, alt_people, alt_truncated
    return {
        "query": query_text,
        "corrected_query": corrected,
        "locations": locations,
        "people": people,
        "people_truncated": truncated,
    }

