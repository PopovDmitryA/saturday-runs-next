from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.models import (
    Platform,
    PlatformLink,
    ProfileFetchPending,
    ProfileFetchPendingOperation,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    User,
)
from app.parkrun.errors import ParkrunBanDetected
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter
from app.platform_fetch.cooldown import is_platform_in_cooldown, parse_cooldown_until_from_message
from app.s95.errors import S95BanDetected, S95FetchTimeout
from app.services.participant_profile_service import resolve_profile_identity

logger = logging.getLogger(__name__)

_BAN_ERROR_TYPES = (ParkrunBanDetected, S95BanDetected, FiveVerstBanDetected)


def is_fetch_cooldown_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, _BAN_ERROR_TYPES):
            return True
        if isinstance(current, S95FetchTimeout):
            return True
        text = str(current).lower()
        if "cooldown until" in text or "ban/protection" in text:
            return True
        current = current.__cause__
    return False


def _reason_from_error(exc: Exception) -> ProfileFetchPendingReason:
    if isinstance(exc, S95FetchTimeout):
        return ProfileFetchPendingReason.timeout
    text = str(exc).lower()
    if "ban/protection" in text:
        return ProfileFetchPendingReason.ban
    if "cooldown" in text:
        return ProfileFetchPendingReason.cooldown
    return ProfileFetchPendingReason.error


def enqueue_profile_fetch_pending(
    db: Session,
    *,
    platform_code: str,
    profile_input: str,
    user_id: UUID | None,
    operation: ProfileFetchPendingOperation = ProfileFetchPendingOperation.profile_preview,
    exc: Exception,
) -> ProfileFetchPending:
    profile_input = profile_input.strip()
    external_user_id: str | None = None
    canonical_profile_url: str | None = None
    try:
        identity = resolve_profile_identity(platform_code, profile_input)
        external_user_id = identity.external_user_id
        canonical_profile_url = identity.canonical_profile_url
    except Exception:
        pass

    existing: ProfileFetchPending | None = None
    if external_user_id:
        existing = (
            db.query(ProfileFetchPending)
            .filter(
                ProfileFetchPending.platform_code == platform_code,
                ProfileFetchPending.external_user_id == external_user_id,
                ProfileFetchPending.status == ProfileFetchPendingStatus.pending,
            )
            .order_by(ProfileFetchPending.created_at.desc())
            .first()
        )
    if existing is None:
        existing = (
            db.query(ProfileFetchPending)
            .filter(
                ProfileFetchPending.platform_code == platform_code,
                ProfileFetchPending.profile_input == profile_input,
                ProfileFetchPending.status == ProfileFetchPendingStatus.pending,
            )
            .order_by(ProfileFetchPending.created_at.desc())
            .first()
        )

    reason = _reason_from_error(exc)
    cooldown_until = parse_cooldown_until_from_message(str(exc))
    if existing is not None:
        existing.last_error = str(exc)
        existing.reason = reason
        existing.cooldown_until = cooldown_until
        if user_id is not None:
            existing.user_id = user_id
        db.flush()
        return existing

    row = ProfileFetchPending(
        user_id=user_id,
        platform_code=platform_code,
        profile_input=profile_input,
        external_user_id=external_user_id,
        canonical_profile_url=canonical_profile_url,
        operation=operation,
        status=ProfileFetchPendingStatus.pending,
        reason=reason,
        last_error=str(exc),
        cooldown_until=cooldown_until,
    )
    db.add(row)
    db.flush()
    return row


def raise_or_enqueue_fetch_error(
    db: Session,
    *,
    platform_code: str,
    profile_input: str,
    user: User | None,
    exc: Exception,
    operation: ProfileFetchPendingOperation = ProfileFetchPendingOperation.profile_preview,
) -> None:
    from app.services.profile_linking_service import ProfileLinkingError

    if not is_fetch_cooldown_error(exc):
        raise exc

    enqueue_profile_fetch_pending(
        db,
        platform_code=platform_code,
        profile_input=profile_input,
        user_id=user.id if user is not None else None,
        operation=operation,
        exc=exc,
    )
    db.commit()
    raise ProfileLinkingError(
        "Не удалось обновить профиль. Операция добавлена в очередь, ожидайте.",
        503,
    ) from exc


def _get_platform(db: Session, platform_code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == platform_code).one_or_none()
    if platform is None:
        raise ValueError(f"Platform not found: {platform_code}")
    return platform


