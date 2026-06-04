from __future__ import annotations

import pytest

from scripts.import_platform_locations_csv import (
    DEFAULT_FIVE_VERST_CSV,
    DEFAULT_S95_CSV,
    import_to_db,
    slug_from_url,
)


def test_slug_from_url_s95() -> None:
    assert slug_from_url("https://s95.ru/events/kuskovo", platform_code="s95") == "kuskovo"
    assert slug_from_url("https://s95.by/events/bs", platform_code="s95") == "bs"


def test_slug_from_url_five_verst() -> None:
    assert slug_from_url("https://5verst.ru/vysota/", platform_code="five_verst") == "vysota"


def test_import_platform_locations_csv_dry_run() -> None:
    if not DEFAULT_S95_CSV.is_file() or not DEFAULT_FIVE_VERST_CSV.is_file():
        pytest.skip("location CSV dumps not available in this environment")

    stats = import_to_db(
        s95_csv=DEFAULT_S95_CSV,
        five_verst_csv=DEFAULT_FIVE_VERST_CSV,
        dry_run=True,
    )
    assert stats["s95"]["rows"] > 0
    assert stats["five_verst"]["rows"] > 0
    assert stats["s95"]["imported"] > 0
    assert stats["five_verst"]["imported"] > 0
