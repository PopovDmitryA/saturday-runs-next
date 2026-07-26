"""Сквозные рейтинги (лидерборды) по всем беговым системам.

Единый принцип для всех рейтингов:
- строка = участник; колонки = значение в каждой системе + «всего»;
- зарегистрированные на сайте пользователи склеиваются по всем платформам в
  одну строку (в отличие от «Встреч» — здесь приватность прячет только ссылку
  на профиль и имя из аккаунта, не сам факт объединения систем в одну строку);
  незарегистрированные участники остаются по-платформенно (мержить их некому —
  единственный источник кросс-платформенной идентичности это привязка аккаунта);
- дельта за последнюю неделю (окно = 7 дней от самой свежей даты событий);
- место и дельта места для залогиненного участника, даже вне видимого топа;
- порог входа: ниже медианного значения метрики участник в рейтинге не
  показывается (для «моей» строки возвращаем порог, а не место).

Расчёт полного снапшота дорогой (агрегация по всем протоколам), поэтому
кэшируется в Redis целиком; «моя» строка считается дёшево поверх снапшота.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Location, Participant, Platform, PlatformLink, User
from app.parkrun.volunteer_credits import (
    resolve_parkrun_volunteering_count,
)
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.location_catalog_service import LocationCatalogIndex
from app.time_format import format_finish_time_display
from app.volunteering_occasions import count_volunteering_occasions

LeaderboardMetric = Literal["runs", "volunteering", "locations", "wins", "win_locations"]

LEADERBOARD_METRICS: tuple[LeaderboardMetric, ...] = (
    "runs",
    "volunteering",
    "locations",
    "wins",
    "win_locations",
)

LeaderboardGender = Literal["all", "male", "female"]

LEADERBOARD_GENDERS: tuple[LeaderboardGender, ...] = ("all", "male", "female")

# Метрики, у которых есть разрез М/Ж (победы). parkrun в гендерный зачёт не идёт:
# его пол известен только по неполным профилям и ненадёжен (см. _GENDER_LABEL_SQL).
GENDERED_METRICS: tuple[LeaderboardMetric, ...] = ("wins", "win_locations")

# Порядок колонок-систем (как на портале: активные, затем архив parkrun).
PLATFORM_COLUMNS: tuple[str, ...] = ("five_verst", "s95", "runpark", "parkrun")
# В гендерном зачёте parkrun исключён — и из подсчёта, и из колонок таблицы.
GENDERED_PLATFORM_COLUMNS: tuple[str, ...] = ("five_verst", "s95", "runpark")

TOP_LIMIT = 1000
CACHE_TTL_SECONDS = 6 * 3600
# v2 — в снапшот победных рейтингов добавлены «лучшее время» и «последняя
# победа» (26.07.2026): старые кэшированные payload'ы этих полей не несут,
# поэтому ключ версионируем, а не ждём протухания по TTL.
CACHE_KEY_PREFIX = "leaderboards:v2"

# Победные рейтинги показывают две дополнительные колонки: глобальный рекорд
# участника и последнюю победу (у win_locations — последнюю НОВУЮ локацию с
# победой). Обе считаются только для этих метрик, чтобы не таскать лишние
# поля в снапшотах «пробежек»/«волонтёрств»/«локаций».
WIN_EXTRAS_METRICS: tuple[LeaderboardMetric, ...] = ("wins", "win_locations")

# Порог входа = процентиль распределения (решение Дмитрия 14.07.2026, по факту
# посчитанной на прод-данных статистике: у 82% участников ровно 1 локация —
# медиана там бесполезна как фильтр, поэтому для локаций взят p95, а не p75/медиана).
# У «победных» метрик порога нет (перцентиль 0 → порог = минимальное значение,
# т.е. 1): сама победа уже достаточно редкое событие, отсекать никого не нужно.
METRIC_THRESHOLD_PERCENTILE: dict[str, float] = {
    "runs": 75,
    "volunteering": 75,
    "locations": 95,
    "wins": 0,
    "win_locations": 0,
}

METRIC_META: dict[str, dict[str, str]] = {
    "runs": {
        "title": "Рейтинг количества пробежек",
        "unit": "пробежек",
        "description": (
            "Дубли одной физической пробежки, попавшей в протоколы двух систем, "
            "не задваиваются."
        ),
    },
    "volunteering": {
        "title": "Рейтинг количества волонтёрств",
        "unit": "волонтёрств",
        "description": "Волонтёрства во всех системах. Дельта — за последнюю неделю.",
    },
    "locations": {
        "title": "Рейтинг туризма — уникальные локации",
        "unit": "локаций",
        "description": (
            "Уникальные локации, где участник финишировал. Одна и та же локация "
            "в разных системах считается одной. «Всего» — уникальные локации "
            "по всем системам (не сумма колонок)."
        ),
    },
    "wins": {
        "title": "Рейтинг количества первых мест",
        "unit": "первых мест",
        "description": (
            "Первое место в абсолютном зачёте пробежки. Топ-локация — где это "
            "случалось чаще всего. Дубли одной физической пробежки, попавшей в "
            "протоколы двух систем, не задваиваются."
        ),
    },
    "win_locations": {
        "title": "Рейтинг локаций с первым местом",
        "unit": "локаций с первым местом",
        "description": (
            "Уникальные локации, где участник финишировал первым в абсолютном "
            "зачёте. Одна и та же локация в разных системах считается одной. "
            "«Всего» — уникальные локации по всем системам (не сумма колонок)."
        ),
    },
}

# Описание меняется под выбранный зачёт: в режимах М/Ж первое место — среди
# своего пола, а не в абсолюте. Оговорка про исключённый parkrun живёт
# отдельной заметкой во фронте, чтобы не дублировать её здесь. Термин
# «первое место», а не «победа», — сознательный выбор: в parkrun-сообществе
# нет «победителей», есть «первый финишировавший» (first finisher), замыкающий
# так же почётен, как лидер.
METRIC_GENDER_DESCRIPTION: dict[str, dict[str, str]] = {
    "wins": {
        "male": (
            "Первое место среди мужчин в зачёте пробежки. Топ-локация — где "
            "участник финишировал первым чаще всего. Дубли одной физической "
            "пробежки, попавшей в протоколы двух систем, не задваиваются."
        ),
        "female": (
            "Первое место среди женщин в зачёте пробежки. Топ-локация — где "
            "участница финишировала первой чаще всего. Дубли одной физической "
            "пробежки, попавшей в протоколы двух систем, не задваиваются."
        ),
    },
    "win_locations": {
        "male": (
            "Уникальные локации, где участник финишировал первым среди мужчин. "
            "Одна и та же локация в разных системах считается одной. «Всего» — "
            "уникальные локации по всем системам (не сумма колонок)."
        ),
        "female": (
            "Уникальные локации, где участница финишировала первой среди женщин. "
            "Одна и та же локация в разных системах считается одной. «Всего» — "
            "уникальные локации по всем системам (не сумма колонок)."
        ),
    },
}


def metric_description(metric: str, gender: str) -> str:
    """Описание рейтинга под выбранный зачёт (абсолют / мужской / женский)."""
    gendered = METRIC_GENDER_DESCRIPTION.get(metric, {}).get(gender)
    return gendered or METRIC_META[metric]["description"]

# parkrun синкает ВЕСЬ мировой профиль участника (не только русские старты).
# Зарубежные гости, реально приезжавшие бегать в Россию, — легитимные данные
# (сайт парсит всех, кто финишировал на русских площадках), но случайные
# иностранцы без всякой связи с нашими системами — нет. Критерий (решение
# Дмитрия 14.07.2026): участник допускается в рейтинги, если ЛИБО у него 50%+
# пробежек на русских (каталогизированных) parkrun-площадках, ЛИБО он вообще
# зарегистрирован на сайте (тогда порог не важен — реальный локальный бегун,
# который просто съездил в турпоездку с parkrun'ом). Один раз посчитанная CTE
# переиспользуется в _RUNS_SQL/_LOCATION_VISITS_SQL и в отдельном запросе для
# волонтёрских кредитов (там нет ни площадки, ни даты, только участник).
_PARKRUN_ELIGIBLE_CTE = """
WITH parkrun_run_stats AS (
    SELECT
        rr2.participant_id,
        COUNT(*) AS total,
        COUNT(*) FILTER (
            WHERE EXISTS (SELECT 1 FROM location_catalog_links lcl WHERE lcl.location_id = e2.location_id)
        ) AS russian
    FROM run_results rr2
    JOIN events e2 ON e2.id = rr2.event_id
    JOIN platforms p2 ON p2.id = e2.platform_id
    WHERE p2.code = 'parkrun' AND e2.is_test_event = false
    GROUP BY rr2.participant_id
),
parkrun_eligible AS (
    SELECT prs.participant_id
    FROM parkrun_run_stats prs
    WHERE prs.total > 0
      AND (
        prs.russian::float / prs.total >= 0.5
        OR EXISTS (SELECT 1 FROM platform_links pl WHERE pl.participant_id = prs.participant_id)
      )
)
"""

_PARKRUN_ELIGIBLE_EXISTS = (
    "EXISTS (SELECT 1 FROM parkrun_eligible pe WHERE pe.participant_id = rr.participant_id)"
)

_PARKRUN_ELIGIBLE_IDS_SQL = _PARKRUN_ELIGIBLE_CTE + "SELECT participant_id FROM parkrun_eligible"

_RUNS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.event_date >= :week_start) AS week
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND ec.secondary_event_id IS NULL
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code
"""
)

