"""Рейтинг быстрых: 5000 самых быстрых финишей и 3000 самых быстрых участников.

Два зачёта одной страницы (/ratings/fastest), а не два разных рейтинга:

- «Результаты» — таблица финишей. Один человек занимает столько строк, сколько
  быстрых забегов у него есть: так был устроен легаси-дашборд Grafana, и так
  видно, что рекордная минута — не случайность, а серия.
- «Участники» — по одному лучшему результату на человека, системы одного
  участника склеены по сайт-аккаунту (тот же `_entity_key`, что у остальных
  рейтингов).

Срез считается ЗАНОВО под каждый фильтр, а не фильтрует общий топ: 5000 самых
быстрых финишей страны — это почти сплошь мужчины, и «женский топ» из такого
списка вышел бы в десяток строк. Поэтому «Женщины» = 5000 самых быстрых финишей
среди женщин, «RunPark» = 5000 самых быстрых финишей RunPark и т.д.

Чего в срезе нет:

- зарубежный parkrun. От такой площадки в БД лежат только строки наших же
  участников, вытащенные из профилей (см. russian_parkrun_location_ids), и
  доверять времени оттуда не больше, чем месту. То же правило, что у побед.
- parkrun-участники, не прошедшие допуск рейтингов (см. _PARKRUN_ELIGIBLE_CTE).
- вторые протоколы кросс-платформенных стартов (event_crosslinks) и тестовые
  старты.

После этих отсечек данные чистые: самый быстрый финиш базы — 14:28, медленнее
мирового рекорда на пятёрке. Без них в базе 516 «финишей» быстрее рекорда мира —
это чужие дистанции из парсинга зарубежных профилей.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import User
from app.services.leaderboard_service import (
    _PARKRUN_ELIGIBLE_CTE,
    PLATFORM_COLUMNS,
    PLATFORM_LABELS,
    REFRESH_INTERVAL_HOURS,
    _entity_key,
    _location_identity_maps,
    _row_key,
    _site_links,
)
from app.services.location_catalog_service import russian_parkrun_location_ids
from app.services.location_page_service import (
    FIVE_VERST_PLATFORM_CODE,
    MAX_PLAUSIBLE_AGE,
    _age_group_sort_key,
    _read_json_cache,
    _write_json_cache,
    normalize_age_group,
)
from app.time_format import format_finish_time_display

FastestMode = Literal["results", "runners"]
FASTEST_MODES: tuple[FastestMode, ...] = ("results", "runners")

# Глубина каждого зачёта (постановка Дмитрия 25.08.2026). У результатов она
# больше: одна фамилия занимает там по несколько строк, и на тысяче список
# кончался бы на первой сотне людей.
MODE_LIMITS: dict[str, int] = {"results": 5000, "runners": 3000}

# Пол берём из participants.gender — материализованной метки (см.
# gender_position_service). Мужской зачёт здесь ЕСТЬ, в отличие от рейтингов
# побед: там его убрали из-за фантомных «первых мест среди мужчин» на стартах,
# где протокол не знает пол части финишёров, а тут места среди своего пола не
# считаются вовсе — строка без пола просто не попадает в гендерный срез и
# ничьё время не искажает.
FastestGender = Literal["all", "male", "female"]
FASTEST_GENDERS: tuple[FastestGender, ...] = ("all", "male", "female")

# Возрастную категорию забега публикуют не все системы: у 5 вёрст она есть в
# 97% строк («М35-39»), у RunPark — в 40% (parkrun-коды), у parkrun в этом поле
# лежит age grade («89.17%»), у S95 его нет вовсе. Поэтому возрастной срез —
# только 5 вёрст (решение Дмитрия 25.08.2026, то же, что у рекордов локаций
# 22.08.2026): иначе в группе «35–39» молча пропадали бы две системы из четырёх.
AGE_GROUP_PLATFORM = FIVE_VERST_PLATFORM_CODE

# И ступень должна быть не единичным артефактом.
MIN_AGE_GROUP_RESULTS = 50

# v2 — из ответа убраны entrants и latest_event_date, добавлены годы в разрезе
# систем (25.08.2026). Меняется форма payload'а, поэтому версия ключа, а не
# ожидание TTL.
CACHE_KEY_PREFIX = "fastest:v2"
CACHE_TTL_SECONDS = 6 * 3600
# Справочники (какие ступени и годы вообще предлагать) меняются раз в год —
# держим их дольше самих таблиц.
OPTIONS_CACHE_TTL_SECONDS = 24 * 3600
OPTIONS_CACHE_KEY = f"{CACHE_KEY_PREFIX}:options"

YEAR_ALL = "all"
AGE_GROUP_ALL = "all"
ALL_PLATFORMS = "all"


def normalize_mode(value: str) -> FastestMode:
    return "runners" if value == "runners" else "results"


def normalize_gender(value: str) -> FastestGender:
    return value if value in FASTEST_GENDERS else "all"


def normalize_platform(value: str, age_group: str) -> str:
    """Система среза. Возрастной срез жёстко приводит её к 5 вёрст — только эта
    система печатает диапазон возраста, и любой другой выбор дал бы пустую
    таблицу вместо ответа на вопрос."""
    if age_group != AGE_GROUP_ALL:
        return AGE_GROUP_PLATFORM
    return value if value in PLATFORM_COLUMNS else "all"


def normalize_year(value: str, years: Iterable[int]) -> str:
    if value == YEAR_ALL:
        return YEAR_ALL
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return YEAR_ALL
    return str(parsed) if parsed in set(years) else YEAR_ALL


# --------------------------------------------------------------------------- #
# Справочники фильтров
# --------------------------------------------------------------------------- #

_AGE_CATEGORIES_SQL = f"""
SELECT rr.age_category, COUNT(*) AS total
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
WHERE p.code = '{AGE_GROUP_PLATFORM}'
  AND e.is_test_event = false
  AND rr.finish_time_sec IS NOT NULL
  AND rr.finish_time_sec > 0
  AND rr.age_category IS NOT NULL
