from __future__ import annotations

import pytest

from app.services.official_map_locations import (
    DEFAULT_FIVE_VERST_CSV,
    is_official_map_location,
    official_map_slugs,
)

pytestmark = pytest.mark.skipif(
    not DEFAULT_FIVE_VERST_CSV.is_file(),
    reason="Official location CSV dumps are not available",
)


def test_official_map_slugs_include_strelka() -> None:
    slugs = official_map_slugs()
    assert ("five_verst", "strelka") in slugs


def test_official_map_slugs_exclude_testpark() -> None:
    assert not is_official_map_location("five_verst", "testpark")
    assert not is_official_map_location("five_verst", "run-only-933cfd2a")


def test_official_map_slugs_count() -> None:
    slugs = official_map_slugs()
    five_verst = {key for platform, key in slugs if platform == "five_verst"}
    s95 = {key for platform, key in slugs if platform == "s95"}
    assert len(five_verst) == 205
    assert len(s95) == 32