# Подсчёт волонтёрств: s95 — каждая запись считается отдельно (без схлопывания
# по дате, как на личной странице бегуна); 5 вёрст И RunPark (решение Дмитрия
# 15.07.2026 — у RunPark та же логика, что у 5в) — occasion-логика (см.
# app.volunteering_occasions.count_volunteering_occasions): обычно один зачёт
# на календарный день, но 1 января (инвентаризация) — по зачёту на каждую
# РАЗНУЮ локацию в этот день; parkrun — по кредитам профиля (без даты/локации
# вообще, отдельный путь через _parkrun_volunteer_counts).
_VOLUNTEERING_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE e.event_date >= :week_start) AS week
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code = 's95'
  AND ec.secondary_event_id IS NULL
GROUP BY vr.participant_id, p.code
"""

_OCCASION_VOLUNTEER_ROWS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    e.event_date AS event_date,
    l.external_key AS location_key
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
JOIN locations l ON l.id = e.location_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code IN ('five_verst', 'runpark')
  AND ec.secondary_event_id IS NULL
"""

def _location_visits_sql(*, only_wins: bool) -> str:
    """Первые визиты участника по локациям; в варианте only_wins — только
    финиши первым в абсолюте (first_date тогда = дата первой победы там)."""
    wins_filter = "AND rr.position = 1" if only_wins else ""
    return (
        _PARKRUN_ELIGIBLE_CTE
        + f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    MIN(e.event_date) AS first_date
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND ec.secondary_event_id IS NULL
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
  {wins_filter}
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id
"""
    )


_LOCATION_VISITS_SQL = _location_visits_sql(only_wins=False)
_WIN_LOCATION_VISITS_SQL = _location_visits_sql(only_wins=True)

# Победы (первые места в абсолюте) с разбивкой по локациям: одной выборкой
# получаем и счёт по системам, и «топ-локацию побед» (локацию с максимумом
# побед), и дату последней победы на каждой локации (из неё — «последняя победа»).
_WIN_ROWS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    COUNT(*) AS wins,
    COUNT(*) FILTER (WHERE e.event_date >= :week_start) AS week_wins,
    MAX(e.event_date) AS last_date
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND rr.position = 1
  AND ec.secondary_event_id IS NULL
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id
"""
)

# Пол результата по системам — для гендерного зачёта побед (parkrun исключён):
# 5в — первая буква age_category (М/Ж); runpark — вторая буква категории (M/W);
# s95 — из profile_extra (в протоколе категории нет). parkrun сюда не попадает:
# в run_results.age_category у него лежит age-grade %, а пол профиля собран по
# неполным данным — гендерные запросы жёстко фильтруют p.code <> 'parkrun'.
_GENDER_LABEL_SQL = """
CASE p.code
    WHEN 'five_verst' THEN CASE LEFT(rr.age_category, 1)
        WHEN 'М' THEN 'male' WHEN 'Ж' THEN 'female' END
    WHEN 'runpark' THEN CASE SUBSTRING(rr.age_category FROM 2 FOR 1)
        WHEN 'M' THEN 'male' WHEN 'W' THEN 'female' END
    WHEN 's95' THEN pt.profile_extra -> 'platform_codes' ->> 'gender'
END
"""

# Победы среди своего пола (gender_position = 1), без parkrun. Одной выборкой
# обслуживает оба гендерных рейтинга: COUNT — «Количество побед», набор
# локаций + first_date — «Локации с победами». gender_label может быть NULL
# на части строк (напр. runpark без категории) — пол участника определяется
# в Python по большинству ненулевых меток его строк.
_GENDERED_WIN_ROWS_SQL = f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    {_GENDER_LABEL_SQL} AS gender_label,
    COUNT(*) AS wins,
    COUNT(*) FILTER (WHERE e.event_date >= :week_start) AS week_wins,
    MIN(e.event_date) AS first_date,
    MAX(e.event_date) AS last_date
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
LEFT JOIN participants pt ON pt.id = rr.participant_id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND rr.gender_position = 1
  AND p.code <> 'parkrun'
  AND ec.secondary_event_id IS NULL
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id, gender_label
"""

# Пол участника по ВСЕЙ его истории финишей (любая позиция, не только 1-е
# место) — нужен, чтобы понять «этот зачёт вообще про него», когда первых
# мест в выбранном поле у него ноль (см. _account_gender): участник мужского
# пола без единой победы среди мужчин не должен видеть в женском зачёте
# «вы появитесь после 1 первого места» — это не про него в принципе.
_PARTICIPANT_GENDER_VOTES_SQL = f"""
SELECT
    rr.participant_id AS participant_id,
    {_GENDER_LABEL_SQL} AS gender_label,
    COUNT(*) AS cnt
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN participants pt ON pt.id = rr.participant_id
WHERE e.is_test_event = false
  AND rr.participant_id = ANY(:pids)
  AND p.code <> 'parkrun'
