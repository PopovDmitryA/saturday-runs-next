from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from app.platform_adapters.canonical import (
    CanonicalEvent,
    CanonicalLocation,
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ExternalEventRef,
    ProfilePreview,
)


@dataclass(frozen=True)
class AdapterCapabilities:
    validate_profile_url: bool = False
    fetch_profile_preview: bool = False
    fetch_user_profile: bool = False
    fetch_user_runs: bool = False
    fetch_user_volunteering: bool = False
    fetch_locations: bool = False
    fetch_events: bool = False
    fetch_event_results: bool = False
    fetch_event_volunteers: bool = False


@runtime_checkable
class PlatformAdapter(Protocol):
    platform_code: str
    parser_version: str
    capabilities: AdapterCapabilities

    def validate_profile_url(self, url: str) -> bool: ...

    def fetch_profile_preview(self, url: str) -> ProfilePreview: ...

    def fetch_user_profile(self, external_user_id: str) -> CanonicalParticipant: ...

    def fetch_user_runs(self, external_user_id: str) -> list[CanonicalRunResult]: ...

    def fetch_user_volunteering(self, external_user_id: str) -> list[CanonicalVolunteerResult]: ...

    def fetch_locations(self) -> list[CanonicalLocation]: ...

    def fetch_events(self, since: date | None = None) -> list[CanonicalEvent]: ...

    def fetch_event_results(self, event_ref: ExternalEventRef) -> list[CanonicalRunResult]: ...

    def fetch_event_volunteers(self, event_ref: ExternalEventRef) -> list[CanonicalVolunteerResult]: ...
