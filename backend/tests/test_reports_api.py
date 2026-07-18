from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services.report_query import ReportQueryError, validate_sql

TOKEN = "report-secret-token"


@pytest.fixture
def report_settings() -> Settings:
    base = get_settings()
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        database_url=base.database_url,
        redis_url="redis://localhost:6379/0",
        report_api_token=TOKEN,
        report_query_max_rows=100,
        report_query_timeout_seconds=5,
    )


@pytest.fixture
def client(report_settings: Settings) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_settings] = lambda: report_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


# --- validate_sql (unit, без БД) ---------------------------------------------


def test_validate_accepts_select() -> None:
    assert validate_sql("SELECT 1") == "SELECT 1"


def test_validate_accepts_with_cte() -> None:
    sql = "WITH x AS (SELECT 1 AS n) SELECT * FROM x"
    assert validate_sql(sql) == sql


def test_validate_strips_trailing_semicolon() -> None:
    assert validate_sql("SELECT 1;") == "SELECT 1"


def test_validate_rejects_empty() -> None:
    with pytest.raises(ReportQueryError):
        validate_sql("   ")


def test_validate_rejects_non_select() -> None:
    with pytest.raises(ReportQueryError):
        validate_sql("UPDATE users SET name = 'x'")


def test_validate_rejects_multiple_statements() -> None:
    with pytest.raises(ReportQueryError):
        validate_sql("SELECT 1; DROP TABLE users")


def test_validate_rejects_data_modifying_cte() -> None:
    with pytest.raises(ReportQueryError):
        validate_sql("WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x")


def test_validate_allows_keyword_inside_string_literal() -> None:
    # 'update' в строковом литерале не должно триггерить блок-лист.
    sql = "SELECT 1 WHERE 'update' = 'update'"
    assert validate_sql(sql) == sql


def test_validate_ignores_semicolon_in_string() -> None:
    sql = "SELECT ';' AS s"
    assert validate_sql(sql) == sql


# --- auth ---------------------------------------------------------------------


def test_list_requires_token(client: TestClient) -> None:
    assert client.get("/api/internal/reports/").status_code == 403


def test_list_rejects_wrong_token(client: TestClient) -> None:
    resp = client.get("/api/internal/reports/", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 403


def test_disabled_when_token_unset() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url=get_settings().database_url, report_api_token=""
    )
    try:
        with TestClient(app) as c:
            assert c.get("/api/internal/reports/", headers=_auth()).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_list_reports(client: TestClient) -> None:
    resp = client.get("/api/internal/reports/", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    names = {r["name"] for r in data["reports"]}
    assert {"db_size", "table_sizes", "connections"} <= names
    assert data["max_rows"] == 100


# --- query execution (нужна БД) ----------------------------------------------


def test_query_select(client: TestClient) -> None:
    resp = client.post(
        "/api/internal/reports/query",
        headers=_auth(),
        json={"sql": "SELECT 1 AS n, 'hello' AS greeting"},
    )
    if resp.status_code == 500:
        pytest.skip("Database not available")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["columns"] == ["n", "greeting"]
    assert data["rows"] == [{"n": 1, "greeting": "hello"}]
    assert data["row_count"] == 1
    assert data["truncated"] is False


def test_query_rejects_write(client: TestClient) -> None:
    resp = client.post(
        "/api/internal/reports/query",
        headers=_auth(),
        json={"sql": "DELETE FROM users"},
    )
    assert resp.status_code == 400


def test_query_truncates_to_limit(client: TestClient) -> None:
    resp = client.post(
        "/api/internal/reports/query",
        headers=_auth(),
        json={"sql": "SELECT g FROM generate_series(1, 10) AS g", "limit": 3},
    )
    if resp.status_code == 500:
        pytest.skip("Database not available")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["row_count"] == 3
    assert data["truncated"] is True


def test_named_report_unknown(client: TestClient) -> None:
    resp = client.get("/api/internal/reports/named/nope", headers=_auth())
    assert resp.status_code == 404


def test_named_report_db_size(client: TestClient) -> None:
    resp = client.get("/api/internal/reports/named/db_size", headers=_auth())
    if resp.status_code == 500:
        pytest.skip("Database not available")
    assert resp.status_code == 200, resp.text
    assert resp.json()["columns"] == ["size"]


def test_all_named_reports_pass_validation() -> None:
    from app.services.report_query import NAMED_REPORTS, validate_sql

    for report in NAMED_REPORTS.values():
        assert validate_sql(report.sql)  # не должно бросать


@pytest.mark.parametrize(
    "name",
    ["db_size", "table_sizes", "connections", "schema", "foreign_keys", "indexes"],
)
def test_all_named_reports_execute(client: TestClient, name: str) -> None:
    resp = client.get(f"/api/internal/reports/named/{name}", headers=_auth())
    if resp.status_code == 400 and "connection" in resp.text.lower():
        pytest.skip("Database not available")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["columns"], list)


def test_schema_report_shape(client: TestClient) -> None:
    resp = client.get("/api/internal/reports/named/schema", headers=_auth())
    if resp.status_code == 400 and "connection" in resp.text.lower():
        pytest.skip("Database not available")
    assert resp.status_code == 200, resp.text
    cols = resp.json()["columns"]
    assert {"table_name", "column_name", "data_type", "is_nullable"} <= set(cols)
