from __future__ import annotations

from datetime import date

from app.services.location_catalog_table_service import _build_visit_index, _earliest_visit


def test_visit_index_uses_earliest_date() -> None:
    details = {
        "locations": [
            {
                "catalog_identity_key": "park-a",
                "platforms": [
                    {
                        "platform_code": "five_verst",
                        "run_dates": ["2026-05-23", "2026-05-16"],
                        "volunteer_dates": ["2026-05-09"],
                    }
                ],
            }
        ]
    }
    assert _build_visit_index(details)["park-a"] == {"five_verst": date(2026, 5, 9)}


def test_visit_index_keeps_earliest_across_systems() -> None:
    """Строки каталога заводятся только для 5 вёрст/s95/runpark, поэтому визит в
    parkrun-эпоху обязан попасть в тот же ключ — иначе локация покажется
    непосещённой (репорт: Сокольники, Петергоф, Нижний пруд, Чертаново)."""
    details = {
        "locations": [
            {
                "catalog_identity_key": "sokolniki",
                "platforms": [
                    {"platform_code": "five_verst", "run_dates": ["2025-04-05"], "volunteer_dates": []},
                    {"platform_code": "parkrun", "run_dates": ["2021-05-15"], "volunteer_dates": []},
                ],
            }
        ]
    }
    # Ключ — только локация; разбивка по системам сохраняется, чтобы фронт мог
    # пересчитать отметку под фильтр систем.
    index = _build_visit_index(details)
    assert index["sokolniki"] == {
        "five_verst": date(2025, 4, 5),
        "parkrun": date(2021, 5, 15),
    }
    assert _earliest_visit(index["sokolniki"]) == (date(2021, 5, 15), "parkrun")


def test_visit_index_covers_parkrun_only_location() -> None:
    details = {
        "locations": [
            {
                "catalog_identity_key": "petergof",
                "platforms": [
                    {"platform_code": "parkrun", "run_dates": ["2022-01-29"], "volunteer_dates": []},
                ],
            }
        ]
    }
    assert _build_visit_index(details)["petergof"] == {"parkrun": date(2022, 1, 29)}
    assert _earliest_visit({}) is None


def test_visit_index_ignores_placeholder_dates() -> None:
    """1970-01-01 — заглушка для активности без даты, посещением её считать нельзя."""
    details = {
        "locations": [
            {
                "catalog_identity_key": "undated",
                "platforms": [
                    {"platform_code": "parkrun", "run_dates": ["1970-01-01"], "volunteer_dates": []},
                ],
            }
        ]
    }
    assert _build_visit_index(details) == {}
