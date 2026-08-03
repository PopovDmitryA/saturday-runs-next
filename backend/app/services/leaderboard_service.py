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

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from app.core.redis_client import get_redis_client
from app.models import Location, Participant, Platform, PlatformLink, User
from app.parkrun.volunteer_credits import (
    resolve_parkrun_volunteering_count,
)
from app.services.co_runners_service import _is_unknown_participant_name
from app.services.home_distance_service import AMBIGUITY_RATIO, haversine_km
from app.services.location_catalog_service import (
    LocationCatalogIndex,
    russian_parkrun_location_ids,
)
from app.time_format import format_finish_time_display
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    canonical_volunteer_role,
    preset_role_keys,
    role_occasions,
)
from app.volunteering_occasions import count_volunteering_occasions

LeaderboardMetric = Literal[
    "runs",
    "volunteering",
    "volunteer_roles",
    "locations",
    "volunteer_locations",
    "wins",
    "win_locations",
    "home_distance",
]

LEADERBOARD_METRICS: tuple[LeaderboardMetric, ...] = (
    "runs",
    "volunteering",
    "volunteer_roles",
    "locations",
    "volunteer_locations",
    "wins",
    "win_locations",
    "home_distance",
)

# Мужского зачёта нет: только абсолют и женский (решение Дмитрия 03.08.2026).
# Причина — данные, а не идеология: пол мы знаем только по возрастной категории
# протокола, а её нет у финишёров без аккаунта («НЕИЗВЕСТНЫЙ» и «Нужна
# регистрация» — 6.3% строк 5 вёрст, 59% RunPark). Такой человек выпадает из
# строя мужчин, и следующий за ним мужчина получает gender_position = 1, хотя
# в протоколе он второй. По базе так набралось 783 фантомных «первых места
# среди мужчин» на 5 вёрст и 669 на RunPark — рейтинг врал заметно и в одну
# сторону. В женском зачёте эффект на порядок слабее (36 и 150 стартов):
# наверху протокола почти всегда мужчины, и женщину неопознанный обгоняет редко.
LeaderboardGender = Literal["all", "female"]

LEADERBOARD_GENDERS: tuple[LeaderboardGender, ...] = ("all", "female")

# Метрики, у которых есть женский зачёт (победы). parkrun в него не идёт:
# его пол известен только по неполным профилям и ненадёжен (см. _GENDER_LABEL_SQL).
GENDERED_METRICS: tuple[LeaderboardMetric, ...] = ("wins", "win_locations")

# Метрики с фильтром «локация засчитывается от N визитов» (перенос фильтра из
# старого дашборда Grafana): при N=3 локация даёт 1 балл, только если участник
# бегал (или волонтёрил) там минимум 3 раза. Фильтр про коллекцию площадок,
# поэтому он у «туристических» рейтингов — бегового и волонтёрского.
MIN_VISITS_METRICS: tuple[LeaderboardMetric, ...] = ("locations", "volunteer_locations")
MAX_MIN_VISITS = 5

# Единица зачёта туристических рейтингов: площадки, города или регионы (решение
# Дмитрия 02.08.2026 — «Гео-коллекционер» из бэклога хаба). Колонки «Городов» и
# «Регионов» в таблице видны всегда, а фильтр решает, что считает «Всего» и по
# чему строится место: коллекционер 30 площадок в одной Москве и человек с 10
# площадками в 10 регионах — это два разных достижения.
CountBy = Literal["locations", "cities", "regions"]
COUNT_BY_VALUES: tuple[str, ...] = ("locations", "cities", "regions")
COUNT_BY_METRICS: tuple[LeaderboardMetric, ...] = ("locations", "volunteer_locations")

# Порядок колонок-систем (как на портале: активные, затем архив parkrun).
PLATFORM_COLUMNS: tuple[str, ...] = ("five_verst", "s95", "runpark", "parkrun")
# В волонтёрском туризме parkrun не участвует вовсе: его волонтёрства приходят
# сводкой ролей на псевдоплощадку parkrun-summary, без локации и даты, а
# рейтинг — именно про площадки.
VOLUNTEER_LOCATION_PLATFORM_COLUMNS: tuple[str, ...] = ("five_verst", "s95", "runpark")

PLATFORM_LABELS: dict[str, str] = {
    "five_verst": "5 вёрст",
    "s95": "С95",
    "runpark": "RunPark",
    "parkrun": "parkrun",
}

# Фильтр «смотреть по одной системе» — стандарт всех рейтингов (решение
# Дмитрия 01.08.2026; до этого он был только у рейтинга туризма). Люди хотят
# объединённый зачёт по умолчанию, но иногда — только результаты одной
# конкретной системы, без чужих столбцов. Место и «Всего» при этом
# пересчитываются заново: это не колонка, а отдельный рейтинг.
PLATFORM_FILTER_METRICS: tuple[LeaderboardMetric, ...] = LEADERBOARD_METRICS

# Фильтр «какие роли считать» — только у рейтингов по числу выходов. В
# «Мультиволонтёре» роли и есть сама метрика (число разных освоенных),
# фильтровать их там значило бы менять смысл рейтинга.
ROLE_FILTER_METRICS: tuple[LeaderboardMetric, ...] = ("volunteering", "volunteer_locations")
PLATFORM_FILTER_VALUES: tuple[str, ...] = ("all", *PLATFORM_COLUMNS)

# Набор систем у метрики, если он отличается от общего PLATFORM_COLUMNS.
METRIC_PLATFORM_COLUMNS: dict[str, tuple[str, ...]] = {
    "volunteer_locations": VOLUNTEER_LOCATION_PLATFORM_COLUMNS,
}


def platform_columns_for(metric: str) -> tuple[str, ...]:
    """Колонки-системы рейтинга: общий набор, если метрика не переопределяет его
    в METRIC_PLATFORM_COLUMNS. От выбранного зачёта (абсолют / М / Ж) набор не
    зависит: до 02.08.2026 гендерный зачёт резал parkrun, но теперь пол его
    участников известен (см. _GENDER_LABEL_SQL)."""
    return METRIC_PLATFORM_COLUMNS.get(metric, PLATFORM_COLUMNS)


def platform_filter_values(metric: str) -> tuple[str, ...]:
    """Допустимые значения фильтра «по системе»: «все» + системы этого рейтинга.
    Система, которой в рейтинге нет (parkrun в волонтёрском туризме), кнопкой не
    предлагается и не принимается."""
    return ("all", *platform_columns_for(metric))

