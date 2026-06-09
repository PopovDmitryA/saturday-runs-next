from __future__ import annotations

from collections.abc import Generator
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    EventSummary,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    SyncJobTrigger,
    User,
)
from app.platform_adapters.canonical import CanonicalParticipant
from app.sync.user_sync import run_user_sync


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
        user_login_auto_sync_interval_seconds=86400,
    )



@pytest.fixture
def client(db_session: Session, fake_redis: fakeredis.FakeRedis, auth_settings: Settings) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: auth_settings

    with patch("app.core.redis_client.get_redis_client", return_value=fake_redis):
        with patch("app.services.auth_service.check_rate_limit", return_value=True):
            with patch("app.services.sync_trigger_service.enqueue_user_sync"):
                with TestClient(app) as test_client:
                    yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient, fake_redis: fakeredis.FakeRedis) -> TestClient:
    telegram_id = int(uuid4().int % 10_000_000_000)
    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": telegram_id,
            "telegram_username": f"dash_tester_{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    magic_link = confirm_response.json()["magic_link"]
    token = magic_link.split("token=")[-1]
    callback_response = client.get(f"/api/auth/callback?token={token}", follow_redirects=False)
    assert callback_response.status_code == 302
    return client


def _seed_user_run(db_session: Session, user: User) -> str:
    external_user_id = str(uuid4().int % 1_000_000_000)
    event_number = int(uuid4().int % 100_000) + 900_000
    test_slug = f"dashboard-seed-{uuid4().hex[:8]}"
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(
        platform_id=platform.id,
        external_key=test_slug,
        name="Dashboard Test Park",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Ali PAPAKHOV",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()

    link = PlatformLink(
        user_id=user.id,
        platform_id=platform.id,
        participant_id=participant.id,
        external_user_id=external_user_id,
        external_url=participant.profile_url,
    )
    db_session.add(link)

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"test:{event_number}:{external_user_id}",
        event_date=date(2099, 6, 1),
        event_number=event_number,
        title=f"Test Event #{event_number}",
        finishers_count=52,
        runners_count=52,
    )
    db_session.add(event)
    db_session.flush()

    db_session.add(
        EventSummary(
            platform_id=platform.id,
            location_id=location.id,
            event_id=event.id,
            external_event_key=event.external_event_key,
            event_date=event.event_date,
            event_number=event.event_number,
            finishers_count=52,
            avg_time_sec=20 * 60,
            avg_time_display="00:20:00",
            summary_hash=f"test-summary-{external_user_id}",
        )
    )

    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"test:{event_number}:{external_user_id}",
            position=1,
            finish_time_sec=18 * 60 + 59,
            finish_time_display="00:18:59",
            age_category="М30-34",
            status="finished",
            is_pr=True,
        )
    )
    db_session.commit()
    return external_user_id


def test_dashboard_and_runs_require_auth(client: TestClient) -> None:
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs/best-results").status_code == 401
    assert client.get("/api/runs/personal-records").status_code == 401
    assert client.get("/api/volunteering/role-stats").status_code == 401
    assert client.get("/api/sync/status").status_code == 401