GROUP BY rr.age_category
"""

# Годы собираем по тем же отсечкам, что и таблицу, И В РАЗРЕЗЕ СИСТЕМ. Разрез
# обязателен: русский parkrun жил 2014–2022, остальные системы стартовали в
# 2022-м — общий список лет предлагал бы «parkrun + 2026», то есть заведомо
# пустую таблицу. Без отсечек в список попадали ещё и 2005–2013: зарубежный
# parkrun из профилей наших участников.
_YEARS_SQL_TEMPLATE = (
    "SELECT p.code AS platform_code, EXTRACT(YEAR FROM e.event_date)::int AS year"
    "{base}"
    "  AND e.event_date <= CURRENT_DATE\n"
    "GROUP BY p.code, year\n"
    "ORDER BY year DESC"
)


def _age_group_sort_age(age_group: str) -> int:
    return _age_group_sort_key(age_group)[0]


def _load_options(db: Session) -> dict[str, Any]:
    """Ступени возраста и годы, которые есть в данных.

    Ступени собираем не из захардкоженного списка, а из самих категорий: 5 вёрст
    успели поменять сетку («М10» вместо «М<10»), и любой наш список разошёлся бы
    с протоколом. Заодно тут отсекается мусор — см. MAX_PLAUSIBLE_AGE.
    """
    cached = _read_json_cache(OPTIONS_CACHE_KEY)
    if cached is not None:
        return cached

    categories_by_group: dict[str, list[str]] = {}
    totals: dict[str, int] = {}
    for raw_category, total in db.execute(text(_AGE_CATEGORIES_SQL)).all():
        group = normalize_age_group(raw_category)
        if group is None or _age_group_sort_age(group) > MAX_PLAUSIBLE_AGE:
            continue
        categories_by_group.setdefault(group, []).append(raw_category)
        totals[group] = totals.get(group, 0) + int(total)

    groups = sorted(
        (group for group, total in totals.items() if total >= MIN_AGE_GROUP_RESULTS),
        key=_age_group_sort_key,
    )
    payload: dict[str, Any] = {
        "age_groups": groups,
        "age_categories": {group: sorted(categories_by_group[group]) for group in groups},
        "years_by_platform": _load_years(db),
    }
    _write_json_cache(OPTIONS_CACHE_KEY, payload, OPTIONS_CACHE_TTL_SECONDS)
    return payload


def _load_years(db: Session) -> dict[str, list[int]]:
    """Годы, в которых у системы есть результаты. Ключ ALL_PLATFORMS — объединение."""
    sql = _PARKRUN_ELIGIBLE_CTE + _YEARS_SQL_TEMPLATE.format(base=_BASE_FROM)
    params = {"russian_parkrun_locations": list(russian_parkrun_location_ids(db))}
    by_platform: dict[str, set[int]] = {ALL_PLATFORMS: set()}
    for platform_code, year in db.execute(text(sql), params).all():
        by_platform.setdefault(platform_code, set()).add(int(year))
        by_platform[ALL_PLATFORMS].add(int(year))
    return {code: sorted(years, reverse=True) for code, years in by_platform.items()}


def available_age_groups(db: Session) -> list[str]:
    return list(_load_options(db)["age_groups"])


def available_years(db: Session, platform: str = ALL_PLATFORMS) -> list[int]:
    years: dict[str, list[int]] = _load_options(db)["years_by_platform"]
    return list(years.get(platform, ()))


# --------------------------------------------------------------------------- #
# Выборки
# --------------------------------------------------------------------------- #

# Общая часть всех выборок среза. Отсечки — в порядке «дешёвые сначала»:
# тестовые старты и вторые протоколы кросс-платформенных событий, затем строки
# без времени, затем два parkrun-правила (допуск участника и русская площадка).
_BASE_FROM = """
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
JOIN participants pa ON pa.id = rr.participant_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND ec.secondary_event_id IS NULL
  AND rr.finish_time_sec IS NOT NULL
  AND rr.finish_time_sec > 0
  AND (p.code <> 'parkrun' OR EXISTS (
        SELECT 1 FROM parkrun_eligible pe WHERE pe.participant_id = rr.participant_id))
  AND (p.code <> 'parkrun' OR e.location_id = ANY(:russian_parkrun_locations))
