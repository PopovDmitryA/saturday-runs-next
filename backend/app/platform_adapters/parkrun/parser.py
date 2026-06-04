from __future__ import annotations

from app.parkrun.errors import (
    ParkrunBanDetected,
    ParkrunFetchTimeout,
    ParkrunProfileNotFound,
    ParkrunProfileParseError,
)
from app.parkrun.parsers.athlete import fetch_athlete_activity, fetch_profile_preview
from app.platform_adapters.canonical import ProfilePreview
from app.platform_adapters.parkrun.url import InvalidProfileUrlError, parse_profile_input

ProfileParseError = ParkrunProfileParseError
ProfileNotFoundError = ParkrunProfileNotFound


def fetch_profile_preview_from_input(raw: str) -> ProfilePreview:
    try:
        parsed = parse_profile_input(raw)
    except InvalidProfileUrlError as exc:
        raise ProfileParseError(str(exc)) from exc
    try:
        return fetch_profile_preview(parsed)
    except ParkrunBanDetected as exc:
        raise ProfileParseError(str(exc)) from exc
    except ParkrunFetchTimeout as exc:
        raise ProfileParseError(str(exc)) from exc


def fetch_athlete_by_id(athlete_id: str):
    from app.platform_adapters.parkrun.url import parse_profile_url

    parsed = parse_profile_url(f"https://www.parkrun.org.uk/parkrunner/{athlete_id}/")
    return fetch_athlete_activity(parsed)