def test_dashboard_returns_stats_from_global_core(authenticated_client: TestClient, db_session: Session) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    _seed_user_run(db_session, user)

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["total_runs"] == 1
    assert data["stats"]["by_platform"]["five_verst"]["runs"] == 1
    analytics = data["stats"]["analytics"]
    assert analytics["unique_locations"] == 1
    assert analytics["unique_run_locations"] == 1
    assert analytics["unique_run_regions"] == 0
    assert analytics["unique_run_cities"] == 1
    assert analytics["best_finish_time_sec"] == 18 * 60 + 59
    assert analytics["pr_count"] == 1
    assert analytics["analytics_version"] == 13
    assert analytics["best_results_platform_count"] == 1
    assert analytics["runs_current_year"] == 1
    assert analytics["total_distance_km"] == 5
    assert analytics["next_milestone_runs"] == 10
    assert analytics["runs_to_next_milestone"] == 9
    assert analytics["new_locations_last_12_months"] == 1
    assert analytics["last_pr_date"] == "2099-06-01"
    assert analytics["last_global_pr_date"] == "2099-06-01"
    assert analytics["pr_last_12_months"] == 1
    assert analytics["avg_vs_field_pct"] == 5.1
    assert analytics["runs_with_field_avg_count"] == 1
    assert analytics["top_location"]["platform_code"] == "five_verst"
    assert analytics["top_location"]["tied_count"] == 1
    assert analytics["avg_position"] == 1

    runs = authenticated_client.get("/api/runs")
    assert runs.status_code == 200
    assert len(runs.json()) == 1
    assert runs.json()[0]["finish_time_display"] == "00:18:59"
    assert runs.json()[0]["is_pr"] is True
    assert runs.json()[0]["location_is_paused"] is False
    assert runs.json()[0]["location_is_cancelled"] is False
    assert runs.json()[0]["event_url"].endswith("/results/01.06.2099/")
    assert "/dashboard-seed-" in runs.json()[0]["event_url"]

    best = authenticated_client.get("/api/runs/best-results")
    assert best.status_code == 200
    best_items = best.json()
    assert len(best_items) == 1
    assert best_items[0]["platform_code"] == "five_verst"
    assert best_items[0]["finish_time_sec"] == 18 * 60 + 59
    assert best_items[0]["location_name"] == "Dashboard Test Park"

    prs = authenticated_client.get("/api/runs/personal-records")
    assert prs.status_code == 200
    pr_items = prs.json()
    assert len(pr_items) == 1
    assert pr_items[0]["platform_code"] == "five_verst"
    assert pr_items[0]["event_date"] == "2099-06-01"


def test_dashboard_field_avg_computed_from_location_runners(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    from app.services.dashboard_service import compute_dashboard_stats

    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(
        platform_id=platform.id,
        external_key=f"field-avg-{suffix}",
        name=f"Field Avg {suffix}",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    external_user_id = f"field-user-{suffix}"
    participant = Participant(
        platform_id=platform.id,
        external_user_id=external_user_id,
        display_name="Field Avg Tester",
        profile_url=f"https://5verst.ru/userstats/{external_user_id}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=external_user_id,
            external_url=participant.profile_url,
        )
    )

    event_date = date(2024, 3, 2)
    event_number = int(uuid4().int % 100_000) + 700_000
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"field-avg:{event_number}:{suffix}",
        event_date=event_date,
        event_number=event_number,
        title=f"Field Avg Event #{event_number}",
        finishers_count=3,
        runners_count=3,
    )
    db_session.add(event)
    db_session.flush()

    user_time_sec = 30 * 60
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"field-avg:user:{suffix}",
            finish_time_sec=user_time_sec,
            finish_time_display="00:30:00",
            status="finished",
            is_pr=False,
        )
    )

    for idx, finish_time_sec in enumerate([32 * 60, 34 * 60], start=1):
        other_participant = Participant(
            platform_id=platform.id,
            external_user_id=f"field-other-{suffix}-{idx}",
            display_name=f"Other Runner {idx}",
            profile_url=f"https://5verst.ru/userstats/field-other-{suffix}-{idx}/",
        )
        db_session.add(other_participant)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=other_participant.id,
                external_result_key=f"field-avg:other:{suffix}:{idx}",
                finish_time_sec=finish_time_sec,
                finish_time_display="00:32:00",
                status="finished",
                is_pr=False,
            )
        )

    db_session.commit()

    analytics = compute_dashboard_stats(db_session, user.id)["analytics"]
    assert analytics["runs_with_field_avg_count"] == 1
    assert analytics["avg_vs_field_pct"] == 6.2


