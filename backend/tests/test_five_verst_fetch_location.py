from __future__ import annotations

from unittest.mock import patch

from app.platform_adapters.five_verst.bulk_parser import fetch_location
from app.platform_adapters.five_verst.http import NotFoundError


def test_fetch_location_continues_when_course_page_missing() -> None:
    home_html = "<html><head><title>5 вёрst | Test Park | City</title></head></html>"

    with patch("app.platform_adapters.five_verst.bulk_parser.fetch_html") as fetch_html:
        fetch_html.side_effect = [home_html, NotFoundError("Страница не найдена: course")]
        location, combined = fetch_location("testpark")

    assert location.external_key == "testpark"
    assert location.latitude is None
    assert "Test Park" in location.name
    assert combined == home_html
