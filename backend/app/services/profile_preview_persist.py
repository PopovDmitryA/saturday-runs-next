from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLinkSyncStatus
from app.platform_adapters.canonical import (
    CanonicalParticipant,
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreview,
)
from app.platform_adapters.registry import ensure_adapters_registered, get_adapter
from app.services.participant_profile_service import (
    _count_participant_runs,
    build_profile_preview_from_db,
    find_participant_by_identity,
    resolve_profile_identity,
)
from app.sync import upsert as sync_upsert


def _preview_to_canonical(preview: ProfilePreview) -> CanonicalParticipant:
    return CanonicalParticipant(
        external_user_id=preview.external_user_id,
        display_name=preview.display_name,
        profile_url=preview.profile_url,
        total_runs=preview.total_runs,
        total_volunteering=preview.total_volunteering,
        club_name=preview.club_name,
        barcode_id=preview.barcode_id,
        planning_location=preview.planning_location,
        source_url=preview.profile_url,
    )


def persist_preview_activity(
    db: Session,
    platform: Platform,
    preview: ProfilePreview,
    *,
    runs: list[CanonicalRunResult] | None,
    volunteering: list[CanonicalVolunteerResult] | None,
    parser_version: str,
    parkrun_profile_extra: dict | None = None,
) -> Participant:
    """Store participant + profile table rows from a single live fetch (preview step)."""
    from app.services.profile_linking_service import _upsert_participant

    participant = _upsert_participant(db, platform, preview, parser_version)

    if platform.code == "s95":
        from app.sync.s95_participant_sync import apply_s95_participant_profile

        apply_s95_participant_profile(
            participant,
            _preview_to_canonical(preview),
            club_name=preview.club_name,
            barcode_id=preview.barcode_id,
            parser_version=parser_version,
        )
    elif platform.code == "parkrun" and parkrun_profile_extra is not None:
        from app.parkrun.parsers.athlete import volunteer_summary_to_canonical
        from app.sync.parkrun_participant_import import apply_parkrun_participant

        apply_parkrun_participant(
            db,
            platform,
            _preview_to_canonical(preview),
            profile_extra=parkrun_profile_extra,
        )
        participant = (
            db.query(Participant)
            .filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id == preview.external_user_id,
            )
            .one()
        )
        volunteering = volunteer_summary_to_canonical(
            parkrun_profile_extra.get("volunteer_summary") or [],
            athlete_id=preview.external_user_id,
            display_name=preview.display_name,
        )

    if runs:
        sync_upsert.import_profile_run_results(db, platform, runs)
    if volunteering:
        sync_upsert.import_profile_volunteer_results(db, platform, participant.id, volunteering)

    db.commit()
    return participant


def refresh_preview_from_db(
    db: Session,
    platform_code: str,
    profile_url: str,
    *,
    fallback: ProfilePreview,
) -> ProfilePreview:
    identity = resolve_profile_identity(platform_code, profile_url)
    platform = db.query(Platform).filter(Platform.code == platform_code).one()
    participant = find_participant_by_identity(db, platform, identity.external_user_id)
    if participant is None:
        return fallback
    db_preview = build_profile_preview_from_db(db, platform, participant, identity=identity)
    return db_preview or fallback


def linking_sync_should_run(
    db: Session,
    platform: Platform,
    participant: Participant,
    preview: ProfilePreview,
) -> bool:
    """Skip Celery user sync only when a live preview already persisted full activity."""
    runs_in_db = _count_participant_runs(db, participant.id, platform.id)

    if platform.code in ("s95", "five_verst"):
        if preview.data_source == "database":
            return True
        return runs_in_db == 0

    if runs_in_db > 0:
        return False

    # Prod server can't reach parkrun.org.uk (WAF/captcha), so only enqueue a
    # Celery sync when there are actually runs to import. Zero-run profiles are
    # fully represented by the already-persisted preview — no sync needed.
    expected = preview.total_runs
    if platform.code == "parkrun":
        return expected is not None and expected > 0
    return False


