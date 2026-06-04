from datetime import date

from app.activity_date import UNDATED_ACTIVITY_PLACEHOLDER, has_real_activity_date


def test_has_real_activity_date_rejects_placeholder() -> None:
    assert not has_real_activity_date(UNDATED_ACTIVITY_PLACEHOLDER)
    assert not has_real_activity_date(date(1970, 1, 1))
    assert has_real_activity_date(date(1970, 1, 2))
    assert has_real_activity_date(date(2022, 2, 26))
    assert not has_real_activity_date(None)
