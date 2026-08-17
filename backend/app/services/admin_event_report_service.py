"""Отчёт по событию для админки: метрики пробежки в локации + готовый текст поста.

Перенос adhoc-скрипта notification_about_event.py из легаси на текущую БД сайта.
Все исторические выборки исключают тестовые события и secondary-события кросслинков
(дубли RunPark), как в rating_service. Идентичность участника — платформенная
(Participant), поэтому фильтр по платформе в исторических запросах не нужен:
все результаты участника принадлежат одной платформе.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, joinedload

from app.models import Event, LocationCatalogLink
from app.volunteer_role_taxonomy import canonical_volunteer_role

# Глобальные клубные уровни (пробежки и волонтёрства в системе платформы).
CLUB_LEVELS = (10, 25, 50, 100, 250)

# Секундомер secondary-событий кросслинков — исключаем из всех исторических выборок.
NOT_SECONDARY_SQL = "id NOT IN (SELECT secondary_event_id FROM event_crosslinks)"

# Подпись в конце поста для рассылок — ссылка на канал автора, не на сайт: кто
# перейдёт в канал по ссылке, увидит там и новости о сайте run5k.run.
POST_SIGNATURE = "📊 Статистика подготовлена каналом t.me/popov_way"


def age_group_sql(column: str) -> str:
    """Чистая возрастная группа без места в группе.

    Протокол 5 вёрст кладёт в run_results.age_category строку вида «М40-44 (2)»,
    где (2) — место внутри группы на этом забеге. Для ранжирования и вывода
    нужна только сама группа; parkrun-категории (SM25-29) остаются как есть.
    """
    return rf"NULLIF(regexp_replace(coalesce({column}, ''), '\s*\(\d+\)\s*$', ''), '')"


def gender_sql(column: str) -> str:
    """SQL-выражение пола по возрастной категории: М/M → 'M', Ж/W/F → 'F'.

    Покрывает русские категории (М18-19, Ж35-39) и parkrun-стиль (SM25-29, VW50-54).
    """
    return f"""CASE
        WHEN upper({column}) ~ '^[МM]' THEN 'M'
        WHEN upper({column}) ~ '^[ЖWF]' THEN 'F'
        WHEN upper({column}) ~ '^[SVJC][MМ]' THEN 'M'
        WHEN upper({column}) ~ '^[SVJC][WЖF]' THEN 'F'
        ELSE NULL
    END"""


# ===== Русская морфология для текста поста =====

def ru_plural(n: int, forms: tuple[str, str, str]) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 14:
        return forms[2]
    if n1 == 1:
        return forms[0]
    if 2 <= n1 <= 4:
        return forms[1]
    return forms[2]


def ru_adj_base(n: int, singular: str, plural: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return plural
    if n_abs % 10 == 1:
        return singular
    return plural


def _bold_surname(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) >= 2:
        return " ".join(parts[:-1]) + f" **{parts[-1]}**"
    return f"**{full_name}**"


def fmt_time(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_date_ru(value: date) -> str:
    return value.strftime("%d.%m.%Y")


# ===== Списки для формы =====

def list_report_locations(db: Session) -> list[dict[str, Any]]:
    """Локации с хотя бы одним не-тестовым событием, для выпадающего списка."""
    rows = db.execute(
        text(
            f"""
            SELECT l.id AS location_id,
                   l.name AS location_name,
                   l.city AS city,
                   p.code AS platform_code,
                   p.name AS platform_name,
                   count(e.id) AS events_count,
                   max(e.event_date) AS last_event_date
            FROM locations l
            JOIN platforms p ON p.id = l.platform_id
            JOIN events e ON e.location_id = l.id
            WHERE NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
            GROUP BY l.id, l.name, l.city, p.code, p.name
            ORDER BY l.name, p.code
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def list_report_event_dates(db: Session, location_id: UUID) -> list[dict[str, Any]]:
    """Даты событий локации (новые сверху) для выпадающего списка."""
    rows = db.execute(
        text(
            f"""
            SELECT e.id AS event_id,
                   e.event_date,
                   e.event_number,
                   -- Все строки протокола, включая «неизвестных» (status='unknown') —
                   -- они физически финишировали, просто без распознанного времени.
                   -- Так же, как в шапке отчёта.
                   coalesce(
                       (SELECT count(*) FROM run_results rr
                        WHERE rr.event_id = e.id),
                       0
                   ) AS finishers_count
            FROM events e
            WHERE e.location_id = :location_id
              AND NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
            ORDER BY e.event_date DESC
            """
        ),
        {"location_id": str(location_id)},
    ).mappings()
    return [dict(row) for row in rows]


# ===== Основной отчёт =====

