from app.parkrun.fetch.diagnostics import inspect_html_response


def test_inspect_detects_captcha_marker() -> None:
    html = "<html><title>Challenge</title><body>g-recaptcha</body></html>"
    inspection = inspect_html_response(html, url="https://www.parkrun.org.uk/")
    assert inspection.is_protection
    assert "recaptcha" in inspection.matched_markers


def test_inspect_accepts_parkrun_profile() -> None:
    html = (
        "<html><title>parkrunner</title><body>"
        "parkrun.org.uk parkrunner 12345 " + ("x" * 600) +
        "</body></html>"
    )
    inspection = inspect_html_response(html, url="https://www.parkrun.org.uk/parkrunner/1/")
    assert not inspection.is_protection
    assert inspection.looks_like_parkrun
