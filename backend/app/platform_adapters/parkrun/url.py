from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import get_settings
from app.s95.parkrun import normalize_parkrun_athlete_id

PARKRUNNER_PATH_RE = re.compile(r"/parkrunner/(\d+)(?:/|$)", re.IGNORECASE)
# Latin A, Cyrillic А, or digits only
BARCODE_RE = re.compile(r"^([AАaа])?(\d{1,9})$")


class InvalidProfileUrlError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedParkrunProfile:
    athlete_id: str
    profile_url: str
    all_results_url: str


def _base_url() -> str:
    return get_settings().parkrun_base_url.rstrip("/")


def parse_profile_input(raw: str) -> ParsedParkrunProfile:
    value = raw.strip()
    if not value:
        raise InvalidProfileUrlError("Укажите ссылку parkrun или штрихкод")

    if "://" in value or value.startswith("www."):
        return parse_profile_url(value if "://" in value else f"https://{value}")

    barcode_match = BARCODE_RE.match(value.replace(" ", ""))
    if barcode_match:
        athlete_id = normalize_parkrun_athlete_id(value)
        base = _base_url()
        return ParsedParkrunProfile(
            athlete_id=athlete_id,
            profile_url=f"{base}/parkrunner/{athlete_id}/",
            all_results_url=f"{base}/parkrunner/{athlete_id}/all/",
        )

    raise InvalidProfileUrlError(
        "Ожидается ссылка parkrun.org.uk/parkrunner/… или штрихкод: A123456, А123456 (латиница/кириллица) или 123456"
    )


def parse_profile_url(url: str) -> ParsedParkrunProfile:
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host and "parkrun.org.uk" not in host:
        raise InvalidProfileUrlError("Поддерживается только parkrun.org.uk")

    match = PARKRUNNER_PATH_RE.search(parsed.path or "")
    if not match:
        raise InvalidProfileUrlError("В ссылке не найден ID parkrunner")

    athlete_id = match.group(1)
    base = _base_url()
    return ParsedParkrunProfile(
        athlete_id=athlete_id,
        profile_url=f"{base}/parkrunner/{athlete_id}/",
        all_results_url=f"{base}/parkrunner/{athlete_id}/all/",
    )


def is_valid_profile_input(raw: str) -> bool:
    try:
        parse_profile_input(raw)
        return True
    except InvalidProfileUrlError:
        return False
