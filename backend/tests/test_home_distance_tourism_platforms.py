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


def test_location_visited_in_other_system_is_unvisited_for_chosen_one() -> None:
    tsaritsyno = _row("Царицыно", 5.0, "s95", "runpark")
    tsaritsyno = DistanceRow(**{**tsaritsyno.__dict__, "visited_platform_codes": ["s95"]})
    rows = [tsaritsyno]
    # Без фильтра — бывала на S95, значит посещена.
    assert nearest_unvisited_for_platforms(rows, []) is None
    # В S95 бывала — не цель; в RunPark не бывала — цель.
    assert nearest_unvisited_for_platforms(rows, ["s95"]) is None
    nearest = nearest_unvisited_for_platforms(rows, ["runpark"])
    assert nearest is not None
    assert nearest.name == "Царицыно"


def test_unvisited_rows_keep_partly_visited_locations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import home_distance_service as service

    def point(key: str, *codes: str) -> dict[str, object]:
        return {
            "catalog_identity_key": key,
            "location_slug": key,
            "name": key,
            "latitude": 55.0,
            "longitude": 37.0,
            "platform_codes": list(codes),
        }

    monkeypatch.setattr(
        service,
        "list_catalog_map_locations",
        lambda db: {
            "points": [
                point("both", "s95", "runpark"),
                point("only-s95", "s95"),
                point("fresh", "runpark"),
            ]
        },
    )
    rows = service._unvisited_rows(None, {"both": {"s95"}, "only-s95": {"s95"}}, (55.0, 37.0))  # type: ignore[arg-type]
    by_key = {row.catalog_identity_key: row for row in rows}
    assert set(by_key) == {"both", "fresh"}
    assert by_key["both"].visited_platform_codes == ["s95"]
    assert service.is_unvisited_for_platforms(by_key["both"], {"runpark"})
    assert not service.is_unvisited_for_platforms(by_key["both"], {"s95"})
    assert not service.is_unvisited_for_platforms(by_key["both"], set())
    assert service.is_unvisited_for_platforms(by_key["fresh"], set())


def test_api_schema_keeps_visited_platform_codes() -> None:
    # 29.10.2026 поле молча выбрасывала схема ответа: бэкенд считал верно, а
    # модалка видела пустой список и показывала посещённые площадки.
    from app.schemas.dashboard import HomeDistanceLocationResponse

    row = DistanceRow(**{**_row("Царицыно", 5.0, "s95", "runpark").__dict__, "visited_platform_codes": ["s95"]})
    response = HomeDistanceLocationResponse.model_validate(row.as_dict())
    assert response.model_dump()["visited_platform_codes"] == ["s95"]