"""

_ROW_COLUMNS = """
    rr.id AS result_id,
    rr.participant_id AS participant_id,
    rr.finish_time_sec AS finish_time_sec,
    rr.age_category AS age_category,
    e.event_date AS event_date,
    e.location_id AS location_id,
    p.code AS platform_code,
    pa.display_name AS display_name,
    pa.gender AS gender
"""


def _slice_sql(extra_clauses: str) -> str:
    return _BASE_FROM + ("  " + extra_clauses + "\n" if extra_clauses else "")


def _slice_filters(
    db: Session,
    *,
    platform: str,
    gender: str,
    age_group: str,
    year: str,
    options: dict[str, Any],
) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    params: dict[str, object] = {
        "russian_parkrun_locations": list(russian_parkrun_location_ids(db)),
    }
    if platform != "all":
        clauses.append("AND p.code = :platform")
        params["platform"] = platform
    if gender != "all":
        clauses.append("AND pa.gender = :gender")
        params["gender"] = gender
    if age_group != AGE_GROUP_ALL:
        # Сравниваем с сырыми категориями протокола, а не с нормализованной
        # ступенью: точное равенство индексируемо и не зависит от того, какой
        # буквой система пометила пол в коде категории.
        categories = list(options["age_categories"].get(age_group, []))
        clauses.append("AND rr.age_category = ANY(:age_categories)")
        params["age_categories"] = categories
    if year != YEAR_ALL:
        clauses.append("AND e.event_date >= :year_start AND e.event_date <= :year_end")
        params["year_start"] = date(int(year), 1, 1)
        params["year_end"] = date(int(year), 12, 31)
    return "\n  ".join(clauses), params


def _fetch_result_rows(
    db: Session, where: str, params: dict[str, object], limit: int
) -> Sequence[Any]:
    sql = (
        _PARKRUN_ELIGIBLE_CTE
        + "SELECT"
        + _ROW_COLUMNS
        + _slice_sql(where)
        + "ORDER BY rr.finish_time_sec ASC, e.event_date ASC, rr.id ASC\nLIMIT :limit"
    )
    return db.execute(text(sql), {**params, "limit": limit}).all()


def _fetch_time_counts(db: Session, where: str, params: dict[str, object]) -> dict[int, int]:
    """Сколько финишей на каждой секунде среза — сырьё для индекса мест."""
    sql = (
        _PARKRUN_ELIGIBLE_CTE
        + "SELECT rr.finish_time_sec, COUNT(*)"
        + _slice_sql(where)
        + "GROUP BY rr.finish_time_sec"
    )
    return {int(row[0]): int(row[1]) for row in db.execute(text(sql), params).all()}


def _fetch_participant_bests(
    db: Session, where: str, params: dict[str, object]
) -> list[tuple[UUID, int]]:
    """participant_id -> лучшее время в срезе. Полная агрегация по срезу (на
    проде ~0.7 с на 1.9 млн строк) — она и даёт честный знаменатель «из скольких»,
    и избавляет от догадок «сколько строк тянуть, чтобы набралось 3000 человек»."""
    sql = (
        _PARKRUN_ELIGIBLE_CTE
        + "SELECT rr.participant_id, MIN(rr.finish_time_sec) AS best"
        + _slice_sql(where)
        + "GROUP BY rr.participant_id"
    )
    return [(row[0], int(row[1])) for row in db.execute(text(sql), params).all()]


def _fetch_best_rows_for(
    db: Session, where: str, params: dict[str, object], pids: list[UUID]
) -> Sequence[Any]:
    """Лучшая строка каждого из перечисленных участников — уже точечно, по id."""
    if not pids:
        return []
    sql = (
        _PARKRUN_ELIGIBLE_CTE
        + "SELECT DISTINCT ON (rr.participant_id)"
        + _ROW_COLUMNS
        + _slice_sql(where + "\n  AND rr.participant_id = ANY(:pids)")
        + "ORDER BY rr.participant_id, rr.finish_time_sec ASC, e.event_date ASC, rr.id ASC"
    )
    return db.execute(text(sql), {**params, "pids": pids}).all()


# --------------------------------------------------------------------------- #
# Сборка строк
# --------------------------------------------------------------------------- #


class _Maps:
    """Справочники, общие для всех строк одного ответа."""

    def __init__(self, db: Session) -> None:
        self.links = _site_links(db)
        identity_by_location, names, slugs = _location_identity_maps(db)
        self.identity_by_location = identity_by_location
        self.names = names
        self.slugs = slugs


def _location_of(maps: _Maps, location_id: UUID) -> tuple[str | None, str | None]:
    identity = maps.identity_by_location.get(location_id)
    if identity is None:
        return None, None
    return maps.names.get(identity), maps.slugs.get(identity)


def _build_row(row: Any, maps: _Maps) -> dict[str, Any]:
    link = maps.links.get(row.participant_id)
    display_name = row.display_name
    site_serial_id: int | None = None
    # Приватный профиль остаётся в таблице (результат — факт протокола), но
    # ссылкой и именем аккаунта не подписывается: ровно как в остальных
    # рейтингах, см. _entity_key.
    if link is not None and not link.private:
        site_serial_id = link.serial_id
        if link.display_name:
            display_name = link.display_name
    location_name, location_slug = _location_of(maps, row.location_id)
    event_date = row.event_date.isoformat() if row.event_date else None
    protocol_url = (
        f"/locations/{location_slug}/protocol/{row.platform_code}/{event_date}"
        if location_slug and event_date
        else None
    )
    age_group = normalize_age_group(row.age_category)
    return {
        "row_key": _row_key(_entity_key(row.participant_id, link)),
        "display_name": display_name,
        "site_serial_id": site_serial_id,
        "finish_time_sec": int(row.finish_time_sec),
        "finish_time_display": format_finish_time_display(int(row.finish_time_sec)),
        "platform": row.platform_code,
        "location_name": location_name,
        "location_slug": location_slug,
        "event_date": event_date,
        "protocol_url": protocol_url,
        # Категорию показываем только там, где это действительно она: у parkrun
        # в том же поле лежит age grade, и «89.17%» в колонке «Группа» — мусор.
        "age_group": age_group,
        "age_category": row.age_category if age_group else None,
        "gender": row.gender,
    }


def _rank_rows(rows: list[dict[str, Any]]) -> None:
    """Спортивное место: равные времена делят место, следующий получает свой
    порядковый номер (1, 2, 2, 4). На пятёрке ничьих в секунду много —
    раздавать им разные места было бы неправдой."""
    previous_time: int | None = None
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        seconds = row["finish_time_sec"]
        if seconds == previous_time:
            row["rank"] = previous_rank
        else:
            row["rank"] = index
            previous_rank = index
            previous_time = seconds


def _results_rows(db: Session, where: str, params: dict[str, object], limit: int, maps: _Maps) -> list[dict[str, Any]]:
    rows = [_build_row(row, maps) for row in _fetch_result_rows(db, where, params, limit)]
    _rank_rows(rows)
    return rows


def _build_rank_index(counts: dict[int, int]) -> dict[str, list[int]]:
    """Индекс «время → сколько строго быстрее», по которому место любого
    результата берётся без похода в базу.

    Зачем: место в строке «Вы» раньше считалось отдельным COUNT'ом по всему
    срезу — то есть полный проход по 1.9 млн строк на КАЖДУЮ смену фильтра, и
    строка отставала от таблицы на секунды. Распределение времён у среза одно,
    считается вместе с ним (в зачёте участников — вообще даром, из уже
    посчитанного агрегата) и живёт в кэше рядом.
    """
    times = sorted(counts)
    faster: list[int] = []
    running = 0
    for seconds in times:
        faster.append(running)
        running += counts[seconds]
    # Хвост для времён медленнее последнего в срезе: быстрее них — вообще все.
    faster.append(running)
    return {"times": times, "faster": faster}


def _rank_from_index(index: dict[str, list[int]], seconds: int) -> int:
    times = index["times"]
    faster = index["faster"]
    if len(faster) != len(times) + 1:
        raise ValueError("битый индекс мест")
    return faster[bisect_left(times, seconds)] + 1


def _merge_bests(
    best_by_pid: list[tuple[UUID, int]], links: dict[UUID, Any]
) -> dict[str, tuple[int, UUID]]:
    """Свести участников разных систем в одного человека.

    Ключ склейки — сайт-аккаунт (тот же _entity_key, что у остальных
    рейтингов): у зарегистрированного человека 5 вёрст и parkrun — одна строка,
    а его результат в зачёте — лучший из всех систем. При равенстве времени
    берём участника с меньшим id, чтобы порядок строк не плавал между
    пересчётами.
    """
    best_by_entity: dict[str, tuple[int, UUID]] = {}
    for pid, seconds in best_by_pid:
        key = _entity_key(pid, links.get(pid))
        current = best_by_entity.get(key)
        if current is None or seconds < current[0] or (seconds == current[0] and pid < current[1]):
            best_by_entity[key] = (seconds, pid)
    return best_by_entity


def _runners_rows(
    db: Session, where: str, params: dict[str, object], limit: int, maps: _Maps
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Топ участников: агрегируем лучшее время по участникам, склеиваем системы
    одного человека по сайт-аккаунту и только для верхушки идём за подробностями
    его лучшего забега. Второе значение — распределение личных рекордов по
    секундам: агрегат его уже знает, и индекс мест достаётся даром."""
    best_by_entity = _merge_bests(_fetch_participant_bests(db, where, params), maps.links)

    ordered = sorted(best_by_entity.items(), key=lambda item: (item[1][0], str(item[1][1])))
    top = ordered[:limit]
    pids = [pid for _key, (_seconds, pid) in top]
    detail_by_pid = {row.participant_id: row for row in _fetch_best_rows_for(db, where, params, pids)}

    rows: list[dict[str, Any]] = []
    for _key, (_seconds, pid) in top:
        detail = detail_by_pid.get(pid)
        if detail is not None:
            rows.append(_build_row(detail, maps))
    _rank_rows(rows)
    counts: dict[int, int] = {}
    for seconds, _pid in best_by_entity.values():
        counts[seconds] = counts.get(seconds, 0) + 1
    return rows, counts