TOP_LIMIT = 1000
CACHE_TTL_SECONDS = 6 * 3600
# v2 — в снапшот победных рейтингов добавлены «лучшее время» и «последняя
# победа» (26.07.2026): старые кэшированные payload'ы этих полей не несут,
# поэтому ключ версионируем, а не ждём протухания по TTL.
# v3 — города/регионы у туристических рейтингов и колонка «Последняя неделя»
# (02.08.2026): по той же причине.
# v4 — parkrun вошёл в разбивку по полу (02.08.2026): состав и порядок гендерных
# рейтингов изменились, старые снапшоты пришлось бы ждать до конца TTL.
CACHE_KEY_PREFIX = "leaderboards:v4"

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
    "volunteer_roles": 75,
    "locations": 95,
    # Волонтёрский туризм — та же форма распределения, что у бегового (подавляющее
    # большинство волонтёров знает ровно одну площадку), поэтому и перцентиль тот же.
    "volunteer_locations": 95,
    "wins": 0,
    "win_locations": 0,
    # Дальность от дома: у 89% участников ровно одна площадка, и сумма у них
    # ноль — их отсекает общее правило «в рейтинг идут строки с total > 0»,
    # перцентиль сверх этого не нужен.
    "home_distance": 0,
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
    "volunteer_roles": {
        "title": "Мультиволонтёр — разнообразие ролей",
        "unit": "ролей",
        "description": (
            "Сколько РАЗНЫХ волонтёрских ролей освоил участник. Одна и та же "
            "роль, названная в системах по-разному («Сканер», «Сканирование», "
            "«Barcode Scanning»), считается одной, поэтому «Всего» — "
            "объединение ролей по всем системам, а не сумма колонок."
        ),
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
    "volunteer_locations": {
        "title": "Рейтинг волонтёрского туризма — уникальные локации",
        "unit": "локаций",
        "description": (
            "Уникальные локации, где участник волонтёрил. Одна и та же локация "
            "в разных системах считается одной. «Всего» — уникальные локации "
            "по всем системам (не сумма колонок). parkrun не участвует: его "
            "волонтёрства приходят сводкой ролей, без локации и даты."
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
    "home_distance": {
        "title": "Рейтинг дальности от дома",
        "unit": "км от дома",
        "description": (
            "Сумма расстояний от домашней локации до каждой площадки, где "
            "участник финишировал. Каждая площадка даёт свои километры один "
            "раз, сколько бы раз человек туда ни ездил. Домашняя локация — где "
            "больше всего пробежек (зарегистрированные на сайте могут выбрать "
            "её вручную в настройках). Расстояние — по прямой. «Всего» — не "
            "сумма колонок: площадка, где бегали в двух системах, попадает в "
            "обе колонки, но в зачёт идёт один раз."
        ),
    },
}

# Описание меняется под выбранный зачёт: в женском первое место — среди
# женщин, а не в абсолюте. Термин
# «первое место», а не «победа», — сознательный выбор: в parkrun-сообществе
# нет «победителей», есть «первый финишировавший» (first finisher), замыкающий
# так же почётен, как лидер.
METRIC_GENDER_DESCRIPTION: dict[str, dict[str, str]] = {
    "wins": {
        "female": (
            "Первое место среди женщин в зачёте пробежки. Топ-локация — где "
            "участница финишировала первой чаще всего. Дубли одной физической "
            "пробежки, попавшей в протоколы двух систем, не задваиваются."
        ),
    },
    "win_locations": {
        "female": (
            "Уникальные локации, где участница финишировала первой среди женщин. "
            "Одна и та же локация в разных системах считается одной. «Всего» — "
            "уникальные локации по всем системам (не сумма колонок)."
        ),
    },
}


# Заголовок и единица измерения меняются вместе с единицей зачёта: «Всего» в
# режиме городов — это города, а не площадки, и порог «появитесь после N»
# должен называть их же.
COUNT_BY_META: dict[str, dict[str, dict[str, str]]] = {
    "locations": {
        "cities": {
            "title": "Рейтинг туризма — города",
            "unit": "городов",
            "description": (
                "Уникальные ГОРОДА, где участник финишировал. Несколько площадок "
                "одного города дают один балл. Зарубежные старты считаются по "
                "стране: одна страна — один город и один регион."
            ),
        },
        "regions": {
            "title": "Рейтинг туризма — регионы",
            "unit": "регионов",
            "description": (
                "Уникальные РЕГИОНЫ, где участник финишировал. Все площадки одного "
                "региона дают один балл. Зарубежные старты считаются по стране: "
                "одна страна — один регион."
            ),
        },
    },
    "volunteer_locations": {
        "cities": {
            "title": "Рейтинг волонтёрского туризма — города",
            "unit": "городов",
            "description": (
                "Уникальные ГОРОДА, где участник волонтёрил. Несколько площадок "
                "одного города дают один балл. Зарубежные смены считаются по "
                "стране: одна страна — один город и один регион."
            ),
        },
        "regions": {
            "title": "Рейтинг волонтёрского туризма — регионы",
            "unit": "регионов",
            "description": (
                "Уникальные РЕГИОНЫ, где участник волонтёрил. Все площадки одного "
                "региона дают один балл. Зарубежные смены считаются по стране: "
                "одна страна — один регион."
            ),
        },
    },
}


def metric_title(metric: str, count_by: str = "locations") -> str:
    override = COUNT_BY_META.get(metric, {}).get(count_by)
    return override["title"] if override else METRIC_META[metric]["title"]


def metric_unit(metric: str, count_by: str = "locations") -> str:
    override = COUNT_BY_META.get(metric, {}).get(count_by)
    return override["unit"] if override else METRIC_META[metric]["unit"]


def _visits_plural(count: int) -> str:
    """«раза» / «раз» для 1..5 — больше значений у фильтра не бывает."""
    return "раз" if count == 5 else "раза"


# Чем участник «набирает» визит на локацию у туристических рейтингов — беговой
# считает финиши, волонтёрский смены.
_MIN_VISITS_VERB: dict[str, str] = {
    "locations": "финишировал",
    "volunteer_locations": "волонтёрил",
}


def metric_description(
    metric: str,
    gender: str,
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
) -> str:
    """Описание рейтинга под выбранный зачёт (абсолют / женский), порог
    визитов (только у туристических рейтингов), единицу зачёта (площадки/города/
    регионы, там же) и фильтр «по одной системе» (есть у всех) — фильтры
    комбинируются свободно."""
    parts: list[str] = []
    geo = COUNT_BY_META.get(metric, {}).get(count_by)
    if geo is not None:
        parts.append(geo["description"])
    has_visits_filter = metric in MIN_VISITS_METRICS and min_visits > 1
    if has_visits_filter:
        verb = _MIN_VISITS_VERB.get(metric, "финишировал")
        parts.append(
            f"Засчитываются только локации, где участник {verb} минимум "
            f"{min_visits} {_visits_plural(min_visits)}."
        )
    if platform != "all":
        parts.append(
            f"Показаны только результаты системы «{PLATFORM_LABELS.get(platform, platform)}» — "
            "столбцы остальных систем скрыты, «Всего» — счёт именно по ней."
        )
    elif has_visits_filter:
        # Порог визитов без фильтра систем: напоминаем, что площадка одна на все
        # системы, а визиты в них складываются в общую норму.
        parts.append(
            "Одна и та же локация в разных системах считается одной, визиты "
            "в них суммируются."
        )
        if geo is None:
            parts.append(
                "«Всего» — уникальные локации по всем системам (не сумма колонок)."
            )
    if parts:
        return " ".join(parts)
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

# Победы по parkrun засчитываются только на русских площадках. Протоколы
# зарубежного parkrun мы не собираем: от такой площадки в БД есть лишь
# пробежка самого участника из его профиля, и «первое место» по ней —
# артефакт неполных данных, а не победа (решение Дмитрия 01.08.2026).
# Список русских площадок приходит параметром: правило связки с каталогом
# живёт в Python (см. russian_parkrun_location_ids) — в нём, кроме прямой
# ссылки location_catalog_links, есть совпадение по слагу, без которого
# половина русских площадок (задвоенные строки) считалась бы зарубежной.
_RUSSIAN_PARKRUN_ONLY = (
    "AND (p.code <> 'parkrun' OR e.location_id = ANY(:russian_parkrun_locations))"
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

# То же, но построчно и с ярлыком роли: нужно, когда включён фильтр ролей —
# схлопнуть синонимы систем в канон умеет только Python (см.
# app.volunteer_role_taxonomy), поэтому агрегируем уже после фильтрации.
_VOLUNTEERING_ROLE_ROWS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    vr.role AS role,
    e.event_date AS event_date
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code = 's95'
  AND ec.secondary_event_id IS NULL
"""

_OCCASION_VOLUNTEER_ROWS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    e.event_date AS event_date,
    l.external_key AS location_key,
    vr.role AS role
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

# Роли волонтёра по системам: дата первого выхода в роли (для дельты недели) и
# число волонтёрств в ней (для «любимой роли»). Ярлыки сырые — канонизирует их Python
# (см. app.volunteer_role_taxonomy), потому что схлопывание межсистемных
# синонимов в SQL превратилось бы в нечитаемый CASE на сотню строк.
# У parkrun строки волонтёрства — сводка профиля на псевдо-локации
# «parkrun-summary» с датой 1970-01-01: в окно недели такие роли не попадут
# никогда (у parkrun дат волонтёрств нет в принципе), а их число берётся из
# счётчика кредитов в самом ярлыке — «Marshal (12×)».
_VOLUNTEER_ROLE_ROWS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    vr.role AS role,
    MIN(e.event_date) AS first_date,
    COUNT(*) AS times
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND vr.role IS NOT NULL
  AND vr.role <> ''
  AND ec.secondary_event_id IS NULL
  AND (
    p.code <> 'parkrun'
    OR EXISTS (SELECT 1 FROM parkrun_eligible pe WHERE pe.participant_id = vr.participant_id)
  )
  /*PIDS_FILTER*/
GROUP BY vr.participant_id, p.code, vr.role
"""
)


def _location_visits_sql(*, only_wins: bool) -> str:
    """Визиты участника по локациям: дата первого, всего и за последнюю неделю.

    В варианте only_wins — только финиши первым в абсолюте (first_date тогда =
    дата первой победы там). Счётчики визитов нужны фильтру «локация от N
    визитов»: из них видно и то, набрана ли норма, и то, набрана ли она именно
    на этой неделе (visits - week_visits < N ≤ visits).
    """
    # only_wins — зачёт побед, поэтому зарубежный parkrun из него выпадает
    # целиком (_RUSSIAN_PARKRUN_ONLY). В обычном туризме он остаётся: съездить
    # на зарубежный старт — настоящий визит на площадку.
    wins_filter = (
        f"AND rr.position = 1\n  {_RUSSIAN_PARKRUN_ONLY}" if only_wins else ""
    )
    return (
        _PARKRUN_ELIGIBLE_CTE
        + f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    MIN(e.event_date) AS first_date,
    COUNT(*) AS visits,
    COUNT(*) FILTER (WHERE e.event_date >= :week_start) AS week_visits
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

# Волонтёрские визиты по локациям — та же форма строк, что у беговых, так что
# рейтинг волонтёрского туризма считается тем же кодом (_collect_location_entities).
# «Визит» здесь = день волонтёрства на площадке: несколько ролей в одну субботу
# на одной локации — это одна смена, а не несколько (для порога «от N раз» важно
# именно сколько раз человек туда приезжал). Это ровно occasion-логика 5в/RunPark,
# суженная до одной локации, поэтому отдельный проход по датам тут не нужен.
# parkrun исключён: его волонтёрства приходят сводкой ролей на псевдоплощадку
# parkrun-summary с датой-заглушкой, локации у них нет.
# Построчный вариант для фильтра ролей: агрегировать визиты в SQL нельзя,
# пока роли не приведены к канону (это умеет только Python).
_VOLUNTEER_LOCATION_ROLE_ROWS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    e.event_date AS event_date,
    vr.role AS role
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code <> 'parkrun'
  AND ec.secondary_event_id IS NULL
"""

_VOLUNTEER_LOCATION_VISITS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    MIN(e.event_date) AS first_date,
    COUNT(DISTINCT e.event_date) AS visits,
    COUNT(DISTINCT e.event_date) FILTER (WHERE e.event_date >= :week_start) AS week_visits
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code <> 'parkrun'
  AND ec.secondary_event_id IS NULL
  /*PIDS_FILTER*/
GROUP BY vr.participant_id, p.code, e.location_id
"""

# Колонка «Последняя неделя»: где участник был за окно дельты — площадка и дата
# под ней, тем же видом, что «Последняя победа» в победных рейтингах. Именно
# «был», а не «прибавил» (решение Дмитрия 02.08.2026): повторный заезд на давно
# освоенную площадку +1 туризму не даёт, но в колонке всё равно виден — человек
# там был. Поэтому у всех четырёх рейтингов список берётся из одних и тех же
# выборок окна: беговые метрики читают протоколы забегов, волонтёрские —
# волонтёрские. Окно недели маленькое, поэтому выборки дешёвые.
_WEEK_RUN_LOCATIONS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
SELECT
    rr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    MAX(e.event_date) AS last_date,
    -- Форму строк держим общей с волонтёрской выборкой: у забегов роли нет,
    -- и фильтр ролей до беговых рейтингов не доходит (см. ROLE_FILTER_METRICS).
    NULL AS role
FROM run_results rr
JOIN events e ON e.id = rr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND rr.participant_id IS NOT NULL
  AND ec.secondary_event_id IS NULL
  AND e.event_date >= :week_start
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id
"""
)

# parkrun в списке недели не участвует: его волонтёрства приходят сводкой ролей
# на псевдоплощадку parkrun-summary с датой-заглушкой — ни локации, ни даты.
# Роль в выборке — чтобы колонка уважала фильтр ролей: при включённом фильтре
# «Всего» считает только разрешённые роли, и показывать в «Последней неделе»
# смену, которой в зачёте нет, было бы враньём. Канонизирует роли Python
# (см. _role_allowed), поэтому группируем ещё и по сырому ярлыку.
_WEEK_VOLUNTEER_LOCATIONS_SQL = """
SELECT
    vr.participant_id AS participant_id,
    p.code AS platform_code,
    e.location_id AS location_id,
    MAX(e.event_date) AS last_date,
    vr.role AS role
FROM volunteer_results vr
JOIN events e ON e.id = vr.event_id
JOIN platforms p ON p.id = e.platform_id
LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
WHERE e.is_test_event = false
  AND vr.participant_id IS NOT NULL
  AND p.code <> 'parkrun'
  AND ec.secondary_event_id IS NULL
  AND e.event_date >= :week_start
  /*PIDS_FILTER*/
GROUP BY vr.participant_id, p.code, e.location_id, vr.role
"""

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
  {_RUSSIAN_PARKRUN_ONLY}
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id
"""
)

# Пол результата по системам — для гендерного зачёта побед:
# 5в — первая буква age_category (М/Ж); runpark — вторая буква категории (M/W);
# s95 и parkrun — от участника, в их протоколах категории нет (у parkrun в
# run_results.age_category лежит age-grade %, а не категория).
#
# parkrun сюда добавлен 02.08.2026. До этого гендерные запросы жёстко фильтровали
# p.code <> 'parkrun': на 21.07.2026, когда разрез М/Ж делался, пол parkrun-профилей
# был собран по неполным данным. Сейчас он известен у 99.5% участников (54 890 из
# 55 141 на проде) и материализован в participants.gender (миграция 053), так что
# причина исключения отпала.
_GENDER_LABEL_SQL = """
CASE p.code
    WHEN 'five_verst' THEN CASE LEFT(rr.age_category, 1)
        WHEN 'М' THEN 'male' WHEN 'Ж' THEN 'female' END
    WHEN 'runpark' THEN CASE SUBSTRING(rr.age_category FROM 2 FOR 1)
        WHEN 'M' THEN 'male' WHEN 'W' THEN 'female' END
    WHEN 's95' THEN pt.profile_extra -> 'platform_codes' ->> 'gender'
    WHEN 'parkrun' THEN pt.gender
END
"""

# Победы среди своего пола (gender_position = 1). Одной выборкой обслуживает оба
# гендерных рейтинга: COUNT — «Количество побед», набор локаций + first_date —
# «Локации с победами». gender_label может быть NULL на части строк (напр.
# runpark без категории) — пол участника определяется в Python по большинству
# ненулевых меток его строк.
#
# parkrun живёт по тем же двум правилам, что и в абсолютном зачёте (_WIN_ROWS_SQL):
# только русские площадки и только «свои» участники (parkrun_eligible). Само по
# себе gender_position у зарубежных стартов и так NULL, но фильтр держим явным —
# он не даёт зачёту разъехаться с абсолютным, если бэкфилл когда-нибудь заполнит
# эти строки.
_GENDERED_WIN_ROWS_SQL = (
    _PARKRUN_ELIGIBLE_CTE
    + f"""
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
  AND ec.secondary_event_id IS NULL
  AND (p.code <> 'parkrun' OR {_PARKRUN_ELIGIBLE_EXISTS})
  {_RUSSIAN_PARKRUN_ONLY}
  /*PIDS_FILTER*/
GROUP BY rr.participant_id, p.code, e.location_id, gender_label
"""
)

# Пол участника по ВСЕЙ его истории финишей (любая позиция, не только 1-е
# место) — нужен, чтобы понять «этот зачёт вообще про него», когда первых
# мест в выбранном поле у него ноль (см. _account_gender): участник мужского
# пола без единой победы среди мужчин не должен видеть в женском зачёте
# «вы появитесь после 1 первого места» — это не про него в принципе.
#
# Здесь, в отличие от выборки побед, ни «русскости» площадки, ни parkrun_eligible
# не требуем: вопрос «какого пола этот человек», а не «идёт ли этот результат в
# зачёт», и лишние строки его пол не искажают.
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
    # Только у home_distance: почему домашняя локация под вопросом.
    # "ambiguous" — автовыбор шаткий (ничья или вторая площадка вровень),
    # "manual_off_top" — человек выбрал руками площадку вне своей тройки.
    home_location_note: str | None = None
    # Только у победных метрик (см. WIN_EXTRAS_METRICS): глобальный рекорд и
    # последняя победа. У win_locations «последняя» — это последняя НОВАЯ
    # локация с победой (дата = первая победа именно на ней).
    best_time_sec: int | None = None
    last_win_location: str | None = None
    last_win_location_slug: str | None = None
    last_win_date: date | None = None
    # Только для метрики volunteer_roles: «любимая роль» (чаще всего выходил) и
    # полный список освоенных ролей — он раскрывается по клику на строке.
    top_role: str | None = None
    top_role_count: int = 0
    # Детализация «роль × система × волонтёрств» — раскрывается по клику на строке.
    role_details: list[dict[str, object]] = field(default_factory=list)
    # Только у туристических рейтингов: сколько площадок / городов / регионов
    # набрано. Одно из этих трёх чисел совпадает с total — какое, решает
    # фильтр «единица зачёта» (см. CountBy).
    locations_total: int | None = None
    cities_total: int | None = None
    regions_total: int | None = None
    # Где участник был за последнюю неделю (метрики с колонкой «Последняя
    # неделя») — ОДНА площадка, самый поздний старт окна.
    week_location: dict[str, object] | None = None
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


def _entity_key(pid: UUID, link: _SiteLink | None) -> str:
    """Склейка по сайт-аккаунту для ЛЮБОГО зарегистрированного участника, в т.ч.
    приватного — приватность прячет только ссылку/имя (см. места, где читают
    link.private), а не сам факт объединения его систем в одну строку рейтинга."""
    if link is not None:
        return f"u:{link.user_id}"
    return f"p:{pid}"


def _parkrun_eligible_ids(db: Session) -> set[UUID]:
    """parkrun-участники, допущенные в рейтинги: 50%+ пробежек на русских
    (каталогизированных) площадках ИЛИ зарегистрированы на сайте — см. подробный
    комментарий у _PARKRUN_ELIGIBLE_CTE."""
    return {row[0] for row in db.execute(text(_PARKRUN_ELIGIBLE_IDS_SQL)).all()}


def _role_allowed(role: str | None, role_filter: frozenset[str]) -> bool:
    """Идёт ли роль в зачёт при включённом фильтре.

    Строка без роли (в данных такое есть) в фильтрованный зачёт не идёт: мы не
    знаем, что это было, а тихо засчитывать её в «строгий» рейтинг нечестно.
    """
    canonical = canonical_volunteer_role(role)
    return canonical is not None and canonical.key in role_filter


def _parkrun_volunteer_counts(
    db: Session, eligible_ids: set[UUID], role_filter: frozenset[str] | None = None
) -> dict[UUID, int]:
    """parkrun: волонтёрства по кредитам профиля (дат у parkrun-волонтёрств нет).

    С фильтром ролей считаем иначе — суммой кредитов только разрешённых ролей.
    Строка «Total Credits» тут неприменима: она по всем ролям сразу. Из-за
    отсутствия дат такая сумма слегка завышает зачёт (день с двумя ролями
    посчитается дважды) — это осознанный компромисс, оговорён в подсказке
    к фильтру на витрине (решение Дмитрия 02.08.2026).
    """
    profiles = {row[0]: row[1] for row in db.execute(text(_PARKRUN_VOLUNTEER_PROFILES_SQL)).all()}
    roles_by_pid: dict[UUID, list[str]] = {}
    for pid, role in db.execute(text(_PARKRUN_VOLUNTEER_ROLES_SQL)).all():
        roles_by_pid.setdefault(pid, []).append(role)
    counts: dict[UUID, int] = {}
    for pid, profile_extra in profiles.items():
        if pid not in eligible_ids:
            continue
        if role_filter is not None:
            count = _parkrun_filtered_credits(roles_by_pid.get(pid), role_filter)
        else:
            count = resolve_parkrun_volunteering_count(
                profile_extra=profile_extra if isinstance(profile_extra, dict) else None,
                summary_role_labels=roles_by_pid.get(pid),
            )
        if count > 0:
            counts[pid] = count
    return counts


def _parkrun_filtered_credits(
    role_labels: list[str] | None, role_filter: frozenset[str]
) -> int:
    """Сумма кредитов parkrun по ролям из фильтра («Marshal (12×)» → 12)."""
    if not role_labels:
        return 0
    total = 0
    for label in role_labels:
        if not _role_allowed(label, role_filter):
            continue
        # Роль без счётчика в ярлыке — это одно волонтёрство.
        total += role_occasions(label) or 1
    return total


def _occasion_volunteering_rows(
    db: Session, week_start: date, role_filter: frozenset[str] | None = None
) -> list[tuple[UUID, str, int, int]]:
    """5в и RunPark: волонтёрства через occasion-логику (см. комментарий у
    _VOLUNTEERING_SQL) — считается отдельно на каждую систему, но одинаково.

    role_filter — канонические ключи ролей, которые идут в зачёт (None — все).
    Фильтруем до схлопывания в occasions: если человек в одну субботу взял и
    отфильтрованную роль, и оставшуюся, день всё равно засчитается — по
    оставшейся, как и должно быть.
    """
    raw = db.execute(text(_OCCASION_VOLUNTEER_ROWS_SQL)).all()
    by_pid_platform: dict[tuple[UUID, str], list[tuple[date, str]]] = {}
    for pid, platform_code, event_date, location_key, role in raw:
        if role_filter is not None and not _role_allowed(role, role_filter):
            continue
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
            # Имя — тем же резолвером, что и у страницы локации: у площадок в
            # каталоге он отдаёт название действующей системы, и подпись ссылки
            # совпадает с заголовком страницы, куда она ведёт.
            name_candidates[identity] = (
                priority,
                catalog_index.display_name(location, platform_code),
            )
        slug = (location.external_key or "").strip().lower()
        if slug and slug != "unknown":
            current_slug = slug_candidates.get(identity)
            if current_slug is None or priority < current_slug[0]:
                slug_candidates[identity] = (priority, slug)
    names = {identity: name for identity, (_priority, name) in name_candidates.items()}
    slugs = {identity: slug for identity, (_priority, slug) in slug_candidates.items()}
    return identity_by_location, names, slugs


# Названия России во всех системах: у 5 вёрст/S95/RunPark страна пишется
# по-русски, у parkrun — как пришло из его каталога.
_RUSSIA_COUNTRY_NAMES = frozenset({"россия", "russia", "russian federation", "рф"})

# Одна страна под двумя написаниями — это одна страна, а значит один регион.
# На проде так живёт Британия: 2148 parkrun-площадок записаны «Великобритания»
# и ещё 274 — «United Kingdom» (проверено 02.08.2026). Без склейки турист по
# британским паркранам получал бы два региона вместо одного. Список дополнять
# по мере появления новых написаний: SELECT DISTINCT country FROM locations.
_COUNTRY_ALIASES: dict[str, str] = {
    "united kingdom": "великобритания",
    "great britain": "великобритания",
}


@dataclass(frozen=True)
class _LocationGeo:
    """Ключи города и региона площадки. None — данных нет, площадка в зачёт
    городов (или регионов) не идёт вовсе, чтобы не плодить бакет «неизвестно»."""

    city: str | None
    region: str | None


def _geo_keys(country: str | None, region: str | None, city: str | None) -> _LocationGeo:
    """Гео-ключи одной локации.

    Зарубежные старты считаются по стране: одна страна = один регион (решение
    Дмитрия 02.08.2026). Города за границей почти всегда неизвестны (у
    британских parkrun-площадок их нет ни у одной), поэтому без города вся
    страна засчитывается и за один город — так столбец не обнуляется на
    зарубежных поездках, но и не раздувается на пустых данных.

    Город ключуется парой «регион + город»: одноимённые города разных регионов
    (Троицк в Москве и в Челябинской области) — это два разных города.
    """
    country_key = (country or "").strip().lower()
    country_key = _COUNTRY_ALIASES.get(country_key, country_key)
    region_key = (region or "").strip().lower()
    city_key = (city or "").strip().lower()
    if country_key and country_key not in _RUSSIA_COUNTRY_NAMES:
        return _LocationGeo(
            city=f"{country_key}|{city_key}" if city_key else f"country:{country_key}",
            region=f"country:{country_key}",
        )
    return _LocationGeo(
        city=f"{region_key}|{city_key}" if city_key else None,
        region=region_key or None,
    )


def _location_geo_map(db: Session) -> dict[str, _LocationGeo]:
    """identity-ключ площадки -> её город и регион.

    У кросс-платформенной площадки данные берём у локации самой «старшей»
    системы (порядок PLATFORM_COLUMNS), но город и регион — независимо: у
    parkrun-строки часто нет ни того, ни другого, а у её 5-вёрстного преемника
    заполнено всё.
    """
    catalog_index = LocationCatalogIndex(db)
    rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .all()
    )
    platform_priority = {code: index for index, code in enumerate(PLATFORM_COLUMNS)}
    city_candidates: dict[str, tuple[int, str]] = {}
    region_candidates: dict[str, tuple[int, str]] = {}
    for location, platform_code in rows:
        identity = catalog_index.canonical_identity_key(location, platform_code)
        priority = platform_priority.get(platform_code, len(PLATFORM_COLUMNS))
        geo = _geo_keys(location.country, location.region, location.city)
        if geo.city is not None:
            current = city_candidates.get(identity)
            if current is None or priority < current[0]:
                city_candidates[identity] = (priority, geo.city)
        if geo.region is not None:
            current = region_candidates.get(identity)
            if current is None or priority < current[0]:
                region_candidates[identity] = (priority, geo.region)
    geo_map: dict[str, _LocationGeo] = {}
    for identity in set(city_candidates) | set(region_candidates):
        city = city_candidates.get(identity)
        region = region_candidates.get(identity)
        geo_map[identity] = _LocationGeo(
            city=city[1] if city is not None else None,
            region=region[1] if region is not None else None,
        )
    return geo_map


def _location_coordinates_map(db: Session) -> dict[str, tuple[float, float]]:
    """identity-ключ площадки -> её координаты.

    Через LocationCatalogIndex.coordinates_for, поэтому parkrun-строка получает
    точку своего действующего преемника, а не остаётся без координат. У
    зарубежных parkrun координаты проставлены бэкфиллом из мирового каталога
    (см. backend/scripts/backfill_parkrun_coordinates.py); у оставшихся закрытых
    площадок их нет — такая площадка в сумму километров не идёт.
    """
    catalog_index = LocationCatalogIndex(db)
    rows = (
        db.query(Location, Platform.code)
        .join(Platform, Location.platform_id == Platform.id)
        .all()
    )
    platform_priority = {code: index for index, code in enumerate(PLATFORM_COLUMNS)}
    best: dict[str, tuple[int, tuple[float, float]]] = {}
    for location, platform_code in rows:
        latitude, longitude = catalog_index.coordinates_for(location, platform_code)
        if latitude is None or longitude is None:
            continue
        identity = catalog_index.canonical_identity_key(location, platform_code)
        priority = platform_priority.get(platform_code, len(PLATFORM_COLUMNS))
        current = best.get(identity)
        if current is None or priority < current[0]:
            best[identity] = (priority, (latitude, longitude))
    return {identity: coords for identity, (_priority, coords) in best.items()}


def _manual_home_identity_keys(db: Session) -> dict[UUID, str]:
    """user_id -> домашняя локация, выбранная руками в настройках.

    Рейтинг считается по всем участникам, у большинства дом определяется
    автоматически (где больше пробежек). Но если человек завёл кабинет и
    поправил выбор — уважаем его: иначе рейтинг спорил бы с тем, что тот же
    сайт показывает ему на главной.
    """
    rows = db.query(User.id, User.home_location_key).filter(User.home_location_key.isnot(None)).all()
    return {user_id: str(key) for user_id, key in rows if key}


def _row_params(db: Session, week_start: date, **extra: object) -> dict[str, object]:
    """Общие параметры сырых выборок рейтингов.

    russian_parkrun_locations нужен только зачётам побед (_RUSSIAN_PARKRUN_ONLY),
    но передаётся всем: лишние bind-параметры text() игнорирует, а один набор
    параметров на все выборки избавляет от дублирования на каждом вызове.
    """
    return {
        "week_start": week_start,
        "russian_parkrun_locations": list(russian_parkrun_location_ids(db)),
        **extra,
    }


class _MetricSource:
    """Данные снапшота, не зависящие от фильтров: SQL-выборки и справочники.

    И порог визитов, и фильтр «по одной системе» применяются уже в Python, над
    одними и теми же сырыми строками, — значит вся сетка вариантов одного
    рейтинга (до 5 порогов × 5 систем) считается с одного чтения базы. Обычный
    запрос создаёт источник на себя и выбрасывает; прогрев кэша переиспользует
    один на метрику, иначе десятки вариантов означали бы десятки полных
    сканирований протоколов.

    Справочники (привязки к сайту, имена, идентичность локаций) переживают
    release() — они одни на все рейтинги; сырые выборки метрики отпускаются,
    чтобы пик памяти остался таким же, как при расчёте одного снапшота.
    """

    def __init__(self, db: Session, latest: date) -> None:
        self.db = db
        self.latest = latest
        self.week_start = _week_start(latest)
        self._links: dict[UUID, _SiteLink] | None = None
        self._identity: tuple[dict[UUID, str], dict[str, str], dict[str, str]] | None = None
        self._geo: dict[str, _LocationGeo] | None = None
        self._coordinates: dict[str, tuple[float, float]] | None = None
        self._manual_homes: dict[UUID, str] | None = None
        self._names: dict[UUID, str | None] | None = None
        self._rows: dict[str, Sequence[Row[Any]]] = {}
        # Волонтёрские выборки зависят от фильтра ролей, поэтому кэшируются
        # по нему: базовый вариант (None) и каждый набор ролей — своя запись.
        self._occasion_rows: dict[frozenset[str] | None, list[tuple[UUID, str, int, int]]] = {}
        self._parkrun_volunteering: dict[frozenset[str] | None, dict[UUID, int]] = {}

    def links(self) -> dict[UUID, _SiteLink]:
        if self._links is None:
            self._links = _site_links(self.db)
        return self._links

    def identity_maps(self) -> tuple[dict[UUID, str], dict[str, str], dict[str, str]]:
        if self._identity is None:
            self._identity = _location_identity_maps(self.db)
        return self._identity

    def geo_map(self) -> dict[str, _LocationGeo]:
        """Города и регионы площадок — справочник общий на все рейтинги, как и
        identity_maps, поэтому переживает release()."""
        if self._geo is None:
            self._geo = _location_geo_map(self.db)
        return self._geo

    def coordinates_map(self) -> dict[str, tuple[float, float]]:
        """Координаты площадок — справочник того же рода, что geo_map."""
        if self._coordinates is None:
            self._coordinates = _location_coordinates_map(self.db)
        return self._coordinates

    def manual_home_keys(self) -> dict[UUID, str]:
        if self._manual_homes is None:
            self._manual_homes = _manual_home_identity_keys(self.db)
        return self._manual_homes

    def names(self) -> dict[UUID, str | None]:
        """Имена всех участников: справочник целиком дешевле, чем гигантский IN
        по идентификаторам из выборки метрики."""
        if self._names is None:
            self._names = {
                row[0]: row[1]
                for row in self.db.query(Participant.id, Participant.display_name).all()
            }
        return self._names

    def rows(self, sql: str) -> Sequence[Row[Any]]:
        cached = self._rows.get(sql)
        if cached is None:
            cached = self.db.execute(text(sql), _row_params(self.db, self.week_start)).all()
            self._rows[sql] = cached
        return cached

    def occasion_volunteering_rows(
        self, role_filter: frozenset[str] | None = None
    ) -> list[tuple[UUID, str, int, int]]:
        cached = self._occasion_rows.get(role_filter)
        if cached is None:
            cached = _occasion_volunteering_rows(self.db, self.week_start, role_filter)
            self._occasion_rows[role_filter] = cached
        return cached

    def parkrun_volunteering(self, role_filter: frozenset[str] | None = None) -> dict[UUID, int]:
        cached = self._parkrun_volunteering.get(role_filter)
        if cached is None:
            cached = _parkrun_volunteer_counts(
                self.db, _parkrun_eligible_ids(self.db), role_filter
            )
            self._parkrun_volunteering[role_filter] = cached
        return cached

    def release(self) -> None:
        """Отпустить сырые данные метрики, оставив общие справочники локаций и
        привязок — их переиспользует следующий рейтинг."""
        self._rows.clear()
        self._names = None
        self._occasion_rows.clear()
        self._parkrun_volunteering.clear()


def make_snapshot_source(db: Session) -> _MetricSource | None:
    """Источник для серии снапшотов (прогрев кэша). None — если событий вообще
    нет: тогда снапшоты пустые и считать нечего."""
    latest = latest_event_date(db)
    if latest is None:
        return None
    return _MetricSource(db, latest)


def _s95_volunteering_rows(
    source: _MetricSource, role_filter: frozenset[str]
) -> list[tuple[UUID, str, int, int]]:
    """С95 с фильтром ролей: считаем строки сами, оставив разрешённые роли."""
    totals: dict[UUID, list[int]] = {}
    for pid, _platform_code, role, event_date in source.rows(_VOLUNTEERING_ROLE_ROWS_SQL):
        if not _role_allowed(role, role_filter):
            continue
        bucket = totals.setdefault(pid, [0, 0])
        bucket[0] += 1
        if event_date >= source.week_start:
            bucket[1] += 1
    return [(pid, "s95", total, week) for pid, (total, week) in totals.items()]


# Сколько площадок недели показываем в строке: за окно дельты человек успевает
# побывать на одной-двух, длинный хвост бывает только у волонтёров, которые
# объезжают несколько площадок. Остальное фронт свернёт в «+N».

# Выборка окна недели для каждой метрики с колонкой «Последняя неделя»: беговые
# рейтинги читают протоколы забегов, волонтёрские — волонтёрские смены. У
# победных рейтингов колонки нет — её место занимает «Последняя победа».
_WEEK_LOCATIONS_SQL_BY_METRIC: dict[str, str] = {
    "runs": _WEEK_RUN_LOCATIONS_SQL,
    "locations": _WEEK_RUN_LOCATIONS_SQL,
    "home_distance": _WEEK_RUN_LOCATIONS_SQL,
    "volunteering": _WEEK_VOLUNTEER_LOCATIONS_SQL,
    "volunteer_locations": _WEEK_VOLUNTEER_LOCATIONS_SQL,
}

WEEK_LOCATIONS_METRICS: tuple[LeaderboardMetric, ...] = cast(
    "tuple[LeaderboardMetric, ...]", tuple(_WEEK_LOCATIONS_SQL_BY_METRIC)
)

# Таблица, по которой фильтруются участники «моей» строки: у беговых выборок это
# run_results (rr), у волонтёрских — volunteer_results (vr).
_WEEK_LOCATIONS_PIDS_ALIAS: dict[str, str] = {
    "runs": "rr",
    "locations": "rr",
    "home_distance": "rr",
    "volunteering": "vr",
    "volunteer_locations": "vr",
}


def _latest_week_location(
    dates: dict[str, date],
    identity_names: dict[str, str],
    identity_slugs: dict[str, str],
) -> dict[str, object] | None:
    """Последний старт окна: ОДНА площадка, самая поздняя по дате.

    Список площадок в ячейке не показываем никогда (решение Дмитрия
    03.08.2026): колонка отвечает на вопрос «где человек был в последний раз»,
    а перечисление рядом с числовыми колонками читалось как приращение.
    Ничью по дате разводим названием, чтобы выбор не «дышал» между пересчётами.
    """

    def name_of(identity: str) -> str:
        return identity_names.get(identity, identity)

    if not dates:
        return None
    identity, on_date = min(
        dates.items(), key=lambda item: (-item[1].toordinal(), name_of(item[0]))
    )
    return {
        "name": name_of(identity),
        "slug": identity_slugs.get(identity),
        "date": on_date.isoformat(),
    }


def _attach_week_locations(
    source: _MetricSource,
    entities: dict[str, _Entity],
    sql: str,
    platform: str,
    role_filter: frozenset[str] | None = None,
) -> None:
    """Проставить строкам «где был за последнюю неделю» по сырой выборке окна.

    role_filter действует и здесь: если «Всего» считает только разрешённые роли,
    то и площадки недели показываем только те, где человек выходил именно в них.
    """
    links = source.links()
    identity_by_location, identity_names, identity_slugs = source.identity_maps()
    per_entity: dict[str, dict[str, date]] = {}
    for pid, code, location_id, last_date, role in source.rows(sql):
        if platform != "all" and code != platform:
            continue
        if role_filter is not None and not _role_allowed(role, role_filter):
            continue
        key = _entity_key(pid, links.get(pid))
        if key not in entities:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        by_identity = per_entity.setdefault(key, {})
        known = by_identity.get(identity)
        by_identity[identity] = max(known, last_date) if known is not None else last_date
    for key, dates in per_entity.items():
        entities[key].week_location = _latest_week_location(
            dates, identity_names, identity_slugs
        )


def _collect_numeric_entities(
    source: _MetricSource,
    metric: str,
    platform: str = "all",
    role_filter: frozenset[str] | None = None,
) -> dict[str, _Entity]:
    links = source.links()
    entities: dict[str, _Entity] = {}
    raw_by_pid: dict[UUID, list[tuple[str, int, int]]] = {}

    if metric == "runs":
        rows = [
            (row[0], row[1], int(row[2]), int(row[3])) for row in source.rows(_RUNS_SQL)
        ]
    elif role_filter is not None:
        # С фильтром ролей С95 считаем построчно: агрегат из _VOLUNTEERING_SQL
        # роли не знает, а канонизировать синонимы систем умеет только Python.
        rows = _s95_volunteering_rows(source, role_filter)
        rows.extend(source.occasion_volunteering_rows(role_filter))
        for pid, count in source.parkrun_volunteering(role_filter).items():
            rows.append((pid, "parkrun", count, 0))
    else:
        rows = [
            (row[0], row[1], int(row[2]), int(row[3]))
            for row in source.rows(_VOLUNTEERING_SQL)
        ]
        rows.extend(source.occasion_volunteering_rows())
        # parkrun-волонтёрства — отдельным проходом по кредитам профиля (без дат),
        # только для допущенных участников (см. _parkrun_eligible_ids).
        for pid, count in source.parkrun_volunteering().items():
            rows.append((pid, "parkrun", count, 0))

    if platform != "all":
        rows = [row for row in rows if row[1] == platform]

    for pid, code, total, week in rows:
        raw_by_pid.setdefault(pid, []).append((code, total, week))

    names = source.names()
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


@dataclass
class _RoleUsage:
    """Освоенная роль: когда впервые вышел в ней и сколько раз волонтёрил."""

    first_date: date
    times: int


def _my_volunteer_role_rows(
    db: Session, pids: list[UUID]
) -> list[tuple[UUID, str, str, date, int]]:
    """Те же строки ролей, но только по участникам залогиненного (строка «Вы»)."""
    sql = _VOLUNTEER_ROLE_ROWS_SQL.replace(
        "/*PIDS_FILTER*/", "AND vr.participant_id = ANY(:pids)"
    )
    return [
        (row[0], row[1], row[2], row[3], int(row[4]))
        for row in db.execute(text(sql), {"pids": pids}).all()
    ]


def _add_role_row(
    by_platform: dict[str, dict[str, _RoleUsage]],
    labels: dict[str, str],
    platform_code: str,
    raw_role: str,
    first_date: date,
    times: int,
) -> None:
    """Свести сырую строку роли в счётчики канонической роли внутри системы.

    Разные ярлыки одной системы могут схлопнуться в одну каноническую роль
    (у С95 «Сканер» и «Сканер 25» — это одна роль, просто с вехой), поэтому
    строки суммируются, а не перезаписываются.
    """
    canonical = canonical_volunteer_role(raw_role)
    if canonical is None:
        return
    labels.setdefault(canonical.key, canonical.label)
    # parkrun не хранит отдельные волонтёрства — только сводку «роль × кредитов».
    occasions = role_occasions(raw_role)
    weight = occasions if occasions is not None else times
    roles = by_platform.setdefault(platform_code, {})
    existing = roles.get(canonical.key)
    if existing is None:
        roles[canonical.key] = _RoleUsage(first_date=first_date, times=weight)
        return
    existing.first_date = min(existing.first_date, first_date)
    existing.times += weight


@dataclass
class _RoleSummary:
    """Итог по ролям одной строки рейтинга."""

    # platform code -> [ролей всего, из них освоено на этой неделе]
    values: dict[str, list[int]]
    total: int
    week: int
    top_role: tuple[str, int] | None
    # Детализация для разворачивания строки: роль → сколько волонтёрств в
    # каждой системе. Порядок — по их числу, как и «любимая роль».
    details: list[dict[str, object]]


def _summarize_roles(
    by_platform: dict[str, dict[str, _RoleUsage]],
    labels: dict[str, str],
    week_start: date,
) -> _RoleSummary:
    """Значения по системам, «всего» (объединение ролей), дельта недели,
    любимая роль и построчная детализация «роль × система × волонтёрств»."""
    values: dict[str, list[int]] = {}
    merged: dict[str, _RoleUsage] = {}
    for code, roles in by_platform.items():
        cell = values.setdefault(code, [0, 0])
        for role_key, usage in roles.items():
            cell[0] += 1
            if usage.first_date >= week_start:
                cell[1] += 1
            existing = merged.get(role_key)
            if existing is None:
                merged[role_key] = _RoleUsage(first_date=usage.first_date, times=usage.times)
            else:
                existing.first_date = min(existing.first_date, usage.first_date)
                existing.times += usage.times
    total = len(merged)
    # Роль «новая», если ПЕРВЫЙ выход в ней случился в окне недели — по всем
    # системам сразу: освоил в 5 вёрст год назад, повторил в С95 вчера — роль
    # не новая.
    week = sum(1 for usage in merged.values() if usage.first_date >= week_start)
    ordered = sorted(
        merged.items(), key=lambda item: (-item[1].times, labels.get(item[0], item[0]))
    )
    top = (labels.get(ordered[0][0], ordered[0][0]), ordered[0][1].times) if ordered else None
    details: list[dict[str, object]] = []
    for role_key, usage in ordered:
        by_code = {
            code: roles[role_key].times
            for code, roles in by_platform.items()
            if role_key in roles
        }
        # Дату первого выхода в детализацию не выносим: у parkrun её нет (сводка
        # профиля лежит на псевдо-событии 1970-01-01), и колонка «освоена с»
        # врала бы ровно там, где у человека есть parkrun.
        details.append(
            {
                "role": labels.get(role_key, role_key),
                "total": usage.times,
                "platforms": by_code,
            }
        )
    return _RoleSummary(
        values=values, total=total, week=week, top_role=top, details=details
    )


def _collect_volunteer_role_entities(
    source: _MetricSource, platform: str = "all"
) -> dict[str, _Entity]:
    """Мультиволонтёр: число РАЗНЫХ ролей, канонизированных между системами."""
    links = source.links()
    rows = source.rows(_VOLUNTEER_ROLE_ROWS_SQL.replace("/*PIDS_FILTER*/", ""))

    per_entity: dict[str, dict[str, dict[str, _RoleUsage]]] = {}
    labels: dict[str, str] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    for pid, code, raw_role, first_date, times in rows:
        if platform != "all" and code != platform:
            continue
        link = links.get(pid)
        key = _entity_key(pid, link)
        meta.setdefault(key, (pid, link))
        _add_role_row(
            per_entity.setdefault(key, {}), labels, code, raw_role, first_date, int(times)
        )

    names = source.names()
    entities: dict[str, _Entity] = {}
    for key, by_platform in per_entity.items():
        pid, link = meta[key]
        entity = _Entity(key=key)
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            entity.display_name = link.display_name or names.get(pid)
        else:
            entity.display_name = names.get(pid)
        summary = _summarize_roles(by_platform, labels, source.week_start)
        entity.values = summary.values
        entity.total = summary.total
        entity.week = summary.week
        entity.role_details = summary.details
        if summary.top_role is not None:
            entity.top_role, entity.top_role_count = summary.top_role
        entities[key] = entity
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


def _collect_win_entities(source: _MetricSource, platform: str = "all") -> dict[str, _Entity]:
    """Победы (1-е места в абсолюте): счёт по системам, «топ-локация побед» и
    последняя победа (локация + дата). При фильтре по системе все три — включая
    топ-локацию и последнюю победу — считаются только по её протоколам."""
    links = source.links()
    identity_by_location, identity_names, identity_slugs = source.identity_maps()
    rows = source.rows(_WIN_ROWS_SQL)

    entities: dict[str, _Entity] = {}
    home_counts: dict[str, dict[str, int]] = {}
    last_dates: dict[str, dict[str, date]] = {}
    names = source.names()
    for pid, code, location_id, wins, week_wins, last_date in rows:
        if platform != "all" and code != platform:
            continue
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
    params: dict[str, object] = _row_params(db, week_start)
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
    source: _MetricSource, gender: str, *, as_locations: bool, platform: str = "all"
) -> dict[str, _Entity]:
    """Гендерный зачёт побед (gender_position=1) — только участники
    запрошенного пола. as_locations=True → «локации с победами» (уникальные
    площадки), иначе «количество побед» (счёт + топ-локация побед)."""
    links = source.links()
    identity_by_location, identity_names, identity_slugs = source.identity_maps()
    rows = source.rows(_GENDERED_WIN_ROWS_SQL.replace("/*PIDS_FILTER*/", ""))

    # entity -> накопители; пол определяем после полного прохода по строкам.
    gender_votes: dict[str, dict[str, int]] = {}
    win_by_platform: dict[str, dict[str, list[int]]] = {}
    home_counts: dict[str, dict[str, int]] = {}
    last_dates: dict[str, dict[str, date]] = {}
    # entity -> identity -> (min first_date, {platform codes})
    loc_by_entity: dict[str, dict[str, tuple[date, set[str]]]] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    pids_by_entity: dict[str, set[UUID]] = {}

    for pid, code, location_id, gender_label, wins, week_wins, first_date, last_date in rows:
        link = links.get(pid)
        key = _entity_key(pid, link)
        # Пол участника считаем по ВСЕМ его строкам, до фильтра по системе:
        # иначе человек мог бы менять пол при переключении вкладки-системы.
        if gender_label in ("male", "female"):
            votes = gender_votes.setdefault(key, {})
            votes[gender_label] = votes.get(gender_label, 0) + int(wins)
        if platform != "all" and code != platform:
            continue
        meta.setdefault(key, (pid, link))
        pids_by_entity.setdefault(key, set()).add(pid)
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

    names = source.names()
    week_start = source.week_start
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


