from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.activity_url import resolve_activity_url
from app.models import Event, EventCrosslink, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.location_catalog_service import LocationCatalogIndex

PARTICIPANT_KEY_PREFIX = "p:"
SITE_USER_KEY_PREFIX = "u:"


@dataclass
class _SiteIdentity:
    user_id: UUID
    serial_id: int
    display_name: str | None


@dataclass
class _MeetingRow:
    their_time_sec: int | None
    their_position: int | None
    event_date: date


@dataclass
class _CoRunnerStats:
    display_name: str | None
    profile_url: str | None
    platform_codes: list[str] = field(default_factory=list)
    site_serial_id: int | None = None
    # canonical event id -> best known meeting row (для дедупликации кросслинков)
    meetings_by_event: dict[UUID, _MeetingRow] = field(default_factory=dict)


def _canonical_event_map(db: Session, event_ids: set[UUID]) -> dict[UUID, UUID]:
    """Map event ids to a canonical id so a crosslinked pair counts as one event."""
    if not event_ids:
        return {}
    rows = (
        db.query(EventCrosslink.primary_event_id, EventCrosslink.secondary_event_id)
        .filter(
            or_(
                EventCrosslink.primary_event_id.in_(event_ids),
                EventCrosslink.secondary_event_id.in_(event_ids),
            )
        )
        .all()
    )
    return {secondary: primary for primary, secondary in rows}


def _site_identities(db: Session, participant_ids: set[UUID]) -> dict[UUID, _SiteIdentity]:
    """Participants belonging to registered site users with public profiles.

    Приватные профили сознательно не склеиваем: объединённая строка раскрывала бы
    связь платформенных аккаунтов одного человека.
    """
    if not participant_ids:
        return {}
    rows = (
        db.query(PlatformLink.participant_id, User.id, User.serial_id, User.display_name)
        .join(User, PlatformLink.user_id == User.id)
        .filter(
            PlatformLink.participant_id.in_(participant_ids),
            User.profile_private.is_(False),
        )
        .all()
    )
    return {
        row[0]: _SiteIdentity(user_id=row[1], serial_id=int(row[2]), display_name=row[3])
        for row in rows
    }


def _user_results_by_canonical_event(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool,
) -> tuple[dict[UUID, tuple[int | None, int | None]], set[UUID], dict[UUID, UUID]]:
    """User's run results keyed by canonical event id.

    Returns (canonical event -> (time, position), user participant ids, canonical map).
    """
    user_query = (
        db.query(RunResult.event_id, RunResult.finish_time_sec, RunResult.position, RunResult.participant_id)
        .join(Event, RunResult.event_id == Event.id)
        .join(PlatformLink, PlatformLink.participant_id == RunResult.participant_id)
        .filter(
            PlatformLink.user_id == user_id,
            PlatformLink.platform_id == Event.platform_id,
        )
    )
    if not include_test_events:
        user_query = user_query.filter(Event.is_test_event.is_(False))
    rows = user_query.all()
    event_ids = {row[0] for row in rows}
    canonical_map = _canonical_event_map(db, event_ids)

    results: dict[UUID, tuple[int | None, int | None]] = {}
    participant_ids: set[UUID] = set()
    for event_id, finish_time_sec, position, participant_id in rows:
        participant_ids.add(participant_id)
        canonical = canonical_map.get(event_id, event_id)
        existing = results.get(canonical)
        if existing is None or (existing[0] is None and finish_time_sec is not None):
            results[canonical] = (finish_time_sec, position)
    return results, participant_ids, canonical_map


