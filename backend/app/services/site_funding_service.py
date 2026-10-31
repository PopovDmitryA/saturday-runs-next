"""Донаты: расходы сайта по месяцам и переводы для блока «Поддержать проект».

Блок на /about (и строка на /backlog) показывает месяц: сколько стоил сайт,
куда ушли деньги и кто, когда и сколько перевёл; стрелками листаются прошлые
месяцы (решения Дмитрия 30–31.10.2026). Всё вносится руками в админке
/admin/donations — сайт переводов не видит.

Правила:
- месяц — по Москве; будущий месяц на сайте не показывается, в админке он —
  план расходов;
- у месяца без своих строк расходов — копия последнего заполненного месяца
  до него (и на сайте, и в админке, где её можно сохранить своей);
- анонимный перевод (или перевод без имени) наружу уходит без имени;
- переводы месяца — по дате, от первого к последнему;
- всё в рублях, излишек на следующий месяц не переносится.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import SiteCostItem, SiteCostMonth, SiteDonation, SiteFundingSettings

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

TITLE_MAX_LENGTH = 120
HINT_MAX_LENGTH = 400
DONOR_NAME_MAX_LENGTH = 80
# Потолок одной суммы — от опечатки «лишний ноль» в обе стороны не спасёт, но
# миллионный перевод на сайт без лишнего вопроса не попадёт.
AMOUNT_MAX_RUB = 10_000_000
# Насколько вперёд можно планировать расходы в админке.
PLAN_MONTHS_AHEAD = 12


class FundingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------- месяцы ----------


def today_msk(now: datetime | None = None) -> date:
    moment = now or datetime.now(tz=MOSCOW_TZ)
    return moment.astimezone(MOSCOW_TZ).date()


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(month: date, delta: int) -> date:
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def parse_month(value: str) -> date:
    """«2026-10» → 2026-10-01."""
    try:
        year_text, month_text = value.strip().split("-")
        parsed = date(int(year_text), int(month_text), 1)
    except (ValueError, AttributeError):
        raise FundingError("Месяц должен быть в виде ГГГГ-ММ, например 2026-10") from None
    if parsed.year < 2000:
        raise FundingError("Месяц должен быть в виде ГГГГ-ММ, например 2026-10")
    return parsed


def format_month(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _month_end(month: date) -> date:
    return add_months(month, 1)


# ---------- расходы ----------


@dataclass(frozen=True)
class CostLine:
    item_id: UUID
    title: str
    hint: str | None
    amount_rub: int


@dataclass(frozen=True)
class MonthCosts:
    lines: list[CostLine]
    # Месяц, чьи суммы показаны, если у этого месяца своих строк нет.
    copied_from: date | None

    @property
    def total_rub(self) -> int:
        return sum(line.amount_rub for line in self.lines)


def _saved_lines(db: Session, month: date) -> list[CostLine]:
    rows = db.execute(
        select(SiteCostItem, SiteCostMonth.amount_rub)
        .join(SiteCostMonth, SiteCostMonth.item_id == SiteCostItem.id)
        .where(SiteCostMonth.month == month)
        .order_by(SiteCostItem.sort_order, SiteCostItem.title)
    ).all()
    return [
        CostLine(item_id=item.id, title=item.title, hint=item.hint, amount_rub=int(amount))
        for item, amount in rows
    ]


def month_costs(db: Session, month: date) -> MonthCosts:
    """Расходы месяца: свои строки, а без них — копия последнего заполненного месяца до него."""
    lines = _saved_lines(db, month)
    if lines:
        return MonthCosts(lines=lines, copied_from=None)
    source = db.execute(
        select(func.max(SiteCostMonth.month)).where(SiteCostMonth.month < month)
    ).scalar_one_or_none()
    if source is None:
        return MonthCosts(lines=[], copied_from=None)
    return MonthCosts(lines=_saved_lines(db, source), copied_from=source)


def list_items(db: Session) -> list[SiteCostItem]:
    return list(
        db.execute(select(SiteCostItem).order_by(SiteCostItem.sort_order, SiteCostItem.title)).scalars().all()
    )


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        raise FundingError("Название статьи не может быть пустым")
    if len(cleaned) > TITLE_MAX_LENGTH:
        raise FundingError(f"Название статьи — не длиннее {TITLE_MAX_LENGTH} символов")
    return cleaned


def _clean_hint(hint: str | None) -> str | None:
    if hint is None:
        return None
    cleaned = " ".join(hint.split())
    if len(cleaned) > HINT_MAX_LENGTH:
        raise FundingError(f"Подсказка — не длиннее {HINT_MAX_LENGTH} символов")
    return cleaned or None


def _check_title_free(db: Session, title: str, *, except_id: UUID | None = None) -> None:
    query = select(SiteCostItem.id).where(func.lower(SiteCostItem.title) == title.lower())
    if except_id is not None:
        query = query.where(SiteCostItem.id != except_id)
    if db.execute(query).first() is not None:
        raise FundingError("Статья с таким названием уже есть — выберите её в «Из прежних статей»", status_code=409)


def create_item(db: Session, *, title: str, hint: str | None = None) -> SiteCostItem:
    cleaned = _clean_title(title)
    _check_title_free(db, cleaned)
    last_order = db.execute(select(func.max(SiteCostItem.sort_order))).scalar_one_or_none() or 0
    item = SiteCostItem(title=cleaned, hint=_clean_hint(hint), sort_order=int(last_order) + 10)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, item_id: UUID, *, title: str, hint: str | None) -> SiteCostItem:
    item = db.get(SiteCostItem, item_id)
    if item is None:
        raise FundingError("Статья не найдена", status_code=404)
    cleaned = _clean_title(title)
    _check_title_free(db, cleaned, except_id=item.id)
    item.title = cleaned
    item.hint = _clean_hint(hint)
    db.commit()
    db.refresh(item)
    return item


def _check_amount(amount_rub: int, *, allow_zero: bool) -> int:
    if amount_rub < 0 or (amount_rub == 0 and not allow_zero):
        raise FundingError("Сумма должна быть больше нуля" if not allow_zero else "Сумма не может быть меньше нуля")
    if amount_rub > AMOUNT_MAX_RUB:
        raise FundingError(f"Сумма — не больше {AMOUNT_MAX_RUB:,} ₽".replace(",", " "))
    return amount_rub


def check_plannable_month(month: date, *, today: date | None = None) -> None:
    limit = add_months(month_start(today or today_msk()), PLAN_MONTHS_AHEAD)
    if month > limit:
        raise FundingError(f"Расходы планируются не дальше чем на {PLAN_MONTHS_AHEAD} месяцев вперёд")


def save_month_costs(
    db: Session,
    month: date,
    amounts: list[tuple[UUID, int]],
    *,
    today: date | None = None,
) -> MonthCosts:
    """Записать расходы месяца целиком: статьи вне списка из месяца уходят.

    Пустой список убирает расходы месяца: учёт может начинаться позже
    (первый месяц на сайте — самый ранний с расходами). Месяц без своих строк
    снова читается копией заполненного месяца до него, если такой есть.
    """
    check_plannable_month(month, today=today)
    seen: set[UUID] = set()
    for item_id, amount in amounts:
        if item_id in seen:
            raise FundingError("Статья указана дважды")
        seen.add(item_id)
        _check_amount(amount, allow_zero=True)
    known = set(db.execute(select(SiteCostItem.id).where(SiteCostItem.id.in_(seen))).scalars().all())
    if known != seen:
        raise FundingError("Статья не найдена", status_code=404)
    db.execute(delete(SiteCostMonth).where(SiteCostMonth.month == month))
    db.add_all(SiteCostMonth(item_id=item_id, month=month, amount_rub=amount) for item_id, amount in amounts)
    db.commit()
    return month_costs(db, month)


# ---------- переводы ----------


def _clean_donor_name(name: str | None) -> str | None:
    if name is None:
        return None
    cleaned = " ".join(name.split())
    if len(cleaned) > DONOR_NAME_MAX_LENGTH:
        raise FundingError(f"Имя — не длиннее {DONOR_NAME_MAX_LENGTH} символов")
    return cleaned or None


def _check_received_on(received_on: date, *, today: date | None) -> None:
    if received_on > (today or today_msk()):
        raise FundingError("Дата перевода не может быть в будущем")


def month_donations(db: Session, month: date) -> list[SiteDonation]:
    return list(
        db.execute(
            select(SiteDonation)
            .where(SiteDonation.received_on >= month, SiteDonation.received_on < _month_end(month))
            .order_by(SiteDonation.received_on, SiteDonation.created_at, SiteDonation.id)
        )
        .scalars()
        .all()
    )


def create_donation(
    db: Session,
    *,
    received_on: date,
    amount_rub: int,
    donor_name: str | None,
    is_anonymous: bool,
    today: date | None = None,
) -> SiteDonation:
    _check_received_on(received_on, today=today)
    donation = SiteDonation(
        received_on=received_on,
        amount_rub=_check_amount(amount_rub, allow_zero=False),
        donor_name=_clean_donor_name(donor_name),
        is_anonymous=is_anonymous,
    )
    db.add(donation)
    db.commit()
    db.refresh(donation)
    return donation


def update_donation(
    db: Session,
    donation_id: UUID,
    *,
    received_on: date,
    amount_rub: int,
    donor_name: str | None,
    is_anonymous: bool,
    today: date | None = None,
) -> SiteDonation:
    donation = db.get(SiteDonation, donation_id)
    if donation is None:
        raise FundingError("Перевод не найден", status_code=404)
    _check_received_on(received_on, today=today)
    donation.received_on = received_on
    donation.amount_rub = _check_amount(amount_rub, allow_zero=False)
    donation.donor_name = _clean_donor_name(donor_name)
    donation.is_anonymous = is_anonymous
    db.commit()
    db.refresh(donation)
    return donation


def delete_donation(db: Session, donation_id: UUID) -> None:
    donation = db.get(SiteDonation, donation_id)
    if donation is None:
        raise FundingError("Перевод не найден", status_code=404)
    db.delete(donation)
    db.commit()


def known_donor_names(db: Session) -> list[tuple[str, int]]:
    """Имена прежних переводов для подсказки в поле «Кто»: частые — первыми."""
    count = func.count(SiteDonation.id)
    rows = db.execute(
        select(SiteDonation.donor_name, count)
        .where(SiteDonation.donor_name.isnot(None))
        .group_by(SiteDonation.donor_name)
        .order_by(count.desc(), SiteDonation.donor_name)
    ).all()
    return [(str(name), int(n)) for name, n in rows]


def public_donor_name(donation: SiteDonation) -> str | None:
    """Имя для сайта: у анонимного перевода и перевода без имени — None («Аноним»)."""
    if donation.is_anonymous:
        return None
    return donation.donor_name


# ---------- настройки и публичный месяц ----------


def get_settings_row(db: Session) -> SiteFundingSettings:
    row = db.get(SiteFundingSettings, 1)
    if row is None:
        row = SiteFundingSettings(id=1, show_public=False)
        db.add(row)
        db.flush()
    return row


def set_show_public(db: Session, show_public: bool, *, today: date | None = None) -> SiteFundingSettings:
    row = get_settings_row(db)
    if show_public and not month_costs(db, month_start(today or today_msk())).lines:
        raise FundingError("Сначала внесите расходы текущего месяца", status_code=409)
    row.show_public = show_public
    db.commit()
    db.refresh(row)
    return row


def first_month(db: Session, *, today: date | None = None) -> date | None:
    """Первый месяц, который можно открыть на сайте: самый ранний с расходами.

    Переводы раньше него на сайте не видны (только в админке): месяц без
    расходов показал бы «поддержали 500 ₽ из 0 ₽».
    """
    current = month_start(today or today_msk())
    return db.execute(
        select(func.min(SiteCostMonth.month)).where(SiteCostMonth.month <= current)
    ).scalar_one_or_none()


@dataclass(frozen=True)
class PublicDonation:
    received_on: date
    name: str | None
    amount_rub: int


@dataclass(frozen=True)
class PublicMonth:
    month: date
    current_month: date
    first_month: date
    last_month: date
    costs: MonthCosts
    donations: list[PublicDonation]

    @property
    def donated_total_rub(self) -> int:
        return sum(donation.amount_rub for donation in self.donations)


def last_month(db: Session, *, today: date | None = None) -> date:
    """Последний месяц, который можно открыть на сайте: текущий или самый
    поздний будущий со своим планом расходов (в пределах горизонта плана).

    Будущий месяц открывается, чтобы был виден план; переводы в нём не
    бывают — они датируются днём поступления, то есть идут в текущий месяц.
    """
    current = month_start(today or today_msk())
    planned = db.execute(
        select(func.max(SiteCostMonth.month)).where(
            SiteCostMonth.month > current,
            SiteCostMonth.month <= add_months(current, PLAN_MONTHS_AHEAD),
        )
    ).scalar_one_or_none()
    return planned or current


def is_public_visible(db: Session, *, today: date | None = None) -> bool:
    if not get_settings_row(db).show_public:
        return False
    return bool(month_costs(db, month_start(today or today_msk())).lines)


def public_month(db: Session, month: date | None = None, *, today: date | None = None) -> PublicMonth | None:
    """Месяц для сайта. None — блок выключен; FundingError 404 — месяца нет на сайте."""
    if not is_public_visible(db, today=today):
        return None
    current = month_start(today or today_msk())
    target = month or current
    earliest = first_month(db, today=today) or current
    latest = last_month(db, today=today)
    if target > latest or target < earliest:
        raise FundingError("Нет данных за этот месяц", status_code=404)
    return PublicMonth(
        month=target,
        current_month=current,
        first_month=earliest,
        last_month=latest,
        costs=month_costs(db, target),
        donations=[
            PublicDonation(
                received_on=donation.received_on,
                name=public_donor_name(donation),
                amount_rub=donation.amount_rub,
            )
            for donation in month_donations(db, target)
        ],
    )
