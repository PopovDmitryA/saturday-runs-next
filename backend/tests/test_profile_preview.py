from datetime import date

from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.profile_preview import build_recent_preview_activities


def test_build_recent_merges_runs_and_volunteering() -> None:
    runs = [
        CanonicalRunResult(
            external_result_key="r1",
            event_date=date(2026, 5, 20),
            external_user_id="1",
            participant_name="A",
            position=1,
            finish_time_sec=23 * 60 + 41,
            finish_time_display="00:23:41",
            location_name="Троицк",
        ),
        CanonicalRunResult(
            external_result_key="r2",
            event_date=date(2026, 5, 10),
            external_user_id="1",
            participant_name="A",
            position=2,
            finish_time_sec=24 * 60,
            finish_time_display="00:24:00",
            location_name="Горький",
        ),
    ]
    volunteering = [
        CanonicalVolunteerResult(
            external_result_key="v1",
            event_date=date(2026, 5, 15),
            external_user_id="1",
            participant_name="A",
            role="Маршал",
            location_name="Сокольники",
        ),
    ]
    recent = build_recent_preview_activities(runs, volunteering, limit=3)
    assert len(recent) == 3
    assert recent[0].kind == "run"
    assert recent[0].event_date == date(2026, 5, 20)
    assert recent[0].location_name == "Троицк"
    assert recent[0].finish_time_display == "00:23:41"
    assert recent[1].kind == "volunteer"
    assert recent[1].role == "Маршал"
    assert recent[2].event_date == date(2026, 5, 10)