def _complete_pending_profile_link(
    db: Session,
    row: ProfileFetchPending,
    platform: Platform,
    profile_input: str,
) -> str:
    """Fetch preview and create PlatformLink for the user who queued this row."""
    from app.models import User
    from app.services.profile_linking_service import (
        ProfileLinkingError,
        confirm_profile_link,
        preview_profile_link,
    )

    user = db.get(User, row.user_id)
    if user is None:
        raise ValueError(f"User not found for pending fetch {row.id}")

    preview_profile_link(db, platform.code, profile_input, user=user)
    try:
        link = confirm_profile_link(db, user, platform.code, profile_input)
    except ProfileLinkingError as exc:
        raise ValueError(exc.message) from exc
    return link.external_user_id


def _persist_profile_preview(db: Session, platform: Platform, profile_input: str) -> str:
    from app.services.profile_linking_service import _upsert_participant

    ensure_adapters_registered()
    adapter = get_adapter(platform.code)
    preview = adapter.fetch_profile_preview(profile_input)
    _upsert_participant(db, platform, preview, adapter.parser_version)
    return preview.external_user_id


def _import_parkrun_activity(db: Session, platform: Platform, athlete_id: str) -> str:
    from app.sync.parkrun_participant_import import import_parkrun_participant_activity

    result = import_parkrun_participant_activity(db, platform, athlete_id)
    return result.participant.external_user_id


def _import_s95_activity(db: Session, platform: Platform, external_user_id: str) -> str:
    from app.platform_adapters.s95.parser import fetch_athlete_activity
    from app.sync import upsert

    profile, runs, volunteering = fetch_athlete_activity(external_user_id)
    participant, _created = upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    upsert.import_profile_run_results(db, platform, runs)
    upsert.import_profile_volunteer_results(db, platform, participant.id, volunteering)
    return participant.external_user_id


def _import_five_verst_activity(db: Session, platform: Platform, profile_input: str) -> str:
    from app.platform_adapters.five_verst.parser import fetch_athlete_activity
    from app.sync import upsert

    identity = resolve_profile_identity(platform.code, profile_input)
    profile, runs, volunteering = fetch_athlete_activity(identity.external_user_id)
    participant, _created = upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    upsert.import_profile_run_results(db, platform, runs)
    upsert.import_profile_volunteer_results(db, platform, participant.id, volunteering)
    return participant.external_user_id


def process_pending_row(db: Session, row: ProfileFetchPending) -> str:
    if row.status not in (ProfileFetchPendingStatus.pending, ProfileFetchPendingStatus.processing):
        return "skipped_status"

    if is_platform_in_cooldown(row.platform_code):
        return "skipped_cooldown"

    row.status = ProfileFetchPendingStatus.processing
    row.updated_at = datetime.now(timezone.utc)
    db.commit()

    platform = _get_platform(db, row.platform_code)
    profile_input = row.canonical_profile_url or row.profile_input
    try:
        if row.operation == ProfileFetchPendingOperation.activity_import:
            if row.platform_code == "parkrun":
                athlete_id = row.external_user_id or resolve_profile_identity(
                    row.platform_code, profile_input
                ).external_user_id
                external_id = _import_parkrun_activity(db, platform, athlete_id)
            elif row.platform_code == "s95":
                external_id = row.external_user_id or resolve_profile_identity(
                    row.platform_code, profile_input
                ).external_user_id
                external_id = _import_s95_activity(db, platform, external_id)
            elif row.platform_code == "five_verst":
                external_id = _import_five_verst_activity(db, platform, profile_input)
            else:
                raise ValueError(f"Unsupported platform: {row.platform_code}")
        elif row.user_id is not None:
            external_id = _complete_pending_profile_link(db, row, platform, profile_input)
        else:
            external_id = _persist_profile_preview(db, platform, profile_input)

        row.external_user_id = external_id
        row.status = ProfileFetchPendingStatus.done
        row.processed_at = datetime.now(timezone.utc)
        row.last_error = None
        db.commit()
        return "done"
    except Exception as exc:
        if is_fetch_cooldown_error(exc):
            row.status = ProfileFetchPendingStatus.pending
            row.reason = _reason_from_error(exc)
            row.last_error = str(exc)
            row.cooldown_until = parse_cooldown_until_from_message(str(exc))
            row.attempts += 1
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.warning(
                "pending profile fetch paused (platform protection): %s %s",
                row.platform_code,
                row.external_user_id or row.profile_input,
            )
            return "cooldown"
        row.status = ProfileFetchPendingStatus.pending
        row.reason = ProfileFetchPendingReason.error
        row.last_error = str(exc)
        row.attempts += 1
        if row.attempts >= 5:
            row.status = ProfileFetchPendingStatus.failed
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.exception("pending profile fetch failed: %s", row.id)
        return "error"


