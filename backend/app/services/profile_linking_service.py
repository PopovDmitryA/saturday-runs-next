from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
from app.platform_adapters.canonical import ProfilePreview
from app.platform_adapters.five_verst.parser import ProfileNotFoundError as FvProfileNotFoundError
from app.platform_adapters.five_verst.parser import ProfileParseError as FvProfileParseError
from app.platform_adapters.s95.parser import ProfileNotFoundError as S95ProfileNotFoundError
from app.platform_adapters.s95.parser import ProfileParseError as S95ProfileParseError
from app.platform_adapters.parkrun.parser import ProfileNotFoundError as ParkrunProfileNotFoundError
from app.platform_adapters.parkrun.parser import ProfileParseError as ParkrunProfileParseError
from app.platform_adapters.parkrun.url import InvalidProfileUrlError as ParkrunInvalidProfileUrlError
from app.platform_adapters.five_verst.url import InvalidProfileUrlError
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter
from app.services.participant_profile_service import get_participant_data_freshness, try_profile_preview_from_db
from app.services.profile_fetch_pending_service import is_fetch_cooldown_error, raise_or_enqueue_fetch_error
from app.services.profile_preview_cache import get_cached_profile_preview, store_profile_preview


class ProfileLinkingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _raise_profile_linking_for_fetch_error(exc: Exception) -> None:
    from app.s95.errors import S95BanDetected, S95FetchTimeout

    if isinstance(exc, S95BanDetected):
        raise ProfileLinkingError(
            "С95 временно недоступен — сработала защита от частых запросов. Попробуйте позже.",
            503,
        ) from exc
    if isinstance(exc, S95FetchTimeout):
        raise ProfileLinkingError(
            "Сейчас идёт другой запрос к С95 — подождите пару минут и повторите предпросмотр.",
            503,
        ) from exc
    raise exc


