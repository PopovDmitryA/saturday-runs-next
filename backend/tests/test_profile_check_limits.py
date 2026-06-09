from __future__ import annotations

from datetime import date

from app.models import SyncJobTrigger
from app.platform_adapters.canonical import CanonicalRunResult, CanonicalVolunteerResult
from app.sync.profile_check_limits import profile_activity_check_limit
from app.sync.profile_protocol_queue import collect_profile_event_refs


def test_profile_activity_check_limit_manual_and_linking_are_unlimited() -> None:
    assert profile_activity_check_limit(SyncJobTrigger.manual, default=10) is None
    assert profile_activity_check_limit(SyncJobTrigger.linking, default=10) is None


def test_profile_activity_check_limit_login_uses_default() -> None:
    assert profile_activity_check_limit(SyncJobTrigger.login, default=10) == 10


def _run(day: int) -> CanonicalRunResult:
    return CanonicalRunResult(
        external_result_key=f"user:2024-01-{day:02d}:loc",
        event_date=date(2024, 1, day),
        external_user_id="user",
        participant_name="Runner",
        position=day,
        finish_time_sec=1500 + day,
        finish_time_display="00:25:00",
        location_external_key="loc",
        location_name="Loc",
    )


def test_collect_profile_event_refs_without_limit_includes_all_runs() -> None:
    runs = [_run(day) for day in range(1, 21)]
    refs = collect_profile_event_refs(runs, [], platform_code="s95", limit=None)
    assert len(refs) == 20


def test_collect_profile_event_refs_respects_limit() -> None:
    runs = [_run(day) for day in range(1, 21)]
    refs = collect_profile_event_refs(runs, [], platform_code="s95", limit=5)
    assert len(refs) == 5


def test_collect_profile_event_refs_without_limit_includes_volunteering_after_runs() -> None:
    runs = [_run(1)]
    volunteering = [
        CanonicalVolunteerResult(
            external_result_key=f"user:2024-02-{day:02d}:loc:role",
            event_date=date(2024, 2, day),
            external_user_id="user",
            participant_name="Runner",
            role="Фотограф",
            location_external_key="loc",
            location_name="Loc",
        )
        for day in range(1, 16)
    ]
    refs = collect_profile_event_refs(runs, volunteering, platform_code="five_verst", limit=None)
    assert len(refs) == 16