def _catalog_location_ids(db: Session, event: Event) -> tuple[list[UUID], list[UUID], str | None]:
    """Локации той же физической точки: (та же платформа, все платформы, канон. имя).

    Через location_catalog_links; если локация не в каталоге — только она сама.
    """
    own_link = (
        db.query(LocationCatalogLink)
        .filter(LocationCatalogLink.location_id == event.location_id)
        .first()
    )
    if own_link is None:
        return [event.location_id], [event.location_id], None

    links = (
        db.query(LocationCatalogLink)
        .options(joinedload(LocationCatalogLink.catalog))
        .filter(LocationCatalogLink.catalog_id == own_link.catalog_id)
        .all()
    )
    same_platform = {event.location_id}
    all_platforms = {event.location_id}
    for link in links:
        if link.location_id is None:
            continue
        all_platforms.add(link.location_id)
        if link.platform_id == event.platform_id:
            same_platform.add(link.location_id)
    canonical_name = links[0].catalog.canonical_name if links else None
    return sorted(same_platform, key=str), sorted(all_platforms, key=str), canonical_name


def _query(db: Session, sql: str, params: dict[str, Any], expanding: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    return [dict(row) for row in db.execute(stmt, params).mappings()]


def _runner_stat_rows(db: Session, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Построчная статистика бегунов события: флаги «впервые/ЛР» + счётчики.

    Общая для отчёта админки и свода кабинета организатора — чтобы цифры
    в посте и в таблице свода не разъезжались.
    """
    return _query(
        db,
        f"""
        -- Только строки со временем финиша: записи без него — это «НЕИЗВЕСТНЫЙ»
        -- (неопознанные участники протокола 5 вёрст, status='unknown'). Легаси
        -- отсекал их фильтром status_runner='active_runner'; иначе каждый такой
        -- ряд считался бы новичком системы.
        WITH today AS (
            SELECT rr.participant_id,
                   min(rr.finish_time_sec) AS finish_time_sec,
                   min(rr.position) AS position
            FROM run_results rr
            WHERE rr.event_id = :event_id
              AND rr.participant_id IS NOT NULL
              AND rr.finish_time_sec IS NOT NULL
            GROUP BY rr.participant_id
        ),
        prev AS (
            SELECT rr.participant_id,
                   min(rr.finish_time_sec) AS best_platform_sec,
                   min(rr.finish_time_sec) FILTER (WHERE e.location_id IN :loc_ids) AS best_location_sec,
                   count(*) FILTER (WHERE e.location_id IN :loc_ids) AS location_runs,
                   count(*) AS platform_runs,
                   max(e.event_date) AS last_run_date
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            WHERE rr.participant_id IN (SELECT participant_id FROM today)
              AND e.event_date < :event_date
              AND NOT e.is_test_event
              AND rr.finish_time_sec IS NOT NULL
              AND e.{NOT_SECONDARY_SQL}
            GROUP BY rr.participant_id
        )
        SELECT t.participant_id, p.display_name, p.profile_url, t.finish_time_sec, t.position,
               (prev.participant_id IS NULL) AS first_in_system,
               (coalesce(prev.location_runs, 0) = 0) AS first_at_location,
               (t.finish_time_sec IS NOT NULL AND prev.best_platform_sec IS NOT NULL
                AND t.finish_time_sec < prev.best_platform_sec) AS is_pb,
               (t.finish_time_sec IS NOT NULL AND prev.best_location_sec IS NOT NULL
                AND t.finish_time_sec < prev.best_location_sec) AS is_location_pb,
               coalesce(prev.location_runs, 0) + 1 AS location_runs_count,
               coalesce(prev.platform_runs, 0) + 1 AS platform_runs_count,
               prev.last_run_date
        FROM today t
        LEFT JOIN prev ON prev.participant_id = t.participant_id
        LEFT JOIN participants p ON p.id = t.participant_id
        """,
        params,
        expanding=("loc_ids",),
    )


def _volunteer_stat_rows(db: Session, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Построчная статистика волонтёров события — общая для отчёта и свода."""
    return _query(
        db,
        f"""
        WITH today AS (
            SELECT vr.participant_id, array_agg(DISTINCT vr.role) AS roles
            FROM volunteer_results vr
            WHERE vr.event_id = :event_id AND vr.participant_id IS NOT NULL
            GROUP BY vr.participant_id
        ),
        prev AS (
            SELECT vr.participant_id,
                   count(DISTINCT vr.event_id) AS vol_events,
                   count(DISTINCT vr.event_id) FILTER (WHERE e.location_id IN :loc_ids) AS loc_vol_events,
                   array_agg(DISTINCT vr.role) AS prev_roles
            FROM volunteer_results vr
            JOIN events e ON e.id = vr.event_id
            WHERE vr.participant_id IN (SELECT participant_id FROM today)
              AND e.event_date < :event_date
              AND NOT e.is_test_event
              AND e.{NOT_SECONDARY_SQL}
            GROUP BY vr.participant_id
        )
        SELECT t.participant_id, p.display_name, p.profile_url, t.roles,
               (prev.participant_id IS NULL) AS first_volunteering,
               (coalesce(prev.loc_vol_events, 0) = 0) AS first_volunteering_at_location,
               coalesce(prev.loc_vol_events, 0) + 1 AS location_vol_count,
               coalesce(prev.vol_events, 0) + 1 AS platform_vol_count,
               prev.prev_roles
        FROM today t
        LEFT JOIN prev ON prev.participant_id = t.participant_id
        LEFT JOIN participants p ON p.id = t.participant_id
        """,
        params,
        expanding=("loc_ids",),
    )


def _new_canonical_roles(
    roles: list[str] | None, prev_roles: list[str] | None
) -> list[str]:
    """Ярлыки сегодняшних ролей, которых не было в истории волонтёра.

    Сравнение по каноническим ключам таксономии, а не по сырым строкам:
    parkrun приклеивает к роли счётчик кредитов («Marshal (12×)»), и сырое
    сравнение считало бы каждую субботу «новой ролью».
    """
    prev_keys = set()
    for role in prev_roles or []:
        canonical = canonical_volunteer_role(role)
        if canonical is not None:
            prev_keys.add(canonical.key)
    new_labels: list[str] = []
    seen: set[str] = set()
    for role in roles or []:
        canonical = canonical_volunteer_role(role)
        if canonical is None or canonical.key in prev_keys or canonical.key in seen:
            continue
        seen.add(canonical.key)
        new_labels.append(canonical.label)
    return new_labels


def _event_context(
    db: Session, event_id: UUID
) -> tuple[Event, dict[str, Any], str | None] | None:
    """Событие + параметры исторических запросов (общие для отчёта и свода)."""
    event = (
        db.query(Event)
        .options(joinedload(Event.location), joinedload(Event.platform), joinedload(Event.summary))
        .filter(Event.id == event_id)
        .one_or_none()
    )
    if event is None:
        return None

    loc_ids, all_loc_ids, canonical_name = _catalog_location_ids(db, event)
    params: dict[str, Any] = {
        "event_id": str(event.id),
        "event_date": event.event_date,
        "loc_ids": [str(x) for x in loc_ids],
        "all_loc_ids": [str(x) for x in all_loc_ids],
    }
    return event, params, canonical_name


def build_event_report(db: Session, event_id: UUID) -> dict[str, Any] | None:
    context = _event_context(db, event_id)
    if context is None:
        return None
    event, params, canonical_name = context
    gender_expr = gender_sql("rr.age_category")

    # --- Шапка: сегодняшние агрегаты ---
    header_row = _query(
        db,
        f"""
        -- Финишёры = все строки протокола, включая «неизвестных» (status='unknown'/
        -- 'unknown_runner') — они физически финишировали, просто без распознанного
        -- времени. Среднее/лучшее время по-прежнему считаются только по известному времени.
        SELECT count(*) AS finishers,
               round(avg(rr.finish_time_sec) FILTER (WHERE rr.finish_time_sec IS NOT NULL))::int AS avg_sec,
               min(rr.finish_time_sec) FILTER (WHERE {gender_expr} = 'M') AS best_male_sec,
               min(rr.finish_time_sec) FILTER (WHERE {gender_expr} = 'F') AS best_female_sec
        FROM run_results rr
        WHERE rr.event_id = :event_id
        """,
        params,
    )[0]
    volunteers_count = _query(
        db,
        "SELECT count(*) AS cnt FROM volunteer_results WHERE event_id = :event_id",
        params,
    )[0]["cnt"]

    # --- Топ финишей: ранги за всю историю локации (платформа события) ---
    top_rows = _query(
        db,
        f"""
        WITH hist AS (
            SELECT rr.id, rr.finish_time_sec, rr.event_id,
                   {age_group_sql("rr.age_category")} AS age_group,
                   {gender_expr} AS gender
            FROM run_results rr
            JOIN events e ON e.id = rr.event_id
            WHERE e.location_id IN :loc_ids
              AND NOT e.is_test_event
              AND e.event_date <= :event_date
              AND e.{NOT_SECONDARY_SQL}
              AND rr.finish_time_sec IS NOT NULL
        ),
        ranked AS (
            SELECT id, gender, age_group, event_id,
                   ROW_NUMBER() OVER (PARTITION BY gender ORDER BY finish_time_sec) AS gender_rank,
                   COUNT(*) OVER (PARTITION BY gender) AS gender_total,
                   ROW_NUMBER() OVER (PARTITION BY age_group ORDER BY finish_time_sec) AS age_rank,
                   COUNT(*) OVER (PARTITION BY age_group) AS age_total
            FROM hist
        )
        SELECT r.gender, r.gender_rank, r.gender_total, r.age_rank, r.age_total,
               r.age_group,
               rr.position, rr.finish_time_sec, rr.finish_time_display,
               p.id AS participant_id, p.display_name, p.profile_url
        FROM ranked r
        JOIN run_results rr ON rr.id = r.id
        LEFT JOIN participants p ON p.id = rr.participant_id
        WHERE r.event_id = :event_id
        ORDER BY rr.position NULLS LAST
        """,
        params,
        expanding=("loc_ids",),
    )
    top_finishes = []
    for row in top_rows:
        gender_qual = row["gender"] is not None and (
            row["gender_rank"] <= 10 or row["gender_rank"] / row["gender_total"] <= 0.01
        )
        age_qual = row["age_group"] is not None and (
            row["age_rank"] <= 10 or row["age_rank"] / row["age_total"] <= 0.03
        )
        if not (gender_qual or age_qual):
            continue
        top_finishes.append({**row, "gender_qual": gender_qual, "age_qual": age_qual})

    # --- Статистика мероприятия: бегуны ---
    runner_rows = _runner_stat_rows(db, params)
    comeback_threshold = event.event_date - timedelta(days=365)

    def _people(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "participant_id": r["participant_id"],
                "name": r["display_name"],
                "profile_url": r["profile_url"],
            }
            for r in sorted(rows, key=lambda r: (r["display_name"] or ""))
        ]

    newcomers = _people([r for r in runner_rows if r["first_in_system"]])
    guests = _people(
        [r for r in runner_rows if r["first_at_location"] and not r["first_in_system"]]
    )
    personal_bests = _people([r for r in runner_rows if r["is_pb"] and not r["first_in_system"]])
    location_bests = _people(
        [r for r in runner_rows if r["is_location_pb"] and not r["first_at_location"]]
    )
    comebacks = _people(
        [
            r
            for r in runner_rows
            if r["last_run_date"] is not None and r["last_run_date"] <= comeback_threshold
        ]
    )

    # --- Статистика мероприятия: волонтёры ---
    volunteer_rows = _volunteer_stat_rows(db, params)
    first_volunteers = _people([r for r in volunteer_rows if r["first_volunteering"]])
    first_volunteers_at_location = _people(
        [
            r
            for r in volunteer_rows
            if r["first_volunteering_at_location"] and not r["first_volunteering"]
        ]
    )
    new_role_volunteers = _people(
        [
            r
            for r in volunteer_rows
            if not r["first_volunteering"]
            and _new_canonical_roles(r["roles"], r["prev_roles"])
        ]
    )

    # --- Юбилеи в локации и «1 шаг до юбилея» ---
    def _location_counts(table: str) -> list[dict[str, Any]]:
        return _query(
            db,
            f"""
            WITH counts AS (
                SELECT x.participant_id,
                       count(DISTINCT x.event_id) AS total,
                       max(e.event_date) AS last_date,
                       bool_or(x.event_id = CAST(:event_id AS uuid)) AS participated_today
                FROM {table} x
                JOIN events e ON e.id = x.event_id
                WHERE e.location_id IN :loc_ids
                  AND NOT e.is_test_event
                  AND e.event_date <= :event_date
                  AND e.{NOT_SECONDARY_SQL}
                  AND x.participant_id IS NOT NULL
                GROUP BY x.participant_id
            )
            SELECT c.participant_id, c.total, c.last_date, c.participated_today,
                   p.display_name, p.profile_url
            FROM counts c
            JOIN participants p ON p.id = c.participant_id
            WHERE (c.participated_today
                   AND (c.total = 10 OR (c.total >= 25 AND c.total % 25 = 0)))
               OR (c.total % 5 = 4
                   AND (c.total + 1) IN (10, 25, 50, 100, 250)
                   AND c.last_date >= CAST(:event_date AS date) - INTERVAL '3 months')
            ORDER BY c.total DESC, p.display_name
            """,
            params,
            expanding=("loc_ids",),
        )

    def _split_milestones(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        milestones = [
            {
                "participant_id": r["participant_id"],
                "name": r["display_name"],
                "profile_url": r["profile_url"],
                "count": r["total"],
            }
            for r in rows
            if r["participated_today"]
            and (r["total"] == 10 or (r["total"] >= 25 and r["total"] % 25 == 0))
        ]
        one_step = [
            {
                "participant_id": r["participant_id"],
                "name": r["display_name"],
                "profile_url": r["profile_url"],
                "count": r["total"],
                "next_milestone": r["total"] + 1,
            }
            for r in rows
            if r["total"] % 5 == 4 and (r["total"] + 1) in (10, 25, 50, 100, 250)
        ]
        return milestones, one_step

    run_milestones, run_one_step = _split_milestones(_location_counts("run_results"))
    vol_milestones, vol_one_step = _split_milestones(_location_counts("volunteer_results"))

    # --- Глобальные клубы и юбилейные пробежки (вся платформа) ---
    def _global_counts(table: str) -> list[dict[str, Any]]:
        return _query(
            db,
            f"""
            WITH today_participants AS (
                SELECT DISTINCT participant_id FROM {table}
                WHERE event_id = :event_id AND participant_id IS NOT NULL
            ),
            counts AS (
                SELECT x.participant_id, count(DISTINCT x.event_id) AS total
                FROM {table} x
                JOIN events e ON e.id = x.event_id
                WHERE x.participant_id IN (SELECT participant_id FROM today_participants)
                  AND NOT e.is_test_event
                  AND e.event_date <= :event_date
                  AND e.{NOT_SECONDARY_SQL}
                GROUP BY x.participant_id
            )
            SELECT c.participant_id, c.total, p.display_name, p.profile_url
            FROM counts c
            JOIN participants p ON p.id = c.participant_id
            WHERE c.total IN (10, 25, 50, 100, 250) OR (c.total >= 25 AND c.total % 25 = 0)
            ORDER BY c.total DESC, p.display_name
            """,
            params,
        )

    global_runs = _global_counts("run_results")
    global_vols = _global_counts("volunteer_results")

    def _club_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "participant_id": r["participant_id"],
                "name": r["display_name"],
                "profile_url": r["profile_url"],
                "count": r["total"],
            }
            for r in rows
            if r["total"] in CLUB_LEVELS
        ]

    run_clubs = _club_items(global_runs)
    vol_clubs = _club_items(global_vols)
    global_run_jubilees = [
        {
            "participant_id": r["participant_id"],
            "name": r["display_name"],
            "profile_url": r["profile_url"],
            "count": r["total"],
        }
        for r in global_runs
        if r["total"] % 25 == 0 and r["total"] not in CLUB_LEVELS
    ]

    # --- Рекорды локации: трасса (кросс-платформенно) и посещаемость ---
    course_rows = _query(
        db,
        f"""
        SELECT {gender_expr} AS gender,
               min(rr.finish_time_sec) FILTER (WHERE e.event_date < :event_date) AS prev_best_sec,
               min(rr.finish_time_sec) FILTER (WHERE e.id = CAST(:event_id AS uuid)) AS today_best_sec
        FROM run_results rr
        JOIN events e ON e.id = rr.event_id
        WHERE e.location_id IN :all_loc_ids
          AND NOT e.is_test_event
          AND e.event_date <= :event_date
          AND e.{NOT_SECONDARY_SQL}
          AND rr.finish_time_sec IS NOT NULL
        GROUP BY 1
        """,
        params,
        expanding=("all_loc_ids",),
    )
    course_records = []
    for row in course_rows:
        if row["gender"] is None or row["today_best_sec"] is None or row["prev_best_sec"] is None:
            continue
        if row["today_best_sec"] < row["prev_best_sec"]:
            holder = _query(
                db,
                f"""
                SELECT p.id AS participant_id, p.display_name, p.profile_url
                FROM run_results rr
                LEFT JOIN participants p ON p.id = rr.participant_id
                WHERE rr.event_id = :event_id
                  AND rr.finish_time_sec = :best_sec
                  AND {gender_expr} = :gender
                LIMIT 1
                """,
                {
                    "event_id": params["event_id"],
                    "best_sec": row["today_best_sec"],
                    "gender": row["gender"],
                },
            )
            holder_row = holder[0] if holder else {}
            course_records.append(
                {
                    "gender": row["gender"],
                    "time_sec": row["today_best_sec"],
                    "previous_sec": row["prev_best_sec"],
                    "participant_id": holder_row.get("participant_id"),
                    "name": holder_row.get("display_name"),
                    "profile_url": holder_row.get("profile_url"),
                }
            )

    attendance_rows = _query(
        db,
        f"""
        SELECT e.id AS event_id, e.event_date,
               count(rr.id) AS finishers
        FROM events e
        LEFT JOIN run_results rr ON rr.event_id = e.id
        WHERE e.location_id IN :all_loc_ids
          AND NOT e.is_test_event
          AND e.event_date <= :event_date
          AND e.{NOT_SECONDARY_SQL}
        GROUP BY e.id, e.event_date
        ORDER BY e.event_date
        """,
        params,
        expanding=("all_loc_ids",),
    )
    prior_events = [r for r in attendance_rows if str(r["event_id"]) != str(event.id)]
    prior_max_finishers = max((r["finishers"] for r in prior_events), default=None)
    previous_event = max(prior_events, key=lambda r: r["event_date"], default=None)
    today_finishers = header_row["finishers"] or 0
    attendance_record = (
        prior_max_finishers is not None and today_finishers > prior_max_finishers
    )

    telegram_contacts = _query(
        db,
        "SELECT id, telegram_url, label FROM location_contacts WHERE location_id = :location_id ORDER BY created_at",
        {"location_id": str(event.location_id)},
    )
    announce_settings = _query(
        db,
        "SELECT do_not_disturb FROM location_announce_settings WHERE location_id = :location_id",
        {"location_id": str(event.location_id)},
    )

    location_title = canonical_name or event.location.name
    report = {
        "event": {
            "event_id": event.id,
            "event_date": event.event_date,
            "event_number": event.event_number,
            "location_id": event.location_id,
            "location_name": event.location.name,
            "canonical_name": canonical_name,
            "platform_code": event.platform.code,
            "platform_name": event.platform.name,
            "source_url": event.source_url,
            "events_total_at_location": len(attendance_rows),
            "telegram_contacts": telegram_contacts,
            "do_not_disturb": bool(announce_settings[0]["do_not_disturb"]) if announce_settings else False,
        },
        "header": {
            "finishers": today_finishers,
            "volunteers": volunteers_count,
            "avg_time_sec": header_row["avg_sec"],
            "best_male_sec": header_row["best_male_sec"],
            "best_female_sec": header_row["best_female_sec"],
            "previous_event_date": previous_event["event_date"] if previous_event else None,
            "previous_event_finishers": previous_event["finishers"] if previous_event else None,
            "attendance_record": attendance_record,
            "prior_max_finishers": prior_max_finishers,
        },
        "course_records": course_records,
        "top_finishes": [
            {
                "participant_id": r["participant_id"],
                "name": r["display_name"],
                "profile_url": r["profile_url"],
                "position": r["position"],
                "finish_time_sec": r["finish_time_sec"],
                # finish_time_display у 5 вёрст приходит как «00:18:58» — для поста
                # компактнее свой формат из секунд, исходный только как запасной.
                "finish_time_display": fmt_time(r["finish_time_sec"])
                if r["finish_time_sec"] is not None
                else (r["finish_time_display"] or "—"),
                "age_category": r["age_group"],
                "gender": r["gender"],
                "gender_rank": r["gender_rank"],
                "gender_total": r["gender_total"],
                "age_rank": r["age_rank"],
                "age_total": r["age_total"],
                "gender_qualified": r["gender_qual"],
                "age_qualified": r["age_qual"],
            }
            for r in top_finishes
        ],
        "stats": {
            "newcomers": newcomers,
            "guests": guests,
            "personal_bests": personal_bests,
            "location_bests": location_bests,
            "comebacks": comebacks,
            "first_volunteers": first_volunteers,
            "first_volunteers_at_location": first_volunteers_at_location,
            "new_role_volunteers": new_role_volunteers,
        },
        "location_milestones": {"runs": run_milestones, "volunteering": vol_milestones},
        "one_step": {"runs": run_one_step, "volunteering": vol_one_step},
        "clubs": {"runs": run_clubs, "volunteering": vol_clubs},
        "global_run_jubilees": global_run_jubilees,
    }
    report["post_text"] = _build_post_text(report, location_title)
    return report


