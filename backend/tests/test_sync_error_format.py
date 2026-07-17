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


def test_parkrun_ban_is_not_labelled_as_s95():
    raw = (
        "parkrun: Ban/protection page detected for "
        "https://www.parkrun.org.uk/parkrunner/2766046/ (bytes=9485 "
        "title='Human Verification' parkrun_page=False markers=captcha; "
        "likely captcha/WAF (captcha))"
    )
    human = humanize_sync_error_message(raw)
    assert human is not None
    assert "parkrun" in human
    assert "С95" not in human


def test_five_verst_cooldown_names_five_verst():
    human = humanize_sync_error_message("five_verst: 5verst fetch in cooldown until 1784356631 (now 1784300000)")
    assert human is not None
    assert "5 вёрст" in human
    assert "С95" not in human


def test_s95_forbidden_still_names_s95():
    human = humanize_sync_error_message("s95: HTTP 403 Forbidden from S95 for https://s95.ru/athletes/1")
    assert human is not None
    assert "С95" in human


def test_ban_without_platform_prefix_stays_neutral():
    human = humanize_sync_error_message("Ban/protection page detected for https://example.org/x")
    assert human is not None
    assert "С95" not in human
    assert "платформы" in human


def test_multi_platform_errors_are_humanized_per_platform():
    raw = (
        "s95: HTTP 403 Forbidden from S95 for https://s95.ru/athletes/1; "
        "parkrun: parkrun fetch in cooldown until 1784356631 (now 1784300000)"
    )
    human = humanize_sync_error_message(raw)
    assert human is not None
    s95_part, _, parkrun_part = human.partition("; parkrun: ")
    assert "С95" in s95_part and "parkrun" not in s95_part
    assert "parkrun" in parkrun_part and "С95" not in parkrun_part


def test_single_platform_error_keeps_raw_text_and_prefix():
    raw = "s95: It looks like you are using Playwright Sync API inside the asyncio loop."
    assert humanize_sync_error_message(raw) == raw
