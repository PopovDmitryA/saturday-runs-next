"""Удаление профиля самим человеком: /api/account/* и account_deletion_service."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import fakeredis
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.session import create_session
from app.db.session import get_db
from app.main import app
from app.models import (
    AdminTrackImport,
    AuthIdentity,
    AuthLoginRequest,
    AuthOneTimeToken,
    BacklogCard,
    BacklogCardSubscription,
    BacklogCardType,
    BacklogComment,
    BacklogVote,
    Base,
    DashboardCache,
    Location,
    LocationCourseProfile,
    LocationOrganizerAccess,
    LocationRating,
    LocationRatingPhoto,
    NotificationDelivery,
    Participant,
    Platform,
    PlatformLink,
    ProfileFetchPending,
    ProfileFetchPendingReason,
    ProfileFetchPendingStatus,
    RunTrack,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    User,
    UserGoal,
    UserNotificationChannel,
    UserNotificationPrefs,
)
from app.services import account_deletion_service, location_course_service
from app.services.account_deletion_service import AccountDeletionReceipt
from app.services.account_merge_service import BLOCKING_USER_REFERENCES
from app.services.backlog_service import create_card, create_comment, list_cards_admin, vote_card
from app.services.location_records_service import user_location_records_cache_key
from app.services.profile_fetch_pending_service import (
    ACCOUNT_DELETED_ERROR,
    ACCOUNT_DELETED_REOPEN_GUARD,
    enqueue_profile_fetch_pending,
    process_pending_row,
    requeue_stuck_processing_pending,
    reset_failed_pending,
)

ADMIN_TELEGRAM_ID = 7_000_000_001


# ---------------------------------------------------------------------------
# Сторож: каждая ссылка на users без ON DELETE разобрана руками


def _blocking_user_fk_columns() -> list[tuple[str, str]]:
    """(таблица, колонка) каждого FK на users.id без ON DELETE — по моделям."""
    return sorted(
        {
            (table.name, fk.parent.name)
            for table in Base.metadata.sorted_tables
            for fk in table.foreign_keys
            if fk.column.table.name == "users" and fk.column.name == "id" and fk.ondelete is None
        }
    )


def _rows_holding_user(db: Session, user_id: UUID) -> dict[str, int]:
    """Сколько строк в каждой такой колонке ссылается на профиль."""
    return {
        f"{table}.{column}": int(
            db.execute(
                sa.text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :user_id'), {"user_id": user_id}
            ).scalar_one()
        )
        for table, column in _blocking_user_fk_columns()
    }


def test_blocking_user_references_match_models() -> None:
    """Новый FK на users без ON DELETE обязан попасть в BLOCKING_USER_REFERENCES —
    иначе удаление профиля (и объединение) упрётся в ForeignKeyViolation.

    Что delete_account такие таблицы действительно чистит, проверяет
    test_delete_account_end_to_end: в мире теста есть строка в каждой из них.
    """
    assert {table for table, _column in _blocking_user_fk_columns()} == set(BLOCKING_USER_REFERENCES)


def test_blocking_user_references_match_live_schema(db_session: Session) -> None:
    """То же по живой схеме после миграций: модели и база не разъехались (109)."""
    inspector = sa.inspect(db_session.connection())
    tables: set[str] = set()
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            if fk["referred_table"] != "users":
                continue
            ondelete = (fk.get("options") or {}).get("ondelete")
            if not ondelete or str(ondelete).upper() == "NO ACTION":
                tables.add(table)
    assert tables == set(BLOCKING_USER_REFERENCES)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("удалить", True), ("  УДАЛИТЬ ", True), ("Удалить", True), ("удали", False), ("delete", False), ("", False)],
)
def test_confirmation_word(raw: str, expected: bool) -> None:
    assert account_deletion_service.confirmation_matches(raw) is expected


# ---------------------------------------------------------------------------
# Клиент с настоящей сессией (вход через бота, как test_profile_unlink_api)


class FakeStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        return None

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def public_url(self, key: str) -> str:
        return f"/media/{key}"


@pytest.fixture
def account_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
        admin_telegram_id=ADMIN_TELEGRAM_ID,
        telegram_admin_chat_id=0,
        admin_emails="",
        og_image_dir=str(tmp_path / "og"),
        avatars_dir=str(tmp_path / "avatars"),
    )


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr("app.core.media_storage.get_media_storage", lambda: fake)
    return fake


@pytest.fixture
def admin_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def _record(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr("app.services.admin_notify.notify_admin", _record)
    # Бэклог шлёт админу свои заметки о карточках — сюда их не путаем.
    monkeypatch.setattr("app.services.backlog_service.notify_admin", lambda text: False)
    return sent


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Отмена синка отзывает задачу в Celery, доставка уведомления — ставит её.
    monkeypatch.setattr("app.services.profile_unlink_service._revoke_celery_tasks_for_job", lambda *args: None)
    monkeypatch.setattr("app.services.notification_service.enqueue_delivery", lambda delivery_id: True)
    monkeypatch.setattr("app.services.admin_track_import_service.STAGING_ROOT", tmp_path / "track_imports")


@pytest.fixture
def client(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    account_settings: Settings,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: account_settings
    with patch("app.services.auth_service.check_rate_limit", return_value=True):
        with patch("app.services.sync_trigger_service.enqueue_user_sync"):
            with TestClient(app) as test_client:
                yield test_client
    app.dependency_overrides.clear()


def _login_via_bot(client: TestClient, db: Session, telegram_id: int) -> User:
    login_response = client.post("/api/auth/login-request")
    assert login_response.status_code == 200, login_response.text
    confirm = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": login_response.json()["request_token"],
            "telegram_id": telegram_id,
            "telegram_username": f"deleter_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm.status_code == 200, confirm.text
    callback = client.get(confirm.json()["magic_link"], follow_redirects=False)
    assert callback.status_code in (302, 303, 307), callback.text
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    return db.get(User, UUID(me.json()["id"]))  # type: ignore[return-value]


def _random_telegram_id() -> int:
    return 1_000_000 + uuid4().int % 1_000_000_000


def _make_user(db: Session, name: str) -> User:
    user = User(display_name=name, consent_accepted=True)
    db.add(user)
    db.commit()
    return user


def _make_location(db: Session) -> Location:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(platform_id=platform.id, external_key=f"loc-{uuid4().hex[:10]}", name="Тестовый парк")
    db.add(location)
    db.commit()
    return location


def _link_platform(db: Session, user: User) -> PlatformLink:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one()
    external_user_id = str(uuid4().int % 1_000_000_000)
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name=f"Бегун {external_user_id}",
        profile_url=f"https://example.test/{external_user_id}/",
    )
    db.add(participant)
    db.flush()
    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=external_user_id,
        external_url=participant.profile_url,
    )
    db.add(link)
    db.commit()
    return link


def _add_track(
    db: Session,
    user: User,
    location: Location,
    *,
    started_at: datetime = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc),
    distance_m: float = 5000.0,
    points: list[list[float]] | None = None,
) -> RunTrack:
    track = RunTrack(
        user_id=user.id,
        location_id=location.id,
        source="gpx",
        source_ref=f"ref-{uuid4().hex}",
        started_at=started_at,
        distance_m=distance_m,
        points=points or [],
        status="ok",
        is_course_eligible=True,
        course_version=1,
    )
    db.add(track)
    db.commit()
    return track


@dataclass
class World:
    user: User
    other: User
    third: User
    own_card_id: UUID
    own_comment_id: UUID
    other_card_id: UUID
    link: PlatformLink
    participant_id: UUID
    sync_job_id: UUID
    rating_id: UUID
    lonely_location: Location
    shared_location: Location
    batch_id: UUID
    # Заявки на сбор профиля по статусу, с которым они были созданы.
    fetch_ids: dict[ProfileFetchPendingStatus, UUID]


def _build_world(db: Session, user: User, settings: Settings) -> World:
    """Профиль со всем, что у человека бывает, плюс чужие строки рядом."""
    other = _make_user(db, "Соседка")
    third = _make_user(db, "Сосед")

    # Бэклог: своя карточка с чужими голосами и ответами, свой голос и
    # комментарий под чужой карточкой.
    own_card = create_card(
        db,
        author_id=user.id,
        type_=BacklogCardType.feature,
        category="other",
        title="Моя идея",
        description="Описание",
        is_anonymous=False,
    )
    vote_card(db, own_card.id, user_id=other.id, value=1)
    vote_card(db, own_card.id, user_id=third.id, value=-1)
    create_comment(db, own_card.id, author_id=other.id, body="Поддерживаю", is_anonymous=False)
    other_card = create_card(
        db,
        author_id=other.id,
        type_=BacklogCardType.bug,
        category="other",
        title="Чужая идея",
        description="Описание",
        is_anonymous=False,
    )
    vote_card(db, other_card.id, user_id=user.id, value=1)
    vote_card(db, other_card.id, user_id=third.id, value=1)
    own_comment = create_comment(db, other_card.id, author_id=user.id, body="И я за", is_anonymous=False)

    link = _link_platform(db, user)
    assert link.participant_id is not None
    job = SyncJob(
        user_id=user.id,
        platform_link_id=link.id,
        trigger=SyncJobTrigger.manual,
        status=SyncJobStatus.queued,
    )
    db.add(job)
    db.add(DashboardCache(user_id=user.id, stats={"runs": 3}))

    lonely = _make_location(db)
    shared = _make_location(db)
    rating = LocationRating(
        user_id=user.id,
        location_id=lonely.id,
        location_key=f"five_verst:{lonely.external_key}",
        event_date=date(2026, 9, 5),
        platform_code="five_verst",
        score_overall=5,
        comment="Отличный парк",
    )
    db.add(rating)
    db.flush()
    db.add(
        LocationRatingPhoto(
            rating_id=rating.id,
            storage_key=f"location-reviews/park/{rating.id}/photo.jpg",
            width=10,
            height=10,
            byte_size=100,
        )
    )
    db.add(UserGoal(user_id=user.id, year=2026, goal_type="runs_total", target_value=30))
    db.add(LocationOrganizerAccess(user_id=user.id, location_key="catalog:999999"))
    # Заявки на сбор профиля во всех состояниях. Каждую незавершённую очередь
    # иначе подобрала бы сама: pending — сразу, failed без метки «навсегда» —
    # reset_failed_pending, processing — requeue_stuck_processing_pending.
    fetches = {
        ProfileFetchPendingStatus.pending: ProfileFetchPending(
            user_id=user.id,
            platform_code="parkrun",
            profile_input="https://www.parkrun.org.uk/parkrunner/123/",
            status=ProfileFetchPendingStatus.pending,
            reason=ProfileFetchPendingReason.cooldown,
            cooldown_until=datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
        ),
        ProfileFetchPendingStatus.failed: ProfileFetchPending(
            user_id=user.id,
            platform_code="s95",
            profile_input="https://s95.ru/athletes/456",
            status=ProfileFetchPendingStatus.failed,
            reason=ProfileFetchPendingReason.timeout,
            last_error="Read timed out",
            attempts=5,
        ),
        ProfileFetchPendingStatus.processing: ProfileFetchPending(
            user_id=user.id,
            platform_code="parkrun",
            profile_input="https://www.parkrun.org.uk/parkrunner/789/",
            status=ProfileFetchPendingStatus.processing,
            reason=ProfileFetchPendingReason.error,
        ),
        ProfileFetchPendingStatus.done: ProfileFetchPending(
            user_id=user.id,
            platform_code="s95",
            profile_input="https://s95.ru/athletes/321",
            status=ProfileFetchPendingStatus.done,
            reason=ProfileFetchPendingReason.error,
            processed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    }
    db.add_all(fetches.values())
    db.commit()

    # Треки: на одной локации только свои (паспорт снимается целиком), на
    # другой есть чужой (паспорт пересобирается без нашего).
    _add_track(db, user, lonely)
    _add_track(db, user, shared)
    _add_track(db, other, shared)
    for location in (lonely, shared):
        db.add(
            LocationCourseProfile(
                location_id=location.id,
                course_version=1,
                is_current=True,
                tracks_count=2,
                unique_user_count=2,
                geometry=[[55.0, 37.0], [55.1, 37.1]],
            )
        )
    batch = AdminTrackImport(target_user_id=user.id, created_by_user_id=other.id, source_kind="archive")
    db.add(batch)
    db.commit()
    from app.services import admin_track_import_service

    batch_dir = admin_track_import_service.STAGING_ROOT / str(batch.id)
    batch_dir.mkdir(parents=True)
    (batch_dir / "run.gpx").write_text("<gpx/>")

    # Аватарка, OG-картинка профиля и уведомления — после бэклога, чтобы
    # создание карточек не разослало человеку уведомлений раньше времени.
    user.avatar_path = "avatars/2026/10/preview.jpg"
    user.avatar_full_path = "avatars/2026/10/full.jpg"
    db.add(UserNotificationPrefs(user_id=user.id))
    db.add(UserNotificationChannel(user_id=user.id, channel="telegram", external_id=str(user.telegram_id)))
    db.commit()
    og_dir = Path(settings.og_image_dir) / "users"
    og_dir.mkdir(parents=True, exist_ok=True)
    (og_dir / f"{user.serial_id}.png").write_bytes(b"png")

    return World(
        user=user,
        other=other,
        third=third,
        own_card_id=own_card.id,
        own_comment_id=own_comment.id,
        other_card_id=other_card.id,
        link=link,
        participant_id=link.participant_id,
        sync_job_id=job.id,
        rating_id=rating.id,
        lonely_location=lonely,
        shared_location=shared,
        batch_id=batch.id,
        fetch_ids={status: row.id for status, row in fetches.items()},
    )


def _count(db: Session, model: Any, *criteria: Any) -> int:
    return int(db.query(model).filter(*criteria).count())


# ---------------------------------------------------------------------------
# Ручки


def test_preview_and_delete_require_auth(client: TestClient) -> None:
    assert client.get("/api/account/deletion-preview").status_code == 401
    assert client.post("/api/account/delete", json={"confirmation": "удалить"}).status_code == 401


def test_delete_rejects_wrong_word(client: TestClient, db_session: Session) -> None:
    user = _login_via_bot(client, db_session, _random_telegram_id())
    response = client.post("/api/account/delete", json={"confirmation": "удали"})
    assert response.status_code == 400
    assert "удалить" in response.json()["detail"]
    db_session.expire_all()
    assert db_session.get(User, user.id) is not None
    assert client.get("/api/auth/me").status_code == 200


def test_admin_cannot_delete_own_profile(client: TestClient, db_session: Session) -> None:
    user = _login_via_bot(client, db_session, ADMIN_TELEGRAM_ID)
    response = client.post("/api/account/delete", json={"confirmation": "удалить"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Профиль администратора удалить нельзя"
    db_session.expire_all()
    assert db_session.get(User, user.id) is not None


def test_preview_counts(client: TestClient, db_session: Session, account_settings: Settings) -> None:
    user = _login_via_bot(client, db_session, _random_telegram_id())
    _build_world(db_session, user, account_settings)

    response = client.get("/api/account/deletion-preview")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["platforms"] == ["5 вёрст"]
    assert preview["auth_providers"] == ["Telegram"]
    assert preview["location_reviews"] == 1
    assert preview["review_photos"] == 1
    assert preview["goals"] == 1
    assert preview["run_tracks"] == 2
    assert preview["backlog_cards"] == 1
    assert preview["backlog_comments"] == 1
    assert preview["backlog_votes"] == 1
    assert preview["organizer_grants"] == 1
    assert preview["has_avatar"] is True


def test_delete_account_end_to_end(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    account_settings: Settings,
    storage: FakeStorage,
    admin_messages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipts: list[AccountDeletionReceipt] = []
    finalize = account_deletion_service.finalize_account_deletion

    def _finalize_spy(settings: Settings, receipt: AccountDeletionReceipt) -> None:
        # Уборка идёт фоном после ответа: сессия к этому моменту уже погашена.
        assert list(fake_redis.scan_iter("session:*")) == []
        receipts.append(receipt)
        finalize(settings, receipt)

    monkeypatch.setattr("app.api.routes.account.finalize_account_deletion", _finalize_spy)

    telegram_id = _random_telegram_id()
    user = _login_via_bot(client, db_session, telegram_id)
    world = _build_world(db_session, user, account_settings)
    user_id, serial_id = user.id, user.serial_id
    # Каждая таблица, которая держит DELETE FROM users, в мире теста занята:
    # новая такая таблица без строки здесь — повод дописать _build_world, а
    # без шага в delete_account удаление упадёт на ForeignKeyViolation.
    held = _rows_holding_user(db_session, user_id)
    assert held and all(count >= 1 for count in held.values()), held
    # Вход через бота оставил одноразовый токен и заявку — их FK держали
    # удаление профиля при объединении (test_account_merge).
    assert _count(db_session, AuthOneTimeToken, AuthOneTimeToken.user_id == user_id) >= 1
    assert _count(db_session, AuthLoginRequest, AuthLoginRequest.telegram_id == telegram_id) >= 1
    # Личные следы в Redis.
    assert fake_redis.get(f"abuse:user:ip:{telegram_id}") is not None
    fake_redis.set(user_location_records_cache_key(user_id), "{}")
    session_cookie = client.cookies.get(account_settings.session_cookie_name)
    assert session_cookie
    assert client.cookies.get(account_settings.known_device_cookie_name) == "1"

    response = client.post("/api/account/delete", json={"confirmation": " Удалить "})
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "deleted"}

    # Обе куки стёрты.
    set_cookies = response.headers.get_list("set-cookie")
    for name in (account_settings.session_cookie_name, account_settings.known_device_cookie_name):
        assert any(item.startswith(f"{name}=") and "Max-Age=0" in item for item in set_cookies), set_cookies
    # Та же кука с другого «устройства» больше никуда не пускает.
    client.cookies.set(account_settings.session_cookie_name, session_cookie)
    assert client.get("/api/auth/me").status_code == 401
    assert [key for key in fake_redis.scan_iter("session:*")] == []

    db_session.expire_all()
    assert db_session.get(User, user_id) is None
    assert set(_rows_holding_user(db_session, user_id).values()) == {0}
    # Личное — удалено.
    assert _count(db_session, AuthIdentity, AuthIdentity.user_id == user_id) == 0
    assert _count(db_session, PlatformLink, PlatformLink.user_id == user_id) == 0
    assert _count(db_session, SyncJob, SyncJob.user_id == user_id) == 0
    assert db_session.get(SyncJob, world.sync_job_id) is None
    assert _count(db_session, DashboardCache, DashboardCache.user_id == user_id) == 0
    assert _count(db_session, AuthOneTimeToken, AuthOneTimeToken.user_id == user_id) == 0
    assert _count(db_session, AuthLoginRequest, AuthLoginRequest.telegram_id == telegram_id) == 0
    assert db_session.get(LocationRating, world.rating_id) is None
    assert _count(db_session, LocationRatingPhoto, LocationRatingPhoto.rating_id == world.rating_id) == 0
    assert _count(db_session, UserGoal, UserGoal.user_id == user_id) == 0
    assert _count(db_session, LocationOrganizerAccess, LocationOrganizerAccess.user_id == user_id) == 0
    assert _count(db_session, RunTrack, RunTrack.user_id == user_id) == 0
    assert db_session.get(AdminTrackImport, world.batch_id) is None
    assert _count(db_session, UserNotificationPrefs, UserNotificationPrefs.user_id == user_id) == 0
    assert _count(db_session, UserNotificationChannel, UserNotificationChannel.user_id == user_id) == 0
    assert _count(db_session, NotificationDelivery, NotificationDelivery.user_id == user_id) == 0
    assert _count(db_session, BacklogCardSubscription, BacklogCardSubscription.user_id == user_id) == 0
    assert _count(db_session, BacklogVote, BacklogVote.user_id == user_id) == 0

    # Заявки на сбор профиля: незавершённые закрыты навсегда — ни старт демона,
    # ни подбор «зависших в работе» их не воскрешает, а прогон, который уже
    # держит такую строку, её пропускает. Готовая осталась как была.
    assert len(receipts) == 1 and receipts[0].fetch_requests_closed == 3
    for platform_code in ("parkrun", "s95"):
        reset_failed_pending(db_session, platform_code)
        requeue_stuck_processing_pending(db_session, platform_code)
    db_session.expire_all()
    for status in (
        ProfileFetchPendingStatus.pending,
        ProfileFetchPendingStatus.failed,
        ProfileFetchPendingStatus.processing,
    ):
        fetch = db_session.get(ProfileFetchPending, world.fetch_ids[status])
        assert fetch is not None, status
        assert (fetch.status, fetch.last_error, fetch.cooldown_until, fetch.user_id) == (
            ProfileFetchPendingStatus.failed,
            "[permanent] профиль удалён",
            None,
            None,
        ), status
        assert process_pending_row(db_session, fetch) == "skipped_status"
    done_fetch = db_session.get(ProfileFetchPending, world.fetch_ids[ProfileFetchPendingStatus.done])
    assert done_fetch is not None
    assert (done_fetch.status, done_fetch.last_error, done_fetch.user_id) == (
        ProfileFetchPendingStatus.done,
        None,
        None,
    )

    # Протокол — не его: участник остаётся, просто без профиля на сайте.
    assert db_session.get(Participant, world.participant_id) is not None

    # Бэклог: написанное остаётся без автора, счётчики честные.
    own_card = db_session.get(BacklogCard, world.own_card_id)
    assert own_card is not None and own_card.author_user_id is None
    assert (own_card.upvotes, own_card.downvotes, own_card.score) == (1, 1, 0)
    assert _count(db_session, BacklogComment, BacklogComment.card_id == world.own_card_id) == 1
    own_comment = db_session.get(BacklogComment, world.own_comment_id)
    assert own_comment is not None and own_comment.author_user_id is None
    other_card = db_session.get(BacklogCard, world.other_card_id)
    assert other_card is not None and other_card.author_user_id == world.other.id
    assert (other_card.upvotes, other_card.downvotes, other_card.score) == (1, 0, 1)
    # Чужое не задето.
    assert db_session.get(User, world.other.id) is not None
    assert db_session.get(User, world.third.id) is not None
    assert _count(db_session, BacklogVote, BacklogVote.user_id == world.other.id) == 1
    assert _count(db_session, RunTrack, RunTrack.user_id == world.other.id) == 1

    # Паспорта трасс: без чужих треков — снят, с чужим — пересобран без нашего.
    assert _count(
        db_session, LocationCourseProfile, LocationCourseProfile.location_id == world.lonely_location.id
    ) == 0
    shared_profile = (
        db_session.query(LocationCourseProfile)
        .filter(LocationCourseProfile.location_id == world.shared_location.id)
        .one()
    )
    assert (shared_profile.tracks_count, shared_profile.unique_user_count) == (1, 1)

    # Файлы и Redis — после коммита.
    assert sorted(storage.deleted) == sorted(
        [
            "avatars/2026/10/preview.jpg",
            "avatars/2026/10/full.jpg",
            f"location-reviews/park/{world.rating_id}/photo.jpg",
        ]
    )
    assert not (Path(account_settings.og_image_dir) / "users" / f"{serial_id}.png").exists()
    from app.services import admin_track_import_service

    assert not (admin_track_import_service.STAGING_ROOT / str(world.batch_id)).exists()
    assert fake_redis.get(f"abuse:user:ip:{telegram_id}") is None
    assert fake_redis.get(user_location_records_cache_key(user_id)) is None

    # Заметка админу — без почты, с тем, что стало с бэклогом.
    assert len(admin_messages) == 1
    note = admin_messages[0]
    assert note.startswith("🗑 Профиль удалён:")
    assert f"#{serial_id}" in note
    assert "5 вёрст" in note
    assert "Привязки: 5 вёрст" in note
    assert "карточек 1, комментариев 1 — остались без автора" in note


def _line(lat: float, lon: float) -> list[list[float]]:
    """Прямая около километра на север: у каждого трека своя, узнаваемая."""
    return [[round(lat + step * 0.0005, 6), round(lon, 6)] for step in range(20)]


def test_delete_account_rebuilds_every_course_version(db_session: Session) -> None:
    """Версия трассы только из треков удалённого снимается, остальные — без них.

    Мир: соседка трижды пробежала прежнюю трассу (v1), уходящий — один раз её
    же (его трек медианный по длине, линия v1 — его) и трижды новую в другом
    месте, из чего и сложилась текущая v2. После удаления у соседки только
    v1, и в нём ничего от него. rebuild_location_profile здесь сверил бы три
    её трека с эталоном v2, счёл бы трассу снова сменённой и перевёл их в v3.
    """
    leaver = _make_user(db_session, "Уходящий")
    neighbour = _make_user(db_session, "Соседка")
    location = _make_location(db_session)

    def day(number: int) -> datetime:
        return datetime(2026, 9, number, 9, 0, tzinfo=timezone.utc)

    neighbour_lines = [_line(56.0, 37.0 + index * 0.001) for index in range(3)]
    neighbour_tracks = [
        _add_track(
            db_session, neighbour, location, started_at=day(5 + index), distance_m=5000 + index * 100, points=line
        )
        for index, line in enumerate(neighbour_lines)
    ]
    leaver_old_line = _line(56.0, 37.003)
    _add_track(db_session, leaver, location, started_at=day(10), distance_m=5150, points=leaver_old_line)
    location_course_service.rebuild_location_profile(db_session, location.id)
    for index in range(3):
        _add_track(db_session, leaver, location, started_at=day(20 + index), points=_line(56.5, 37.5 + index * 0.001))
    location_course_service.rebuild_location_profile(db_session, location.id)
    db_session.commit()

    # Исходное состояние такое, как задумано: текущая v2 целиком из треков
    # уходящего, а линия истории v1 — его старый трек.
    before = {item.course_version: item for item in location_course_service.profile_history(db_session, location.id)}
    assert sorted(before) == [1, 2]
    assert before[2].is_current and (before[2].tracks_count, before[2].unique_user_count) == (3, 1)
    assert not before[1].is_current and (before[1].tracks_count, before[1].unique_user_count) == (4, 2)
    assert before[1].geometry == leaver_old_line

    receipt = account_deletion_service.delete_account(db_session, leaver)
    db_session.commit()
    assert (receipt.course_profiles_rebuilt, receipt.course_profiles_removed) == (1, 1)

    db_session.expire_all()
    after = location_course_service.profile_history(db_session, location.id)
    assert [(item.course_version, item.is_current) for item in after] == [(1, True)]
    profile = after[0]
    assert (profile.tracks_count, profile.unique_user_count) == (3, 1)
    # Линия — медианный по длине трек соседки, отпечаток и даты — только её.
    assert profile.geometry == neighbour_lines[1]
    expected_cells: set[str] = set()
    for line in neighbour_lines:
        expected_cells |= location_course_service._cells(line)
    assert set(profile.geometry_cells) == expected_cells
    assert (profile.first_track_at, profile.last_track_at) == (day(5), day(7))
    assert profile.distance_m == 5100.0
    # Чужим трекам версия не переписана.
    for track in neighbour_tracks:
        db_session.refresh(track)
        assert track.course_version == 1


def test_backlog_renders_after_author_deleted(
    client: TestClient,
    db_session: Session,
    account_settings: Settings,
    storage: FakeStorage,
    admin_messages: list[str],
) -> None:
    user = _login_via_bot(client, db_session, _random_telegram_id())
    world = _build_world(db_session, user, account_settings)
    assert client.post("/api/account/delete", json={"confirmation": "удалить"}).status_code == 200
    client.cookies.clear()

    listing = client.get("/api/backlog/cards")
    assert listing.status_code == 200, listing.text
    card = next(item for item in listing.json()["items"] if item["id"] == str(world.own_card_id))
    assert card["author_deleted"] is True
    assert card["author_display_name"] is None
    assert card["author_handle"] is None
    assert card["author_avatar_url"] is None

    single = client.get(f"/api/backlog/cards/{world.own_card_id}")
    assert single.status_code == 200
    assert single.json()["author_deleted"] is True

    thread = client.get(f"/api/backlog/cards/{world.other_card_id}/comments")
    assert thread.status_code == 200, thread.text
    orphan = next(item for item in thread.json()["items"] if item["id"] == str(world.own_comment_id))
    assert orphan["author_deleted"] is True
    assert orphan["author_handle"] is None
    assert orphan["is_admin_author"] is False

    db_session.expire_all()
    admin_card = next(item for item in list_cards_admin(db_session) if item.id == world.own_card_id)
    assert admin_card.author_deleted is True
    assert admin_card.author_display_name is None


def test_stale_session_of_missing_user_is_dropped(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    account_settings: Settings,
) -> None:
    """Сессии других устройств удалённого профиля гаснут на первом запросе."""
    for path in ("/api/auth/me", "/api/backlog/cards"):
        ghost = _make_user(db_session, "Призрак")
        signed = create_session(account_settings, str(ghost.id))
        assert len(list(fake_redis.scan_iter("session:*"))) == 1
        db_session.execute(sa.delete(User).where(User.id == ghost.id))
        db_session.commit()
        db_session.expunge(ghost)

        client.cookies.set(account_settings.session_cookie_name, signed)
        response = client.get(path)
        # /auth/me — get_current_user (401), лента бэклога — get_optional_user (гость).
        assert response.status_code == (401 if path == "/api/auth/me" else 200), response.text
        assert list(fake_redis.scan_iter("session:*")) == []
        client.cookies.clear()


# ---------------------------------------------------------------------------
# Очередь сбора профилей: заявку, закрытую удалением посреди работы, демон
# не возвращает в очередь (повторное ревью 13.10.2026)


def _make_fetch(db: Session, user: User) -> ProfileFetchPending:
    row = ProfileFetchPending(
        user_id=user.id,
        platform_code="parkrun",
        profile_input=f"https://www.parkrun.org.uk/parkrunner/{uuid4().int % 10_000_000}/",
        status=ProfileFetchPendingStatus.pending,
        reason=ProfileFetchPendingReason.error,
    )
    db.add(row)
    db.commit()
    return row


def _assert_fetch_stays_closed(db: Session, row_id: UUID) -> None:
    for _ in range(2):
        db.expire_all()
        row = db.get(ProfileFetchPending, row_id)
        assert row is not None
        assert row.status == ProfileFetchPendingStatus.failed
        assert row.last_error == ACCOUNT_DELETED_ERROR
        assert row.user_id is None
        # Ни перезапуск неудач, ни подбор зависших не возвращают её в очередь.
        reset_failed_pending(db, "parkrun")
        requeue_stuck_processing_pending(db, "parkrun")


def _no_fetch(*_args: Any, **_kwargs: Any) -> str:
    raise AssertionError("закрытая удалением заявка не должна собираться")


def test_fetch_closed_while_in_flight_is_not_requeued(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбор уже идёт, а человек удаляет профиль: сбор падает без хозяина
    («User not found» / FK на platform_links), и обычная ветка ошибки вернула
    бы строку в pending — откуда она ушла бы анонимным сбором."""
    user = _make_user(db_session, "Уходящий посреди сбора")
    row = _make_fetch(db_session, user)
    row_id, user_id = row.id, user.id

    def _delete_then_fail(db: Session, *_args: Any) -> str:
        doomed = db.get(User, user_id)
        assert doomed is not None
        account_deletion_service.delete_account(db, doomed)
        db.commit()
        raise RuntimeError("User not found")

    monkeypatch.setattr(
        "app.services.profile_fetch_pending_service._complete_pending_profile_link", _delete_then_fail
    )
    monkeypatch.setattr("app.services.profile_fetch_pending_service._persist_profile_preview", _no_fetch)
    with patch("app.services.profile_fetch_pending_service.is_platform_in_cooldown", return_value=False):
        assert process_pending_row(db_session, row) == "skipped_status"
    _assert_fetch_stays_closed(db_session, row_id)


