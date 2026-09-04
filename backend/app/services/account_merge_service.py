from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    AuthIdentity,
    AuthOneTimeToken,
    BacklogCard,
    BacklogComment,
    BacklogVote,
    DashboardCache,
    LocationOrganizerAccess,
    LocationRating,
    Platform,
    PlatformLink,
    SyncJob,
    User,
    UserGoal,
)
from app.services.auth_identity_service import (
    CONFLICT_KEEP_MERGED,
    CONFLICT_KEEP_SURVIVOR,
    MERGE_STRATEGIES,
    MERGE_STRATEGY_SURVIVOR_ONLY,
    MERGE_STRATEGY_UNION,
    list_user_identities,
    merge_preview_payload,
)
from app.services.dashboard_service import recompute_dashboard_cache
from app.services.organizer_access_service import invalidate_organizer_locations_cache
from app.services.platform_titles import PLATFORM_TITLES
from app.services.profile_preview_cache import clear_profile_preview_cache
from app.services.profile_unlink_service import unlink_user_profile
from app.services.user_display_name_service import rebind_display_name_source


class AccountMergeError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


#: Таблицы, ссылающиеся на users.id без ON DELETE: удаление профиля упрётся
#: в каждую, где остались строки. platform_links снимается отвязкой профилей
#: до вызова, остальные разбирает delete_user_with_dependencies.
BLOCKING_USER_REFERENCES = (
    "platform_links",
    "dashboard_cache",
    "sync_jobs",
    "auth_one_time_tokens",
    "backlog_cards",
    "backlog_votes",
    "backlog_comments",
)


def _reassign_backlog(db: Session, merged_id: UUID, survivor_id: UUID) -> None:
    """Авторство карточек, комментариев и голосов переезжает к выжившему."""
    # Ключ голоса — (card_id, user_id): там, где выживший уже голосовал,
    # переносить нечего, второй голос снимаем и пересчитываем счётчики.
    survivor_cards = {
        row[0] for row in db.query(BacklogVote.card_id).filter(BacklogVote.user_id == survivor_id).all()
    }
    doubled = [
        row[0]
        for row in db.query(BacklogVote.card_id).filter(
            BacklogVote.user_id == merged_id, BacklogVote.card_id.in_(survivor_cards)
        )
    ] if survivor_cards else []
    if doubled:
        db.query(BacklogVote).filter(
            BacklogVote.user_id == merged_id, BacklogVote.card_id.in_(doubled)
        ).delete(synchronize_session=False)
    db.query(BacklogVote).filter(BacklogVote.user_id == merged_id).update(
        {"user_id": survivor_id}, synchronize_session=False
    )
    db.query(BacklogCard).filter(BacklogCard.author_user_id == merged_id).update(
        {"author_user_id": survivor_id}, synchronize_session=False
    )
    db.query(BacklogComment).filter(BacklogComment.author_user_id == merged_id).update(
        {"author_user_id": survivor_id}, synchronize_session=False
    )
    if doubled:
        db.flush()
        # Локальный импорт: backlog_service тянет фото и уведомления админам,
        # а account_merge_service грузится на старте приложения.
        from app.services.backlog_service import recompute_vote_counts

        for card in db.query(BacklogCard).filter(BacklogCard.id.in_(doubled)).all():
            recompute_vote_counts(db, card)


def _delete_backlog(db: Session, merged_id: UUID) -> None:
    """Наследника нет — карточки автора уходят вместе с ним (голоса и
    комментарии под ними снимет ON DELETE CASCADE по card_id)."""
    db.query(BacklogVote).filter(BacklogVote.user_id == merged_id).delete(synchronize_session=False)
    db.query(BacklogComment).filter(BacklogComment.author_user_id == merged_id).delete(synchronize_session=False)
    db.query(BacklogCard).filter(BacklogCard.author_user_id == merged_id).delete(synchronize_session=False)


def delete_user_with_dependencies(
    db: Session,
    merged: User,
    *,
    reassign_to: UUID | None = None,
) -> None:
    """Удаление профиля со снятием строк, которые FK не гасит сам.

    Список таблиц — BLOCKING_USER_REFERENCES; забытая означает
    ForeignKeyViolation на DELETE FROM users прямо в лицо пользователю.
    """
    db.query(DashboardCache).filter(DashboardCache.user_id == merged.id).delete()
    # Одноразовые токены магической ссылки при использовании не удаляются, а
    # только помечаются used_at. У любого, кто хоть раз входил через бота,
    # здесь лежит строка — и она держит удаление профиля.
    db.query(AuthOneTimeToken).filter(AuthOneTimeToken.user_id == merged.id).delete(synchronize_session=False)
    if reassign_to is not None:
        db.query(SyncJob).filter(SyncJob.user_id == merged.id).update({"user_id": reassign_to})
        _reassign_backlog(db, merged.id, reassign_to)
    else:
        db.query(SyncJob).filter(SyncJob.user_id == merged.id).delete(synchronize_session=False)
        _delete_backlog(db, merged.id)
    db.flush()
    db.delete(merged)


