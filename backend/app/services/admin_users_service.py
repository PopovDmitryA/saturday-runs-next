from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AuthIdentity,
    AuthProvider,
    Event,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services.dashboard_service import (
    get_dashboard_payload,
    list_user_best_results,
    list_user_personal_records,
    list_user_runs,
    list_user_volunteer_role_stats,
    list_user_volunteering,
)


def _link_brief(
    link: PlatformLink,
    platform: Platform,
    participant: Participant | None,
    counts: dict[UUID, tuple[int, int]],
) -> dict[str, object]:
    display_name = participant.display_name if participant else None
    run_count, volunteer_count = counts.get(participant.id, (0, 0)) if participant else (0, 0)
    barcode_id = participant.barcode_id if participant else None
    return {
        "platform_code": platform.code,
        "external_user_id": link.external_user_id,
        "external_url": link.external_url,
        "display_name": display_name,
        "sync_status": link.sync_status.value,
        "run_count": run_count,
        "volunteer_count": volunteer_count,
        "barcode_id": barcode_id,
    }


def _load_participant_counts(
    db: Session, participant_ids: list[UUID]
) -> dict[UUID, tuple[int, int]]:
    """Non-test run and volunteer counts per participant (matches profile totals)."""
    if not participant_ids:
        return {}

    def _counts(result_model: type[RunResult] | type[VolunteerResult]) -> dict[UUID, int]:
        out: dict[UUID, int] = {}
        rows = (
            db.query(result_model.participant_id, func.count(result_model.id))
            .join(Event, Event.id == result_model.event_id)
            .filter(
                result_model.participant_id.in_(participant_ids),
                Event.is_test_event.is_(False),
            )
            .group_by(result_model.participant_id)
            .all()
        )
        for participant_id, count in rows:
            if participant_id is not None:
                out[participant_id] = int(count)
        return out

    runs = _counts(RunResult)
    volunteers = _counts(VolunteerResult)
    return {pid: (runs.get(pid, 0), volunteers.get(pid, 0)) for pid in participant_ids}


