from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.config import get_settings
from app.db.session import get_db
from app.history_milestone_kinds import MILESTONE_KIND_REGISTRY
from app.models import SyncJobTrigger, User
from app.schemas.abuse_admin import (
    AbuseBanCreateRequest,
    AbuseBanCreateResponse,
    AbuseBlockListResponse,
    AbuseIpBlockItem,
    AbuseMessageResponse,
    AbuseTelegramBanItem,
)
from app.schemas.admin import (
    AdminUserListResponse,
    HistoryMilestoneKindSettingResponse,
    HistoryMilestoneKindSettingsResponse,
    HistoryMilestoneKindUpdateRequest,
)
from app.schemas.admin_event_report import (
    EventReportDatesResponse,
    EventReportLocationsResponse,
    EventReportResponse,
)
from app.schemas.admin_stats import AdminSiteStatsResponse
from app.schemas.blocked_slug_admin import (
    BlockedSlugCreateRequest,
    BlockedSlugItem,
    BlockedSlugListResponse,
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
from app.services.blocked_slug_admin_service import (
    BlockedSlugError,
    create_blocked_slug,
    delete_blocked_slug,
    list_blocked_slugs,
    list_reserved_slugs,
)
from app.services.history_milestone_settings_service import (
    UnknownMilestoneKindError,
    list_milestone_kind_settings,
    set_milestone_kind_enabled,
)
from app.services.location_contacts_service import (
    LocationContactError,
    create_location_contact_link,
    delete_location_contact_link,
    list_location_contacts,
    update_location_announce_settings,
    update_location_contact_link,
)
from app.services.rating_service import (
    list_all_ratings,
    location_rating_aggregates,
    ratings_stats,
)
from app.services.records_digest_service import build_records_digest, list_digest_dates
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


@router.get("/history-milestones", response_model=HistoryMilestoneKindSettingsResponse)
def admin_history_milestones(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> HistoryMilestoneKindSettingsResponse:
    return HistoryMilestoneKindSettingsResponse.model_validate(
        {"kinds": list_milestone_kind_settings(db)}
    )


@router.put("/history-milestones/{kind}", response_model=HistoryMilestoneKindSettingResponse)
def admin_update_history_milestone(
    kind: str,
    payload: HistoryMilestoneKindUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> HistoryMilestoneKindSettingResponse:
    try:
        result = set_milestone_kind_enabled(db, kind, payload.enabled)
    except UnknownMilestoneKindError as exc:
        raise HTTPException(status_code=404, detail="Неизвестный вид вехи") from exc
    info = next(item for item in MILESTONE_KIND_REGISTRY if item.kind == kind)
    return HistoryMilestoneKindSettingResponse.model_validate(
        {**result, "label": info.label, "description": info.description}
    )
