from __future__ import annotations

from app.parkrun.fetch.cdp_session import _is_captcha_title, _same_document_url
from app.parkrun.fetch.diagnostics import inspect_html_response


def is_parkrun_page_ready(*, title: str, html: str, url: str) -> bool:
    """True when the document at url is not captcha/WAF and looks fetchable."""
    if _is_captcha_title(title):
        return False
    inspection = inspect_html_response(html, url=url)
    return not inspection.is_protection


def page_url_matches_target(page_url: str, target_url: str) -> bool:
    return _same_document_url(page_url, target_url)