@dataclass
class _LocationVisits:
    """Визиты участника на одну физическую площадку (сумма по её системам)."""

    first_date: date
    codes: set[str] = field(default_factory=set)
    visits: int = 0
    week_visits: int = 0
    # Визиты по каждой системе отдельно (code -> [visits, week_visits): порог
    # визитов в колонке конкретной системы должен набираться ЕЙ САМОЙ, а не
    # суммой систем — иначе один визит на 5В + один на S95 (в «Всего» это
    # законно 2+) попал бы в зачёт колонки «5В», хотя там всего 1 визит.
    by_platform: dict[str, list[int]] = field(default_factory=dict)

    def counts(self, min_visits: int) -> bool:
        """Локация набрала норму визитов и идёт в зачёт."""
        return self.visits >= min_visits

    def is_new(self, min_visits: int) -> bool:
        """Норма набрана именно на этой неделе — локация «прибавилась».

        При min_visits=1 это ровно прежняя семантика «первый визит на неделе».
        """
        return self.visits - self.week_visits < min_visits

    def platform_counts(self, code: str, min_visits: int) -> bool:
        """Порог визитов набран именно в этой системе (без суммирования с другими)."""
        cell = self.by_platform.get(code)
        return cell is not None and cell[0] >= min_visits

    def platform_is_new(self, code: str, min_visits: int) -> bool:
        cell = self.by_platform[code]
        return cell[0] - cell[1] < min_visits


