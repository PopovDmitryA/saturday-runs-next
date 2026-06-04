from __future__ import annotations

import re

from bs4 import Tag

PR_TITLE_RE = re.compile(r"личн\w*\s*рекорд|personal\s*record|lični\s*rekord", re.I)


def row_is_pr(row: Tag) -> bool:
    """S95 marks PR with fa-bolt icon (title «Личный рекорд!») or legacy beaker image."""
    for icon in row.find_all(["i", "img", "span", "svg"]):
        title = (icon.get("title") or icon.get("alt") or "").strip()
        if title and PR_TITLE_RE.search(title):
            return True
        classes = " ".join(icon.get("class") or []).lower()
        if "fa-bolt" in classes and title and PR_TITLE_RE.search(title):
            return True
        src = (icon.get("src") or "").lower()
        if "beaker" in src:
            return True
    row_text = row.get_text(" ", strip=True).lower()
    return "личный рекорд" in row_text or "personal record" in row_text
