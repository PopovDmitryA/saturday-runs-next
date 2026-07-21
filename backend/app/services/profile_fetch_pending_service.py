from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.five_verst.errors import FiveVerstBanDetected
from app.models import (
    Participant,
    Platform,
    PlatformLink,
    ProfileFetchPending,
    ProfileFetchPendingOperation,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    User,
)
from app.parkrun.errors import ParkrunBanDetected, ParkrunProfileNotFound
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter
from app.platform_fetch.cooldown import is_platform_in_cooldown, parse_cooldown_until_from_message
from app.s95.errors import S95BanDetected, S95FetchTimeout
from app.services.participant_profile_service import resolve_profile_identity

logger = logging.getLogger(__name__)

_BAN_ERROR_TYPES = (ParkrunBanDetected, S95BanDetected, FiveVerstBanDetected)

# Метка, которой scripts/seed_parkrun_queue_from_runpark.py помечает `last_error`
# при сидировании очереди из архива RunPark — единственный сигнал происхождения
# строки, который у нас сейчас есть (без миграции схемы). list_pending_rows
# использует её, чтобы дать этим строкам приоритет над остальным backlog'ом:
# там гарантированно есть история, а не просто гипотеза о существовании профиля.
RUNPARK_SEED_NOTE_PREFIX = "seed from RunPark Pakrun archive"

# Маркирует last_error строки, по которой демон сдался НАВСЕГДА: либо профиль
# реально не существует (404), либо платформа стабильно банит/блокирует его
# MAX_RETRY_ATTEMPTS раз подряд. reset_failed_pending пропускает такие строки —
# без метки они воскресали бы в pending на каждом старте демона и повторяли
# один и тот же обречённый запрос бесконечно (жалобы про 790115304 у parkrun
# и у s95 — разные платформы, один и тот же паттерн).
PERMANENT_ERROR_PREFIX = "[permanent] "