def _merge_visit_row(
    identities: dict[str, _LocationVisits],
    identity: str,
    code: str,
    first_date: date,
    visits: int,
    week_visits: int,
) -> None:
    """Свести строку (система × локация) в счётчики физической площадки."""
    existing = identities.get(identity)
    if existing is None:
        existing = _LocationVisits(
            first_date=first_date, codes={code}, visits=visits, week_visits=week_visits
        )
        existing.by_platform[code] = [visits, week_visits]
        identities[identity] = existing
        return
    existing.first_date = min(existing.first_date, first_date)
    existing.codes.add(code)
    existing.visits += visits
    existing.week_visits += week_visits
    cell = existing.by_platform.setdefault(code, [0, 0])
    cell[0] += visits
    cell[1] += week_visits


def _volunteer_location_rows_filtered(
    source: _MetricSource, role_filter: frozenset[str]
) -> list[tuple[UUID, str, UUID, date, int, int]]:
    """Строки волонтёрского туризма с фильтром ролей.

    Формат тот же, что у _VOLUNTEER_LOCATION_VISITS_SQL, но визиты считаются по
    датам, оставшимся после фильтра: локация набирает порог только теми днями,
    где человек выходил в разрешённой роли.
    """
    dates: dict[tuple[UUID, str, UUID], set[date]] = {}
    for pid, code, location_id, event_date, role in source.rows(
        _VOLUNTEER_LOCATION_ROLE_ROWS_SQL
    ):
        if not _role_allowed(role, role_filter):
            continue
        dates.setdefault((pid, code, location_id), set()).add(event_date)
    rows: list[tuple[UUID, str, UUID, date, int, int]] = []
    for (pid, code, location_id), event_dates in dates.items():
        week = sum(1 for d in event_dates if d >= source.week_start)
        rows.append((pid, code, location_id, min(event_dates), len(event_dates), week))
    return rows


