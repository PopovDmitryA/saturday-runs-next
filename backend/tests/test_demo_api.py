from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_demo_routes_are_public() -> None:
    assert client.get("/api/demo/dashboard").status_code in (200, 404, 503)
    assert client.get("/api/demo/runs").status_code in (200, 404, 503)
    assert client.get("/api/demo/volunteering").status_code in (200, 404, 503)
    assert client.get("/api/demo/locations/visited/map").status_code in (200, 404, 503)
    assert client.get("/api/demo/locations/catalog/map").status_code in (200, 404, 503)


def test_demo_dashboard_does_not_require_auth() -> None:
    response = client.get("/api/demo/dashboard")
    assert response.status_code != 401
