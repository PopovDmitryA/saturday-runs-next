"""Рейтинги челленджей: p-индекс, V-индекс, Уилсон, Алфавит и ещё шесть — по всем участникам.

Каждый челлендж из «Моих достижений», у которого счётчик есть смысл сравнивать
между людьми, — отдельный рейтинг со своим адресом (/ratings/p-index и т.д.) и
пунктом меню (решения Дмитрия 26–27.10.2026):

* в зачёте ВСЕ участники протоколов, а не только зарегистрированные на сайте —
  как в остальных рейтингах. Системы одного человека склеиваются по аккаунту
  сайта (leaderboard_service._entity_key), остальные — строкой на систему.
  Безымянные заглушки протоколов («Неизвестный», «Неизвестный бегун», ключи
  anon:/unknown:) — не люди и в зачёт не идут;
* фильтр систем: без фильтра — все системы вместе, с фильтром значение и дата
  пересчитываются только по этой системе (это отдельный рейтинг, а не колонка);
  фильтр «Активные» — только те, кто выходил на старт за последний год;
* у каждой строки — дата, когда человек впервые набрал своё текущее значение.
  При равном значении выше тот, кто дошёл до него раньше; одинаковы и значение,
  и дата — место общее. У p- и V-индекса между значением и датой стоит ещё
  «сколько осталось до следующего»: кому меньше, тот выше;
* медалей в таблице нет — только значение;
* варианты счёта: серия — любая активность / пробежки / волонтёрство / текущая;
  «Нумератор» — №1–200 / №201–400; Уилсон — цепочка с №1 / самая длинная;
  «Ударный год» — лучший год / этот год. У «60 секунд», «Круглого года» и
  «Алфавита» — колонка того, чего ещё не хватает.

Счёт ведут те же функции, что у карточки челленджа (app.challenge_growth):
рейтинг не может разойтись с тем, что человек видит у себя в кабинете. Отсечки
данных — как у остальных рейтингов: тестовые старты, вторые протоколы
кросслинков, недопущенные parkrun-участники (leaderboard_service).

Посчитать всех — пара минут одним проходом по протоколам, в запросе так не
сделать. Поэтому сетка «челлендж × вариант × система × активность»
пересчитывается фоном (задача challenge_ratings.rebuild — по расписанию и после
синков) и лежит готовыми снимками в таблице challenge_rating_snapshots. В
снимке — верх таблицы и строки всех зарегистрированных на сайте: по ним
отвечает «Вы» без пересчёта.
"""

from __future__ import annotations

import heapq
import json
import logging
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from itertools import count
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, literal_column, select, text, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.challenge_growth import (
    best_year_growth,
    calendar_day_key,
    current_streak_growth,
    first_dates,
    hirsch_growth,
    hirsch_shortfall,
    position_key,
    seconds_key,
    start_number_key,
    streak_growth,
    wilson_floating_growth,
    wilson_growth,
)
from app.models import ChallengeRatingSnapshot, Location, Participant, Platform, User
from app.participant_identity import is_anonymous_participant
from app.services.achievements_service import (
    _RU_ALPHABET,
    START_NUMBER_RANGES,
    START_NUMBER_TITLES,
    _first_letter,
)
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.leaderboard_service import (
    _COUNTED_RUN_ROW,
    _PARKRUN_ELIGIBLE_CTE,
    _PARKRUN_ELIGIBLE_EXISTS,
    PLATFORM_COLUMNS,
    PLATFORM_LABELS,
    REFRESH_INTERVAL_HOURS,
    _location_identity_maps,
    _row_key,
    _site_links,
    _SiteLink,
    latest_event_date,
)
from app.services.location_catalog_service import LocationCatalogIndex
from app.volunteer_role_taxonomy import canonical_volunteer_role
from app.volunteering_occasions import volunteer_occasion_dates

logger = logging.getLogger(__name__)

# Сколько строк держит таблица — как у остальных рейтингов (TOP_LIMIT).
TOP_LIMIT = 1000
# Сколько строк отдаёт карточка челленджа на хабе /ratings.
HUB_TOP_N = 3
# «Активные» — кто выходил на старт (бегом или волонтёром) за столько дней до
# последнего старта базы. Без этого верх общего зачёта занимает архив parkrun
# России до 2022 года (решение Дмитрия 27.10.2026, Р35).
ACTIVE_DAYS = 365

ALL = "all"
ACTIVITY_ALL = "all"
ACTIVITY_ACTIVE = "active"
ACTIVITIES = (ACTIVITY_ALL, ACTIVITY_ACTIVE)
PARKRUN = "parkrun"
# parkrun: волонтёрства приходят сводкой профиля без даты и локации — V-индексу
# и волонтёрской серии считать нечего (то же решение, что у карточки, 07.09.2026).
_NO_PARKRUN = tuple(code for code in PLATFORM_COLUMNS if code != PARKRUN)

# Срез parkrun — по сути архив: в России parkrun закрыт с марта 2022, дальше
# приходят только зарубежные старты зарегистрированных из их профилей.
PARKRUN_SCOPE_NOTE = (
    "parkrun в России не проводится с марта 2022 года: здесь архив российских "
    "стартов и зарубежные старты зарегистрированных на сайте. Недельных сдвигов "
    "в этом срезе почти не бывает, поэтому они не показываются."
)


@dataclass(frozen=True)
class RatingVariant:
    """Вариант счёта одного челленджа — переключатель над таблицей."""

    key: str
    label: str
    # Системы варианта, если отличаются от челленджа: у волонтёрской серии нет
    # parkrun — его волонтёрства приходят сводкой без дат.
    platforms: tuple[str, ...] | None = None
    # Формат колонки даты, если отличается от челленджа.
    reached_format: str | None = None


MAIN = RatingVariant(key="main", label="")


@dataclass(frozen=True)
class RatingChallenge:
    """Челлендж, у которого есть рейтинг.

    code — код челленджа в achievements_service, slug — сегмент адреса
    /ratings/{slug} и ключ пункта меню (RATING_GROUPS в
    frontend/src/features/portal/nav/siteNav.ts).
    """

    code: str
    slug: str
    title: str
    icon: str
    # Подпись колонки значения в таблице.
    value_label: str
    # Склонение значения рядом с числом: «7 недель», «1 неделя».
    unit_forms: tuple[str, str, str] | None
    description: str
    platforms: tuple[str, ...] = PLATFORM_COLUMNS
    variants: tuple[RatingVariant, ...] = (MAIN,)
    # Подпись переключателя вариантов.
    variant_label: str | None = None
    # Индексы Хирша: при равном значении выше тот, кому меньше осталось до
    # следующего. Склонение — того, чего не хватает.
    to_next_forms: tuple[str, str, str] | None = None
    # Колонка даты: date — день достижения, year — только год.
    reached_format: str = "date"
    # Колонка «Не хватает»: секунды, даты календаря или буквы.
    missing_kind: str | None = None
    # Колонка «Старты»: от какого до какого номера закрыта цепочка (Уилсон).
    shows_chain: bool = False