_EMPTY_GEO = _LocationGeo(city=None, region=None)


@dataclass
class _UnitTally:
    """Итог по одной единице зачёта (площадки / города / регионы)."""

    total: int
    week: int
    # platform code -> [всего, из них прибавилось на этой неделе]
    values: dict[str, list[int]]


def _unit_key_getters(
    geo: dict[str, _LocationGeo],
) -> dict[str, Callable[[str], str | None]]:
    """Единица зачёта -> как получить её ключ из identity-ключа площадки.

    None означает «площадка в этот зачёт не идёт»: у локации нет ни города, ни
    региона (так бывает у части parkrun-площадок) — считать её отдельным
    «неизвестным» городом было бы враньём.
    """
    return {
        "locations": lambda identity: identity,
        "cities": lambda identity: geo.get(identity, _EMPTY_GEO).city,
        "regions": lambda identity: geo.get(identity, _EMPTY_GEO).region,
    }


def _unit_counts(
    counted: dict[str, _LocationVisits],
    unit_key: Callable[[str], str | None],
    min_visits: int,
) -> _UnitTally:
    """Свести засчитанные площадки в единицы зачёта.

    Для «площадок» единица = сама площадка, и результат совпадает с прежним
    построчным подсчётом; для городов и регионов несколько площадок схлопываются
    в одну единицу. Единица «прибавилась на этой неделе», только если ВСЕ её
    засчитанные площадки взяты именно сейчас: второй парк в давно освоенном
    городе прибавляет площадку, но не город.
    """
    groups: dict[str, list[_LocationVisits]] = {}
    for identity, visits in counted.items():
        unit = unit_key(identity)
        if unit is None:
            continue
        groups.setdefault(unit, []).append(visits)

    values: dict[str, list[int]] = {}
    total = 0
    week = 0
    for group in groups.values():
        total += 1
        if all(visits.is_new(min_visits) for visits in group):
            week += 1
        # Система «даёт» единицу, если хотя бы одна её площадка набрала порог
        # визитов силами именно этой системы (см. _LocationVisits.by_platform).
        counted_by_code: dict[str, list[_LocationVisits]] = {}
        for visits in group:
            for code in visits.codes:
                if visits.platform_counts(code, min_visits):
                    counted_by_code.setdefault(code, []).append(visits)
        for code, in_code in counted_by_code.items():
            cell = values.setdefault(code, [0, 0])
            cell[0] += 1
            if all(visits.platform_is_new(code, min_visits) for visits in in_code):
                cell[1] += 1
    return _UnitTally(total=total, week=week, values=values)


