from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthProfile:
    external_id: str
    display_name: str | None
    email: str | None
    profile_json: dict[str, object]
