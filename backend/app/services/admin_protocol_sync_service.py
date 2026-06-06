from __future__ import annotations

import hashlib
import re
from datetime import date

from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.http import fetch_html
from app.sync import upsert
from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
from app.sync.s95_protocol import fetch_and_upsert_activity_protocol
from app.sync.s95_protocol_lookup import ACTIVITY_URL_RE, _summary_to_canonical

FIVE_VERST_PROTOCOL_URL_RE = re.compile(
    r"5verst\.ru/(?P<slug>[a-z0-9-]+)/results/(?P<date>\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)


def _parse_five_verst_date(raw: str) -> date:
    day, month, year = raw.split(".")
    return date(int(year), int(month), int(day))


def _ensure_five_verst_summary(
    db: Session,
    platform: Platform,
    location: Location,
    *,
    slug: str,
    event_date: date,
    source_url: str,
    html: str,
) -> EventSummary:
    external_event_key = f"{slug}:{event_date.isoformat()}"
    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.external_event_key == external_event_key,
        )
        .one_or_none()
    )
    if summary_row is not None:
        return summary_row

    event_number = None
    title_match = re.search(r"#(\d+)", html[:5000])
    if title_match:
        event_number = int(title_match.group(1))

    summary = CanonicalEventSummary(
        external_event_key=external_event_key,
        event_date=event_date,
        event_number=event_number,
        location_external_key=slug,
        location_name=location.name,
        finishers_count=None,
        source_url=source_url,
        summary_hash=hashlib.sha256(source_url.encode()).hexdigest()[:32],
    )
    row, _ = upsert.upsert_event_summary(db, platform, location, summary)
    db.flush()
    return row


def _sync_five_verst_protocol_url(db: Session, url: str) -> dict[str, object]:
    match = FIVE_VERST_PROTOCOL_URL_RE.search(url)
    if match is None:
        raise ValueError("Ожидается ссылка вида https://5verst.ru/<slug>/results/DD.MM.YYYY/")

    slug = match.group("slug")
    event_date = _parse_five_verst_date(match.group("date"))
    source_url = bulk_parser._results_date_url(slug, event_date)
    platform = upsert.get_platform(db, "five_verst")

    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == slug,
        )
        .one_or_none()
    )
    if location is None:
        location_data = CanonicalLocation(
            external_key=slug,
            name=slug,
            source_url=f"https://5verst.ru/{slug}/",
        )
        location, _ = upsert.upsert_location(db, platform, location_data)

    html = fetch_html(source_url)
    summary_row = _ensure_five_verst_summary(
        db,
        platform,
        location,
        slug=slug,
        event_date=event_date,
        source_url=source_url,
        html=html,
    )
    summary = CanonicalEventSummary(
        external_event_key=summary_row.external_event_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_external_key=location.external_key,
        location_name=location.name,
        finishers_count=summary_row.finishers_count,
        source_url=summary_row.source_url or source_url,
        summary_hash=summary_row.summary_hash,
    )
    result = fetch_and_upsert_event_protocol(db, platform, location, summary, summary_row)
    db.commit()
    return {
        "platform": "five_verst",
        "location_slug": slug,
        "event_date": event_date.isoformat(),
        "run_results_upserted": result.run_results_upserted,
        "volunteer_results_upserted": result.volunteer_results_upserted,
        "protocol_changed": result.protocol_changed,
    }


def _sync_s95_protocol_url(db: Session, url: str) -> dict[str, object]:
    match = ACTIVITY_URL_RE.search(url)
    if match is None:
        raise ValueError("Ожидается ссылка вида https://s95.ru/activities/<id>")

    activity_id = match.group(1)
    platform = upsert.get_platform(db, "s95")
    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.source_url.ilike(f"%/activities/{activity_id}%"),
        )
        .one_or_none()
    )
    if summary_row is None:
        summary_row = (
            db.query(EventSummary)
            .filter(
                EventSummary.platform_id == platform.id,
                EventSummary.source_url.ilike(f"%/activities/{activity_id}"),
            )
            .one_or_none()
        )
    if summary_row is None:
        raise ValueError(
            f"Активность {activity_id} не найдена в БД. "
            "Сначала синхронизируйте локацию или latest/reconcile."
        )

    location = db.query(Location).filter(Location.id == summary_row.location_id).one()
    protocol_url = summary_row.source_url or url
    summary = _summary_to_canonical(summary_row, location)
    summary.source_url = protocol_url

    result = fetch_and_upsert_activity_protocol(
        db,
        platform,
        location,
        summary,
        summary_row,
        protocol_url=protocol_url,
    )
    db.commit()
    return {
        "platform": "s95",
        "activity_id": activity_id,
        "location_slug": location.external_key,
        "event_date": summary_row.event_date.isoformat(),
        "run_results_upserted": result.run_results_upserted,
        "volunteer_results_upserted": result.volunteer_results_upserted,
        "protocol_changed": result.protocol_changed,
    }


def sync_protocol_from_url(db: Session, url: str) -> dict[str, object]:
    normalized = url.strip()
    if not normalized.startswith("http"):
        normalized = f"https://{normalized.lstrip('/')}"

    lowered = normalized.lower()
    if "5verst.ru" in lowered:
        return _sync_five_verst_protocol_url(db, normalized)
    if "s95.ru" in lowered:
        return _sync_s95_protocol_url(db, normalized)
    raise ValueError("Поддерживаются только ссылки 5verst.ru и s95.ru")
