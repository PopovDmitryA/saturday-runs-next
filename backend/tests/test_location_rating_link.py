"""Вкладка «Оценка» локации (/locations/<slug>/rate): что оценить и почему нельзя.

Организатор кидает ссылку на вкладку в чат локации, и по ней каждый должен
либо сразу найти свой старт для оценки, либо прочитать конкретную причину,
почему оценить нельзя. Правила оценки не дублируются: ответ строится поверх
list_eligible_runs, тесты стерегут разбор причин и выбор старта.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    LocationRating,
    Participant,
    Platform,
    PlatformLink,
    PlatformLinkSyncStatus,
    RunResult,
    SyncJob,
    SyncJobStatus,
    SyncJobTrigger,
    User,
    VolunteerResult,
)
from app.schemas.rating import LocationRatingStatusResponse
from app.services.location_page_service import resolve_location_identity
from app.services.rating_service import (
    MIN_RUNS_TO_RATE,
    RATE_LINK_CAN_RATE,
    RATE_LINK_FEW_RUNS,
    RATE_LINK_NO_PROFILE,
    RATE_LINK_NOT_HERE,
    RATE_LINK_PARKRUN_HISTORY,
    RATE_LINK_RATED,
    RATE_LINK_RECENT_DAYS,
    RATE_LINK_SYNC_GRACE,
    RATE_LINK_SYNCING,
    RATE_LINK_TOO_OLD,
    RATING_WINDOW_DAYS,
    list_eligible_runs,
    location_rating_status,
)

TODAY = date.today()


def _platform(db: Session, code: str) -> Platform:
    row = db.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        pytest.skip(f"{code} platform not seeded")
    return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _location(db: Session, platform: Platform, name: str = "Оценочная") -> Location:
    slug = f"ratelink{uuid4().hex[:10]}"
    location = Location(platform_id=platform.id, external_key=slug, name=name, city="Город")
    db.add(location)
    db.flush()
    return location


def _user(db: Session) -> User:
    user = User(display_name="Бегун по ссылке", consent_accepted=True)
    db.add(user)
    db.flush()
    return user


def _link(
    db: Session,
    user: User,
    platform: Platform,
    *,
    synced: bool = True,
    linked_at: datetime | None = None,
    sync_status: PlatformLinkSyncStatus = PlatformLinkSyncStatus.ok,
) -> Participant:
    """Привязка с участником. По умолчанию история уже загружена (синк прошёл):
    иначе свежая привязка с идущим синком читается как «история ещё грузится»."""
    participant = Participant(
        platform_id=platform.id,
        external_user_id=str(800000 + uuid4().int % 100000),
        display_name="Бегун по ссылке",
    )
    db.add(participant)
    db.flush()
    db.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=f"https://example.org/{participant.external_user_id}",
            last_user_sync_at=_now() if synced else None,
            linked_at=linked_at or _now(),
            sync_status=sync_status,
        )
    )
    db.flush()
    return participant


def _queue_sync(db: Session, user: User, participant: Participant) -> None:
    """Задача синка привязки ждёт воркера: привязка ставит её, не трогая статус."""
    link = db.query(PlatformLink).filter(PlatformLink.participant_id == participant.id).one()
    db.add(
        SyncJob(
            user_id=user.id,
            platform_link_id=link.id,
            trigger=SyncJobTrigger.linking,
            status=SyncJobStatus.queued,
        )
    )
    db.flush()


def _event(db: Session, location: Location, day: date) -> Event:
    event = Event(
        platform_id=location.platform_id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{day.isoformat()}:{uuid4().hex[:6]}",
        event_date=day,
        event_number=1,
    )
    db.add(event)
    db.flush()
    return event


def _run(db: Session, participant: Participant, location: Location, day: date) -> RunResult:
    event = _event(db, location, day)
    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"{event.external_event_key}:{participant.external_user_id}",
        position=12,
        finish_time_sec=1500,
        finish_time_display="00:25:00",
    )
    db.add(run)
    db.flush()
    return run


def _volunteer(db: Session, participant: Participant, location: Location, day: date) -> VolunteerResult:
    event = _event(db, location, day)
    vol = VolunteerResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"{event.external_event_key}:vol:{participant.external_user_id}",
        role="Секундомер",
    )
    db.add(vol)
    db.flush()
    return vol


def _history(db: Session, participant: Participant, platform: Platform, count: int = MIN_RUNS_TO_RATE) -> None:
    """Пробежки на чужой локации давным-давно: порог права оценивать, а не визит."""
    elsewhere = _location(db, platform, name="Где-то ещё")
    for shift in range(count):
        _run(db, participant, elsewhere, TODAY - timedelta(days=400 + 7 * shift))


def _rate(
    db: Session,
    user: User,
    location: Location,
    run: RunResult,
    day: date,
    *,
    platform_code: str = "five_verst",
) -> LocationRating:
    rating = LocationRating(
        user_id=user.id,
        run_result_id=run.id,
        participation_type="run",
        location_id=location.id,
        location_key=f"location:{location.id}",
        event_date=day,
        platform_code=platform_code,
        score_overall=5,
    )
    db.add(rating)
    db.flush()
    return rating


def _status(db: Session, user: User, location: Location) -> dict[str, object]:
    return location_rating_status(
        db, user.id, identity_key=f"location:{location.id}", location_ids=[location.id]
    )


def test_no_linked_profile(db_session: Session) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    result = _status(db_session, _user(db_session), location)
    assert result["status"] == RATE_LINK_NO_PROFILE
    assert result["run"] is None


def test_too_few_runs_tells_how_many(db_session: Session) -> None:
    """Порог — по всей истории: показываем, сколько есть и сколько нужно."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five, count=2)
    _run(db_session, participant, location, TODAY - timedelta(days=3))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_FEW_RUNS
    assert result["total_runs"] == 3
    assert result["min_runs_required"] == MIN_RUNS_TO_RATE
    assert result["last_visit_date"] == TODAY - timedelta(days=3)
    assert result["run"] is None


