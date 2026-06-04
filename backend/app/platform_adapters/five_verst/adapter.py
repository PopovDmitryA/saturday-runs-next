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
from app.platform_adapters.five_verst import parser as fv_parser
from app.platform_adapters.five_verst.url import (
    is_valid_profile_url,
    parse_profile_input,
    userstats_url,
)


class BulkFetchNotImplementedError(NotImplementedError):
    pass


class FiveVerstAdapter:
    platform_code = "five_verst"
    parser_version = "0.1.0"
    capabilities = AdapterCapabilities(
        validate_profile_url=True,
        fetch_profile_preview=True,
        fetch_user_profile=True,
        fetch_user_runs=False,
        fetch_user_volunteering=False,
        fetch_locations=False,
        fetch_events=False,
        fetch_event_results=False,
        fetch_event_volunteers=False,
    )

    def validate_profile_url(self, url: str) -> bool:
        return is_valid_profile_url(url)

    def fetch_profile_preview(self, url: str) -> ProfilePreview:
        parsed = parse_profile_input(url)
        html = fv_parser.fetch_userstats_html(parsed.canonical_url)
        participant = fv_parser.parse_userstats_html(html, parsed)
        runs = fv_parser.parse_userstats_runs_html(
            html,
            participant.external_user_id,
            participant.display_name,
        )
        volunteering = fv_parser.parse_userstats_volunteering_html(
            html,
            participant.external_user_id,
            participant.display_name,
        )
        return fv_parser.participant_to_preview(
            participant,
            runs=runs,
            volunteering=volunteering,
        )

    def fetch_user_profile(self, external_user_id: str) -> CanonicalParticipant:
        parsed = parse_profile_input(userstats_url(external_user_id))
        return fv_parser.fetch_and_parse_profile(parsed)

    def fetch_user_runs(self, external_user_id: str) -> list[CanonicalRunResult]:
        raise BulkFetchNotImplementedError("fetch_user_runs will be implemented in Phase 3")

    def fetch_user_volunteering(self, external_user_id: str) -> list[CanonicalVolunteerResult]:
        raise BulkFetchNotImplementedError("fetch_user_volunteering will be implemented in Phase 3")

    def fetch_locations(self) -> list[CanonicalLocation]:
        raise BulkFetchNotImplementedError("fetch_locations will be implemented in Phase 3")

    def fetch_events(self, since: date | None = None) -> list[CanonicalEvent]:
        raise BulkFetchNotImplementedError("fetch_events will be implemented in Phase 3")

    def fetch_event_results(self, event_ref: ExternalEventRef) -> list[CanonicalRunResult]:
        raise BulkFetchNotImplementedError("fetch_event_results will be implemented in Phase 3")

    def fetch_event_volunteers(self, event_ref: ExternalEventRef) -> list[CanonicalVolunteerResult]:
        raise BulkFetchNotImplementedError("fetch_event_volunteers will be implemented in Phase 3")