# Чего не хватает до полной коллекции — держим в снимке не больше стольких
# значений на строку: «Круглому году» новичка недостаёт трёх сотен дат, а
# смотрят ближайшие. Число всех недостающих приходит рядом.
MISSING_LIMIT = 30

_START_RANGES = {
    "base": START_NUMBER_RANGES["start_numbers"],
    "pro": START_NUMBER_RANGES["start_numbers_pro"],
}

_HIRSCH_NOTE = (
    "При равном индексе выше тот, кому меньше осталось до следующего, при равном "
    "остатке — кто набрал индекс раньше, а если и дата одна — кто раньше дошёл до "
    "предыдущих ступеней. "
)
_DATE_NOTE = (
    "При равном значении выше тот, кто набрал его раньше, а если и дата одна — "
    "кто раньше дошёл до предыдущих ступеней. "
)
_SITE_NOTE = "Системы одного человека складываются, если он зарегистрирован на сайте."

# Порядок — как в меню. Названия — те же, что у карточек челленджей: человек
# узнаёт рейтинг по своей карточке в «Моих достижениях».
RATING_CHALLENGES: tuple[RatingChallenge, ...] = (
    RatingChallenge(
        code="p_index",
        slug="p-index",
        title="p-индекс",
        icon="🧮",
        value_label="p-индекс",
        unit_forms=None,
        description=(
            "p локаций, в каждой из которых минимум p финишей: индекс растёт, "
            "когда человек возвращается на разные локации, а не копит пробежки на одной."
        ),
        to_next_forms=("финиш", "финиша", "финишей"),
    ),
    RatingChallenge(
        code="v_index",
        slug="v-index",
        title="V-индекс",
        icon="🧰",
        value_label="V-индекс",
        unit_forms=None,
        description=(
            "V локаций, на каждой минимум по V волонтёрств. parkrun не участвует: "
            "его волонтёрства приходят сводкой ролей, без локации и даты."
        ),
        platforms=_NO_PARKRUN,
        to_next_forms=("волонтёрство", "волонтёрства", "волонтёрств"),
    ),
    RatingChallenge(
        code="wilson",
        slug="wilson",
        title="Индекс Уилсона",
        icon="⛓️",
        value_label="Индекс",
        unit_forms=None,
        description=(
            "Длина цепочки номеров стартов подряд. Классический индекс считается "
            "строго с №1, плавающий — самая длинная цепочка с любого номера. "
            "Номер засчитывается в любой системе."
        ),
        variants=(
            RatingVariant(key="classic", label="С №1"),
            RatingVariant(key="floating", label="Самая длинная"),
        ),
        variant_label="Цепочка",
        shows_chain=True,
    ),
    RatingChallenge(
        code="streak",
        slug="streak",
        title="Серийный бегун",
        icon="🔥",
        value_label="Серия",
        unit_forms=("неделя", "недели", "недель"),
        description=(
            "Самая длинная серия недель подряд с пробежкой или волонтёрством — или "
            "только с чем-то одним; «Текущая» — серия, которая идёт сейчас. Неделя "
            "засчитывается, даже если старт перенесли на другой день."
        ),
        variants=(
            RatingVariant(key="any", label="Всё"),
            RatingVariant(key="runs", label="Пробежки"),
            RatingVariant(key="volunteering", label="Волонтёрство", platforms=_NO_PARKRUN),
            RatingVariant(key="current", label="Текущая"),
        ),
        variant_label="Что считаем",
    ),
    RatingChallenge(
        code="best_year",
        slug="best-year",
        title="Ударный год",
        icon="📈",
        value_label="Пробежек за год",
        unit_forms=("пробежка", "пробежки", "пробежек"),
        description=(
            "Больше всего пробежек за один календарный год — за лучший год или за "
            "текущий: гонка этого года обновляется каждую субботу."
        ),
        variants=(
            RatingVariant(key="best", label="Лучший год"),
            RatingVariant(key="this_year", label="Этот год", reached_format="date"),
        ),
        variant_label="Какой год",
        reached_format="year",
    ),
    RatingChallenge(
        code="seconds",
        slug="seconds",
        title="60 секунд",
        icon="⏱️",
        value_label="Секунд",
        unit_forms=("секунда", "секунды", "секунд"),
        description="Сколько разных секунд на часах финиша собрано — от :00 до :59.",
        missing_kind="seconds",
    ),
    RatingChallenge(
        code="positions",
        slug="positions",
        title="Бинго позиций",
        icon="🎯",
        value_label="Позиций",
        unit_forms=("позиция", "позиции", "позиций"),
        description=(
            "Сколько разных окончаний места собрано — от 00 до 99: 18-е, 118-е "
            "и 218-е место закрывают одну клетку «18»."
        ),
    ),
    RatingChallenge(
        code="calendar_days",
        slug="calendar-days",
        title="Круглый год",
        icon="🗓️",
        value_label="Дат",
        unit_forms=("дата", "даты", "дат"),
        description="Сколько разных дат календаря закрыто пробежками — из 366, год не важен.",
        missing_kind="calendar_days",
    ),
    RatingChallenge(
        code="alphabet",
        slug="alphabet",
        title="Алфавит",
        icon="🔤",
        value_label="Букв",
        unit_forms=("буква", "буквы", "букв"),
        description=(
            "Сколько букв алфавита собрано первыми буквами названий локаций, где "
            "человек финишировал. В общем зачёте parkrun не считается: названия у "
            "него латиницей; под фильтром системы — только буквы, которые в ней есть."
        ),
        missing_kind="alphabet",
    ),
    RatingChallenge(
        code="start_numbers",
        slug="start-numbers",
        title=START_NUMBER_TITLES["start_numbers"],
        icon="🔢",
        value_label="Номеров",
        unit_forms=("номер", "номера", "номеров"),
        description=(
            "Сколько разных номеров стартов собрано: №1–200 в «Нумераторе», "
            "№201–400 в «Нумераторе ПРО» — неважно, в какой системе получен каждый номер."
        ),
        variants=(
            RatingVariant(key="base", label="№1–200"),
            RatingVariant(key="pro", label=f"№201–400 · {START_NUMBER_TITLES['start_numbers_pro'].split()[-1]}"),
        ),
        variant_label="Диапазон",
    ),
)

CHALLENGES_BY_SLUG: dict[str, RatingChallenge] = {item.slug: item for item in RATING_CHALLENGES}
CHALLENGES_BY_CODE: dict[str, RatingChallenge] = {item.code: item for item in RATING_CHALLENGES}


def ranking_note(challenge: RatingChallenge) -> str:
    return (_HIRSCH_NOTE if challenge.to_next_forms else _DATE_NOTE) + _SITE_NOTE


def variant_of(challenge: RatingChallenge, key: str | None) -> RatingVariant:
    """Неизвестный вариант — первый, как и незнакомая система — общий зачёт."""
    return next((variant for variant in challenge.variants if variant.key == key), challenge.variants[0])