def test_fresh_link_is_loading_not_few_runs(db_session: Session) -> None:
    """Новичок из чата привязал профиль в онбординге и вернулся по ссылке раньше,
    чем доехала его история: не «у вас 0 пробежек», а «загружаем историю»."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    _link(db_session, user, five, synced=False, sync_status=PlatformLinkSyncStatus.syncing)

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_SYNCING
    assert result["run"] is None


def test_new_system_loading_instead_of_not_here(db_session: Session) -> None:
    """Пробежек хватает, а здесь человек бегал в системе, которую только что
    привязал: «не нашли ваших стартов» было бы неправдой ещё несколько минут."""
    five = _platform(db_session, "five_verst")
    s95 = _platform(db_session, "s95")
    location = _location(db_session, s95)
    user = _user(db_session)
    _history(db_session, _link(db_session, user, five), five)
    s95_participant = _link(
        db_session, user, s95, synced=False, sync_status=PlatformLinkSyncStatus.idle
    )
    _queue_sync(db_session, user, s95_participant)

    assert _status(db_session, user, location)["status"] == RATE_LINK_SYNCING


@pytest.mark.parametrize(
    ("linked_days_ago", "sync_status"),
    [
        # Привязки, застрявшие в syncing с сентября, на проде есть: это не загрузка.
        (RATE_LINK_SYNC_GRACE.days + 1, PlatformLinkSyncStatus.syncing),
        # Ошибка синка сама не пройдёт — ждать её бесполезно.
        (0, PlatformLinkSyncStatus.error),
        # Привязка из поиска без синка (parkrun, RunPark дома): ждать нечего.
        (0, PlatformLinkSyncStatus.idle),
    ],
)
def test_stuck_or_failed_link_is_not_loading(
    db_session: Session, linked_days_ago: int, sync_status: PlatformLinkSyncStatus
) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    _link(
        db_session,
        user,
        five,
        synced=False,
        linked_at=_now() - timedelta(days=linked_days_ago),
        sync_status=sync_status,
    )

    assert _status(db_session, user, location)["status"] == RATE_LINK_FEW_RUNS


def test_fresh_run_opens_rating(db_session: Session) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    _run(db_session, participant, location, TODAY - timedelta(days=14))
    fresh = _run(db_session, participant, location, TODAY - timedelta(days=7))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_CAN_RATE
    run = result["run"]
    assert isinstance(run, dict)
    # Самый свежий из доступных, а не первый попавшийся.
    assert run["entry_id"] == f"run:{fresh.id}"
    assert run["my_rating"] is None


@pytest.mark.parametrize(
    ("days_ago", "recent"),
    [(0, True), (RATE_LINK_RECENT_DAYS, True), (RATE_LINK_RECENT_DAYS + 1, False)],
)
def test_recent_flag_on_offered_start(db_session: Session, days_ago: int, recent: bool) -> None:
    """Ссылку кидают в чат сразу после субботы: если протокол дня ещё не
    загружен, свежее прошлой недели ничего нет — такой старт вкладка помечает
    как давний и подсказывает зайти позже."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    _run(db_session, participant, location, TODAY - timedelta(days=days_ago))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_CAN_RATE
    assert result["run_is_recent"] is recent


