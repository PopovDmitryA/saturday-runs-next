"""Эндпоинты бота «локации рядом» и журнал «где ищут старт»."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import internal_bot
from app.config import Settings, get_settings
from app.main import app
from app.models import NearbyQueryLog
from app.services.nearby_query_log_service import get_nearby_log_report, label_place, record_nearby_query

SECRET = "bot-secret"


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="https://run5k.run",
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
        telegram_bot_internal_secret=SECRET,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_nearby_requires_bot_secret(client: TestClient) -> None:
    response = client.post("/api/internal/bot/nearby", json={"latitude": 55.75, "longitude": 37.61})
    assert response.status_code == 403


def test_nearby_rejects_impossible_coordinates(client: TestClient) -> None:
    response = client.post(
        "/api/internal/bot/nearby",
        json={"latitude": 123, "longitude": 37.61},
        headers={"X-Bot-Secret": SECRET},
    )
    assert response.status_code == 422


def test_nearby_answers_and_logs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, Any]] = []
    queued: list[int] = []

    def fake_build(db: Session, latitude: float, longitude: float, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["base_url"] == "https://run5k.run"
        assert kwargs["user"] is None
        return {
            "has_nearby": False,
            "text_html": "📍 В радиусе 15 км стартов нет.",
            "items": [
                {
                    "identity_key": "catalog:x",
                    "name": "Гродно",
                    "latitude": 53.68,
                    "longitude": 23.83,
                    "distance_km": 249.0,
                    "site_url": "https://run5k.run/locations/grodno",
                    "status": "ok",
                }
            ],
            "linked": False,
            "nearest_identity_key": "catalog:x",
            "nearest_distance_km": 249.0,
            "within_radius": 0,
        }

    def fake_record(db: Session, **kwargs: Any) -> int:
        logged.append(kwargs)
        return 99

    monkeypatch.setattr(internal_bot, "build_nearby", fake_build)
    monkeypatch.setattr(internal_bot, "record_nearby_query", fake_record)
    monkeypatch.setattr(internal_bot, "queue_label_place", queued.append)

    response = client.post(
        "/api/internal/bot/nearby",
        json={"latitude": 53.9, "longitude": 27.5667},
        headers={"X-Bot-Secret": SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_nearby"] is False
    assert body["items"][0]["name"] == "Гродно"
    assert logged == [
        {
            "source": "bot",
            "latitude": 53.9,
            "longitude": 27.5667,
            "nearest_identity_key": "catalog:x",
            "nearest_distance_km": 249.0,
            "within_radius": 0,
            "is_linked": False,
        }
    ]
    # Белое пятно — подпись места уходит воркеру.
    assert queued == [99]


def test_inline_search_by_name_is_not_logged(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, Any]] = []

    def fake_inline(db: Session, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "identity_key": "catalog:x",
                "name": "Сокольники",
                "latitude": 55.79,
                "longitude": 37.66,
                "distance_km": None,
                "site_url": None,
                "status": "ok",
                "platform_code": "five_verst",
                "address": None,
                "start_label": "🗓 завтра, 9:00 · старт ≈№137",
                "text_html": "<b>Сокольники</b>",
            }
        ]

    monkeypatch.setattr(internal_bot, "build_inline_results", fake_inline)
    monkeypatch.setattr(internal_bot, "record_nearby_query", lambda db, **kwargs: logged.append(kwargs))

    response = client.post(
        "/api/internal/bot/nearby/inline",
        json={"query": "сокол"},
        headers={"X-Bot-Secret": SECRET},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["platform_title"] == "5 вёрст"
    assert logged == []


def test_log_rounds_point_and_reports_white_spot(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id = record_nearby_query(
        db_session,
        source="bot",
        latitude=53.9012,
        longitude=27.5589,
        nearest_identity_key=None,
        nearest_distance_km=249.0,
        within_radius=0,
        is_linked=True,
    )
    assert row_id is not None
    row = db_session.get(NearbyQueryLog, row_id)
    assert row is not None
    assert (row.cell_latitude, row.cell_longitude) == (53.9, 27.55)
    assert row.place_label is None

    monkeypatch.setattr(
        "app.geo.reverse_geocode.lookup_address",
        lambda lat, lon: {"city": "Минск", "region": "Минск", "country": "Беларусь"},
    )
    assert label_place(db_session, row_id) == "Минск, Беларусь"

    # Вторая точка из той же клетки получает подпись сразу, без геокодинга.
    again = record_nearby_query(
        db_session,
        source="inline",
        latitude=53.91,
        longitude=27.56,
        nearest_identity_key=None,
        nearest_distance_km=249.0,
        within_radius=0,
        is_linked=False,
    )
    assert again is None

    report = get_nearby_log_report(db_session, period_days=1)
    spot = next(item for item in report["white_spots"] if item["cell_latitude"] == 53.9)
    assert spot["place_label"] == "Минск, Беларусь"
    assert spot["count"] >= 2
    assert report["inline_total"] >= 1


def test_close_location_is_not_a_white_spot(db_session: Session) -> None:
    row_id = record_nearby_query(
        db_session,
        source="bot",
        latitude=55.7558,
        longitude=37.6173,
        nearest_identity_key="catalog:x",
        nearest_distance_km=3.0,
        within_radius=3,
        is_linked=False,
    )
    assert row_id is None