GROUP BY rr.participant_id, gender_label
"""

# Глобальный рекорд участника — лучшее время по ВСЕЙ его истории финишей, а не
# только по забегам с победой (в победных рейтингах колонка так и называется —
# «лучшее время»). Фильтры те же, что у остальных метрик: без тестовых событий и
# без «левых» parkrun-профилей; кросслинки не исключаем — дубль одной физической
# пробежки в двух системах на минимум времени не влияет. Считается только по
# участникам победных рейтингов (их немного), поэтому запрос всегда с ANY(:pids).
_BEST_TIME_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
SELECT
    rr.participant_id AS participant_id,
    MIN(rr.finish_time_sec) AS best_time_sec
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
WHERE e.is_test_event = false
  AND rr.participant_id = ANY(:pids)
  AND rr.finish_time_sec IS NOT NULL
  AND rr.finish_time_sec > 0
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
GROUP BY rr.participant_id
"""
)

_LATEST_EVENT_DATE_SQL = """
SELECT MAX(e.event_date)
FROM events e
WHERE e.is_test_event = false AND e.event_date <= CURRENT_DATE
"""

_PARKRUN_VOLUNTEER_PROFILES_SQL = """
SELECT DISTINCT pt.id, pt.profile_extra
FROM participants pt
JOIN platforms p ON p.id = pt.platform_id
JOIN volunteer_results vr ON vr.participant_id = pt.id
WHERE p.code = 'parkrun'
"""

_PARKRUN_VOLUNTEER_ROLES_SQL = """
SELECT vr.participant_id, vr.role
FROM volunteer_results vr
JOIN participants pt ON pt.id = vr.participant_id
JOIN platforms p ON p.id = pt.platform_id
WHERE p.code = 'parkrun' AND vr.role IS NOT NULL
"""

@dataclass
class _SiteLink:
    user_id: UUID
    serial_id: int
    display_name: str | None
    private: bool


@dataclass
class _Entity:
    key: str
    display_name: str | None = None
    site_serial_id: int | None = None
    # platform code -> [total, week]
    values: dict[str, list[int]] = field(default_factory=dict)
    total: int = 0
    week: int = 0
    # Только для метрики wins: «топ-локация побед» — локация с максимумом побед.
    home_location: str | None = None
    home_location_wins: int = 0
    # Только у победных метрик (см. WIN_EXTRAS_METRICS): глобальный рекорд и
    # последняя победа. У win_locations «последняя» — это последняя НОВАЯ
    # локация с победой (дата = первая победа именно на ней).
    best_time_sec: int | None = None
    last_win_location: str | None = None
    last_win_location_slug: str | None = None
    last_win_date: date | None = None
    # Участники, из которых собрана строка — нужны для запроса рекорда.
    participant_ids: set[UUID] = field(default_factory=set)


def latest_event_date(db: Session) -> date | None:
    value = db.execute(text(_LATEST_EVENT_DATE_SQL)).scalar()
    return value if isinstance(value, date) else None


def _week_start(latest: date) -> date:
    return latest - timedelta(days=6)


def _site_links(db: Session) -> dict[UUID, _SiteLink]:
    """participant_id -> сайт-пользователь (для склейки платформ одного человека)."""
    rows = (
        db.query(
            PlatformLink.participant_id,
            User.id,
            User.serial_id,
            User.display_name,
            User.profile_private,
        )
        .join(User, PlatformLink.user_id == User.id)
        .filter(PlatformLink.participant_id.isnot(None))
        .all()
    )
    return {
        row[0]: _SiteLink(
            user_id=row[1],
            serial_id=int(row[2]),
            display_name=row[3],
            private=bool(row[4]),
        )
        for row in rows
    }


def _participant_names(db: Session, participant_ids: set[UUID]) -> dict[UUID, str | None]:
    """Имена участников. Загружаем всех разом — это дешевле, чем гигантский IN."""
    rows = db.query(Participant.id, Participant.display_name).all()
    return {row[0]: row[1] for row in rows if row[0] in participant_ids}


def _entity_key(pid: UUID, link: _SiteLink | None) -> str:
    """Склейка по сайт-аккаунту для ЛЮБОГО зарегистрированного участника, в т.ч.
    приватного — приватность прячет только ссылку/имя (см. места, где читают
    link.private), а не сам факт объединения его систем в одну строку рейтинга."""
    if link is not None:
        return f"u:{link.user_id}"
    return f"p:{pid}"


def _numeric_rows(db: Session, sql: str, week_start: date) -> list[tuple[UUID, str, int, int]]:
    rows = db.execute(text(sql), {"week_start": week_start}).all()
    return [(row[0], row[1], int(row[2]), int(row[3])) for row in rows]


def _parkrun_eligible_ids(db: Session) -> set[UUID]:
    """parkrun-участники, допущенные в рейтинги: 50%+ пробежек на русских
    (каталогизированных) площадках ИЛИ зарегистрированы на сайте — см. подробный
    комментарий у _PARKRUN_ELIGIBLE_CTE."""
    return {row[0] for row in db.execute(text(_PARKRUN_ELIGIBLE_IDS_SQL)).all()}


def _parkrun_volunteer_counts(db: Session, eligible_ids: set[UUID]) -> dict[UUID, int]:
    """parkrun: волонтёрства по кредитам профиля (дат у parkrun-волонтёрств нет)."""
    profiles = {row[0]: row[1] for row in db.execute(text(_PARKRUN_VOLUNTEER_PROFILES_SQL)).all()}
    roles_by_pid: dict[UUID, list[str]] = {}
    for pid, role in db.execute(text(_PARKRUN_VOLUNTEER_ROLES_SQL)).all():
        roles_by_pid.setdefault(pid, []).append(role)
    counts: dict[UUID, int] = {}
    for pid, profile_extra in profiles.items():
        if pid not in eligible_ids:
            continue
        count = resolve_parkrun_volunteering_count(
            profile_extra=profile_extra if isinstance(profile_extra, dict) else None,
            summary_role_labels=roles_by_pid.get(pid),
        )
        if count > 0:
            counts[pid] = count
    return counts


def _occasion_volunteering_rows(
    db: Session, week_start: date
) -> list[tuple[UUID, str, int, int]]:
    """5в и RunPark: волонтёрства через occasion-логику (см. комментарий у
    _VOLUNTEERING_SQL) — считается отдельно на каждую систему, но одинаково."""
    raw = db.execute(text(_OCCASION_VOLUNTEER_ROWS_SQL)).all()
    by_pid_platform: dict[tuple[UUID, str], list[tuple[date, str]]] = {}
    for pid, platform_code, event_date, location_key in raw:
        by_pid_platform.setdefault((pid, platform_code), []).append(
            (event_date, location_key or "unknown")
        )
    result: list[tuple[UUID, str, int, int]] = []
    for (pid, platform_code), rows in by_pid_platform.items():
        total = count_volunteering_occasions(rows)
        week = count_volunteering_occasions([r for r in rows if r[0] >= week_start])
        if total > 0:
            result.append((pid, platform_code, total, week))
    return result