def _collect_location_entities(
    source: _MetricSource,
    *,
    sql: str = _LOCATION_VISITS_SQL,
    with_last_win: bool = False,
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    with_geo: bool = False,
    role_filter: frozenset[str] | None = None,
) -> dict[str, _Entity]:
    """Уникальные локации участника. with_last_win — для рейтинга локаций с
    победами: дополнительно заполняет последнюю НОВУЮ локацию (её первая
    победа — самая свежая из всех «первых побед» участника). min_visits —
    порог визитов, ниже которого локация в зачёт не идёт. platform — фильтр
    «смотреть по одной системе»: локации других систем просто не попадают в
    выборку (мерж по identity тут ни при чём — сырые строки уже отфильтрованы
    по коду системы, так что дополнительной SQL-выборки не нужно). with_geo —
    туристические рейтинги: считать заодно города и регионы (и ранжировать по
    count_by), плюс заполнять «Последнюю неделю»."""
    links = source.links()
    identity_by_location, identity_names, identity_slugs = source.identity_maps()
    getters = _unit_key_getters(source.geo_map() if with_geo else {})
    units = COUNT_BY_VALUES if with_geo else ("locations",)
    rows = (
        _volunteer_location_rows_filtered(source, role_filter)
        if role_filter is not None
        else source.rows(sql)
    )

    per_entity: dict[str, dict[str, _LocationVisits]] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    pids_by_entity: dict[str, set[UUID]] = {}
    for pid, code, location_id, first_date, visits, week_visits in rows:
        if platform != "all" and code != platform:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        link = links.get(pid)
        key = _entity_key(pid, link)
        meta.setdefault(key, (pid, link))
        if with_last_win:
            pids_by_entity.setdefault(key, set()).add(pid)
        _merge_visit_row(
            per_entity.setdefault(key, {}),
            identity,
            code,
            first_date,
            int(visits),
            int(week_visits),
        )

    names = source.names()
    entities: dict[str, _Entity] = {}
    for key, identities in per_entity.items():
        pid, link = meta[key]
        entity = _Entity(key=key)
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            entity.display_name = link.display_name or names.get(pid)
        else:
            entity.display_name = names.get(pid)
        counted = {
            identity: visits
            for identity, visits in identities.items()
            if visits.counts(min_visits)
        }
        tallies = {unit: _unit_counts(counted, getters[unit], min_visits) for unit in units}
        selected = tallies[count_by if count_by in tallies else "locations"]
        entity.total = selected.total
        entity.week = selected.week
        entity.values = selected.values
        if with_geo:
            entity.locations_total = tallies["locations"].total
            entity.cities_total = tallies["cities"].total
            entity.regions_total = tallies["regions"].total
        if with_last_win:
            entity.participant_ids = pids_by_entity.get(key, {pid})
            _apply_last_win(
                entity,
                {identity: visits.first_date for identity, visits in counted.items()},
                identity_names,
                identity_slugs,
            )
        entities[key] = entity
    return entities


def _home_identity(
    identities: dict[str, _LocationVisits],
    identity_names: dict[str, str],
    manual_key: str | None,
) -> str | None:
    """Домашняя площадка участника: выбранная руками, иначе — где больше визитов.

    Ничьи разводим по названию, чтобы рейтинг не «дышал» между пересчётами:
    у 4846 участников первое место делят две площадки.
    """
    if manual_key is not None and manual_key in identities:
        return manual_key
    if not identities:
        return None
    return min(
        identities,
        key=lambda identity: (
            -identities[identity].visits,
            identity_names.get(identity, identity).casefold(),
        ),
    )


def _home_location_note(
    identities: dict[str, _LocationVisits],
    identity_names: dict[str, str],
    home: str,
    manual_key: str | None,
) -> str | None:
    """Почему домашняя локация под вопросом — или None, если вопросов нет.

    Автовыбор берёт площадку с максимумом визитов, поэтому «вне топ-3» у него
    не бывает в принципе; зато бывает ничья или вторая площадка вровень —
    тогда километры вполне могли бы считаться от другой точки. Ручной выбор
    наоборот: человек уже решил, и придираться к почти равным площадкам
    незачем, а вот выбор вне собственной тройки стоит пометить.
    """
    ranked = sorted(
        identities,
        key=lambda identity: (
            -identities[identity].visits,
            identity_names.get(identity, identity).casefold(),
        ),
    )
    if manual_key is not None and manual_key in identities:
        return "manual_off_top" if ranked.index(home) >= 3 else None
    if len(ranked) < 2:
        return None
    leader = identities[ranked[0]].visits
    runner_up = identities[ranked[1]].visits
    return "ambiguous" if runner_up >= leader * AMBIGUITY_RATIO else None


def _home_distance_tally(
    identities: dict[str, _LocationVisits],
    home: str,
    coordinates: dict[str, tuple[float, float]],
    week_start: date,
) -> tuple[int, int, dict[str, list[int]]]:
    """(сумма км, из них прибавилось за неделю, разбивка по системам).

    Площадка даёт свои километры один раз — сколько бы раз человек туда ни
    ездил (решение Дмитрия 02.08.2026). «За неделю» — километры площадок,
    впервые посещённых в окне дельты.
    """
    home_point = coordinates.get(home)
    if home_point is None:
        return 0, 0, {}
    total = 0
    week = 0
    values: dict[str, list[int]] = {}
    for identity, visits in identities.items():
        if identity == home:
            continue
        point = coordinates.get(identity)
        if point is None:
            continue
        km = int(round(haversine_km(home_point, point)))
        if km <= 0:
            continue
        total += km
        if visits.first_date >= week_start:
            week += km
        for code in visits.codes:
            cell = values.setdefault(code, [0, 0])
            cell[0] += km
            if visits.platform_is_new(code, 1):
                cell[1] += km
    return total, week, values


def _collect_home_distance_entities(
    source: _MetricSource, *, platform: str = "all"
) -> dict[str, _Entity]:
    """Рейтинг дальности от дома: сумма километров до уникальных площадок.

    Считается по тем же строкам визитов, что беговой туризм, — меняется только
    способ превратить набор площадок в число.
    """
    links = source.links()
    identity_by_location, identity_names, _identity_slugs = source.identity_maps()
    coordinates = source.coordinates_map()
    manual_homes = source.manual_home_keys()
    rows = source.rows(_LOCATION_VISITS_SQL)

    per_entity: dict[str, dict[str, _LocationVisits]] = {}
    meta: dict[str, tuple[UUID, _SiteLink | None]] = {}
    for pid, code, location_id, first_date, visits, week_visits in rows:
        if platform != "all" and code != platform:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        link = links.get(pid)
        key = _entity_key(pid, link)
        meta.setdefault(key, (pid, link))
        _merge_visit_row(
            per_entity.setdefault(key, {}),
            identity,
            code,
            first_date,
            int(visits),
            int(week_visits),
        )

    names = source.names()
    entities: dict[str, _Entity] = {}
    for key, identities in per_entity.items():
        pid, link = meta[key]
        entity = _Entity(key=key)
        if link is not None and not link.private:
            entity.site_serial_id = link.serial_id
            entity.display_name = link.display_name or names.get(pid)
        else:
            entity.display_name = names.get(pid)
        manual_key = manual_homes.get(link.user_id) if link is not None else None
        home = _home_identity(identities, identity_names, manual_key)
        if home is None:
            continue
        total, week, values = _home_distance_tally(
            identities, home, coordinates, source.week_start
        )
        entity.total = total
        entity.week = week
        entity.values = values
        entity.home_location = identity_names.get(home, home)
        entity.home_location_note = _home_location_note(
            identities, identity_names, home, manual_key
        )
        entity.locations_total = len(identities)
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
    """Гендерный разрез есть только у победных метрик, и только женский; для
    остальных — всегда all.

    gender=male сюда ещё приходит по старым ссылкам и из закладок — молча
    отдаём абсолют вместо 400: это ближайший честный зачёт для мужчины (см.
    комментарий у LEADERBOARD_GENDERS о том, почему мужского больше нет)."""
    if metric in GENDERED_METRICS and gender == "female":
        return "female"
    return "all"


def _normalize_min_visits(metric: LeaderboardMetric, min_visits: int) -> int:
    """Порог визитов есть только у рейтинга туризма; у остальных всегда 1."""
    if metric not in MIN_VISITS_METRICS:
        return 1
    return max(1, min(int(min_visits), MAX_MIN_VISITS))


def _normalize_count_by(metric: LeaderboardMetric, count_by: str) -> str:
    """Единица зачёта есть только у туристических рейтингов; у остальных всегда
    «площадки». Неизвестное значение трактуем так же — 400 тут избыточен."""
    if metric not in COUNT_BY_METRICS or count_by not in COUNT_BY_VALUES:
        return "locations"
    return count_by


def count_by_values(metric: str) -> tuple[str, ...]:
    """Кнопки фильтра «единица зачёта» для этого рейтинга (пусто — фильтра нет)."""
    return COUNT_BY_VALUES if metric in COUNT_BY_METRICS else ()


