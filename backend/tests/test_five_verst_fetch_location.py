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


def test_fetch_location_collects_course_description() -> None:
    """Описание снимается с уже загруженной страницы «О трассе» — без второго запроса."""
    home_html = "<html><head><title>5 вёрст | Сокольники | Москва</title></head></html>"
    course_html = (
        '<html><body><div class="entry-content">'
        "<p>Маршрут проходит по грунтовым дорожкам парка.</p>"
        '<div class="knd-block knd-block-info"><div class="knd-block-info__heading">'
        '<div class="knd-block-info__title">Как добраться?</div>'
        '<h2 class="knd-block-info__text">Москва, Сокольнический Вал, 1с1</h2></div>'
        '<div class="knd-block-info__row"><div class="knd-block-info__col">'
        '<div class="knd-block-info__content"><h3>Пешком</h3><p>От метро Сокольники 1,2 км.</p>'
        "</div></div></div></div>"
        "<h2>Правила безопасности на трассе</h2><p>Будьте внимательны.</p>"
        "</div></body></html>"
    )

    with patch("app.platform_adapters.five_verst.bulk_parser.fetch_html") as fetch_html:
        fetch_html.side_effect = [home_html, course_html]
        location, _combined = fetch_location("sokolniki")

    assert fetch_html.call_count == 2
    assert location.description is not None
    assert location.description.course_text == "Маршрут проходит по грунтовым дорожкам парка."
    assert location.description.travel_text == "Москва, Сокольнический Вал, 1с1"
    assert [section.title for section in location.description.travel_sections] == ["Пешком"]
    assert location.description.source_url == "https://5verst.ru/sokolniki/course/"
    assert "Правила безопасности" not in (location.description.course_text or "")