def _parkrun_platform_id(db: Session) -> UUID | None:
    return db.query(Platform.id).filter(Platform.code == "parkrun").scalar()


def count_stuck_done_parkrun_pending(db: Session) -> int:
    """Done rows for users who still have no parkrun PlatformLink (failed link step)."""
    parkrun_platform_id = _parkrun_platform_id(db)
    if parkrun_platform_id is None:
        return 0
    linked_user_ids = (
        db.query(PlatformLink.user_id)
        .filter(PlatformLink.platform_id == parkrun_platform_id)
        .distinct()
    )
    return (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.done,
            ProfileFetchPending.user_id.isnot(None),
            ~ProfileFetchPending.user_id.in_(linked_user_ids),
        )
        .count()
    )


def requeue_stuck_done_parkrun_pending(db: Session) -> int:
    parkrun_platform_id = _parkrun_platform_id(db)
    if parkrun_platform_id is None:
        return 0
    linked_user_ids = (
        db.query(PlatformLink.user_id)
        .filter(PlatformLink.platform_id == parkrun_platform_id)
        .distinct()
    )
    rows = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.done,
            ProfileFetchPending.user_id.isnot(None),
            ~ProfileFetchPending.user_id.in_(linked_user_ids),
        )
        .all()
    )
    for row in rows:
        row.status = ProfileFetchPendingStatus.pending
        row.attempts = 0
        row.last_error = None
        row.processed_at = None
    if rows:
        db.commit()
    return len(rows)


def ensure_parkrun_pending_queue_row(
    db: Session,
    athlete_id: str,
    *,
    user_id: UUID | None = None,
    operation: ProfileFetchPendingOperation = ProfileFetchPendingOperation.activity_import,
    note: str = "queued for Mac parkrun daemon",
) -> ProfileFetchPending:
    """Insert or re-open a parkrun queue row for daemon/seed scripts."""
    athlete_id = athlete_id.strip()
    profile_input = athlete_id
    canonical_profile_url: str | None = None
    try:
        identity = resolve_profile_identity("parkrun", profile_input)
        athlete_id = identity.external_user_id
        canonical_profile_url = identity.canonical_profile_url
        profile_input = canonical_profile_url or profile_input
    except Exception:
        pass

    row = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.external_user_id == athlete_id,
        )
        .order_by(ProfileFetchPending.created_at.desc())
        .first()
    )
    if row is None:
        row = (
            db.query(ProfileFetchPending)
            .filter(
                ProfileFetchPending.platform_code == "parkrun",
                ProfileFetchPending.profile_input == profile_input,
            )
            .order_by(ProfileFetchPending.created_at.desc())
            .first()
        )

    if row is not None:
        row.status = ProfileFetchPendingStatus.pending
        row.attempts = 0
        row.last_error = note
        row.reason = ProfileFetchPendingReason.error
        row.cooldown_until = None
        row.operation = operation
        row.processed_at = None
        if user_id is not None:
            row.user_id = user_id
        if canonical_profile_url:
            row.canonical_profile_url = canonical_profile_url
        row.external_user_id = athlete_id
        db.flush()
        return row

    row = ProfileFetchPending(
        user_id=user_id,
        platform_code="parkrun",
        profile_input=profile_input,
        external_user_id=athlete_id,
        canonical_profile_url=canonical_profile_url,
        operation=operation,
        status=ProfileFetchPendingStatus.pending,
        reason=ProfileFetchPendingReason.error,
        last_error=note,
    )
    db.add(row)
    db.flush()
    return row


def reset_failed_parkrun_pending(db: Session) -> int:
    rows = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.failed,
        )
        .all()
    )
    for row in rows:
        row.status = ProfileFetchPendingStatus.pending
        row.attempts = 0
        row.last_error = None
    if rows:
        db.commit()
    return len(rows)


def reset_failed_pending(db: Session, platform_code: str) -> int:
    """Re-open failed rows for a platform so the daemon retries them."""
    rows = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == platform_code,
            ProfileFetchPending.status == ProfileFetchPendingStatus.failed,
        )
        .all()
    )
    for row in rows:
        row.status = ProfileFetchPendingStatus.pending
        row.attempts = 0
        row.last_error = None
    if rows:
        db.commit()
    return len(rows)


def list_pending_rows(
    db: Session,
    *,
    platform_code: str | None = None,
    limit: int = 50,
) -> list[ProfileFetchPending]:
    query = db.query(ProfileFetchPending).filter(
        ProfileFetchPending.status == ProfileFetchPendingStatus.pending
    )
    if platform_code:
        query = query.filter(ProfileFetchPending.platform_code == platform_code)
    return query.order_by(ProfileFetchPending.created_at.asc()).limit(limit).all()
