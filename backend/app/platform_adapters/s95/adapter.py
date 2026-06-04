from __future__ import annotations

from datetime import date

from app.platform_adapters.base import AdapterCapabilities
from app.platform_adapters.canonical import (
    CanonicalEvent,
    CanonicalLocation,
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ExternalEventRef,
    ProfilePreview,
)
from app.platform_adapters.s95 import parser as s95_parser
from app.platform_adapters.s95.url import InvalidProfileUrlError, is_valid_profile_url, parse_athlete_url


class S95Adapter:
    platform_code = "s95"
    parser_version = "0.1.0"
    capabilities = AdapterCapabilities(
        validate_profile_url=True,
        fetch_profile_preview=True,
        fetch_user_profile=True,
        fetch_user_runs=True,
        fetch_user_volunteering=True,
        fetch_locations=False,
        fetch_events=False,
        fetch_event_results=False,
        fetch_event_volunteers=False,
    )

    def validate_profile_url(self, url: str) -> bool:
        return is_valid_profile_url(url)

    def fetch_profile_preview(self, url: str) -> ProfilePreview:
        try:
            return s95_parser.fetch_profile_preview(url)
        except InvalidProfileUrlError as exc:
            raise s95_parser.ProfileParseError(str(exc)) from exc

    def fetch_user_profile(self, external_user_id: str) -> CanonicalParticipant:
        return s95_parser.fetch_user_profile(external_user_id)

    def fetch_user_runs(self, external_user_id: str) -> list[CanonicalRunResult]:
        _profile, runs, _vol = s95_parser.fetch_athlete_activity(external_user_id)
        return runs

    def fetch_user_volunteering(self, external_user_id: str) -> list[CanonicalVolunteerResult]:
        _profile, _runs, vol = s95_parser.fetch_athlete_activity(external_user_id)
        return vol

    def fetch_locations(self) -> list[CanonicalLocation]:
        raise NotImplementedError("S95 locations are synced via global sync pipeline")

    def fetch_events(self, since: date | None = None) -> list[CanonicalEvent]:
        raise NotImplementedError

    def fetch_event_results(self, event_ref: ExternalEventRef) -> list[CanonicalRunResult]:
        raise NotImplementedError

    def fetch_event_volunteers(self, event_ref: ExternalEventRef) -> list[CanonicalVolunteerResult]:
        raise NotImplementedError