def test_volunteering_counts_as_a_start(db_session: Session) -> None:
    """Волонтёрил, а не бегал, — оценивает как волонтёр, как и карточка на странице."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    vol = _volunteer(db_session, participant, location, TODAY - timedelta(days=2))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_CAN_RATE
    run = result["run"]
    assert isinstance(run, dict)
    assert run["entry_id"] == f"vol:{vol.id}"
    assert run["volunteer_role"] == "Секундомер"


def test_unrated_older_start_still_opens(db_session: Session) -> None:
    """Свежий старт уже оценён, а прошлый в окне — нет: есть что оценить."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    older = _run(db_session, participant, location, TODAY - timedelta(days=14))
    fresh_date = TODAY - timedelta(days=7)
    fresh = _run(db_session, participant, location, fresh_date)
    _rate(db_session, user, location, fresh, fresh_date)

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_CAN_RATE
    run = result["run"]
    assert isinstance(run, dict)
    assert run["entry_id"] == f"run:{older.id}"
    assert run["is_legacy"] is False
    # По последнему визиту позже старта вкладка понимает, что свежий старт уже
    # оценён, и не советует ждать протокол (ревью 22.10.2026).
    assert result["last_visit_date"] == fresh_date
    assert result["last_visit_date"] > run["event_date"]


def test_already_rated_offers_own_rating(db_session: Session) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    fresh = _run(db_session, participant, location, TODAY - timedelta(days=7))
    rating = _rate(db_session, user, location, fresh, TODAY - timedelta(days=7))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_RATED
    run = result["run"]
    assert isinstance(run, dict)
    my_rating = run["my_rating"]
    assert isinstance(my_rating, dict)
    assert my_rating["id"] == rating.id
    assert my_rating["editable"] is True
    # Неделю назад — уже не «последняя неделя»: вкладка подскажет, что свежий
    # протокол мог ещё не загрузиться.
    assert result["run_is_recent"] is False


def test_rating_without_result_row_is_still_rated(db_session: Session) -> None:
    """Пересинк унёс строку результата, а отзыв остался (миграция 091): человек
    по-прежнему «уже оценил», а не «не был здесь», и может открыть свою оценку."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    day = TODAY - timedelta(days=5)
    run = _run(db_session, participant, location, day)
    rating = _rate(db_session, user, location, run, day)
    rating.run_result_id = None
    db_session.flush()
    db_session.query(RunResult).filter(RunResult.id == run.id).delete()
    db_session.flush()

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_RATED
    assert result["last_visit_date"] is None
    rated = result["run"]
    assert isinstance(rated, dict)
    assert rated["entry_id"] == f"rate:{rating.id}"
    assert result["run_is_recent"] is True


def test_old_start_opens_through_legacy_slot(db_session: Session) -> None:
    """Здесь давно и ни разу не оценивали — добор истории даёт оценить последний старт."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    old = _run(db_session, participant, location, TODAY - timedelta(days=RATING_WINDOW_DAYS + 30))

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_CAN_RATE
    run = result["run"]
    assert isinstance(run, dict)
    assert run["entry_id"] == f"run:{old.id}"
    assert run["is_legacy"] is True
    assert result["run_is_recent"] is False


