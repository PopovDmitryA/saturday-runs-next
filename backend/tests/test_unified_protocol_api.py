from __future__ import annotations

from fastapi.testclient import TestClient

pytest_plugins = ["tests.test_dashboard_api"]


def test_week_is_public_and_anchored_on_saturday(client: TestClient) -> None:
    """Открыт без логина, и любой день недели приводится к её субботе."""
    saturday = client.get("/api/protocol/week/2026-08-15")
    assert saturday.status_code == 200
    sunday = client.get("/api/protocol/week/2026-08-16")
    assert sunday.status_code == 200
    assert sunday.json()["saturday"] == saturday.json()["saturday"] == "2026-08-15"
    assert saturday.json()["week_start"] == "2026-08-10"
    assert saturday.json()["week_end"] == "2026-08-16"
    # Аноним видит протокол, но своих строк у него нет.
    assert saturday.json()["my_results"] == []


def test_week_without_date_falls_back_to_latest(client: TestClient) -> None:
    response = client.get("/api/protocol/week")
    # Пустая dev-БД законно отдаёт 404 «протоколов пока нет».
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        payload = response.json()
        assert payload["saturday"] == payload["latest_saturday"]


def test_weeks_list_is_sorted(client: TestClient) -> None:
    response = client.get("/api/protocol/weeks")
    assert response.status_code == 200
    weeks = response.json()["weeks"]
    assert [week["saturday"] for week in weeks] == sorted(week["saturday"] for week in weeks)


def test_unknown_platform_falls_back_to_all_systems(client: TestClient) -> None:
    """Мусор в ?platform= не 500-ит и не даёт пустой протокол — просто игнорируется."""
    response = client.get("/api/protocol/week/2026-08-15?platform=nosuchsystem")
    assert response.status_code == 200
    assert response.json()["scope_platform"] is None


def test_gender_tabs_keep_system_wide_counts(client: TestClient) -> None:
    """Таблетки пола считают всю систему, а плитки — выбранный зачёт.

    Иначе после выбора «женщины» таблетка «Мужчины» показывала бы 0 и обратно
    переключиться было бы не по чему.
    """
    everyone = client.get("/api/protocol/week/2026-08-15").json()
    women = client.get("/api/protocol/week/2026-08-15?gender=female").json()
    assert women["gender_counts"] == everyone["gender_counts"]
    # Плитка «финишёров» по полу не сужается, а знаменатель долей — да.
    assert women["summary"]["finishers"] == everyone["summary"]["finishers"]
    assert women["summary"]["scope_finishers"] == women["total"]
    # Зачёт нумеруется с единицы, каким бы срезом он ни был.
    if women["results"]:
        assert women["results"][0]["place"] == 1


def test_page_is_clamped_to_existing_pages(client: TestClient) -> None:
    response = client.get("/api/protocol/week/2026-08-15?page=99999&per_page=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == payload["pages"]