def _location_identity_maps(
    db: Session,
) -> tuple[dict[UUID, str], dict[str, str], dict[str, str]]:
    """location_id -> канонический ключ площадки + ключ -> имя и slug страницы.

    Имя и slug канонической площадки берём у локации самой «старшей» системы в
    порядке PLATFORM_COLUMNS — у кросс-платформенных площадок имена в системах
    слегка различаются, нужен один детерминированный вариант. Тем же порядком
    страница локации выбирает свой публичный slug (см. resolve_location_identity),
    и она же принимает external_key любой системы идентичности — так что ссылка
    /locations/<slug> ведёт на нужную площадку.
    """
    catalog_index = LocationCatalogIndex(db)
    rows = db.query(Location, Platform.code).join(Platform, Location.platform_id == Platform.id).all()
    platform_priority = {code: index for index, code in enumerate(PLATFORM_COLUMNS)}
    identity_by_location: dict[UUID, str] = {}
    name_candidates: dict[str, tuple[int, str]] = {}
    slug_candidates: dict[str, tuple[int, str]] = {}
    for location, platform_code in rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        identity_by_location[location.id] = identity
        priority = platform_priority.get(platform_code, len(PLATFORM_COLUMNS))
        current = name_candidates.get(identity)
        if current is None or priority < current[0]:
            # Имя берём у самой «старшей» системы как есть, а не через каталог:
            # у трёх площадок canonical_name в каталоге — легаси-транслитерация
            # времён parkrun («Ufa Botanichesky Sad» вместо «Парк Лесоводов»),
            # и в рейтинге нужно живое русское название. Из-за этого у них
            # подпись ссылки расходится с заголовком страницы локации —
            # чинится правкой canonical_name в каталоге, не здесь.
            name_candidates[identity] = (priority, location.name)
        slug = (location.external_key or "").strip().lower()
        if slug and slug != "unknown":
            current_slug = slug_candidates.get(identity)
            if current_slug is None or priority < current_slug[0]:
                slug_candidates[identity] = (priority, slug)
    names = {identity: name for identity, (_priority, name) in name_candidates.items()}
    slugs = {identity: slug for identity, (_priority, slug) in slug_candidates.items()}
    return identity_by_location, names, slugs


def _location_identity_map(db: Session) -> dict[UUID, str]:
    """location_id -> канонический ключ физической площадки (склейка систем)."""
    identity_by_location, _names, _slugs = _location_identity_maps(db)
    return identity_by_location


def _collect_numeric_entities(
    db: Session,
    metric: str,
    week_start: date,
) -> dict[str, _Entity]:
    links = _site_links(db)
    entities: dict[str, _Entity] = {}
    raw_by_pid: dict[UUID, list[tuple[str, int, int]]] = {}

    if metric == "runs":
        rows = _numeric_rows(db, _RUNS_SQL, week_start)
    else:
        rows = _numeric_rows(db, _VOLUNTEERING_SQL, week_start)
        rows.extend(_occasion_volunteering_rows(db, week_start))
        # parkrun-волонтёрства — отдельным проходом по кредитам профиля (без дат),
        # только для допущенных участников (см. _parkrun_eligible_ids).
        eligible_ids = _parkrun_eligible_ids(db)
        for pid, count in _parkrun_volunteer_counts(db, eligible_ids).items():
            rows.append((pid, "parkrun", count, 0))

    participant_ids = {pid for pid, _, _, _ in rows}
    for pid, code, total, week in rows:
        raw_by_pid.setdefault(pid, []).append((code, total, week))

    names = _participant_names(db, participant_ids)
    for pid, platform_values in raw_by_pid.items():
        link = links.get(pid)
        key = _entity_key(pid, link)
        entity = entities.get(key)
        if entity is None:
            entity = _Entity(key=key)
            entities[key] = entity
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            if link.display_name:
                entity.display_name = link.display_name
        if not entity.display_name:
            entity.display_name = names.get(pid)
        for code, total, week in platform_values:
            bucket = entity.values.setdefault(code, [0, 0])
            bucket[0] += total
            bucket[1] += week
            entity.total += total
            entity.week += week
    return entities


def _pick_home(win_counts_by_identity: dict[str, int]) -> tuple[str, int] | None:
    """«Топ-локация побед»: identity-ключ локации с максимумом побед.

    При равенстве побед выбор детерминирован (меньший ключ), чтобы снапшоты
    не «мигали» между пересчётами.
    """
    if not win_counts_by_identity:
        return None
    identity, wins = min(win_counts_by_identity.items(), key=lambda item: (-item[1], item[0]))
    return identity, wins


def _pick_last(dates_by_identity: dict[str, date]) -> tuple[str, date] | None:
    """Последняя победа: identity-ключ локации с самой свежей датой.

    При равенстве дат (две победы в один день на разных локациях) выбор
    детерминирован (меньший ключ), чтобы снапшоты не «мигали» между пересчётами.
    """
    if not dates_by_identity:
        return None
    identity, last = min(dates_by_identity.items(), key=lambda item: (-item[1].toordinal(), item[0]))
    return identity, last


def _apply_last_win(
    entity: _Entity,
    dates_by_identity: dict[str, date],
    identity_names: dict[str, str],
    identity_slugs: dict[str, str],
) -> None:
    last = _pick_last(dates_by_identity)
    if last is None:
        return
    identity, last_date = last
    entity.last_win_location = identity_names.get(identity, identity)
    entity.last_win_location_slug = identity_slugs.get(identity)
    entity.last_win_date = last_date


def _collect_win_entities(db: Session, week_start: date) -> dict[str, _Entity]:
    """Победы (1-е места в абсолюте): счёт по системам, «топ-локация побед» и
    последняя победа (локация + дата)."""
    links = _site_links(db)
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    rows = db.execute(text(_WIN_ROWS_SQL), {"week_start": week_start}).all()

    entities: dict[str, _Entity] = {}
    home_counts: dict[str, dict[str, int]] = {}
    last_dates: dict[str, dict[str, date]] = {}
    participant_ids = {row[0] for row in rows}
    names = _participant_names(db, participant_ids)
    for pid, code, location_id, wins, week_wins, last_date in rows:
        link = links.get(pid)
        key = _entity_key(pid, link)
        entity = entities.get(key)
        if entity is None:
            entity = _Entity(key=key)
            entities[key] = entity
        entity.participant_ids.add(pid)
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            if link.display_name:
                entity.display_name = link.display_name
        if not entity.display_name:
            entity.display_name = names.get(pid)
        bucket = entity.values.setdefault(code, [0, 0])
        bucket[0] += int(wins)
        bucket[1] += int(week_wins)
        entity.total += int(wins)
        entity.week += int(week_wins)
        identity = identity_by_location.get(location_id, str(location_id))
        by_identity = home_counts.setdefault(key, {})
        by_identity[identity] = by_identity.get(identity, 0) + int(wins)
        by_date = last_dates.setdefault(key, {})
        known = by_date.get(identity)
        by_date[identity] = max(known, last_date) if known is not None else last_date

    for key, entity in entities.items():
        home = _pick_home(home_counts.get(key, {}))
        if home is not None:
            identity, wins = home
            entity.home_location = identity_names.get(identity, identity)
            entity.home_location_wins = wins
        _apply_last_win(entity, last_dates.get(key, {}), identity_names, identity_slugs)
    return entities


def _dominant_gender(votes: dict[str, int]) -> str | None:
    """Пол участника — по большинству ненулевых меток его строк (при равенстве
    выбор детерминирован по алфавиту метки)."""
    if not votes:
        return None
    return min(votes.items(), key=lambda item: (-item[1], item[0]))[0]


def _account_gender(db: Session, participant_ids: list[UUID]) -> str | None:
    """Пол аккаунта по всей истории финишей его участников (см. комментарий у
    _PARTICIPANT_GENDER_VOTES_SQL) — независимо от того, есть ли у него первые
    места. None, если ни одного результата с определяемым полом нет (например,
    только parkrun-профиль)."""
    if not participant_ids:
        return None
    rows = db.execute(
        text(_PARTICIPANT_GENDER_VOTES_SQL), {"pids": participant_ids}
    ).all()
    votes: dict[str, int] = {}
    for _pid, gender_label, cnt in rows:
        if gender_label in ("male", "female"):
            votes[gender_label] = votes.get(gender_label, 0) + int(cnt)
    return _dominant_gender(votes)


