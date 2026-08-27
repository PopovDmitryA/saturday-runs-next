"""Красная плашка отмены на странице площадки s95.

Фикстура снята со страницы Иванова 27.08.2026 — первой отмены ближайшего
старта у s95, которую мы увидели. До неё считалось, что недельных отмен эта
система не публикует вовсе, и любая неработающая карточка означала закрытие.
"""

from __future__ import annotations

from pathlib import Path

from app.s95.parsers.location import parse_location_alert

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_cancellation_alert_with_reason() -> None:
    html = (FIXTURES / "s95_location_cancelled.html").read_text(encoding="utf-8")

    alert = parse_location_alert(html)

    assert alert is not None
    assert alert.is_cancelled is True
    assert alert.reason == "Отмена забега 29 августа. Увидимся на Набережной 5 сентября"


def test_parse_cancellation_alert_without_reason() -> None:
    """Причина необязательна: организатор может не написать ничего."""
    html = """
    <div class="alert alert-danger" role="alert">
      <div><strong>Внимание!</strong> Ближайший старт отменён.</div>
    </div>
    """

    alert = parse_location_alert(html)

    assert alert is not None
    assert alert.is_cancelled is True
    assert alert.reason is None


def test_no_alert_on_regular_page() -> None:
    """Красные кнопки страницы («Зарегистрироваться») за отмену не считаются."""
    html = """
    <a class="btn btn-danger" href="/user/login">Войти</a>
    <div class="card border-primary"><div class="card-body">Трасса ровная</div></div>
    """

    assert parse_location_alert(html) is None
