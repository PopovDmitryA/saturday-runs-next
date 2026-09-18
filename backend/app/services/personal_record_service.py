from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Participant, Platform, PlatformLink, RunResult
from app.participant_identity import anonymous_participant_sql, is_anonymous_participant
from app.services.location_catalog_service import LocationCatalogIndex
from app.sync import upsert

TIME_BASED_PR_PLATFORMS = frozenset({"parkrun", "s95", "five_verst", "runpark"})

# five_verst и HTML-протокол s95 уже проставляют is_first_run/is_first_run_at_location
# из иконок достижений на самом сайте — это авторитетный источник, трогать его не нужно.
# Для остальных путей синка (s95 JSON API, parkrun, runpark) поля никогда не заполняются,
# поэтому там флаг выводим сами: хронологически первый (не "не в зачёте") протокол
# участника на платформе / на локации.
FIRST_RUN_DERIVED_PLATFORMS = frozenset({"s95", "parkrun", "runpark"})


def _five_verst_protocol_personal_record(run: RunResult) -> bool:
    for label in run.achievement_labels or []:
        if "личный рекорд" in str(label).casefold():
            return True
    return False


def run_shows_personal_record(platform_code: str, run: RunResult) -> bool:
    if run.is_pr:
        return True
    if platform_code == "five_verst" and _five_verst_protocol_personal_record(run):
        return True
    return False


def run_is_personal_record_sql_filter():
    """SQL filter matching run_shows_personal_record (requires Platform join)."""
    five_verst_achievement_pr = and_(
        Platform.code == "five_verst",
        func.lower(cast(RunResult.achievement_labels, String)).like("%личный рекорд%"),
    )
    return or_(
        RunResult.is_pr.is_(True),
        five_verst_achievement_pr,
    )


def run_displayed_personal_record_sql_filter():
    """SQL filter for the personal-records page / dashboard tile (requires Platform join).

    Единый критерий «строка попадает в список PR-пробежек»: рекорд системы
    (is_pr / метка 5 вёрст), глобальный рекорд, рекорд локации или дебют в
    системе. Счётчик на плитке главной обязан использовать этот же фильтр,
    иначе цифра расходится с числом строк в списке."""
    return or_(
        run_is_personal_record_sql_filter(),
        RunResult.is_global_pr.is_(True),
        RunResult.is_location_pr.is_(True),
        RunResult.is_first_run.is_(True),
    )


def reset_personal_records(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
) -> int:
    platform = upsert.get_platform(db, platform_code)
    event_ids = select(Event.id).where(Event.platform_id == platform.id)
    query = db.query(RunResult).filter(RunResult.event_id.in_(event_ids))
    if participant_id is not None:
        query = query.filter(RunResult.participant_id == participant_id)
    return query.update({RunResult.is_pr: False}, synchronize_session=False)


