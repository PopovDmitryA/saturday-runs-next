"""Автоопределение профилей участника (фокус дашборда)."""

from app.services.dashboard_focus import (
    FOCUS_PROFILES,
    detect_focus_profiles,
    normalize_focus_selection,
)


def test_detect_empty_for_newbie() -> None:
    assert detect_focus_profiles({}, total_volunteering=0) == []


def test_detect_volunteer_by_occasions() -> None:
    assert detect_focus_profiles({}, total_volunteering=10) == ["volunteer"]
    assert detect_focus_profiles({}, total_volunteering=9) == []


def test_detect_tourist_by_locations() -> None:
    assert detect_focus_profiles({"unique_run_locations": 10}, total_volunteering=0) == ["tourist"]


def test_detect_racer_by_any_speed_signal() -> None:
    assert detect_focus_profiles({"wins_count": 1}, total_volunteering=0) == ["racer"]
    assert detect_focus_profiles({"avg_position": 8.5}, total_volunteering=0) == ["racer"]
    assert detect_focus_profiles({"best_finish_time_sec": 20 * 60}, total_volunteering=0) == [
        "racer"
    ]
    assert detect_focus_profiles(
        {"location_records": {"current_count": 2}}, total_volunteering=0
    ) == ["racer"]
    # Утерянный рекорд (current 0) сигналом скорости не считается.
    assert (
        detect_focus_profiles({"location_records": {"lost_count": 3}}, total_volunteering=0) == []
    )


def test_detect_regular_by_consistency_or_streak() -> None:
    assert detect_focus_profiles({"saturday_consistency_pct": 40.0}, total_volunteering=0) == [
        "regular"
    ]
    assert detect_focus_profiles({"saturday_streak_current": 8}, total_volunteering=0) == [
        "regular"
    ]
    assert detect_focus_profiles({"saturday_consistency_pct": 39.9}, total_volunteering=0) == []


def test_detect_returns_canonical_order() -> None:
    detected = detect_focus_profiles(
        {
            "saturday_streak_current": 10,
            "wins_count": 2,
            "unique_run_locations": 50,
        },
        total_volunteering=100,
    )
    assert detected == list(FOCUS_PROFILES)


def test_normalize_orders_and_validates() -> None:
    assert normalize_focus_selection(None) is None
    assert normalize_focus_selection([]) == []
    assert normalize_focus_selection(["volunteer", "regular"]) == ["regular", "volunteer"]
    try:
        normalize_focus_selection(["hacker"])
    except ValueError as exc:
        assert "hacker" in str(exc)
    else:
        raise AssertionError("неизвестный профиль должен отклоняться")
