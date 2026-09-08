"""«Куда дальше» по выбранным системам.

Кто-то коллекционирует только 5 вёрст — ближайший parkrun ему на плитке не
нужен (Дмитрий, 08.09.2026). Список систем хранится в users.tourism_platforms,
пустой список — все системы, как раньше.
"""

from __future__ import annotations

from app.services.home_distance_service import DistanceRow, nearest_unvisited_for_platforms


def _row(name: str, km: float | None, *codes: str) -> DistanceRow:
    return DistanceRow(
        catalog_identity_key=f"key-{name}",
        location_slug=name.lower(),
        name=name,
        city=None,
        region=None,
        distance_km=km,
        run_count=0,
        last_visit_date=None,
        is_home=False,
        is_paused=False,
        platform_codes=list(codes),
    )


ROWS = [
    _row("Parkrun-рядом", 3.0, "parkrun"),
    _row("Пять-вёрст-дальше", 12.0, "five_verst"),
    _row("Обе-системы", 40.0, "five_verst", "s95"),
    _row("Без-координат", None, "five_verst"),
]


def test_empty_filter_keeps_the_nearest_of_any_system() -> None:
    nearest = nearest_unvisited_for_platforms(ROWS, [])
    assert nearest is not None
    assert nearest.name == "Parkrun-рядом"


def test_filter_skips_other_systems() -> None:
    nearest = nearest_unvisited_for_platforms(ROWS, ["five_verst"])
    assert nearest is not None
    assert nearest.name == "Пять-вёрст-дальше"


def test_shared_location_matches_any_of_its_systems() -> None:
    nearest = nearest_unvisited_for_platforms(ROWS, ["s95"])
    assert nearest is not None
    assert nearest.name == "Обе-системы"


def test_rows_without_coordinates_never_become_nearest() -> None:
    assert nearest_unvisited_for_platforms(ROWS, ["runpark"]) is None
