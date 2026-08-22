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
    EventCrosslink,
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
from app.services.dashboard_service import ANALYTICS_VERSION
from app.services.personal_record_service import recalculate_cross_platform_personal_records
from app.services.sync_dedup_service import SyncEnqueueResult
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
    db_session.flush()
    recalculate_cross_platform_personal_records(db_session, user.id)
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
    assert analytics["analytics_version"] == ANALYTICS_VERSION
    assert analytics["best_results_platform_count"] == 1
    assert analytics["runs_current_year"] == 1
    assert analytics["total_distance_km"] == 5
    assert analytics["next_milestone_runs"] == 10
    assert analytics["runs_to_next_milestone"] == 9
    assert analytics["new_locations_last_12_months"] == 1
    assert analytics["last_pr_date"] == "2099-06-01"
    # Единственная (первая вообще) пробежка — baseline, глобальным рекордом не является.
    assert analytics["last_global_pr_date"] is None
    assert analytics["pr_last_12_months"] == 1
    assert analytics["avg_vs_field_pct"] == 5.1
    assert analytics["runs_with_field_avg_count"] == 1
    assert analytics["top_location"]["platform_codes"] == ["five_verst"]
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

    db_session.flush()
    recalculate_cross_platform_personal_records(db_session, user.id)
    db_session.commit()

    response = authenticated_client.get("/api/runs/personal-records")
    assert response.status_code == 200
    items = response.json()
    # 2024-08-03 (18:30) попадает в список без is_pr — как глобальный рекорд и
    # рекорд локации; самая первая пробежка (2023-04-08) — baseline, не
    # глобальный рекорд, но показана из-за проставленного is_pr.
    assert len(items) == 4
    assert [item["event_date"] for item in items] == [
        "2025-01-04",
        "2024-08-03",
        "2024-05-11",
        "2023-04-08",
    ]
    global_flags = {item["event_date"]: item["is_global_pr"] for item in items}
    # Самая первая пробежка — глобальный лучший результат на тот момент,
    # на витрине подсвечивается как глобальный рекорд (в БД остаётся False).
    assert global_flags["2023-04-08"] is True
    assert global_flags["2024-05-11"] is True
    assert global_flags["2024-08-03"] is True
    assert global_flags["2025-01-04"] is False
    location_flags = {item["event_date"]: item["is_location_pr"] for item in items}
    assert location_flags["2023-04-08"] is False  # первая пробежка на локации
    assert location_flags["2024-05-11"] is True
    assert location_flags["2024-08-03"] is True
    assert location_flags["2025-01-04"] is False
    assert global_flags["2025-01-04"] is False


