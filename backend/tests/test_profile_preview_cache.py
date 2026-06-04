from datetime import date
from uuid import uuid4

import fakeredis

from app.platform_adapters.canonical import ProfilePreview, ProfilePreviewActivity
from app.services.profile_preview_cache import (
    get_cached_profile_preview,
    store_profile_preview,
)


def test_profile_preview_cache_roundtrip_with_activity_dates() -> None:
    user_id = uuid4()
    preview = ProfilePreview(
        platform_code="five_verst",
        external_user_id="790103773",
        display_name="Test Runner",
        profile_url="https://5verst.ru/userstats/790103773/",
        total_runs=42,
        total_volunteering=3,
        recent_activities=[
            ProfilePreviewActivity(
                kind="run",
                event_date=date(2026, 5, 17),
                location_name="Троицк",
                finish_time_display="00:23:41",
            )
        ],
    )

    with fakeredis.FakeRedis(decode_responses=True) as redis:
        from unittest.mock import patch

        with patch("app.services.profile_preview_cache.get_redis_client", return_value=redis):
            store_profile_preview(user_id, "five_verst", preview.profile_url, preview)
            cached = get_cached_profile_preview(user_id, "five_verst", preview.profile_url)

    assert cached is not None
    assert cached.external_user_id == "790103773"
    assert len(cached.recent_activities) == 1
    assert cached.recent_activities[0].event_date == date(2026, 5, 17)
    assert cached.recent_activities[0].location_name == "Троицк"