def test_top_location_reports_tied_count(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location_a = Location(
        platform_id=five_verst.id,
        external_key=f"tie-a-{suffix}",
        name="AAA Tie Location",
        city="Москва",
        country="Россия",
    )
    location_b = Location(
        platform_id=five_verst.id,
        external_key=f"tie-b-{suffix}",
        name="BBB Tie Location",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([location_a, location_b])
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"tie-user-{suffix}",
        display_name="Tie Tester",
        profile_url=f"https://5verst.ru/userstats/tie-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    runs_plan = [
        (location_a, date(2024, 1, 6)),
        (location_a, date(2024, 2, 3)),
        (location_b, date(2024, 3, 2)),
        (location_b, date(2024, 4, 6)),
    ]
    for index, (location, event_date) in enumerate(runs_plan, start=1):
        event = Event(
            platform_id=five_verst.id,
            location_id=location.id,
            external_event_key=f"tie-event-{suffix}-{index}",
            event_date=event_date,
            event_number=940_000 + index,
            title=f"Tie Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"tie-result-{suffix}-{index}",
                position=index,
                finish_time_sec=20 * 60 + index,
                finish_time_display=f"00:20:0{index}",
                status="finished",
            )
        )

    db_session.commit()

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    top_location = response.json()["stats"]["analytics"]["top_location"]
    assert top_location["count"] == 2
    assert top_location["tied_count"] == 2
    assert top_location["name"] == "AAA Tie Location"


def test_best_results_per_platform(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    five_location = Location(
        platform_id=five_verst.id,
        external_key=f"best-five-{suffix}",
        name="Five Best Location",
        city="Москва",
        country="Россия",
    )
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"best-parkrun-{suffix}",
        name="Parkrun Best Location",
        city="Санкт-Петербург",
        country="Россия",
    )
    db_session.add_all([five_location, parkrun_location])
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"best-user-{suffix}",
        display_name="Best Tester",
        profile_url=f"https://5verst.ru/userstats/best-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    parkrun_participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"best-parkrun-{suffix}",
        display_name="Best Tester PR",
        profile_url=f"https://www.parkrun.ru/profile/{suffix}/",
    )
    db_session.add(parkrun_participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=parkrun.id,
            participant_id=parkrun_participant.id,
            external_user_id=parkrun_participant.external_user_id,
            external_url=parkrun_participant.profile_url,
        )
    )

    runs_seed = [
        (five_verst, five_location, participant, date(2024, 3, 9), 19 * 60 + 30, "00:19:30"),
        (five_verst, five_location, participant, date(2024, 6, 1), 18 * 60 + 10, "00:18:10"),
        (parkrun, parkrun_location, parkrun_participant, date(2025, 1, 11), 22 * 60 + 5, "00:22:05"),
        (parkrun, parkrun_location, parkrun_participant, date(2025, 2, 8), 21 * 60 + 40, "00:21:40"),
    ]
    for index, (platform, location, run_participant, event_date, finish_sec, finish_display) in enumerate(
        runs_seed,
        start=1,
    ):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"best-event-{suffix}-{index}",
            event_date=event_date,
            event_number=910_000 + index,
            title=f"Best Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=run_participant.id,
                external_result_key=f"best-result-{suffix}-{index}",
                position=index,
                finish_time_sec=finish_sec,
                finish_time_display=finish_display,
                status="finished",
            )
        )

    db_session.commit()

    response = authenticated_client.get("/api/runs/best-results")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert [item["platform_code"] for item in items] == ["five_verst", "parkrun"]
    assert items[0]["finish_time_sec"] == 18 * 60 + 10
    assert items[0]["event_date"] == "2024-06-01"
    assert items[0]["location_name"] == "Five Best Location"
    assert items[1]["finish_time_sec"] == 21 * 60 + 40
    assert items[1]["event_date"] == "2025-02-08"
    assert items[1]["location_name"] == "Parkrun Best Location"
    assert items[1]["finish_time_display"] == "00:21:40"

    dashboard = authenticated_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["stats"]["analytics"]["best_results_platform_count"] == 2


def test_best_results_normalizes_parkrun_finish_time_display(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    location = Location(
        platform_id=parkrun.id,
        external_key=f"parkrun-best-{suffix}",
        name="Parkrun Format Test",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"parkrun-best-{suffix}",
        display_name="Format Tester",
        profile_url=f"https://www.parkrun.ru/profile/{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=parkrun.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    event = Event(
        platform_id=parkrun.id,
        location_id=location.id,
        external_event_key=f"parkrun-best-event-{suffix}",
        event_date=date(2025, 3, 1),
        event_number=930_001,
        title="Parkrun Format Event",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"parkrun-best-result-{suffix}",
            position=1,
            finish_time_sec=25 * 60 + 30,
            finish_time_display="25:30",
            status="finished",
        )
    )
    db_session.commit()

    response = authenticated_client.get("/api/runs/best-results")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["finish_time_display"] == "00:25:30"


