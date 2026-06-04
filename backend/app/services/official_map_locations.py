from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

FIVE_VERST_URL_SUFFIX = "5verst.ru/"
S95_URL_SUFFIX = "/events/"

SERVICES_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = SERVICES_ROOT.parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if Path("/data/locations").is_dir():
    DATA_ROOT = Path("/")
elif (REPO_ROOT / "data").is_dir():
    DATA_ROOT = REPO_ROOT
else:
    DATA_ROOT = REPO_ROOT

DEFAULT_S95_CSV = DATA_ROOT / "data" / "locations" / "s95_locations.csv"
DEFAULT_FIVE_VERST_CSV = DATA_ROOT / "data" / "locations" / "five_verst_locations.csv"


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes", "да"}


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip().strip('"').replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _slug_from_link(link: str, *, platform_code: str) -> str | None:
    trimmed = (link or "").strip().rstrip("/")
    if not trimmed:
        return None
    if platform_code == "s95":
        marker = S95_URL_SUFFIX
        if marker not in trimmed:
            return None
        return trimmed.rsplit("/", 1)[-1]
    if FIVE_VERST_URL_SUFFIX not in trimmed:
        return None
    return trimmed.rsplit("/", 1)[-1]


def _load_slugs_from_csv(path: Path, platform_code: str) -> set[tuple[str, str]]:
    if not path.is_file():
        return set()

    slugs: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            latitude = _parse_float(row.get("latitude"))
            longitude = _parse_float(row.get("longitude"))
            if latitude is None or longitude is None:
                continue
            external_key = _slug_from_link(row.get("link_point") or "", platform_code=platform_code)
            if external_key:
                slugs.add((platform_code, external_key))
    return slugs


@lru_cache(maxsize=1)
def official_map_slugs(
    s95_csv: str = str(DEFAULT_S95_CSV),
    five_verst_csv: str = str(DEFAULT_FIVE_VERST_CSV),
) -> frozenset[tuple[str, str]]:
    slugs = _load_slugs_from_csv(Path(s95_csv), "s95")
    slugs |= _load_slugs_from_csv(Path(five_verst_csv), "five_verst")
    return frozenset(slugs)


def is_official_map_location(platform_code: str, external_key: str) -> bool:
    return (platform_code, external_key) in official_map_slugs()


def clear_official_map_slugs_cache() -> None:
    official_map_slugs.cache_clear()