def complete_link_without_sync(db: Session, link) -> None:
    from app.services.dashboard_service import recompute_dashboard_cache
    from app.services.personal_record_service import (
        recalculate_participants_cross_platform_personal_records,
        recalculate_participants_personal_records,
    )

    link.sync_status = PlatformLinkSyncStatus.ok
    link.error_message = None
    link.last_user_sync_at = datetime.now(timezone.utc)
    db.flush()
    # Celery-синк пропускается, а привязка меняет состав платформ пользователя —
    # без пересчёта is_pr и кросс-платформенных рекордов флаги останутся от
    # старого состава (или от batch-истории без привязки).
    platform = db.get(Platform, link.platform_id)
    if platform is not None and link.participant_id is not None:
        recalculate_participants_personal_records(db, platform.code, {link.participant_id})
        recalculate_participants_cross_platform_personal_records(db, {link.participant_id})
    recompute_dashboard_cache(db, link.user_id)
    db.commit()


def fetch_live_preview_bundle(
    db: Session,
    platform_code: str,
    profile_url: str,
) -> tuple[ProfilePreview, list[CanonicalRunResult] | None, list[CanonicalVolunteerResult] | None, dict | None]:
    from app.db.session import release_db_connection_for_network_io

    # Дальше только сеть (rate-limit ожидание + фетч, до десятков секунд) —
    # держать слот пула БД всё это время нельзя. К этому моменту в транзакции
    # только чтения (preview_profile_link) либо всё закоммичено (pending queue).
    release_db_connection_for_network_io(db)

    ensure_adapters_registered()
    if platform_code == "s95":
        from app.platform_adapters.s95.parser import fetch_profile_activity

        bundle = fetch_profile_activity(profile_url)
        return bundle.preview, bundle.runs, bundle.volunteering, None
    if platform_code == "five_verst":
        from app.platform_adapters.five_verst.parser import (
            fetch_userstats_html,
            parse_userstats_html,
            parse_userstats_runs_html,
            parse_userstats_volunteering_html,
            participant_to_preview,
        )
        from app.platform_adapters.five_verst.url import parse_profile_input

        parsed = parse_profile_input(profile_url)
        html = fetch_userstats_html(parsed.canonical_url)
        participant = parse_userstats_html(html, parsed)
        runs = parse_userstats_runs_html(
            html,
            participant.external_user_id,
            participant.display_name,
        )
        volunteering = parse_userstats_volunteering_html(
            html,
            participant.external_user_id,
            participant.display_name,
        )
        preview = participant_to_preview(participant, runs=runs, volunteering=volunteering)
        preview.platform_code = "five_verst"
        return preview, runs, volunteering, None
    if platform_code == "parkrun":
        from app.parkrun.parsers.athlete import fetch_profile_preview_activity
        from app.platform_adapters.parkrun.url import parse_profile_input

        parsed = parse_profile_input(profile_url)
        bundle = fetch_profile_preview_activity(parsed)
        return bundle.preview, bundle.runs, None, bundle.profile_extra

    adapter = get_adapter(platform_code)
    preview = adapter.fetch_profile_preview(profile_url)
    return preview, None, None, None


def persist_live_profile_preview(
    db: Session,
    platform_code: str,
    profile_url: str,
    user_id: UUID,
) -> ProfilePreview:
    platform = db.query(Platform).filter(Platform.code == platform_code).one()
    adapter = get_adapter(platform_code)
    preview, runs, volunteering, parkrun_extra = fetch_live_preview_bundle(db, platform_code, profile_url)
    persist_preview_activity(
        db,
        platform,
        preview,
        runs=runs,
        volunteering=volunteering,
        parser_version=adapter.parser_version,
        parkrun_profile_extra=parkrun_extra,
    )
    return refresh_preview_from_db(db, platform_code, profile_url, fallback=preview)