def test_fetch_closed_just_before_pickup_is_not_fetched(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Профиль удалили между тем, как демон прочитал пачку, и его «processing»:
    демон перезаписал бы «failed», а user_id уже пуст — анонимный сбор в том
    же прогоне."""
    from sqlalchemy.orm.attributes import set_committed_value

    user = _make_user(db_session, "Уходящий до подбора")
    row = _make_fetch(db_session, user)
    row_id, user_id = row.id, user.id
    account_deletion_service.delete_account(db_session, user)
    db_session.commit()
    # Демон держит строку такой, какой прочитал её до удаления.
    db_session.refresh(row)
    set_committed_value(row, "status", ProfileFetchPendingStatus.pending)
    set_committed_value(row, "user_id", user_id)

    # Сбор подменён удачным: брось он ошибку, её поймала бы ветка отката и
    # строка закрылась бы и без проверки перед сбором.
    fetched: list[str] = []

    def _fetch_ok(*_args: Any, **_kwargs: Any) -> str:
        fetched.append("fetched")
        return "123"

    monkeypatch.setattr("app.services.profile_fetch_pending_service._complete_pending_profile_link", _fetch_ok)
    monkeypatch.setattr("app.services.profile_fetch_pending_service._persist_profile_preview", _fetch_ok)
    with patch("app.services.profile_fetch_pending_service.is_platform_in_cooldown", return_value=False):
        assert process_pending_row(db_session, row) == "skipped_status"
    assert fetched == []
    _assert_fetch_stays_closed(db_session, row_id)


# ---------------------------------------------------------------------------
# Исход попытки для браузера, до которого не дошёл ответ (повторное ревью
# 13.10.2026): по одному 401 «удалила эта попытка» не отличить от «вышли на
# другой вкладке»


def test_deletion_status_by_attempt_id(client: TestClient, db_session: Session) -> None:
    attempt = uuid4().hex
    other = uuid4().hex
    assert client.get("/api/account/deletion-status", params={"attempt": attempt}).json() == {"deleted": False}

    _login_via_bot(client, db_session, _random_telegram_id())
    response = client.post("/api/account/delete", json={"confirmation": "удалить", "attempt_id": attempt})
    assert response.status_code == 200, response.text

    # Сессии уже нет — статус отвечает без входа, и только про свою попытку.
    client.cookies.clear()
    assert client.get("/api/account/deletion-status", params={"attempt": attempt}).json() == {"deleted": True}
    assert client.get("/api/account/deletion-status", params={"attempt": other}).json() == {"deleted": False}


def test_deletion_status_is_not_set_by_a_refused_attempt(client: TestClient, db_session: Session) -> None:
    attempt = uuid4().hex
    _login_via_bot(client, db_session, _random_telegram_id())
    refused = client.post("/api/account/delete", json={"confirmation": "удали", "attempt_id": attempt})
    assert refused.status_code == 400
    assert client.get("/api/account/deletion-status", params={"attempt": attempt}).json() == {"deleted": False}


@pytest.mark.parametrize("attempt", ["short", "x" * 65, "bad id with spaces!!", "../../etc/passwd-0000"])
def test_deletion_attempt_id_is_validated(client: TestClient, attempt: str) -> None:
    assert client.get("/api/account/deletion-status", params={"attempt": attempt}).status_code == 422
    assert (
        client.post("/api/account/delete", json={"confirmation": "удалить", "attempt_id": attempt}).status_code
        in (401, 422)
    )


def test_ownerless_enqueue_does_not_reopen_fresh_deletion_closure(db_session: Session) -> None:
    """Импорт активности ловит бан посреди обработки строки и сам ставит её в
    очередь (без владельца) — поверх только что закрытой удалением заявки.
    Свежую закрытую заявку такой вызов не переоткрывает; через час — обычная
    заявка (кто-то нашёл этот публичный профиль в поиске)."""
    user = _make_user(db_session, "Уходящий при бане")
    row = _make_fetch(db_session, user)
    row_id = row.id
    account_deletion_service.delete_account(db_session, user)
    db_session.commit()

    ban = RuntimeError("ban/protection")
    reopened = enqueue_profile_fetch_pending(
        db_session, platform_code="parkrun", profile_input=row.profile_input, user_id=None, exc=ban
    )
    db_session.commit()
    assert reopened.id == row_id
    _assert_fetch_stays_closed(db_session, row_id)

    closed = db_session.get(ProfileFetchPending, row_id)
    assert closed is not None
    closed.updated_at = datetime.now(timezone.utc) - ACCOUNT_DELETED_REOPEN_GUARD * 2
    db_session.commit()
    later = enqueue_profile_fetch_pending(
        db_session, platform_code="parkrun", profile_input=closed.profile_input, user_id=None, exc=ban
    )
    db_session.commit()
    assert later.id == row_id
    assert later.status == ProfileFetchPendingStatus.pending
