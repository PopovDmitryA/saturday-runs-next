from __future__ import annotations

from fastapi.testclient import TestClient

pytest_plugins = ["tests.test_dashboard_api"]


def test_catalog_table_is_public_without_visited_marks(client: TestClient) -> None:
    """Аноним видит таблицу каталога, но без отметок «посещено».

    Роут на get_optional_user (см. locations.py — решение Дмитрия 25.07.2026:
    Локации и Рейтинги публичные). user_id в build_catalog_locations_table
    уходит None, поэтому visited у всех строк False.
    """
    response = client.get("/api/locations/catalog/table")
    assert response.status_code == 200
    payload = response.json()
    assert "rows" in payload
    assert payload["total_rows"] == len(payload["rows"])
    assert all(row["visited"] is False for row in payload["rows"])


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
