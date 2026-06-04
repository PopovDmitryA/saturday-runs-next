from app.services.sync_error_format import (
    UNLINK_CANCELLED_MESSAGE,
    humanize_sync_error_message,
    present_sync_error,
)


def test_humanize_unlink_cancelled():
    assert humanize_sync_error_message(UNLINK_CANCELLED_MESSAGE) == UNLINK_CANCELLED_MESSAGE


def test_humanize_unique_violation_with_event_name():
    raw = (
        "(psycopg.errors.UniqueViolation) duplicate key value violates unique constraint "
        '"uq_events_platform_location_event_date"\n'
        "DETAIL: Key (platform_id, location_id, event_date)=(...) already exists.\n"
        "[parameters: {'event_name': 'Первоуральск #94', 'event_date': datetime.date(2026, 5, 9)}]"
    )
    human = humanize_sync_error_message(raw)
    assert human is not None
    assert "Первоуральск #94" in human
    assert "INSERT INTO" not in human


def test_present_sync_error_splits_technical_details():
    raw = "x" * 400 + "\nINSERT INTO events (id) VALUES (1)"
    summary, details = present_sync_error(raw)
    assert summary is not None
    assert details is not None
    assert len(summary) < len(details)


def test_present_sync_error_no_details_for_friendly():
    summary, details = present_sync_error(UNLINK_CANCELLED_MESSAGE)
    assert summary == UNLINK_CANCELLED_MESSAGE
    assert details is None