def recalculate_personal_records(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
    commit_every: int = 200,
    reset: bool = True,
) -> dict[str, int]:
    """Mark is_pr on time improvements and 5verst protocol PR labels.

    Дебют рекордом НЕ является: первый результат участника на платформе (как и
    первый забег «в нашей БД» у участников с неполной историей) задаёт базовое
    время, is_pr получает только более быстрый последующий забег. Сама 5 вёрст
    дебют рекордом не помечает никогда.

    Тестовые события (is_test_event) не участвуют вовсе: сами is_pr не получают
    и базовое время не задают — иначе официальный дебют, пробежанный быстрее
    тест-забега, ложно помечался рекордом (кейс Егора Свиридова, 19.07.2026)."""
    if reset:
        reset_personal_records(db, platform_code, participant_id=participant_id)
        db.flush()

    platform = upsert.get_platform(db, platform_code)
    participant_query = (
        db.query(RunResult.participant_id)
        .join(Event, RunResult.event_id == Event.id)
        .filter(
            Event.platform_id == platform.id,
            RunResult.participant_id.isnot(None),
        )
        .distinct()
    )
    if participant_id is not None:
        participant_query = participant_query.filter(RunResult.participant_id == participant_id)

    participant_ids = [row[0] for row in participant_query.all()]
    participants_touched = 0
    updated = 0
    pr_runs = 0

    for index, current_participant_id in enumerate(participant_ids, start=1):
        rows = (
            db.query(RunResult, Event)
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                Event.platform_id == platform.id,
                RunResult.participant_id == current_participant_id,
            )
            .order_by(Event.event_date, Event.event_number, Event.location_id)
            .all()
        )
        if not rows:
            continue

        participants_touched += 1

        # "Не в зачёте" duplicates (secondary crosslink events) must never carry a PR
        # marker — even inside their own system — because the same protocol is counted
        # on the primary platform. Force is_pr=False on them and exclude them from the
        # best-time / first-run computation so the PR lands on the counted run instead.
        row_event_ids = [event.id for _run, event in rows]
        secondary_event_ids: set[UUID] = set()
        if row_event_ids:
            secondary_event_ids = {
                cl_row[0]
                for cl_row in db.query(EventCrosslink.secondary_event_id)
                .filter(EventCrosslink.secondary_event_id.in_(row_event_ids))
                .all()
            }

        counted_rows = []
        for run, event in rows:
            if event.id in secondary_event_ids or event.is_test_event:
                if run.is_pr:
                    run.is_pr = False
                    updated += 1
                continue
            counted_rows.append((run, event))

        best_time: int | None = None
        for run, _event in counted_rows:
            time_based_pr = False
            finish_time = run.finish_time_sec
            if finish_time is not None and finish_time > 0:
                if best_time is None:
                    best_time = finish_time  # baseline: первый результат — не рекорд
                elif finish_time < best_time:
                    time_based_pr = True
                    best_time = finish_time
            new_is_pr = time_based_pr
            if platform.code == "five_verst" and _five_verst_protocol_personal_record(run):
                new_is_pr = True
            if run.is_pr != new_is_pr:
                run.is_pr = new_is_pr
                updated += 1
            if new_is_pr:
                pr_runs += 1

        if commit_every > 0 and index % commit_every == 0:
            db.commit()

    return {
        "platform_code": platform_code,
        "participants_touched": participants_touched,
        "runs_updated": updated,
        "pr_runs": pr_runs,
    }