def test_personal_records_shows_undefeated_debut(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """Регрессия для кейса Егора Свиридова (20.07.2026): дебютный старт в
    системе должен попасть в список с is_debut=True и — на витрине — с
    маркером is_pr=True (в БД run.is_pr остаётся False), а счётчик плитки
    на главной обязан считать ту же строку."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if runpark is None:
        runpark = Platform(code="runpark", name="RunPark")
        db_session.add(runpark)
        db_session.flush()

    location = Location(
        platform_id=runpark.id,
        external_key=f"debut-{suffix}",
        name="Дружба",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=runpark.id,
        external_user_id=f"debut-user-{suffix}",
        display_name="Debut Tester",
        profile_url=f"https://runpark.example/profile/debut-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=runpark.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    # Дебют (24:30, самый быстрый) и три более медленных забега позже —
    # ни один is_pr не проставлен нигде, ровно как в реальных данных Егора.
    runs_seed = [
        (date(2022, 4, 2), 24 * 60 + 30, "00:24:30", True),
        (date(2022, 4, 9), 24 * 60 + 52, "00:24:52", False),
        (date(2022, 4, 16), 27 * 60 + 14, "00:27:14", False),
        (date(2022, 4, 23), 25 * 60 + 10, "00:25:10", False),
    ]
    for index, (event_date, finish_sec, finish_display, is_first_run) in enumerate(runs_seed, start=1):
        event = Event(
            platform_id=runpark.id,
            location_id=location.id,
            external_event_key=f"debut-event-{suffix}-{index}",
            event_date=event_date,
            event_number=930_000 + index,
            title=f"Debut Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"debut-result-{suffix}-{index}",
                position=index,
                finish_time_sec=finish_sec,
                finish_time_display=finish_display,
                status="finished",
                is_pr=False,
                is_first_run=is_first_run,
            )
        )

    db_session.flush()
    recalculate_cross_platform_personal_records(db_session, user.id)
    db_session.commit()

    response = authenticated_client.get("/api/runs/personal-records")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    debut = items[0]
    assert debut["event_date"] == "2022-04-02"
    assert debut["is_debut"] is True
    # На витрине дебют помечен PR и глобальным рекордом (лучший результат на
    # момент той пробежки), хотя в БД is_pr/is_global_pr остаются False.
    assert debut["is_pr"] is True
    assert debut["is_global_pr"] is True

    run_row = (
        db_session.query(RunResult)
        .filter(RunResult.external_result_key == f"debut-result-{suffix}-1")
        .one()
    )
    assert run_row.is_pr is False
    assert run_row.is_global_pr is False

    dashboard = authenticated_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["stats"]["analytics"]["pr_count"] == len(items)

    # Вкладка «Пробежки»: дебют в системе тоже с маркером PR, а самая первая
    # пробежка вообще — с подсветкой глобального рекорда (витрина, не БД).
    runs = authenticated_client.get("/api/runs")
    assert runs.status_code == 200
    runs_by_date = {item["event_date"]: item for item in runs.json()}
    assert runs_by_date["2022-04-02"]["is_pr"] is True
    assert runs_by_date["2022-04-02"]["is_global_pr"] is True
    assert runs_by_date["2022-04-09"]["is_pr"] is False
    assert runs_by_date["2022-04-09"]["is_global_pr"] is False


def test_dashboard_pr_count_includes_location_pr(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """dashboard.analytics.pr_count открывает список PR-пробежек — должен
    считать те же строки, включая рекорды локации, а не только is_pr
    (Дмитрий, 20.07.2026)."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    suffix = str(uuid4().int % 1_000_000)

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(
        platform_id=platform.id,
        external_key=f"loc-pr-{suffix}",
        name="Loc PR Park",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"loc-pr-user-{suffix}",
        display_name="Loc PR Tester",
        profile_url=f"https://5verst.ru/userstats/loc-pr-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"loc-pr-event-{suffix}",
        event_date=date(2026, 5, 2),
        event_number=940_001,
        title="Loc PR Event",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"loc-pr-result-{suffix}",
            position=1,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
            is_pr=False,  # не рекорд системы
            is_location_pr=True,  # но рекорд локации
        )
    )
    db_session.flush()
    db_session.commit()

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    analytics = response.json()["stats"]["analytics"]
    assert analytics["pr_count"] == 1

    prs = authenticated_client.get("/api/runs/personal-records")
    assert prs.status_code == 200
    assert len(prs.json()) == 1
    assert prs.json()[0]["is_location_pr"] is True
    assert prs.json()[0]["is_pr"] is False


def _seed_wins_fixture(
    db_session: Session,
    user: User,
    *,
    gender: str,
    results: list[tuple[int, int | None]],
) -> None:
    """Один участник заданного пола и по результату на каждое (place, gender_place)."""
    suffix = str(uuid4().int % 1_000_000)
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = Location(
        platform_id=platform.id,
        external_key=f"loc-win-{suffix}",
        name="Win Park",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"win-user-{suffix}",
        display_name="Win Tester",
        profile_url=f"https://5verst.ru/userstats/win-{suffix}/",
        gender=gender,
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    for index, (position, gender_position) in enumerate(results):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"win-event-{suffix}-{index}",
            event_date=date(2026, 5, 2 + index),
            event_number=950_000 + index,
            title="Win Event",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"win-result-{suffix}-{index}",
                position=position,
                gender_position=gender_position,
                finish_time_sec=20 * 60 + index,
                finish_time_display="00:20:00",
                status="finished",
            )
        )
    db_session.flush()
    db_session.commit()