def platform_filter_values(challenge: RatingChallenge, variant: RatingVariant | None = None) -> tuple[str, ...]:
    platforms = variant.platforms if variant is not None and variant.platforms is not None else challenge.platforms
    return (ALL, *platforms)


def normalize_platform(challenge: RatingChallenge, platform: str, variant: RatingVariant | None = None) -> str:
    """Неизвестная или неприменимая система — общий зачёт, а не ошибка: старая
    или набранная руками ссылка должна открывать рейтинг."""
    return platform if platform in platform_filter_values(challenge, variant) else ALL


def normalize_activity(activity: str | None) -> str:
    return activity if activity in ACTIVITIES else ACTIVITY_ALL


def reached_format(challenge: RatingChallenge, variant: RatingVariant) -> str:
    return variant.reached_format or challenge.reached_format


BoardKey = tuple[str, str, str, str]


def board_keys() -> list[BoardKey]:
    """Вся сетка снимков: челлендж × вариант × срез × активность."""
    return [
        (item.code, variant.key, scope, activity)
        for item in RATING_CHALLENGES
        for variant in item.variants
        for scope in platform_filter_values(item, variant)
        for activity in ACTIVITIES
    ]


# ---------------------------------------------------------------------------
# Счёт одного участника


@dataclass(frozen=True, slots=True)
class _Run:
    platform: str
    event_date: date
    location: str
    finish_time_sec: int | None
    position: int | None
    event_number: int | None
    # Первая буква названия локации (как его показывает карточка) — для «Алфавита».
    letter: str | None = None


@dataclass(frozen=True, slots=True)
class _Volunteering:
    platform: str
    event_date: date
    location: str
    # Роль узнана таксономией: V-индекс считает только такие строки, как и
    # карточка (_collect_volunteer_role_rows), а серия — любые.
    role_known: bool


@dataclass
class _Metric:
    """Значение челленджа у одного участника в одном срезе."""

    growth: list[date]
    # Индексы Хирша: сколько не хватает до индекса +1 — сейчас и неделю назад,
    # и визиты по локациям (из них строится подсказка «где добрать»).
    to_next: int | None = None
    prev_to_next: int | None = None
    counts: dict[str, int] | None = None
    # Коллекции с колонкой «Не хватает»: что уже собрано. Сам список
    # недостающего строится только для строк, попавших в снимок.
    keys: set[Any] | None = None
    # Уилсон: первый номер цепочки (последний — первый + значение − 1).
    chain_start: int | None = None
    # Серии: лучшая серия ещё идёт.
    ongoing: bool | None = None
    # «Ударный год»: сколько пробежек в текущем году.
    this_year: int | None = None


@dataclass
class _ScopeData:
    """Срез одного участника: значения челленджей и дата последнего выхода."""

    metrics: dict[tuple[str, str], _Metric]
    last_seen: date | None


def _hirsch_metric(visits: Sequence[tuple[date, str]], week_start: date) -> _Metric:
    growth = hirsch_growth(visits)
    counts: dict[str, int] = {}
    prev_counts: dict[str, int] = {}
    for event_date, location in visits:
        counts[location] = counts.get(location, 0) + 1
        if event_date < week_start:
            prev_counts[location] = prev_counts.get(location, 0) + 1
    prev_value = bisect_left(growth, week_start)
    return _Metric(
        growth=growth,
        to_next=hirsch_shortfall(counts.values(), len(growth) + 1),
        prev_to_next=hirsch_shortfall(prev_counts.values(), prev_value + 1),
        counts=counts,
    )


def _wilson_floating_metric(runs: Sequence[_Run]) -> _Metric:
    growth, start = wilson_floating_growth((run.event_date, run.event_number) for run in runs)
    return _Metric(growth=growth, chain_start=start)


def _collection_metric(keyed_dates: Iterable[tuple[Any, date]]) -> _Metric:
    pairs = [(key, value) for key, value in keyed_dates if key is not None]
    return _Metric(growth=first_dates(pairs), keys={key for key, _value in pairs})


@dataclass(frozen=True)
class _Context:
    """Общее для всех участников: даты отсечек и каталог букв по срезам."""

    week_start: date
    latest: date
    alphabet: dict[str, frozenset[str]]


def _alphabet_metric(runs: Sequence[_Run], scope: str, context: _Context) -> _Metric:
    """«Алфавит» — как карточка (_alphabet_challenge): в общем зачёте parkrun не
    считается, буква засчитывается, только если она есть в каталоге среза."""
    available = context.alphabet.get(scope, frozenset())
    return _collection_metric(
        (run.letter if run.letter in available else None, run.event_date)
        for run in runs
        if not (scope == ALL and run.platform == PARKRUN)
    )


def _scope_metrics(
    runs: Sequence[_Run], vols: Sequence[_Volunteering], scope: str, context: _Context
) -> _ScopeData:
    """Все челленджи рейтинга по одному набору строк: (код, вариант) → значение.

    Строки — по возрастанию даты. Правило каждого челленджа — в
    app.challenge_growth, здесь только выбор полей.
    """
    run_dates = [run.event_date for run in runs]
    vols_by_platform: dict[str, list[tuple[date, str]]] = {}
    for vol in vols:
        vols_by_platform.setdefault(vol.platform, []).append((vol.event_date, vol.location))
    vol_dates: set[date] = set()
    for platform, rows in vols_by_platform.items():
        vol_dates |= volunteer_occasion_dates(platform, rows)
    shifts = sorted(
        {(vol.event_date, vol.location) for vol in vols if vol.role_known and vol.platform != PARKRUN}
    )
    any_dates = set(run_dates) | vol_dates
    metrics: dict[tuple[str, str], _Metric] = {
        ("p_index", "main"): _hirsch_metric([(run.event_date, run.location) for run in runs], context.week_start),
        ("v_index", "main"): _hirsch_metric(shifts, context.week_start),
        ("wilson", "classic"): _Metric(
            wilson_growth((run.event_date, run.event_number) for run in runs), chain_start=1
        ),
        ("wilson", "floating"): _wilson_floating_metric(runs),
        ("seconds", "main"): _collection_metric((seconds_key(run.finish_time_sec), run.event_date) for run in runs),
        ("positions", "main"): _Metric(first_dates((position_key(run.position), run.event_date) for run in runs)),
        ("calendar_days", "main"): _collection_metric(
            (calendar_day_key(run.event_date), run.event_date) for run in runs
        ),
        ("alphabet", "main"): _alphabet_metric(runs, scope, context),
    }
    current = current_streak_growth(any_dates, context.latest)
    metrics[("streak", "current")] = _Metric(current)
    for variant, dates in (("any", any_dates), ("runs", run_dates), ("volunteering", vol_dates)):
        best = streak_growth(dates)
        live = current_streak_growth(dates, context.latest) if variant != "any" else current
        metrics[("streak", variant)] = _Metric(best, ongoing=bool(live) and len(live) == len(best))
    this_year = [value for value in run_dates if value.year == context.latest.year]
    metrics[("best_year", "best")] = _Metric(best_year_growth(run_dates), this_year=len(this_year))
    metrics[("best_year", "this_year")] = _Metric(this_year, this_year=len(this_year))
    for variant, (low, high) in _START_RANGES.items():
        metrics[("start_numbers", variant)] = _Metric(
            first_dates((start_number_key(run.event_number, low, high), run.event_date) for run in runs)
        )
    return _ScopeData(metrics=metrics, last_seen=max(any_dates) if any_dates else None)


