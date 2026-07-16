from __future__ import annotations

import pytest

from app.platform_adapters.five_verst.age_category import normalize_five_verst_age_group


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("М40-44 (2)", "М40-44"),
        ("М35-39(1)", "М35-39"),
        ("Ж65-69  (12)", "Ж65-69"),
        ("М10 (3)", "М10"),
        # Уже чистое значение — no-op (бэкфилл идемпотентен).
        ("М40-44", "М40-44"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_five_verst_age_group(raw: object, expected: str | None) -> None:
    assert normalize_five_verst_age_group(raw) == expected
