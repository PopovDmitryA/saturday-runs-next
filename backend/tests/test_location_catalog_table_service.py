from __future__ import annotations

from datetime import date

import pytest

from app.services.location_catalog_table_service import _build_platform_visit_index


def test_build_platform_visit_index_uses_earliest_date_per_platform() -> None:
    details = {
        "locations": [
            {
                "catalog_identity_key": "park-a",
                "platforms": [
                    {
                        "platform_code": "five_verst",
                        "run_dates": ["2026-05-23", "2026-05-16"],
                        "volunteer_dates": ["2026-05-09"],
                    }
                ],
            }
        ]
    }
    index = _build_platform_visit_index(details)
    assert index[("park-a", "five_verst")] == date(2026, 5, 9)