def test_dashboard_wins_counts_absolute_for_men(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """У мужчин победа — первое место в протоколе, а не в своей половине."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    # Победа в абсолюте, победа среди мужчин без победы в абсолюте, и обычный забег.
    _seed_wins_fixture(db_session, user, gender="male", results=[(1, 1), (4, 1), (7, 5)])

    analytics = authenticated_client.get("/api/dashboard").json()["stats"]["analytics"]
    assert analytics["wins_scope"] == "absolute"
    assert analytics["wins_count"] == 1

    wins = authenticated_client.get("/api/runs/wins")
    assert wins.status_code == 200
    # Цифра плитки и детализация обязаны совпадать.
    assert len(wins.json()) == analytics["wins_count"]
    assert wins.json()[0]["position"] == 1
    assert wins.json()[0]["scope"] == "absolute"


def test_dashboard_wins_counts_gender_places_for_women(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """У женщин победа — первое место среди женщин, абсолют не важен."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    _seed_wins_fixture(db_session, user, gender="female", results=[(12, 1), (5, 3)])

    analytics = authenticated_client.get("/api/dashboard").json()["stats"]["analytics"]
    assert analytics["wins_scope"] == "female"
    assert analytics["wins_count"] == 1

    wins = authenticated_client.get("/api/runs/wins")
    assert wins.status_code == 200
    assert len(wins.json()) == 1
    item = wins.json()[0]
    assert item["gender_position"] == 1
    assert item["position"] == 12
    assert item["scope"] == "female"


def _seed_parkrun_win(
    db_session: Session,
    user: User,
    *,
    catalogued: bool,
    gender: str = "female",
) -> None:
    """Победа на parkrun-площадке: catalogued=True — русская (есть связка с
    каталогом локаций), False — зарубежная (протокола у нас нет)."""
    suffix = str(uuid4().int % 1_000_000)
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    location = Location(
        platform_id=parkrun.id,
        external_key=f"parkrun-win-{suffix}",
        name="Parkrun Win Park",
        country="United Kingdom",
    )
    db_session.add(location)
    db_session.flush()

    if catalogued:
        catalog = LocationCatalog(
            canonical_name=f"Parkrun Win Park {suffix}",
            active_platform="five_verst",
            is_closed=False,
        )
        db_session.add(catalog)
        db_session.flush()
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )

    participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"parkrun-win-user-{suffix}",
        display_name="Parkrun Win Tester",
        profile_url=f"https://www.parkrun.com/parkrunner/{suffix}/",
        gender=gender,
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
        external_event_key=f"parkrun-win-event-{suffix}",
        event_date=date(2019, 6, 1),
        event_number=100,
        title="Parkrun Win Event",
        finishers_count=1,
        runners_count=1,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"parkrun-win-result-{suffix}",
            position=1,
            gender_position=1,
            finish_time_sec=22 * 60,
            finish_time_display="00:22:00",
            status="finished",
        )
    )
    db_session.flush()
    db_session.commit()