# ===== Текст поста для Telegram =====

def _join_names(items: list[dict[str, Any]]) -> str:
    return ", ".join(item["name"] or "Неизвестный участник" for item in items)


def _build_post_text(report: dict[str, Any], location_title: str) -> str:
    event = report["event"]
    header_stats = report["header"]
    platform_label = event["platform_name"]

    date_line = f"📅 Дата: **{fmt_date_ru(event['event_date'])}**"
    number = event["event_number"]
    if number:
        suffix = " 🎉" if number % 50 == 0 else ""
        date_line += f" · забег №{number}{suffix}"
    header_lines = [
        "🔢Статистика пробежки",
        f"📍Локация: **{location_title}**",
        date_line,
        f"🏃Финишёров: **{header_stats['finishers']}**"
        + (
            f" (пред. забег — {header_stats['previous_event_finishers']})"
            if header_stats["previous_event_finishers"] is not None
            else ""
        ),
    ]
    if header_stats["volunteers"]:
        header_lines.append(f"🤝Волонтёров: **{header_stats['volunteers']}**")
    header = "\n".join(header_lines)

    # Рекорды локации
    record_lines: list[str] = []
    if header_stats["attendance_record"]:
        record_lines.append(
            f"👥Рекорд посещаемости: {header_stats['finishers']} финишёров "
            f"(прежний максимум — {header_stats['prior_max_finishers']})"
        )
    for record in report["course_records"]:
        gender_label = "мужчин" if record["gender"] == "M" else "женщин"
        name = record.get("name") or "Неизвестный участник"
        record_lines.append(
            f"⚡Рекорд трассы среди {gender_label}: {_bold_surname(name)} — "
            f"{fmt_time(record['time_sec'])} (прежний — {fmt_time(record['previous_sec'])})"
        )
    records_block = "**🚀Рекорды локации:**\n" + "\n".join(record_lines) if record_lines else ""

    # Топ финишей
    top_lines: list[str] = []
    for item in report["top_finishes"]:
        parts: list[str] = []
        if item["age_qualified"]:
            parts.append(
                f"{item['age_rank']}-е место по возрастной группе ({item['age_category']}) "
                f"среди {item['age_total']} финишей"
            )
        if item["gender_qualified"]:
            gender_word = "мужчинам" if item["gender"] == "M" else "женщинам"
            parts.append(
                f"{item['gender_rank']}-й результат среди {item['gender_total']} пробежек по {gender_word}"
            )
        if not parts:
            continue
        name = item["name"] or "Неизвестный участник"
        top_lines.append(f"🔸{_bold_surname(name)} ({item['finish_time_display']}) - {' и '.join(parts)}")
    top_block = "**🔝ТОП финишей:**\n" + "\n".join(top_lines) if top_lines else ""

    # Статистика мероприятия
    stats = report["stats"]
    stats_lines: list[str] = []
    n = len(stats["newcomers"])
    if n:
        stats_lines.append(
            f"+{n} {ru_adj_base(n, 'новый', 'новых')} {ru_plural(n, ('участник', 'участника', 'участников'))}, "
            f"кто ранее не бегал в системе {platform_label}"
        )
    n = len(stats["guests"])
    if n:
        stats_lines.append(
            f"+{n} {ru_plural(n, ('гость', 'гостя', 'гостей'))}, кто ранее не бегал в данной локации"
        )
    n = len(stats["personal_bests"])
    if n:
        stats_lines.append(
            f"+{n} {ru_adj_base(n, 'личный', 'личных')} {ru_plural(n, ('рекорд', 'рекорда', 'рекордов'))}"
        )
    n = len(stats["location_bests"])
    if n:
        stats_lines.append(
            f"+{n} {ru_adj_base(n, 'личный', 'личных')} {ru_plural(n, ('рекорд', 'рекорда', 'рекордов'))} "
            f"на этой локации"
        )
    n = len(stats["comebacks"])
    if n:
        stats_lines.append(
            f"+{n} {ru_plural(n, ('человек', 'человека', 'человек'))}, "
            f"кто вернулся к пробежкам после перерыва больше года"
        )
    n = len(stats["first_volunteers"])
    if n:
        stats_lines.append(
            f"+{n} {ru_plural(n, ('человек', 'человека', 'человек'))}, "
            f"кто впервые волонтёрил в системе {platform_label}"
        )
    n = len(stats["new_role_volunteers"])
    if n:
        stats_lines.append(
            f"+{n} {ru_plural(n, ('человек', 'человека', 'человек'))}, "
            f"кто волонтёрил на новой для себя роли"
        )
    stats_block = "**📊Статистика мероприятия:**\n" + "\n".join(stats_lines) if stats_lines else ""

    # Юбилеи в локации
    def _milestone_lines(items: list[dict[str, Any]], word: str) -> list[str]:
        by_count: dict[int, list[dict[str, Any]]] = {}
        for item in items:
            by_count.setdefault(item["count"], []).append(item)
        return [
            f"{count} {word} в локации: {_join_names(people)}"
            for count, people in sorted(by_count.items(), reverse=True)
        ]

    jub_lines = _milestone_lines(report["location_milestones"]["runs"], "пробежек")
    jub_lines += _milestone_lines(report["location_milestones"]["volunteering"], "волонтёрств")
    jub_block = "**🏆Юбилейные участия внутри локации:**\n" + "\n".join(jub_lines) if jub_lines else ""

    # 1 шаг до юбилея
    def _one_step_lines(items: list[dict[str, Any]], word: str) -> list[str]:
        by_next: dict[int, list[dict[str, Any]]] = {}
        for item in items:
            by_next.setdefault(item["next_milestone"], []).append(item)
        return [
            f"1 {word} до {next_n} в локации: {_join_names(people)}"
            for next_n, people in sorted(by_next.items(), reverse=True)
        ]

    one_step_lines = _one_step_lines(report["one_step"]["runs"], "пробежка")
    one_step_lines += _one_step_lines(report["one_step"]["volunteering"], "волонтёрство")
    one_step_block = (
        "**✌1 шаг до юбилейного участия внутри локации:**\n" + "\n".join(one_step_lines)
        if one_step_lines
        else ""
    )

    # Клубы
    def _club_lines(items: list[dict[str, Any]], label: str) -> list[str]:
        by_level: dict[int, list[dict[str, Any]]] = {}
        for item in items:
            by_level.setdefault(item["count"], []).append(item)
        return [
            f"🌟Клуб {level} {label}: {_join_names(people)}"
            for level, people in sorted(by_level.items())
        ]

    club_lines = _club_lines(report["clubs"]["runs"], f"пробежек в системе {platform_label}")
    club_lines += _club_lines(report["clubs"]["volunteering"], f"волонтёрств в системе {platform_label}")
    club_block = "**🥇Клубы:**\n" + "\n".join(club_lines) if club_lines else ""

    # Глобальные юбилейные пробежки (кратные 25, кроме клубных уровней)
    jubilee_items = [f"{item['name']} ({item['count']} пробежек)" for item in report["global_run_jubilees"]]
    jub_extra_block = "🎖️**Юбилейные пробежки:**\n" + ", ".join(jubilee_items) if jubilee_items else ""

    blocks = [
        header,
        records_block,
        top_block,
        stats_block,
        jub_block,
        one_step_block,
        club_block,
        jub_extra_block,
        POST_SIGNATURE,
    ]
    return "\n\n".join(block for block in blocks if block)