def _gendered_win_rows(
    db: Session, week_start: date, *, pids: list[UUID] | None = None
) -> list[tuple[UUID, str, UUID, str | None, int, int, date, date]]:
    sql = _GENDERED_WIN_ROWS_SQL
    params: dict[str, object] = {"week_start": week_start}
    if pids is not None:
        sql = sql.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
        params["pids"] = pids
    else:
        sql = sql.replace("/*PIDS_FILTER*/", "")
    return [
        (row[0], row[1], row[2], row[3], int(row[4]), int(row[5]), row[6], row[7])
        for row in db.execute(text(sql), params).all()
    ]


def _collect_gendered_win_entities(
    db: Session, week_start: date, gender: str, *, as_locations: bool
) -> dict[str, _Entity]:
    """Гендерный зачёт побед (gender_position=1, без parkrun) — только участники
    запрошенного пола. as_locations=True → «локации с победами» (уникальные
    площадки), иначе «количество побед» (счёт + топ-локация побед)."""
    links = _site_links(db)
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    rows = _gendered_win_rows(db, week_start)

    # entity -> накопители; пол определяем после полного прохода по строкам.
    gender_votes: dict[str, dict[str, int]] = {}
    win_by_platform: dict[str, dict[str, list[int]]] = {}
    home_counts: dict[str, dict[str, int]] = {}
    last_dates: dict[str, dict[str, date]] = {}
    # entity -> identity -> (min first_date, {platform codes})
    loc_by_entity: dict[str, dict[str, tuple[date, set[str]]]] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    pids_by_entity: dict[str, set[UUID]] = {}
    participant_ids = {row[0] for row in rows}

    for pid, code, location_id, gender_label, wins, week_wins, first_date, last_date in rows:
        link = links.get(pid)
        key = _entity_key(pid, link)
        meta.setdefault(key, (pid, link))
        pids_by_entity.setdefault(key, set()).add(pid)
        if gender_label in ("male", "female"):
            votes = gender_votes.setdefault(key, {})
            votes[gender_label] = votes.get(gender_label, 0) + int(wins)
        platform_bucket = win_by_platform.setdefault(key, {})
        cell = platform_bucket.setdefault(code, [0, 0])
        cell[0] += int(wins)
        cell[1] += int(week_wins)
        identity = identity_by_location.get(location_id, str(location_id))
        home_counts.setdefault(key, {})[identity] = (
            home_counts.setdefault(key, {}).get(identity, 0) + int(wins)
        )
        by_date = last_dates.setdefault(key, {})
        known = by_date.get(identity)
        by_date[identity] = max(known, last_date) if known is not None else last_date
        identities = loc_by_entity.setdefault(key, {})
        existing = identities.get(identity)
        if existing is None:
            identities[identity] = (first_date, {code})
        else:
            existing[1].add(code)
            identities[identity] = (min(existing[0], first_date), existing[1])

    names = _participant_names(db, participant_ids)
    entities: dict[str, _Entity] = {}
    for key, (pid, link) in meta.items():
        if _dominant_gender(gender_votes.get(key, {})) != gender:
            continue
        entity = _Entity(key=key)
        entity.participant_ids = pids_by_entity.get(key, {pid})
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            entity.display_name = link.display_name or names.get(pid)
        else:
            entity.display_name = names.get(pid)

        if as_locations:
            for _identity, (first_date, codes) in loc_by_entity.get(key, {}).items():
                is_new = first_date >= week_start
                entity.total += 1
                if is_new:
                    entity.week += 1
                for code in codes:
                    bucket = entity.values.setdefault(code, [0, 0])
                    bucket[0] += 1
                    if is_new:
                        bucket[1] += 1
            # «Последняя» для рейтинга локаций — последняя НОВАЯ локация в
            # коллекции, поэтому дата = первая победа именно на ней.
            _apply_last_win(
                entity,
                {identity: first for identity, (first, _codes) in loc_by_entity.get(key, {}).items()},
                identity_names,
                identity_slugs,
            )
        else:
            for code, cell in win_by_platform.get(key, {}).items():
                entity.values[code] = [cell[0], cell[1]]
                entity.total += cell[0]
                entity.week += cell[1]
            home = _pick_home(home_counts.get(key, {}))
            if home is not None:
                identity, wins = home
                entity.home_location = identity_names.get(identity, identity)
                entity.home_location_wins = wins
            _apply_last_win(entity, last_dates.get(key, {}), identity_names, identity_slugs)
        entities[key] = entity
    return entities


def _collect_location_entities(
    db: Session,
    week_start: date,
    *,
    sql: str = _LOCATION_VISITS_SQL,
    with_last_win: bool = False,
) -> dict[str, _Entity]:
    """Уникальные локации участника. with_last_win — для рейтинга локаций с
    победами: дополнительно заполняет последнюю НОВУЮ локацию (её первая
    победа — самая свежая из всех «первых побед» участника)."""
    links = _site_links(db)
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    rows = db.execute(text(sql)).all()

    # entity -> identity -> (min first_date overall, {platform codes})
    per_entity: dict[str, dict[str, tuple[date, set[str]]]] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    pids_by_entity: dict[str, set[UUID]] = {}
    participant_ids = {row[0] for row in rows}
    for pid, code, location_id, first_date in rows:
        identity = identity_by_location.get(location_id, str(location_id))
        link = links.get(pid)
        key = _entity_key(pid, link)
        meta.setdefault(key, (pid, link))
        if with_last_win:
            pids_by_entity.setdefault(key, set()).add(pid)
        identities = per_entity.setdefault(key, {})
        existing = identities.get(identity)
        if existing is None:
            identities[identity] = (first_date, {code})
        else:
            best = min(existing[0], first_date)
            existing[1].add(code)
            identities[identity] = (best, existing[1])

    names = _participant_names(db, participant_ids)
    entities: dict[str, _Entity] = {}
    for key, identities in per_entity.items():
        pid, link = meta[key]
        entity = _Entity(key=key)
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            entity.display_name = link.display_name or names.get(pid)
        else:
            entity.display_name = names.get(pid)
        for _identity, (first_date, codes) in identities.items():
            is_new = first_date >= week_start
            entity.total += 1
            if is_new:
                entity.week += 1
            for code in codes:
                bucket = entity.values.setdefault(code, [0, 0])
                bucket[0] += 1
                if is_new:
                    bucket[1] += 1
        if with_last_win:
            entity.participant_ids = pids_by_entity.get(key, {pid})
            _apply_last_win(
                entity,
                {identity: first for identity, (first, _codes) in identities.items()},
                identity_names,
                identity_slugs,
            )
        entities[key] = entity
    return entities


def _best_times(db: Session, participant_ids: list[UUID]) -> dict[UUID, int]:
    """participant_id -> лучшее время (сек) по всей истории финишей."""
    if not participant_ids:
        return {}
    rows = db.execute(text(_BEST_TIME_SQL), {"pids": participant_ids}).all()
    return {row[0]: int(row[1]) for row in rows if row[1] is not None}


