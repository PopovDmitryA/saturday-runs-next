"""Донаты: расходы по месяцам, переводы и публичный месяц блока «Поддержать проект».

Даты — 2001 год: заведомо раньше настоящих записей, поэтому первый месяц
учёта и копия расходов в тестах берутся из своих строк.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models import SiteCostItem
from app.services import site_funding_service as funding

TODAY = date(2001, 3, 15)
JAN, FEB, MAR, APR = date(2001, 1, 1), date(2001, 2, 1), date(2001, 3, 1), date(2001, 4, 1)


def _item(db: Session, title: str, hint: str | None = None) -> SiteCostItem:
    return funding.create_item(db, title=f"{title} {uuid4().hex[:6]}", hint=hint)


def _donate(db: Session, day: date, amount: int, name: str | None, *, anonymous: bool = False):
    return funding.create_donation(
        db, received_on=day, amount_rub=amount, donor_name=name, is_anonymous=anonymous, today=TODAY
    )


def _enable(db: Session) -> None:
    funding.get_settings_row(db).show_public = True
    db.commit()


# ---------- месяцы ----------


def test_parse_and_format_month() -> None:
    assert funding.parse_month("2026-10") == date(2026, 10, 1)
    assert funding.format_month(date(2026, 1, 1)) == "2026-01"
    assert funding.add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert funding.add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    for bad in ("2026-13", "октябрь", "2026", "1999-01"):
        with pytest.raises(funding.FundingError):
            funding.parse_month(bad)


# ---------- расходы ----------


def test_empty_month_reads_copy_of_last_filled_month(db_session: Session) -> None:
    server, ai = _item(db_session, "Сервер"), _item(db_session, "AI")
    funding.save_month_costs(db_session, JAN, [(server.id, 1500), (ai.id, 17500)], today=TODAY)

    copied = funding.month_costs(db_session, MAR)
    assert copied.copied_from == JAN
    assert [line.amount_rub for line in copied.lines] == [1500, 17500]

    funding.save_month_costs(db_session, FEB, [(server.id, 1500), (ai.id, 19700)], today=TODAY)
    assert funding.month_costs(db_session, MAR).copied_from == FEB
    own = funding.month_costs(db_session, FEB)
    assert own.copied_from is None
    assert own.total_rub == 21200


def test_save_month_replaces_items_of_that_month_only(db_session: Session) -> None:
    server, vps = _item(db_session, "Сервер"), _item(db_session, "VPS")
    funding.save_month_costs(db_session, JAN, [(server.id, 1500), (vps.id, 1300)], today=TODAY)
    funding.save_month_costs(db_session, FEB, [(server.id, 1600)], today=TODAY)

    assert [line.item_id for line in funding.month_costs(db_session, FEB).lines] == [server.id]
    assert {line.item_id for line in funding.month_costs(db_session, JAN).lines} == {server.id, vps.id}


def test_empty_save_clears_month_and_moves_first_month(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, JAN, [(server.id, 1500)], today=TODAY)
    funding.save_month_costs(db_session, FEB, [(server.id, 1600)], today=TODAY)
    assert funding.first_month(db_session, today=TODAY) == JAN

    # Учёт начинается с февраля: январь очищается целиком.
    cleared = funding.save_month_costs(db_session, JAN, [], today=TODAY)
    assert cleared.lines == []
    assert cleared.copied_from is None
    assert funding.first_month(db_session, today=TODAY) == FEB
    assert [line.amount_rub for line in funding.month_costs(db_session, FEB).lines] == [1600]


def test_future_month_is_a_plan(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, APR, [(server.id, 900)], today=TODAY)
    assert funding.month_costs(db_session, APR).total_rub == 900
    with pytest.raises(funding.FundingError):
        funding.save_month_costs(db_session, date(2002, 4, 1), [(server.id, 900)], today=TODAY)


def test_save_month_rejects_bad_input(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    with pytest.raises(funding.FundingError):
        funding.save_month_costs(db_session, JAN, [(server.id, 1), (server.id, 2)], today=TODAY)
    with pytest.raises(funding.FundingError):
        funding.save_month_costs(db_session, JAN, [(server.id, -1)], today=TODAY)
    with pytest.raises(funding.FundingError) as missing:
        funding.save_month_costs(db_session, JAN, [(uuid4(), 100)], today=TODAY)
    assert missing.value.status_code == 404


def test_item_title_is_required(db_session: Session) -> None:
    with pytest.raises(funding.FundingError):
        funding.create_item(db_session, title="   ")
    title = f"Домены {uuid4().hex[:6]}"
    item = funding.create_item(db_session, title=f"  {title}  ", hint="  ")
    assert item.title == title
    assert item.hint is None


def test_item_titles_are_unique(db_session: Session) -> None:
    item = _item(db_session, "Сервер")
    with pytest.raises(funding.FundingError) as dup:
        funding.create_item(db_session, title=item.title.upper())
    assert dup.value.status_code == 409
    other = _item(db_session, "VPS")
    with pytest.raises(funding.FundingError):
        funding.update_item(db_session, other.id, title=item.title, hint=None)
    assert funding.update_item(db_session, item.id, title=item.title, hint="ⓘ").hint == "ⓘ"


def test_migration_seeds_items_with_ai_hint(db_session: Session) -> None:
    titles = {item.title: item for item in funding.list_items(db_session)}
    for title in (
        "Домашний сервер: электричество и интернет",
        "Арендованный сервер",
        "Почтовый сервис",
        "Резервные копии и хранилище",
        "Домены",
        "VPN-сервисы",
        "AI-агенты",
    ):
        assert title in titles
    assert titles["AI-агенты"].hint and "развивался" in titles["AI-агенты"].hint


# ---------- переводы ----------


def test_donation_validation(db_session: Session) -> None:
    with pytest.raises(funding.FundingError):
        _donate(db_session, date(2001, 3, 16), 500, "Иван П.")
    with pytest.raises(funding.FundingError):
        _donate(db_session, JAN, 0, "Иван П.")
    donation = _donate(db_session, JAN, 500, "  Иван   П. ")
    assert donation.donor_name == "Иван П."
    assert _donate(db_session, JAN, 500, "   ").donor_name is None


def test_known_donor_names_frequent_first(db_session: Session) -> None:
    name_a, name_b = f"Анна {uuid4().hex[:4]}", f"Иван {uuid4().hex[:4]}"
    _donate(db_session, JAN, 500, name_b)
    _donate(db_session, FEB, 500, name_b)
    _donate(db_session, FEB, 900, name_a)
    names = [name for name, _ in funding.known_donor_names(db_session) if name in (name_a, name_b)]
    assert names == [name_b, name_a]


# ---------- публичный месяц ----------


def test_hidden_until_enabled_and_costs_exist(db_session: Session) -> None:
    funding.get_settings_row(db_session).show_public = False
    db_session.commit()
    assert funding.public_month(db_session, today=TODAY) is None

    with pytest.raises(funding.FundingError) as no_costs:
        # В 2001 году у текущего месяца расходов нет — включить нельзя.
        funding.set_show_public(db_session, True, today=date(2000, 6, 1))
    assert no_costs.value.status_code == 409

    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, JAN, [(server.id, 1500)], today=TODAY)
    assert funding.set_show_public(db_session, True, today=TODAY).show_public is True
    assert funding.public_month(db_session, today=TODAY) is not None


def test_public_month_hides_anonymous_names_and_sorts_by_date(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, FEB, [(server.id, 4000)], today=TODAY)
    _enable(db_session)
    _donate(db_session, date(2001, 2, 20), 1000, "Анна С.")
    _donate(db_session, date(2001, 2, 3), 500, "Иван П.")
    _donate(db_session, date(2001, 2, 10), 700, "Пётр Н.", anonymous=True)
    _donate(db_session, date(2001, 2, 12), 300, None)
    _donate(db_session, date(2001, 3, 1), 900, "Мартовский")

    result = funding.public_month(db_session, FEB, today=TODAY)
    assert result is not None
    assert [(d.received_on.day, d.name) for d in result.donations] == [
        (3, "Иван П."),
        (10, None),
        (12, None),
        (20, "Анна С."),
    ]
    assert result.donated_total_rub == 2500
    assert result.costs.total_rub == 4000


def test_last_month_without_plan_is_current(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, FEB, [(server.id, 4000)], today=TODAY)
    assert funding.last_month(db_session, today=TODAY) == MAR


def test_public_month_bounds(db_session: Session) -> None:
    server = _item(db_session, "Сервер")
    funding.save_month_costs(db_session, FEB, [(server.id, 4000)], today=TODAY)
    # План на апрель открывает апрель на сайте: виден план, переводов в нём нет.
    funding.save_month_costs(db_session, APR, [(server.id, 5000)], today=TODAY)
    _enable(db_session)
    _donate(db_session, date(2001, 1, 25), 500, "Иван П.")

    current = funding.public_month(db_session, today=TODAY)
    assert current is not None
    assert current.month == MAR
    # Январский перевод раньше первых расходов на сайте не открывает январь:
    # месяц без расходов показал бы «поддержали 500 ₽ из 0 ₽».
    assert current.first_month == FEB
    assert current.last_month == APR
    assert current.costs.copied_from == FEB

    planned = funding.public_month(db_session, APR, today=TODAY)
    assert planned is not None
    assert planned.costs.total_rub == 5000
    assert planned.donations == []

    for outside in (date(2001, 5, 1), JAN):
        with pytest.raises(funding.FundingError) as err:
            funding.public_month(db_session, outside, today=TODAY)
        assert err.value.status_code == 404


# ---------- API ----------


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_public_api_is_a_whitelist(client: TestClient, db_session: Session) -> None:
    server = _item(db_session, "Сервер", hint="Подсказка")
    funding.save_month_costs(db_session, FEB, [(server.id, 4000)], today=TODAY)
    _enable(db_session)
    secret = f"Тайный {uuid4().hex[:6]}"
    _donate(db_session, date(2001, 2, 10), 700, secret, anonymous=True)
    _donate(db_session, date(2001, 2, 11), 300, "Анна С.")

    response = client.get("/api/funding/month", params={"month": "2001-02"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    assert secret not in response.text
    assert str(server.id) not in response.text
    body = response.json()
    assert set(body) == {
        "visible",
        "month",
        "current_month",
        "first_month",
        "last_month",
        "costs",
        "cost_total_rub",
        "donations",
        "donated_total_rub",
    }
    assert body["month"] == "2001-02"
    assert set(body["costs"][0]) == {"title", "hint", "amount_rub"}
    assert [set(d) for d in body["donations"]] == [{"received_on", "name", "amount_rub"}] * 2
    assert [d["name"] for d in body["donations"]] == [None, "Анна С."]

    assert client.get("/api/funding/month", params={"month": "кривой"}).status_code == 400


def test_public_api_when_hidden(client: TestClient, db_session: Session) -> None:
    funding.get_settings_row(db_session).show_public = False
    db_session.commit()
    response = client.get("/api/funding/month")
    assert response.status_code == 200
    assert response.json()["visible"] is False
    assert response.json()["donations"] == []


def test_admin_api_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/funding").status_code in (401, 403)
    assert client.post("/api/admin/funding/donations", json={}).status_code in (401, 403, 422)


# ---------- report_ro ----------


def test_donations_closed_for_report_ro() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic" / "versions" / "120_site_funding.py").read_text(encoding="utf-8")
    assert "REVOKE ALL ON TABLE site_donations FROM report_ro" in migration
    script_path = root.parent / "scripts" / "create_report_ro_role.sql"
    if script_path.exists():
        assert "public.site_donations FROM report_ro" in script_path.read_text(encoding="utf-8")
