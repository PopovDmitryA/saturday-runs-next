from __future__ import annotations

from app.platform_adapters.canonical import (
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.platform_adapters.s95.url import ParsedAthleteUrl, parse_athlete_url
from app.s95.fetch import fetch_page_html
from app.s95.parkrun import is_parkrun_eligible_barcode
from app.config import get_settings
from app.s95.parsers.athlete import (
    AthleteParseError,
    enrich_participant_activity_totals,
    parse_athlete_html,
    parse_athlete_runs_html,
    parse_athlete_volunteering_html,
    participant_to_preview,
)

class ProfileNotFoundError(LookupError):
    pass


class ProfileParseError(ValueError):
    pass


def fetch_and_parse_profile(parsed_url: ParsedAthleteUrl) -> CanonicalParticipant:
    settings = get_settings()
    html = fetch_page_html(
        parsed_url.canonical_url,
        reason="athlete_profile",
        extra_wait_ms=settings.s95_playwright_athlete_wait_ms,
    )
    try:
        participant = parse_athlete_html(html, parsed_url.canonical_url, parsed_url.external_user_id)
        return enrich_participant_activity_totals(html, participant)
    except AthleteParseError as exc:
        raise ProfileParseError(str(exc)) from exc


def fetch_profile_preview(profile_url: str) -> ProfilePreview:
    parsed = parse_athlete_url(profile_url)
    settings = get_settings()
    html = fetch_page_html(
        parsed.canonical_url,
        reason="athlete_profile_preview",
        extra_wait_ms=settings.s95_playwright_athlete_wait_ms,
    )
    try:
        participant = enrich_participant_activity_totals(
            html,
            parse_athlete_html(html, parsed.canonical_url, parsed.external_user_id),
        )
    except AthleteParseError as exc:
        raise ProfileParseError(str(exc)) from exc
    runs = parse_athlete_runs_html(
        html,
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url,
    )
    volunteering = parse_athlete_volunteering_html(
        html,
        external_user_id=participant.external_user_id,
        display_name=participant.display_name,
        profile_url=participant.profile_url or parsed.canonical_url,
    )
    return participant_to_preview(
        participant,
        parkrun_eligible=is_parkrun_eligible_barcode(participant.barcode_id),
        runs=runs,
        volunteering=volunteering,
    )


def fetch_user_profile(external_user_id: str, *, domain: str = "s95.ru") -> CanonicalParticipant:
    from app.platform_adapters.s95.url import athlete_url

    parsed = parse_athlete_url(athlete_url(external_user_id, domain=domain))
    return fetch_and_parse_profile(parsed)


def fetch_user_runs(external_user_id: str, display_name: str, profile_url: str) -> list[CanonicalRunResult]:
    html = fetch_page_html(profile_url, reason="athlete_runs")
    return parse_athlete_runs_html(
        html,
        external_user_id=external_user_id,
        display_name=display_name,
        profile_url=profile_url,
    )


def fetch_user_volunteering(external_user_id: str, display_name: str, profile_url: str) -> list[CanonicalVolunteerResult]:
    html = fetch_page_html(profile_url, reason="athlete_volunteering")
    return parse_athlete_volunteering_html(
        html,
        external_user_id=external_user_id,
        display_name=display_name,
    )


def fetch_athlete_activity(
    external_user_id: str,
    *,
    domain: str = "s95.ru",
) -> tuple[CanonicalParticipant, list[CanonicalRunResult], list[CanonicalVolunteerResult]]:
    """Single page load: profile + runs + volunteering tables."""
    from app.platform_adapters.s95.url import athlete_url

    profile_url = athlete_url(external_user_id, domain=domain)
    settings = get_settings()
    html = fetch_page_html(
        profile_url,
        reason="athlete_full",
        extra_wait_ms=settings.s95_playwright_athlete_wait_ms,
    )
    try:
        profile = enrich_participant_activity_totals(
            html,
            parse_athlete_html(html, profile_url, external_user_id),
        )
    except AthleteParseError as exc:
        raise ProfileParseError(str(exc)) from exc
    runs = parse_athlete_runs_html(
        html,
        external_user_id=external_user_id,
        display_name=profile.display_name,
        profile_url=profile_url,
    )
    volunteering = parse_athlete_volunteering_html(
        html,
        external_user_id=external_user_id,
        display_name=profile.display_name,
        profile_url=profile_url,
    )
    return profile, runs, volunteering
