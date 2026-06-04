from __future__ import annotations

import re
from dataclasses import dataclass

from app.s95.ban import BAN_MARKERS, is_ban_or_protection_html

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParkrunHtmlInspection:
    url: str
    html_bytes: int
    title: str | None
    matched_markers: tuple[str, ...]
    looks_like_parkrun: bool
    is_protection: bool
    is_too_short: bool

    @property
    def summary(self) -> str:
        parts = [
            f"bytes={self.html_bytes}",
            f"title={self.title!r}" if self.title else "title=<missing>",
            f"parkrun_page={self.looks_like_parkrun}",
        ]
        if self.matched_markers:
            parts.append(f"markers={','.join(self.matched_markers)}")
        if self.is_too_short:
            parts.append("too_short=true")
        return " ".join(parts)


def _extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    return " ".join(match.group(1).split())[:200]


def inspect_html_response(html: str, *, url: str) -> ParkrunHtmlInspection:
    text_lower = (html or "").lower()
    matched = tuple(marker for marker in BAN_MARKERS if marker in text_lower)
    html_bytes = len(html or "")
    looks_like_parkrun = "parkrunner" in text_lower or "parkrun.org.uk" in text_lower
    is_too_short = html_bytes < 500
    is_protection = is_ban_or_protection_html(html) or is_too_short
    return ParkrunHtmlInspection(
        url=url,
        html_bytes=html_bytes,
        title=_extract_title(html),
        matched_markers=matched,
        looks_like_parkrun=looks_like_parkrun,
        is_protection=is_protection,
        is_too_short=is_too_short,
    )
