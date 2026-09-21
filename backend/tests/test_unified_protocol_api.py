from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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


def test_week_without_date_falls_back_to_latest(client: TestClient, db_session: Session) -> None:
    """Адрес без даты и поле latest_saturday считаются одной формулой.

    Раньше latest_saturday брался из кэшированного списка недель (где уже есть
    строки), а неделя по умолчанию — по максимальной дате события. В субботу
    утром они расходились, и тест падал по воскресеньям на пустой базе.
    Здесь сеем протокол с известной датой — от дня недели не зависим.
    """
    from uuid import uuid4

    from app.models import Event, Location, Participant, Platform, RunResult

    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()
    suffix = uuid4().hex[:8]
    location = Location(platform_id=platform.id, external_key=f"latest-{suffix}", name="Последний парк", country="Россия")
    participant = Participant(platform_id=platform.id, external_user_id=f"latest-{suffix}", display_name="Тест Последний")
    db_session.add_all([location, participant])
    db_session.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"latest-{suffix}",
        event_date=date(2026, 8, 15),
        event_number=1,
        title="Старт",
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(RunResult(event_id=event.id, participant_id=participant.id, external_result_key=f"latest-{suffix}", position=1, finish_time_sec=1500))
    db_session.commit()

    response = client.get("/api/protocol/week")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["saturday"] == payload["latest_saturday"]
    assert payload["latest_saturday"] >= "2026-08-15"


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
