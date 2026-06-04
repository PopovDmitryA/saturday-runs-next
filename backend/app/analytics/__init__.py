"""Post-MVP analytics layer placeholder."""

from typing import Any, Protocol


class DashboardAggregator(Protocol):
    def compute(self, user_id: str) -> dict[str, Any]: ...
