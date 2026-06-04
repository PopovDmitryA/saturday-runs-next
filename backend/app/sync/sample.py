from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.platform_adapters.five_verst import bulk_parser


@dataclass
class SampleSyncOptions:
    location_slug: str = "babushkinskynayauze"
    summaries_limit: int = 3
    run_results_limit: int | None = 10
    volunteer_results_limit: int | None = 10


@dataclass
class SampleSyncResult:
    options: SampleSyncOptions
    location: CanonicalLocation
    event_summaries: list[CanonicalEventSummary] = field(default_factory=list)
    run_results: list[CanonicalRunResult] = field(default_factory=list)
    volunteer_results: list[CanonicalVolunteerResult] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "options": asdict(self.options),
            "location": asdict(self.location),
            "event_summaries": [asdict(item) for item in self.event_summaries],
            "run_results": [asdict(item) for item in self.run_results],
            "volunteer_results": [asdict(item) for item in self.volunteer_results],
            "meta": self.meta,
        }


def run_sample_sync(options: SampleSyncOptions | None = None) -> SampleSyncResult:
    opts = options or SampleSyncOptions()
    location, _ = bulk_parser.fetch_location(opts.location_slug)
    summaries, summaries_html = bulk_parser.fetch_event_summaries(
        opts.location_slug,
        location.name,
        limit=opts.summaries_limit,
    )
    if not summaries:
        raise ValueError(f"Не найдено саммари событий для парка {opts.location_slug}")

    latest = summaries[0]
    run_results, run_html = bulk_parser.fetch_run_protocol(
        opts.location_slug,
        latest.event_date,
        latest.event_number,
        limit=opts.run_results_limit,
    )
    volunteer_results, vol_html = bulk_parser.fetch_volunteers(
        opts.location_slug,
        latest.event_date,
        latest.event_number,
        limit=opts.volunteer_results_limit,
    )

    return SampleSyncResult(
        options=opts,
        location=location,
        event_summaries=summaries,
        run_results=run_results,
        volunteer_results=volunteer_results,
        meta={
            "parser_version": "0.3.0-sample",
            "summaries_source_hash": bulk_parser.source_hash(summaries_html),
            "run_protocol_source_hash": bulk_parser.source_hash(run_html),
            "volunteers_source_hash": bulk_parser.source_hash(vol_html),
            "volunteers_parse_mode": "organizer_team_table",
            "summaries_total_parsed": len(summaries),
            "run_results_total_parsed": len(run_results),
            "volunteer_results_total_parsed": len(volunteer_results),
        },
    )