# ===== Свод по пробежке для кабинета организатора =====

# Юбилейные уровни сайта: 10, дальше кратные 25 — как в отчёте админки
# (легаси-Grafana считала кратные 5, но на сайте юбилеи уже переосмыслены).


def _milestone_value(total: int) -> int | None:
    """total, если это юбилейное число, иначе None."""
    if total == 10 or (total >= 25 and total % 25 == 0):
        return total
    return None


def _volunteer_role_counts(db: Session, params: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Счётчики волонтёрств по каноническим ролям (включая сегодняшнее событие).

    participant_id (строкой) → {канонический ключ роли: число событий}.
    Канонизация в Python: сырые ярлыки несут счётчики parkrun («Marshal (12×)»)
    и разные написания по системам.
    """
    rows = _query(
        db,
        f"""
        SELECT vr.participant_id, vr.role, vr.event_id
        FROM volunteer_results vr
        JOIN events e ON e.id = vr.event_id
        WHERE vr.participant_id IN (
                SELECT DISTINCT participant_id FROM volunteer_results
                WHERE event_id = :event_id AND participant_id IS NOT NULL
              )
          AND e.event_date <= :event_date
          AND NOT e.is_test_event
          AND e.{NOT_SECONDARY_SQL}
          AND vr.role IS NOT NULL
        """,
        params,
    )
    events_by_role: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        canonical = canonical_volunteer_role(row["role"])
        if canonical is None:
            continue
        participant_key = str(row["participant_id"])
        events_by_role.setdefault(participant_key, {}).setdefault(canonical.key, set()).add(
            str(row["event_id"])
        )
    return {
        participant_key: {role_key: len(event_ids) for role_key, event_ids in roles.items()}
        for participant_key, roles in events_by_role.items()
    }


def build_event_svod(db: Session, event_id: UUID) -> dict[str, Any] | None:
    """Свод по пробежке для отчёта оргкоманды: построчные таблицы бегунов и волонтёров.

    Перенос Grafana-дашборда «Свод по пробежке для отчетов» на данные сайта.
    Флаги считаются теми же запросами, что и отчёт админки (_runner_stat_rows /
    _volunteer_stat_rows), чтобы цифры поста и свода совпадали.
    """
    context = _event_context(db, event_id)
    if context is None:
        return None
    event, params, canonical_name = context

    runner_rows = _runner_stat_rows(db, params)
    volunteer_rows = _volunteer_stat_rows(db, params)
    role_counts = _volunteer_role_counts(db, params)
    comeback_threshold = event.event_date - timedelta(days=365)

    finishers_total = _query(
        db,
        "SELECT count(*) AS cnt FROM run_results WHERE event_id = :event_id",
        params,
    )[0]["cnt"]
    volunteers_total = _query(
        db,
        "SELECT count(*) AS cnt FROM volunteer_results WHERE event_id = :event_id",
        params,
    )[0]["cnt"]

    runners: list[dict[str, Any]] = []
    for row in sorted(
        runner_rows, key=lambda r: (r["position"] is None, r["position"] or 0)
    ):
        location_runs = int(row["location_runs_count"])
        platform_runs = int(row["platform_runs_count"])
        runners.append(
            {
                "position": row["position"],
                "participant_id": row["participant_id"],
                "name": row["display_name"],
                "profile_url": row["profile_url"],
                "finish_time_sec": row["finish_time_sec"],
                "finish_time_display": fmt_time(row["finish_time_sec"]),
                "first_in_system": bool(row["first_in_system"]),
                "first_at_location": bool(row["first_at_location"]),
                "is_pb": bool(row["is_pb"]),
                "is_location_pb": bool(row["is_location_pb"]),
                "comeback": (
                    row["last_run_date"] is not None
                    and row["last_run_date"] <= comeback_threshold
                ),
                "location_runs_count": location_runs,
                "platform_runs_count": platform_runs,
                "location_milestone": _milestone_value(location_runs),
                "location_next_milestone": _milestone_value(location_runs + 1),
                "platform_milestone": _milestone_value(platform_runs),
                "platform_next_milestone": _milestone_value(platform_runs + 1),
            }
        )

    volunteers: list[dict[str, Any]] = []
    for row in sorted(volunteer_rows, key=lambda r: (r["display_name"] or "")):
        location_vols = int(row["location_vol_count"])
        platform_vols = int(row["platform_vol_count"])
        participant_key = str(row["participant_id"])
        roles_detail: list[dict[str, Any]] = []
        seen_role_keys: set[str] = set()
        for role in row["roles"] or []:
            canonical = canonical_volunteer_role(role)
            if canonical is None or canonical.key in seen_role_keys:
                continue
            seen_role_keys.add(canonical.key)
            role_total = role_counts.get(participant_key, {}).get(canonical.key, 1)
            roles_detail.append(
                {
                    "label": canonical.label,
                    "count": role_total,
                    "milestone": _milestone_value(role_total),
                }
            )
        volunteers.append(
            {
                "participant_id": row["participant_id"],
                "name": row["display_name"],
                "profile_url": row["profile_url"],
                "roles": roles_detail,
                "new_roles": _new_canonical_roles(row["roles"], row["prev_roles"])
                if not row["first_volunteering"]
                else [],
                "first_volunteering": bool(row["first_volunteering"]),
                "first_at_location": bool(row["first_volunteering_at_location"]),
                "location_vol_count": location_vols,
                "platform_vol_count": platform_vols,
                "location_milestone": _milestone_value(location_vols),
                "location_next_milestone": _milestone_value(location_vols + 1),
                "platform_milestone": _milestone_value(platform_vols),
                "platform_next_milestone": _milestone_value(platform_vols + 1),
            }
        )

    return {
        "event": {
            "event_id": event.id,
            "event_date": event.event_date,
            "event_number": event.event_number,
            "location_id": event.location_id,
            "location_name": canonical_name or event.location.name,
            "platform_code": event.platform.code,
            "platform_name": event.platform.name,
            "source_url": event.source_url,
            "finishers_count": finishers_total,
            "volunteers_count": volunteers_total,
        },
        "runners": runners,
        "volunteers": volunteers,
    }