def list_co_runners(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Top people who appeared in the same run protocols as the user.

    Зарегистрированные на сайте участники с открытым профилем склеиваются в одну
    строку по всем их платформам; остальные остаются по-платформенно.
    """
    user_results, user_participant_ids, canonical_map = _user_results_by_canonical_event(
        db, user_id, include_test_events=include_test_events
    )
    if not user_results:
        return []

    user_event_ids = set(user_results.keys()) | set(canonical_map.keys())
    other_rows = (
        db.query(
            RunResult.event_id,
            RunResult.finish_time_sec,
            RunResult.position,
            Participant.id,
            Participant.display_name,
            Participant.profile_url,
            Platform.code,
            Event.event_date,
        )
        .join(Event, RunResult.event_id == Event.id)
        .join(Platform, Event.platform_id == Platform.id)
        .join(Participant, RunResult.participant_id == Participant.id)
        .filter(
            RunResult.event_id.in_(user_event_ids),
            RunResult.participant_id.notin_(user_participant_ids),
        )
        .all()
    )

    site_by_participant = _site_identities(db, {row[3] for row in other_rows})

    stats_by_key: dict[str, _CoRunnerStats] = {}
    for event_id, their_time, their_position, participant_id, display_name, profile_url, platform_code, event_date in other_rows:
        site = site_by_participant.get(participant_id)
        key = (
            f"{SITE_USER_KEY_PREFIX}{site.user_id}"
            if site is not None
            else f"{PARTICIPANT_KEY_PREFIX}{participant_id}"
        )
        stats = stats_by_key.get(key)
        if stats is None:
            stats = _CoRunnerStats(
                display_name=(site.display_name if site is not None and site.display_name else display_name),
                profile_url=profile_url,
                site_serial_id=site.serial_id if site is not None else None,
            )
            stats_by_key[key] = stats
        if platform_code not in stats.platform_codes:
            stats.platform_codes.append(platform_code)
        if stats.profile_url != profile_url:
            stats.profile_url = None  # несколько платформ — внешней ссылки нет

        canonical = canonical_map.get(event_id, event_id)
        existing = stats.meetings_by_event.get(canonical)
        if existing is None or (existing.their_time_sec is None and their_time is not None):
            stats.meetings_by_event[canonical] = _MeetingRow(
                their_time_sec=their_time,
                their_position=their_position,
                event_date=event_date,
            )

    ranked = sorted(
        stats_by_key.items(),
        key=lambda item: (-len(item[1].meetings_by_event), item[1].display_name or ""),
    )[:limit]

    items: list[dict[str, object]] = []
    for key, stats in ranked:
        my_wins = 0
        their_wins = 0
        timed = 0
        first_meeting: date | None = None
        last_meeting: date | None = None
        for canonical, meeting in stats.meetings_by_event.items():
            my_time, my_position = user_results.get(canonical, (None, None))
            if my_time is not None and meeting.their_time_sec is not None:
                timed += 1
                if my_time < meeting.their_time_sec:
                    my_wins += 1
                elif meeting.their_time_sec < my_time:
                    their_wins += 1
                elif my_position is not None and meeting.their_position is not None:
                    if my_position < meeting.their_position:
                        my_wins += 1
                    elif meeting.their_position < my_position:
                        their_wins += 1
            if first_meeting is None or meeting.event_date < first_meeting:
                first_meeting = meeting.event_date
            if last_meeting is None or meeting.event_date > last_meeting:
                last_meeting = meeting.event_date
        items.append(
            {
                "participant_key": key,
                "display_name": stats.display_name,
                "profile_url": stats.profile_url,
                "platform_codes": stats.platform_codes,
                "site_serial_id": stats.site_serial_id,
                "meetings": len(stats.meetings_by_event),
                "my_wins": my_wins,
                "their_wins": their_wins,
                "timed_meetings": timed,
                "first_meeting_date": first_meeting,
                "last_meeting_date": last_meeting,
            }
        )
    return items


def _resolve_key_participant_ids(db: Session, key: str) -> list[UUID]:
    if key.startswith(SITE_USER_KEY_PREFIX):
        site_user_id = UUID(key[len(SITE_USER_KEY_PREFIX):])
        rows = (
            db.query(PlatformLink.participant_id)
            .join(User, PlatformLink.user_id == User.id)
            .filter(
                PlatformLink.user_id == site_user_id,
                PlatformLink.participant_id.isnot(None),
                User.profile_private.is_(False),
            )
            .all()
        )
        return [row[0] for row in rows]
    if key.startswith(PARTICIPANT_KEY_PREFIX):
        return [UUID(key[len(PARTICIPANT_KEY_PREFIX):])]
    return [UUID(key)]


def list_co_runner_meetings(
    db: Session,
    user_id: UUID,
    key: str,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]]:
    """Per-event details of intersections between the user and one person."""
    participant_ids = _resolve_key_participant_ids(db, key)
    if not participant_ids:
        return []

    user_results, _, canonical_map = _user_results_by_canonical_event(
        db, user_id, include_test_events=include_test_events
    )
    if not user_results:
        return []
    user_event_ids = set(user_results.keys()) | set(canonical_map.keys())

    their_rows = (
        db.query(RunResult, Event, Location, Platform.code)
        .join(Event, RunResult.event_id == Event.id)
        .join(Location, Event.location_id == Location.id)
        .join(Platform, Event.platform_id == Platform.id)
        .filter(
            RunResult.participant_id.in_(participant_ids),
            RunResult.event_id.in_(user_event_ids),
        )
        .all()
    )

    catalog_index = LocationCatalogIndex(db)
    by_canonical: dict[UUID, dict[str, object]] = {}
    # Приоритет строк на канонический event: событие-первоисточник (event.id ==
    # canonical) важнее кросслинкнутого дубля (напр. RunPark), а среди равных по
    # этому критерию — строка с известным временем финиша.
    best_rank: dict[UUID, tuple[int, int]] = {}
    for run, event, location, platform_code in their_rows:
        canonical = canonical_map.get(event.id, event.id)
        rank = (0 if event.id == canonical else 1, 0 if run.finish_time_sec is not None else 1)
        current_best = best_rank.get(canonical)
        if current_best is not None and current_best <= rank:
            continue
        best_rank[canonical] = rank
        my_time, my_position = user_results.get(canonical, (None, None))
        by_canonical[canonical] = {
            "event_date": event.event_date,
            "platform_code": platform_code,
            "location_name": catalog_index.display_name(location, platform_code),
            "my_time_sec": my_time,
            "their_time_sec": run.finish_time_sec,
            "my_position": my_position,
            "their_position": run.position,
            "event_url": resolve_activity_url(
                platform_code=platform_code,
                event_date=event.event_date,
                event_number=event.event_number,
                event_source_url=event.source_url,
                location_external_key=location.external_key,
                profile_url=None,
            ),
        }
    items = list(by_canonical.values())
    items.sort(key=lambda item: item["event_date"], reverse=True)  # type: ignore[arg-type,return-value]
    return items