def _attach_best_times(db: Session, entities: dict[str, _Entity]) -> None:
    """Проставляет строкам рейтинга глобальный рекорд участника.

    У зарегистрированных на сайте строка склеена из всех их участников, и
    рекорд ищем по ВСЕМ ним, а не только по тем, где были победы: колонка
    обещает личный рекорд человека, а не рекорд «в системе, где он выигрывал».
    """
    links = _site_links(db)
    pids_by_user: dict[UUID, list[UUID]] = {}
    for pid, link in links.items():
        pids_by_user.setdefault(link.user_id, []).append(pid)

    pids_by_entity: dict[str, set[UUID]] = {}
    all_pids: set[UUID] = set()
    for key, entity in entities.items():
        pids = set(entity.participant_ids)
        if key.startswith("u:"):
            pids.update(pids_by_user.get(UUID(key[2:]), []))
        pids_by_entity[key] = pids
        all_pids |= pids

    best = _best_times(db, sorted(all_pids))
    for key, entity in entities.items():
        times = [best[pid] for pid in pids_by_entity[key] if pid in best]
        entity.best_time_sec = min(times) if times else None


def _ranked(values_desc: list[int], value: int) -> int:
    """Место (RANK-семантика) по убывающему списку значений: 1 + сколько строго больше.

    Список значений отсортирован по убыванию; bisect работает по возрастанию,
    поэтому ищем по зеркальному индексу с конца.
    """
    # число элементов строго больше value = позиция первого элемента <= value
    lo, hi = 0, len(values_desc)
    while lo < hi:
        mid = (lo + hi) // 2
        if values_desc[mid] > value:
            lo = mid + 1
        else:
            hi = mid
    return lo + 1


def _percentile(values_desc: list[int], p: float) -> int:
    """p-й процентиль (0..100) по списку, отсортированному по убыванию."""
    if not values_desc:
        return 0
    n = len(values_desc)
    idx_from_end = round((100 - p) / 100 * (n - 1))
    return values_desc[idx_from_end]


def _normalize_gender(metric: LeaderboardMetric, gender: str) -> LeaderboardGender:
    """Гендерный разрез есть только у победных метрик; для остальных — всегда all."""
    if metric in GENDERED_METRICS and gender in ("male", "female"):
        return gender  # type: ignore[return-value]
    return "all"


