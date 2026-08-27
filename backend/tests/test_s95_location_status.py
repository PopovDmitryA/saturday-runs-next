"""Что означает `active=false` в реестре s95 — отмену субботы или закрытие."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.s95.api_client import S95ApiLocation
from app.sync.s95_location_status import resolve_s95_location_status

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _entry(active: bool) -> S95ApiLocation:
    return S95ApiLocation(
        domain="https://s95.ru",
        slug="ivanovo",
        name="Иваново",
        town="Иваново",
        place="Центральная набережная",
        active=active,
    )


def test_active_location_does_not_touch_the_page() -> None:
    """У работающей площадки плашки нет по определению — страницу не грузим."""

    def _fetch(url: str) -> str:
        raise AssertionError(f"страница не должна грузиться: {url}")

    status = resolve_s95_location_status(_entry(active=True), fetch_html=_fetch)

    assert status.is_paused is False
    assert status.is_cancelled is False


def test_inactive_with_alert_is_a_cancellation() -> None:
    html = (FIXTURES / "s95_location_cancelled.html").read_text(encoding="utf-8")

    status = resolve_s95_location_status(_entry(active=False), fetch_html=lambda url: html)

    assert status.is_cancelled is True
    assert status.is_paused is False
    assert status.cancel_reason == "Отмена забега 29 августа. Увидимся на Набережной 5 сентября"


def test_inactive_without_alert_is_a_closed_venue() -> None:
    status = resolve_s95_location_status(
        _entry(active=False),
        fetch_html=lambda url: "<div class='card'>Общая информация</div>",
    )

    assert status.is_paused is True
    assert status.is_cancelled is False
    assert status.cancel_reason is None


def test_page_url_is_taken_from_the_registry_domain() -> None:
    seen: list[str] = []

    def _fetch(url: str) -> str:
        seen.append(url)
        return ""

    resolve_s95_location_status(_entry(active=False), fetch_html=_fetch)

    assert seen == ["https://s95.ru/events/ivanovo"]


def test_fetch_error_is_not_swallowed() -> None:
    """Ошибку наверх: вызывающий сам решит, что делать с непрочитанной страницей."""

    def _fetch(url: str) -> str:
        raise RuntimeError("503")

    with pytest.raises(RuntimeError):
        resolve_s95_location_status(_entry(active=False), fetch_html=_fetch)
