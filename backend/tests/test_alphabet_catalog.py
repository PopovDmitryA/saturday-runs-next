"""Каталог букв челленджа «Алфавит» — выборка локаций из БД (нужна БД)."""

from __future__ import annotations

from uuid import uuid4

import pytest

pytest_plugins = ["tests.test_dashboard_api"]

from app.models import Location, Platform
from app.services.achievements_service import _alphabet_available_names


def _platform(db, code: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code)
        db.add(platform)
        db.flush()
    return platform


def _location(db, platform: Platform, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"alphabet-{uuid4().hex[:8]}",
        name=name,
    )
    db.add(location)
    db.flush()
    return location


def test_available_letters_follow_platform_filter(db_session) -> None:
    """Фильтр по системе меняет каталог букв: локация S95 на «Ц» не делает «Ц»
    доступной в скоупе 5 вёрст (в реальных данных «Ц» есть только у S95)."""
    try:
        s95 = _platform(db_session, "s95")
    except Exception:
        pytest.skip("Database not available")
    five_verst = _platform(db_session, "five_verst")

    s95_name = f"Цветной бульвар {uuid4().hex[:6]}"
    _location(db_session, s95, s95_name)
    five_verst_name = f"Кузьминки {uuid4().hex[:6]}"
    _location(db_session, five_verst, five_verst_name)

    s95_letters = _alphabet_available_names(db_session, "s95")
    five_verst_letters = _alphabet_available_names(db_session, "five_verst")

    assert s95_name in s95_letters["Ц"]
    assert s95_name not in five_verst_letters.get("Ц", set())
    assert five_verst_name in five_verst_letters["К"]
    assert five_verst_name not in s95_letters.get("К", set())


def test_parkrun_locations_out_of_cross_platform_catalog(db_session) -> None:
    """Сквозной вид parkrun не считает — значит и буквы его локаций в каталог
    не попадают, иначе клетка была бы заведомо незакрываемой."""
    try:
        parkrun = _platform(db_session, "parkrun")
    except Exception:
        pytest.skip("Database not available")

    name = f"Тропарёво {uuid4().hex[:6]}"
    _location(db_session, parkrun, name)

    assert name not in _alphabet_available_names(db_session, None).get("Т", set())
    assert name in _alphabet_available_names(db_session, "parkrun")["Т"]
