from __future__ import annotations

from app.config import Settings
from app.parkrun.fetch.cdp_session import (
    _athlete_id_from_url,
    _format_cdp_connect_error,
    _is_captcha_title,
    _parkrun_host,
    _same_document_url,
)


def test_is_captcha_title() -> None:
    assert _is_captcha_title("Human Verification")
    assert _is_captcha_title("Attention Required")
    assert not _is_captcha_title("parkrun UK")


def test_parkrun_host_from_settings() -> None:
    settings = Settings(parkrun_base_url="https://www.parkrun.org.uk")
    assert _parkrun_host(settings) == "www.parkrun.org.uk"


def test_same_document_url() -> None:
    assert _same_document_url(
        "https://www.parkrun.org.uk/parkrunner/3197430/",
        "https://www.parkrun.org.uk/parkrunner/3197430",
    )
    assert not _same_document_url(
        "https://www.parkrun.org.uk/parkrunner/3197430/",
        "https://www.parkrun.org.uk/parkrunner/3197430/all/",
    )


def test_athlete_id_from_url() -> None:
    assert _athlete_id_from_url("https://www.parkrun.org.uk/parkrunner/3197430/all/") == "3197430"


def test_format_cdp_connect_error_500() -> None:
    msg = _format_cdp_connect_error(
        Exception("Unexpected status 500 when connecting to http://host.docker.internal:9222/json/version/"),
        "http://host.docker.internal:9222",
    )
    assert "parkrun-save-cdp-host" in msg