def recalculate_participants_personal_records(
    db: Session,
    platform_code: str,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate is_pr for specific participants (safe after profile/protocol sync)."""
    if platform_code not in TIME_BASED_PR_PLATFORMS:
        return
    for participant_id in set(participant_ids):
        recalculate_personal_records(db, platform_code, participant_id=participant_id)


def recalculate_first_run_flags(
    db: Session,
    platform_code: str,
    *,
    participant_id: UUID | None = None,
    commit_every: int = 200,
) -> dict[str, int]:
    """Derive is_first_run / is_first_run_at_location from chronological run order.

    For each participant on the platform, orders their (non "не в зачёте") runs by
    event_date/event_number/location_id: the first one is is_first_run=True, and the
    first run at each distinct location is is_first_run_at_location=True. Secondary
    crosslink duplicates and test events (is_test_event) never carry either flag and
    do not consume the "first" slot — дебютом считается первый официальный старт.

    Безымянные строки протокола («НЕИЗВЕСТНЫЙ», «Неизвестный бегун») не несут
    флагов вовсе: под каждую такую строку заводится одноразовая личность, и по
    хронологии она всегда «впервые» (см. app/participant_identity.py).
    """
    platform = upsert.get_platform(db, platform_code)
    participant_query = (
        db.query(RunResult.participant_id, Participant.external_user_id, Participant.display_name)
        .join(Event, RunResult.event_id == Event.id)
        .join(Participant, Participant.id == RunResult.participant_id)
        .filter(
            Event.platform_id == platform.id,
            RunResult.participant_id.isnot(None),
        )
        .distinct()
    )
    if participant_id is not None:
        participant_query = participant_query.filter(RunResult.participant_id == participant_id)

    participant_rows = participant_query.all()
    participants_touched = 0
    updated = 0

    for index, (current_participant_id, external_user_id, display_name) in enumerate(
        participant_rows, start=1
    ):
        rows = (
            db.query(RunResult, Event)
            .join(Event, RunResult.event_id == Event.id)
            .filter(
                Event.platform_id == platform.id,
                RunResult.participant_id == current_participant_id,
            )
            .order_by(Event.event_date, Event.event_number, Event.location_id, RunResult.id)
            .all()
        )
        if not rows:
            continue

        participants_touched += 1

        if is_anonymous_participant(external_user_id, display_name):
            for run, _event in rows:
                if run.is_first_run or run.is_first_run_at_location:
                    run.is_first_run = False
                    run.is_first_run_at_location = False
                    updated += 1
            if commit_every > 0 and index % commit_every == 0:
                db.commit()
            continue

        row_event_ids = [event.id for _run, event in rows]
        secondary_event_ids: set[UUID] = set()
        if row_event_ids:
            secondary_event_ids = {
                cl_row[0]
                for cl_row in db.query(EventCrosslink.secondary_event_id)
                .filter(EventCrosslink.secondary_event_id.in_(row_event_ids))
                .all()
            }

        counted_rows = [
            (run, event)
            for run, event in rows
            if event.id not in secondary_event_ids and not event.is_test_event
        ]

        seen_locations: set[UUID] = set()
        for run_index, (run, event) in enumerate(counted_rows):
            new_is_first_run = run_index == 0
            new_is_first_run_at_location = event.location_id not in seen_locations
            seen_locations.add(event.location_id)
            if run.is_first_run != new_is_first_run:
                run.is_first_run = new_is_first_run
                updated += 1
            if run.is_first_run_at_location != new_is_first_run_at_location:
                run.is_first_run_at_location = new_is_first_run_at_location
                updated += 1

        for run, event in rows:
            if (event.id in secondary_event_ids or event.is_test_event) and (
                run.is_first_run or run.is_first_run_at_location
            ):
                run.is_first_run = False
                run.is_first_run_at_location = False
                updated += 1

        if commit_every > 0 and index % commit_every == 0:
            db.commit()

    return {
        "platform_code": platform_code,
        "participants_touched": participants_touched,
        "runs_updated": updated,
    }


def recalculate_participants_first_run_flags(
    db: Session,
    platform_code: str,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate first-run flags for specific participants (safe after protocol sync)."""
    if platform_code not in FIRST_RUN_DERIVED_PLATFORMS:
        return
    for participant_id in set(participant_ids):
        recalculate_first_run_flags(db, platform_code, participant_id=participant_id)


# Тот же инвариант, что и в recalculate_first_run_flags, но одним запросом:
# «сохранённый флаг обязан совпадать с хронологией». Нужен сторожем, потому что
# флаг живёт в таблице, а upsert протокола затирает его на False (адаптеры
# s95/parkrun/runpark его не заполняют) — стоит какому-нибудь пути синка забыть
# про пересчёт, и цифра «Новичков» тихо уезжает вниз. Ровно так на 17.09.2026
# на проде потерялись 3004 строки s95 на 538 протоколах.
_FIRST_RUN_MISMATCH_SQL = """
WITH protocol_rows AS (
    SELECT
        rr.id,
        rr.participant_id,
        rr.is_first_run,
        rr.is_first_run_at_location,
        e.event_date,
        e.event_number,
        e.location_id,
        (ec.secondary_event_id IS NOT NULL OR e.is_test_event) AS excluded,
        {anonymous_sql} AS anonymous
    FROM run_results rr
    JOIN events e ON e.id = rr.event_id
    JOIN platforms p ON p.id = e.platform_id
    LEFT JOIN participants pt ON pt.id = rr.participant_id
    LEFT JOIN event_crosslinks ec ON ec.secondary_event_id = e.id
    WHERE p.code = :platform_code
      AND rr.participant_id IS NOT NULL
      -- Сужение на конкретных участников: тестам и точечному ремонту незачем
      -- перебирать платформу целиком. NULL — «все».
      AND (
        CAST(:participant_ids AS uuid[]) IS NULL
        OR rr.participant_id = ANY(CAST(:participant_ids AS uuid[]))
      )
), counted AS (
    SELECT * FROM protocol_rows WHERE NOT excluded AND NOT anonymous
), expected AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY participant_id
            ORDER BY event_date, event_number, location_id, id
        ) = 1 AS want_first,
        row_number() OVER (
            PARTITION BY participant_id, location_id
            ORDER BY event_date, event_number, location_id, id
        ) = 1 AS want_first_at_location
    FROM counted
), mismatched AS (
    SELECT
        r.participant_id,
        r.is_first_run IS DISTINCT FROM COALESCE(x.want_first, false) AS first_run_off,
        r.is_first_run_at_location IS DISTINCT FROM COALESCE(x.want_first_at_location, false)
            AS first_at_location_off,
        r.is_first_run AND NOT COALESCE(x.want_first, false) AS extra_first_run,
        COALESCE(x.want_first, false) AND NOT r.is_first_run AS missing_first_run
    FROM protocol_rows r
    LEFT JOIN expected x ON x.id = r.id
)
{tail}
"""

_FIRST_RUN_MISMATCH_COUNTS_TAIL = """
SELECT
    count(*) FILTER (WHERE first_run_off) AS first_run_mismatch,
    count(*) FILTER (WHERE first_at_location_off) AS first_at_location_mismatch,
    count(*) FILTER (WHERE extra_first_run) AS extra_first_run,
    count(*) FILTER (WHERE missing_first_run) AS missing_first_run
FROM mismatched
"""

_FIRST_RUN_MISMATCH_PARTICIPANTS_TAIL = """
SELECT DISTINCT participant_id
FROM mismatched
WHERE first_run_off OR first_at_location_off
"""


def _first_run_mismatch_sql(tail: str) -> str:
    return _FIRST_RUN_MISMATCH_SQL.format(
        anonymous_sql=anonymous_participant_sql("pt.external_user_id", "pt.display_name"),
        tail=tail,
    )


def first_run_flag_mismatches(
    db: Session,
    platform_code: str,
    *,
    participant_ids: Sequence[UUID] | None = None,
) -> dict[str, int]:
    """Сколько строк платформы разошлись с правилом дебюта (0 — всё сходится)."""
    row = db.execute(
        text(_first_run_mismatch_sql(_FIRST_RUN_MISMATCH_COUNTS_TAIL)),
        {
            "platform_code": platform_code,
            "participant_ids": list(participant_ids) if participant_ids is not None else None,
        },
    ).one()
    return {
        "platform_code": platform_code,
        "first_run_mismatch": int(row.first_run_mismatch),
        "first_at_location_mismatch": int(row.first_at_location_mismatch),
        "extra_first_run": int(row.extra_first_run),
        "missing_first_run": int(row.missing_first_run),
    }


def repair_first_run_flags(
    db: Session,
    platform_code: str,
    *,
    participant_ids: Sequence[UUID] | None = None,
) -> dict[str, int]:
    """Пересчитать флаги только тем участникам, у кого они разъехались с правилом.

    Дешёвая замена полному пересчёту платформы после массовой заливки: сам
    поиск расхождений — один запрос, а участников с ними обычно единицы.
    Полный проход по всем участникам стоил бы столько же, сколько пересчёт
    личных рекордов, и ради десятка строк его гонять незачем.
    """
    if platform_code not in FIRST_RUN_DERIVED_PLATFORMS:
        return {"platform_code": platform_code, "participants_repaired": 0, "runs_updated": 0}

    drifted = [
        row[0]
        for row in db.execute(
            text(_first_run_mismatch_sql(_FIRST_RUN_MISMATCH_PARTICIPANTS_TAIL)),
            {
                "platform_code": platform_code,
                "participant_ids": list(participant_ids) if participant_ids is not None else None,
            },
        ).all()
    ]
    runs_updated = 0
    for participant_id in drifted:
        stats = recalculate_first_run_flags(db, platform_code, participant_id=participant_id)
        runs_updated += stats["runs_updated"]
    return {
        "platform_code": platform_code,
        "participants_repaired": len(drifted),
        "runs_updated": runs_updated,
    }


def user_secondary_crosslinked_run_ids(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> set[UUID]:
    """Run IDs that are duplicates via event_crosslinks: the user's run sits in a
    secondary crosslink event AND the user also has a run in the primary event (i.e. the
    "не в зачёте" runs shown in the runs list). These must not participate in any personal
    record calculation — neither the per-system PR nor the global all-systems record."""
    primary_run_sq = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(Event.id == EventCrosslink.primary_event_id)
        .correlate(EventCrosslink)
        .exists()
    )
    query = (
        db.query(RunResult.id)
        .join(Event, RunResult.event_id == Event.id)
        .join(EventCrosslink, EventCrosslink.secondary_event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
        .filter(primary_run_sq)
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    return {row[0] for row in query.distinct().all()}


def reset_cross_platform_personal_records(db: Session, user_id: UUID) -> int:
    run_ids = (
        db.query(RunResult.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(PlatformLink.user_id == user_id)
    )
    return (
        db.query(RunResult)
        .filter(RunResult.id.in_(run_ids))
        .update(
            {RunResult.is_global_pr: False, RunResult.is_location_pr: False},
            synchronize_session=False,
        )
    )


def recalculate_cross_platform_personal_records(
    db: Session,
    user_id: UUID,
    *,
    catalog_index: LocationCatalogIndex | None = None,
    include_test_events: bool = False,
    reset: bool = True,
) -> dict[str, int]:
    """Mark is_global_pr (all-time best finish across every platform) and
    is_location_pr (best finish at one physical location across platforms) from
    chronological run order. Both are baseline-style: the very first run (and a
    location's first-ever run) sets the reference time but is never a record —
    only a later run that beats the standing best time is.

    Both records are athlete-wide (span every platform the user has linked),
    unlike is_pr which only compares within a single platform — hence this
    operates on user_id rather than participant_id."""
    if reset:
        reset_cross_platform_personal_records(db, user_id)
        db.flush()

    excluded_ids = user_secondary_crosslinked_run_ids(
        db, user_id, include_test_events=include_test_events
    )
    query = (
        db.query(RunResult, Event, Location, Platform)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Platform.id,
            RunResult.finish_time_sec.isnot(None),
            RunResult.finish_time_sec > 0,
        )
        .order_by(Event.event_date, Event.event_number, Event.location_id, RunResult.id)
    )
    if not include_test_events:
        query = query.filter(Event.is_test_event.is_(False))
    if excluded_ids:
        query = query.filter(RunResult.id.notin_(excluded_ids))

    index = catalog_index or LocationCatalogIndex(db)
    global_best: int | None = None
    best_by_location: dict[str, int] = {}
    updated = 0
    global_pr_runs = 0
    location_pr_runs = 0

    for run, _event, location, platform in query.all():
        finish_time = run.finish_time_sec

        is_global_pr = global_best is not None and finish_time < global_best
        if global_best is None or finish_time < global_best:
            global_best = finish_time

        key = index.canonical_identity_key(location, platform.code)
        previous_location_best = best_by_location.get(key)
        is_location_pr = previous_location_best is not None and finish_time < previous_location_best
        if previous_location_best is None or finish_time < previous_location_best:
            best_by_location[key] = finish_time

        if run.is_global_pr != is_global_pr or run.is_location_pr != is_location_pr:
            run.is_global_pr = is_global_pr
            run.is_location_pr = is_location_pr
            updated += 1
        if is_global_pr:
            global_pr_runs += 1
        if is_location_pr:
            location_pr_runs += 1

    return {
        "runs_updated": updated,
        "global_pr_runs": global_pr_runs,
        "location_pr_runs": location_pr_runs,
    }


def recalculate_participants_cross_platform_personal_records(
    db: Session,
    participant_ids: set[UUID] | list[UUID],
) -> None:
    """Recalculate is_global_pr/is_location_pr for the users linked to these
    participants. Safe to call after any single-platform sync: both records are
    athlete-wide, so a change on one platform can only be reflected correctly by
    recomputing across all of the affected users' linked platforms."""
    ids = set(participant_ids)
    if not ids:
        return
    user_ids = {
        row[0]
        for row in db.query(PlatformLink.user_id)
        .filter(PlatformLink.participant_id.in_(ids))
        .distinct()
        .all()
    }
    if not user_ids:
        return
    catalog_index = LocationCatalogIndex(db)
    for uid in user_ids:
        recalculate_cross_platform_personal_records(db, uid, catalog_index=catalog_index)
