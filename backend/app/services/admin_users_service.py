from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import AuthIdentity, AuthProvider, DashboardCache, Participant, Platform, PlatformLink, User
from app.services.dashboard_service import get_dashboard_payload, list_user_runs, list_user_volunteering


def _stats_from_cache(cache: DashboardCache | None) -> tuple[int | None, int | None]:
    if cache is None or not cache.stats:
        return None, None
    total_runs = cache.stats.get("total_runs")
    total_volunteering = cache.stats.get("total_volunteering")
    return (
        int(total_runs) if total_runs is not None else None,
        int(total_volunteering) if total_volunteering is not None else None,
    )


def _link_brief(link: PlatformLink, platform: Platform, participant: Participant | None) -> dict[str, object]:
    display_name = participant.display_name if participant else None
    return {
        "platform_code": platform.code,
        "external_user_id": link.external_user_id,
        "external_url": link.external_url,
        "display_name": display_name,
        "sync_status": link.sync_status.value,
    }


def _load_user_links(db: Session, user_ids: list[UUID]) -> dict[UUID, list[dict[str, object]]]:
    if not user_ids:
        return {}
    rows = (
        db.query(PlatformLink, Platform, Participant)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .outerjoin(Participant, PlatformLink.participant_id == Participant.id)
        .filter(PlatformLink.user_id.in_(user_ids))
        .order_by(PlatformLink.linked_at.desc())
        .all()
    )
    grouped: dict[UUID, list[dict[str, object]]] = {user_id: [] for user_id in user_ids}
    for link, platform, participant in rows:
        grouped[link.user_id].append(_link_brief(link, platform, participant))
    return grouped


def search_admin_users(
    db: Session,
    *,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, object]], int]:
    base = db.query(User)
    normalized = (query or "").strip()
    if normalized:
        like = f"%{normalized}%"
        link_user_ids = select(PlatformLink.user_id).where(PlatformLink.external_user_id.ilike(like)).distinct()
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
                User.id.in_(identity_user_ids),
            )
        )

    total = base.with_entities(func.count(User.id)).scalar() or 0
    users = base.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    user_ids = [user.id for user in users]

    caches = {
        row.user_id: row
        for row in db.query(DashboardCache).filter(DashboardCache.user_id.in_(user_ids)).all()
    }
    links_by_user = _load_user_links(db, user_ids)
    auth_by_user = _load_user_auth_logins(db, user_ids)

    items: list[dict[str, object]] = []
    for user in users:
        total_runs, total_volunteering = _stats_from_cache(caches.get(user.id))
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
                "platform_links": links_by_user.get(user.id, []),
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
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_runs(db, user_id, limit=limit, offset=offset, include_test_events=False)


def get_admin_user_preview_volunteering(
    db: Session,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, object]] | None:
    if get_admin_user(db, user_id) is None:
        return None
    return list_user_volunteering(db, user_id, limit=limit, offset=offset, include_test_events=False)