# --------------------------------------------------------------------------- #
# Публичное API
# --------------------------------------------------------------------------- #

MODE_TITLES: dict[str, str] = {
    "results": "Самые быстрые финиши",
    "runners": "Самые быстрые участники",
}


def _format_limit(limit: int) -> str:
    """«5 000» неразрывным пробелом — как остальные числа на сайте."""
    return f"{limit:,}".replace(",", "\u00a0")


def _mode_description(mode: str, limit: int) -> str:
    if mode == "runners":
        return (
            f"{_format_limit(limit)} участников с самым быстрым личным рекордом — по одной строке "
            "на человека; у зарегистрированных на сайте системы объединены в одну строку."
        )
    return (
        f"{_format_limit(limit)} самых быстрых финишей за всю историю. Один участник занимает "
        "столько строк, сколько быстрых забегов у него есть."
    )


def _cut_rows(payload: dict[str, Any], limit: int | None) -> dict[str, Any]:
    """Укоротить таблицу к ответу, НЕ заводя отдельный снапшот под каждую глубину.

    Карточке рейтинга на хабе нужны три строки, а не пять тысяч, но считать их
    отдельным срезом было бы расточительно: срез один и тот же, отличается
    только сколько его видно.
    """
    if limit is None or limit >= len(payload["rows"]):
        return payload
    return {**payload, "rows": payload["rows"][:limit]}


