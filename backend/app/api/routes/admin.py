from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.config import get_settings
from app.db.session import get_db
from app.models import SyncJobTrigger, User
from app.schemas.abuse_admin import (
    AbuseBanCreateRequest,
    AbuseBanCreateResponse,
    AbuseBlockListResponse,
    AbuseIpBlockItem,
    AbuseMessageResponse,
    AbuseTelegramBanItem,
)
from app.schemas.admin import AdminLoginEventItem, AdminLoginEventsResponse, AdminUserListResponse
from app.schemas.admin_event_report import (
    EventReportDatesResponse,
    EventReportLocationsResponse,
    EventReportResponse,
)
from app.schemas.admin_stats import AdminSiteStatsResponse, PageAnalyticsResponse
from app.schemas.admin_sync_runs import AdminSyncRunsResponse
from app.schemas.backlog import (
    BacklogCardAdminListResponse,
    BacklogCardAdminResponse,
    BacklogCardUpdateRequest,
    BacklogVoteAdminListResponse,
)
from app.schemas.blocked_slug_admin import (
    BlockedSlugCreateRequest,
    BlockedSlugItem,
    BlockedSlugListResponse,
)
from app.schemas.blog import (
    BlogPostAdminListResponse,
    BlogPostAdminResponse,
    BlogPostCreateRequest,
    BlogPostUpdateRequest,
)
from app.schemas.dashboard import SyncRefreshResponse
from app.schemas.location_contacts import (
    LocationAnnounceSettingsResponse,
    LocationAnnounceSettingsUpdateRequest,
    LocationContactItem,
    LocationContactLink,
    LocationContactLinkCreateRequest,
    LocationContactLinkUpdateRequest,
    LocationContactListResponse,
)
from app.schemas.rating import (
    AdminLocationRatingsResponse,
    AdminRatingsResponse,
)
from app.schemas.records_digest import DigestDatesResponse, RecordsDigestResponse
from app.services.abuse_admin_service import (
    AbuseAdminError,
    clear_ip_score,
    create_abuse_ban,
    delete_ip_block,
    delete_telegram_ban,
    get_ip_block_details,
    list_abuse_blocks,
)
from app.services.admin_event_report_service import (
    build_event_report,
    list_report_event_dates,
    list_report_locations,
)
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.admin_users_service import get_admin_user, search_admin_users
from app.services.backlog_service import (
    BacklogError,
    list_card_votes_admin,
    list_cards_admin,
    update_card_admin,
)
from app.services.backlog_service import (
    delete_card as delete_backlog_card,
)
from app.services.backlog_service import (
    delete_comment as delete_backlog_comment,
)
from app.services.blocked_slug_admin_service import (
    BlockedSlugError,
    create_blocked_slug,
    delete_blocked_slug,
    list_blocked_slugs,
    list_reserved_slugs,
)
from app.services.blog_service import (
    BlogPostError,
    create_post,
    delete_post,
    list_all_posts,
    update_post,
)
from app.services.location_contacts_service import (
    LocationContactError,
    create_location_contact_link,
    delete_location_contact_link,
    list_location_contacts,
    update_location_announce_settings,
    update_location_contact_link,
)
from app.services.login_journal_service import list_login_events, summarize_login_events
from app.services.page_analytics_service import build_home_ab_stats, build_page_analytics, resolve_period
from app.services.rating_service import (
    list_all_ratings,
    location_rating_aggregates,
    ratings_stats,
)
from app.services.records_digest_service import build_records_digest, list_digest_dates
from app.services.scheduled_run_log_service import list_runs as list_scheduled_runs
from app.services.scheduled_run_log_service import resolve_period as resolve_runs_period
from app.services.scheduled_run_log_service import summarize as summarize_scheduled_runs
from app.services.sync_enqueue_service import enqueue_manual_platform_sync, enqueue_sync_for_all_platforms

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_SUPPORTED_SYNC_PLATFORMS = frozenset({"five_verst", "s95", "parkrun"})
ADMIN_SYNC_QUEUED_MESSAGE = (
    "Запрос на обновление отправлен. Ожидайте исполнения в ближайшее время."
)
ADMIN_SYNC_ALREADY_QUEUED_MESSAGE = (
    "Обновление уже в очереди. Ожидайте исполнения в ближайшее время."
)