def _build_snapshot(
    db: Session, metric: LeaderboardMetric, gender: LeaderboardGender = "all"
) -> dict[str, object]:
    latest = latest_event_date(db)
    if latest is None:
        return {
            "metric": metric,
            "gender": gender,
            "rows": [],
            "totals_desc": [],
            "prev_totals_desc": [],
            "threshold": 0,
            "median": 0,
            "entrants": 0,
            "latest_event_date": None,
            "week_start": None,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
    week_start = _week_start(latest)

    if metric == "locations":
        entities = _collect_location_entities(db, week_start)
    elif metric == "win_locations":
        if gender == "all":
            entities = _collect_location_entities(
                db, week_start, sql=_WIN_LOCATION_VISITS_SQL, with_last_win=True
            )
        else:
            entities = _collect_gendered_win_entities(db, week_start, gender, as_locations=True)
    elif metric == "wins":
        if gender == "all":
            entities = _collect_win_entities(db, week_start)
        else:
            entities = _collect_gendered_win_entities(db, week_start, gender, as_locations=False)
    else:
        entities = _collect_numeric_entities(db, metric, week_start)

    ranked_entities = [
        e for e in entities.values() if e.total > 0 and not _is_unknown_participant_name(e.display_name)
    ]
    totals_desc = sorted((e.total for e in ranked_entities), reverse=True)
    prev_totals_desc = sorted((e.total - e.week for e in ranked_entities), reverse=True)

    metric_median = int(median(totals_desc)) if totals_desc else 0
    threshold = _percentile(totals_desc, METRIC_THRESHOLD_PERCENTILE.get(metric, 75))

    ranked_entities.sort(key=lambda e: (-e.total, e.display_name or ""))

    visible: list[_Entity] = []
    for entity in ranked_entities[: TOP_LIMIT * 2]:
        if entity.total < threshold:
            break
        visible.append(entity)
        if len(visible) >= TOP_LIMIT:
            break
    # Рекорд нужен только видимым строкам — за пределами топа его никто не увидит.
    if metric in WIN_EXTRAS_METRICS:
        _attach_best_times(db, {entity.key: entity for entity in visible})

    rows: list[dict[str, object]] = []
    for entity in visible:
        rank = _ranked(totals_desc, entity.total)
        prev_total = entity.total - entity.week
        prev_rank = _ranked(prev_totals_desc, prev_total)
        row: dict[str, object] = {
            "rank": rank,
            "rank_delta": prev_rank - rank,
            "display_name": entity.display_name,
            "site_serial_id": entity.site_serial_id,
            "platforms": {
                code: {"value": vals[0], "delta": vals[1]}
                for code, vals in entity.values.items()
            },
            "total": entity.total,
            "total_delta": entity.week,
        }
        if entity.home_location is not None:
            row["home_location"] = entity.home_location
            row["home_location_wins"] = entity.home_location_wins
        if entity.best_time_sec is not None:
            row["best_time_sec"] = entity.best_time_sec
            row["best_time_display"] = format_finish_time_display(entity.best_time_sec)
        if entity.last_win_location is not None:
            row["last_win_location"] = entity.last_win_location
            row["last_win_location_slug"] = entity.last_win_location_slug
            row["last_win_date"] = (
                entity.last_win_date.isoformat() if entity.last_win_date else None
            )
        rows.append(row)

    return {
        "metric": metric,
        "gender": gender,
        "rows": rows,
        "totals_desc": totals_desc,
        "prev_totals_desc": prev_totals_desc,
        "threshold": threshold,
        "median": metric_median,
        "entrants": len(ranked_entities),
        "latest_event_date": latest.isoformat(),
        "week_start": week_start.isoformat(),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def _cache_key(metric: str, gender: str = "all") -> str:
    # Гендерные варианты — отдельным суффиксом; all оставляет прежний ключ, чтобы
    # не плодить лишние ключи у метрик без разреза по полу.
    if gender == "all":
        return f"{CACHE_KEY_PREFIX}:{metric}"
    return f"{CACHE_KEY_PREFIX}:{metric}:{gender}"


def _read_cache(metric: str, gender: str = "all") -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(_cache_key(metric, gender))
    except Exception:
        return None
    if not raw or not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache(metric: str, payload: dict[str, object], gender: str = "all") -> None:
    try:
        get_redis_client().setex(
            _cache_key(metric, gender), CACHE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False)
        )
    except Exception:
        return


def get_leaderboard_snapshot(
    db: Session, metric: LeaderboardMetric, gender: str = "all", *, use_cache: bool = True
) -> dict[str, object]:
    resolved = _normalize_gender(metric, gender)
    if use_cache:
        cached = _read_cache(metric, resolved)
        if cached is not None:
            return cached
    payload = _build_snapshot(db, metric, resolved)
    if use_cache:
        _write_cache(metric, payload, resolved)
    return payload


def refresh_leaderboard_cache(
    db: Session, metric: LeaderboardMetric, gender: str = "all"
) -> dict[str, object]:
    """Пересчитать снапшот и перезаписать кэш, даже если тот ещё не протух.

    Для beat-задачи прогрева: обычный get_leaderboard при живом кэше ничего не
    пересчитывает, и кэш умирал бы по TTL между запусками.
    """
    resolved = _normalize_gender(metric, gender)
    payload = _build_snapshot(db, metric, resolved)
    _write_cache(metric, payload, resolved)
    return payload


def get_leaderboard(
    db: Session,
    metric: LeaderboardMetric,
    gender: str = "all",
    *,
    limit: int = 100,
    use_cache: bool = True,
) -> dict[str, object]:
    """Публичная часть снапшота (без служебных массивов рангов)."""
    resolved = _normalize_gender(metric, gender)
    snapshot = get_leaderboard_snapshot(db, metric, resolved, use_cache=use_cache)
    meta = METRIC_META[metric]
    rows = cast("list[dict[str, object]]", snapshot.get("rows") or [])
    columns = GENDERED_PLATFORM_COLUMNS if resolved != "all" else PLATFORM_COLUMNS
    return {
        "metric": metric,
        "gender": resolved,
        "title": meta["title"],
        "description": metric_description(metric, resolved),
        "unit": meta["unit"],
        "platform_columns": list(columns),
        "rows": rows[: max(1, min(limit, TOP_LIMIT))],
        "threshold": snapshot.get("threshold", 0),
        "median": snapshot.get("median", 0),
        "entrants": snapshot.get("entrants", 0),
        "latest_event_date": snapshot.get("latest_event_date"),
        "week_start": snapshot.get("week_start"),
        "built_at": snapshot.get("built_at"),
    }


def _my_numeric_values(
    db: Session, metric: str, participant_ids: list[UUID], week_start: date
) -> dict[str, list[int]]:
    if not participant_ids:
        return {}
    if metric == "runs":
        # _RUNS_SQL несёт CTE со своим GROUP BY — обычный .replace("GROUP BY", ...)
        # задел бы её тоже; сентинел однозначно указывает на внешний запрос.
        sql = _RUNS_SQL.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
        rows = db.execute(text(sql), {"week_start": week_start, "pids": participant_ids}).all()
        return {row[1]: [int(row[2]), int(row[3])] for row in rows}

    sql = _VOLUNTEERING_SQL.replace("GROUP BY", "AND vr.participant_id = ANY(:pids)\nGROUP BY")
    rows = db.execute(text(sql), {"week_start": week_start, "pids": participant_ids}).all()
    values = {row[1]: [int(row[2]), int(row[3])] for row in rows}

    occasion_raw = db.execute(
        text(_OCCASION_VOLUNTEER_ROWS_SQL + " AND vr.participant_id = ANY(:pids)"),
        {"pids": participant_ids},
    ).all()
    occasion_rows_by_platform: dict[str, list[tuple[date, str]]] = {}
    for _pid, platform_code, event_date, location_key in occasion_raw:
        occasion_rows_by_platform.setdefault(platform_code, []).append(
            (event_date, location_key or "unknown")
        )
    for platform_code, occasion_rows in occasion_rows_by_platform.items():
        total = count_volunteering_occasions(occasion_rows)
        week = count_volunteering_occasions([r for r in occasion_rows if r[0] >= week_start])
        values[platform_code] = [total, week]

    parkrun_counts = _parkrun_volunteer_counts_for(db, participant_ids)
    if parkrun_counts:
        values["parkrun"] = [parkrun_counts, 0]
    return values


def _parkrun_volunteer_counts_for(db: Session, participant_ids: list[UUID]) -> int:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT pt.id, pt.profile_extra
            FROM participants pt
            JOIN platforms p ON p.id = pt.platform_id
            JOIN volunteer_results vr ON vr.participant_id = pt.id
            WHERE p.code = 'parkrun' AND pt.id = ANY(:pids)
            """
        ),
        {"pids": participant_ids},
    ).all()
    if not rows:
        return 0
    role_rows = db.execute(
        text(
            """
            SELECT vr.participant_id, vr.role
            FROM volunteer_results vr
            WHERE vr.participant_id = ANY(:pids) AND vr.role IS NOT NULL
            """
        ),
        {"pids": [row[0] for row in rows]},
    ).all()
    roles_by_pid: dict[UUID, list[str]] = {}
    for pid, role in role_rows:
        roles_by_pid.setdefault(pid, []).append(role)
    total = 0
    for pid, profile_extra in rows:
        total += resolve_parkrun_volunteering_count(
            profile_extra=profile_extra if isinstance(profile_extra, dict) else None,
            summary_role_labels=roles_by_pid.get(pid),
        )
    return total


@dataclass
class _LastWin:
    """Последняя победа для «моей» строки: локация, ссылка на неё и дата."""

    location: str
    slug: str | None
    on_date: date


def _resolve_last_win(
    dates_by_identity: dict[str, date],
    identity_names: dict[str, str],
    identity_slugs: dict[str, str],
) -> _LastWin | None:
    last = _pick_last(dates_by_identity)
    if last is None:
        return None
    identity, last_date = last
    return _LastWin(
        location=identity_names.get(identity, identity),
        slug=identity_slugs.get(identity),
        on_date=last_date,
    )


def _my_win_values(
    db: Session, participant_ids: list[UUID], week_start: date
) -> tuple[dict[str, list[int]], tuple[str, int] | None, _LastWin | None]:
    """Победы залогиненного: значения по системам, «топ-локация побед» и
    последняя победа."""
    if not participant_ids:
        return {}, None, None
    sql = _WIN_ROWS_SQL.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
    rows = db.execute(text(sql), {"week_start": week_start, "pids": participant_ids}).all()
    if not rows:
        return {}, None, None
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    values: dict[str, list[int]] = {}
    win_counts_by_identity: dict[str, int] = {}
    last_dates: dict[str, date] = {}
    for _pid, code, location_id, wins, week_wins, last_date in rows:
        bucket = values.setdefault(code, [0, 0])
        bucket[0] += int(wins)
        bucket[1] += int(week_wins)
        identity = identity_by_location.get(location_id, str(location_id))
        win_counts_by_identity[identity] = win_counts_by_identity.get(identity, 0) + int(wins)
        known = last_dates.get(identity)
        last_dates[identity] = max(known, last_date) if known is not None else last_date
    last_win = _resolve_last_win(last_dates, identity_names, identity_slugs)
    home = _pick_home(win_counts_by_identity)
    if home is not None:
        identity, wins = home
        return values, (identity_names.get(identity, identity), wins), last_win
    return values, None, last_win


def _my_gendered_win_values(
    db: Session, participant_ids: list[UUID], week_start: date, gender: str, *, as_locations: bool
) -> tuple[dict[str, list[int]], int, int, tuple[str, int] | None, _LastWin | None]:
    """«Моя» строка в гендерном зачёте. Если пол участника не совпадает с
    запрошенным — он в этот рейтинг не входит (нули, not included)."""
    if not participant_ids:
        return {}, 0, 0, None, None
    rows = _gendered_win_rows(db, week_start, pids=participant_ids)
    if not rows:
        return {}, 0, 0, None, None
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    votes: dict[str, int] = {}
    values: dict[str, list[int]] = {}
    win_counts_by_identity: dict[str, int] = {}
    last_dates: dict[str, date] = {}
    loc_identities: dict[str, tuple[date, set[str]]] = {}
    for _pid, code, location_id, gender_label, wins, week_wins, first_date, last_date in rows:
        if gender_label in ("male", "female"):
            votes[gender_label] = votes.get(gender_label, 0) + int(wins)
        bucket = values.setdefault(code, [0, 0])
        bucket[0] += int(wins)
        bucket[1] += int(week_wins)
        identity = identity_by_location.get(location_id, str(location_id))
        win_counts_by_identity[identity] = win_counts_by_identity.get(identity, 0) + int(wins)
        known = last_dates.get(identity)
        last_dates[identity] = max(known, last_date) if known is not None else last_date
        existing = loc_identities.get(identity)
        if existing is None:
            loc_identities[identity] = (first_date, {code})
        else:
            existing[1].add(code)
            loc_identities[identity] = (min(existing[0], first_date), existing[1])

    if _dominant_gender(votes) != gender:
        return {}, 0, 0, None, None

    if as_locations:
        loc_values: dict[str, list[int]] = {}
        total = 0
        week = 0
        for _identity, (first_date, codes) in loc_identities.items():
            is_new = first_date >= week_start
            total += 1
            if is_new:
                week += 1
            for code in codes:
                cell = loc_values.setdefault(code, [0, 0])
                cell[0] += 1
                if is_new:
                    cell[1] += 1
        # «Последняя» здесь — последняя новая локация в коллекции (см.
        # _collect_location_entities): дата = первая победа именно на ней.
        last_new = _resolve_last_win(
            {identity: first for identity, (first, _codes) in loc_identities.items()},
            identity_names,
            identity_slugs,
        )
        return loc_values, total, week, None, last_new

    total = sum(v[0] for v in values.values())
    week = sum(v[1] for v in values.values())
    home = _pick_home(win_counts_by_identity)
    home_out = (identity_names.get(home[0], home[0]), home[1]) if home is not None else None
    return values, total, week, home_out, _resolve_last_win(
        last_dates, identity_names, identity_slugs
    )


def _my_location_values(
    db: Session,
    participant_ids: list[UUID],
    week_start: date,
    *,
    sql_template: str = _LOCATION_VISITS_SQL,
    with_last_win: bool = False,
) -> tuple[dict[str, list[int]], int, int, _LastWin | None]:
    if not participant_ids:
        return {}, 0, 0, None
    sql = sql_template.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
    rows = db.execute(text(sql), {"pids": participant_ids}).all()
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    identities: dict[str, tuple[date, set[str]]] = {}
    for _pid, code, location_id, first_date in rows:
        identity = identity_by_location.get(location_id, str(location_id))
        existing = identities.get(identity)
        if existing is None:
            identities[identity] = (first_date, {code})
        else:
            existing[1].add(code)
            identities[identity] = (min(existing[0], first_date), existing[1])
    values: dict[str, list[int]] = {}
    total = 0
    week = 0
    for _identity, (first_date, codes) in identities.items():
        is_new = first_date >= week_start
        total += 1
        if is_new:
            week += 1
        for code in codes:
            bucket = values.setdefault(code, [0, 0])
            bucket[0] += 1
            if is_new:
                bucket[1] += 1
    last_new = (
        _resolve_last_win(
            {identity: first for identity, (first, _codes) in identities.items()},
            identity_names,
            identity_slugs,
        )
        if with_last_win
        else None
    )
    return values, total, week, last_new


def get_my_leaderboard_row(
    db: Session, metric: LeaderboardMetric, user: User, gender: str = "all"
) -> dict[str, object]:
    """Строка залогиненного участника: значения, место, дельта места, порог."""
    resolved = _normalize_gender(metric, gender)
    snapshot = get_leaderboard_snapshot(db, metric, resolved)
    week_start_raw = snapshot.get("week_start")
    latest = latest_event_date(db)
    week_start = (
        date.fromisoformat(str(week_start_raw))
        if week_start_raw
        else _week_start(latest or date.today())
    )

    participant_ids = [
        row[0]
        for row in db.query(PlatformLink.participant_id)
        .filter(PlatformLink.user_id == user.id, PlatformLink.participant_id.isnot(None))
        .all()
    ]

    my_home: tuple[str, int] | None = None
    my_last_win: _LastWin | None = None
    if metric == "locations":
        values, total, week, _last = _my_location_values(db, participant_ids, week_start)
    elif metric == "win_locations":
        if resolved == "all":
            values, total, week, my_last_win = _my_location_values(
                db,
                participant_ids,
                week_start,
                sql_template=_WIN_LOCATION_VISITS_SQL,
                with_last_win=True,
            )
        else:
            values, total, week, my_home, my_last_win = _my_gendered_win_values(
                db, participant_ids, week_start, resolved, as_locations=True
            )
    elif metric == "wins":
        if resolved == "all":
            values, my_home, my_last_win = _my_win_values(db, participant_ids, week_start)
            total = sum(v[0] for v in values.values())
            week = sum(v[1] for v in values.values())
        else:
            values, total, week, my_home, my_last_win = _my_gendered_win_values(
                db, participant_ids, week_start, resolved, as_locations=False
            )
    else:
        values = _my_numeric_values(db, metric, participant_ids, week_start)
        total = sum(v[0] for v in values.values())
        week = sum(v[1] for v in values.values())

    threshold = int(cast(int, snapshot.get("threshold") or 0))
    totals_desc = [int(v) for v in cast("list[int]", snapshot.get("totals_desc") or [])]
    prev_totals_desc = [
        int(v) for v in cast("list[int]", snapshot.get("prev_totals_desc") or [])
    ]

    rank = _ranked(totals_desc, total) if total > 0 else None
    prev_rank = _ranked(prev_totals_desc, total - week) if total > 0 else None
    included = total >= threshold and total > 0

    # Зачёт «не про него»: в мужском/женском зачёте у участника ноль первых
    # мест — прежде чем звать «появитесь после 1», проверяем, его ли это пол
    # вообще (по всей истории финишей, не только по победам). Если пол
    # определился и не совпадает с выбранным — это не «ещё не достиг», а
    # структурно другой зачёт, порог показывать незачем.
    # Рекорд — только у победных рейтингов, и только если строка вообще в них
    # попала: в чужом гендерном зачёте показывать личное время незачем.
    my_best_time: int | None = None
    if metric in WIN_EXTRAS_METRICS and total > 0:
        best = _best_times(db, participant_ids)
        my_best_time = min(best.values()) if best else None

    gender_mismatch = False
    if not included and resolved != "all" and metric in GENDERED_METRICS:
        account_gender = _account_gender(db, participant_ids)
        gender_mismatch = account_gender is not None and account_gender != resolved

    return {
        "metric": metric,
        "display_name": user.display_name,
        "site_serial_id": user.serial_id,
        "platforms": {code: {"value": v[0], "delta": v[1]} for code, v in values.items()},
        "total": total,
        "total_delta": week,
        "rank": rank if included else None,
        "rank_delta": (prev_rank - rank) if included and rank is not None and prev_rank is not None else None,
        "included": included,
        "threshold": threshold,
        "gender_mismatch": gender_mismatch,
        "home_location": my_home[0] if my_home else None,
        "home_location_wins": my_home[1] if my_home else None,
        "best_time_sec": my_best_time,
        "best_time_display": (
            format_finish_time_display(my_best_time) if my_best_time is not None else None
        ),
        "last_win_location": my_last_win.location if my_last_win else None,
        "last_win_location_slug": my_last_win.slug if my_last_win else None,
        "last_win_date": my_last_win.on_date.isoformat() if my_last_win else None,
    }