def _normalize_platform_filter(metric: LeaderboardMetric, platform: str) -> str:
    """Фильтр «по одной системе» есть у всех рейтингов, но набор систем зависит
    от метрики (см. platform_filter_values). Систему, которой в этом рейтинге
    нет, и неизвестный код молча трактуем как «все системы»."""
    if platform in platform_filter_values(metric):
        return platform
    return "all"


def _build_snapshot(
    db: Session,
    metric: LeaderboardMetric,
    gender: LeaderboardGender = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    source: _MetricSource | None = None,
    role_filter: frozenset[str] | None = None,
) -> dict[str, object]:
    latest = source.latest if source is not None else latest_event_date(db)
    if latest is None:
        return {
            "metric": metric,
            "gender": gender,
            "min_visits": min_visits,
            "platform": platform,
            "count_by": count_by,
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
    src = source if source is not None else _MetricSource(db, latest)

    if metric == "volunteer_roles":
        entities = _collect_volunteer_role_entities(src, platform=platform)
    elif metric == "home_distance":
        entities = _collect_home_distance_entities(src, platform=platform)
    elif metric == "locations":
        entities = _collect_location_entities(
            src,
            min_visits=min_visits,
            platform=platform,
            count_by=count_by,
            with_geo=True,
        )
    elif metric == "volunteer_locations":
        entities = _collect_location_entities(
            src,
            sql=_VOLUNTEER_LOCATION_VISITS_SQL,
            min_visits=min_visits,
            platform=platform,
            count_by=count_by,
            with_geo=True,
            role_filter=role_filter,
        )
    elif metric == "win_locations":
        if gender == "all":
            entities = _collect_location_entities(
                src, sql=_WIN_LOCATION_VISITS_SQL, with_last_win=True, platform=platform
            )
        else:
            entities = _collect_gendered_win_entities(
                src, gender, as_locations=True, platform=platform
            )
    elif metric == "wins":
        if gender == "all":
            entities = _collect_win_entities(src, platform=platform)
        else:
            entities = _collect_gendered_win_entities(
                src, gender, as_locations=False, platform=platform
            )
    else:
        entities = _collect_numeric_entities(
            src, metric, platform=platform, role_filter=role_filter
        )

    # «Последняя неделя» — одинаково для всех четырёх метрик: просто где человек
    # был за окно дельты, независимо от того, дало это +1 или нет.
    week_sql = _WEEK_LOCATIONS_SQL_BY_METRIC.get(metric)
    if week_sql is not None:
        _attach_week_locations(
            src,
            entities,
            week_sql.replace("/*PIDS_FILTER*/", ""),
            platform,
            role_filter,
        )

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
        if entity.locations_total is not None:
            row["locations_total"] = entity.locations_total
            row["cities_total"] = entity.cities_total
            row["regions_total"] = entity.regions_total
        if entity.week_location is not None:
            row["week_location"] = entity.week_location
        if entity.home_location is not None:
            row["home_location"] = entity.home_location
            # У «дальности от дома» колонка «Дом» — это домашняя локация, а не
            # топ-локация побед: числа побед у неё нет, зато есть пометка о том,
            # что выбор дома под вопросом.
            if metric == "home_distance":
                row["home_location_note"] = entity.home_location_note
            else:
                row["home_location_wins"] = entity.home_location_wins
        if entity.top_role is not None:
            row["top_role"] = entity.top_role
            row["top_role_count"] = entity.top_role_count
            row["role_details"] = entity.role_details
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
        "min_visits": min_visits,
        "platform": platform,
        "count_by": count_by,
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


def _cache_key(
    metric: str,
    gender: str = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles_key: str = "",
) -> str:
    # Гендерные варианты, пороги визитов, фильтр по системе, единица зачёта и
    # набор ролей — отдельными суффиксами; базовый вариант (all / от 1 визита /
    # все системы / площадки / все роли) оставляет прежний ключ, чтобы не
    # плодить лишние ключи у метрик без этих разрезов.
    key = f"{CACHE_KEY_PREFIX}:{metric}"
    if gender != "all":
        key = f"{key}:{gender}"
    if min_visits > 1:
        key = f"{key}:v{min_visits}"
    if platform != "all":
        key = f"{key}:p{platform}"
    if count_by != "locations":
        key = f"{key}:c{count_by}"
    if roles_key:
        # Пресет — читаемым суффиксом, произвольный набор — короткой сигнатурой
        # (полный список ключей в имени Redis-ключа не нужен).
        key = f"{key}:r{roles_key}"
    return key


def _read_cache(
    metric: str,
    gender: str = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles_key: str = "",
) -> dict[str, object] | None:
    try:
        raw = get_redis_client().get(
            _cache_key(metric, gender, min_visits, platform, count_by, roles_key)
        )
    except Exception:
        return None
    if not raw or not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache(
    metric: str,
    payload: dict[str, object],
    gender: str = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles_key: str = "",
) -> None:
    try:
        get_redis_client().setex(
            _cache_key(metric, gender, min_visits, platform, count_by, roles_key),
            CACHE_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        return


_USED_ROLES_CACHE_KEY = f"{CACHE_KEY_PREFIX}:used_roles"
_USED_ROLES_TTL_SECONDS = 24 * 3600


def used_role_keys(db: Session) -> set[str]:
    """Канонические роли, которые реально встречаются в данных.

    В таксономии есть роли «про запас» (у систем они объявлены, но ими никто не
    выходил) — в списке фильтра они только мешают. Считается редко и кэшируется
    на сутки: полный проход по ярлыкам ролей стоит дорого.
    """
    try:
        cached = get_redis_client().get(_USED_ROLES_CACHE_KEY)
        if isinstance(cached, (str, bytes, bytearray)):
            parsed = json.loads(cached)
            if isinstance(parsed, list):
                return set(parsed)
    except Exception:
        pass
    keys: set[str] = set()
    for (label,) in db.execute(
        text("SELECT DISTINCT role FROM volunteer_results WHERE role IS NOT NULL")
    ).all():
        canonical = canonical_volunteer_role(label)
        if canonical is not None:
            keys.add(canonical.key)
    try:
        get_redis_client().setex(
            _USED_ROLES_CACHE_KEY, _USED_ROLES_TTL_SECONDS, json.dumps(sorted(keys))
        )
    except Exception:
        pass
    return keys


def normalize_role_filter(
    metric: str, roles: Sequence[str] | None
) -> tuple[frozenset[str] | None, str]:
    """Фильтр ролей → (набор канонических ключей, суффикс кэша).

    Фильтр есть только у волонтёрских рейтингов по числу выходов; у остальных
    (пробежки, победы, «Мультиволонтёр» — там роли и есть сама метрика) он
    молча игнорируется, как и прочие неприменимые параметры.
    """
    if not roles or metric not in ROLE_FILTER_METRICS:
        return None, ""
    known = set(CANONICAL_ROLE_LABELS)
    selected = frozenset(role for role in roles if role in known)
    # Пустой или полный набор — это «все роли», фильтровать нечего.
    if not selected or selected == known:
        return None, ""
    for preset in ("on_site", "on_site_no_run", "remote"):
        if preset_role_keys(preset) == selected:
            return selected, preset
    digest = hashlib.sha1(",".join(sorted(selected)).encode()).hexdigest()[:10]
    return selected, f"c{digest}"


def get_leaderboard_snapshot(
    db: Session,
    metric: LeaderboardMetric,
    gender: str = "all",
    *,
    use_cache: bool = True,
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles: Sequence[str] | None = None,
) -> dict[str, object]:
    resolved = _normalize_gender(metric, gender)
    visits = _normalize_min_visits(metric, min_visits)
    platform_resolved = _normalize_platform_filter(metric, platform)
    unit = _normalize_count_by(metric, count_by)
    role_filter, roles_key = normalize_role_filter(metric, roles)
    if use_cache:
        cached = _read_cache(metric, resolved, visits, platform_resolved, unit, roles_key)
        if cached is not None:
            return cached
    payload = _build_snapshot(
        db,
        metric,
        resolved,
        visits,
        platform_resolved,
        unit,
        role_filter=role_filter,
    )
    if use_cache:
        _write_cache(
            metric, payload, resolved, visits, platform_resolved, unit, roles_key
        )
    return payload


def refresh_leaderboard_cache(
    db: Session,
    metric: LeaderboardMetric,
    gender: str = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    source: _MetricSource | None = None,
) -> dict[str, object]:
    """Пересчитать снапшот и перезаписать кэш, даже если тот ещё не протух.

    Для beat-задачи прогрева: обычный get_leaderboard при живом кэше ничего не
    пересчитывает, и кэш умирал бы по TTL между запусками. source — общий на
    серию вариантов одной метрики (см. _MetricSource).
    """
    resolved = _normalize_gender(metric, gender)
    visits = _normalize_min_visits(metric, min_visits)
    platform_resolved = _normalize_platform_filter(metric, platform)
    unit = _normalize_count_by(metric, count_by)
    payload = _build_snapshot(
        db, metric, resolved, visits, platform_resolved, unit, source
    )
    _write_cache(metric, payload, resolved, visits, platform_resolved, unit)
    return payload


def get_leaderboard(
    db: Session,
    metric: LeaderboardMetric,
    gender: str = "all",
    *,
    limit: int = 100,
    use_cache: bool = True,
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles: Sequence[str] | None = None,
) -> dict[str, object]:
    """Публичная часть снапшота (без служебных массивов рангов)."""
    resolved = _normalize_gender(metric, gender)
    visits = _normalize_min_visits(metric, min_visits)
    platform_resolved = _normalize_platform_filter(metric, platform)
    unit = _normalize_count_by(metric, count_by)
    snapshot = get_leaderboard_snapshot(
        db,
        metric,
        resolved,
        use_cache=use_cache,
        min_visits=visits,
        platform=platform_resolved,
        count_by=unit,
        roles=roles,
    )
    rows = cast("list[dict[str, object]]", snapshot.get("rows") or [])
    # Фильтр по одной системе прячет колонки-системы целиком (люди хотели
    # объединённый зачёт как норму, а не постоянный набор колонок «своя
    # система + остальные нули») — остаётся только «Всего», уже посчитанное
    # именно по этой системе.
    columns: tuple[str, ...] = (
        () if platform_resolved != "all" else platform_columns_for(metric)
    )
    return {
        "metric": metric,
        "gender": resolved,
        "min_visits": visits,
        "platform": platform_resolved,
        "count_by": unit,
        "title": metric_title(metric, unit),
        "description": metric_description(metric, resolved, visits, platform_resolved, unit),
        "unit": metric_unit(metric, unit),
        "platform_columns": list(columns),
        # Кнопки фильтра «по системе» — их набор зависит от рейтинга, поэтому
        # фронт берёт его отсюда, а не повторяет правила у себя.
        "platform_options": list(platform_filter_values(metric)),
        # Кнопки фильтра «единица зачёта» (пусто — фильтра у рейтинга нет) и
        # флаг колонки «Последняя неделя»: те же правила, что и у систем, живут
        # на бэкенде, фронт их не повторяет.
        "count_by_options": list(count_by_values(metric)),
        "has_week_locations": metric in WEEK_LOCATIONS_METRICS,
        "rows": rows[: max(1, min(limit, TOP_LIMIT))],
        "threshold": snapshot.get("threshold", 0),
        "median": snapshot.get("median", 0),
        "entrants": snapshot.get("entrants", 0),
        "latest_event_date": snapshot.get("latest_event_date"),
        "week_start": snapshot.get("week_start"),
        "built_at": snapshot.get("built_at"),
    }


def _my_numeric_values(
    db: Session,
    metric: str,
    participant_ids: list[UUID],
    week_start: date,
    platform: str = "all",
) -> dict[str, list[int]]:
    values = _my_numeric_values_all(db, metric, participant_ids, week_start)
    if platform == "all":
        return values
    # Фильтр «по одной системе»: значения других систем не просто скрыты — они
    # не идут и в «Всего» (его считает вызывающий по этому же словарю).
    return {code: cell for code, cell in values.items() if code == platform}


def _my_numeric_values_all(
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


def _my_volunteer_role_values(
    db: Session,
    participant_ids: list[UUID],
    week_start: date,
    platform: str = "all",
) -> _RoleSummary:
    """«Моя» строка мультиволонтёра: роли по системам, всего, любимая роль
    и та же детализация «роль × система», что у строк таблицы."""
    if not participant_ids:
        return _RoleSummary(values={}, total=0, week=0, top_role=None, details=[])
    by_platform: dict[str, dict[str, _RoleUsage]] = {}
    labels: dict[str, str] = {}
    for _pid, code, raw_role, first_date, times in _my_volunteer_role_rows(
        db, participant_ids
    ):
        if platform != "all" and code != platform:
            continue
        _add_role_row(by_platform, labels, code, raw_role, first_date, times)
    return _summarize_roles(by_platform, labels, week_start)


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
    db: Session, participant_ids: list[UUID], week_start: date, platform: str = "all"
) -> tuple[dict[str, list[int]], tuple[str, int] | None, _LastWin | None]:
    """Победы залогиненного: значения по системам, «топ-локация побед» и
    последняя победа."""
    if not participant_ids:
        return {}, None, None
    sql = _WIN_ROWS_SQL.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
    rows = db.execute(text(sql), _row_params(db, week_start, pids=participant_ids)).all()
    if not rows:
        return {}, None, None
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    values: dict[str, list[int]] = {}
    win_counts_by_identity: dict[str, int] = {}
    last_dates: dict[str, date] = {}
    for _pid, code, location_id, wins, week_wins, last_date in rows:
        if platform != "all" and code != platform:
            continue
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
    db: Session,
    participant_ids: list[UUID],
    week_start: date,
    gender: str,
    *,
    as_locations: bool,
    platform: str = "all",
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
        # Пол — по всем строкам, до фильтра по системе (как в _collect_gendered_win_entities).
        if gender_label in ("male", "female"):
            votes[gender_label] = votes.get(gender_label, 0) + int(wins)
        if platform != "all" and code != platform:
            continue
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


@dataclass
class _MyLocationRow:
    """«Моя» строка туристического рейтинга: значения по системам и все три
    единицы зачёта сразу (какая из них в «Всего» — решает count_by)."""

    values: dict[str, list[int]]
    total: int
    week: int
    last_win: _LastWin | None = None
    locations_total: int | None = None
    cities_total: int | None = None
    regions_total: int | None = None


def _my_location_values(
    db: Session,
    participant_ids: list[UUID],
    week_start: date,
    *,
    sql_template: str = _LOCATION_VISITS_SQL,
    with_last_win: bool = False,
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
    with_geo: bool = False,
) -> _MyLocationRow:
    if not participant_ids:
        return _MyLocationRow(values={}, total=0, week=0)
    sql = sql_template.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
    rows = db.execute(text(sql), _row_params(db, week_start, pids=participant_ids)).all()
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    getters = _unit_key_getters(_location_geo_map(db) if with_geo else {})
    identities: dict[str, _LocationVisits] = {}
    for _pid, code, location_id, first_date, visits, week_visits in rows:
        if platform != "all" and code != platform:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        _merge_visit_row(identities, identity, code, first_date, int(visits), int(week_visits))
    counted = {
        identity: visits for identity, visits in identities.items() if visits.counts(min_visits)
    }
    units = COUNT_BY_VALUES if with_geo else ("locations",)
    tallies = {unit: _unit_counts(counted, getters[unit], min_visits) for unit in units}
    selected = tallies[count_by if count_by in tallies else "locations"]
    row = _MyLocationRow(
        values=selected.values, total=selected.total, week=selected.week
    )
    if with_geo:
        row.locations_total = tallies["locations"].total
        row.cities_total = tallies["cities"].total
        row.regions_total = tallies["regions"].total
    if with_last_win:
        row.last_win = _resolve_last_win(
            {identity: visits.first_date for identity, visits in counted.items()},
            identity_names,
            identity_slugs,
        )
    return row


@dataclass
class _MyHomeDistanceRow:
    """«Моя» строка рейтинга дальности: километры, дом и число площадок."""

    values: dict[str, list[int]]
    total: int
    week: int
    home_location: str | None = None
    home_location_note: str | None = None
    locations_total: int | None = None


def _my_home_distance_values(
    db: Session,
    user: User,
    participant_ids: list[UUID],
    week_start: date,
    platform: str = "all",
) -> _MyHomeDistanceRow:
    """Та же арифметика, что в снапшоте, но по одному человеку.

    Домашняя локация берётся из настроек кабинета, если он её задал, — так «моя
    строка» рейтинга совпадает с плиткой на главной.
    """
    if not participant_ids:
        return _MyHomeDistanceRow(values={}, total=0, week=0)
    sql = _LOCATION_VISITS_SQL.replace("/*PIDS_FILTER*/", "AND rr.participant_id = ANY(:pids)")
    rows = db.execute(text(sql), _row_params(db, week_start, pids=participant_ids)).all()
    identity_by_location, identity_names, _identity_slugs = _location_identity_maps(db)
    identities: dict[str, _LocationVisits] = {}
    for _pid, code, location_id, first_date, visits, week_visits in rows:
        if platform != "all" and code != platform:
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        _merge_visit_row(identities, identity, code, first_date, int(visits), int(week_visits))
    home = _home_identity(identities, identity_names, user.home_location_key)
    if home is None:
        return _MyHomeDistanceRow(values={}, total=0, week=0)
    total, week, values = _home_distance_tally(
        identities, home, _location_coordinates_map(db), week_start
    )
    return _MyHomeDistanceRow(
        values=values,
        total=total,
        week=week,
        home_location=identity_names.get(home, home),
        home_location_note=_home_location_note(
            identities, identity_names, home, user.home_location_key
        ),
        locations_total=len(identities),
    )


def _my_week_location(
    db: Session,
    metric: str,
    participant_ids: list[UUID],
    week_start: date,
    platform: str = "all",
    role_filter: frozenset[str] | None = None,
) -> dict[str, object] | None:
    """«Где я был в последний раз за неделю» — как и в строках таблицы."""
    template = _WEEK_LOCATIONS_SQL_BY_METRIC.get(metric)
    if template is None or not participant_ids:
        return None
    alias = _WEEK_LOCATIONS_PIDS_ALIAS[metric]
    sql = template.replace(
        "/*PIDS_FILTER*/", f"AND {alias}.participant_id = ANY(:pids)"
    )
    rows = db.execute(text(sql), {"week_start": week_start, "pids": participant_ids}).all()
    identity_by_location, identity_names, identity_slugs = _location_identity_maps(db)
    dates: dict[str, date] = {}
    for _pid, code, location_id, last_date, role in rows:
        if platform != "all" and code != platform:
            continue
        if role_filter is not None and not _role_allowed(role, role_filter):
            continue
        identity = identity_by_location.get(location_id, str(location_id))
        known = dates.get(identity)
        dates[identity] = max(known, last_date) if known is not None else last_date
    return _latest_week_location(dates, identity_names, identity_slugs)


def get_my_leaderboard_row(
    db: Session,
    metric: LeaderboardMetric,
    user: User,
    gender: str = "all",
    min_visits: int = 1,
    platform: str = "all",
    count_by: str = "locations",
) -> dict[str, object]:
    """Строка залогиненного участника: значения, место, дельта места, порог."""
    resolved = _normalize_gender(metric, gender)
    visits = _normalize_min_visits(metric, min_visits)
    platform_resolved = _normalize_platform_filter(metric, platform)
    unit = _normalize_count_by(metric, count_by)
    snapshot = get_leaderboard_snapshot(
        db,
        metric,
        resolved,
        min_visits=visits,
        platform=platform_resolved,
        count_by=unit,
    )
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
    my_top_role: tuple[str, int] | None = None
    my_role_details: list[dict[str, object]] = []
    my_geo: _MyLocationRow | None = None
    # Только у «дальности от дома»: домашняя локация без числа побед и общее
    # число посещённых площадок (у туристических рейтингов это считает my_geo).
    my_home_name: str | None = None
    my_home_note: str | None = None
    my_locations_total: int | None = None
    if metric == "volunteer_roles":
        role_summary = _my_volunteer_role_values(
            db, participant_ids, week_start, platform_resolved
        )
        values = role_summary.values
        total = role_summary.total
        week = role_summary.week
        my_top_role = role_summary.top_role
        my_role_details = role_summary.details
    elif metric == "home_distance":
        distance_row = _my_home_distance_values(
            db, user, participant_ids, week_start, platform_resolved
        )
        values, total, week = distance_row.values, distance_row.total, distance_row.week
        my_home_name = distance_row.home_location
        my_home_note = distance_row.home_location_note
        my_locations_total = distance_row.locations_total
    elif metric in ("locations", "volunteer_locations"):
        my_geo = _my_location_values(
            db,
            participant_ids,
            week_start,
            sql_template=(
                _VOLUNTEER_LOCATION_VISITS_SQL
                if metric == "volunteer_locations"
                else _LOCATION_VISITS_SQL
            ),
            min_visits=visits,
            platform=platform_resolved,
            count_by=unit,
            with_geo=True,
        )
        values, total, week = my_geo.values, my_geo.total, my_geo.week
    elif metric == "win_locations":
        if resolved == "all":
            my_row = _my_location_values(
                db,
                participant_ids,
                week_start,
                sql_template=_WIN_LOCATION_VISITS_SQL,
                with_last_win=True,
                platform=platform_resolved,
            )
            values, total, week = my_row.values, my_row.total, my_row.week
            my_last_win = my_row.last_win
        else:
            values, total, week, my_home, my_last_win = _my_gendered_win_values(
                db,
                participant_ids,
                week_start,
                resolved,
                as_locations=True,
                platform=platform_resolved,
            )
    elif metric == "wins":
        if resolved == "all":
            values, my_home, my_last_win = _my_win_values(
                db, participant_ids, week_start, platform_resolved
            )
            total = sum(v[0] for v in values.values())
            week = sum(v[1] for v in values.values())
        else:
            values, total, week, my_home, my_last_win = _my_gendered_win_values(
                db,
                participant_ids,
                week_start,
                resolved,
                as_locations=False,
                platform=platform_resolved,
            )
    else:
        values = _my_numeric_values(
            db, metric, participant_ids, week_start, platform_resolved
        )
        total = sum(v[0] for v in values.values())
        week = sum(v[1] for v in values.values())

    # «Последняя неделя» — одинаково для всех метрик с этой колонкой: просто где
    # человек был за окно дельты, дало это +1 или нет (у остальных вернётся []).
    my_week_location = _my_week_location(
        db, metric, participant_ids, week_start, platform_resolved
    )

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
        "min_visits": visits,
        "platform": platform_resolved,
        "count_by": unit,
        "locations_total": my_geo.locations_total if my_geo else my_locations_total,
        "cities_total": my_geo.cities_total if my_geo else None,
        "regions_total": my_geo.regions_total if my_geo else None,
        "week_location": my_week_location,
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
        "home_location": my_home[0] if my_home else my_home_name,
        "home_location_wins": my_home[1] if my_home else None,
        "home_location_note": my_home_note,
        "top_role": my_top_role[0] if my_top_role else None,
        "top_role_count": my_top_role[1] if my_top_role else None,
        "role_details": my_role_details,
        "best_time_sec": my_best_time,
        "best_time_display": (
            format_finish_time_display(my_best_time) if my_best_time is not None else None
        ),
        "last_win_location": my_last_win.location if my_last_win else None,
        "last_win_location_slug": my_last_win.slug if my_last_win else None,
        "last_win_date": my_last_win.on_date.isoformat() if my_last_win else None,
    }
