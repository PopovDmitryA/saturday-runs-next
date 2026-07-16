from __future__ import annotations

import re

# Возрастная группа 5 вёрст: М30-34, Ж65-69, М10.
# Протокол печатает рядом место в группе на этом забеге («М40-44 (2)») — оно к категории не относится.
FIVE_VERST_PLACE_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def normalize_five_verst_age_group(value: object) -> str | None:
    """Категория 5 вёрст без места в возрастной группе, или None для пустого значения."""
    if value is None:
        return None
    cleaned = FIVE_VERST_PLACE_SUFFIX_RE.sub("", str(value).strip()).strip()
    return cleaned or None