def entity_metrics(
    runs: Sequence[_Run], vols: Sequence[_Volunteering], context: _Context
) -> dict[str, _ScopeData]:
    """Значения по каждому срезу: общий зачёт и каждая система человека.

    У человека с одной системой общий зачёт и срез этой системы совпадают —
    считаем один раз (так у подавляющего большинства участников). Кроме
    «Алфавита»: в общем зачёте он не видит parkrun и сверяется с другим
    каталогом букв.
    """
    platforms = sorted({run.platform for run in runs} | {vol.platform for vol in vols})
    overall = _scope_metrics(runs, vols, ALL, context)
    by_scope: dict[str, _ScopeData] = {ALL: overall}
    if len(platforms) == 1:
        only = platforms[0]
        metrics = dict(overall.metrics)
        metrics[("alphabet", "main")] = _alphabet_metric(runs, only, context)
        by_scope[only] = _ScopeData(metrics=metrics, last_seen=overall.last_seen)
        return by_scope
    for platform in platforms:
        by_scope[platform] = _scope_metrics(
            [run for run in runs if run.platform == platform],
            [vol for vol in vols if vol.platform == platform],
            platform,
            context,
        )
    return by_scope


# Все дни календаря в порядке високосного года — «Круглый год» знает 29 февраля.
_CALENDAR = [
    (day.month, day.day) for day in (date(2024, 1, 1) + timedelta(days=offset) for offset in range(366))
]


def _missing(kind: str, keys: set[Any], anchor: date, alphabet: frozenset[str] = frozenset()) -> tuple[list[Any], int]:
    """Чего не хватает до полной коллекции: (первые MISSING_LIMIT, всего).

    Секунды — по порядку часов, буквы — по алфавиту (только буквы каталога
    среза). Даты — от ближайшей после последнего старта базы: так наверху те,
    что можно закрыть в ближайшие недели, а прошедшие в этом году уезжают в
    хвост — до них теперь целый год.
    """
    if kind == "seconds":
        missing: list[Any] = [second for second in range(60) if second not in keys]
    elif kind == "alphabet":
        missing = [letter for letter in _RU_ALPHABET if letter in alphabet and letter not in keys]
    else:
        start = _CALENDAR.index((anchor.month, anchor.day)) + 1 if (anchor.month, anchor.day) in _CALENDAR else 0
        ordered = _CALENDAR[start:] + _CALENDAR[:start]
        missing = [f"{month:02d}-{day:02d}" for month, day in ordered if (month, day) not in keys]
    return missing[:MISSING_LIMIT], len(missing)


def _to_next_plan(counts: dict[str, int], value: int, names: dict[str, str]) -> list[dict[str, Any]]:
    """Где добрать до индекса +1 — дешевейшим путём, как hirsch_shortfall.

    Добираем value+1 самых обжитых локаций до value+1 визитов. Локаций меньше
    нужного — недостающие стоят полные value+1 визитов на новых локациях.
    """
    target = value + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], names.get(item[0], item[0])))[:target]
    plan: list[dict[str, Any]] = [
        {"location": names.get(location, location), "add": target - visits}
        for location, visits in ranked
        if visits < target
    ]
    plan.sort(key=lambda step: step["add"])
    new_locations = target - len(ranked)
    if new_locations > 0:
        plan.append({"location": None, "add": target, "count": new_locations})
    return plan


# ---------------------------------------------------------------------------
# Накопление сетки снимков


@dataclass
class _Candidate:
    entity_key: str
    rank_key: int
    prev_key: int | None
    value: int
    reached: date
    prev_value: int
    prev_reached: date | None
    to_next: int | None
    prev_to_next: int | None
    platforms: dict[str, int] | None
    missing: list[Any] | None
    missing_total: int | None
    chain_start: int | None
    ongoing: bool | None
    this_year: int | None
    last_seen: date | None
    to_next_plan: list[dict[str, Any]] | None
    user_id: UUID | None
    participant_ids: tuple[UUID, ...]
    site_name: str | None
    site_serial_id: int | None


# Сколько предыдущих ступеней лестницы разбивают общие места (Р34, решение
# Дмитрия 27.10.2026): при равных значении, остатке и дате выше тот, кто раньше
# дошёл до значения−1, затем −2. На стенде это убрало общие места в «60
# секундах» и «Бинго» почти целиком, в p-индексе — с 375 строк топа до единиц.
# Две ступени — потолок 64-битного ключа (см. _rank_key).
TIE_STEPS = 2

# Ключ места — одно число, упакованное так, что МЕНЬШЕ = ВЫШЕ в таблице:
# [1023 − значение : 10 бит][остаток до следующего : 10][день набора : 14]
# [дни двух предыдущих ступеней : 14 + 14]. День — номер дня от 01.01.2004
# (parkrun в профилях начинается с 2004-го; 14 бит хватит до 2048 года).
# Число вместо кортежа — ради памяти: ключ хранится у каждого участника в
# каждом снимке (миллионы штук), и кортежи из шести чисел раздували проход до
# 1,5 ГБ, а массив по 8 байт — нет.
_DAY_ZERO = date(2004, 1, 1).toordinal()
_DAY_MAX = (1 << 14) - 1
_FIELD_MAX = (1 << 10) - 1


def _day(value: date) -> int:
    return min(max(value.toordinal() - _DAY_ZERO, 0), _DAY_MAX)


def _rank_key(growth: Sequence[date], tiebreak: int) -> int:
    """Ключ места по лестнице. Ступеней меньше, чем TIE_STEPS, — недостающие
    нулями: у равных по значению длина лестницы одна, сравнение честное."""
    key = _FIELD_MAX - min(len(growth), _FIELD_MAX)
    key = (key << 10) | min(tiebreak, _FIELD_MAX)
    key = (key << 14) | _day(growth[-1])
    for offset in range(1, TIE_STEPS + 1):
        key = (key << 14) | (_day(growth[-1 - offset]) if offset < len(growth) else 0)
    return key


@dataclass
class _Board:
    """Один снимок. Полное распределение — массив ключей места всех строк (по
    8 байт): отсортированный, он даёт место любой строки двоичным поиском, не
    держа в памяти самих участников."""

    keys: array = field(default_factory=lambda: array("Q"))
    prev_keys: array = field(default_factory=lambda: array("Q"))
    # Мин-куча верха таблицы: худшая строка — в корне. Ключ кучи — ключ места
    # с обратным знаком (больше — лучше) и «зарегистрирован»: при полной ничьей
    # на границе топа в таблицу проходит человек с сайта, — иначе ему с тем же
    # местом писалось бы «вы пока ниже».
    top: list[tuple[Any, ...]] = field(default_factory=list)
    site: list[_Candidate] = field(default_factory=list)