# Общий порог для двух независимых веток process_pending_row: обычная ошибка
# (см. "error" ниже) и cooldown/бан (см. "cooldown_exhausted") — обе после
# этого числа попыток сдаются и ставят PERMANENT_ERROR_PREFIX.
MAX_RETRY_ATTEMPTS = 5


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

    # Ищем БЕЗ фильтра по статусу — иначе гонка с демоном (строка на секунду
    # оказалась 'processing' ровно когда пользователь снова упал на бане) или
    # уже закрытая 'done'/'failed' строка не находится дедупом, и для одного
    # и того же атлета копится несколько независимых pending-строк (нашли на
    # живых данных: 790269774 у s95 заведён дважды, за 21.06 и 06.07).
    existing: ProfileFetchPending | None = None
    if external_user_id:
        existing = (
            db.query(ProfileFetchPending)
            .filter(
                ProfileFetchPending.platform_code == platform_code,
                ProfileFetchPending.external_user_id == external_user_id,
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
        # 'processing' — демон работает над строкой прямо сейчас, статус не
        # трогаем (обновили только метаданные выше). Иначе (done/failed/уже
        # pending) — переоткрываем: новая ошибка отменяет прежний терминальный
        # исход, ретраить нужно с чистого счётчика попыток.
        if existing.status != ProfileFetchPendingStatus.processing:
            existing.status = ProfileFetchPendingStatus.pending
            existing.attempts = 0
            existing.processed_at = None
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


def describe_processed_profile(
    db: Session, platform_code: str, external_user_id: str | None
) -> str | None:
    """Ссылка + ФИО только что обработанного бегуна — для краткого статуса
    демона (--quiet), а не для полного лога. None, если участник ещё не
    успел появиться в БД (сбой до создания Participant)."""
    if not external_user_id:
        return None
    participant = (
        db.query(Participant)
        .join(Platform, Platform.id == Participant.platform_id)
        .filter(
            Platform.code == platform_code,
            Participant.external_user_id == external_user_id,
        )
        .one_or_none()
    )
    if participant is None:
        return None
    return f"→ {participant.profile_url or '?'}  {participant.display_name or '?'}"


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
    participant = upsert.upsert_participant(
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
    participant = upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    upsert.import_profile_run_results(db, platform, runs)
    upsert.import_profile_volunteer_results(db, platform, participant.id, volunteering)
    return participant.external_user_id


def _is_permanent_profile_error(exc: BaseException) -> bool:
    """Профиль не существует (404) — терминальная ошибка, ретраи не помогут.

    Ловим ParkrunProfileNotFound где угодно в цепочке __cause__ (прямой импорт
    поднимает его напрямую; preview-путь заворачивает в ProfileLinkingError с
    from exc). Плюс общий сигнал: ProfileLinkingError со status_code 404 —
    так же маппятся not-found у five_verst и s95.
    """
    cursor: BaseException | None = exc
    for _ in range(10):
        if cursor is None:
            break
        if isinstance(cursor, ParkrunProfileNotFound):
            return True
        if getattr(cursor, "status_code", None) == 404:
            return True
        cursor = cursor.__cause__
    return False


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
            row.attempts += 1
            row.updated_at = datetime.now(timezone.utc)
            if row.attempts >= MAX_RETRY_ATTEMPTS:
                # Платформа стабильно банит/блокирует именно эту строку
                # (не общий кулдаун — тот ловится раньше, is_platform_in_cooldown),
                # ретраить без реального бэкоффа бессмысленно: строка иначе висит
                # в pending вечно, попытки растут, а reset_* её каждый раз
                # воскрешает (нашли на 790269774 у s95 — 403 месяцами подряд).
                row.status = ProfileFetchPendingStatus.failed
                row.reason = _reason_from_error(exc)
                row.last_error = PERMANENT_ERROR_PREFIX + str(exc)
                db.commit()
                logger.info(
                    "pending profile fetch: %s попыток кулдауна подряд, сдаюсь: %s %s",
                    MAX_RETRY_ATTEMPTS,
                    row.platform_code,
                    row.external_user_id or row.profile_input,
                )
                return "cooldown_exhausted"
            row.status = ProfileFetchPendingStatus.pending
            row.reason = _reason_from_error(exc)
            row.last_error = str(exc)
            row.cooldown_until = parse_cooldown_until_from_message(str(exc))
            db.commit()
            logger.warning(
                "pending profile fetch paused (platform protection): %s %s",
                row.platform_code,
                row.external_user_id or row.profile_input,
            )
            return "cooldown"
        if _is_permanent_profile_error(exc):
            # Профиль не существует (404) — ретраи бессмысленны, сразу failed,
            # иначе строка 5 раз крутится в pending (жалоба про 790115304).
            # PERMANENT_ERROR_PREFIX защищает и от воскрешения на следующем
            # старте демона — см. reset_failed_pending.
            row.status = ProfileFetchPendingStatus.failed
            row.reason = ProfileFetchPendingReason.error
            row.last_error = PERMANENT_ERROR_PREFIX + str(exc)
            row.attempts += 1
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "pending profile fetch: профиль не найден, помечаю failed: %s %s",
                row.platform_code,
                row.external_user_id or row.profile_input,
            )
            return "not_found"
        row.status = ProfileFetchPendingStatus.pending
        row.reason = ProfileFetchPendingReason.error
        row.last_error = str(exc)
        row.attempts += 1
        if row.attempts >= MAX_RETRY_ATTEMPTS:
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
    return reset_failed_pending(db, "parkrun")


# Строка помечается 'processing' и коммитится до фетча (process_pending_row).
# Если демон падает на этом шаге (упавший туннель, Ctrl+C, краш браузера), строка
# навсегда виснет в 'processing' — list_pending_rows её не выбирает, и профиль
# теряется. Реквьюем «протухшие» processing на старте демона. Порог нужен, чтобы
# не тронуть строку, которую прямо сейчас обрабатывает живой демон; один элемент
# берётся ощутимо быстрее, так что 15 минут — заведомо безопасно.
STUCK_PROCESSING_AGE = timedelta(minutes=15)


def requeue_stuck_processing_parkrun_pending(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - STUCK_PROCESSING_AGE
    rows = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.processing,
            ProfileFetchPending.updated_at < cutoff,
        )
        .all()
    )
    for row in rows:
        row.status = ProfileFetchPendingStatus.pending
        row.updated_at = datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)


def reset_failed_pending(db: Session, platform_code: str) -> int:
    """Re-open failed rows for a platform so the daemon retries them.

    Rows given up permanently (see PERMANENT_ERROR_PREFIX — a genuine 404, or
    a resource that kept failing/banning past MAX_RETRY_ATTEMPTS) are skipped:
    their last run already gave a final answer, resurrecting them on every
    daemon start just repeats the same failed request forever.
    """
    rows = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == platform_code,
            ProfileFetchPending.status == ProfileFetchPendingStatus.failed,
            (ProfileFetchPending.last_error.is_(None))
            | (~ProfileFetchPending.last_error.like(f"{PERMANENT_ERROR_PREFIX}%")),
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
    # Три уровня приоритета:
    # 1. пользовательские строки (user_id заполнен — кто-то ждёт ответа);
    # 2. сид из архива RunPark — там гарантированно ЕСТЬ история (архив уже
    #    отсортирован по объёму при сидировании), в отличие от:
    # 3. discovery-backlog (например, s95-декаплинг) — это лишь гипотеза,
    #    что у атлета вообще есть профиль parkrun.
    # RUNPARK_SEED_NOTE_PREFIX завязан на текстовую метку из
    # seed_parkrun_queue_from_runpark.py — единственный маркер источника,
    # который у нас сейчас есть, без миграции схемы.
    is_runpark_seed = ProfileFetchPending.last_error.like(f"{RUNPARK_SEED_NOTE_PREFIX}%")
    return (
        query.order_by(
            ProfileFetchPending.user_id.is_(None),
            case((is_runpark_seed, 0), else_=1),
            ProfileFetchPending.created_at.asc(),
        )
        .limit(limit)
        .all()
    )
