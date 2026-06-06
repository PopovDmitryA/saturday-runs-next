from __future__ import annotations

from fastapi.testclient import TestClient

pytest_plugins = ["tests.test_dashboard_api"]


def test_catalog_table_requires_auth(client: TestClient) -> None:
    assert client.get("/api/locations/catalog/table").status_code == 401


def test_catalog_table_returns_rows(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/locations/catalog/table")
    assert response.status_code == 200
    payload = response.json()
    assert "rows" in payload
    assert "total_rows" in payload
    assert payload["total_rows"] == len(payload["rows"])
    if payload["rows"]:
        row = payload["rows"][0]
        assert "name" in row
        assert "platform_code" in row
        assert "visited" in row
        assert "first_visit_date" in row
        assert "region" in row
        assert "has_coordinates" in row