def _admin_sync_refresh_response(result) -> SyncRefreshResponse:
    return SyncRefreshResponse(
        job_id=result.job_id,
        status="already_queued" if result.duplicate else "queued",
        message=ADMIN_SYNC_ALREADY_QUEUED_MESSAGE if result.duplicate else ADMIN_SYNC_QUEUED_MESSAGE,
    )


def _handle_abuse_admin_error(exc: AbuseAdminError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/users", response_model=AdminUserListResponse)
def list_admin_users(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query(pattern="^(created|runs|volunteering|profile)$")] = "created",
    direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> AdminUserListResponse:
    items, total = search_admin_users(
        db, query=q, limit=limit, offset=offset, sort=sort, direction=direction
    )
    return AdminUserListResponse(
        items=items,  # type: ignore[arg-type]
        total=total,
        limit=limit,
        offset=offset,
        query=q,
    )


@router.get("/users/{user_id}/login-events", response_model=AdminLoginEventsResponse)
def admin_user_login_events(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AdminLoginEventsResponse:
    if get_admin_user(db, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")

    events = list_login_events(db, user_id, limit=limit)
    summary = summarize_login_events(events)
    return AdminLoginEventsResponse(
        items=[AdminLoginEventItem.model_validate(event, from_attributes=True) for event in events],
        logins=int(summary["logins"]),  # type: ignore[arg-type]
        logouts=int(summary["logouts"]),  # type: ignore[arg-type]
        devices=int(summary["devices"]),  # type: ignore[arg-type]
        unexpected_relogins=int(summary["unexpected_relogins"]),  # type: ignore[arg-type]
    )


@router.post(
    "/users/{user_id}/sync",
    response_model=SyncRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def admin_user_sync_all(
    user_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> SyncRefreshResponse:
    user = get_admin_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = enqueue_sync_for_all_platforms(db, user.id, SyncJobTrigger.manual, mark_syncing=True)
    db.commit()
    return _admin_sync_refresh_response(result)


@router.post(
    "/users/{user_id}/sync/{platform_code}",
    response_model=SyncRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def admin_user_sync_platform(
    user_id: UUID,
    platform_code: str,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> SyncRefreshResponse:
    if platform_code not in ADMIN_SUPPORTED_SYNC_PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown platform")
    user = get_admin_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        result = enqueue_manual_platform_sync(db, user, platform_code)
    except ValueError:
        raise HTTPException(status_code=404, detail="Platform link not found") from None
    db.commit()
    return _admin_sync_refresh_response(result)


@router.get("/abuse/blocks", response_model=AbuseBlockListResponse)
def admin_list_abuse_blocks(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseBlockListResponse:
    payload = list_abuse_blocks(db)
    return AbuseBlockListResponse(
        ip_blocks=[AbuseIpBlockItem.model_validate(item) for item in payload["ip_blocks"]],
        telegram_bans=[AbuseTelegramBanItem.model_validate(item) for item in payload["telegram_bans"]],
    )


@router.post("/abuse/blocks", response_model=AbuseBanCreateResponse)
def admin_create_abuse_ban(
    body: AbuseBanCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseBanCreateResponse:
    try:
        payload = create_abuse_ban(
            db,
            admin,
            get_settings(),
            target=body.target,
            duration_seconds=body.duration_seconds,
            reason=body.reason,
            ban_ip=body.ban_ip,
            ban_account=body.ban_account,
        )
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseBanCreateResponse.model_validate(payload)


@router.delete("/abuse/blocks/ip/{ip}", response_model=AbuseMessageResponse)
def admin_delete_ip_block(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_ip_block(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="ip_unblocked")


@router.delete("/abuse/blocks/telegram/{telegram_id}", response_model=AbuseMessageResponse)
def admin_delete_telegram_ban(
    telegram_id: int,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_telegram_ban(telegram_id, get_settings())
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="telegram_unblocked")


@router.post("/abuse/blocks/ip/{ip}/clear-score", response_model=AbuseMessageResponse)
def admin_clear_ip_abuse_score(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        clear_ip_score(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseMessageResponse(message="score_cleared")


@router.get("/abuse/blocks/ip/{ip}", response_model=AbuseIpBlockItem)
def admin_get_ip_block(
    ip: str,
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseIpBlockItem:
    try:
        payload = get_ip_block_details(ip)
    except AbuseAdminError as exc:
        raise _handle_abuse_admin_error(exc) from exc
    return AbuseIpBlockItem.model_validate(payload)


@router.get("/profile-slugs/blocked", response_model=BlockedSlugListResponse)
def admin_list_blocked_slugs(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BlockedSlugListResponse:
    items = list_blocked_slugs(db)
    return BlockedSlugListResponse(
        items=[
            BlockedSlugItem(
                id=str(item.id),
                slug=item.slug,
                comment=item.comment,
                created_at=item.created_at,
            )
            for item in items
        ],
        system_slugs=list_reserved_slugs(),
    )


@router.post("/profile-slugs/blocked", response_model=BlockedSlugItem, status_code=status.HTTP_201_CREATED)
def admin_create_blocked_slug(
    body: BlockedSlugCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> BlockedSlugItem:
    try:
        entry = create_blocked_slug(db, admin, raw_slug=body.slug, comment=body.comment)
    except BlockedSlugError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BlockedSlugItem(
        id=str(entry.id),
        slug=entry.slug,
        comment=entry.comment,
        created_at=entry.created_at,
    )


@router.delete("/profile-slugs/blocked/{entry_id}", response_model=AbuseMessageResponse)
def admin_delete_blocked_slug(
    entry_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_blocked_slug(db, entry_id)
    except BlockedSlugError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return AbuseMessageResponse(message="slug_unblocked")


@router.get("/stats", response_model=AdminSiteStatsResponse)
def admin_site_stats(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    period_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AdminSiteStatsResponse:
    payload = get_admin_site_stats(db, period_days=period_days)
    return AdminSiteStatsResponse.model_validate(payload)


@router.get("/page-analytics", response_model=PageAnalyticsResponse)
def admin_page_analytics(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    period_days: Annotated[int | None, Query(ge=1, le=1830)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> PageAnalyticsResponse:
    """Популярность разделов: либо последние period_days, либо явный диапазон.

    Явные даты приоритетнее period_days; без параметров — 30 дней.
    """
    start, end = resolve_period(period_days=period_days, date_from=date_from, date_to=date_to)
    payload = build_page_analytics(db, start=start, end=end)
    payload["home_ab"] = build_home_ab_stats(db, start=start, end=end)
    payload["generated_at"] = datetime.now(timezone.utc)
    return PageAnalyticsResponse.model_validate(payload)


@router.get("/sync-runs", response_model=AdminSyncRunsResponse)
def admin_sync_runs(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    period_days: Annotated[int | None, Query(ge=1, le=120)] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    platform: Annotated[str | None, Query(pattern="^(five_verst|s95|parkrun|runpark|other)$")] = None,
    run_status: Annotated[str | None, Query(pattern="^(ok|error|failed|skipped|problems)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminSyncRunsResponse:
    """История запусков автообновления: итоги по платформам + лента запусков.

    Итоги считаются по всему периоду, лента — с учётом фильтров и пагинации.
    """
    start, end = resolve_runs_period(period_days=period_days, date_from=date_from, date_to=date_to)
    runs, total = list_scheduled_runs(
        db,
        start=start,
        end=end,
        platform=platform,
        status=run_status,
        limit=limit,
        offset=offset,
    )
    return AdminSyncRunsResponse.model_validate(
        {
            "generated_at": datetime.now(timezone.utc),
            "date_from": start,
            "date_to": end,
            "platforms": summarize_scheduled_runs(db, start=start, end=end),
            "runs": runs,
            "total": total,
        }
    )


@router.get("/ratings", response_model=AdminRatingsResponse)
def admin_ratings(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AdminRatingsResponse:
    return AdminRatingsResponse.model_validate(
        {"ratings": list_all_ratings(db), "stats": ratings_stats(db)}
    )


@router.get("/ratings/locations", response_model=AdminLocationRatingsResponse)
def admin_ratings_locations(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    exclude_locals: Annotated[bool, Query()] = False,
) -> AdminLocationRatingsResponse:
    return AdminLocationRatingsResponse.model_validate(
        location_rating_aggregates(db, exclude_locals=exclude_locals)
    )


@router.get("/event-report/locations", response_model=EventReportLocationsResponse)
def admin_event_report_locations(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> EventReportLocationsResponse:
    return EventReportLocationsResponse.model_validate({"items": list_report_locations(db)})


@router.get("/event-report/dates", response_model=EventReportDatesResponse)
def admin_event_report_dates(
    location_id: Annotated[UUID, Query()],
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> EventReportDatesResponse:
    return EventReportDatesResponse.model_validate(
        {"items": list_report_event_dates(db, location_id)}
    )


@router.get("/event-report", response_model=EventReportResponse)
def admin_event_report(
    event_id: Annotated[UUID, Query()],
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> EventReportResponse:
    payload = build_event_report(db, event_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return EventReportResponse.model_validate(payload)


@router.get("/records-digest/dates", response_model=DigestDatesResponse)
def admin_records_digest_dates(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    limit: Annotated[int, Query(ge=1, le=60)] = 20,
) -> DigestDatesResponse:
    return DigestDatesResponse.model_validate({"items": list_digest_dates(db, limit=limit)})


@router.get("/records-digest", response_model=RecordsDigestResponse)
def admin_records_digest(
    event_date: Annotated[date, Query()],
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> RecordsDigestResponse:
    return RecordsDigestResponse.model_validate(build_records_digest(db, event_date))


@router.get("/location-contacts", response_model=LocationContactListResponse)
def admin_location_contacts(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    q: Annotated[str | None, Query(max_length=128)] = None,
    only_missing: Annotated[bool, Query()] = False,
    only_do_not_disturb: Annotated[bool, Query()] = False,
) -> LocationContactListResponse:
    items = list_location_contacts(
        db, query=q, only_missing=only_missing, only_do_not_disturb=only_do_not_disturb
    )
    return LocationContactListResponse(
        items=[LocationContactItem.model_validate(item) for item in items],
        total=len(items),
        with_telegram=sum(1 for item in items if item["contacts"]),
        do_not_disturb_total=sum(1 for item in items if item["do_not_disturb"]),
    )


@router.put(
    "/location-contacts/{location_id}/settings", response_model=LocationAnnounceSettingsResponse
)
def admin_update_location_announce_settings(
    location_id: UUID,
    body: LocationAnnounceSettingsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> LocationAnnounceSettingsResponse:
    try:
        payload = update_location_announce_settings(
            db, location_id, do_not_disturb=body.do_not_disturb, comment=body.comment
        )
    except LocationContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return LocationAnnounceSettingsResponse.model_validate(payload)


@router.post(
    "/location-contacts/{location_id}/links",
    response_model=LocationContactLink,
    status_code=status.HTTP_201_CREATED,
)
def admin_create_location_contact_link(
    location_id: UUID,
    body: LocationContactLinkCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> LocationContactLink:
    try:
        payload = create_location_contact_link(
            db, location_id, telegram_url=body.telegram_url, label=body.label
        )
    except LocationContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return LocationContactLink.model_validate(payload)


@router.put("/location-contacts/links/{contact_id}", response_model=LocationContactLink)
def admin_update_location_contact_link(
    contact_id: UUID,
    body: LocationContactLinkUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> LocationContactLink:
    try:
        payload = update_location_contact_link(
            db, contact_id, telegram_url=body.telegram_url, label=body.label
        )
    except LocationContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return LocationContactLink.model_validate(payload)


@router.delete("/location-contacts/links/{contact_id}", response_model=AbuseMessageResponse)
def admin_delete_location_contact_link(
    contact_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_location_contact_link(db, contact_id)
    except LocationContactError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return AbuseMessageResponse(message="contact_link_deleted")




@router.get("/blog/posts", response_model=BlogPostAdminListResponse)
def admin_list_blog_posts(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BlogPostAdminListResponse:
    posts = list_all_posts(db)
    return BlogPostAdminListResponse(
        items=[BlogPostAdminResponse.model_validate(post) for post in posts],
        total=len(posts),
    )


@router.post("/blog/posts", response_model=BlogPostAdminResponse, status_code=status.HTTP_201_CREATED)
def admin_create_blog_post(
    body: BlogPostCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BlogPostAdminResponse:
    try:
        post = create_post(
            db,
            title=body.title,
            teaser=body.teaser,
            telegram_url=body.telegram_url,
            topic=body.topic,
            published_at=body.published_at,
            is_published=body.is_published,
        )
    except BlogPostError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BlogPostAdminResponse.model_validate(post)


@router.put("/blog/posts/{post_id}", response_model=BlogPostAdminResponse)
def admin_update_blog_post(
    post_id: UUID,
    body: BlogPostUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BlogPostAdminResponse:
    try:
        post = update_post(
            db,
            post_id,
            title=body.title,
            teaser=body.teaser,
            telegram_url=body.telegram_url,
            topic=body.topic,
            published_at=body.published_at,
            is_published=body.is_published,
        )
    except BlogPostError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return BlogPostAdminResponse.model_validate(post)


@router.delete("/blog/posts/{post_id}", response_model=AbuseMessageResponse)
def admin_delete_blog_post(
    post_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_post(db, post_id)
    except BlogPostError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return AbuseMessageResponse(message="blog_post_deleted")


@router.get("/backlog/cards", response_model=BacklogCardAdminListResponse)
def admin_list_backlog_cards(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BacklogCardAdminListResponse:
    items = list_cards_admin(db)
    return BacklogCardAdminListResponse(items=items, total=len(items))


@router.get("/backlog/cards/{card_id}/votes", response_model=BacklogVoteAdminListResponse)
def admin_list_backlog_votes(
    card_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> BacklogVoteAdminListResponse:
    try:
        return list_card_votes_admin(db, card_id)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.patch("/backlog/cards/{card_id}", response_model=BacklogCardAdminResponse)
def admin_update_backlog_card(
    card_id: UUID,
    body: BacklogCardUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    admin_user: Annotated[User, Depends(get_current_admin_user)],
) -> BacklogCardAdminResponse:
    # Админ правит любое поле карточки, включая статус (модерация).
    try:
        return update_card_admin(
            db,
            card_id,
            editor=admin_user,
            type_=body.type,
            category=body.category,
            title=body.title,
            description=body.description,
            is_anonymous=body.is_anonymous,
            status=body.status,
        )
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.delete("/backlog/cards/{card_id}", response_model=AbuseMessageResponse)
def admin_delete_backlog_card(
    card_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_backlog_card(db, card_id)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return AbuseMessageResponse(message="backlog_card_deleted")


@router.delete("/backlog/comments/{comment_id}", response_model=AbuseMessageResponse)
def admin_delete_backlog_comment(
    comment_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AbuseMessageResponse:
    try:
        delete_backlog_comment(db, comment_id)
    except BacklogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return AbuseMessageResponse(message="backlog_comment_deleted")