def _get_active_platform(db: Session, platform_code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        raise ProfileLinkingError("Платформа не найдена", 404)
    if not platform.is_active:
        raise ProfileLinkingError("Платформа пока недоступна для привязки", 400)
    return platform


def _require_personal_data_consent(user: User) -> None:
    if not user.consent_accepted:
        raise ProfileLinkingError(
            "Сначала примите условия обработки персональных данных при входе через Telegram-бота "
            "(кнопка «Принимаю»). После этого можно привязывать профили.",
            403,
        )


def preview_profile_link(
    db: Session,
    platform_code: str,
    profile_url: str,
    *,
    user: User | None = None,
) -> ProfilePreview:
    if user is not None:
        _require_personal_data_consent(user)
    ensure_adapters_registered()
    _get_active_platform(db, platform_code)

    adapter = get_adapter(platform_code)
    if not adapter.capabilities.fetch_profile_preview:
        raise ProfileLinkingError("Предпросмотр профиля для этой платформы недоступен", 501)

    db_preview = try_profile_preview_from_db(db, platform_code, profile_url)
    if db_preview is not None:
        if user is not None and platform_code in ("five_verst", "parkrun", "s95"):
            store_profile_preview(user.id, platform_code, profile_url, db_preview)
        return db_preview

    try:
        preview = adapter.fetch_profile_preview(profile_url)
        if user is not None and platform_code in ("five_verst", "parkrun", "s95"):
            store_profile_preview(user.id, platform_code, profile_url, preview)
        return preview
    except (InvalidProfileUrlError, ParkrunInvalidProfileUrlError) as exc:
        raise ProfileLinkingError(str(exc), 400) from exc
    except (FvProfileNotFoundError, S95ProfileNotFoundError, ParkrunProfileNotFoundError) as exc:
        raise ProfileLinkingError(str(exc), 404) from exc
    except (FvProfileParseError, S95ProfileParseError, ParkrunProfileParseError) as exc:
        if user is not None and is_fetch_cooldown_error(exc):
            raise_or_enqueue_fetch_error(
                db,
                platform_code=platform_code,
                profile_input=profile_url,
                user=user,
                exc=exc.__cause__ or exc,
            )
        raise ProfileLinkingError(str(exc), 422) from exc
    except ProfileLinkingError:
        raise
    except Exception as exc:
        if user is not None:
            raise_or_enqueue_fetch_error(
                db,
                platform_code=platform_code,
                profile_input=profile_url,
                user=user,
                exc=exc,
            )
        _raise_profile_linking_for_fetch_error(exc)


def _upsert_participant(
    db: Session,
    platform: Platform,
    preview: ProfilePreview,
    parser_version: str,
) -> Participant:
    participant = (
        db.query(Participant)
        .filter(
            Participant.platform_id == platform.id,
            Participant.external_user_id == preview.external_user_id,
        )
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    preserve_fetched_at = preview.data_source == "database"
    if participant is None:
        participant = Participant(
            platform_id=platform.id,
            external_user_id=preview.external_user_id,
            display_name=preview.display_name,
            profile_url=preview.profile_url,
            club_name=preview.club_name,
            barcode_id=preview.barcode_id,
            planning_location=preview.planning_location,
            planning_location_seen_at=(
                preview.planning_location_seen_at
                if preserve_fetched_at and preview.planning_location
                else (now if preview.planning_location else None)
            ),
            age_category=preview.age_category,
            source_url=preview.profile_url,
            parser_version=parser_version,
            fetched_at=preview.data_updated_at if preserve_fetched_at else now,
        )
        db.add(participant)
    else:
        participant.display_name = preview.display_name
        participant.profile_url = preview.profile_url
        participant.club_name = preview.club_name
        participant.barcode_id = preview.barcode_id
        participant.planning_location = preview.planning_location
        if preview.planning_location:
            participant.planning_location_seen_at = (
                preview.planning_location_seen_at if preserve_fetched_at else now
            )
        else:
            participant.planning_location_seen_at = None
        if preview.age_category is not None:
            participant.age_category = preview.age_category
        participant.source_url = preview.profile_url
        participant.parser_version = parser_version
        if not preserve_fetched_at:
            participant.fetched_at = now

    db.flush()
    return participant


def _user_has_platform_link(db: Session, user: User, platform_code: str) -> bool:
    return (
        db.query(PlatformLink)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user.id, Platform.code == platform_code)
        .count()
        > 0
    )


def preview_s95_profile_link(
    db: Session,
    profile_url: str,
    *,
    user: User,
) -> tuple[ProfilePreview, ProfilePreview | None]:
    from app.s95.fetch.browser import shutdown_browser as shutdown_s95_browser

    s95_preview = preview_profile_link(db, "s95", profile_url, user=user)
    parkrun_match: ProfilePreview | None = None
    if (
        s95_preview.parkrun_eligible
        and s95_preview.barcode_id
        and not _user_has_platform_link(db, user, "parkrun")
    ):
        shutdown_s95_browser()
        try:
            parkrun_match = preview_profile_link(
                db,
                "parkrun",
                s95_preview.barcode_id.strip(),
                user=user,
            )
        except ProfileLinkingError:
            parkrun_match = None
    return s95_preview, parkrun_match


def confirm_s95_profile_link(
    db: Session,
    user: User,
    profile_url: str,
    *,
    link_parkrun: bool = False,
) -> tuple[PlatformLink, PlatformLink | None]:
    s95_link = confirm_profile_link(db, user, "s95", profile_url)
    if not link_parkrun:
        return s95_link, None

    if _user_has_platform_link(db, user, "parkrun"):
        return s95_link, None

    s95_preview = get_cached_profile_preview(user.id, "s95", profile_url)
    if s95_preview is None or not s95_preview.barcode_id:
        raise ProfileLinkingError(
            "Сначала нажмите «Предпросмотр» (данные действуют 15 минут), затем подтвердите привязку.",
            400,
        )

    parkrun_input = s95_preview.barcode_id.strip()
    if get_cached_profile_preview(user.id, "parkrun", parkrun_input) is None:
        raise ProfileLinkingError(
            "Данные предпросмотра parkrun устарели или профиль не найден — повторите предпросмотр.",
            400,
        )

    try:
        parkrun_link = confirm_profile_link(db, user, "parkrun", parkrun_input)
    except ProfileLinkingError as exc:
        if exc.status_code == 409:
            return s95_link, None
        raise
    return s95_link, parkrun_link


def confirm_profile_link(db: Session, user: User, platform_code: str, profile_url: str) -> PlatformLink:
    _require_personal_data_consent(user)
    ensure_adapters_registered()
    platform = _get_active_platform(db, platform_code)
    adapter = get_adapter(platform_code)

    existing_user_link = (
        db.query(PlatformLink)
        .filter(
            PlatformLink.user_id == user.id,
            PlatformLink.platform_id == platform.id,
        )
        .one_or_none()
    )
    if existing_user_link is not None:
        raise ProfileLinkingError("Профиль на этой платформе уже привязан к вашему аккаунту", 409)

    try:
        preview = None
        if platform_code in ("five_verst", "parkrun", "s95"):
            preview = get_cached_profile_preview(user.id, platform_code, profile_url)
            if preview is None:
                raise ProfileLinkingError(
                    "Сначала нажмите «Предпросмотр» (данные действуют 15 минут), затем подтвердите привязку.",
                    400,
                )
        else:
            preview = adapter.fetch_profile_preview(profile_url)
    except (InvalidProfileUrlError, ParkrunInvalidProfileUrlError) as exc:
        raise ProfileLinkingError(str(exc), 400) from exc
    except (FvProfileNotFoundError, S95ProfileNotFoundError, ParkrunProfileNotFoundError) as exc:
        raise ProfileLinkingError(str(exc), 404) from exc
    except (FvProfileParseError, S95ProfileParseError, ParkrunProfileParseError) as exc:
        raise ProfileLinkingError(str(exc), 422) from exc

    existing_external_link = (
        db.query(PlatformLink)
        .filter(
            PlatformLink.platform_id == platform.id,
            PlatformLink.external_user_id == preview.external_user_id,
        )
        .one_or_none()
    )
    if existing_external_link is not None and existing_external_link.user_id != user.id:
        raise ProfileLinkingError("Этот профиль уже привязан к другому аккаунту", 409)

    participant = _upsert_participant(db, platform, preview, adapter.parser_version)

    if existing_external_link is not None:
        link = existing_external_link
        link.participant_id = participant.id
        link.external_url = preview.profile_url
    else:
        link = PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=preview.external_user_id,
            external_url=preview.profile_url,
        )
        db.add(link)

    db.commit()
    db.refresh(link)

    from app.models import SyncJobTrigger
    from app.services.dashboard_service import create_sync_job
    from app.services.sync_trigger_service import (
        enqueue_parkrun_user_sync,
        enqueue_s95_user_sync,
        enqueue_user_sync,
    )

    job = create_sync_job(db, user.id, SyncJobTrigger.linking, platform_link_id=link.id)
    db.commit()
    if platform_code == "s95":
        enqueue_s95_user_sync(user.id, SyncJobTrigger.linking, job_id=job.id, platform_link_id=link.id)
    elif platform_code == "parkrun":
        enqueue_parkrun_user_sync(user.id, SyncJobTrigger.linking, job_id=job.id, platform_link_id=link.id)
    else:
        enqueue_user_sync(user.id, SyncJobTrigger.linking, job_id=job.id)

    return link


def list_user_profile_links(db: Session, user: User) -> list[dict[str, object]]:
    links = (
        db.query(PlatformLink, Platform, Participant)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .outerjoin(Participant, PlatformLink.participant_id == Participant.id)
        .filter(PlatformLink.user_id == user.id)
        .order_by(PlatformLink.linked_at.desc())
        .all()
    )
    items: list[dict[str, object]] = []
    for link, platform, participant in links:
        data_updated_at = None
        data_through_date = None
        if participant is not None:
            freshness = get_participant_data_freshness(
                db,
                participant,
                platform_code=platform.code,
                platform_id=platform.id,
            )
            data_updated_at = freshness.data_updated_at
            data_through_date = freshness.data_through_date
        items.append(
            {
                "id": link.id,
                "platform_code": platform.code,
                "platform_name": platform.name,
                "external_user_id": link.external_user_id,
                "external_url": link.external_url,
                "display_name": participant.display_name if participant else None,
                "sync_status": link.sync_status.value,
                "last_user_sync_at": link.last_user_sync_at,
                "data_updated_at": data_updated_at,
                "data_through_date": data_through_date,
            }
        )
    return items