def test_personal_records_lists_pr_runs_per_platform(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    five_location = Location(
        platform_id=five_verst.id,
        external_key=f"pr-five-{suffix}",
        name="Five PR Location",
        city="Москва",
        country="Россия",
    )
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"pr-parkrun-{suffix}",
        name="Parkrun PR Location",
        city="Санкт-Петербург",
        country="Россия",
    )
    db_session.add_all([five_location, parkrun_location])
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"pr-user-{suffix}",
        display_name="PR Tester",
        profile_url=f"https://5verst.ru/userstats/pr-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    parkrun_participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"pr-parkrun-{suffix}",
        display_name="PR Tester Parkrun",
        profile_url=f"https://www.parkrun.ru/profile/pr-{suffix}/",
    )
    db_session.add(parkrun_participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=parkrun.id,
            participant_id=parkrun_participant.id,
            external_user_id=parkrun_participant.external_user_id,
            external_url=parkrun_participant.profile_url,
        )
    )

    runs_seed = [
        (five_verst, five_location, participant, date(2023, 4, 8), 20 * 60, "00:20:00", True),
        (five_verst, five_location, participant, date(2024, 5, 11), 19 * 60, "00:19:00", True),
        (five_verst, five_location, participant, date(2024, 8, 3), 18 * 60 + 30, "00:18:30", False),
        (parkrun, parkrun_location, parkrun_participant, date(2025, 1, 4), 23 * 60, "00:23:00", True),
    ]
    for index, (
        platform,
        location,
        run_participant,
        event_date,
        finish_sec,
        finish_display,
        is_pr,
    ) in enumerate(runs_seed, start=1):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"pr-event-{suffix}-{index}",
            event_date=event_date,
            event_number=920_000 + index,
            title=f"PR Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=run_participant.id,
                external_result_key=f"pr-result-{suffix}-{index}",
                position=index,
                finish_time_sec=finish_sec,
                finish_time_display=finish_display,
                status="finished",
                is_pr=is_pr,
            )
        )

    db_session.commit()

    response = authenticated_client.get("/api/runs/personal-records")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 3
    assert [item["event_date"] for item in items] == ["2025-01-04", "2024-05-11", "2023-04-08"]
    global_flags = {item["event_date"]: item["is_global_pr"] for item in items}
    assert global_flags["2023-04-08"] is True
    assert global_flags["2024-05-11"] is True
    assert global_flags["2025-01-04"] is False


def test_dashboard_unique_locations_merged_by_catalog(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    suffix = str(uuid4().int % 1_000_000)

    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"parkrun-merge-{suffix}",
        name="Parkrun Merge Test",
        city="Москва",
        country="Россия",
    )
    five_verst_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-merge-{suffix}",
        name="Five Verst Merge Test",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([parkrun_location, five_verst_location])
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Merge Test Park",
        legacy_parkrun_slug=f"parkrun-merge-{suffix}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=parkrun_location.external_key,
                location_id=parkrun_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=five_verst_location.external_key,
                location_id=five_verst_location.id,
            ),
        ]
    )

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"merge-user-{suffix}",
        display_name="Merge Tester",
        profile_url=f"https://5verst.ru/userstats/merge-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    for index, (platform, location) in enumerate(
        [(parkrun, parkrun_location), (five_verst, five_verst_location)],
        start=1,
    ):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"merge-event-{suffix}-{index}",
            event_date=date(2024, index, 6),
            event_number=900_000 + index,
            title=f"Merge Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"merge-result-{suffix}-{index}",
                position=index,
                finish_time_sec=20 * 60 + index,
                finish_time_display=f"00:20:0{index}",
                status="finished",
            )
        )

    db_session.commit()

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    analytics = response.json()["stats"]["analytics"]
    assert analytics["unique_locations"] == 1
    assert analytics["unique_run_locations"] == 1

    detail = authenticated_client.get("/api/locations/visited/detail")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["unique_run_locations"] == analytics["unique_run_locations"]
    assert detail_payload["total_locations"] == analytics["unique_locations"]
    assert len(detail_payload["locations"]) == 1


