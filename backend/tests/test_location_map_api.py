from __future__ import annotations

from fastapi.testclient import TestClient

pytest_plugins = ["tests.test_dashboard_api"]


def test_visited_map_requires_auth(client: TestClient) -> None:
    assert client.get("/api/locations/visited/map").status_code == 401


def test_catalog_map_requires_auth(client: TestClient) -> None:
    assert client.get("/api/locations/catalog/map").status_code == 401


def test_visited_map_returns_payload(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/locations/visited/map")
    assert response.status_code == 200
    payload = response.json()
    assert "points" in payload
    assert "total_locations" in payload
    assert "mapped_locations" in payload
    assert "unmapped_locations" in payload


def test_catalog_map_returns_payload(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/locations/catalog/map")
    assert response.status_code == 200
    payload = response.json()
    assert "points" in payload
    assert payload["total_locations"] >= 0


def test_visited_detail_requires_auth(client: TestClient) -> None:
    assert client.get("/api/locations/visited/detail").status_code == 401


def test_visited_detail_returns_payload(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/locations/visited/detail")
    assert response.status_code == 200
    payload = response.json()
    assert "locations" in payload
    assert "platform_summary" in payload
    assert "total_locations" in payload
    assert "unique_run_locations" in payload
    assert "unique_volunteer_locations" in payload
