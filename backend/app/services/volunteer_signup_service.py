"""Заявки на волонтёрство: участник → организатор → NRMS.

Схема (решение Дмитрия 04.09.2026):
1. Участник с привязанным профилем 5 вёрст выбирает на странице локации дату и
   роль. Даты и роли берём из открытой записи на 5verst.ru (organizer_roster_
   service): человек видит реальные вакансии, а не абстрактный справочник.
2. Заявка уходит организаторам локации в Telegram (тем, кто писал боту).
3. Организатор в кабинете подтверждает или отклоняет. При подтверждении сайт
   вносит человека в NRMS под сессией организатора (nrms_client); если сессии
   нет или запись не удалась, заявка всё равно подтверждена, а nrms_status
   говорит, что осталось сделать руками.
4. Открытая запись 5verst.ru — обратная связь: если имя участника появилось в
   клетке роли, заявка помечается «в строю» независимо от того, кто её внёс.

Заявка возможна только на локацию с половиной 5 вёрст (у остальных систем
нет ни NRMS, ни открытой записи) и только на будущие даты.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Event,
    LocationOrganizerAccess,
    NrmsRosterSnapshot,
    Participant,
    Platform,
    PlatformLink,
    User,
    VolunteerResult,
    VolunteerSignupRequest,
)
from app.services import nrms_client
from app.services.location_page_service import LocationIdentity
from app.services.organizer_access_service import ORGANIZER_ROLE_KEY
from app.services.organizer_roster_service import fetch_volunteer_roster, roster_url
from app.services.user_telegram_notify import send_user_telegram_message
from app.volunteer_role_taxonomy import canonical_volunteer_role

logger = logging.getLogger(__name__)

FIVE_VERST_CODE = "five_verst"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_DECLINED = "declined"
STATUS_CANCELLED = "cancelled"
OPEN_STATUSES = (STATUS_PENDING, STATUS_CONFIRMED)

NRMS_NONE = "none"
NRMS_SAVED = "saved"
NRMS_FAILED = "failed"
NRMS_MANUAL = "manual"

# Насколько вперёд можно записаться. Открытая запись 5verst.ru показывает
# ближайшие даты; дальше этого горизонта состав никто не собирает.
SIGNUP_HORIZON_DAYS = 70
# Сколько суббот предложить, если страница записи недоступна.
FALLBACK_SATURDAYS = 4
MAX_COMMENT_LENGTH = 500
MAX_ROLE_LENGTH = 128

# Справочник ролей NRMS (volunteer/role/list, снят 04.09.2026). Используется
# как запасной список, когда страница записи локации не отвечает: первые —
# роли с is_default, которые NRMS показывает в таблице по умолчанию.
NRMS_DEFAULT_ROLE_NAMES: tuple[str, ...] = (
    "Организатор",
    "Секундомер",
    "Фотограф",
    "Маршал",
    "Подготовка мероприятия",
    "Сканирование штрих-кодов",
    "Раздача карточек позиций",
    "Замыкающий",
)
NRMS_EXTRA_ROLE_NAMES: tuple[str, ...] = (
    "Сортировка карточек",
    "Обработка результатов",
    "Связи с общественностью",
    "Хранение и доставка оборудования",
    "Разное",
    "Завершение мероприятия",
    "Проверка карточек позиций",
    "Инструктаж новых участников",
    "Организация финиша",
    "Ведущий велосипед",
    "Помощь в раздаче карточек позиций",
    "Координация волонтёров",
    "Составление отчёта",
    "Пейсмейкер",
    "Лидер для слабовидящих",
    "Проведение разминки",
    "Сурдопереводчик",
    "Координатор парковки",
    "Проверка трассы",
    "Разметка трассы",
    "Ведущий мероприятия",
    "Проведение предстартового брифинга",
    "Видеограф",
)

_ROSTER_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


class SignupError(Exception):
    """Ошибка, текст которой показывается пользователю как есть."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class LinkedFiveVerst:
    verst_id: str
    participant_name: str | None


