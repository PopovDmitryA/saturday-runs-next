from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models import Platform
from app.platform_adapters.canonical import ProfilePreview
from app.services.profile_preview_persist import linking_sync_should_run


def test_linking_sync_skipped_when_runs_already_imported(db_session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if platform is None:
        return
    participant = MagicMock()
    participant.id = uuid4()
    preview = ProfilePreview(
        external_user_id="123",
        display_name="Runner",
        profile_url="https://s95.ru/athletes/123/",
        total_runs=50,
        platform_code="s95",
    )
    with patch(
        "app.services.profile_preview_persist._count_participant_runs",
        return_value=12,
    ):
        assert linking_sync_should_run(db_session, platform, participant, preview) is False


def test_linking_sync_runs_for_parkrun_when_only_summary_counts(db_session) -> None:
    platform = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if platform is None:
        return
    participant = MagicMock()
    participant.id = uuid4()
    preview = ProfilePreview(
        external_user_id="7035519",
        display_name="Runner",
        profile_url="https://www.parkrun.org.uk/parkrunner/7035519/",
        total_runs=40,
        platform_code="parkrun",
    )
    with patch(
        "app.services.profile_preview_persist._count_participant_runs",
        return_value=0,
    ):
        assert linking_sync_should_run(db_session, platform, participant, preview) is True
