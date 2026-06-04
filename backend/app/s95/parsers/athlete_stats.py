from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ATHLETE_ID_RE = re.compile(r"/athletes/(\d+)", re.IGNORECASE)


def parse_athlete_links_html(html: str, base_url: str, *, exclude_user_id: str | None = None) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    friends: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=ATHLETE_ID_RE):
        href = link.get("href", "")
        if "/statistics/" in href or "lang=" in href:
            continue
        match = ATHLETE_ID_RE.search(href)
        if not match:
            continue
        user_id = match.group(1)
        if exclude_user_id and user_id == exclude_user_id:
            continue
        if user_id in seen:
            continue
        seen.add(user_id)
        friends.append(
            {
                "external_user_id": user_id,
                "display_name": link.get_text(strip=True),
                "profile_url": urljoin(base_url, href),
            }
        )
    return friends


def parse_trophies_html(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    awards: list[dict[str, str]] = []
    for item in soup.find_all(["li", "div", "span", "p", "a"]):
        text = item.get_text(" ", strip=True)
        if not text or len(text) > 200:
            continue
        if text.lower() in {"загрузка...", "loading..."}:
            continue
        if re.search(r"^\d+\s+всего", text, re.I):
            continue
        if text not in {a["title"] for a in awards}:
            awards.append({"title": text})
    return awards[:100]
