from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.models import (
    AuthIdentity,
    AuthOneTimeToken,
    BacklogCard,
    BacklogCardSubscription,
    BacklogComment,
    BacklogVote,
    DashboardCache,
    LocationOrganizerAccess,
    LocationRating,
    Platform,
    PlatformLink,
    RunPlan,
    RunPlanSeries,
    SyncJob,
    User,
    UserAward,
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
#: до вызова, остальные разбирает delete_user_with_dependencies (объединение)
#: и account_deletion_service (удаление профиля самим человеком). Бэклог из
#: списка ушёл с миграцией 109: авторство — SET NULL, голоса — CASCADE.
#: Сторож — tests/test_account_deletion.py: новый FK на users без ON DELETE
#: обязан попасть сюда, в мир сквозного теста удаления и в шаги
#: account_deletion_service.delete_account.
BLOCKING_USER_REFERENCES = (
    "platform_links",
    "dashboard_cache",
    "sync_jobs",
    "auth_one_time_tokens",
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


def _reassign_backlog_subscriptions(db: Session, merged_id: UUID, survivor_id: UUID) -> None:
    """Подписки на карточки бэклога переезжают к выжившему; где он уже следит —
    дубль просто уходит вместе с профилем (CASCADE). Настройки и каналы
    уведомлений поглощаемого не переносим: у выжившего свои, а каналы
    привязаны к его же способам входа."""
    survivor_cards = {
        row[0]
        for row in db.query(BacklogCardSubscription.card_id)
        .filter(BacklogCardSubscription.user_id == survivor_id)
        .all()
    }
    query = db.query(BacklogCardSubscription).filter(BacklogCardSubscription.user_id == merged_id)
    if survivor_cards:
        query = query.filter(BacklogCardSubscription.card_id.notin_(survivor_cards))
    query.update({"user_id": survivor_id}, synchronize_session=False)


def delete_user_with_dependencies(
    db: Session,
    merged: User,
    *,
    reassign_to: UUID,
) -> None:
    """Удаление поглощённого профиля: его строки переезжают к выжившему.

    Только для объединения аккаунтов — наследник обязателен. Удаление профиля
    без наследника (человек удаляет себя сам) живёт в account_deletion_service:
    там другой порядок шагов и уборка за пределами базы. До 12.10.2026 здесь
    была и ветка без наследника, но ни разу не вызывалась и была с ошибками:
    падала на auth_identities (ORM обнуляет NOT NULL-ключ раньше CASCADE базы)
    и снимала голоса без пересчёта счётчиков чужих карточек.

    Список таблиц — BLOCKING_USER_REFERENCES; забытая означает
    ForeignKeyViolation на DELETE FROM users прямо в лицо пользователю.

    Написанное человеком (отзывы, цели, гранты организатора, особые награды)
    переезжает здесь же, а не только в merge_users: профиль поглощает и
    привязка VK/Яндекса в настройках (oauth_service, «сирота» с одним
    способом входа), и до 21.10.2026 этот путь молча терял всё это каскадом.
    """
    _reassign_user_content(db, merged.id, reassign_to)
    db.query(DashboardCache).filter(DashboardCache.user_id == merged.id).delete()
    # Одноразовые токены магической ссылки при использовании не удаляются, а
    # только помечаются used_at. У любого, кто хоть раз входил через бота,
    # здесь лежит строка — и она держит удаление профиля.
    db.query(AuthOneTimeToken).filter(AuthOneTimeToken.user_id == merged.id).delete(synchronize_session=False)
    db.query(SyncJob).filter(SyncJob.user_id == merged.id).update({"user_id": reassign_to})
    _reassign_backlog(db, merged.id, reassign_to)
    _reassign_backlog_subscriptions(db, merged.id, reassign_to)
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
    поглощаемого профиля молча унесло бы отзывы, цели, права организатора и
    особые награды. Уникальные ключи есть у целей (user+год+тип), у доступа
    (user+локация), у наград (user+код) и у планов (user+дата+локация, у
    отметки «Не бегаю» — user+дата): там, где у выжившего уже своя строка,
    чужую не тащим.
    """
    # Отзыв уникален по (человек, локация, дата, тип участия) — с миграции 091
    # ключ естественный, а не по строке результата (её синк пересобирает). Один
    # и тот же старт мог попасть в оба профиля, если привязка успела переехать
    # между ними: тогда у выжившего уже есть свой отзыв, и чужой не переносим —
    # иначе перенос упёрся бы в уникалку.
    survivor_rated = {
        (row[0], row[1], row[2])
        for row in db.query(
            LocationRating.location_id,
            LocationRating.event_date,
            LocationRating.participation_type,
        )
        .filter(LocationRating.user_id == survivor_id)
        .all()
    }
    for rating in db.query(LocationRating).filter(LocationRating.user_id == merged_id).all():
        if (rating.location_id, rating.event_date, rating.participation_type) in survivor_rated:
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

    # Особая награда («Тысячный участник») — о человеке, а не об аккаунте: она
    # обязана пережить объединение, в какую бы сторону оно ни прошло.
    survivor_awards = {row[0] for row in db.query(UserAward.code).filter(UserAward.user_id == survivor_id).all()}
    for award in db.query(UserAward).filter(UserAward.user_id == merged_id).all():
        if award.code in survivor_awards:
            db.delete(award)
        else:
            award.user_id = survivor_id

    _reassign_run_plans(db, merged_id, survivor_id)
    _reassign_run_plan_series(db, merged_id, survivor_id)
    db.flush()


def _reassign_run_plan_series(db: Session, merged_id: UUID, survivor_id: UUID) -> None:
    """Постоянные планы переезжают к выжившему; активный у человека один.

    Остановленные поглощённого — историей (его прошлые субботы). Активный
    поглощённого переезжает как есть, вместе со сном, если у выжившего
    активного нет; есть — у выжившего остаётся свой, а поглощённый
    останавливается (будущее уходит, без прошлого — удаляется) и переезжает
    историей. Исключение — у выжившего срок кончился, а у поглощённого план
    живой: тогда останавливается кончившийся, иначе после объединения
    постоянного плана не осталось бы нигде. Сначала остановка и flush, потом смена владельца: частичный
    уникальный индекс «одна активная» не должен увидеть две активные строки
    ни на одной инструкции. Двойное покрытие прошлой даты разруливает чтение
    (planned_starts: серия с самым поздним starts_on).

    Будущие «Не бегаю» поглощённого переезжают строками дня (_reassign_run_plans)
    и снимают вхождения серии выжившего в эти дни — в том числе его «Пропущу»
    к своей, теперь остановленной, серии: «Не бегаю» — отметка о дне, а не о
    локации. «Не сейчас» у подсказки — позднее из двух. Публичность —
    своя у каждого плана и переезжает как есть: план сделал тот же человек;
    закрытый профиль выжившего спрячет её при чтении.
    """
    from app.services.plan_series_service import _stop_row, forget_series_hint
    from app.services.run_plan_service import _lock_user
    from app.services.start_calendar_service import moscow_today

    for user_id in sorted((merged_id, survivor_id), key=str):
        _lock_user(db, user_id)
    today = moscow_today()
    survivor_active = (
        db.query(RunPlanSeries)
        .filter(RunPlanSeries.user_id == survivor_id, RunPlanSeries.stopped_at.is_(None))
        .one_or_none()
    )
    merged_series = db.query(RunPlanSeries).filter(RunPlanSeries.user_id == merged_id).all()

    def live(series: RunPlanSeries) -> bool:
        return series.until is None or series.until >= today

    merged_active = next((series for series in merged_series if series.stopped_at is None), None)
    if survivor_active is not None and merged_active is not None and not live(survivor_active) and live(merged_active):
        # Срок выжившего кончился (держит только «Продлить», пока задача сна
        # его не остановит), а у поглощённого план живой — остаётся живой:
        # кончившийся тихо останавливается, как при новом плане.
        _stop_row(db, survivor_active, today)
        survivor_active = None
    for series in merged_series:
        if series.stopped_at is None and survivor_active is not None:
            if _stop_row(db, series, today):
                continue
        db.flush()
        series.user_id = survivor_id
    db.flush()
    survivor = db.get(User, survivor_id)
    merged = db.get(User, merged_id)
    if survivor is not None and merged is not None and merged.plans_hint_snoozed_until is not None:
        if survivor.plans_hint_snoozed_until is None or survivor.plans_hint_snoozed_until < merged.plans_hint_snoozed_until:
            survivor.plans_hint_snoozed_until = merged.plans_hint_snoozed_until
    forget_series_hint(survivor_id)


def _reassign_run_plans(db: Session, merged_id: UUID, survivor_id: UUID) -> None:
    """Планы и отметки «Не бегаю» переезжают к выжившему.

    Лимит дня (1 план на обычную дату) после переноса может превыситься — это
    не беда: оба плана человек сделал сам, лишний уберёт в кабинете.
    Публичность (`is_public`) у каждой строки своя и переезжает с ней.

    Конфликт — та же дата и та же ТОЧКА, а не тот же сырой ключ: ключ плана
    дрейфует (`location:<id>` → `catalog:<id>`, когда локацию заводят в
    каталог), и у выжившего оставались две строки одной локации на дату — с
    двумя одинаковыми строками в напоминании (ревью 25.10.2026, INTEG-2).
    Точку узнаём по ключу, по опорной строке и по сегодняшнему ключу опорной
    строки. Строка дня («Не бегаю») — одна на дату: её метка (дата, None).
    Две отметки на одну дату сводятся в строку выжившего с его публичностью.
    План пробежки на ту же дату сильнее «Не бегаю», как при обычном
    «Планирую»: отметка уходит.
    """
    from app.services.run_plan_service import canonical_identity_keys
    from app.services.start_calendar_service import normalize_identity_key

    survivor_plans = db.query(RunPlan).filter(RunPlan.user_id == survivor_id).all()
    merged_plans = db.query(RunPlan).filter(RunPlan.user_id == merged_id).all()
    canonical = canonical_identity_keys(
        db, [plan.location_id for plan in (*survivor_plans, *merged_plans) if plan.location_id is not None]
    )

    def marks(plan: RunPlan) -> set[tuple[object, str | None]]:
        if plan.location_key is None:
            return {(plan.plan_date, None)}
        found: set[tuple[object, str | None]] = {
            (plan.plan_date, plan.location_key),
            (plan.plan_date, normalize_identity_key(plan.location_key) or plan.location_key),
        }
        if plan.location_id is not None:
            found.add((plan.plan_date, f"id:{plan.location_id}"))
            if plan.location_id in canonical:
                found.add((plan.plan_date, canonical[plan.location_id]))
        return found

    taken: set[tuple[object, str | None]] = set()
    for plan in survivor_plans:
        taken |= marks(plan)
    kept = list(survivor_plans)
    for plan in merged_plans:
        plan_marks = marks(plan)
        if plan_marks & taken:
            db.delete(plan)
        else:
            plan.user_id = survivor_id
            taken |= plan_marks
            kept.append(plan)
    planned_dates = {plan.plan_date for plan in kept if plan.location_key is not None}
    for plan in kept:
        if plan.location_key is None and plan.plan_date in planned_dates:
            db.delete(plan)


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
    settings: Settings | None = None,
) -> User:
    if survivor_id == merged_id:
        raise AccountMergeError("Нельзя объединить профиль сам с собой.")
    if strategy not in MERGE_STRATEGIES:
        raise AccountMergeError("Неизвестный способ объединения профилей.")

    survivor = db.query(User).filter(User.id == survivor_id).one_or_none()
    merged = db.query(User).filter(User.id == merged_id).one_or_none()
    if survivor is None or merged is None:
        raise AccountMergeError("Профиль не найден.", 404)
    # Поглощённый профиль отдаёт выжившему Telegram и способы входа, а по ним
    # сайт и узнаёт админа (core/admin.py). Ссылку на подтверждение привязки
    # в боте можно переслать: админ нажал бы «Подтвердить» — и админка ушла бы
    # к тому, кто начал привязку (SEC-02, аудит 23.10.2026). Проверка здесь
    # закрывает все пути объединения: бот, виджет Telegram, VK, Яндекс, почту.
    merged_is_admin = is_admin_user(merged, settings or get_settings())
    # Проверка загрузила merged.auth_identities. Ниже способы входа уезжают к
    # выжившему, и при удалении профиля SQLAlchemy обнулил бы user_id у всего,
    # что осталось в загруженной коллекции, — объединение падало бы на NOT NULL.
    db.expire(merged, ["auth_identities"])
    if merged_is_admin:
        raise AccountMergeError(
            "Профиль администратора нельзя присоединить к другому профилю. "
            "Войдите как администратор и привяжите этот способ входа из его профиля.",
            403,
        )

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