# ---------------------------------------------------------------------------
# Вспомогательное


def five_verst_slug_of(identity: LocationIdentity) -> str | None:
    for location, code in identity.locations:
        if code == FIVE_VERST_CODE:
            return location.external_key.strip().lower()
    return None


def parse_roster_date(label: str) -> date | None:
    match = _ROSTER_DATE_RE.match(label.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def format_date_ru(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def upcoming_saturdays(today: date, count: int) -> list[date]:
    days_ahead = (5 - today.weekday()) % 7
    first = today + timedelta(days=days_ahead)
    return [first + timedelta(weeks=i) for i in range(count)]


def linked_five_verst(db: Session, user_id: UUID) -> LinkedFiveVerst | None:
    row = (
        db.query(PlatformLink.external_user_id, Participant.display_name)
        .join(Platform, PlatformLink.platform_id == Platform.id)
        .outerjoin(
            Participant,
            and_(
                Participant.platform_id == PlatformLink.platform_id,
                Participant.external_user_id == PlatformLink.external_user_id,
            ),
        )
        .filter(PlatformLink.user_id == user_id, Platform.code == FIVE_VERST_CODE)
        .first()
    )
    if row is None:
        return None
    external_id, display_name = row
    return LinkedFiveVerst(verst_id=str(external_id).strip(), participant_name=display_name)


def _name_tokens(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(token.lower().replace("ё", "е") for token in re.split(r"[\s,]+", value) if token)


def names_match(a: str | None, b: str | None) -> bool:
    """«Дмитрий ПОПОВ» и «Попов Дмитрий» — один человек: сравниваем наборы слов."""
    tokens_a, tokens_b = _name_tokens(a), _name_tokens(b)
    return bool(tokens_a) and tokens_a == tokens_b


def list_identity_organizer_users(db: Session, identity: LocationIdentity) -> list[User]:
    """Кому уходит заявка: ручные гранты + волонтёрившие организатором здесь.

    Зеркало organizer_access_service.derive_organizer_identity_keys, но в
    обратную сторону (локация → люди). Роли канонизируем в Python — у parkrun
    к роли приклеен счётчик, сырое сравнение его не поймает.
    """
    location_ids = [location.id for location, _ in identity.locations]
    users: dict[UUID, User] = {}

    manual_rows = (
        db.query(User)
        .join(LocationOrganizerAccess, LocationOrganizerAccess.user_id == User.id)
        .filter(LocationOrganizerAccess.location_key == identity.identity_key)
        .all()
    )
    for user in manual_rows:
        users[user.id] = user

    if location_ids:
        rows = (
            db.query(VolunteerResult.role, User)
            .join(Event, VolunteerResult.event_id == Event.id)
            .join(Participant, VolunteerResult.participant_id == Participant.id)
            .join(
                PlatformLink,
                and_(
                    PlatformLink.platform_id == Participant.platform_id,
                    PlatformLink.external_user_id == Participant.external_user_id,
                ),
            )
            .join(User, PlatformLink.user_id == User.id)
            .filter(
                Event.location_id.in_(location_ids),
                Event.is_test_event.is_(False),
                VolunteerResult.role.isnot(None),
            )
            .distinct()
            .all()
        )
        for role, user in rows:
            canonical = canonical_volunteer_role(role)
            if canonical is not None and canonical.key == ORGANIZER_ROLE_KEY:
                users[user.id] = user
    return list(users.values())


# ---------------------------------------------------------------------------
# Сторона участника


def _roster_or_none(five_verst_slug: str | None) -> dict[str, Any] | None:
    if not five_verst_slug:
        return None
    try:
        return fetch_volunteer_roster(five_verst_slug)
    except Exception:  # noqa: BLE001 — страница записи не должна ронять заявку
        logger.exception("volunteer signup: roster fetch crashed for %s", five_verst_slug)
        return None


def load_roster_snapshots(db: Session, five_verst_slug: str, since: date) -> dict[date, NrmsRosterSnapshot]:
    rows = (
        db.query(NrmsRosterSnapshot)
        .filter(
            NrmsRosterSnapshot.five_verst_slug == five_verst_slug,
            NrmsRosterSnapshot.event_date >= since,
        )
        .all()
    )
    return {row.event_date: row for row in rows}


def store_roster_snapshot(
    db: Session,
    *,
    five_verst_slug: str,
    event_date: date,
    roster: nrms_client.NrmsRoster,
    nrms_event_id: int | None,
    user_id: UUID | None,
) -> None:
    """Снимок состава NRMS ложится на сайт тем же проходом, что чтение/запись."""
    entries = [
        {"verst_id": e.verst_id, "role_id": e.role_id, "role_name": e.role_name, "full_name": e.full_name}
        for e in roster.entries
    ]
    row = (
        db.query(NrmsRosterSnapshot)
        .filter(
            NrmsRosterSnapshot.five_verst_slug == five_verst_slug,
            NrmsRosterSnapshot.event_date == event_date,
        )
        .one_or_none()
    )
    if row is None:
        row = NrmsRosterSnapshot(five_verst_slug=five_verst_slug, event_date=event_date, entries=entries)
        db.add(row)
    row.entries = entries
    row.nrms_event_id = nrms_event_id
    row.status_id = roster.status_id
    row.upload_status_id = roster.upload_status_id
    row.fetched_at = datetime.now(UTC)
    row.fetched_by_user_id = user_id
    db.flush()


def _apply_snapshots(roster: dict[str, Any] | None, snapshots: dict[date, NrmsRosterSnapshot]) -> dict[str, Any] | None:
    """Занятость на даты со снимком NRMS берём из снимка — он свежее открытой записи.

    Структура ролей (число мест) остаётся от открытой записи 5verst.ru; если
    у роли из снимка строк не хватает — добавляем. Без открытой записи снимки
    дают и даты, и роли.
    """
    if not snapshots:
        return roster
    result: dict[str, Any] = {
        "source_url": (roster or {}).get("source_url"),
        "dates": list((roster or {}).get("dates") or []),
        "roles": [
            {"role": r["role"], "filled": dict(r.get("filled") or {})} for r in (roster or {}).get("roles") or []
        ],
        "snapshot_dates": [],
    }
    for event_date, snapshot in sorted(snapshots.items()):
        label = format_date_ru(event_date)
        if label not in result["dates"]:
            result["dates"].append(label)
        result["snapshot_dates"].append(label)
        for row in result["roles"]:
            row["filled"].pop(label, None)
        for entry in snapshot.entries or []:
            role_name = str(entry.get("role_name") or "").strip()
            full_name = str(entry.get("full_name") or "").strip()
            if not role_name:
                continue
            target = next(
                (
                    row
                    for row in result["roles"]
                    if row["role"].strip().lower() == role_name.lower() and label not in row["filled"]
                ),
                None,
            )
            if target is None:
                target = {"role": role_name, "filled": {}}
                result["roles"].append(target)
            target["filled"][label] = full_name
    result["dates"].sort(key=lambda value: parse_roster_date(value) or date.max)
    return result


def _effective_roster(db: Session, five_verst_slug: str | None) -> dict[str, Any] | None:
    if not five_verst_slug:
        return None
    roster = _roster_or_none(five_verst_slug)
    snapshots = load_roster_snapshots(db, five_verst_slug, _today())
    return _apply_snapshots(roster, snapshots)


def _date_options(roster: dict[str, Any] | None, today: date) -> list[date]:
    dates: list[date] = []
    if roster:
        for label in roster.get("dates") or []:
            parsed = parse_roster_date(str(label))
            if parsed is not None and parsed >= today:
                dates.append(parsed)
    if not dates:
        dates = upcoming_saturdays(today, FALLBACK_SATURDAYS)
    return sorted(set(dates))


def _role_options(roster: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Роли из открытой записи, схлопнутые по имени.

    На 5verst.ru одна роль бывает несколькими строками — по числу мест
    («Фотограф» ×3, «Секундомер» ×2). Для выбора это одна позиция: считаем
    места, а занятых на дату перечисляем через запятую.
    """
    if roster and roster.get("roles"):
        merged: dict[str, dict[str, Any]] = {}
        for row in roster["roles"]:
            name = str(row.get("role") or "").strip()
            if not name:
                continue
            option = merged.setdefault(name, {"name": name, "slots": 0, "filled": {}})
            option["slots"] += 1
            for date_label, who in (row.get("filled") or {}).items():
                if not who:
                    continue
                current = option["filled"].get(date_label)
                option["filled"][date_label] = f"{current}, {who}" if current else who
        if merged:
            return list(merged.values())
    return [{"name": name, "slots": 1, "filled": {}} for name in NRMS_DEFAULT_ROLE_NAMES + NRMS_EXTRA_ROLE_NAMES]


def _fallback_message(identity: LocationIdentity, linked: LinkedFiveVerst | None) -> str:
    who = linked.participant_name if linked and linked.participant_name else "Я"
    verst = f" (ID {linked.verst_id})" if linked else ""
    return f"{who}{verst} хочу волонтёрить на {identity.name}. Подскажите, на какую дату и роль записать?"


def build_signup_options(db: Session, identity: LocationIdentity, user: User) -> dict[str, Any]:
    today = _today()
    five_verst_slug = five_verst_slug_of(identity)
    linked = linked_five_verst(db, user.id)
    roster = _effective_roster(db, five_verst_slug)
    organizers = list_identity_organizer_users(db, identity) if five_verst_slug else []
    my_requests = (
        db.query(VolunteerSignupRequest)
        .filter(
            VolunteerSignupRequest.user_id == user.id,
            VolunteerSignupRequest.location_key == identity.identity_key,
            VolunteerSignupRequest.event_date >= today - timedelta(days=30),
        )
        .order_by(VolunteerSignupRequest.event_date.asc(), VolunteerSignupRequest.created_at.asc())
        .all()
    )
    dates = _date_options(roster, today)
    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "supported": five_verst_slug is not None,
        "five_verst_slug": five_verst_slug,
        "roster_url": roster_url(five_verst_slug) if five_verst_slug else None,
        "roster_available": roster is not None,
        "organizer_connected": bool(organizers),
        "linked": linked is not None,
        "verst_id": linked.verst_id if linked else None,
        "participant_name": linked.participant_name if linked else None,
        "dates": [{"date": d, "date_display": format_date_ru(d)} for d in dates],
        "roles": _role_options(roster),
        "my_requests": [serialize_request(item, roster=roster) for item in my_requests],
        "fallback_message": _fallback_message(identity, linked),
    }


def create_signup_request(
    db: Session,
    identity: LocationIdentity,
    user: User,
    *,
    event_date: date,
    role_name: str,
    comment: str | None,
) -> VolunteerSignupRequest:
    today = _today()
    five_verst_slug = five_verst_slug_of(identity)
    if not five_verst_slug:
        raise SignupError("Запись через сайт работает только для локаций 5 вёрст")
    linked = linked_five_verst(db, user.id)
    if linked is None:
        raise SignupError("Привяжите профиль 5 вёрст в настройках — организатору нужен ваш ID")
    if event_date < today:
        raise SignupError("Эта дата уже прошла")
    if event_date > today + timedelta(days=SIGNUP_HORIZON_DAYS):
        raise SignupError("Так далеко вперёд запись ещё не открыта")
    role_name = " ".join(role_name.split())
    if not role_name or len(role_name) > MAX_ROLE_LENGTH:
        raise SignupError("Выберите роль")
    comment = (comment or "").strip() or None
    if comment and len(comment) > MAX_COMMENT_LENGTH:
        raise SignupError("Комментарий слишком длинный")
    organizers = list_identity_organizer_users(db, identity)
    if not organizers:
        raise SignupError(
            "Организатор этой локации ещё не заходил на сайт — напишите ему в чат локации",
            status_code=409,
        )
    duplicate = (
        db.query(VolunteerSignupRequest)
        .filter(
            VolunteerSignupRequest.user_id == user.id,
            VolunteerSignupRequest.location_key == identity.identity_key,
            VolunteerSignupRequest.event_date == event_date,
            VolunteerSignupRequest.role_name == role_name,
            VolunteerSignupRequest.status.in_(OPEN_STATUSES),
        )
        .first()
    )
    if duplicate is not None:
        raise SignupError("Такая заявка уже есть", status_code=409)

    request = VolunteerSignupRequest(
        user_id=user.id,
        location_key=identity.identity_key,
        five_verst_slug=five_verst_slug,
        event_date=event_date,
        role_name=role_name,
        verst_id=linked.verst_id,
        participant_name=linked.participant_name,
        comment=comment,
        status=STATUS_PENDING,
        nrms_status=NRMS_NONE,
    )
    db.add(request)
    db.flush()

    if _notify_organizers(identity, organizers, request):
        request.organizer_notified_at = datetime.now(UTC)
    db.commit()
    return request


def cancel_signup_request(db: Session, user: User, request_id: UUID) -> VolunteerSignupRequest:
    request = (
        db.query(VolunteerSignupRequest)
        .filter(VolunteerSignupRequest.id == request_id, VolunteerSignupRequest.user_id == user.id)
        .one_or_none()
    )
    if request is None:
        raise SignupError("Заявка не найдена", status_code=404)
    if request.status != STATUS_PENDING:
        raise SignupError("Отозвать можно только заявку, по которой ещё нет решения", status_code=409)
    request.status = STATUS_CANCELLED
    request.decided_at = datetime.now(UTC)
    db.commit()
    return request


def _notify_organizers(identity: LocationIdentity, organizers: list[User], request: VolunteerSignupRequest) -> bool:
    base_url = get_settings().app_base_url.rstrip("/")
    lines = [
        f"🙋 Заявка на волонтёрство — {identity.name}",
        f"{request.participant_name or 'Участник'} (ID {request.verst_id})",
        f"{format_date_ru(request.event_date)}, роль: {request.role_name}",
    ]
    if request.comment:
        lines.append(f"Комментарий: {request.comment}")
    lines.append("")
    lines.append(f"Подтвердить или отклонить: {base_url}/organizer/{identity.slug}/signup-requests")
    text = "\n".join(lines)
    delivered = False
    for organizer in organizers:
        if send_user_telegram_message(organizer.telegram_chat_id, text):
            delivered = True
    return delivered


# ---------------------------------------------------------------------------
# Сторона организатора


def list_location_requests(db: Session, identity: LocationIdentity, *, history_days: int = 60) -> dict[str, Any]:
    today = _today()
    five_verst_slug = five_verst_slug_of(identity)
    roster = _effective_roster(db, five_verst_slug)
    rows = (
        db.query(VolunteerSignupRequest)
        .filter(
            VolunteerSignupRequest.location_key == identity.identity_key,
            or_(
                VolunteerSignupRequest.status == STATUS_PENDING,
                VolunteerSignupRequest.event_date >= today - timedelta(days=history_days),
            ),
        )
        .order_by(VolunteerSignupRequest.event_date.asc(), VolunteerSignupRequest.created_at.asc())
        .all()
    )
    items = [serialize_request(item, roster=roster) for item in rows]
    _mark_manual_entries(db, rows, roster)
    return {
        "location": {"slug": identity.slug, "name": identity.name},
        "supported": five_verst_slug is not None,
        "five_verst_slug": five_verst_slug,
        "roster_url": roster_url(five_verst_slug) if five_verst_slug else None,
        "roster_available": roster is not None,
        "pending_count": sum(1 for item in rows if item.status == STATUS_PENDING),
        "items": items,
    }


def count_pending_requests(db: Session, identity_key: str) -> int:
    return (
        db.query(VolunteerSignupRequest)
        .filter(
            VolunteerSignupRequest.location_key == identity_key,
            VolunteerSignupRequest.status == STATUS_PENDING,
        )
        .count()
    )


def _mark_manual_entries(db: Session, rows: list[VolunteerSignupRequest], roster: dict[str, Any] | None) -> None:
    """Подтверждённая заявка, чьё имя нашлось в открытой записи, — внесена руками."""
    if not roster:
        return
    changed = False
    for item in rows:
        if item.status != STATUS_CONFIRMED or item.nrms_status in (NRMS_SAVED, NRMS_MANUAL):
            continue
        if in_open_roster(item, roster):
            item.nrms_status = NRMS_MANUAL
            item.nrms_saved_at = datetime.now(UTC)
            changed = True
    if changed:
        db.commit()


def in_open_roster(request: VolunteerSignupRequest, roster: dict[str, Any] | None) -> bool | None:
    """None — записи нет или дата за её горизонтом; иначе есть ли имя в клетке."""
    if not roster:
        return None
    label = format_date_ru(request.event_date)
    if label not in (roster.get("dates") or []):
        return None
    # Одна роль — несколько строк (мест): имя может стоять в любой из них.
    for row in roster.get("roles") or []:
        if str(row.get("role") or "").strip().lower() != request.role_name.strip().lower():
            continue
        if names_match(row.get("filled", {}).get(label), request.participant_name):
            return True
    return False


@dataclass
class Decision:
    request_id: UUID
    # confirmed | declined
    decision: str
    note: str | None = None


def decide_signup_request(
    db: Session,
    identity: LocationIdentity,
    organizer: User,
    request_id: UUID,
    *,
    decision: str,
    note: str | None,
) -> VolunteerSignupRequest:
    """Одна заявка — частный случай пакета."""
    results = decide_signup_requests(
        db, identity, organizer, [Decision(request_id=request_id, decision=decision, note=note)]
    )
    return results[0]


def decide_signup_requests(
    db: Session,
    identity: LocationIdentity,
    organizer: User,
    decisions: list[Decision],
) -> list[VolunteerSignupRequest]:
    """Пакет решений одним проходом (просьба Дмитрия 04.09.2026).

    Подтверждённые заявки группируются по дате: на каждую дату — одно чтение
    состава NRMS, один save с полным списком, одно контрольное чтение и один
    снимок на сайт. Отклонения не ходят в NRMS. Ошибки NRMS не валят пакет:
    заявка остаётся подтверждённой с nrms_status failed и текстом причины.
    """
    if not decisions:
        raise SignupError("Нет решений")
    ids = [item.request_id for item in decisions]
    if len(set(ids)) != len(ids):
        raise SignupError("Одна заявка указана дважды")
    by_id = {item.request_id: item for item in decisions}
    rows = (
        db.query(VolunteerSignupRequest)
        .filter(
            VolunteerSignupRequest.id.in_(ids),
            VolunteerSignupRequest.location_key == identity.identity_key,
        )
        .all()
    )
    found = {row.id: row for row in rows}
    for request_id in ids:
        if request_id not in found:
            raise SignupError("Заявка не найдена", status_code=404)
        if found[request_id].status != STATUS_PENDING:
            raise SignupError("По одной из заявок решение уже принято", status_code=409)
    for item in decisions:
        if item.decision not in (STATUS_CONFIRMED, STATUS_DECLINED):
            raise SignupError("Неизвестное решение")
        item.note = (item.note or "").strip() or None
        if item.note and len(item.note) > MAX_COMMENT_LENGTH:
            raise SignupError("Комментарий слишком длинный")

    now = datetime.now(UTC)
    ordered = [found[request_id] for request_id in ids]
    for request in ordered:
        item = by_id[request.id]
        request.status = item.decision
        request.decided_at = now
        request.decided_by_user_id = organizer.id
        request.decision_note = item.note

    confirmed = [r for r in ordered if r.status == STATUS_CONFIRMED]
    by_date: dict[date, list[VolunteerSignupRequest]] = {}
    for request in confirmed:
        by_date.setdefault(request.event_date, []).append(request)
    for event_date, batch in sorted(by_date.items()):
        _push_batch_to_nrms(db, organizer, event_date, batch)
    db.commit()
    for request in ordered:
        _notify_participant(db, identity, request)
    return ordered


def _fail(batch: list[VolunteerSignupRequest], message: str) -> None:
    for request in batch:
        request.nrms_status = NRMS_FAILED
        request.nrms_error = message


def _push_batch_to_nrms(db: Session, organizer: User, event_date: date, batch: list[VolunteerSignupRequest]) -> None:
    """Все подтверждённые заявки одной даты — одним save в NRMS.

    Цепочка по HAR: локации организатора → роли → участники по ID → текущий
    состав (сторожа: дата не в прошлом, статус выгрузки не Final) → save с
    полным списком → контрольное чтение → снимок на сайт.
    """
    slug = batch[0].five_verst_slug
    session = nrms_client.load_session(organizer.id)
    if session is None:
        for request in batch:
            request.nrms_status = NRMS_NONE
            request.nrms_error = "Нет сессии NRMS — войдите в NRMS в кабинете и внесите человека"
        return
    if event_date < _today():
        _fail(batch, "Дата уже прошла — в прошлое сайт не пишет")
        return
    try:
        events = nrms_client.list_events(session)
        event = next((e for e in events if e.url.strip().lower() == slug), None)
        if event is None:
            _fail(
                batch,
                f"В вашем NRMS нет локации с адресом «{slug}»: доступны "
                f"{', '.join(e.name for e in events) or 'ничего'}",
            )
            return
        roles = nrms_client.list_roles(session)
        roster = nrms_client.list_event_volunteers(session, event.id, event_date)
        store_roster_snapshot(
            db,
            five_verst_slug=slug,
            event_date=event_date,
            roster=roster,
            nrms_event_id=event.id,
            user_id=organizer.id,
        )
        if roster.upload_status_id == nrms_client.UPLOAD_STATUS_FINAL:
            _fail(batch, "Состав этой даты уже загружен на сайт — он закрыт")
            return
        if roster.status_id == nrms_client.EVENT_STATUS_CANCEL:
            _fail(batch, "Старт на эту дату отменён в NRMS — запись закрыта")
            return
        if roster.status_id == nrms_client.EVENT_STATUS_PAUSE:
            _fail(batch, "Локация на паузе в NRMS — запись закрыта")
            return

        present = {(e.verst_id, e.role_id) for e in roster.entries}
        volunteers = [(e.verst_id, e.role_id) for e in roster.entries]
        to_add: list[tuple[VolunteerSignupRequest, int, int]] = []
        for request in batch:
            role = next((r for r in roles if r.name.strip().lower() == request.role_name.lower()), None)
            if role is None:
                request.nrms_status = NRMS_FAILED
                request.nrms_error = f"В справочнике NRMS нет роли «{request.role_name}»"
                continue
            try:
                verst_id = int(request.verst_id)
            except (TypeError, ValueError):
                request.nrms_status = NRMS_FAILED
                request.nrms_error = f"Некорректный ID участника: {request.verst_id}"
                continue
            athlete = nrms_client.find_athlete_by_id(session, verst_id)
            if athlete is None:
                request.nrms_status = NRMS_FAILED
                request.nrms_error = f"NRMS не нашёл участника с ID {request.verst_id}"
                continue
            if (athlete.id, role.id) in present:
                request.nrms_status = NRMS_SAVED
                request.nrms_error = None
                request.nrms_saved_at = datetime.now(UTC)
                continue
            present.add((athlete.id, role.id))
            volunteers.append((athlete.id, role.id))
            to_add.append((request, athlete.id, role.id))
        if not to_add:
            return

        nrms_client.save_event_volunteers(
            session,
            event_id=event.id,
            event_date=event_date,
            upload_status_id=roster.upload_status_id or nrms_client.UPLOAD_STATUS_DRAFT,
            volunteers=volunteers,
        )
        after = nrms_client.list_event_volunteers(session, event.id, event_date)
        store_roster_snapshot(
            db,
            five_verst_slug=slug,
            event_date=event_date,
            roster=after,
            nrms_event_id=event.id,
            user_id=organizer.id,
        )
        got = {(e.verst_id, e.role_id) for e in after.entries}
        lost = [pair for pair in volunteers if pair not in got]
        for request, verst_id, role_id in to_add:
            if (verst_id, role_id) in got and not lost:
                request.nrms_status = NRMS_SAVED
                request.nrms_error = None
                request.nrms_saved_at = datetime.now(UTC)
            else:
                request.nrms_status = NRMS_FAILED
                request.nrms_error = (
                    f"NRMS принял запись, но в составе не хватает {len(lost)} чел. — проверьте состав даты в NRMS"
                )
    except nrms_client.NrmsAuthError as exc:
        nrms_client.drop_session(organizer.id)
        _fail([r for r in batch if r.nrms_status == NRMS_NONE], str(exc))
    except nrms_client.NrmsError as exc:
        _fail([r for r in batch if r.nrms_status == NRMS_NONE], str(exc))


def _notify_participant(db: Session, identity: LocationIdentity, request: VolunteerSignupRequest) -> None:
    user = db.get(User, request.user_id)
    if user is None or not user.telegram_chat_id:
        return
    when = f"{identity.name}, {format_date_ru(request.event_date)}, {request.role_name}"
    if request.status == STATUS_CONFIRMED:
        if request.nrms_status == NRMS_SAVED:
            text = f"✅ Заявка на волонтёрство подтверждена: {when}. Вы в записи, до встречи на старте!"
        else:
            text = f"✅ Организатор принял заявку на волонтёрство: {when}. В запись вас внесут вручную."
    else:
        text = f"Организатор не смог принять заявку на волонтёрство: {when}."
    if request.decision_note:
        text += f"\nКомментарий организатора: {request.decision_note}"
    send_user_telegram_message(user.telegram_chat_id, text)


# ---------------------------------------------------------------------------
# NRMS-сессия организатора


def nrms_session_state(organizer: User) -> dict[str, Any]:
    session = nrms_client.load_session(organizer.id)
    if session is None:
        return {"connected": False, "username": None, "expires_at": None}
    return {"connected": True, "username": session.username, "expires_at": session.expires_at}


def nrms_login(organizer: User, username: str, password: str) -> dict[str, Any]:
    session = nrms_client.login(username, password)
    nrms_client.store_session(organizer.id, session)
    try:
        events = nrms_client.list_events(session)
    except nrms_client.NrmsError:
        events = []
    return {
        "connected": True,
        "username": session.username,
        "expires_at": session.expires_at,
        "events": [{"id": e.id, "name": e.name, "url": e.url} for e in events],
    }


def nrms_logout(organizer: User) -> None:
    nrms_client.drop_session(organizer.id)


# ---------------------------------------------------------------------------
# Сериализация


def serialize_request(item: VolunteerSignupRequest, *, roster: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": item.id,
        "event_date": item.event_date,
        "event_date_display": format_date_ru(item.event_date),
        "role_name": item.role_name,
        "verst_id": item.verst_id,
        "participant_name": item.participant_name,
        "comment": item.comment,
        "status": item.status,
        "created_at": item.created_at,
        "decided_at": item.decided_at,
        "decision_note": item.decision_note,
        "nrms_status": item.nrms_status,
        "nrms_error": item.nrms_error,
        "organizer_notified": item.organizer_notified_at is not None,
        "in_open_roster": in_open_roster(item, roster),
    }


def _today() -> date:
    # Даты стартов — московские; сервер живёт в UTC, ночью с пятницы на субботу
    # UTC-«сегодня» ещё пятница. Берём московский календарный день.
    return (datetime.now(UTC) + timedelta(hours=3)).date()