def test_too_old_when_legacy_slot_is_spent(db_session: Session) -> None:
    """Последний старт вне окна, а старый здесь уже оценён — называем дату и даём
    открыть свою оценку (собранную из «Моих оценок»: в доступных её нет)."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    rated_day = TODAY - timedelta(days=RATING_WINDOW_DAYS + 60)
    last_day = TODAY - timedelta(days=RATING_WINDOW_DAYS + 10)
    rated_run = _run(db_session, participant, location, rated_day)
    rating = _rate(db_session, user, location, rated_run, rated_day)
    _run(db_session, participant, location, last_day)

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_TOO_OLD
    assert result["last_visit_date"] == last_day
    assert result["window_days"] == RATING_WINDOW_DAYS
    run = result["run"]
    assert isinstance(run, dict)
    assert run["entry_id"] == f"run:{rated_run.id}"
    assert run["event_date"] == rated_day
    assert run["location_identity_key"] == f"location:{location.id}"
    my_rating = run["my_rating"]
    assert isinstance(my_rating, dict)
    assert my_rating["id"] == rating.id
    # Карточка из «Моих оценок» проходит ту же схему ответа, что и доступный старт.
    response = LocationRatingStatusResponse.model_validate(result)
    assert response.run is not None and response.run.my_rating is not None
    assert response.run.my_rating.editable is True


def test_rating_under_pre_catalog_key_is_found(db_session: Session) -> None:
    """Локацию связали в каталоге после оценки: идентичность уже «catalog:…»,
    а оценка лежит со старым ключом «location:…» (на проде таких сотни).
    Своя оценка находится по location_id — иначе ссылка ответила бы «давно
    здесь не были» вместо «уже оценили»."""
    five = _platform(db_session, "five_verst")
    s95 = _platform(db_session, "s95")
    catalog = LocationCatalog(canonical_name="Оценочный узел", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    five_location = _location(db_session, five)
    s95_location = _location(db_session, s95)
    for location, platform in ((five_location, five), (s95_location, s95)):
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=platform.id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db_session.flush()
    user = _user(db_session)
    _history(db_session, _link(db_session, user, five), five)
    day = TODAY - timedelta(days=5)
    run = _run(db_session, _link(db_session, user, s95), s95_location, day)
    rating = _rate(db_session, user, s95_location, run, day, platform_code="s95")

    identity = resolve_location_identity(db_session, five_location.external_key)
    assert identity is not None
    assert identity.identity_key == f"catalog:{catalog.id}"
    assert rating.location_key != identity.identity_key
    result = location_rating_status(
        db_session,
        user.id,
        identity_key=identity.identity_key,
        location_ids=[location.id for location, _code in identity.locations],
    )
    assert result["status"] == RATE_LINK_RATED
    rated = result["run"]
    assert isinstance(rated, dict)
    my_rating = rated["my_rating"]
    assert isinstance(my_rating, dict)
    assert my_rating["id"] == rating.id


@pytest.mark.parametrize("stale_key", ["pre_catalog", "old_catalog_node"])
def test_legacy_slot_stays_spent_under_stale_rating_key(
    db_session: Session, stale_key: str
) -> None:
    """Давний старт оценён, а ключ в оценке устарел: локацию связали в каталоге
    после оценки («location:<id>») или узел каталога пересобрали («catalog:» со
    старым id). Слот добора на этой локации всё равно израсходован — оценку
    узнаём по её location_id, — и ссылка честно отвечает «давно не были», а не
    предлагает «оценить давний старт, где вы ещё не оценивали» (ревью 21.10.2026)."""
    five = _platform(db_session, "five_verst")
    s95 = _platform(db_session, "s95")
    catalog = LocationCatalog(canonical_name="Оценочный узел", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    five_location = _location(db_session, five)
    s95_location = _location(db_session, s95)
    for location, platform in ((five_location, five), (s95_location, s95)):
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=platform.id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db_session.flush()
    user = _user(db_session)
    five_participant = _link(db_session, user, five)
    _history(db_session, five_participant, five)
    rated_day = TODAY - timedelta(days=RATING_WINDOW_DAYS + 60)
    last_day = TODAY - timedelta(days=RATING_WINDOW_DAYS + 10)
    rated_run = _run(db_session, _link(db_session, user, s95), s95_location, rated_day)
    rating = _rate(db_session, user, s95_location, rated_run, rated_day, platform_code="s95")
    if stale_key == "old_catalog_node":
        rating.location_key = f"catalog:{uuid4()}"
        db_session.flush()
    _run(db_session, five_participant, five_location, last_day)

    identity_key = f"catalog:{catalog.id}"
    assert rating.location_key != identity_key
    result = location_rating_status(
        db_session,
        user.id,
        identity_key=identity_key,
        location_ids=[five_location.id, s95_location.id],
    )
    assert result["status"] == RATE_LINK_TOO_OLD
    assert result["last_visit_date"] == last_day
    run = result["run"]
    assert isinstance(run, dict)
    assert run["entry_id"] == f"run:{rated_run.id}"
    eligible = list_eligible_runs(db_session, user.id)["runs"]
    assert isinstance(eligible, list)
    assert not [entry for entry in eligible if entry["location_identity_key"] == identity_key]


def test_parkrun_history_is_not_rated(db_session: Session) -> None:
    """Давний parkrun в добор не входит — так и говорим, а не «вас здесь не было»."""
    five = _platform(db_session, "five_verst")
    parkrun = _platform(db_session, "parkrun")
    location = _location(db_session, parkrun, name="Bushy Park")
    user = _user(db_session)
    _history(db_session, _link(db_session, user, five), five)
    parkrun_participant = _link(db_session, user, parkrun)
    last_day = TODAY - timedelta(days=RATING_WINDOW_DAYS + 5)
    _run(db_session, parkrun_participant, location, last_day)

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_PARKRUN_HISTORY
    assert result["last_visit_date"] == last_day
    assert result["run"] is None


def test_not_here(db_session: Session) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    _history(db_session, _link(db_session, user, five), five)

    result = _status(db_session, user, location)
    assert result["status"] == RATE_LINK_NOT_HERE
    assert result["last_visit_date"] is None
    assert result["run"] is None


def test_test_event_is_not_a_visit(db_session: Session) -> None:
    """Тестовый старт не оценивается — и визитом для объяснения не считается."""
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    run = _run(db_session, participant, location, TODAY - timedelta(days=2))
    db_session.query(Event).filter(Event.id == run.event_id).update({Event.is_test_event: True})
    db_session.flush()

    assert _status(db_session, user, location)["status"] == RATE_LINK_NOT_HERE


def test_route_resolves_slug_and_answers(db_session: Session) -> None:
    five = _platform(db_session, "five_verst")
    location = _location(db_session, five)
    user = _user(db_session)
    participant = _link(db_session, user, five)
    _history(db_session, participant, five)
    fresh = _run(db_session, participant, location, TODAY - timedelta(days=1))

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        client = TestClient(app)
        response = client.get(f"/api/ratings/location/{location.external_key}")
        missing = client.get("/api/ratings/location/no-such-location-anywhere")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == RATE_LINK_CAN_RATE
    assert body["run"]["entry_id"] == f"run:{fresh.id}"
    assert body["run"]["location_name"] == "Оценочная"
    assert body["last_visit_date"] == (TODAY - timedelta(days=1)).isoformat()
    assert body["run_is_recent"] is True
    assert missing.status_code == 404


def test_route_requires_login(db_session: Session) -> None:
    """Гостю ответа нет: страница сама предлагает войти и вернуться по той же ссылке."""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        response = TestClient(app).get("/api/ratings/location/anything")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401
