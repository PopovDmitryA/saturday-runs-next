"""«Обновить по ссылке» в админке: приоритетная перечитка профиля, протокола
или списка стартов локации у 5 вёрст и S95.

Заявка идёт через ОБЩУЮ очередь платформы, но её приоритетную ветку —
`five_verst_user` (батчи 5 вёрст встают на паузу между фетчами) и `s95_user`
(батч S95 уступает исключением). Мимо координатора фетчей ничего не ходит:
общий Redis-лок и пауза между запросами действуют как обычно.

Что делает каждая ветка:

* **профиль** — читает профиль, сверяет каждую строку с базой (нет строки,
  не совпало время, протокол ни разу не качался целиком) и перекачивает
  ЦЕЛИКОМ те протоколы, где человек есть у источника, а у нас — нет или не так;
* **протокол** — перекачивает его целиком и сравнивает слепки до/после;
* **список стартов локации** — сверяет сводку `/results/all/` (у S95 —
  `updated_at` из `/events/{slug}.json`) и перекачивает только расходящиеся.

Ход работы пишется в `AdminResyncRequest.steps` шаг за шагом (страница
админки опрашивает их), итог — в `result`: что добавилось, поправилось,
удалилось; если ничего — так и пишем.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.five_verst.errors import FiveVerstBanDetected
from app.models import (
    AdminResyncRequest,
    Event,
    EventSummary,
    Location,
    Participant,
    Platform,
    ProtocolSyncState,
    RunResult,
    VolunteerResult,
)
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.platform_adapters.five_verst import bulk_parser
from app.platform_adapters.five_verst.url import USERSTATS_URL_RE
from app.platform_adapters.s95.url import S95_DOMAINS
from app.s95.errors import S95BanDetected
from app.sync import upsert

logger = logging.getLogger(__name__)

PLATFORM_FIVE_VERST = "five_verst"
PLATFORM_S95 = "s95"

KIND_PROFILE = "profile"
KIND_PROTOCOL = "protocol"
KIND_LOCATION = "location"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

# Очереди, куда уходят заявки. Это приоритетные ветки общих очередей платформ:
# у них свой воркер (five_verst_user) либо батч уступает им сам (s95_user).
QUEUE_BY_PLATFORM = {
    PLATFORM_FIVE_VERST: "five_verst_user",
    PLATFORM_S95: "s95_user",
}

PLATFORM_LABELS = {PLATFORM_FIVE_VERST: "5 вёрст", PLATFORM_S95: "S95"}
KIND_LABELS = {KIND_PROFILE: "профиль", KIND_PROTOCOL: "протокол", KIND_LOCATION: "список стартов"}

# Сколько строк диффа хранить в итоге: имена нужны, но не тысячами.
_DIFF_SAMPLE_LIMIT = 60

FIVE_VERST_PROTOCOL_RE = re.compile(
    r"^https?://(?:www\.)?5verst\.ru/(?P<slug>[a-z0-9_-]+)/results/(?P<date>\d{2}\.\d{2}\.\d{4})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
# Страница локации, её список стартов или трасса — всё это «локация».
FIVE_VERST_LOCATION_RE = re.compile(
    r"^https?://(?:www\.)?5verst\.ru/(?P<slug>[a-z0-9_-]+)/?"
    r"(?:results/?(?:all/?)?|course/?)?(?:[?#].*)?$",
    re.IGNORECASE,
)
S95_ATHLETE_RE = re.compile(r"^/athletes/(?P<id>\d+)/?$", re.IGNORECASE)
S95_ACTIVITY_RE = re.compile(r"^/activities/(?P<id>\d+)(?:\.json)?/?$", re.IGNORECASE)
S95_EVENT_RE = re.compile(r"^/events/(?P<slug>[^/.?#]+)(?:\.json)?/?$", re.IGNORECASE)


class ResyncInputError(ValueError):
    """Ссылка не распознана — сообщение пригодно для показа админу."""


@dataclass(frozen=True)
class ResyncTarget:
    platform_code: str
    kind: str
    canonical_url: str
    slug: str | None = None
    event_date: date | None = None
    external_user_id: str | None = None
    activity_id: str | None = None
    domain: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_url": self.canonical_url,
            "slug": self.slug,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "external_user_id": self.external_user_id,
            "activity_id": self.activity_id,
            "domain": self.domain,
        }


def _parse_ru_date(raw: str) -> date:
    day, month, year = raw.split(".")
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise ResyncInputError(f"Дата в ссылке не разбирается: {raw}") from exc


def parse_resync_input(raw: str) -> ResyncTarget:
    """Определить платформу и вид заявки по вставленной ссылке.

    Принимаем только ссылки: голое число у 5 вёрст — id участника, у S95 —
    тоже, и угадывать платформу по нему нельзя.
    """
    value = (raw or "").strip()
    if not value:
        raise ResyncInputError("Вставьте ссылку на профиль, протокол или список стартов локации")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    if not host:
        raise ResyncInputError("Не похоже на ссылку")

    if host == "5verst.ru":
        return _parse_five_verst(value, parsed.path)
    if any(host == domain or host.endswith(f".{domain}") for domain in S95_DOMAINS):
        return _parse_s95(host, parsed.path)
    raise ResyncInputError("Поддерживаются ссылки на 5verst.ru и s95.ru / s95.rs / s95.by")


def _parse_five_verst(value: str, path: str) -> ResyncTarget:
    profile_match = USERSTATS_URL_RE.match(value.split("?", 1)[0])
    if profile_match:
        user_id = profile_match.group("user_id")
        return ResyncTarget(
            platform_code=PLATFORM_FIVE_VERST,
            kind=KIND_PROFILE,
            canonical_url=f"https://5verst.ru/userstats/{user_id}/",
            external_user_id=user_id,
        )
    protocol_match = FIVE_VERST_PROTOCOL_RE.match(value)
    if protocol_match:
        slug = protocol_match.group("slug").lower()
        event_date = _parse_ru_date(protocol_match.group("date"))
        return ResyncTarget(
            platform_code=PLATFORM_FIVE_VERST,
            kind=KIND_PROTOCOL,
            canonical_url=bulk_parser._results_date_url(slug, event_date),
            slug=slug,
            event_date=event_date,
        )
    location_match = FIVE_VERST_LOCATION_RE.match(value)
    if location_match:
        slug = location_match.group("slug").lower()
        if slug in bulk_parser.RESERVED_SLUGS:
            raise ResyncInputError(
                f"«{slug}» — служебная страница 5 вёрст, а не локация. "
                "Нужна ссылка вида https://5verst.ru/<slug>/results/all/"
            )
        return ResyncTarget(
            platform_code=PLATFORM_FIVE_VERST,
            kind=KIND_LOCATION,
            canonical_url=bulk_parser._results_all_url(slug),
            slug=slug,
        )
    raise ResyncInputError(
        "У 5 вёрст понимаю три вида ссылок: профиль https://5verst.ru/userstats/<id>/, "
        "протокол https://5verst.ru/<slug>/results/DD.MM.YYYY/ и список стартов "
        f"https://5verst.ru/<slug>/results/all/ (получил {path or '/'})"
    )


def _parse_s95(host: str, path: str) -> ResyncTarget:
    athlete = S95_ATHLETE_RE.match(path)
    if athlete:
        user_id = athlete.group("id")
        return ResyncTarget(
            platform_code=PLATFORM_S95,
            kind=KIND_PROFILE,
            canonical_url=f"https://{host}/athletes/{user_id}/",
            external_user_id=user_id,
            domain=host,
        )
    activity = S95_ACTIVITY_RE.match(path)
    if activity:
        activity_id = activity.group("id")
        return ResyncTarget(
            platform_code=PLATFORM_S95,
            kind=KIND_PROTOCOL,
            canonical_url=f"https://{host}/activities/{activity_id}",
            activity_id=activity_id,
            domain=host,
        )
    event = S95_EVENT_RE.match(path)
    if event:
        slug = event.group("slug")
        return ResyncTarget(
            platform_code=PLATFORM_S95,
            kind=KIND_LOCATION,
            canonical_url=f"https://{host}/events/{slug}",
            slug=slug,
            domain=host,
        )
    raise ResyncInputError(
        "У S95 понимаю три вида ссылок: профиль https://s95.ru/athletes/<id>/, "
        "протокол https://s95.ru/activities/<id> и локацию https://s95.ru/events/<slug> "
        f"(получил {path or '/'})"
    )


# ===== Заявка и её прогресс =====


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _task_id(request_id: UUID) -> str:
    return f"admin-resync:{request_id}"


def create_resync_request(db: Session, raw_url: str, *, user_id: UUID | None) -> AdminResyncRequest:
    """Разобрать ссылку, создать заявку и поставить её в приоритетную очередь."""
    target = parse_resync_input(raw_url)
    row = AdminResyncRequest(
        platform_code=target.platform_code,
        kind=target.kind,
        input_url=raw_url.strip(),
        target=target.as_dict(),
        status=STATUS_QUEUED,
        steps=[],
        created_by_user_id=user_id,
    )
    db.add(row)
    db.flush()
    row.celery_task_id = _task_id(row.id)
    _append_step(row, "queued", "Ждём очереди")
    db.commit()
    db.refresh(row)
    _dispatch(row)
    return row


def _dispatch(row: AdminResyncRequest) -> None:
    from app.workers.tasks.admin_resync import admin_resync_five_verst_task, admin_resync_s95_task

    task = admin_resync_five_verst_task if row.platform_code == PLATFORM_FIVE_VERST else admin_resync_s95_task
    task.apply_async(
        args=[str(row.id)],
        queue=QUEUE_BY_PLATFORM[row.platform_code],
        task_id=row.celery_task_id,
    )


def _append_step(row: AdminResyncRequest, code: str, text: str, details: dict[str, Any] | None = None) -> None:
    steps = list(row.steps or [])
    entry: dict[str, Any] = {"at": _utcnow().isoformat(), "code": code, "text": text}
    if details:
        entry["details"] = details
    steps.append(entry)
    row.steps = steps
    flag_modified(row, "steps")


class ResyncProgress:
    """Пишет шаги в заявку с немедленным коммитом — страница видит их живьём.

    Шаг пишется только на границах, где транзакция и так закрыта (до
    сетевого запроса и после коммита протокола): иначе коммит шага утащил бы
    в базу полупереписанный протокол.
    """

    def __init__(self, db: Session, request_id: UUID) -> None:
        self.db = db
        self.request_id = request_id

    def step(self, code: str, text: str, **details: Any) -> None:
        row = self.db.get(AdminResyncRequest, self.request_id)
        if row is None:
            return
        _append_step(row, code, text, {k: v for k, v in details.items() if v is not None} or None)
        self.db.commit()
        logger.info("admin resync %s: %s", self.request_id, text)


def queue_position(row: AdminResyncRequest) -> tuple[int | None, int | None]:
    """(позиция в очереди, длина очереди) для заявки, которая ещё ждёт."""
    if row.status != STATUS_QUEUED or not row.celery_task_id:
        return None, None
    from app.services.celery_queue_inspector import find_task_queue_position, get_queue_length

    queue_name = QUEUE_BY_PLATFORM.get(row.platform_code)
    if queue_name is None:
        return None, None
    try:
        return find_task_queue_position(queue_name, row.celery_task_id), get_queue_length(queue_name)
    except Exception:  # noqa: BLE001 — Redis недоступен: позиция просто неизвестна
        logger.warning("admin resync: не удалось прочитать очередь %s", queue_name, exc_info=True)
        return None, None


def list_recent_requests(db: Session, *, limit: int = 30) -> list[AdminResyncRequest]:
    return db.query(AdminResyncRequest).order_by(AdminResyncRequest.created_at.desc()).limit(limit).all()


def get_request(db: Session, request_id: UUID) -> AdminResyncRequest | None:
    return db.get(AdminResyncRequest, request_id)


# ===== Слепки протокола и дифф =====


@dataclass(frozen=True)
class RunSnap:
    position: int | None
    finish_time_sec: int | None
    finish_time_display: str | None
    status: str | None
    name: str
    external_user_id: str | None


@dataclass(frozen=True)
class VolunteerSnap:
    role: str | None
    name: str


@dataclass
class EventSnapshot:
    runs: dict[str, RunSnap] = field(default_factory=dict)
    volunteers: dict[str, VolunteerSnap] = field(default_factory=dict)


UNKNOWN_LABEL = "НЕИЗВЕСТНЫЙ"


def _is_unknown(snap: RunSnap) -> bool:
    return snap.status == "unknown" or (snap.external_user_id or "").startswith("unknown:")


def snapshot_event(db: Session, event_id: UUID | None) -> EventSnapshot:
    snapshot = EventSnapshot()
    if event_id is None:
        return snapshot
    run_rows = (
        db.query(RunResult, Participant)
        .outerjoin(Participant, Participant.id == RunResult.participant_id)
        .filter(RunResult.event_id == event_id)
        .all()
    )
    for row, participant in run_rows:
        snapshot.runs[row.external_result_key] = RunSnap(
            position=row.position,
            finish_time_sec=row.finish_time_sec,
            finish_time_display=row.finish_time_display,
            status=row.status,
            name=(participant.display_name if participant and participant.display_name else UNKNOWN_LABEL),
            external_user_id=participant.external_user_id if participant else None,
        )
    vol_rows = (
        db.query(VolunteerResult, Participant)
        .outerjoin(Participant, Participant.id == VolunteerResult.participant_id)
        .filter(VolunteerResult.event_id == event_id)
        .all()
    )
    for row, participant in vol_rows:
        name = row.display_name or (participant.display_name if participant else None) or UNKNOWN_LABEL
        snapshot.volunteers[row.external_result_key] = VolunteerSnap(role=row.role, name=name)
    return snapshot


def _run_entry(snap: RunSnap) -> dict[str, Any]:
    return {
        "name": snap.name,
        "position": snap.position,
        "time": snap.finish_time_display,
        "time_sec": snap.finish_time_sec,
    }


def _capped(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    return items[:_DIFF_SAMPLE_LIMIT], len(items)


def diff_snapshots(before: EventSnapshot, after: EventSnapshot) -> dict[str, Any]:
    """Что изменилось в протоколе между двумя слепками.

    «Неизвестный стал известным» при той же позиции и времени — не удаление
    с добавлением, а отдельная категория «опознан» (то же правило, что в
    журнале правок протокола, решение Дмитрия 23.08.2026).
    """
    added_keys = [key for key in after.runs if key not in before.runs]
    removed_keys = [key for key in before.runs if key not in after.runs]

    identified: list[dict[str, Any]] = []
    remaining_added = list(added_keys)
    remaining_removed: list[str] = []
    for key in removed_keys:
        old = before.runs[key]
        if not _is_unknown(old):
            remaining_removed.append(key)
            continue
        # «Неизвестный» на той же позиции стал именем — с тем же временем или
        # получил время, которого у него не было (так выглядит поздняя привязка
        # штрихкода: Серпухов №219, 22.08.2026).
        pair = next(
            (
                new_key
                for new_key in remaining_added
                if after.runs[new_key].position == old.position
                and (old.finish_time_sec is None or after.runs[new_key].finish_time_sec == old.finish_time_sec)
                and not _is_unknown(after.runs[new_key])
            ),
            None,
        )
        if pair is None:
            remaining_removed.append(key)
            continue
        remaining_added.remove(pair)
        identified.append(_run_entry(after.runs[pair]))

    changed: list[dict[str, Any]] = []
    for key, old in before.runs.items():
        new = after.runs.get(key)
        if new is None:
            continue
        if old.position == new.position and old.finish_time_sec == new.finish_time_sec:
            if _is_unknown(old) and not _is_unknown(new):
                identified.append(_run_entry(new))
            continue
        changed.append(
            {
                "name": new.name,
                "position_before": old.position,
                "position_after": new.position,
                "time_before": old.finish_time_display,
                "time_after": new.finish_time_display,
            }
        )

    added = sorted((_run_entry(after.runs[key]) for key in remaining_added), key=lambda r: r["position"] or 10**6)
    removed = sorted((_run_entry(before.runs[key]) for key in remaining_removed), key=lambda r: r["position"] or 10**6)
    changed.sort(key=lambda r: r["position_after"] or 10**6)
    identified.sort(key=lambda r: r["position"] or 10**6)

    vol_added = [
        {"name": after.volunteers[key].name, "role": after.volunteers[key].role}
        for key in after.volunteers
        if key not in before.volunteers
    ]
    vol_removed = [
        {"name": before.volunteers[key].name, "role": before.volunteers[key].role}
        for key in before.volunteers
        if key not in after.volunteers
    ]
    vol_changed = [
        {
            "name": after.volunteers[key].name,
            "role_before": before.volunteers[key].role,
            "role_after": after.volunteers[key].role,
        }
        for key in before.volunteers
        if key in after.volunteers and before.volunteers[key].role != after.volunteers[key].role
    ]

    added_sample, added_total = _capped(added)
    removed_sample, removed_total = _capped(removed)
    changed_sample, changed_total = _capped(changed)
    identified_sample, identified_total = _capped(identified)
    has_changes = bool(
        added_total or removed_total or changed_total or identified_total or vol_added or vol_removed or vol_changed
    )
    return {
        "changed": has_changes,
        "runs": {
            "before": len(before.runs),
            "after": len(after.runs),
            "added": added_sample,
            "added_total": added_total,
            "removed": removed_sample,
            "removed_total": removed_total,
            "changed": changed_sample,
            "changed_total": changed_total,
            "identified": identified_sample,
            "identified_total": identified_total,
        },
        "volunteers": {
            "before": len(before.volunteers),
            "after": len(after.volunteers),
            "added": vol_added[:_DIFF_SAMPLE_LIMIT],
            "added_total": len(vol_added),
            "removed": vol_removed[:_DIFF_SAMPLE_LIMIT],
            "removed_total": len(vol_removed),
            "changed": vol_changed[:_DIFF_SAMPLE_LIMIT],
            "changed_total": len(vol_changed),
        },
    }


def describe_diff(diff: dict[str, Any]) -> str:
    """Короткая фраза для шага и сводки: «+2 добавлено, 1 поправлено»."""
    if not diff.get("changed"):
        return "без изменений"
    runs = diff["runs"]
    vols = diff["volunteers"]
    parts: list[str] = []
    if runs["added_total"]:
        parts.append(f"добавлено {runs['added_total']}")
    if runs["removed_total"]:
        parts.append(f"удалено {runs['removed_total']}")
    if runs["changed_total"]:
        parts.append(f"поправлено {runs['changed_total']}")
    if runs["identified_total"]:
        parts.append(f"опознано {runs['identified_total']}")
    vol_parts: list[str] = []
    if vols["added_total"]:
        vol_parts.append(f"+{vols['added_total']}")
    if vols["removed_total"]:
        vol_parts.append(f"−{vols['removed_total']}")
    if vols["changed_total"]:
        vol_parts.append(f"роль у {vols['changed_total']}")
    if vol_parts:
        parts.append("волонтёры: " + ", ".join(vol_parts))
    return ", ".join(parts) if parts else "без изменений"


# ===== Общие кирпичики =====


def _event_label(location_name: str, event_date: date, event_number: int | None) -> str:
    number = f" #{event_number}" if event_number else ""
    return f"{location_name}{number} · {event_date.strftime('%d.%m.%Y')}"


@dataclass
class ProtocolOutcome:
    label: str
    slug: str
    event_date: str
    event_number: int | None
    reason: str
    changed: bool = False
    diff: dict[str, Any] | None = None
    error: str | None = None
    event_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "slug": self.slug,
            "event_date": self.event_date,
            "event_number": self.event_number,
            "reason": self.reason,
            "changed": self.changed,
            "diff": self.diff,
            "error": self.error,
            "event_id": self.event_id,
        }


@dataclass
class ProtocolPlanItem:
    """Протокол, который надо перекачать: у 5 вёрст — summary, у S95 — activity ref."""

    slug: str
    event_date: date
    event_number: int | None
    location_name: str
    reason: str
    summary: CanonicalEventSummary | None = None
    summary_row: EventSummary | None = None
    location: Location | None = None
    activity_ref: Any = None
    activity_url: str | None = None

    @property
    def label(self) -> str:
        return _event_label(self.location_name, self.event_date, self.event_number)


def _protocol_limit() -> int:
    return max(int(get_settings().admin_resync_protocol_limit), 1)


def _run_protocols(
    db: Session,
    progress: ResyncProgress,
    plan: list[ProtocolPlanItem],
    refetch: Callable[[Session, ProtocolPlanItem], tuple[UUID, bool]],
    *,
    platform_code: str,
) -> tuple[list[ProtocolOutcome], list[str]]:
    """Перекачать протоколы из плана в приоритете; остаток — в обычную очередь.

    `refetch` делает сетевой запрос и перезаписывает протокол; сюда
    возвращает id события и признак «источник изменился». Слепки до/после и
    дифф — здесь, одинаково для обеих платформ.
    """
    from app.sync.profile_protocol_queue import ProfileEventRef, _dispatch_protocol_fetch

    limit = _protocol_limit()
    outcomes: list[ProtocolOutcome] = []
    deferred: list[str] = []
    for index, item in enumerate(plan):
        if index >= limit:
            ref = ProfileEventRef(
                platform_code=platform_code,
                location_slug=item.slug,
                location_name=item.location_name,
                event_date=item.event_date,
                event_number=item.event_number,
            )
            _dispatch_protocol_fetch(ref, force=True)
            deferred.append(item.label)
            continue

        outcome = ProtocolOutcome(
            label=item.label,
            slug=item.slug,
            event_date=item.event_date.isoformat(),
            event_number=item.event_number,
            reason=item.reason,
        )
        before_event_id = _find_event_id(db, platform_code, item)
        before = snapshot_event(db, before_event_id)
        progress.step("protocol_fetch", f"Перекачиваем протокол {item.label}", reason=item.reason)
        try:
            event_id, source_changed = refetch(db, item)
            db.commit()
        except (FiveVerstBanDetected, S95BanDetected):
            db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 — один битый протокол не должен ронять заявку
            db.rollback()
            outcome.error = str(exc)[:500]
            logger.warning("admin resync: протокол %s не перекачан: %s", item.label, exc, exc_info=True)
            progress.step("protocol_error", f"{item.label}: не удалось перекачать — {outcome.error}")
            outcomes.append(outcome)
            continue

        after = snapshot_event(db, event_id)
        diff = diff_snapshots(before, after)
        outcome.diff = diff
        outcome.changed = bool(diff["changed"])
        outcome.event_id = str(event_id)
        if before_event_id is None:
            outcome.reason = outcome.reason or "new"
            summary_text = f"загружен впервые: {after_summary(after)}"
        elif diff["changed"]:
            summary_text = describe_diff(diff)
        elif source_changed:
            summary_text = "страница у источника изменилась, но состав и времена те же"
        else:
            summary_text = "без изменений"
        progress.step("protocol_done", f"{item.label}: {summary_text}", changed=outcome.changed)
        outcomes.append(outcome)

    if deferred:
        progress.step(
            "deferred",
            f"Лимит приоритетной перекачки — {limit}; ещё {len(deferred)} протокол(ов) "
            "поставлены в обычную очередь батча",
            protocols=deferred[:20],
        )
    return outcomes, deferred


def after_summary(after: EventSnapshot) -> str:
    return f"{len(after.runs)} финишей, {len(after.volunteers)} волонтёров"


def _find_event_id(db: Session, platform_code: str, item: ProtocolPlanItem) -> UUID | None:
    platform = upsert.get_platform(db, platform_code)
    if item.summary_row is not None and item.summary_row.event_id is not None:
        return item.summary_row.event_id
    location = item.location
    if location is None:
        location = (
            db.query(Location)
            .filter(Location.platform_id == platform.id, Location.external_key == item.slug)
            .one_or_none()
        )
    if location is None:
        return None
    event = upsert._find_event_by_location_date(db, platform, location.id, item.event_date)
    return event.id if event is not None else None


def _protocols_result(outcomes: list[ProtocolOutcome], deferred: list[str]) -> dict[str, Any]:
    return {
        "protocols": [outcome.as_dict() for outcome in outcomes],
        "protocols_checked": len(outcomes),
        "protocols_changed": sum(1 for o in outcomes if o.changed),
        "protocols_unchanged": sum(1 for o in outcomes if not o.changed and o.error is None),
        "protocols_failed": sum(1 for o in outcomes if o.error),
        "deferred": deferred,
        "deferred_total": len(deferred),
    }


# ===== 5 вёрст =====


def _five_verst_location(db: Session, platform: Platform, slug: str, *, name_hint: str | None = None) -> Location:
    location = (
        db.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one_or_none()
    )
    if location is not None:
        return location
    # Локации у нас нет — заводим заглушку без похода в сеть: имя и координаты
    # подтянет ежедневный реестр /events/. Ради одной заявки лишний фетч не нужен.
    location, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(
            external_key=slug,
            name=name_hint or slug,
            country="Россия",
            source_url=f"https://5verst.ru/{slug}/",
        ),
    )
    db.flush()
    return location


def _five_verst_summary_for_date(
    db: Session,
    platform: Platform,
    location: Location,
    event_date: date,
    *,
    event_number: int | None,
) -> tuple[CanonicalEventSummary, EventSummary]:
    from app.sync.profile_protocol_queue import _summary_row_to_canonical

    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.location_id == location.id,
            EventSummary.event_date == event_date,
        )
        .order_by(EventSummary.event_number.desc().nullslast())
        .first()
    )
    if summary_row is None:
        stub = CanonicalEventSummary(
            external_event_key=f"{location.external_key}:{event_number or 0}:{event_date.isoformat()}",
            event_date=event_date,
            event_number=event_number,
            location_external_key=location.external_key,
            location_name=location.name,
            source_url=bulk_parser._results_date_url(location.external_key, event_date),
            summary_hash="admin_resync_stub",
        )
        summary_row, _ = upsert.upsert_event_summary(db, platform, location, stub)
        db.flush()
    return _summary_row_to_canonical(summary_row, location), summary_row


def _refetch_five_verst(db: Session, item: ProtocolPlanItem) -> tuple[UUID, bool]:
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

    platform = upsert.get_platform(db, PLATFORM_FIVE_VERST)
    location = item.location or _five_verst_location(db, platform, item.slug, name_hint=item.location_name)
    summary, summary_row = (
        (item.summary, item.summary_row)
        if item.summary is not None and item.summary_row is not None
        else _five_verst_summary_for_date(db, platform, location, item.event_date, event_number=item.event_number)
    )
    result = fetch_and_upsert_event_protocol(db, platform, location, summary, summary_row)
    return UUID(result.event_id), result.protocol_changed


def _run_five_verst_protocol(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    slug = row.target["slug"]
    event_date = date.fromisoformat(row.target["event_date"])
    platform = upsert.get_platform(db, PLATFORM_FIVE_VERST)
    location = _five_verst_location(db, platform, slug)
    db.commit()
    summary, summary_row = _five_verst_summary_for_date(db, platform, location, event_date, event_number=None)
    db.commit()
    item = ProtocolPlanItem(
        slug=slug,
        event_date=event_date,
        event_number=summary_row.event_number,
        location_name=location.name,
        reason="requested",
        summary=summary,
        summary_row=summary_row,
        location=location,
    )
    progress.step("fetch", f"Запрашиваем протокол {item.label}")
    outcomes, deferred = _run_protocols(db, progress, [item], _refetch_five_verst, platform_code=PLATFORM_FIVE_VERST)
    return {"label": item.label, **_protocols_result(outcomes, deferred)}


def _summary_reason(db: Session, platform: Platform, location: Location, summary: CanonicalEventSummary) -> str | None:
    """Почему сводка расходится с базой; None — совпадает."""
    from app.sync.protocol_debt import protocol_is_stale

    existing = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.location_id == location.id,
            EventSummary.event_date == summary.event_date,
        )
        .order_by(EventSummary.event_number.desc().nullslast())
        .first()
    )
    if existing is None:
        return "new_summary"
    if existing.summary_hash != summary.summary_hash:
        return "summary_changed"
    if existing.event_id is None:
        return "missing_protocol"
    state = db.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == existing.event_id).one_or_none()
    if state is None or state.last_protocol_fetched_at is None:
        return "protocol_never_fetched"
    if protocol_is_stale(state, existing):
        return "protocol_debt"
    return None


REASON_LABELS = {
    "requested": "по ссылке",
    "new_summary": "нового старта нет в базе",
    "summary_changed": "сводка изменилась",
    "missing_protocol": "протокол не загружен",
    "protocol_never_fetched": "протокол ни разу не качался целиком",
    "protocol_debt": "протокол отстал от сводки",
    "updated": "источник обновил протокол",
    "new": "протокола нет в базе",
    "missing_run": "пробежки нет в базе",
    "time_mismatch": "не совпало время",
    "missing_volunteering": "волонтёрства нет в базе",
    "extra_in_db": "у нас есть строка, которой нет в профиле",
}


def _run_five_verst_location(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    slug = row.target["slug"]
    platform = upsert.get_platform(db, PLATFORM_FIVE_VERST)
    location = _five_verst_location(db, platform, slug)
    db.commit()
    progress.step("fetch", f"Запрашиваем список стартов {location.name}")
    summaries, _html = bulk_parser.fetch_event_summaries(slug, location.name)
    progress.step("compare", f"Сравниваем {len(summaries)} стартов с базой")

    plan: list[ProtocolPlanItem] = []
    unchanged = 0
    reasons: dict[str, int] = {}
    for summary in summaries:
        reason = _summary_reason(db, platform, location, summary)
        if reason is None:
            unchanged += 1
            continue
        summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
        db.flush()
        reasons[reason] = reasons.get(reason, 0) + 1
        plan.append(
            ProtocolPlanItem(
                slug=slug,
                event_date=summary.event_date,
                event_number=summary.event_number,
                location_name=location.name,
                reason=reason,
                summary=summary,
                summary_row=summary_row,
                location=location,
            )
        )
    db.commit()
    # Свежие сверху — если упрёмся в лимит, в приоритете окажутся последние субботы.
    plan.sort(key=lambda item: item.event_date, reverse=True)

    if not plan:
        progress.step("compared", f"Сравнили: расхождений нет, все {len(summaries)} стартов совпадают со сводкой")
        outcomes: list[ProtocolOutcome] = []
        deferred: list[str] = []
    else:
        dates = ", ".join(item.event_date.strftime("%d.%m.%Y") for item in plan[:8])
        more = f" и ещё {len(plan) - 8}" if len(plan) > 8 else ""
        progress.step(
            "compared",
            f"Сравнили: нашли расхождения в {len(plan)} стартах — {dates}{more}",
            reasons={REASON_LABELS.get(k, k): v for k, v in reasons.items()},
        )
        outcomes, deferred = _run_protocols(db, progress, plan, _refetch_five_verst, platform_code=PLATFORM_FIVE_VERST)

    return {
        "label": location.name,
        "summaries_total": len(summaries),
        "summaries_unchanged": unchanged,
        "summaries_diverged": len(plan),
        "reasons": reasons,
        **_protocols_result(outcomes, deferred),
    }


@dataclass
class ProfileComparison:
    participant_name: str
    profile_runs: int
    profile_volunteering: int
    db_runs_before: int
    plan: list[ProtocolPlanItem]
    missing: list[dict[str, Any]]
    mismatched: list[dict[str, Any]]
    not_fully_loaded: list[dict[str, Any]]
    missing_volunteering: list[dict[str, Any]]
    extra_in_db: list[dict[str, Any]]


def _compare_profile(
    db: Session,
    platform: Platform,
    participant: Participant,
    runs: list[CanonicalRunResult],
    volunteering: list[CanonicalVolunteerResult],
) -> ProfileComparison:
    """Сверить строки профиля с базой и собрать план перекачки.

    Протокол перекачивается целиком, если у источника человек есть, а у нас
    его строки нет, время не совпало, протокол ни разу не качался целиком —
    или наоборот, у нас есть строка, которой в профиле нет.
    """
    db_runs = (
        db.query(RunResult, Event, Location, ProtocolSyncState)
        .join(Event, Event.id == RunResult.event_id)
        .join(Location, Location.id == Event.location_id)
        .outerjoin(ProtocolSyncState, ProtocolSyncState.event_id == Event.id)
        .filter(RunResult.participant_id == participant.id, Event.platform_id == platform.id)
        .all()
    )
    by_key: dict[tuple[str, date], tuple[RunResult, Event, Location, ProtocolSyncState | None]] = {}
    for run_row, event, location, state in db_runs:
        by_key[(location.external_key, event.event_date)] = (run_row, event, location, state)

    db_vols = (
        db.query(VolunteerResult, Event, Location)
        .join(Event, Event.id == VolunteerResult.event_id)
        .join(Location, Location.id == Event.location_id)
        .filter(VolunteerResult.participant_id == participant.id, Event.platform_id == platform.id)
        .all()
    )
    vol_keys = {(location.external_key, event.event_date) for _row, event, location in db_vols}

    plan_by_key: dict[tuple[str, date], ProtocolPlanItem] = {}
    missing: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    not_fully_loaded: list[dict[str, Any]] = []
    missing_vol: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    profile_keys: set[tuple[str, date]] = set()

    def _plan(slug: str, event_date: date, number: int | None, name: str, reason: str) -> None:
        key = (slug, event_date)
        if key in plan_by_key:
            return
        plan_by_key[key] = ProtocolPlanItem(
            slug=slug,
            event_date=event_date,
            event_number=number,
            location_name=name or slug,
            reason=reason,
        )

    for run in runs:
        slug = upsert._normalize_location_slug(run.location_external_key, run.location_name)
        if slug == "unknown":
            continue
        key = (slug, run.event_date)
        profile_keys.add(key)
        label = _event_label(run.location_name or slug, run.event_date, run.event_number)
        found = by_key.get(key)
        if found is None:
            missing.append({"label": label, "time": run.finish_time_display})
            _plan(slug, run.event_date, run.event_number, run.location_name, "missing_run")
            continue
        run_row, _event, _location, state = found
        if state is None or state.last_protocol_fetched_at is None:
            not_fully_loaded.append({"label": label})
            _plan(slug, run.event_date, run.event_number, run.location_name, "protocol_never_fetched")
            continue
        if (
            run.finish_time_sec is not None
            and run_row.finish_time_sec is not None
            and run.finish_time_sec != run_row.finish_time_sec
        ):
            mismatched.append(
                {"label": label, "profile_time": run.finish_time_display, "db_time": run_row.finish_time_display}
            )
            _plan(slug, run.event_date, run.event_number, run.location_name, "time_mismatch")

    for vol in volunteering:
        slug = upsert._normalize_location_slug(vol.location_external_key, vol.location_name)
        if slug == "unknown":
            continue
        key = (slug, vol.event_date)
        if key in vol_keys:
            continue
        label = _event_label(vol.location_name or slug, vol.event_date, None)
        missing_vol.append({"label": label, "role": vol.role})
        _plan(slug, vol.event_date, None, vol.location_name, "missing_volunteering")

    for key, (_run_row, event, location, _state) in by_key.items():
        if key in profile_keys or event.is_test_event:
            continue
        extra.append({"label": _event_label(location.name, event.event_date, event.event_number)})
        _plan(location.external_key, event.event_date, event.event_number, location.name, "extra_in_db")

    plan = sorted(plan_by_key.values(), key=lambda item: item.event_date, reverse=True)
    return ProfileComparison(
        participant_name=participant.display_name or participant.external_user_id,
        profile_runs=len(runs),
        profile_volunteering=len(volunteering),
        db_runs_before=sum(1 for _r, event, _l, _s in db_runs if not event.is_test_event),
        plan=plan,
        missing=missing,
        mismatched=mismatched,
        not_fully_loaded=not_fully_loaded,
        missing_volunteering=missing_vol,
        extra_in_db=extra,
    )


def _report_comparison(progress: ResyncProgress, comparison: ProfileComparison) -> None:
    if not comparison.plan:
        progress.step(
            "compared",
            f"Сравнили: расхождений нет — {comparison.profile_runs} пробежек профиля есть в базе",
        )
        return
    parts: list[str] = []
    if comparison.missing:
        parts.append(f"нет в базе: {len(comparison.missing)}")
    if comparison.mismatched:
        parts.append(f"не совпало время: {len(comparison.mismatched)}")
    if comparison.not_fully_loaded:
        parts.append(f"протокол не качался целиком: {len(comparison.not_fully_loaded)}")
    if comparison.missing_volunteering:
        parts.append(f"нет волонтёрств: {len(comparison.missing_volunteering)}")
    if comparison.extra_in_db:
        parts.append(f"лишних у нас: {len(comparison.extra_in_db)}")
    dates = ", ".join(item.event_date.strftime("%d.%m.%Y") for item in comparison.plan[:6])
    more = f" и ещё {len(comparison.plan) - 6}" if len(comparison.plan) > 6 else ""
    progress.step(
        "compared",
        f"Сравнили: нашли расхождения в {len(comparison.plan)} протоколах ({'; '.join(parts)}) — {dates}{more}",
    )


def _count_participant_runs(db: Session, participant_id: UUID) -> int:
    return (
        db.query(RunResult)
        .join(Event, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id == participant_id, Event.is_test_event.is_(False))
        .count()
    )


def _profile_result(
    comparison: ProfileComparison,
    *,
    participant: Participant,
    expected_runs: int | None,
    db_runs_after: int,
    outcomes: list[ProtocolOutcome],
    deferred: list[str],
) -> dict[str, Any]:
    return {
        "label": comparison.participant_name,
        "participant": {
            "name": comparison.participant_name,
            "external_user_id": participant.external_user_id,
            "profile_url": participant.profile_url,
        },
        "profile_runs": comparison.profile_runs,
        "profile_runs_declared": expected_runs,
        "profile_volunteering": comparison.profile_volunteering,
        "db_runs_before": comparison.db_runs_before,
        "db_runs_after": db_runs_after,
        "missing": comparison.missing[:_DIFF_SAMPLE_LIMIT],
        "missing_total": len(comparison.missing),
        "mismatched": comparison.mismatched[:_DIFF_SAMPLE_LIMIT],
        "mismatched_total": len(comparison.mismatched),
        "not_fully_loaded": comparison.not_fully_loaded[:_DIFF_SAMPLE_LIMIT],
        "not_fully_loaded_total": len(comparison.not_fully_loaded),
        "missing_volunteering": comparison.missing_volunteering[:_DIFF_SAMPLE_LIMIT],
        "missing_volunteering_total": len(comparison.missing_volunteering),
        "extra_in_db": comparison.extra_in_db[:_DIFF_SAMPLE_LIMIT],
        "extra_in_db_total": len(comparison.extra_in_db),
        **_protocols_result(outcomes, deferred),
    }


def _run_five_verst_profile(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    from app.platform_adapters.five_verst.parser import (
        fetch_userstats_html,
        parse_userstats_html,
        parse_userstats_runs_html,
        parse_userstats_volunteering_html,
    )
    from app.platform_adapters.five_verst.url import normalize_profile_url
    from app.sync.user_sync import _apply_participant_profile

    profile_url = row.target["canonical_url"]
    platform = upsert.get_platform(db, PLATFORM_FIVE_VERST)
    progress.step("fetch", f"Запрашиваем профиль {profile_url}")
    db.commit()
    html = fetch_userstats_html(profile_url)
    profile = parse_userstats_html(html, normalize_profile_url(profile_url))
    runs = parse_userstats_runs_html(html, profile.external_user_id, profile.display_name)
    volunteering = parse_userstats_volunteering_html(html, profile.external_user_id, profile.display_name)
    participant = _apply_participant_profile(db, platform, profile)
    db.commit()

    progress.step(
        "compare",
        f"{profile.display_name}: в профиле {len(runs)} пробежек и {len(volunteering)} волонтёрств — сравниваем с базой",
    )
    comparison = _compare_profile(db, platform, participant, runs, volunteering)
    _report_comparison(progress, comparison)
    outcomes, deferred = _run_protocols(
        db, progress, comparison.plan, _refetch_five_verst, platform_code=PLATFORM_FIVE_VERST
    )
    db_runs_after = _count_participant_runs(db, participant.id)
    return _profile_result(
        comparison,
        participant=participant,
        expected_runs=profile.total_runs,
        db_runs_after=db_runs_after,
        outcomes=outcomes,
        deferred=deferred,
    )


# ===== S95 =====


def _s95_location(
    db: Session, platform: Platform, slug: str, *, domain: str | None, name_hint: str | None = None
) -> Location:
    location = (
        db.query(Location).filter(Location.platform_id == platform.id, Location.external_key == slug).one_or_none()
    )
    if location is not None:
        return location
    host = domain or "s95.ru"
    location, _ = upsert.upsert_location(
        db,
        platform,
        CanonicalLocation(
            external_key=slug,
            name=name_hint or slug,
            source_url=f"https://{host}/events/{slug}",
        ),
    )
    db.flush()
    return location


def _refetch_s95(db: Session, item: ProtocolPlanItem) -> tuple[UUID, bool]:
    from app.sync.s95_protocol_api import fetch_and_upsert_activity_protocol_api, upsert_activity_protocol_api
    from app.sync.s95_protocol_lookup import resolve_s95_protocol

    platform = upsert.get_platform(db, PLATFORM_S95)
    if item.activity_ref is not None and item.location is not None:
        outcome = upsert_activity_protocol_api(
            db, platform, item.location, item.activity_ref, event_number=item.event_number
        )
        event = (
            db.query(Event)
            .filter(Event.platform_id == platform.id, Event.external_event_key == outcome.external_event_key)
            .one()
        )
        return event.id, outcome.changed

    resolved = resolve_s95_protocol(
        db,
        platform,
        location_slug=item.slug,
        location_name=item.location_name,
        event_date=item.event_date,
    )
    if resolved is None:
        raise ValueError(f"протокол S95 за {item.event_date.strftime('%d.%m.%Y')} не найден в /events/{item.slug}.json")
    state_before = (
        db.query(ProtocolSyncState.protocol_source_hash)
        .filter(ProtocolSyncState.event_id == resolved.summary_row.event_id)
        .scalar()
        if resolved.summary_row.event_id is not None
        else None
    )
    fetch_and_upsert_activity_protocol_api(
        db,
        platform,
        resolved.location,
        resolved.summary,
        resolved.summary_row,
        protocol_url=resolved.protocol_url,
    )
    db.flush()
    summary_row = db.get(EventSummary, resolved.summary_row.id)
    if summary_row is None or summary_row.event_id is None:
        raise ValueError("после перекачки у сводки нет события")
    state_after = (
        db.query(ProtocolSyncState.protocol_source_hash)
        .filter(ProtocolSyncState.event_id == summary_row.event_id)
        .scalar()
    )
    return summary_row.event_id, state_before != state_after


def _run_s95_protocol(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    from app.sync.s95_protocol_lookup import _summary_to_canonical

    activity_id = row.target["activity_id"]
    platform = upsert.get_platform(db, PLATFORM_S95)
    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.source_url.ilike(f"%/activities/{activity_id}%"),
        )
        .order_by(EventSummary.created_at.desc())
        .first()
    )
    if summary_row is None:
        raise ValueError(
            f"Активность {activity_id} ещё не известна базе — S95 не отдаёт по ней локацию. "
            "Вставьте ссылку на локацию (https://s95.ru/events/<slug>), она подтянет новые старты."
        )
    location = db.get(Location, summary_row.location_id)
    if location is None:
        raise ValueError("у сводки нет локации")
    from app.s95.api_client import S95ApiActivityRef

    activity_url = (
        summary_row.source_url or f"https://{row.target.get('domain') or 's95.ru'}/activities/{activity_id}.json"
    )
    summary = _summary_to_canonical(summary_row, location)
    item = ProtocolPlanItem(
        slug=location.external_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_name=location.name,
        reason="requested",
        summary=summary,
        summary_row=summary_row,
        location=location,
        activity_ref=S95ApiActivityRef(date=summary_row.event_date.isoformat(), url=activity_url),
    )
    progress.step("fetch", f"Запрашиваем протокол {item.label}")
    outcomes, deferred = _run_protocols(db, progress, [item], _refetch_s95, platform_code=PLATFORM_S95)
    return {"label": item.label, **_protocols_result(outcomes, deferred)}


def _run_s95_location(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    from app.s95.api_client import fetch_event_activities
    from app.sync.s95_global_sync_api import _protocol_freshness_map, event_numbers_by_date
    from app.sync.s95_protocol_lookup import _location_domain

    slug = row.target["slug"]
    platform = upsert.get_platform(db, PLATFORM_S95)
    location = _s95_location(db, platform, slug, domain=row.target.get("domain"))
    db.commit()
    domain = _location_domain(location)
    progress.step("fetch", f"Запрашиваем список стартов {location.name}")
    refs = fetch_event_activities(f"{domain}/events/{slug}.json")
    progress.step("compare", f"Сравниваем {len(refs)} стартов с базой")

    freshness = _protocol_freshness_map(db, platform, location)
    numbers = event_numbers_by_date(refs)
    plan: list[ProtocolPlanItem] = []
    unchanged = 0
    reasons: dict[str, int] = {}
    for ref in refs:
        try:
            event_date = date.fromisoformat(ref.date)
        except ValueError:
            continue
        known = freshness.get(f"{slug}:{ref.date}")
        reason: str | None
        if known is None:
            reason = "new"
        else:
            source_updated_at, last_fetched_at = known
            ref_updated_at = ref.updated_at_dt()
            if ref_updated_at is None:
                reason = None
            elif source_updated_at is not None:
                reason = "updated" if ref_updated_at > source_updated_at else None
            elif last_fetched_at is not None and ref_updated_at <= last_fetched_at:
                reason = None
            else:
                reason = "updated"
        if reason is None:
            unchanged += 1
            continue
        reasons[reason] = reasons.get(reason, 0) + 1
        plan.append(
            ProtocolPlanItem(
                slug=slug,
                event_date=event_date,
                event_number=numbers.get(ref.date),
                location_name=location.name,
                reason=reason,
                location=location,
                activity_ref=ref,
                activity_url=ref.url,
            )
        )
    plan.sort(key=lambda item: item.event_date, reverse=True)

    if not plan:
        progress.step("compared", f"Сравнили: расхождений нет, все {len(refs)} стартов свежие")
        outcomes: list[ProtocolOutcome] = []
        deferred: list[str] = []
    else:
        dates = ", ".join(item.event_date.strftime("%d.%m.%Y") for item in plan[:8])
        more = f" и ещё {len(plan) - 8}" if len(plan) > 8 else ""
        progress.step(
            "compared",
            f"Сравнили: нашли расхождения в {len(plan)} стартах — {dates}{more}",
            reasons={REASON_LABELS.get(k, k): v for k, v in reasons.items()},
        )
        outcomes, deferred = _run_protocols(db, progress, plan, _refetch_s95, platform_code=PLATFORM_S95)

    return {
        "label": location.name,
        "summaries_total": len(refs),
        "summaries_unchanged": unchanged,
        "summaries_diverged": len(plan),
        "reasons": reasons,
        **_protocols_result(outcomes, deferred),
    }


def _run_s95_profile(db: Session, row: AdminResyncRequest, progress: ResyncProgress) -> dict[str, Any]:
    from app.platform_adapters.s95 import parser as s95_parser
    from app.sync.s95_user_sync import _apply_s95_participant

    external_user_id = row.target["external_user_id"]
    domain = row.target.get("domain") or "s95.ru"
    platform = upsert.get_platform(db, PLATFORM_S95)
    progress.step("fetch", f"Запрашиваем профиль {row.target['canonical_url']}")
    db.commit()
    profile, runs, volunteering = s95_parser.fetch_athlete_activity(external_user_id, domain=domain)
    participant = _apply_s95_participant(db, platform, profile)
    db.commit()

    progress.step(
        "compare",
        f"{profile.display_name}: в профиле {len(runs)} пробежек и {len(volunteering)} волонтёрств — сравниваем с базой",
    )
    comparison = _compare_profile(db, platform, participant, runs, volunteering)
    _report_comparison(progress, comparison)
    outcomes, deferred = _run_protocols(db, progress, comparison.plan, _refetch_s95, platform_code=PLATFORM_S95)
    db_runs_after = _count_participant_runs(db, participant.id)
    return _profile_result(
        comparison,
        participant=participant,
        expected_runs=profile.total_runs,
        db_runs_after=db_runs_after,
        outcomes=outcomes,
        deferred=deferred,
    )


RUNNERS: dict[tuple[str, str], Callable[[Session, AdminResyncRequest, ResyncProgress], dict[str, Any]]] = {
    (PLATFORM_FIVE_VERST, KIND_PROFILE): _run_five_verst_profile,
    (PLATFORM_FIVE_VERST, KIND_PROTOCOL): _run_five_verst_protocol,
    (PLATFORM_FIVE_VERST, KIND_LOCATION): _run_five_verst_location,
    (PLATFORM_S95, KIND_PROFILE): _run_s95_profile,
    (PLATFORM_S95, KIND_PROTOCOL): _run_s95_protocol,
    (PLATFORM_S95, KIND_LOCATION): _run_s95_location,
}


# ===== Запуск заявки (из Celery-задачи) =====


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, FiveVerstBanDetected):
        return f"5 вёрст не пускает (охлаждение после отказа): {exc}. Повторите позже."
    if isinstance(exc, S95BanDetected):
        from app.s95.messages import s95_user_facing_error

        return s95_user_facing_error(exc)
    return str(exc)[:1000] or exc.__class__.__name__


def summarize_result(row: AdminResyncRequest) -> str:
    """Одна строка итога для шага «Готово» и для списка заявок."""
    result = row.result or {}
    if row.status == STATUS_FAILED:
        return row.error_message or "не удалось"
    if row.status != STATUS_DONE:
        return ""
    checked = int(result.get("protocols_checked") or 0)
    changed = int(result.get("protocols_changed") or 0)
    failed = int(result.get("protocols_failed") or 0)
    deferred = int(result.get("deferred_total") or 0)
    if row.kind == KIND_PROFILE:
        before = result.get("db_runs_before")
        after = result.get("db_runs_after")
        head = f"пробежек в базе: {before} → {after}"
    elif row.kind == KIND_LOCATION:
        head = f"стартов совпало: {result.get('summaries_unchanged', 0)} из {result.get('summaries_total', 0)}"
    else:
        head = result.get("label") or "протокол"
    if checked == 0:
        tail = "без изменений" if row.kind != KIND_PROTOCOL else "протокол не перекачан"
    elif changed == 0 and failed == 0:
        tail = f"перекачано {checked}, без изменений"
    else:
        tail = f"перекачано {checked}, изменилось {changed}"
        if failed:
            tail += f", с ошибкой {failed}"
    if deferred:
        tail += f", ещё {deferred} в обычной очереди"
    return f"{head}; {tail}"


def run_admin_resync(request_id: UUID) -> dict[str, Any]:
    """Тело Celery-задачи: выполнить заявку и записать итог."""
    from app.db.session import get_session_factory
    from app.services.scheduled_run_log_service import record_run

    db = get_session_factory()()
    started_at = _utcnow()
    payload: dict[str, Any] = {"errors": []}
    failed = False
    pipeline = "admin resync"
    try:
        row = db.get(AdminResyncRequest, request_id)
        if row is None:
            return {"status": "missing"}
        if row.status != STATUS_QUEUED:
            return {"status": row.status, "skipped": True}
        prefix = "5v" if row.platform_code == PLATFORM_FIVE_VERST else row.platform_code
        pipeline = f"{prefix} resync {row.kind}"
        row.status = STATUS_RUNNING
        row.started_at = started_at
        db.commit()
        progress = ResyncProgress(db, row.id)
        progress.step(
            "started",
            f"Взяли в работу: {PLATFORM_LABELS.get(row.platform_code, row.platform_code)}, {KIND_LABELS.get(row.kind, row.kind)}",
        )

        runner = RUNNERS.get((row.platform_code, row.kind))
        if runner is None:
            raise ValueError(f"нет исполнителя для {row.platform_code}/{row.kind}")
        try:
            result = runner(db, row, progress)
            db.commit()
            row = db.get(AdminResyncRequest, request_id)
            assert row is not None
            row.result = result
            flag_modified(row, "result")
            row.status = STATUS_DONE
            row.finished_at = _utcnow()
            _append_step(row, "done", f"Готово: {summarize_result(row)}")
            db.commit()
            payload = {
                "label": result.get("label"),
                "protocols_checked": result.get("protocols_checked", 0),
                "protocols_changed": result.get("protocols_changed", 0),
                "deferred": result.get("deferred_total", 0),
                "errors": [o["error"] for o in result.get("protocols", []) if o.get("error")],
            }
        except Exception as exc:
            db.rollback()
            failed = True
            message = _friendly_error(exc)
            logger.exception("admin resync %s failed", request_id)
            row = db.get(AdminResyncRequest, request_id)
            if row is not None:
                row.status = STATUS_FAILED
                row.error_message = message
                row.finished_at = _utcnow()
                _append_step(row, "failed", f"Не удалось: {message}")
                db.commit()
            payload = {"errors": [message]}
        return {"status": STATUS_FAILED if failed else STATUS_DONE, "request_id": str(request_id)}
    finally:
        db.close()
        try:
            record_run(pipeline, payload, started_at=started_at, finished_at=_utcnow(), failed=failed)
        except Exception:  # noqa: BLE001 — история запусков не должна ронять задачу
            logger.exception("admin resync: не удалось записать историю запуска")