def test_user_sync_updates_cache(authenticated_client: TestClient, db_session: Session) -> None:
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    external_user_id = _seed_user_run(db_session, user)

    profile = CanonicalParticipant(
        external_user_id=external_user_id,
        display_name="Ali PAPAKHOV",
        profile_url="https://5verst.ru/userstats/790087870/",
        total_runs=70,
        total_volunteering=3,
        club_name="Test Club",
    )

    with patch("app.platform_adapters.five_verst.adapter.FiveVerstAdapter.fetch_user_profile", return_value=profile):
        job = run_user_sync(db_session, user.id, SyncJobTrigger.manual)
        assert job.status.value == "success"

    dashboard = authenticated_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["stats"]["total_runs"] == 1

    status_response = authenticated_client.get("/api/sync/status")
    assert status_response.status_code == 200
    assert status_response.json()["latest_job"]["status"] == "success"


def test_sync_queue_requires_admin(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/sync/queue")
    assert response.status_code == 403


def test_sync_queue_admin_lists_jobs(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    auth_settings: Settings,
) -> None:
    admin_telegram_id = 42424242
    admin_settings = auth_settings.model_copy(update={"admin_telegram_id": admin_telegram_id})

    def override_get_settings() -> Settings:
        return admin_settings

    app.dependency_overrides[get_settings] = override_get_settings

    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": admin_telegram_id,
            "telegram_username": "queue_admin",
            "telegram_chat_id": admin_telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    magic_link = confirm_response.json()["magic_link"]
    token = magic_link.split("token=")[-1]
    callback_response = client.get(f"/api/auth/callback?token={token}", follow_redirects=False)
    assert callback_response.status_code == 302

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_admin"] is True

    response = client.get("/api/sync/queue")
    assert response.status_code == 200
    payload = response.json()
    assert "jobs" in payload
    assert "queues" in payload
    assert len(payload["queues"]) == 3


def test_sync_queue_admin_accepts_user_without_telegram_id(
    client: TestClient,
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    auth_settings: Settings,
) -> None:
    from app.models import SyncJob, SyncJobStatus, SyncJobTrigger, User

    admin_telegram_id = 42424243
    admin_settings = auth_settings.model_copy(update={"admin_telegram_id": admin_telegram_id})
    app.dependency_overrides[get_settings] = lambda: admin_settings

    oauth_user = User(telegram_id=None, consent_accepted=True, display_name="VK Only")
    db_session.add(oauth_user)
    db_session.flush()
    db_session.add(
        SyncJob(
            user_id=oauth_user.id,
            trigger=SyncJobTrigger.manual,
            status=SyncJobStatus.failed,
            error_message="parkrun captcha",
        )
    )
    db_session.commit()

    login_response = client.post("/api/auth/login-request")
    request_token = login_response.json()["request_token"]
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": request_token,
            "telegram_id": admin_telegram_id,
            "telegram_username": "queue_admin2",
            "telegram_chat_id": admin_telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    assert confirm_response.status_code == 200
    token = confirm_response.json()["magic_link"].split("token=")[-1]
    assert client.get(f"/api/auth/callback?token={token}", follow_redirects=False).status_code == 302

    response = client.get("/api/sync/queue")
    assert response.status_code == 200, response.text
    job = next((item for item in response.json()["jobs"] if item["user"]), None)
    assert job is not None
    assert job["user"]["telegram_id"] is None
    assert job["user"]["display_name"] == "VK Only"


def test_sync_refresh_rate_limited(authenticated_client: TestClient, fake_redis: fakeredis.FakeRedis) -> None:
    with patch("app.api.routes.sync.enqueue_user_sync"):
        with patch("app.core.rate_limit.get_redis_client", return_value=fake_redis):
            first = authenticated_client.post("/api/sync/refresh")
            second = authenticated_client.post("/api/sync/refresh")
    assert first.status_code == 202
    assert second.status_code == 429
