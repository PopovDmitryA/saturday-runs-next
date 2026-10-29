"""Удаление профиля самим человеком: настройки → «Удаление профиля».

Что уходит, а что остаётся (решения Дмитрия 12.10.2026):
  * профиль, способы входа, привязки к беговым системам, настройки,
    уведомления, цели, отзывы о локациях вместе с фото, треки, ручные гранты
    организатора, особые награды, планы на старты, постоянный план
    («каждую субботу», с историей) и отметки «Не бегаю»,
    журнал входов и гео-отметки — всё это личное и удаляется:
    часть явными шагами ниже, остальное ON DELETE CASCADE базы;
  * протоколы (participants, run_results, volunteer_results) — общие данные
    систем, а не человека: остаются, человек снова виден под именем из
    протокола, без ссылки на профиль сайта;
  * карточки и комментарии бэклога остаются с автором «Профиль удалён», его
    голоса снимаются с пересчётом счётчиков (backlog_service);
  * публичные кэши (рейтинги 6 ч, страницы локаций 3 ч, главная сутки)
    дотухают сами — как после отвязки профиля и включения приватности. Окно
    подтверждения честно об этом предупреждает;
  * освободившийся public_slug не резервируется, serial_id не переиспользуется.

Порядок шагов важен (delete_account). Главная ловушка — ORM: у User четыре
коллекции (platform_links, dashboard_cache, sync_jobs, auth_identities) без
passive_deletes, и session.delete(user) обнулил бы их NOT NULL-ключи раньше
CASCADE базы — IntegrityError. Поэтому сам профиль удаляется Core-запросом, а
объект выводится из сессии.

Файлы, OG-картинка, Redis и уведомление админу — после коммита
(finalize_account_deletion): откат транзакции не должен оставить живой
профиль без аватарки. И после ответа: ручка гасит сессию, стирает куки и
отдаёт «готово», а уборку ставит фоновой задачей. Иначе медленный бакет или
зависшая прокси Telegram (уведомление админу ждёт до 30 с, клиент — тоже 30)
показали бы человеку ошибку после уже состоявшегося удаления.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AdminTrackImport,
    AuthIdentity,
    AuthLoginRequest,
    AuthOneTimeToken,
    AuthProvider,
    BacklogCard,
    BacklogComment,
    BacklogVote,
    Base,
    DashboardCache,
    LocationOrganizerAccess,
    LocationRating,
    LocationRatingPhoto,
    Platform,
    PlatformLink,
    ProfileFetchPending,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    RunPlan,
    RunPlanSeries,
    RunTrack,
    SyncJob,
    User,
    UserGoal,
)
from app.schemas.account import AccountDeletionPreviewResponse
from app.services.auth_identity_service import provider_label
from app.services.backlog_service import BacklogDetachResult, detach_user_from_backlog
from app.services.location_course_service import refresh_profiles_after_track_removal
from app.services.platform_titles import PLATFORM_ORDER, platform_title
from app.services.profile_fetch_pending_service import ACCOUNT_DELETED_ERROR
from app.services.profile_unlink_service import release_link_sync_jobs

logger = logging.getLogger(__name__)

#: Слово, которое человек набирает руками в окне подтверждения.
CONFIRMATION_WORD = "удалить"

#: Чем закрываются незавершённые заявки на сбор профиля удалённого человека.
#: Метка «навсегда» — чтобы reset_failed_pending их не воскрешал, а
#: process_pending_row не возвращал в очередь заявку, закрытую посреди сбора.
FETCH_CLOSED_ERROR = ACCOUNT_DELETED_ERROR

_PROVIDER_ORDER = tuple(AuthProvider)


#: Попытка удаления, которая дошла до коммита, — по id из браузера. Ответ на
#: POST может не доехать (обрыв связи после коммита, 502 на перезапуске), а
#: по одному 401 браузер не отличит «удалила эта попытка» от «вышли на другой
#: вкладке». Час с запасом покрывает повтор и проверку исхода.
DELETION_ATTEMPT_PREFIX = "account:deleted:"
DELETION_ATTEMPT_TTL_SECONDS = 3600


def remember_deletion_attempt(attempt_id: str) -> None:
    from app.core.redis_client import get_redis_client

    get_redis_client().setex(f"{DELETION_ATTEMPT_PREFIX}{attempt_id}", DELETION_ATTEMPT_TTL_SECONDS, "1")


def deletion_attempt_succeeded(attempt_id: str) -> bool:
    from app.core.redis_client import get_redis_client

    return bool(get_redis_client().exists(f"{DELETION_ATTEMPT_PREFIX}{attempt_id}"))


class AccountDeletionError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def confirmation_matches(raw: str) -> bool:
    """«Удалить», « удалить » и «УДАЛИТЬ» подходят; «удали» — нет."""
    return raw.strip().casefold() == CONFIRMATION_WORD


@dataclass
class AccountDeletionReceipt:
    """Что сделано в базе и что осталось убрать за её пределами после коммита."""

    user_id: UUID
    serial_id: int
    public_slug: str | None
    display_name: str | None
    created_at: datetime | None
    platform_titles: list[str]
    telegram_ids: list[int]
    location_reviews: int
    goals: int
    run_tracks: int
    cancelled_sync_jobs: int
    fetch_requests_closed: int
    course_profiles_rebuilt: int
    course_profiles_removed: int
    backlog: BacklogDetachResult
    # Уборка после коммита.
    avatar_keys: list[str] = field(default_factory=list)
    review_photo_keys: list[str] = field(default_factory=list)
    track_import_ids: list[UUID] = field(default_factory=list)
    # (код системы, адрес профиля) — по ним лежит кэш превью привязки.
    preview_cache_refs: list[tuple[str, str]] = field(default_factory=list)


def _count(db: Session, model: type[Base], *criteria: ColumnElement[bool]) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)


def _platform_codes(db: Session, user_id: UUID) -> list[str]:
    codes = {
        row[0]
        for row in db.query(Platform.code)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    }
    ordered = [code for code in PLATFORM_ORDER if code in codes]
    return ordered + sorted(codes - set(ordered))


def _telegram_ids(db: Session, user: User) -> list[int]:
    """Telegram-аккаунты человека: поле профиля и способы входа через бота."""
    ids: set[int] = set()
    if user.telegram_id is not None:
        ids.add(int(user.telegram_id))
    rows = db.query(AuthIdentity.external_id).filter(
        AuthIdentity.user_id == user.id, AuthIdentity.provider == AuthProvider.telegram
    )
    for (external_id,) in rows.all():
        if str(external_id).isdigit():
            ids.add(int(external_id))
    return sorted(ids)


def _review_photo_keys(db: Session, user_id: UUID) -> list[str]:
    return list(
        db.scalars(
            select(LocationRatingPhoto.storage_key)
            .join(LocationRating, LocationRating.id == LocationRatingPhoto.rating_id)
            .where(LocationRating.user_id == user_id)
        )
    )


def deletion_preview(db: Session, user: User) -> AccountDeletionPreviewResponse:
    """Цифры для окна «Удалить профиль навсегда?»."""
    from app.services.organizer_access_service import user_is_organizer

    providers = {row[0] for row in db.query(AuthIdentity.provider).filter(AuthIdentity.user_id == user.id).all()}
    try:
        # Тот же кэшированный расчёт, что у /auth/me: на настройки человек
        # приходит с прогретым кэшем.
        is_organizer = user_is_organizer(db, user)
    except Exception:  # noqa: BLE001 — без этой строки окно всё равно полезно
        logger.exception("account deletion preview: organizer check failed for %s", user.id)
        is_organizer = False
    return AccountDeletionPreviewResponse(
        platforms=[platform_title(code) for code in _platform_codes(db, user.id)],
        auth_providers=[provider_label(provider) for provider in _PROVIDER_ORDER if provider in providers],
        location_reviews=_count(db, LocationRating, LocationRating.user_id == user.id),
        review_photos=len(_review_photo_keys(db, user.id)),
        goals=_count(db, UserGoal, UserGoal.user_id == user.id),
        run_tracks=_count(db, RunTrack, RunTrack.user_id == user.id),
        # Планы и «Не бегаю» одной строкой: всё уходит вместе с профилем.
        run_plans=_count(db, RunPlan, RunPlan.user_id == user.id),
        # Постоянный план отдельной строкой: «правило» не то же, что планы на даты.
        run_plan_series=_count(db, RunPlanSeries, RunPlanSeries.user_id == user.id),
        backlog_cards=_count(db, BacklogCard, BacklogCard.author_user_id == user.id),
        backlog_comments=_count(db, BacklogComment, BacklogComment.author_user_id == user.id),
        backlog_votes=_count(db, BacklogVote, BacklogVote.user_id == user.id),
        organizer_grants=_count(db, LocationOrganizerAccess, LocationOrganizerAccess.user_id == user.id),
        is_organizer=is_organizer,
        has_avatar=bool(user.avatar_path or user.avatar_full_path),
    )


def _remove_platform_links(db: Session, user_id: UUID) -> tuple[int, list[tuple[str, str]]]:
    """Привязки уходят без отвязки по одной (см. release_link_sync_jobs)."""
    rows = (
        db.query(Platform.code, PlatformLink)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    cancelled = 0
    preview_refs: list[tuple[str, str]] = []
    for code, link in rows:
        cancelled += release_link_sync_jobs(db, link)
        preview_refs.append((code, link.external_url))
        db.delete(link)
    db.flush()
    return cancelled, preview_refs


def _remove_login_leftovers(db: Session, user_id: UUID, telegram_ids: list[int]) -> None:
    """Одноразовые токены и заявки входа через бота.

    Токен при входе не удаляется, а только помечается used_at — у любого, кто
    входил через бота, он лежит и держит FK на users. Заявки
    (auth_login_requests) ссылок на профиль не держат, но хранят его
    telegram_id и сами не чистятся; на них, в свою очередь, ссылаются токены.
    """
    token_filter = AuthOneTimeToken.user_id == user_id
    if telegram_ids:
        request_ids = select(AuthLoginRequest.id).where(AuthLoginRequest.telegram_id.in_(telegram_ids))
        token_filter = or_(token_filter, AuthOneTimeToken.login_request_id.in_(request_ids))
    db.query(AuthOneTimeToken).filter(token_filter).delete(synchronize_session="fetch")
    if telegram_ids:
        db.query(AuthLoginRequest).filter(AuthLoginRequest.telegram_id.in_(telegram_ids)).delete(
            synchronize_session="fetch"
        )


def _refresh_course_profiles(db: Session, location_ids: list[UUID]) -> tuple[int, int]:
    """Паспорта трасс, в которые входили треки удалённого человека.

    Линия трассы в паспорте — прорежённая копия одного настоящего трека, так
    что каждая версия паспорта обязана пересобраться без его треков, а версия
    только из них — исчезнуть. Не rebuild_location_profile: его поиск смены
    трассы после удаления сверял бы чужие треки с эталоном из удалённых и
    переписывал бы им версию (см. refresh_profiles_after_track_removal).
    """
    refilled = removed = 0
    for location_id in location_ids:
        location_refilled, location_removed = refresh_profiles_after_track_removal(db, location_id)
        refilled += location_refilled
        removed += location_removed
    return refilled, removed


def _close_fetch_requests(db: Session, user_id: UUID) -> int:
    """Незавершённые заявки человека на сбор профиля закрываются навсегда.

    Удалить строку нельзя, оставить как есть — тоже. После SET NULL у
    оставленной строки нет хозяина, и обработчик собирает профиль анонимно
    (ветка user_id is None в process_pending_row): pending — на ближайшем
    прогоне, failed — после reset_failed_pending на старте демона, processing
    — после requeue_stuck_processing_pending. А удалённая строка роняет прогон,
    который уже загрузил её пачкой: её поля после чужого коммита читаются как
    ObjectDeletedError, и ни s95_queue_daemon, ни parkrun_sync, ни
    parkrun_local_worker его не ловят. Закрытая же строка (failed + метка
    «навсегда») прогону отвечает «skipped_status», и воскрешать её некому.

    processing закрываем вместе со всеми: обычно это строка, брошенная упавшим
    демоном, и requeue_stuck_processing_pending вернул бы её в очередь уже
    анонимной. Если же демон держит её прямо сейчас, сбор без профиля упадёт
    («User not found» или FK на platform_links), и process_pending_row по
    метке ACCOUNT_DELETED_ERROR оставит строку закрытой, а не вернёт в
    очередь; удачная же запись (done) без хозяина никому не мешает.
    """
    return int(
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.user_id == user_id,
            ProfileFetchPending.status != ProfileFetchPendingStatus.done,
        )
        .update(
            {
                ProfileFetchPending.status: ProfileFetchPendingStatus.failed,
                ProfileFetchPending.reason: ProfileFetchPendingReason.error,
                ProfileFetchPending.last_error: FETCH_CLOSED_ERROR,
                ProfileFetchPending.cooldown_until: None,
                ProfileFetchPending.updated_at: func.now(),
            },
            synchronize_session="fetch",
        )
    )


def delete_account(db: Session, user: User) -> AccountDeletionReceipt:
    """Удалить профиль со всем личным. Без коммита — его делает вызывающий.

    После вызова объект user выведен из сессии: читать у него можно только
    уже загруженные поля, всё нужное дальше лежит в квитанции.
    """
    user_id = user.id
    # Блокировка строки профиля: прогрев сводки (dashboard_warm) мог бы вставить
    # dashboard_cache между нашим удалением сводки и DELETE FROM users.
    locked = db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if locked is None:
        raise AccountDeletionError("Профиль не найден", status_code=404)

    # Всё, что каскад унесёт без следа, собираем заранее.
    platform_titles = [platform_title(code) for code in _platform_codes(db, user_id)]
    telegram_ids = _telegram_ids(db, user)
    avatar_keys = [key for key in (user.avatar_path, user.avatar_full_path) if key]
    review_photo_keys = _review_photo_keys(db, user_id)
    track_location_ids = [
        location_id
        for location_id in db.scalars(
            select(RunTrack.location_id).where(RunTrack.user_id == user_id, RunTrack.location_id.isnot(None)).distinct()
        )
        if location_id is not None
    ]
    track_import_ids = list(
        db.scalars(select(AdminTrackImport.id).where(AdminTrackImport.target_user_id == user_id))
    )
    location_reviews = _count(db, LocationRating, LocationRating.user_id == user_id)
    goals = _count(db, UserGoal, UserGoal.user_id == user_id)
    run_tracks = _count(db, RunTrack, RunTrack.user_id == user_id)
    serial_id, public_slug, display_name, created_at = (
        user.serial_id,
        user.public_slug,
        user.display_name,
        user.created_at,
    )

    cancelled_sync_jobs, preview_refs = _remove_platform_links(db, user_id)

    # Таблицы без ON DELETE (account_merge_service.BLOCKING_USER_REFERENCES).
    db.query(SyncJob).filter(SyncJob.user_id == user_id).delete(synchronize_session="fetch")
    db.query(DashboardCache).filter(DashboardCache.user_id == user_id).delete(synchronize_session="fetch")
    _remove_login_leftovers(db, user_id, telegram_ids)

    # До DELETE FROM users: после него SET NULL сотрёт, чьи это заявки.
    fetch_requests_closed = _close_fetch_requests(db, user_id)

    backlog = detach_user_from_backlog(db, user_id)

    # Сам профиль — Core-запросом (см. докстринг модуля), остальное снимет
    # CASCADE базы, ссылки «кем создано» обнулит SET NULL.
    db.flush()
    db.execute(delete(User).where(User.id == user_id).execution_options(synchronize_session=False))
    if user in db:
        db.expunge(user)

    rebuilt, removed = _refresh_course_profiles(db, track_location_ids)

    return AccountDeletionReceipt(
        user_id=user_id,
        serial_id=serial_id,
        public_slug=public_slug,
        display_name=display_name,
        created_at=created_at,
        platform_titles=platform_titles,
        telegram_ids=telegram_ids,
        location_reviews=location_reviews,
        goals=goals,
        run_tracks=run_tracks,
        cancelled_sync_jobs=cancelled_sync_jobs,
        fetch_requests_closed=fetch_requests_closed,
        course_profiles_rebuilt=rebuilt,
        course_profiles_removed=removed,
        backlog=backlog,
        avatar_keys=avatar_keys,
        review_photo_keys=review_photo_keys,
        track_import_ids=track_import_ids,
        preview_cache_refs=preview_refs,
    )


def _attempt(step: str, user_id: UUID, action: Callable[..., object], *args: object) -> None:
    try:
        action(*args)
    except Exception:  # noqa: BLE001 — уборка после коммита не должна ронять ответ
        logger.exception("account deletion: step %s failed for user %s", step, user_id)


def _delete_media(key: str) -> None:
    from app.core.media_storage import get_media_storage

    get_media_storage().delete(key)


def _delete_redis_key(key: str) -> None:
    from app.core.redis_client import get_redis_client

    get_redis_client().delete(key)


def admin_message(receipt: AccountDeletionReceipt) -> str:
    """Короткая заметка админу. Адресов почты и телефонов здесь нет."""
    name = receipt.display_name or f"Участник #{receipt.serial_id}"
    head = f"🗑 Профиль удалён: {name} · #{receipt.serial_id}"
    if receipt.public_slug:
        head += f" · @{receipt.public_slug}"
    lines = [head, "Привязки: " + (", ".join(receipt.platform_titles) or "нет")]
    personal = [
        f"{label}: {value}"
        for label, value in (
            ("отзывов", receipt.location_reviews),
            ("целей", receipt.goals),
            ("треков", receipt.run_tracks),
        )
        if value
    ]
    if personal:
        lines.append("Удалено — " + ", ".join(personal))
    backlog = receipt.backlog
    if backlog.cards_kept or backlog.comments_kept:
        lines.append(
            f"Бэклог: карточек {backlog.cards_kept}, комментариев {backlog.comments_kept} — остались без автора"
        )
    if backlog.votes_removed:
        lines.append(f"Голосов в бэклоге снято: {backlog.votes_removed}")
    return "\n".join(lines)


def finalize_account_deletion(settings: Settings, receipt: AccountDeletionReceipt) -> None:
    """Уборка за пределами базы — строго после коммита, каждый шаг сам по себе.

    Ручка зовёт её фоновой задачей, когда ответ с «готово» и стёртыми куками
    уже ушёл: здесь сеть (бакет, Telegram), и её задержки человек ждать не
    должен. Упавший шаг пишет в лог и не мешает остальным — профиль уже удалён,
    а исключение из фоновой задачи увидел бы только лог сервера.
    """
    from app.core.abuse_store import _user_ip_key
    from app.core.media_storage import delete_avatar_media
    from app.services import backlog_status_digest
    from app.services.admin_notify import notify_admin
    from app.services.admin_track_import_service import _cleanup_staging
    from app.services.location_records_service import user_location_records_cache_key
    from app.services.organizer_access_service import invalidate_organizer_locations_cache
    from app.services.plan_series_service import _HINT_CACHE_PREFIX
    from app.services.planner_guests_service import _HOME_CACHE_PREFIX, _RUNS_CACHE_PREFIX
    from app.services.profile_preview_cache import clear_profile_preview_cache

    user_id = receipt.user_id

    for key in receipt.avatar_keys:
        _attempt("avatar", user_id, delete_avatar_media, key, settings)
    for key in receipt.review_photo_keys:
        _attempt("review photo", user_id, _delete_media, key)
    # Карточка для превью ссылки на профиль (имя и цифры) лежит файлом и
    # раздаётся nginx напрямую — сама она не исчезнет никогда.
    og_path = Path(settings.og_image_dir) / "users" / f"{receipt.serial_id}.png"
    _attempt("og image", user_id, og_path.unlink, True)
    # Распакованные архивы треков из админской загрузки на этого человека.
    for batch_id in receipt.track_import_ids:
        _attempt("track import staging", user_id, _cleanup_staging, batch_id)

    for code, url in receipt.preview_cache_refs:
        _attempt("profile preview cache", user_id, clear_profile_preview_cache, user_id, code, url)
    # Последний IP по Telegram — личный след в Redis на 30 дней. Бан по
    # telegram_id (abuse:ban:user:*) сознательно остаётся: удалить профиль и
    # завести новый не должно снимать блокировку.
    for telegram_id in receipt.telegram_ids:
        _attempt("abuse ip", user_id, _delete_redis_key, _user_ip_key(telegram_id))
    _attempt("organizer cache", user_id, invalidate_organizer_locations_cache, user_id)
    # Кэши планировщика по user_id: всего пробежек и дом (блок локации и
    # «Кто у нас бежит»), подсказка «каждую субботу».
    for prefix in (_RUNS_CACHE_PREFIX, _HOME_CACHE_PREFIX, _HINT_CACHE_PREFIX):
        _attempt("planner cache", user_id, _delete_redis_key, f"{prefix}{user_id}")
    _attempt("location records cache", user_id, _delete_redis_key, user_location_records_cache_key(user_id))
    # Отложенные смены статусов карточек для этого человека: take() снимает
    # группу целиком (список, отметку в очереди групп и время последней смены).
    _attempt("backlog status digest", user_id, backlog_status_digest.take, str(user_id))

    backlog = receipt.backlog
    logger.info(
        "account deleted: user=%s serial=%s platforms=%s reviews=%d review_photos=%d goals=%d tracks=%d "
        "cancelled_sync_jobs=%d fetch_requests_closed=%d course_profiles rebuilt=%d removed=%d "
        "backlog cards_kept=%d comments_kept=%d votes_removed=%d",
        user_id,
        receipt.serial_id,
        ",".join(receipt.platform_titles) or "-",
        receipt.location_reviews,
        len(receipt.review_photo_keys),
        receipt.goals,
        receipt.run_tracks,
        receipt.cancelled_sync_jobs,
        receipt.fetch_requests_closed,
        receipt.course_profiles_rebuilt,
        receipt.course_profiles_removed,
        backlog.cards_kept,
        backlog.comments_kept,
        backlog.votes_removed,
    )

    def _notify() -> None:
        notify_admin(admin_message(receipt))

    _attempt("admin notify", user_id, _notify)
