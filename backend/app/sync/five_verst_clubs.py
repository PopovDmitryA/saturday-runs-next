"""Sync of 5verst clubs: list registry (/clubs/) and per-club detail pages with rosters."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import (
    Club,
    ClubCatalog,
    ClubCatalogLink,
    ClubMember,
    Participant,
    Platform,
    SyncRun,
    SyncRunStatus,
)
from app.platform_adapters.five_verst import clubs_parser
from app.platform_adapters.five_verst.clubs_parser import ParsedClubsListEntry
from app.platform_adapters.five_verst.http import NotFoundError, source_hash
from app.services.admin_notify import notify_admin
from app.sync import upsert
from app.sync.iteration_commit import commit_step, rollback_step

logger = logging.getLogger(__name__)

PLATFORM_CODE = "five_verst"
# Slug-change heuristics: roster overlap with an inactive club that signals "same club, new slug".
SLUG_CHANGE_OVERLAP_MIN = 5
SLUG_CHANGE_OVERLAP_SHARE = 0.5
SLUG_CHANGE_MIN_ROSTER = 3


def normalize_club_name(name: str) -> str:
    normalized = name.strip().lower().replace("ё", "е")
    normalized = normalized.lstrip("#").strip()
    return " ".join(normalized.split())


@dataclass
class ClubsRegistrySyncOptions:
    limit: int | None = None


@dataclass
class ClubsRegistrySyncResult:
    entries_total: int = 0
    clubs_created: int = 0
    clubs_updated: int = 0
    clubs_unchanged: int = 0
    clubs_deactivated: int = 0
    clubs_reactivated: int = 0
    catalog_links_created: int = 0
    marked_for_detail_sync: int = 0
    created_clubs: list[str] = field(default_factory=list)
    updated_clubs: list[str] = field(default_factory=list)
    deactivated_clubs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ClubDetailsSyncResult:
    clubs_planned: int = 0
    clubs_synced: int = 0
    clubs_not_found: int = 0
    members_seen: int = 0
    members_added: int = 0
    members_deactivated: int = 0
    participants_created: int = 0
    members_skipped: int = 0
    slug_change_alerts: int = 0
    synced_clubs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _start_sync_run(db: Session, platform: Platform, sync_type: str) -> SyncRun:
    run = SyncRun(
        platform_id=platform.id,
        sync_type=sync_type,
        status=SyncRunStatus.running,
        parser_version=upsert.PARSER_VERSION,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _finish_sync_run(
    db: Session,
    run: SyncRun,
    *,
    success: bool,
    fetched: int,
    upserted: int,
    unchanged: int,
    error: str | None = None,
) -> None:
    run.status = SyncRunStatus.success if success else SyncRunStatus.failed
    run.finished_at = datetime.now(timezone.utc)
    run.records_fetched = fetched
    run.records_upserted = upserted
    run.records_unchanged = unchanged
    run.error_message = error
    db.flush()


def _apply_list_entry(club: Club, entry: ParsedClubsListEntry, now: datetime) -> bool:
    changed = club.list_row_hash != entry.row_hash
    club.name = entry.name
    club.members_count = entry.members_count
    club.finishes_count = entry.finishes_count
    club.volunteerings_count = entry.volunteerings_count
    club.avg_time_seconds = entry.avg_time_seconds
    club.best_female_time_seconds = entry.best_female_time_seconds
    club.best_male_time_seconds = entry.best_male_time_seconds
    club.external_links = entry.external_links
    club.source_url = entry.source_url
    club.list_row_hash = entry.row_hash
    club.list_seen_at = now
    club.parser_version = upsert.PARSER_VERSION
    club.fetched_at = now
    return changed


def _ensure_catalog_link(db: Session, platform: Platform, club: Club) -> bool:
    """Attach the club to the cross-platform catalog; auto-match by normalized name.

    Existing links (incl. manual ones) are never rewired here.
    """
    link = (
        db.query(ClubCatalogLink)
        .filter(
            ClubCatalogLink.platform_id == platform.id,
            ClubCatalogLink.external_key == club.external_key,
        )
        .one_or_none()
    )
    if link is not None:
        if link.club_id is None:
            link.club_id = club.id
            db.flush()
        return False

    normalized = normalize_club_name(club.name)
    catalog = (
        db.query(ClubCatalog)
        .filter(ClubCatalog.normalized_name == normalized)
        .order_by(ClubCatalog.created_at.asc())
        .first()
    )
    if catalog is None:
        catalog = ClubCatalog(canonical_name=club.name, normalized_name=normalized)
        db.add(catalog)
        db.flush()
    db.add(
        ClubCatalogLink(
            catalog_id=catalog.id,
            platform_id=platform.id,
            external_key=club.external_key,
            club_id=club.id,
            link_source="auto_name",
        )
    )
    db.flush()
    return True


def sync_clubs_registry(
    db: Session,
    options: ClubsRegistrySyncOptions | None = None,
) -> ClubsRegistrySyncResult:
    options = options or ClubsRegistrySyncOptions()
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = ClubsRegistrySyncResult()
    sync_run = _start_sync_run(db, platform, "clubs_registry")
    db.commit()

    try:
        entries, _ = clubs_parser.fetch_clubs_page()
        if options.limit is not None:
            entries = entries[: options.limit]
        result.entries_total = len(entries)
        now = datetime.now(timezone.utc)
        seen_keys: set[str] = set()

        for entry in entries:
            seen_keys.add(entry.slug)
            try:
                club = (
                    db.query(Club)
                    .filter(Club.platform_id == platform.id, Club.external_key == entry.slug)
                    .one_or_none()
                )
                if club is None:
                    club = Club(
                        platform_id=platform.id,
                        external_key=entry.slug,
                        name=entry.name,
                        needs_detail_sync=True,
                    )
                    _apply_list_entry(club, entry, now)
                    db.add(club)
                    db.flush()
                    result.clubs_created += 1
                    result.marked_for_detail_sync += 1
                    result.created_clubs.append(entry.slug)
                else:
                    changed = _apply_list_entry(club, entry, now)
                    if not club.is_active:
                        club.is_active = True
                        result.clubs_reactivated += 1
                        changed = True
                    if changed:
                        if not club.needs_detail_sync:
                            club.needs_detail_sync = True
                            result.marked_for_detail_sync += 1
                        result.clubs_updated += 1
                        result.updated_clubs.append(entry.slug)
                    else:
                        result.clubs_unchanged += 1
                if _ensure_catalog_link(db, platform, club):
                    result.catalog_links_created += 1
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{entry.slug}: {exc}")

        # Only deactivate on a full (non-limited) parse: a partial run must not hide the tail.
        if options.limit is None and entries:
            missing = (
                db.query(Club)
                .filter(
                    Club.platform_id == platform.id,
                    Club.is_active.is_(True),
                    Club.external_key.notin_(seen_keys),
                )
                .all()
            )
            for club in missing:
                club.is_active = False
                result.clubs_deactivated += 1
                result.deactivated_clubs.append(club.external_key)
            if missing:
                db.flush()

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.entries_total,
            upserted=result.clubs_created + result.clubs_updated,
            unchanged=result.clubs_unchanged,
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform, "clubs_registry")
        _finish_sync_run(db, failed_run, success=False, fetched=0, upserted=0, unchanged=0, error=str(exc))
        db.commit()
        raise


def _active_member_ids(db: Session, club_id: UUID) -> set[UUID]:
    rows = (
        db.query(ClubMember.participant_id)
        .filter(ClubMember.club_id == club_id, ClubMember.is_active.is_(True))
        .all()
    )
    return {row.participant_id for row in rows}


def _detect_slug_change(db: Session, platform: Platform, club: Club, member_ids: set[UUID]) -> list[tuple[Club, int]]:
    if len(member_ids) < SLUG_CHANGE_MIN_ROSTER:
        return []
    inactive_clubs = (
        db.query(Club)
        .filter(
            Club.platform_id == platform.id,
            Club.is_active.is_(False),
            Club.id != club.id,
        )
        .all()
    )
    matches: list[tuple[Club, int]] = []
    for candidate in inactive_clubs:
        overlap = len(member_ids & _active_member_ids(db, candidate.id))
        if overlap >= SLUG_CHANGE_OVERLAP_MIN or overlap >= SLUG_CHANGE_OVERLAP_SHARE * len(member_ids):
            matches.append((candidate, overlap))
    matches.sort(key=lambda item: item[1], reverse=True)
    return matches


def _notify_slug_change(club: Club, matched: Club, overlap: int, roster_size: int) -> bool:
    text = (
        "⚠️ Возможная смена slug клуба 5 вёрст\n\n"
        f"Новый клуб: {club.external_key} ({club.name})\n"
        f"URL: {club.source_url or '—'}\n\n"
        f"Похоже на неактивный: {matched.external_key} ({matched.name})\n"
        f"Пересечение состава: {overlap} из {roster_size}\n\n"
        f"Если это тот же клуб — объединить вручную:\n"
        f"scripts/link_club_catalog.py --rename-slug {matched.external_key} {club.external_key}"
    )
    return notify_admin(text)


def _sync_club_membership(
    db: Session,
    platform: Platform,
    club: Club,
    detail: clubs_parser.ParsedClubDetail,
    result: ClubDetailsSyncResult,
    now: datetime,
) -> set[UUID]:
    existing_rows = db.query(ClubMember).filter(ClubMember.club_id == club.id).all()
    by_participant = {row.participant_id: row for row in existing_rows}
    current_ids: set[UUID] = set()

    for member in detail.members:
        existed = (
            db.query(Participant.id)
            .filter(
                Participant.platform_id == platform.id,
                Participant.external_user_id == member.userstats_id,
            )
            .first()
            is not None
        )
        participant = upsert.upsert_participant(
            db,
            platform,
            external_user_id=member.userstats_id,
            display_name=member.display_name,
            profile_url=member.profile_url,
            club_name=club.name,
        )
        db.flush()
        if not existed:
            result.participants_created += 1
        current_ids.add(participant.id)
        row = by_participant.get(participant.id)
        if row is None:
            db.add(
                ClubMember(
                    club_id=club.id,
                    participant_id=participant.id,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_active=True,
                )
            )
            result.members_added += 1
        else:
            row.last_seen_at = now
            if not row.is_active:
                row.is_active = True

    for participant_id, row in by_participant.items():
        if participant_id not in current_ids and row.is_active:
            row.is_active = False
            result.members_deactivated += 1

    db.flush()
    result.members_seen += len(current_ids)
    result.members_skipped += detail.members_skipped
    return current_ids


def sync_club_details_batch(db: Session, limit: int = 20) -> ClubDetailsSyncResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = ClubDetailsSyncResult()
    sync_run = _start_sync_run(db, platform, "clubs_details")
    db.commit()

    clubs = (
        db.query(Club)
        .filter(Club.platform_id == platform.id, Club.is_active.is_(True))
        .order_by(
            Club.needs_detail_sync.desc(),
            Club.detail_synced_at.asc().nullsfirst(),
            Club.external_key.asc(),
        )
        .limit(limit)
        .all()
    )
    result.clubs_planned = len(clubs)

    try:
        for club in clubs:
            try:
                first_detail_sync = club.detail_synced_at is None
                now = datetime.now(timezone.utc)
                try:
                    detail, html = clubs_parser.fetch_club_detail(club.external_key)
                except NotFoundError:
                    club.is_active = False
                    club.needs_detail_sync = False
                    result.clubs_not_found += 1
                    commit_step(db)
                    continue

                club.description = detail.description
                club.logo_url = detail.logo_url
                if detail.external_links:
                    club.external_links = detail.external_links
                if detail.title and detail.title != club.name:
                    club.stats_extra = {**club.stats_extra, "detail_title": detail.title}
                club.detail_source_hash = source_hash(html)

                member_ids = _sync_club_membership(db, platform, club, detail, result, now)

                if first_detail_sync:
                    matches = _detect_slug_change(db, platform, club, member_ids)
                    if matches:
                        matched, overlap = matches[0]
                        if _notify_slug_change(club, matched, overlap, len(member_ids)):
                            result.slug_change_alerts += 1

                club.detail_synced_at = now
                club.needs_detail_sync = False
                club.fetched_at = now
                club.parser_version = upsert.PARSER_VERSION
                result.clubs_synced += 1
                result.synced_clubs.append(club.external_key)
                commit_step(db)
            except Exception as exc:
                rollback_step(db)
                result.errors.append(f"{club.external_key}: {exc}")

        _finish_sync_run(
            db,
            sync_run,
            success=not result.errors,
            fetched=result.clubs_planned,
            upserted=result.clubs_synced,
            unchanged=result.clubs_planned - result.clubs_synced - result.clubs_not_found - len(result.errors),
            error="; ".join(result.errors) or None,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform, "clubs_details")
        _finish_sync_run(db, failed_run, success=False, fetched=0, upserted=0, unchanged=0, error=str(exc))
        db.commit()
        raise