_CONFLICT_CHOICES = (CONFLICT_KEEP_SURVIVOR, CONFLICT_KEEP_MERGED)


def _titles(platform_codes: list[str]) -> str:
    return ", ".join(PLATFORM_TITLES.get(code, code) for code in platform_codes)


def _links_by_platform_code(db: Session, user_id: UUID) -> dict[str, PlatformLink]:
    rows = (
        db.query(Platform.code, PlatformLink)
        .join(PlatformLink, PlatformLink.platform_id == Platform.id)
        .filter(PlatformLink.user_id == user_id)
        .all()
    )
    return {code: link for code, link in rows}


def _move_platform_link(db: Session, link: PlatformLink, *, platform_code: str, survivor_id: UUID) -> None:
    """Привязка меняет владельца, а не пересоздаётся.

    Пересоздание порвало бы историю sync_jobs и потребовало бы повторного
    синка; здесь же переезжает та же строка, а задачи по ней достаются
    выжившему вместе с остальными (см. delete_user_with_dependencies).
    """
    # Превью профиля кэшируется по (user_id, platform, url) — у прежнего
    # владельца оно больше не про кого.
    clear_profile_preview_cache(link.user_id, platform_code, link.external_url)
    link.user_id = survivor_id
    db.flush()


def _reassign_user_content(db: Session, merged_id: UUID, survivor_id: UUID) -> None:
    """Написанное человеком переезжает к выжившему профилю.

    Эти таблицы висят на users с ON DELETE CASCADE, то есть удаление
    поглощаемого профиля молча унесло бы отзывы, цели и права организатора.
    Уникальные ключи есть у целей (user+год+тип) и у доступа (user+локация):
    там, где у выжившего уже своя строка, чужую не тащим.
    """
    # Отзыв уникален по (человек, пробежка) и (человек, волонтёрство). Один и
    # тот же старт мог попасть в оба профиля, если привязка успела переехать
    # между ними: тогда у выжившего уже есть свой отзыв, и чужой не переносим.
    survivor_rated = {
        (row[0], row[1])
        for row in db.query(LocationRating.run_result_id, LocationRating.volunteer_result_id)
        .filter(LocationRating.user_id == survivor_id)
        .all()
    }
    for rating in db.query(LocationRating).filter(LocationRating.user_id == merged_id).all():
        if (rating.run_result_id, rating.volunteer_result_id) in survivor_rated:
            db.delete(rating)
        else:
            rating.user_id = survivor_id

    survivor_goals = {
        (row[0], row[1])
        for row in db.query(UserGoal.year, UserGoal.goal_type).filter(UserGoal.user_id == survivor_id).all()
    }
    for goal in db.query(UserGoal).filter(UserGoal.user_id == merged_id).all():
        if (goal.year, goal.goal_type) in survivor_goals:
            db.delete(goal)
        else:
            goal.user_id = survivor_id

    survivor_locations = {
        row[0]
        for row in db.query(LocationOrganizerAccess.location_key)
        .filter(LocationOrganizerAccess.user_id == survivor_id)
        .all()
    }
    for access in db.query(LocationOrganizerAccess).filter(LocationOrganizerAccess.user_id == merged_id).all():
        if access.location_key in survivor_locations:
            db.delete(access)
        else:
            access.user_id = survivor_id
    db.flush()


