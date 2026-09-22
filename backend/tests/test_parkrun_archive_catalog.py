"""География parkrun-локаций по архивному каталогу площадок.

Живой events.json знает только действующие площадки, а у нас в базе живут и
закрытые — им страну и координаты даёт слепок из архивных снимков каталога.
Тест сторожит три вещи: что слепок отвечает на реальные ключи из базы (включая
покалеченные не-ASCII слаги), что каждому countrycode в нём есть русское
название и что неоднозначные названия из индекса выброшены.
"""

from __future__ import annotations

import json

import pytest

from app.geo.country_names import normalize_country_name
from scripts.backfill_parkrun_from_archive import (
    ARCHIVE_INDEX,
    COUNTRY_BY_CODE,
    ArchiveCatalog,
    CatalogEvent,
    normalize_name,
)


@pytest.fixture(scope="module")
def catalog() -> ArchiveCatalog:
    built = ArchiveCatalog()
    built.add_dump(json.loads(ARCHIVE_INDEX.read_text(encoding="utf-8")))
    return built


@pytest.mark.parametrize(
    ("external_key", "name", "expected"),
    [
        # Действующие площадки — есть и в живом каталоге.
        ("bushy", "Bushy Park", "Великобритания"),
        ("kuechenholz", "Küchenholz", "Германия"),
        ("orakeibay", "Ōrākei Bay", "Новая Зеландия"),
        # Закрытые: в живом каталоге их нет, отвечает архив.
        ("pieschener-allee", "Pieschener Allee", "Германия"),
        ("firenze", "Firenze", "Италия"),
        ("victoria-dock", "Victoria Dock", "Великобритания"),
        # Страна ушла из parkrun целиком — код 31 живёт только в снимках.
        ("bois-de-boulogne", "Bois de Boulogne", "Франция"),
        # Слаг покалечен парсером профиля (la Ramée → la-ram-e) — спасает название.
        ("la-ram-e", "la Ramée", "Франция"),
    ],
)
def test_country_from_catalog(catalog: ArchiveCatalog, external_key: str, name: str, expected: str) -> None:
    event = catalog.lookup(external_key, name)
    assert event is not None
    assert normalize_country_name(COUNTRY_BY_CODE[event.country_code]) == expected


def test_coordinates_from_catalog(catalog: ArchiveCatalog) -> None:
    """У закрытой площадки координаты берутся из архива — без них поездка в
    Лондон считается нулём километров."""
    event = catalog.lookup("victoria-dock", "Victoria Dock")
    assert event is not None
    assert event.latitude == pytest.approx(51.5, abs=0.2)
    assert event.longitude == pytest.approx(0.0, abs=0.3)


def test_unknown_location_stays_unknown(catalog: ArchiveCatalog) -> None:
    """Незнакомую площадку не приписываем к стране наугад — пусть остаётся пустой."""
    assert catalog.lookup("freedonia-park", "Freedonia Park") is None


def test_every_code_in_index_has_russian_name(catalog: ArchiveCatalog) -> None:
    codes = {event.country_code for event in catalog.events.values()}
    missing = sorted(codes - set(COUNTRY_BY_CODE))
    assert not missing, f"нет названия для countrycode: {missing} — дополни COUNTRY_BY_CODE"
    for code in sorted(codes):
        russian = normalize_country_name(COUNTRY_BY_CODE[code])
        assert russian and russian != COUNTRY_BY_CODE[code], (
            f"countrycode {code}: «{COUNTRY_BY_CODE[code]}» не переводится — "
            "дополни RUSSIAN_COUNTRY_BY_ALIAS в app/geo/country_names.py"
        )


def test_ambiguous_name_dropped_from_index() -> None:
    """Одно название у двух площадок — не подсказка, а ловушка: выбрасываем."""
    built = ArchiveCatalog()
    built.add_event("riversidegrandrapids", CatalogEvent(98, 42.99, -85.67))
    built.add_event("riversideuk", CatalogEvent(97, 51.5, -0.1))
    built.add_name(normalize_name("Riverside"), "riversidegrandrapids")
    built.add_name(normalize_name("Riverside"), "riversideuk")
    assert built.lookup(None, "Riverside") is None
    # Слаг при этом по-прежнему отвечает.
    assert built.lookup("riverside-uk", "Riverside") is not None


def test_first_source_wins() -> None:
    """Снимки подаются от свежего к старому, и свежий источник не перебивается."""
    built = ArchiveCatalog()
    built.add_event("bushy", CatalogEvent(97, 51.410992, -0.335791))
    built.add_event("bushy", CatalogEvent(97, 0.0, 0.0))
    assert built.events["bushy"].latitude == pytest.approx(51.410992)


def test_reviewed_mismatch_tables_do_not_overlap() -> None:
    """Площадка не может одновременно «каталог прав» и «база права»."""
    from scripts.backfill_parkrun_from_archive import CATALOG_WINS, DATABASE_WINS

    assert not (CATALOG_WINS & set(DATABASE_WINS))


def test_reviewed_mismatches_really_disagree_with_catalog(catalog: ArchiveCatalog) -> None:
    """Разбор описывает живые расхождения: площадка есть в каталоге, а страна
    в таблице DATABASE_WINS от каталожной отличается — иначе строка мусорная."""
    from scripts.backfill_parkrun_from_archive import CATALOG_WINS, DATABASE_WINS

    for slug in sorted(CATALOG_WINS | set(DATABASE_WINS)):
        event = catalog.lookup(slug, None)
        assert event is not None, f"{slug}: площадки нет в каталоге, расхождению неоткуда взяться"
    for slug, expected in sorted(DATABASE_WINS.items()):
        event = catalog.lookup(slug, None)
        assert event is not None
        assert normalize_country_name(COUNTRY_BY_CODE[event.country_code]) != expected, (
            f"{slug}: каталог согласен с базой, строка в DATABASE_WINS лишняя"
        )