def _ranker(keys: array) -> Callable[[int], int]:
    """Место по ключу: 1 + сколько строк строго выше (ключ меньше)."""
    ordered = array("Q", sorted(keys))

    def rank(key: int) -> int:
        return bisect_left(ordered, key) + 1

    return rank


class _GridBuilder:
    def __init__(
        self,
        week_start: date,
        anchor: date | None = None,
        *,
        alphabet: dict[str, frozenset[str]] | None = None,
        location_names: dict[str, str] | None = None,
    ) -> None:
        latest = anchor or week_start
        self.context = _Context(week_start=week_start, latest=latest, alphabet=alphabet or {})
        self.active_since = latest - timedelta(days=ACTIVE_DAYS)
        self.location_names = location_names or {}
        self.boards: dict[BoardKey, _Board] = {key: _Board() for key in board_keys()}
        self._seq = count()

    @property
    def week_start(self) -> date:
        return self.context.week_start

    def add(
        self,
        entity_key: str,
        participant_ids: tuple[UUID, ...],
        runs: Sequence[_Run],
        vols: Sequence[_Volunteering],
        link: _SiteLink | None,
    ) -> None:
        if not runs and not vols:
            return
        by_scope = entity_metrics(runs, vols, self.context)
        for code, variant, scope, activity in self.boards:
            data = by_scope.get(scope)
            if data is None:
                continue
            if activity == ACTIVITY_ACTIVE and (data.last_seen is None or data.last_seen < self.active_since):
                continue
            metric = data.metrics.get((code, variant))
            if metric is None or not metric.growth:
                continue
            self._place((code, variant, scope, activity), entity_key, participant_ids, metric, data, by_scope, link)

    def _place(
        self,
        key: BoardKey,
        entity_key: str,
        participant_ids: tuple[UUID, ...],
        metric: _Metric,
        data: _ScopeData,
        by_scope: dict[str, _ScopeData],
        link: _SiteLink | None,
    ) -> None:
        code, variant, scope, _activity = key
        board = self.boards[key]
        growth = metric.growth
        value = len(growth)
        reached = growth[-1]
        rank_key = _rank_key(growth, metric.to_next or 0)
        board.keys.append(rank_key)
        # Неделя назад: сколько шагов лестницы случилось до начала окна дельты.
        prev_value = bisect_left(growth, self.week_start)
        prev_reached = growth[prev_value - 1] if prev_value else None
        prev_key: int | None = None
        if prev_value:
            prev_key = _rank_key(growth[:prev_value], metric.prev_to_next or 0)
            board.prev_keys.append(prev_key)

        is_site = link is not None
        heap_key = (-rank_key, int(is_site))
        fits_top = len(board.top) < TOP_LIMIT or heap_key > board.top[0][:2]
        if not (fits_top or is_site):
            return
        item = CHALLENGES_BY_CODE[code]
        platforms = None
        if scope == ALL:
            platforms = {}
            for platform in platform_filter_values(item, variant_of(item, variant))[1:]:
                other = by_scope.get(platform)
                other_metric = other.metrics.get((code, variant)) if other is not None else None
                if other_metric is not None and other_metric.growth:
                    platforms[platform] = len(other_metric.growth)
        missing: list[Any] | None = None
        missing_total: int | None = None
        if item.missing_kind and metric.keys is not None:
            missing, missing_total = _missing(
                item.missing_kind, metric.keys, self.context.latest, self.context.alphabet.get(scope, frozenset())
            )
        plan = None
        if is_site and metric.counts is not None:
            plan = _to_next_plan(metric.counts, value, self.location_names)
        candidate = _Candidate(
            entity_key=entity_key,
            rank_key=rank_key,
            prev_key=prev_key,
            value=value,
            reached=reached,
            prev_value=prev_value,
            prev_reached=prev_reached,
            to_next=metric.to_next,
            prev_to_next=metric.prev_to_next,
            platforms=platforms,
            missing=missing,
            missing_total=missing_total,
            chain_start=metric.chain_start,
            ongoing=metric.ongoing,
            this_year=metric.this_year,
            last_seen=data.last_seen,
            to_next_plan=plan,
            user_id=link.user_id if link else None,
            participant_ids=participant_ids,
            site_name=link.display_name if link and not link.private else None,
            site_serial_id=link.serial_id if link and not link.private else None,
        )
        if is_site:
            board.site.append(candidate)
        if fits_top:
            entry = (*heap_key, next(self._seq), candidate)
            if len(board.top) < TOP_LIMIT:
                heapq.heappush(board.top, entry)
            else:
                heapq.heapreplace(board.top, entry)


def _row_payload(
    scope: str,
    candidate: _Candidate,
    ranks: Callable[[int], int],
    prev_ranks: Callable[[int], int],
    display_name: str | None,
    *,
    with_plan: bool = False,
) -> dict[str, Any]:
    rank = ranks(candidate.rank_key)
    prev_rank = prev_ranks(candidate.prev_key) if candidate.prev_key is not None else None
    row: dict[str, Any] = {
        "rank": rank,
        # Новичок недели (неделю назад значения не было) — без стрелки.
        "rank_delta": prev_rank - rank if prev_rank is not None else None,
        "row_key": _row_key(candidate.entity_key),
        "display_name": display_name,
        "site_serial_id": candidate.site_serial_id,
        "value": candidate.value,
        "value_delta": candidate.value - candidate.prev_value,
        "reached_on": candidate.reached.isoformat(),
        "last_seen": candidate.last_seen.isoformat() if candidate.last_seen else None,
    }
    if candidate.to_next is not None:
        row["to_next"] = candidate.to_next
    if candidate.missing is not None:
        row["missing"] = candidate.missing
        row["missing_total"] = candidate.missing_total
    if candidate.chain_start is not None:
        row["chain_from"] = candidate.chain_start
        row["chain_to"] = candidate.chain_start + candidate.value - 1
    if candidate.ongoing is not None:
        row["ongoing"] = candidate.ongoing
    if candidate.this_year is not None:
        row["this_year"] = candidate.this_year
    if with_plan and candidate.to_next_plan is not None:
        row["to_next_plan"] = candidate.to_next_plan
    if scope == ALL:
        row["platforms"] = candidate.platforms or {}
    return row


# ---------------------------------------------------------------------------
# Чтение базы