def _resolve_platform_links(
    db: Session,
    survivor: User,
    merged: User,
    *,
    strategy: str,
    conflict_choices: dict[str, str],
) -> None:
    """Развилка объединения: что станет с привязанными учётками систем.

    Два варианта, и третьего быть не должно. «Объединить» собирает привязки
    обоих профилей; «только текущий» оставляет привязки того аккаунта, под
    которым человек сидит. Забрать чужие привязки, выбросив свои, нельзя:
    основным становится залогиненный профиль, и набор учёток должен быть его
    собственным либо общим.
    """
    survivor_links = _links_by_platform_code(db, survivor.id)
    merged_links = _links_by_platform_code(db, merged.id)

    if strategy == MERGE_STRATEGY_SURVIVOR_ONLY:
        for platform_code in merged_links:
            unlink_user_profile(db, merged, platform_code, commit=False)
        return

    conflicts = sorted(set(survivor_links) & set(merged_links))
    unknown = sorted(set(conflict_choices) - set(conflicts))
    if unknown:
        raise AccountMergeError(f"Выбор профиля лишний для системы: {_titles(unknown)}.")

    # Проверяем весь набор до первой отвязки: иначе при двух спорных системах
    # первая успела бы отвязаться, а вторая уронила бы операцию — и человек
    # получил бы ошибку вместе с уже потерянной привязкой.
    unanswered = [code for code in conflicts if conflict_choices.get(code) not in _CONFLICT_CHOICES]
    if unanswered:
        raise AccountMergeError(
            "Эти системы привязаны в обоих профилях — выберите, какую учётку оставить: "
            f"{_titles(unanswered)}."
        )

    for platform_code in conflicts:
        if conflict_choices[platform_code] == CONFLICT_KEEP_SURVIVOR:
            unlink_user_profile(db, merged, platform_code, commit=False)
        else:
            # Порядок важен: пока своя привязка на месте, чужая не встанет —
            # в platform_links уникальна пара (user_id, platform_id).
            unlink_user_profile(db, survivor, platform_code, commit=False)
            _move_platform_link(
                db, merged_links[platform_code], platform_code=platform_code, survivor_id=survivor.id
            )

    for platform_code, link in merged_links.items():
        if platform_code not in survivor_links:
            _move_platform_link(db, link, platform_code=platform_code, survivor_id=survivor.id)


def merge_users(
    db: Session,
    survivor_id: UUID,
    merged_id: UUID,
    *,
    strategy: str = MERGE_STRATEGY_UNION,
    conflict_choices: dict[str, str] | None = None,
) -> User:
    if survivor_id == merged_id:
        raise AccountMergeError("Нельзя объединить профиль сам с собой.")
    if strategy not in MERGE_STRATEGIES:
        raise AccountMergeError("Неизвестный способ объединения профилей.")

    survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
    merged = db.query(User).filter(User.id == merged_id).one_or_none()
    if survivor is None or merged is None:
        raise AccountMergeError("Профиль не найден.", 404)

    try:
        _resolve_platform_links(
            db,
            survivor,
            merged,
            strategy=strategy,
            conflict_choices=conflict_choices or {},
        )
    except AccountMergeError:
        # Отказ по выбору — не конец сценария: сессия должна остаться чистой,
        # чтобы человек мог ответить на вопрос и повторить тем же токеном.
        db.rollback()
        raise

    merged_identities = list_user_identities(db, merged.id)
    for identity in merged_identities:
        existing = (
            db.query(AuthIdentity)
            .filter(AuthIdentity.user_id == survivor.id, AuthIdentity.provider == identity.provider)
            .one_or_none()
        )
        if existing is not None and existing.id != identity.id:
            db.delete(existing)
        identity.user_id = survivor.id
        if identity.provider.value == "telegram":
            survivor.telegram_id = int(identity.external_id)
            survivor.telegram_username = identity.profile_json.get("telegram_username")
            survivor.telegram_first_name = identity.profile_json.get("telegram_first_name")
            survivor.telegram_last_name = identity.profile_json.get("telegram_last_name")
            chat_id = identity.profile_json.get("telegram_chat_id")
            survivor.telegram_chat_id = int(chat_id) if chat_id is not None else None

    if merged.consent_accepted and not survivor.consent_accepted:
        survivor.consent_accepted = True
        survivor.consent_ts = merged.consent_ts or datetime.now(timezone.utc)

    for platform_code, enabled in (merged.auto_sync_by_platform or {}).items():
        survivor_prefs = survivor.auto_sync_by_platform or {}
        if enabled and not survivor_prefs.get(platform_code):
            survivor_prefs[platform_code] = True
        survivor.auto_sync_by_platform = survivor_prefs

    if merged.news_subscribed and survivor.telegram_chat_id:
        survivor.news_subscribed = True

    _reassign_user_content(db, merged.id, survivor.id)
    delete_user_with_dependencies(db, merged, reassign_to=survivor.id)
    db.commit()
    db.refresh(survivor)
    recompute_dashboard_cache(db, survivor.id)
    # Привязки и способы входа переехали — источник имени пересматриваем заново.
    rebind_display_name_source(db, survivor, commit=True)
    # К выжившему профилю переехали чужие привязки — доступ мог появиться.
    invalidate_organizer_locations_cache(survivor.id)
    invalidate_organizer_locations_cache(merged.id)
    return survivor


def build_merge_preview(db: Session, survivor_id: UUID, merged_id: UUID) -> dict[str, object]:
    survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
    merged = db.query(User).filter(User.id == merged_id).one_or_none()
    if survivor is None or merged is None:
        raise AccountMergeError("Профиль не найден.", 404)
    if survivor.id == merged.id:
        raise AccountMergeError("Нельзя объединить профиль сам с собой.")
    return merge_preview_payload(db, survivor, merged)