def _load_user_totals(db: Session, user_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
    """Non-test run and volunteer totals per user, across all linked participants."""
    if not user_ids:
        return {}

    def _counts(result_model: type[RunResult] | type[VolunteerResult]) -> dict[UUID, int]:
        out: dict[UUID, int] = {}
        rows = (
            db.query(PlatformLink.user_id, func.count(result_model.id))
            .join(Participant, _participant_join())
            .join(result_model, result_model.participant_id == Participant.id)
            .join(Event, Event.id == result_model.event_id)
            .filter(PlatformLink.user_id.in_(user_ids), Event.is_test_event.is_(False))
            .group_by(PlatformLink.user_id)
            .all()
        )
        for user_id, count in rows:
            if user_id is not None:
                out[user_id] = int(count)
        return out

    runs = _counts(RunResult)
    volunteers = _counts(VolunteerResult)
    return {uid: (runs.get(uid, 0), volunteers.get(uid, 0)) for uid in user_ids}


def _participant_join() -> Any:
    return and_(
        PlatformLink.platform_id == Participant.platform_id,
        PlatformLink.external_user_id == Participant.external_user_id,
    )


def _load_user_links(db: Session, user_ids: list[UUID]) -> dict[UUID, list[dict[str, object]]]:
    if not user_ids:
        return {}
    rows = (
        db.query(PlatformLink, Platform, Participant)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .outerjoin(Participant, _participant_join())
        .filter(PlatformLink.user_id.in_(user_ids))
        .order_by(PlatformLink.linked_at.desc())
        .all()
    )
    participant_ids = [participant.id for _, _, participant in rows if participant is not None]
    counts = _load_participant_counts(db, participant_ids)
    grouped: dict[UUID, list[dict[str, object]]] = {user_id: [] for user_id in user_ids}
    for link, platform, participant in rows:
        grouped[link.user_id].append(_link_brief(link, platform, participant, counts))
    return grouped


def _auth_login_brief(identity: AuthIdentity) -> dict[str, object]:
    label = identity.email or identity.display_name or identity.external_id
    return {
        "provider": identity.provider.value,
        "label": label,
        "external_id": identity.external_id,
    }


def _load_user_auth_logins(db: Session, user_ids: list[UUID]) -> dict[UUID, list[dict[str, object]]]:
    if not user_ids:
        return {}
    rows = (
        db.query(AuthIdentity)
        .filter(AuthIdentity.user_id.in_(user_ids))
        .order_by(AuthIdentity.last_login_at.desc().nullslast(), AuthIdentity.linked_at.desc())
        .all()
    )
    grouped: dict[UUID, list[dict[str, object]]] = {user_id: [] for user_id in user_ids}
    for identity in rows:
        grouped[identity.user_id].append(_auth_login_brief(identity))
    return grouped


def _user_result_count_subquery(result_model: type[RunResult] | type[VolunteerResult]) -> Any:
    """Per-user non-test result counts, aggregated across all linked participants."""
    return (
        select(
            PlatformLink.user_id.label("user_id"),
            func.count(result_model.id).label("cnt"),
        )
        .join(Participant, _participant_join())
        .join(result_model, result_model.participant_id == Participant.id)
        .join(Event, Event.id == result_model.event_id)
        .where(Event.is_test_event.is_(False))
        .group_by(PlatformLink.user_id)
        .subquery()
    )


SORT_KEYS = {"created", "runs", "volunteering"}


def search_admin_users(
    db: Session,
    *,
    query: str | None,
    limit: int,
    offset: int,
    sort: str = "created",
    direction: str = "desc",
) -> tuple[list[dict[str, object]], int]:
    sort = sort if sort in SORT_KEYS else "created"
    descending = direction != "asc"
    base = db.query(User)
    normalized = (query or "").strip()
    if normalized:
        like = f"%{normalized}%"
        link_user_ids = select(PlatformLink.user_id).where(PlatformLink.external_user_id.ilike(like)).distinct()
        participant_user_ids = (
            select(PlatformLink.user_id)
            .join(
                Participant,
                _participant_join(),
            )
            .where(Participant.display_name.ilike(like))
            .distinct()
        )
        identity_user_ids = (
            select(AuthIdentity.user_id)
            .where(
                or_(
                    AuthIdentity.email.ilike(like),
                    AuthIdentity.display_name.ilike(like),
                    AuthIdentity.external_id.ilike(like),
                )
            )
            .distinct()
        )
        base = base.filter(
            or_(
                User.telegram_username.ilike(like),
                User.display_name.ilike(like),
                cast(User.telegram_id, String).like(like),
                User.id.in_(link_user_ids),
                User.id.in_(participant_user_ids),
                User.id.in_(identity_user_ids),
            )
        )

    total = base.with_entities(func.count(User.id)).scalar() or 0

    runs_sq = _user_result_count_subquery(RunResult)
    vol_sq = _user_result_count_subquery(VolunteerResult)
    ordered = base.outerjoin(runs_sq, runs_sq.c.user_id == User.id).outerjoin(
        vol_sq, vol_sq.c.user_id == User.id
    )
    sort_col: Any
    if sort == "runs":
        sort_col = func.coalesce(runs_sq.c.cnt, 0)
    elif sort == "volunteering":
        sort_col = func.coalesce(vol_sq.c.cnt, 0)
    else:
        sort_col = User.created_at
    order_clause = sort_col.desc() if descending else sort_col.asc()

    users = (
        ordered.order_by(order_clause, User.created_at.desc(), User.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    user_ids = [user.id for user in users]

    links_by_user = _load_user_links(db, user_ids)
    auth_by_user = _load_user_auth_logins(db, user_ids)
    totals_by_user = _load_user_totals(db, user_ids)

    items: list[dict[str, object]] = []
    for user in users:
        links = links_by_user.get(user.id, [])
        total_runs, total_volunteering = totals_by_user.get(user.id, (0, 0))
        auth_logins = list(auth_by_user.get(user.id, []))
        if user.telegram_id is not None and not any(item["provider"] == AuthProvider.telegram.value for item in auth_logins):
            auth_logins.insert(
                0,
                {
                    "provider": AuthProvider.telegram.value,
                    "label": user.telegram_username or user.display_name or str(user.telegram_id),
                    "external_id": str(user.telegram_id),
                },
            )
        items.append(
            {
                "id": str(user.id),
                "serial_id": user.serial_id,
                "telegram_id": user.telegram_id,
                "telegram_username": user.telegram_username,
                "display_name": user.display_name,
                "auth_logins": auth_logins,
                "news_subscribed": user.news_subscribed,
                "consent_accepted": user.consent_accepted,
                "created_at": user.created_at,
                "last_login_at": user.last_login_at,
                "total_runs": total_runs,
                "total_volunteering": total_volunteering,
                "platform_links": links,
            }
        )
    return items, int(total)


def get_admin_user(db: Session, user_id: UUID) -> User | None:
    return db.query(User).filter(User.id == user_id).one_or_none()


def get_admin_user_preview_dashboard(db: Session, user_id: UUID) -> dict[str, object] | None:
    user = get_admin_user(db, user_id)
    if user is None:
        return None
    payload = get_dashboard_payload(db, user)
    links = _load_user_links(db, [user.id]).get(user.id, [])
    auth_logins = list(_load_user_auth_logins(db, [user.id]).get(user.id, []))
    if user.telegram_id is not None and not any(
        item["provider"] == AuthProvider.telegram.value for item in auth_logins
    ):
        auth_logins.insert(
            0,
            {
                "provider": AuthProvider.telegram.value,
                "label": user.telegram_username or user.display_name or str(user.telegram_id),
                "external_id": str(user.telegram_id),
            },
        )
    return {
        "user": {
            "id": str(user.id),
            "telegram_id": user.telegram_id,
            "telegram_username": user.telegram_username,
            "display_name": user.display_name,
            "news_subscribed": user.news_subscribed,
            "auth_logins": auth_logins,
        },
        "stats": payload["stats"],
        "computed_at": payload["computed_at"],
        "platform_links": links,
    }


def get_admin_user_preview_runs(
    db: Session,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
    include_test: bool = False,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_runs(db, user_id, limit=limit, offset=offset, include_test_events=include_test)


def get_admin_user_preview_volunteering(
    db: Session,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
    include_test: bool = False,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_volunteering(db, user_id, limit=limit, offset=offset, include_test_events=include_test)


def get_admin_user_preview_best_results(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_best_results(db, user_id, include_test_events=include_test_events)


def get_admin_user_preview_personal_records(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_personal_records(db, user_id, include_test_events=include_test_events)


def get_admin_user_preview_volunteer_role_stats(
    db: Session,
    user_id: UUID,
    *,
    include_test_events: bool = False,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_volunteer_role_stats(db, user_id, include_test_events=include_test_events)