_RUN_ROWS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
SELECT
    rr.participant_id,
    p.code,
    e.event_date,
    e.location_id,
    rr.finish_time_sec,
    rr.position,
    e.event_number
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND e.event_date > DATE '1970-01-01'
{_COUNTED_RUN_ROW}
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
ORDER BY rr.participant_id, e.event_date, e.location_id
"""
)

# Волонтёрства с датой (сводка parkrun лежит на 1970-01-01 и сюда не идёт).
# Второй протокол кросслинка у непривязанного участника — это та же суббота
# под другой личностью системы; у привязанного он схлопнется сам: пара «дата ×
# локация каталога» у него одна.
_VOLUNTEER_ROWS_SQL = """
SELECT
    vr.participant_id,
    p.code,
    e.event_date,
    e.location_id,
    vr.role
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND e.event_date > DATE '1970-01-01'
  AND (
    ec.secondary_event_id IS NULL
    OR EXISTS (SELECT 1 FROM platform_links pl WHERE pl.participant_id = vr.participant_id)
  )
"""

_STREAM_BATCH = 20_000


def _is_placeholder(external_user_id: str | None, display_name: str | None) -> bool:
    """Безымянная строка протокола, а не человек: правило участников
    (app.participant_identity — ключи anon:/unknown:, «Неизвестный бегун»…) и
    прежнее правило рейтингов (co_runners_service — «Неизвестный», «unknown #…»)."""
    return is_anonymous_participant(external_user_id, display_name) or _is_unknown_participant_name(display_name)


def _unknown_participants(db: Session) -> set[UUID]:
    """Заглушки протоколов: в рейтинг не идут (решение Дмитрия 27.10.2026)."""
    unknown: set[UUID] = set()
    result = db.execute(
        select(Participant.id, Participant.external_user_id, Participant.display_name).execution_options(
            stream_results=True, yield_per=_STREAM_BATCH
        )
    )
    for pid, external_user_id, name in result:
        if _is_placeholder(external_user_id, name):
            unknown.add(pid)
    return unknown


@dataclass
class _LocationMaps:
    identity: dict[UUID, str]
    names: dict[str, str]
    letters: dict[UUID, str]
    alphabet: dict[str, frozenset[str]]


def _location_maps(db: Session) -> _LocationMaps:
    """Идентичность и имя локации (как у остальных рейтингов) плюс буквы
    «Алфавита» — тем же правилом, что карточка: буква засчитывается по
    названию, которое показывает сайт, а каталог букв среза — по названиям
    локаций самой системы (_alphabet_available_names)."""
    identity, names, _slugs = _location_identity_maps(db)
    catalog = LocationCatalogIndex(db)
    letters: dict[UUID, str] = {}
    available: dict[str, set[str]] = {ALL: set()}
    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    for location, platform_code in rows:
        shown = _first_letter(catalog.display_name(location, platform_code) or "")
        if shown is not None and shown in _RU_ALPHABET:
            letters[location.id] = shown
        if location.name is None:
            continue
        raw = _first_letter(location.name)
        if raw is None or raw not in _RU_ALPHABET:
            continue
        available.setdefault(platform_code, set()).add(raw)
        if platform_code != PARKRUN:
            available[ALL].add(raw)
    return _LocationMaps(
        identity=identity,
        names=names,
        letters=letters,
        alphabet={scope: frozenset(values) for scope, values in available.items()},
    )


def _volunteer_rows(db: Session, identity: dict[UUID, str]) -> dict[UUID, list[_Volunteering]]:
    role_known: dict[str | None, bool] = {}
    by_pid: dict[UUID, list[_Volunteering]] = {}
    for pid, platform, event_date, location_id, role in db.execute(text(_VOLUNTEER_ROWS_SQL)):
        known = role_known.get(role)
        if known is None:
            known = canonical_volunteer_role(role) is not None
            role_known[role] = known
        by_pid.setdefault(pid, []).append(
            _Volunteering(
                platform=platform,
                event_date=event_date,
                location=identity.get(location_id, str(location_id)),
                role_known=known,
            )
        )
    for rows in by_pid.values():
        rows.sort(key=lambda row: row.event_date)
    return by_pid


def _stream_runs(db: Session, maps: _LocationMaps) -> Iterable[tuple[UUID, list[_Run]]]:
    """Пробежки участник за участником, по возрастанию даты — без загрузки
    двух с половиной миллионов строк в память разом."""
    result = db.execute(text(_RUN_ROWS_SQL).execution_options(stream_results=True, yield_per=_STREAM_BATCH))
    current: UUID | None = None
    runs: list[_Run] = []
    for pid, platform, event_date, location_id, finish, position, number in result:
        if pid != current:
            if current is not None:
                yield current, runs
            current, runs = pid, []
        runs.append(
            _Run(
                platform=platform,
                event_date=event_date,
                location=maps.identity.get(location_id, str(location_id)),
                finish_time_sec=finish,
                position=position,
                event_number=number,
                letter=maps.letters.get(location_id),
            )
        )
    if current is not None:
        yield current, runs


@dataclass
class _SiteEntity:
    participant_ids: list[UUID] = field(default_factory=list)
    runs: list[_Run] = field(default_factory=list)
    vols: list[_Volunteering] = field(default_factory=list)


def build_challenge_rating_grid(db: Session) -> dict[BoardKey, dict[str, Any]]:
    """Все снимки сетки одним проходом по протоколам."""
    latest = latest_event_date(db)
    built_at = datetime.now(timezone.utc).isoformat()
    if latest is None:
        return {key: _empty_snapshot(key, None, None, built_at) for key in board_keys()}
    week_start = latest - timedelta(days=6)

    links = _site_links(db)
    maps = _location_maps(db)
    unknown = _unknown_participants(db)
    vols_by_pid = _volunteer_rows(db, maps.identity)
    # Справочники прочитаны — отпускаем транзакцию: дальше один длинный
    # потоковый запрос, а между ним и записью снимков — только Python.
    db.rollback()

    grid = _GridBuilder(week_start, anchor=latest, alphabet=maps.alphabet, location_names=maps.names)
    site_entities: dict[UUID, _SiteEntity] = {}

    def _collect_site(pid: UUID, link: _SiteLink, runs: list[_Run], vols: list[_Volunteering]) -> None:
        entity = site_entities.setdefault(link.user_id, _SiteEntity())
        entity.participant_ids.append(pid)
        entity.runs.extend(runs)
        entity.vols.extend(vols)

    for pid, runs in _stream_runs(db, maps):
        vols = vols_by_pid.pop(pid, [])
        link = links.get(pid)
        if link is not None:
            _collect_site(pid, link, runs, vols)
        elif pid not in unknown:
            grid.add(f"p:{pid}", (pid,), runs, vols, None)
    db.rollback()

    # Волонтёры без единой пробежки — им есть что показать в V-индексе и серии.
    for pid, vols in vols_by_pid.items():
        link = links.get(pid)
        if link is not None:
            _collect_site(pid, link, [], vols)
        elif pid not in unknown:
            grid.add(f"p:{pid}", (pid,), [], vols, None)
    vols_by_pid.clear()

    # Зарегистрированные: системы человека — одна строка (как _entity_key).
    for user_id, entity in site_entities.items():
        entity.runs.sort(key=lambda run: run.event_date)
        entity.vols.sort(key=lambda vol: vol.event_date)
        link = links[entity.participant_ids[0]]
        grid.add(f"u:{user_id}", tuple(entity.participant_ids), entity.runs, entity.vols, link)

    names = _participant_names(db, grid)
    return {key: _snapshot(key, grid.boards[key], names, latest, week_start, built_at) for key in board_keys()}


def _participant_names(db: Session, grid: _GridBuilder) -> dict[UUID, str | None]:
    needed: set[UUID] = set()
    for board in grid.boards.values():
        for entry in board.top:
            needed.update(entry[-1].participant_ids)
        for candidate in board.site:
            needed.update(candidate.participant_ids)
    if not needed:
        return {}
    rows = db.execute(
        select(Participant.id, Participant.display_name).where(Participant.id.in_(list(needed)))
    ).all()
    return {pid: name for pid, name in rows}


def _display_name(candidate: _Candidate, names: dict[UUID, str | None]) -> str | None:
    """Имя строки — как у остальных рейтингов: у открытого профиля имя с сайта,
    иначе — из протокола (у приватного профиля ссылки на него нет)."""
    if candidate.site_name:
        return candidate.site_name
    for pid in candidate.participant_ids:
        name = names.get(pid)
        if not _is_placeholder("-", name):
            return name
    return None


def _empty_snapshot(key: BoardKey, latest: date | None, week_start: date | None, built_at: str) -> dict[str, Any]:
    code, variant, scope, activity = key
    return {
        "code": code,
        "variant": variant,
        "platform": scope,
        "activity": activity,
        "rows": [],
        "site_rows": {},
        "entrants": 0,
        "latest_event_date": latest,
        "week_start": week_start,
        "built_at": built_at,
    }


def _snapshot(
    key: BoardKey,
    board: _Board,
    names: dict[UUID, str | None],
    latest: date,
    week_start: date,
    built_at: str,
) -> dict[str, Any]:
    scope = key[2]
    ranks = _ranker(board.keys)
    prev_ranks = _ranker(board.prev_keys)
    top = [entry[-1] for entry in board.top]
    top.sort(key=lambda c: (c.rank_key, _display_name(c, names) or ""))
    rows = [_row_payload(scope, c, ranks, prev_ranks, _display_name(c, names)) for c in top]
    top_keys = {c.entity_key for c in top}
    site_rows: dict[str, dict[str, Any]] = {}
    for candidate in board.site:
        row = _row_payload(scope, candidate, ranks, prev_ranks, _display_name(candidate, names), with_plan=True)
        row["included"] = candidate.entity_key in top_keys
        site_rows[str(candidate.user_id)] = row
    snapshot = _empty_snapshot(key, latest, week_start, built_at)
    snapshot.update(rows=rows, site_rows=site_rows, entrants=len(board.keys))
    return snapshot


def rebuild_challenge_ratings(db: Session) -> dict[str, int]:
    """Пересчитать всю сетку и заменить снимки одной транзакцией.

    Возвращает число участников в каждом снимке — для журнала прогонов.
    """
    grid = build_challenge_rating_grid(db)
    db.execute(delete(ChallengeRatingSnapshot))
    for (code, variant, scope, activity), snapshot in grid.items():
        db.add(
            ChallengeRatingSnapshot(
                code=code,
                variant=variant,
                platform=scope,
                activity=activity,
                rows=snapshot["rows"],
                site_rows=snapshot["site_rows"],
                entrants=snapshot["entrants"],
                latest_event_date=snapshot["latest_event_date"],
                week_start=snapshot["week_start"],
                built_at=datetime.fromisoformat(snapshot["built_at"]),
            )
        )
    db.commit()
    return {":".join(key): int(snapshot["entrants"]) for key, snapshot in grid.items()}


# ---------------------------------------------------------------------------
# Витрина


def _meta(item: RatingChallenge, variant: RatingVariant, scope: str, activity: str) -> dict[str, Any]:
    return {
        "code": item.code,
        "slug": item.slug,
        "title": item.title,
        "icon": item.icon,
        "description": item.description,
        "ranking_note": ranking_note(item),
        "scope_note": PARKRUN_SCOPE_NOTE if scope == PARKRUN else None,
        # Недельные сдвиги в архивном срезе parkrun — шум из нулей.
        "show_deltas": scope != PARKRUN,
        "value_label": item.value_label,
        "unit_forms": list(item.unit_forms) if item.unit_forms else None,
        "to_next_forms": list(item.to_next_forms) if item.to_next_forms else None,
        "reached_format": reached_format(item, variant),
        "missing_kind": item.missing_kind,
        "shows_chain": item.shows_chain,
        "variant": variant.key,
        "variant_label": item.variant_label,
        # Переключатель вариантов — только там, где их больше одного.
        "variants": [
            {"key": option.key, "label": option.label, "platform_options": list(platform_filter_values(item, option))}
            for option in item.variants
            if len(item.variants) > 1
        ],
        "platform": scope,
        "platform_options": list(platform_filter_values(item, variant)),
        "platform_labels": {code: PLATFORM_LABELS[code] for code in item.platforms},
        "activity": activity,
        "active_days": ACTIVE_DAYS,
        "refresh_hours": REFRESH_INTERVAL_HOURS,
        "limit": TOP_LIMIT,
    }


def _resolve_slice(
    item: RatingChallenge, platform: str, variant: str | None, activity: str | None
) -> tuple[RatingVariant, str, str]:
    chosen = variant_of(item, variant)
    return chosen, normalize_platform(item, platform, chosen), normalize_activity(activity)


def _first_rows(limit: int) -> Any:
    """Первые limit строк таблицы — срезом в базе, не разбирая весь массив в
    Python. limit — целое из кода или проверенного Query, в SQL идёт числом."""
    path = literal_column(f"'$[0 to {max(int(limit), 1) - 1}]'::jsonpath")
    return func.jsonb_path_query_array(ChallengeRatingSnapshot.rows, path, type_=JSONB)


def _snapshot_filter(code: str, variant: str, scope: str, activity: str) -> Any:
    return (
        (ChallengeRatingSnapshot.code == code)
        & (ChallengeRatingSnapshot.variant == variant)
        & (ChallengeRatingSnapshot.platform == scope)
        & (ChallengeRatingSnapshot.activity == activity)
    )


def get_challenge_rating(
    db: Session,
    item: RatingChallenge,
    platform: str = ALL,
    limit: int | None = None,
    variant: str | None = None,
    activity: str | None = None,
) -> dict[str, Any]:
    chosen, scope, activity_key = _resolve_slice(item, platform, variant, activity)
    # Без site_rows: строки всех зарегистрированных этой витрине не нужны, а
    # весят столько же, сколько сама таблица.
    rows_column: Any = ChallengeRatingSnapshot.rows
    if limit is not None:
        rows_column = _first_rows(int(limit))
    record = db.execute(
        select(
            rows_column,
            ChallengeRatingSnapshot.entrants,
            ChallengeRatingSnapshot.latest_event_date,
            ChallengeRatingSnapshot.built_at,
        ).where(_snapshot_filter(item.code, chosen.key, scope, activity_key))
    ).one_or_none()
    payload = _meta(item, chosen, scope, activity_key)
    if record is None:
        # Снимков ещё нет (первый выкат до первого пересчёта) — пустая таблица
        # с честной подписью на витрине, а не 500.
        payload.update(rows=[], entrants=0, latest_event_date=None, built_at=None, ready=False)
        return payload
    rows, entrants, latest, built_at = record
    payload.update(rows=list(rows or []), entrants=entrants, latest_event_date=latest, built_at=built_at, ready=True)
    return payload


def get_my_challenge_row(
    db: Session,
    item: RatingChallenge,
    user: User,
    platform: str = ALL,
    variant: str | None = None,
    activity: str | None = None,
) -> dict[str, Any]:
    chosen, scope, activity_key = _resolve_slice(item, platform, variant, activity)
    row = db.execute(
        select(ChallengeRatingSnapshot.site_rows[str(user.id)]).where(
            _snapshot_filter(item.code, chosen.key, scope, activity_key)
        )
    ).scalar_one_or_none()
    return {
        "code": item.code,
        "variant": chosen.key,
        "platform": scope,
        "activity": activity_key,
        "row": row,
        "included": bool(row and row.get("included")),
    }


def get_challenge_hub(db: Session, user: User | None, platform: str = ALL) -> dict[str, Any]:
    """Карточки хаба /ratings: топ-3 и «Вы» по всем челленджам одним запросом.

    База отдаёт только нужные снимки и только первые строки таблиц: разбирать
    в Python все снимки целиком ради трёх строк стоило ~0,5 с на заход (аудит
    27.10.2026). Карточка показывает первый вариант челленджа. Челлендж, где
    выбранной системы нет (parkrun у V-индекса), показывает общий зачёт.
    """
    wanted = [
        (item.code, item.variants[0].key, normalize_platform(item, platform, item.variants[0]), ACTIVITY_ALL)
        for item in RATING_CHALLENGES
    ]
    columns: list[Any] = [
        ChallengeRatingSnapshot.code,
        ChallengeRatingSnapshot.platform,
        _first_rows(HUB_TOP_N),
    ]
    if user is not None:
        columns.append(ChallengeRatingSnapshot.site_rows[str(user.id)])
    query = select(*columns).where(
        tuple_(
            ChallengeRatingSnapshot.code,
            ChallengeRatingSnapshot.variant,
            ChallengeRatingSnapshot.platform,
            ChallengeRatingSnapshot.activity,
        ).in_(wanted)
    )
    found: dict[str, Any] = {}
    for record in db.execute(query):
        found[record[0]] = {
            "rows": list(record[2] or []),
            "me": record[3] if user is not None else None,
            "platform": record[1],
        }
    cards = []
    for item in RATING_CHALLENGES:
        fallback = {"rows": [], "me": None, "platform": normalize_platform(item, platform, item.variants[0])}
        cards.append(
            {
                "code": item.code,
                "slug": item.slug,
                "title": item.title,
                "icon": item.icon,
                "value_label": item.value_label,
                "unit_forms": list(item.unit_forms) if item.unit_forms else None,
                **found.get(item.code, fallback),
            }
        )
    return {"platform": platform, "cards": cards}


# Карточка челленджа «Моих достижений» → рейтинг и вариант, который считает то
# же число: Уилсон карточки — классический, серия — по любой активности,
# «Нумератор ПРО» — вариант pro рейтинга «Нумератор».
CARD_RATINGS: dict[str, tuple[str, str]] = {
    "p_index": ("p_index", "main"),
    "v_index": ("v_index", "main"),
    "wilson": ("wilson", "classic"),
    "streak": ("streak", "any"),
    "best_year": ("best_year", "best"),
    "seconds": ("seconds", "main"),
    "positions": ("positions", "main"),
    "calendar_days": ("calendar_days", "main"),
    "alphabet": ("alphabet", "main"),
    "start_numbers": ("start_numbers", "base"),
    "start_numbers_pro": ("start_numbers", "pro"),
}


def get_my_challenge_places(db: Session, user: User, platform: str = ALL) -> dict[str, Any]:
    """Места человека во всех рейтингах челленджей — для ссылок с карточек
    «Моих достижений» одним запросом (решение Дмитрия 27.10.2026, Р30).

    Срез — тот же, что выбран фильтром систем на странице достижений: карточка
    под «5 вёрст» ведёт в рейтинг по 5 вёрстам. Системы, которой у рейтинга
    нет (parkrun у V-индекса), — общий зачёт.
    """
    wanted: dict[tuple[str, str, str, str], str] = {}
    links: dict[str, dict[str, Any]] = {}
    for card_code, (code, variant_key) in CARD_RATINGS.items():
        item = CHALLENGES_BY_CODE[code]
        variant = variant_of(item, variant_key)
        scope = normalize_platform(item, platform, variant)
        wanted[(code, variant.key, scope, ACTIVITY_ALL)] = card_code
        links[card_code] = {
            "slug": item.slug,
            "variant": variant.key if len(item.variants) > 1 and variant.key != item.variants[0].key else None,
            "platform": scope,
            "rank": None,
            "entrants": None,
            "value": None,
        }
    query = select(
        ChallengeRatingSnapshot.code,
        ChallengeRatingSnapshot.variant,
        ChallengeRatingSnapshot.platform,
        ChallengeRatingSnapshot.entrants,
        ChallengeRatingSnapshot.site_rows[str(user.id)],
    ).where(
        tuple_(
            ChallengeRatingSnapshot.code,
            ChallengeRatingSnapshot.variant,
            ChallengeRatingSnapshot.platform,
            ChallengeRatingSnapshot.activity,
        ).in_(list(wanted))
    )
    for code, variant, scope, entrants, row in db.execute(query):
        card_code = wanted.get((code, variant, scope, ACTIVITY_ALL))
        if card_code is None:
            continue
        links[card_code]["entrants"] = entrants
        if row:
            links[card_code]["rank"] = row.get("rank")
            links[card_code]["value"] = row.get("value")
    return {"platform": platform, "places": links}


def resolve(slug: str) -> RatingChallenge | None:
    return CHALLENGES_BY_SLUG.get(slug)


def _main() -> None:
    """Пересчёт отдельным процессом (задача challenge_ratings.rebuild зовёт его
    через subprocess): проход держит ~0,4 ГБ, и процесс воркера Celery после
    него эту память не отдал бы — отдельный процесс отдаёт её, завершаясь."""
    from app.db.session import get_session_factory

    logging.basicConfig(level=logging.INFO)
    db = get_session_factory()()
    try:
        entrants = rebuild_challenge_ratings(db)
    finally:
        db.close()
    print(json.dumps({"snapshots": len(entrants), "p_index_entrants": entrants.get("p_index:main:all:all", 0)}))


if __name__ == "__main__":
    _main()
