"""Правило: locations.country хранится по-русски, одно название на одну страну."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.geo.country_names import normalize_country_name
from app.models import Location, Platform
from app.platform_adapters.canonical import CanonicalLocation, CanonicalRunResult
from app.sync import upsert


def test_normalize_country_name() -> None:
    assert normalize_country_name("United Kingdom") == "Великобритания"
    assert normalize_country_name("  united kingdom  ") == "Великобритания"
    assert normalize_country_name("Russia") == "Россия"
    assert normalize_country_name("Белоруссия") == "Беларусь"
    assert normalize_country_name("Россия") == "Россия"
    # Незнакомое не выдумываем, а пустое — это отсутствие страны, а не значение:
    # иначе `country or row.country` в upsert_location затрёт уже известное.
    assert normalize_country_name("Freedonia") == "Freedonia"
    assert normalize_country_name("   ") is None
    assert normalize_country_name(None) is None


def _platform(db: Session, code: str) -> Platform:
    row = db.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        pytest.skip(f"platform {code} not seeded")
    return row


def test_upsert_location_stores_country_in_russian(db_session: Session) -> None:
    """Английское название не доезжает до БД ни одним путём записи."""
    platform = _platform(db_session, "parkrun")
    slug = f"country-{uuid4().hex[:8]}"

    row, _ = upsert.upsert_location(
        db_session,
        platform,
        CanonicalLocation(external_key=slug, name="Bushy Park", country="United Kingdom"),
    )

    assert row.country == "Великобритания"


def test_profile_import_does_not_stamp_parkrun_with_uk(db_session: Session) -> None:
    """Профильный импорт parkrun страну не выдумывает.

    parkrun.org.uk — общий вход в мировой каталог: прежняя заглушка «United
    Kingdom» помечала Британией Якутск и Йошкар-Олу. Пусто честнее, страну
    добирает бэкфилл по координатам.
    """
    platform = _platform(db_session, "parkrun")
    slug = f"profile-{uuid4().hex[:8]}"

    upsert.import_profile_run_results(
        db_session,
        platform,
        [
            CanonicalRunResult(
                external_result_key=f"{slug}:2026-07-25:1",
                external_user_id="123456",
                participant_name="Тестовый Бегун",
                event_date=date(2026, 7, 25),
                location_external_key=slug,
                location_name="Yakutsk Dokhsun",
                position=1,
                finish_time_sec=1500,
                finish_time_display="25:00",
            )
        ],
    )

    row = (
        db_session.query(Location)
        .filter(Location.platform_id == platform.id, Location.external_key == slug)
        .one()
    )
    assert row.country is None
