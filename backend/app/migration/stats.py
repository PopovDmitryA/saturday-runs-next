from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MigrationStats:
    locations: int = 0
    event_summaries: int = 0
    events: int = 0
    participants: int = 0
    runs: int = 0
    volunteers: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str, *, limit: int = 100) -> None:
        if len(self.errors) < limit:
            self.errors.append(message)

    def merge(self, other: MigrationStats) -> None:
        self.locations += other.locations
        self.event_summaries += other.event_summaries
        self.events += other.events
        self.participants += other.participants
        self.runs += other.runs
        self.volunteers += other.volunteers
        self.skipped += other.skipped
        for err in other.errors:
            self.add_error(err)

    def as_dict(self) -> dict[str, object]:
        return {
            "locations": self.locations,
            "event_summaries": self.event_summaries,
            "events": self.events,
            "participants": self.participants,
            "runs": self.runs,
            "volunteers": self.volunteers,
            "skipped": self.skipped,
            "errors": list(self.errors),
        }
