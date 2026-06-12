from __future__ import annotations

from collections.abc import Generator
from datetime import date
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models import Event, Location, Participant, Platform, RunResult
from app.services.five_verst_run_count_rating_service import (
    _normalize_rows,
    get_five_verst_run_count_rating,
)


@pytest.fixture(autouse=True)
def _rating_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.five_verst_run_count_rating_service.get_redis_client", lambda: redis)
    return redis


@pytest.fixture
def ratings_client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_five_verst_rating(db_session: Session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    suffix = uuid4().hex[:8]
    loc_a = Location(
        id=uuid4(),
        platform_id=platform.id,
        external_key=f"rating-test-a-{suffix}",
        name="Битца",
    )
    loc_b = Location(
        id=uuid4(),
        platform_id=platform.id,
        external_key=f"rating-test-b-{suffix}",
        name="Kuskovo",
    )
    db_session.add_all([loc_a, loc_b])
    db_session.flush()

    fast = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id="111",
        display_name="Быстрый Бегун",
    )
    steady = Participant(
        id=uuid4(),
        platform_id=platform.id,
        external_user_id="222",
        display_name="Стабильный Бегун",
    )
    db_session.add_all([fast, steady])
    db_session.flush()

    def add_run(participant: Participant, location: Location, event_date: date, *, status: str = "active_runner") -> None:
        external_event_key = f"{location.external_key}:{event_date.isoformat()}"
        event = (
            db_session.query(Event)
            .filter(
                Event.platform_id == platform.id,
                Event.external_event_key == external_event_key,
            )
            .one_or_none()
        )
        if event is None:
            event = Event(
                id=uuid4(),
                platform_id=platform.id,
                location_id=location.id,
                external_event_key=external_event_key,
                event_date=event_date,
                is_test_event=False,
            )
            db_session.add(event)
            db_session.flush()
        db_session.add(
            RunResult(
                id=uuid4(),
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"{participant.external_user_id}:{event.id}:{uuid4().hex[:6]}",
                status=status,
            )
        )

    add_run(fast, loc_a, date(2026, 5, 31))
    add_run(fast, loc_b, date(2026, 5, 24))
    add_run(fast, loc_a, date(2026, 5, 17))
    add_run(steady, loc_a, date(2026, 5, 31))
    add_run(steady, loc_b, date(2026, 5, 24))
    db_session.flush()


def test_run_count_rating_orders_by_run_count(db_session: Session) -> None:
    _seed_five_verst_rating(db_session)
    run_rows = db_session.execute(
        __import__("sqlalchemy").text(
            """
            SELECT pt.display_name AS runner_name, COUNT(*) AS run_count
            FROM run_results rr
            JOIN participants pt ON pt.id = rr.participant_id
            WHERE pt.display_name IN ('Быстрый Бегун', 'Стабильный Бегун')
            GROUP BY pt.display_name
            """
        )
    ).all()
    by_name = {row.runner_name: row.run_count for row in run_rows}
    assert by_name["Быстрый Бегун"] == 3
    assert by_name["Стабильный Бегун"] == 2


def test_normalize_rows_maps_grafana_columns() -> None:
    rows = _normalize_rows(
        [
            {
                "rank": 1,
                "runner_name": "Test Runner",
                "sum_event": 10,
                "delta_last_week": True,
                "last_location": "Битца",
                "count_pass": 0,
            }
        ]
    )
    assert rows[0]["runner_name"] == "Test Runner"
    assert rows[0]["run_count"] == 10
    assert rows[0]["ran_on_latest_date"] is True


def test_run_count_rating_api_is_public(ratings_client: TestClient) -> None:
    response = ratings_client.get("/api/public/ratings/five-verst/run-count?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Рейтинг количества пробежек"
    assert len(body["rows"]) == 5
    assert body["rows"][0]["rank"] == 1
    assert body["rows"][0]["run_count"] >= body["rows"][1]["run_count"]


def test_run_count_rating_uses_redis_cache(db_session: Session, _rating_redis: fakeredis.FakeRedis) -> None:
    _seed_five_verst_rating(db_session)
    first = get_five_verst_run_count_rating(db_session, limit=1000, use_cache=True)
    assert first["cached_at"] is not None

    second = get_five_verst_run_count_rating(db_session, limit=1000, use_cache=True)
    assert second["cached_at"] == first["cached_at"]
    assert _rating_redis.get("ratings:five_verst:run_count:v1:1000") is not None