def test_dashboard_wins_ignore_foreign_parkrun(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """Зарубежный parkrun в зачёт побед не идёт: протокола у нас нет, и
    единственная строка из профиля всегда оказывается первой."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    _seed_parkrun_win(db_session, user, catalogued=False)

    analytics = authenticated_client.get("/api/dashboard").json()["stats"]["analytics"]
    assert analytics["wins_count"] == 0
    assert authenticated_client.get("/api/runs/wins").json() == []


def test_dashboard_wins_count_russian_parkrun(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """Русский parkrun собран протоколами целиком — его победы в зачёте."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    _seed_parkrun_win(db_session, user, catalogued=True)

    analytics = authenticated_client.get("/api/dashboard").json()["stats"]["analytics"]
    assert analytics["wins_count"] == 1
    wins = authenticated_client.get("/api/runs/wins").json()
    assert len(wins) == 1
    assert wins[0]["platform_code"] == "parkrun"


def test_wins_require_auth(client: TestClient) -> None:
    assert client.get("/api/runs/wins").status_code == 401


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

    five_verst_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"merge-user-{suffix}",
        display_name="Merge Tester",
        profile_url=f"https://5verst.ru/userstats/merge-user-{suffix}/",
    )
    parkrun_participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"merge-parkrun-user-{suffix}",
        display_name="Merge Tester",
        profile_url=f"https://www.parkrun.ru/parkrunner/merge-parkrun-user-{suffix}/",
    )
    db_session.add_all([five_verst_participant, parkrun_participant])
    db_session.flush()
    db_session.add_all(
        [
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst.id,
                participant_id=five_verst_participant.id,
                external_user_id=five_verst_participant.external_user_id,
                external_url=five_verst_participant.profile_url,
            ),
            PlatformLink(
                user_id=user.id,
                platform_id=parkrun.id,
                participant_id=parkrun_participant.id,
                external_user_id=parkrun_participant.external_user_id,
                external_url=parkrun_participant.profile_url,
            ),
        ]
    )

    for index, (platform, location, participant) in enumerate(
        [
            (parkrun, parkrun_location, parkrun_participant),
            (five_verst, five_verst_location, five_verst_participant),
        ],
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
    assert analytics["top_location"]["name"] == "Merge Test Park"
    assert analytics["top_location"]["count"] == 2
    assert sorted(analytics["top_location"]["platform_codes"]) == ["five_verst", "parkrun"]

    detail = authenticated_client.get("/api/locations/visited/detail")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["unique_run_locations"] == analytics["unique_run_locations"]
    assert detail_payload["total_locations"] == analytics["unique_locations"]
    assert len(detail_payload["locations"]) == 1


def test_dashboard_unique_locations_excludes_secondary_crosslink_duplicate(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """A RunPark-republished ("не в зачёте") duplicate of the user's own primary
    result must not inflate the "Локаций/Городов с пробежками" tiles — the
    detail modal (build_user_unique_location_details) already excludes such
    duplicates, so the tile must match it exactly."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if runpark is None:
        runpark = Platform(code="runpark", name="RunPark")
        db_session.add(runpark)
        db_session.flush()

    suffix = str(uuid4().int % 1_000_000)

    primary_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-xlink-{suffix}",
        name="Druzhba",
        city="Москва",
        country="Россия",
    )
    runpark_only_location = Location(
        platform_id=runpark.id,
        external_key=f"runpark-xlink-{suffix}",
        name="Druzhba RunPark",
        city="Тверь",
        country="Россия",
    )
    db_session.add_all([primary_location, runpark_only_location])
    db_session.flush()

    five_verst_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"xlink-user-{suffix}",
        display_name="Xlink Tester",
        profile_url=f"https://5verst.ru/userstats/xlink-user-{suffix}/",
    )
    runpark_participant = Participant(
        platform_id=runpark.id,
        external_user_id=f"xlink-runpark-user-{suffix}",
        display_name="Xlink Tester",
        profile_url=f"https://runpark.ru/user/xlink-runpark-user-{suffix}/",
    )
    db_session.add_all([five_verst_participant, runpark_participant])
    db_session.flush()
    db_session.add_all(
        [
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst.id,
                participant_id=five_verst_participant.id,
                external_user_id=five_verst_participant.external_user_id,
                external_url=five_verst_participant.profile_url,
            ),
            PlatformLink(
                user_id=user.id,
                platform_id=runpark.id,
                participant_id=runpark_participant.id,
                external_user_id=runpark_participant.external_user_id,
                external_url=runpark_participant.profile_url,
            ),
        ]
    )

    primary_event = Event(
        platform_id=five_verst.id,
        location_id=primary_location.id,
        external_event_key=f"xlink-primary-{suffix}",
        event_date=date(2024, 5, 11),
        event_number=900_400,
        title="Xlink Primary Event",
        finishers_count=10,
        runners_count=10,
    )
    runpark_event = Event(
        platform_id=runpark.id,
        location_id=runpark_only_location.id,
        external_event_key=f"xlink-runpark-{suffix}",
        event_date=date(2024, 5, 11),
        event_number=900_401,
        title="Xlink RunPark Event",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add_all([primary_event, runpark_event])
    db_session.flush()
    db_session.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=runpark_event.id))
    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=five_verst_participant.id,
            external_result_key=f"xlink-primary-result-{suffix}",
            position=1,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=runpark_event.id,
            participant_id=runpark_participant.id,
            external_result_key=f"xlink-runpark-result-{suffix}",
            position=1,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.commit()

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    analytics = response.json()["stats"]["analytics"]
    assert analytics["unique_run_locations"] == 1
    assert analytics["unique_run_cities"] == 1

    detail = authenticated_client.get("/api/locations/visited/detail")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["unique_run_locations"] == analytics["unique_run_locations"]
    assert len(detail_payload["locations"]) == analytics["unique_run_locations"]


def test_list_user_runs_is_crosslinked_requires_users_own_primary_result(
    authenticated_client: TestClient,
    db_session: Session,
) -> None:
    """A dual_load RunPark event is crosslinked at the event level (any primary event
    on the same date/location), but that does not mean every RunPark result in it is a
    personal duplicate. If this user has no result of their own in the primary event
    (e.g. they only ran the RunPark side that day), the RunPark run must stay зачётный
    — matching the narrow, per-user definition already used by the dashboard tile and
    the locations detail modal (user_secondary_crosslinked_run_ids)."""
    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    runpark = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    if runpark is None:
        runpark = Platform(code="runpark", name="RunPark")
        db_session.add(runpark)
        db_session.flush()

    suffix = str(uuid4().int % 1_000_000)

    primary_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-solo-{suffix}",
        name="Mikhalkovo",
        city="Москва",
        country="Россия",
    )
    runpark_location = Location(
        platform_id=runpark.id,
        external_key=f"runpark-solo-{suffix}",
        name="Mikhalkovo RunPark",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([primary_location, runpark_location])
    db_session.flush()

    five_verst_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"solo-5v-user-{suffix}",
        display_name="Solo Tester",
        profile_url=f"https://5verst.ru/userstats/solo-5v-user-{suffix}/",
    )
    runpark_participant = Participant(
        platform_id=runpark.id,
        external_user_id=f"solo-runpark-user-{suffix}",
        display_name="Solo Tester",
        profile_url=f"https://runpark.ru/user/solo-runpark-user-{suffix}/",
    )
    other_five_verst_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"solo-5v-other-{suffix}",
        display_name="Someone Else",
        profile_url=f"https://5verst.ru/userstats/solo-5v-other-{suffix}/",
    )
    db_session.add_all([five_verst_participant, runpark_participant, other_five_verst_participant])
    db_session.flush()
    db_session.add_all(
        [
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst.id,
                participant_id=five_verst_participant.id,
                external_user_id=five_verst_participant.external_user_id,
                external_url=five_verst_participant.profile_url,
            ),
            PlatformLink(
                user_id=user.id,
                platform_id=runpark.id,
                participant_id=runpark_participant.id,
                external_user_id=runpark_participant.external_user_id,
                external_url=runpark_participant.profile_url,
            ),
        ]
    )

    # Day A: user ran both systems — genuine duplicate, must stay "не в зачёте".
    primary_event_a = Event(
        platform_id=five_verst.id,
        location_id=primary_location.id,
        external_event_key=f"solo-primary-a-{suffix}",
        event_date=date(2024, 6, 29),
        event_number=900_500,
        title="Mikhalkovo 5v A",
        finishers_count=10,
        runners_count=10,
    )
    runpark_event_a = Event(
        platform_id=runpark.id,
        location_id=runpark_location.id,
        external_event_key=f"solo-runpark-a-{suffix}",
        event_date=date(2024, 6, 29),
        event_number=900_501,
        title="Mikhalkovo RunPark A",
        finishers_count=10,
        runners_count=10,
    )
    # Day B: primary event exists (someone else ran it) but the user only ran RunPark.
    primary_event_b = Event(
        platform_id=five_verst.id,
        location_id=primary_location.id,
        external_event_key=f"solo-primary-b-{suffix}",
        event_date=date(2025, 6, 28),
        event_number=900_502,
        title="Mikhalkovo 5v B",
        finishers_count=10,
        runners_count=10,
    )
    runpark_event_b = Event(
        platform_id=runpark.id,
        location_id=runpark_location.id,
        external_event_key=f"solo-runpark-b-{suffix}",
        event_date=date(2025, 6, 28),
        event_number=900_503,
        title="Mikhalkovo RunPark B",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add_all([primary_event_a, runpark_event_a, primary_event_b, runpark_event_b])
    db_session.flush()
    db_session.add_all(
        [
            EventCrosslink(primary_event_id=primary_event_a.id, secondary_event_id=runpark_event_a.id),
            EventCrosslink(primary_event_id=primary_event_b.id, secondary_event_id=runpark_event_b.id),
        ]
    )
    db_session.add_all(
        [
            RunResult(
                event_id=primary_event_a.id,
                participant_id=five_verst_participant.id,
                external_result_key=f"solo-primary-a-result-{suffix}",
                position=13,
                finish_time_sec=20 * 60,
                finish_time_display="00:20:00",
                status="finished",
            ),
            RunResult(
                event_id=runpark_event_a.id,
                participant_id=runpark_participant.id,
                external_result_key=f"solo-runpark-a-result-{suffix}",
                position=13,
                finish_time_sec=20 * 60,
                finish_time_display="00:20:00",
                status="finished",
            ),
            # Day B primary result belongs to someone else, not this user.
            RunResult(
                event_id=primary_event_b.id,
                participant_id=other_five_verst_participant.id,
                external_result_key=f"solo-primary-b-result-{suffix}",
                position=1,
                finish_time_sec=19 * 60,
                finish_time_display="00:19:00",
                status="finished",
            ),
            RunResult(
                event_id=runpark_event_b.id,
                participant_id=runpark_participant.id,
                external_result_key=f"solo-runpark-b-result-{suffix}",
                position=85,
                finish_time_sec=25 * 60,
                finish_time_display="00:25:00",
                status="finished",
            ),
        ]
    )
    db_session.commit()

    runs = authenticated_client.get("/api/runs")
    assert runs.status_code == 200
    by_date = {row["event_date"]: row for row in runs.json() if row["platform_code"] == "runpark"}
    assert by_date["2024-06-29"]["is_crosslinked"] is True
    assert by_date["2025-06-28"]["is_crosslinked"] is False


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

    # У five_verst синк идёт мимо адаптера — напрямую через fetch_userstats_html,
    # поэтому патчим именно его, иначе тест уходит в живую сеть на 5verst.ru.
    with (
        patch("app.sync.user_sync.fetch_userstats_html", return_value="<html></html>"),
        patch("app.sync.user_sync.parse_userstats_html", return_value=profile),
        patch("app.sync.user_sync.parse_userstats_runs_html", return_value=[]),
        patch("app.sync.user_sync.parse_userstats_volunteering_html", return_value=[]),
    ):
        job = run_user_sync(db_session, user.id, SyncJobTrigger.manual)
        assert job.status.value == "success", job.error_message

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
    assert "parkrun_queue" in payload
    assert payload["parkrun_queue"]["pending"] == 0


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


def test_sync_refresh_rate_limited(authenticated_client: TestClient) -> None:
    result = SyncEnqueueResult(job_id=uuid4(), duplicate=False)
    with patch("app.api.routes.sync.enqueue_manual_platform_sync", return_value=result):
        first = authenticated_client.post("/api/sync/refresh/five_verst")
        second = authenticated_client.post("/api/sync/refresh/five_verst")
    assert first.status_code == 202
    assert second.status_code == 429