def _cache_key(mode: str, platform: str, gender: str, age_group: str, year: str) -> str:
    return f"{CACHE_KEY_PREFIX}:{mode}:{platform}:{gender}:{age_group}:{year}"


def _rank_index_key(cache_key: str) -> str:
    """Индекс мест живёт отдельным ключом, а не полем снапшота: снапшот целиком
    уезжает витрине, а таблица распределения ей не нужна и весит десятки КБ."""
    return f"{cache_key}:ranks"


def get_fastest_rating(
    db: Session,
    *,
    mode: str = "results",
    platform: str = "all",
    gender: str = "all",
    age_group: str = AGE_GROUP_ALL,
    year: str = YEAR_ALL,
    limit: int | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    options = _load_options(db)
    mode = normalize_mode(mode)
    gender = normalize_gender(gender)
    age_group = age_group if age_group in options["age_groups"] else AGE_GROUP_ALL
    platform = normalize_platform(platform, age_group)
    year = normalize_year(year, options["years_by_platform"].get(platform, ()))
    # Глубина зачёта (сколько строк вообще считаем) и limit ответа — разные
    # вещи: срез всегда считается целиком, limit лишь укорачивает выдачу.
    depth = MODE_LIMITS[mode]

    key = _cache_key(mode, platform, gender, age_group, year)
    if use_cache:
        cached = _read_json_cache(key)
        if cached is not None:
            return _cut_rows(cached, limit)

    maps = _Maps(db)
    where, params = _slice_filters(
        db, platform=platform, gender=gender, age_group=age_group, year=year, options=options
    )
    if mode == "runners":
        rows, time_counts = _runners_rows(db, where, params, depth, maps)
    else:
        rows = _results_rows(db, where, params, depth, maps)
        time_counts = _fetch_time_counts(db, where, params)

    payload: dict[str, Any] = {
        "mode": mode,
        "platform": platform,
        "gender": gender,
        "age_group": age_group,
        "year": year,
        "limit": depth,
        "title": MODE_TITLES[mode],
        "description": _mode_description(mode, depth),
        "platform_options": ["all", *PLATFORM_COLUMNS],
        "platform_labels": dict(PLATFORM_LABELS),
        "age_group_options": list(options["age_groups"]),
        "age_group_platform": AGE_GROUP_PLATFORM,
        # Годы по системам разом: переключение системы должно сразу подрезать
        # список лет, не бегая за этим на сервер.
        "year_options_by_platform": {
            code: list(years) for code, years in options["years_by_platform"].items()
        },
        "rows": rows,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "refresh_hours": REFRESH_INTERVAL_HOURS,
    }
    _write_json_cache(key, payload, CACHE_TTL_SECONDS)
    _write_json_cache(_rank_index_key(key), _build_rank_index(time_counts), CACHE_TTL_SECONDS)
    return _cut_rows(payload, limit)


_MY_PARTICIPANTS_SQL = """
SELECT pl.participant_id
FROM platform_links pl
WHERE pl.user_id = :user_id AND pl.participant_id IS NOT NULL
"""


def _rank_of_time(
    db: Session,
    mode: str,
    where: str,
    params: dict[str, object],
    seconds: int,
    cache_key: str,
) -> int:
    """Место результата (или участника) в срезе: «сколько строго быстрее» плюс
    единица — то же спортивное место, что и в таблице.

    Сначала пробуем индекс, посчитанный вместе со срезом: там ответ мгновенный.
    Полный COUNT остаётся запасным путём — на случай, если срез уже в кэше, а
    индекс успел протухнуть или его записали ещё старой версией кода.
    """
    index = _read_json_cache(_rank_index_key(cache_key))
    if index is not None:
        try:
            return _rank_from_index(index, seconds)  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            pass
    if mode == "runners":
        sql = (
            _PARKRUN_ELIGIBLE_CTE
            + "SELECT COUNT(*) FROM (SELECT rr.participant_id, MIN(rr.finish_time_sec) AS best"
            + _slice_sql(where)
            + "GROUP BY rr.participant_id) t WHERE t.best < :seconds"
        )
    else:
        sql = (
            _PARKRUN_ELIGIBLE_CTE
            + "SELECT COUNT(*)"
            + _slice_sql(where + "\n  AND rr.finish_time_sec < :seconds")
        )
    faster = int(db.execute(text(sql), {**params, "seconds": seconds}).scalar() or 0)
    return faster + 1


def get_my_fastest_row(
    db: Session,
    user: User,
    *,
    mode: str = "results",
    platform: str = "all",
    gender: str = "all",
    age_group: str = AGE_GROUP_ALL,
    year: str = YEAR_ALL,
) -> dict[str, Any]:
    options = _load_options(db)
    mode = normalize_mode(mode)
    gender = normalize_gender(gender)
    age_group = age_group if age_group in options["age_groups"] else AGE_GROUP_ALL
    platform = normalize_platform(platform, age_group)
    year = normalize_year(year, options["years_by_platform"].get(platform, ()))
    limit = MODE_LIMITS[mode]

    pids = [row[0] for row in db.execute(text(_MY_PARTICIPANTS_SQL), {"user_id": user.id}).all()]
    empty: dict[str, Any] = {
        "mode": mode,
        "platform": platform,
        "gender": gender,
        "age_group": age_group,
        "year": year,
        "row": None,
        "rank": None,
        "included": False,
    }
    if not pids:
        return empty

    maps = _Maps(db)
    where, params = _slice_filters(
        db, platform=platform, gender=gender, age_group=age_group, year=year, options=options
    )
    my_rows = _fetch_best_rows_for(db, where, params, pids)
    if not my_rows:
        return empty
    best = min(my_rows, key=lambda row: (int(row.finish_time_sec), row.event_date))
    rank = _rank_of_time(
        db,
        mode,
        where,
        params,
        int(best.finish_time_sec),
        _cache_key(mode, platform, gender, age_group, year),
    )
    row = _build_row(best, maps)
    row["rank"] = rank
    return {**empty, "row": row, "rank": rank, "included": rank <= limit}


def warm_fastest_rating(db: Session) -> int:
    """Прогрев ходовых срезов: зачёт × пол × система.

    Возрастные ступени и годы в прогрев НЕ берём — вместе с ними сетка выросла
    бы до пары тысяч вариантов, каждый ценой полного прохода по 1.9 млн строк,
    а за шесть часов жизни кэша почти ни один из них никто не откроет. Кнопки
    же «Система» и «Зачёт» — первое, что нажимают, и ждать пересчёта на них
    человек не должен.

    Зачёт «все» тоже греем, хотя кнопки у него больше нет: по нему живёт
    карточка рейтинга на /ratings и старые ссылки.
    """
    warmed = 0
    for mode in FASTEST_MODES:
        for gender in FASTEST_GENDERS:
            for platform in (ALL_PLATFORMS, *PLATFORM_COLUMNS):
                # use_cache=False: прогрев обязан ПЕРЕсчитать срез, иначе он
                # просто прочитает то, что и так лежит в кэше, и ничего не обновит.
                get_fastest_rating(
                    db, mode=mode, gender=gender, platform=platform, use_cache=False
                )
                warmed += 1
    return warmed
