"""Публичный поиск по сайту и журнал поисковых запросов.

Стережём то, что в поиске легко сломать незаметно: приватность (закрытый
профиль и «Иван П.» не должны вести на аккаунт, внешних ссылок на профили в
системах нет вовсе), склейку нескольких систем одного человека в одну строку
и приём бекона с телом text/plain.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Generator
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.abuse_protection import RouteTier, classify_route
from app.db.session import get_db
from app.main import app
from app.models import (
    Event,
    Location,
    LocationCatalog,
    LocationCatalogLink,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    SearchQueryLog,
    User,
)
from app.services import location_page_service, participant_search_service, site_search_service
from app.services.participant_search_service import normalize_query_text, significant_words
from app.services.search_log_service import get_search_log_report, record_search
from app.services.site_search_service import (
    normalize_log_query,
    search_locations,
    site_search,
    switch_keyboard_layout,
)

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


# ---------------------------------------------------------------------------
# Помощники
# ---------------------------------------------------------------------------


def _platform(db_session: Session, code: str) -> Platform:
    return db_session.query(Platform).filter(Platform.code == code).one()


def _participant(db_session: Session, platform: Platform, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"site-srch-{uuid4().hex[:12]}",
        display_name=name,
        profile_url=f"https://example.org/profile/{uuid4().hex[:8]}",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _runs(
    db_session: Session,
    platform: Platform,
    participant: Participant,
    count: int,
    *,
    location_name: str = "Поисковый парк",
    city: str = "Поискоград",
) -> None:
    location = Location(
        platform_id=platform.id,
        external_key=f"site-srch-loc-{uuid4().hex[:10]}",
        name=location_name,
        city=city,
    )
    db_session.add(location)
    db_session.flush()
    for index in range(count):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"site-srch-ev-{uuid4().hex[:10]}",
            event_date=date(2026, 1, 3) + timedelta(days=7 * index),
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"site-srch-res-{uuid4().hex[:10]}",
                position=1,
            )
        )
    db_session.flush()


def _user(db_session: Session, **kwargs) -> User:
    user = User(consent_accepted=True, **kwargs)
    db_session.add(user)
    db_session.flush()
    return user


def _link(db_session: Session, user: User, participant: Participant) -> None:
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=participant.platform_id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url or "https://example.org",
        )
    )
    db_session.flush()


@pytest.fixture
def no_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каталог локаций в тестах людей не нужен — и не должен зависеть от Redis."""
    monkeypatch.setattr(site_search_service, "_location_entries", lambda _db: [])


# ---------------------------------------------------------------------------
# Нормализация и раскладка
# ---------------------------------------------------------------------------


def test_normalize_log_query_collapses_spaces_and_lowercases() -> None:
    assert normalize_log_query("  Попов   Дмитрий \t") == "попов дмитрий"
    assert normalize_log_query(None) == ""
    assert len(normalize_log_query("я" * 300)) == 100


def test_switch_keyboard_layout_both_directions() -> None:
    assert switch_keyboard_layout("cjrjkmybrb") == "сокольники"
    assert switch_keyboard_layout("ыщлщдтшлш") == "sokolniki"
    assert switch_keyboard_layout("Gjgjd") == "Попов"
    # Знаковые клавиши — тоже буквы: «[» — это «х», «,» — «б».
    assert switch_keyboard_layout("[jkv") == "холм"
    assert switch_keyboard_layout(",thtpf") == "береза"


def test_search_route_has_own_abuse_tier() -> None:
    assert classify_route("/api/search", "GET") is RouteTier.search
    # Беконы журнала — своё ведро: иначе они съедали лимит самого поиска.
    assert classify_route("/api/search/log", "POST") is RouteTier.search_log
    assert classify_route("/api/searching-else", "GET") is RouteTier.default


def test_control_chars_are_dropped_from_queries() -> None:
    """Нулевой байт из адреса (?q=ab%00cd) доходил до LIKE, и поиск отвечал 500 (ревью, NUL-6)."""
    assert normalize_query_text("ab\x00cd") == "abcd"
    assert normalize_query_text("\x00Иван\x07 \u200bПетров\ud800") == "Иван Петров"
    # Табуляция и неразрывный пробел — просто пробелы.
    assert normalize_query_text("Иван\tПетров\u00a0Сидоров") == "Иван Петров Сидоров"
    assert normalize_log_query("Ива\x00н") == "иван"


def test_significant_words_drop_repeats_and_keep_the_longest() -> None:
    assert significant_words(["ова"] * 25) == ["ова"]
    assert significant_words(["Попов", "попов", "Дмитрий", "ПОПОВ"]) == ["Попов", "Дмитрий"]
    # «Ё» и «е» — одно слово.
    assert significant_words(["Фёдоров", "Федоров"]) == ["Фёдоров"]
    # Больше четырёх — оставляем самые длинные, в исходном порядке.
    assert significant_words(["а", "Иванов", "б", "Иван", "Иванович", "в", "Москва"]) == [
        "Иванов",
        "Иван",
        "Иванович",
        "Москва",
    ]


def test_significant_words_trim_punctuation() -> None:
    """Слово из одних знаков — не слово (SKEP-4), знак по краю — опечатка («Попов,»)."""
    assert significant_words(["%%%", "___", "...", "---", "№№№", "!!!"]) == []
    assert significant_words(["Попов,", "Д.", "(Москва)"]) == ["Попов", "Д", "Москва"]
    # Знаки внутри слова — часть имени.
    assert significant_words(["Римского-Корсакова", "О'Нил"]) == ["Римского-Корсакова", "О'Нил"]
    # «Попов» и «Попов,» — одно слово.
    assert significant_words(["Попов", "Попов,"]) == ["Попов"]


# ---------------------------------------------------------------------------
# Локации
# ---------------------------------------------------------------------------


def _fake_index(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        {"slug": "park-sokolniki-yug", "name": "Южные Сокольники", "city": "Москва",
         "platform_codes": ["s95"], "events_count": 300, "finishers_total": 9000},
        {"slug": "sokolniki", "name": "Сокольники", "city": "Москва",
         "platform_codes": ["five_verst", "parkrun"], "events_count": 200, "finishers_total": 5000},
        {"slug": "kuzminki", "name": "Кузьминки", "city": "Москва",
         "platform_codes": ["five_verst"], "events_count": 400, "finishers_total": 20000},
        {"slug": "berezovaya-roshcha", "name": "Берёзовая роща", "city": "Новосибирск",
         "platform_codes": ["five_verst"], "events_count": 100, "finishers_total": 3000},
    ]
    monkeypatch.setattr(site_search_service, "_location_entries", lambda _db: entries)


def test_locations_prefix_first_then_popularity(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_index(monkeypatch)
    found = search_locations(None, "сокол")  # type: ignore[arg-type]
    # «Сокольники» начинаются с запроса — выше «Южных», хотя у тех стартов больше.
    assert [item["slug"] for item in found] == ["sokolniki", "park-sokolniki-yug"]
    assert found[0] == {
        "slug": "sokolniki",
        "name": "Сокольники",
        "city": "Москва",
        "platform_codes": ["five_verst", "parkrun"],
        "href": "/locations/sokolniki",
    }


def test_locations_words_any_order_city_slug_and_yo(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_index(monkeypatch)
    assert [item["slug"] for item in search_locations(None, "москва кузьм")] == ["kuzminki"]  # type: ignore[arg-type]
    assert [item["slug"] for item in search_locations(None, "kuzmin")] == ["kuzminki"]  # type: ignore[arg-type]
    # «е» и «ё» не различаем: в названиях они вперемешку.
    assert [item["slug"] for item in search_locations(None, "березовая")] == ["berezovaya-roshcha"]  # type: ignore[arg-type]
    assert search_locations(None, "к") == []  # type: ignore[arg-type]


def test_layout_fallback_for_locations(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    _fake_index(monkeypatch)
    page = site_search(db_session, "cjrjkmybrb")
    assert page["corrected_query"] == "сокольники"
    assert page["locations"][0]["slug"] == "sokolniki"


# ---------------------------------------------------------------------------
# Люди
# ---------------------------------------------------------------------------


def test_registered_user_merges_platforms(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    s95 = _platform(db_session, "s95")
    fv_participant = _participant(db_session, five, "Щукоглазов ТЕСТОПОИСК")
    # В С95 он записан латиницей — имя не совпадает, но пробежки его.
    s95_participant = _participant(db_session, s95, "Shchukoglazov Testopoisk")
    _runs(db_session, five, fv_participant, 3, location_name="Главный парк")
    _runs(db_session, s95, s95_participant, 2, location_name="Второй парк")
    slug = f"srch{uuid4().hex[:8]}"
    user = _user(db_session, display_name="Щукоглазов Тестопоиск", public_slug=slug, avatar_path="2026/09/a.jpg")
    _link(db_session, user, fv_participant)
    _link(db_session, user, s95_participant)

    page = site_search(db_session, "тестопоиск щукоглазов")

    assert len(page["people"]) == 1
    person = page["people"][0]
    assert person == {
        "kind": "registered",
        "display_name": "Щукоглазов Тестопоиск",
        "href": f"/users/{slug}",
        "avatar_url": user.avatar_url,
        "total_runs": 5,
        "total_volunteering": 0,
        # Пять пробежек по субботам с 03.01.2026: последняя — пятая неделя.
        "last_run_date": date(2026, 1, 3) + timedelta(days=7 * 2),
        "top_location_name": "Главный парк",
        "platform_codes": ["five_verst", "s95"],
        "partial": False,
    }


def test_registered_without_slug_uses_serial_id_and_goes_first(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    linked = _participant(db_session, five, "Мухолов Искомыйбегун")
    stranger = _participant(db_session, five, "Мухолов Искомыйбегун")
    _runs(db_session, five, stranger, 10)
    user = _user(db_session, display_name="Мухолов Искомыйбегун")
    _link(db_session, user, linked)

    people = site_search(db_session, "искомыйбегун")["people"]

    assert [person["kind"] for person in people] == ["registered", "participant"]
    assert people[0]["href"] == f"/users/{user.serial_id}"
    assert people[1]["total_runs"] == 10
    assert people[1]["top_location_name"] == "Поисковый парк"
    assert people[1]["top_location_city"] == "Поискоград"


def test_private_profile_is_plain_participant(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    participant = _participant(db_session, five, "Скрытнов ПРИВАТНЫЙТЕСТ")
    _runs(db_session, five, participant, 1)
    user = _user(db_session, display_name="Скрытнов Приватныйтест", profile_private=True, public_slug=f"p{uuid4().hex[:8]}")
    _link(db_session, user, participant)

    people = site_search(db_session, "приватныйтест")["people"]

    assert len(people) == 1
    assert people[0]["kind"] == "participant"
    assert "href" not in people[0]
    assert "avatar_url" not in people[0]
    assert people[0]["display_name"] == "Скрытнов Приватныйтест"


def test_initial_style_not_found_by_hidden_surname(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    participant = _participant(db_session, five, "Иван ИНИЦИАЛОВТЕСТ")
    user = _user(db_session, display_name="Иван И.", display_name_style="initial")
    _link(db_session, user, participant)

    people = site_search(db_session, "инициаловтест")["people"]

    # Фамилию человек прячет — по ней на его профиль не выводим.
    assert [person["kind"] for person in people] == ["participant"]


def test_unknown_names_are_skipped(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    runpark = _platform(db_session, "runpark")
    _participant(db_session, five, "НЕИЗВЕСТНЫЙ")
    _participant(db_session, runpark, "Неизвестный бегун")
    real = _participant(db_session, five, "Максим НЕИЗВЕСТНЫЙТЕСТ")
    people = site_search(db_session, "неизвестный")["people"]
    names = {person["display_name"] for person in people}
    assert "Неизвестный" not in names
    assert "Неизвестный Бегун" not in names
    # Настоящая фамилия, начинающаяся так же, — не заглушка.
    assert real.display_name and "Максим Неизвестныйтест" in {
        person["display_name"] for person in site_search(db_session, "неизвестныйтест")["people"]
    }


def test_two_letter_surname_is_found_as_whole_word(db_session: Session, no_locations: None) -> None:
    """«Ли», «Ан», «Юн» — настоящие фамилии, но как подстрока они есть почти в каждом имени."""
    five = _platform(db_session, "five_verst")
    for name in ("Мария ЪЭ", "ЪЭ Анна", "Пётр ЪЭ-Тестов", "Алиса ТЪЭСТ"):
        _participant(db_session, five, name)

    whole = _participant(db_session, five, "Ольга ЪЭ")
    _link(db_session, _user(db_session, display_name="Ольга Ъэ"), whole)
    inside = _participant(db_session, five, "Олег ТЪЭСТОВ")
    _link(db_session, _user(db_session, display_name="Олег Тъэстов"), inside)

    people = site_search(db_session, "ъэ")["people"]
    names = {person["display_name"] for person in people}

    assert names == {"Ольга Ъэ", "Мария Ъэ", "Ъэ Анна", "Пётр Ъэ-Тестов"}
    assert people[0]["kind"] == "registered"


def test_short_words_next_to_long_are_not_substrings(db_session: Session, no_locations: None) -> None:
    """«Ли Москва» находил Анатолия Москву: «ли» — подстрока «Анатолия»."""
    five = _platform(db_session, "five_verst")
    for name in ("Ъэ ЪЮЖАНИН", "Анатолъэй ЪЮЖАНИН", "Анна ЪЮЖАНИНА", "Ольга ЪЮЖАНИНА"):
        _participant(db_session, five, name)

    two_letters = {person["display_name"] for person in site_search(db_session, "ъэ ъюжанин")["people"]}
    # Одна буква — начало слова имени («Попов Д»).
    initial = {person["display_name"] for person in site_search(db_session, "ъюжанин а")["people"]}

    assert two_letters == {"Ъэ Ъюжанин"}
    assert initial == {"Анатолъэй Ъюжанин", "Анна Ъюжанина"}
    # Две цифры или буква с цифрой — не имя.
    assert site_search(db_session, "ъ1")["people"] == []


def test_layout_fallback_for_people(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Раскладкин Тестович")
    typed = switch_keyboard_layout("раскладкин")

    page = site_search(db_session, typed)

    assert page["corrected_query"] == "раскладкин"
    assert [person["display_name"] for person in page["people"]] == ["Раскладкин Тестович"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def api_settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key",
        app_debug=True,
        app_base_url="http://testserver",
        telegram_bot_internal_secret="bot-secret",
        telegram_bot_username="TestBot",
        admin_telegram_id=9001,
        database_url=get_settings().database_url,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture
def client(
    db_session: Session,
    fake_redis: fakeredis.FakeRedis,
    api_settings: Settings,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: api_settings
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.auth_service.check_rate_limit", lambda *_args, **_kwargs: True)
        with TestClient(app, headers={"User-Agent": BROWSER_UA}) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def _login(client: TestClient, telegram_id: int) -> None:
    login_response = client.post("/api/auth/login-request")
    confirm_response = client.post(
        "/api/auth/bot/confirm",
        json={
            "request_token": login_response.json()["request_token"],
            "telegram_id": telegram_id,
            "telegram_username": f"user{telegram_id}",
            "telegram_chat_id": telegram_id,
            "consent_accepted": True,
        },
        headers={"X-Bot-Secret": "bot-secret"},
    )
    token = confirm_response.json()["magic_link"].split("token=")[-1]
    assert client.get(f"/api/auth/callback?token={token}", follow_redirects=False).status_code == 302


def test_api_response_has_no_external_profile_urls(
    client: TestClient, db_session: Session, no_locations: None
) -> None:
    five = _platform(db_session, "five_verst")
    registered = _participant(db_session, five, "Внешнессылкин Апитест")
    plain = _participant(db_session, five, "Внешнессылкин Апитест")
    _runs(db_session, five, plain, 2)
    user = _user(db_session, display_name="Внешнессылкин Апитест")
    _link(db_session, user, registered)

    response = client.get("/api/search", params={"q": "внешнессылкин"})

    assert response.status_code == 200
    body = response.json()
    assert [person["kind"] for person in body["people"]] == ["registered", "participant"]
    assert "profile_url" not in response.text
    assert "example.org" not in response.text
    assert set(body) == {
        "query",
        "corrected_query",
        "locations",
        "locations_total",
        "locations_all",
        "locations_similar",
        "people",
        "people_truncated",
        "people_place",
        "people_skipped",
    }


def _log_rows(db_session: Session) -> list[SearchQueryLog]:
    return db_session.query(SearchQueryLog).order_by(SearchQueryLog.id).all()


def test_log_accepts_text_plain_beacon(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    response = client.post(
        "/api/search/log",
        content='{"query": "  Сокольники  Парк ", "pages_found": 0, "locations_found": 1, "people_found": 3,'
        ' "clicked_kind": "location", "clicked_target": "/locations/sokolniki", "is_mobile": true}',
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )
    assert response.status_code == 204
    rows = _log_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert (row.query, row.locations_found, row.people_found) == ("сокольники парк", 1, 3)
    assert (row.clicked_kind, row.clicked_target) == ("location", "/locations/sokolniki")
    assert row.is_mobile is True
    assert row.is_authed is False


def test_log_ignores_short_empty_and_garbage(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    for body in ('{"query": "я"}', '{"query": "   "}', "не json", "[1, 2]", ""):
        assert client.post("/api/search/log", content=body, headers={"Content-Type": "text/plain"}).status_code == 204
    assert _log_rows(db_session) == []


def test_log_marks_authed_and_drops_unknown_click_kind(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    _login(client, int(uuid4().int % 10_000_000_000) + 100_000)
    client.post("/api/search/log", json={"query": "кузьминки", "clicked_kind": "hack", "clicked_target": "x"})
    row = _log_rows(db_session)[0]
    assert row.is_authed is True
    assert row.clicked_kind is None
    assert row.clicked_target is None


def test_log_skips_admin(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    _login(client, 9001)
    client.post("/api/search/log", json={"query": "админский поиск"})
    assert _log_rows(db_session) == []


def test_admin_search_log_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/search-log").status_code == 401


def test_admin_search_log_report(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    for payload in (
        {"query": "Сокольники", "locations_found": 1, "clicked_kind": "location", "clicked_target": "/locations/sokolniki"},
        {"query": "сокольники", "locations_found": 1},
        {"query": "чего нет", "pages_found": 0},
        {"query": "Чего  нет"},
        {"query": "попов", "people_found": 5, "clicked_kind": "person", "clicked_target": "/users/popov"},
    ):
        assert record_search(db_session, payload, is_authed=False)
    old = SearchQueryLog(query="давний", created_at=datetime.now(timezone.utc) - timedelta(days=40))
    db_session.add(old)
    db_session.flush()

    report = get_search_log_report(db_session, period_days=30)

    assert report["total"] == 5
    assert report["zero_result_total"] == 2
    assert report["top_queries"][:2] == [
        {"query": "сокольники", "count": 2, "zero_results_count": 0, "clicks": 1},
        {"query": "чего нет", "count": 2, "zero_results_count": 2, "clicks": 0},
    ]
    assert [(item["query"], item["count"]) for item in report["zero_result_queries"]] == [("чего нет", 2)]
    assert report["clicks_by_kind"] == {"page": 0, "location": 1, "person": 1, "none": 3}
    assert sum(item["count"] for item in report["daily"]) == 5
    assert len(report["recent"]) == 5
    assert report["recent"][0]["query"] == "попов"

    _login(client, 9001)
    response = client.get("/api/admin/search-log", params={"period_days": 90})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6
    assert body["clicks_by_kind"]["none"] == 4


# ---------------------------------------------------------------------------
# «Е» и «ё», совпадения с начала слова (ревью 25.09.2026)
# ---------------------------------------------------------------------------


def test_yo_and_ye_find_each_other(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Василий ЁЖИКОВЁНКОВ")
    _participant(db_session, five, "Пётр ЕЖИКОВЕНКОВ")

    for query in ("Ёжиковёнков", "ежиковенков", "ЕЖИКОВЁНКОВ"):
        names = {person["display_name"] for person in site_search(db_session, query)["people"]}
        assert names == {"Василий Ёжиковёнков", "Пётр Ежиковенков"}, query
    # И в имени тоже: «Петр» находит «Пётра».
    assert [person["display_name"] for person in site_search(db_session, "петр ежиковенков")["people"]] == [
        "Пётр Ежиковенков"
    ]


def test_yo_in_registered_user_name(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    participant = _participant(db_session, five, "Артём СЕМЁНОВИЧЕВ")
    user = _user(db_session, display_name="Артём Семёновичев")
    _link(db_session, user, participant)

    people = site_search(db_session, "семеновичев артем")["people"]

    assert [person["kind"] for person in people] == ["registered"]


def test_word_start_matches_go_first(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    # Из середины слова — и с самыми свежими пробежками: раньше они и стояли первыми.
    for name in ("Хакимоглызов Рустам", "Екимоглыз Анна"):
        _runs(db_session, five, _participant(db_session, five, name), 5)
    _participant(db_session, five, "Кимоглыз Юлия")
    _participant(db_session, five, "Юлия Кимоглызовская")

    people = site_search(db_session, "кимоглыз")["people"]
    names = [person["display_name"] for person in people]

    assert names[:2] == ["Кимоглыз Юлия", "Юлия Кимоглызовская"]
    assert set(names[2:]) == {"Хакимоглызов Рустам", "Екимоглыз Анна"}
    # Совпадения из середины помечены: окно показывает их отдельной группой.
    assert [person["partial"] for person in people] == [False, False, True, True]


def test_registered_middle_match_goes_below_protocol_word_start(db_session: Session, no_locations: None) -> None:
    """«Лев»: Михалевский с открытым профилем — ниже Льва из протоколов (ревью, search-6)."""
    five = _platform(db_session, "five_verst")
    middle = _participant(db_session, five, "Кирилл МИЗУРБАЛЬСКИЙ")
    _runs(db_session, five, middle, 30)
    _link(db_session, _user(db_session, display_name="Кирилл Мизурбальский", public_slug=f"m{uuid4().hex[:8]}"), middle)
    prefix = _participant(db_session, five, "Александр ЗУРБАЛИН")
    _link(db_session, _user(db_session, display_name="Александр Зурбалин"), prefix)
    _participant(db_session, five, "Зурбал Ионов")

    people = site_search(db_session, "зурбал")["people"]

    assert [(person["display_name"], person["kind"], person["partial"]) for person in people] == [
        # С начала слова: участник сайта первым, даже если у человека из
        # протоколов имя совпало целиком — иначе при двух десятках таких
        # «Львов» участник сайта выпал бы за лимит.
        ("Александр Зурбалин", "registered", False),
        ("Зурбал Ионов", "participant", False),
        # Из середины слова — после всех, хоть у него и 30 пробежек.
        ("Кирилл Мизурбальский", "registered", True),
    ]


def test_registered_uses_best_matching_profile(db_session: Session, no_locations: None) -> None:
    """У человека два профиля: по одному имя совпало из середины, по другому — с начала."""
    five = _platform(db_session, "five_verst")
    s95 = _platform(db_session, "s95")
    user = _user(db_session, display_name="Олег Дрымбов")
    _link(db_session, user, _participant(db_session, five, "Олег ПОДРЫМБОВ"))
    _link(db_session, user, _participant(db_session, s95, "Олег ДРЫМБОВ"))

    people = site_search(db_session, "дрымбов")["people"]

    assert [(person["kind"], person["partial"]) for person in people] == [("registered", False)]


def test_word_start_ordering_happens_before_candidate_limit(
    db_session: Session, no_locations: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кандидатов по имени берём ограниченно — нужный человек не должен выпасть за лимит."""
    monkeypatch.setattr(site_search_service, "PEOPLE_CANDIDATE_LIMIT", 2)
    five = _platform(db_session, "five_verst")
    for index in range(4):
        _participant(db_session, five, f"Залевоглаз{index} Мидлтест")
    _participant(db_session, five, "Левоглаз Нужныйтест")

    names = [person["display_name"] for person in site_search(db_session, "левоглаз")["people"]]

    assert names[0] == "Левоглаз Нужныйтест"


def test_participant_row_has_last_run_date(db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    participant = _participant(db_session, five, "Датов Последнийстарт")
    _runs(db_session, five, participant, 3)

    person = site_search(db_session, "последнийстарт")["people"][0]

    assert person["last_run_date"] == date(2026, 1, 17)


# ---------------------------------------------------------------------------
# Локации: города, сокращения, регионы, «парк», похожие названия
# ---------------------------------------------------------------------------


def _catalog(monkeypatch: pytest.MonkeyPatch, extra: list[dict] | None = None) -> None:
    def entry(slug: str, name: str, city: str, region: str, country: str = "Россия", events: int = 10, **flags) -> dict:
        return {
            "slug": slug,
            "name": name,
            "city": city,
            "region": region,
            "country": country,
            "platform_codes": ["five_verst"],
            "events_count": events,
            "finishers_total": events * 10,
            **flags,
        }

    entries = [
        entry("sokolniki", "Сокольники", "Москва", "Москва", events=200),
        entry("kuzminki", "Кузьминки", "Москва", "Москва", events=150),
        entry("kotelniki", "Котельники Кузьминский", "Котельники", "Московская", events=50),
        entry("gorky", "Парк Горького", "Москва", "Москва", events=100),
        entry("butovo", "Бутово", "Москва", "Москва", events=40),
        entry("tomsk", "Томск Лагерный сад", "Томск", "Томская", events=60),
        entry("piter", "Пулковский парк", "Санкт-Петербург", "Санкт-Петербург", events=80),
        entry("minsk", "Минск Лошица", "Минск", "Минская", country="Беларусь", events=20),
        entry("mytishchi", "Мытищи Центральный парк", "Мытищи", "Московская", events=30),
        entry("paused", "Старый сквер", "Москва", "Москва", events=5, is_paused=True),
        *(extra or []),
    ]
    monkeypatch.setattr(site_search_service, "_location_entries", lambda _db: entries)


def _slugs(query: str) -> list[str]:
    return [item["slug"] for item in search_locations(None, query)]  # type: ignore[arg-type]


def test_locations_by_city_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch)
    assert _slugs("спб") == ["piter"]
    assert _slugs("питер") == ["piter"]
    assert _slugs("санкт петербург") == ["piter"]
    # «мск» — это Москва, а не Томск.
    assert "tomsk" not in _slugs("мск")
    assert _slugs("мск")[0] == "sokolniki"


def test_locations_by_region_and_country(monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch)
    assert set(_slugs("подмосковье")) == {"kotelniki", "mytishchi"}
    assert set(_slugs("московская область")) == {"kotelniki", "mytishchi"}
    assert _slugs("беларусь") == ["minsk"]
    assert _slugs("белоруссия") == ["minsk"]


def test_locations_ignore_park_word_and_stem(monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch)
    assert _slugs("бутово парк") == ["butovo"]
    assert _slugs("горький") == ["gorky"]
    assert _slugs("сокольниках") == ["sokolniki"]


def test_locations_word_start_beats_middle_of_word(monkeypatch: pytest.MonkeyPatch) -> None:
    _catalog(monkeypatch)
    # «Минск» — не «Кузьминский»: из середины слова локации не ищем вовсе.
    assert _slugs("минск") == ["minsk"]
    assert _slugs("зьминск") == []


def test_abbreviation_does_not_search_people(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    _catalog(monkeypatch)
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Сергей МСКИНСКИЙ")
    page = site_search(db_session, "мск")
    assert page["people"] == []
    assert page["locations"][0]["slug"] == "sokolniki"


def test_locations_all_link_to_catalog(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    extra = [
        {"slug": f"msk-{index}", "name": f"Москворецкий {index}", "city": "Москва", "region": "Москва",
         "country": "Россия", "platform_codes": ["s95"], "events_count": 1, "finishers_total": 1}
        for index in range(8)
    ]
    _catalog(monkeypatch, extra)

    page = site_search(db_session, "москва")

    assert len(page["locations"]) == 8
    assert page["locations_total"] == 13
    # Каталог по умолчанию не показывает локации на паузе — и счётчик тоже.
    assert page["locations_all"] == {"label": "Москва", "query": "Москва", "count": 12}
    assert page["locations"][0]["slug"] == "sokolniki"


def test_locations_all_link_names_region(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    extra = [
        {"slug": f"mo-{index}", "name": f"Подмосковный {index}", "city": "Химки", "region": "Московская",
         "country": "Россия", "platform_codes": ["s95"], "events_count": 1, "finishers_total": 1}
        for index in range(8)
    ]
    _catalog(monkeypatch, extra)

    page = site_search(db_session, "подмосковье")

    assert page["locations_all"] == {"label": "Московская область", "query": "Московская", "count": 10}


def test_similar_location_names_when_nothing_found(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    _catalog(monkeypatch)

    page = site_search(db_session, "сокольнеки")

    assert page["locations_similar"] is True
    assert page["locations"][0]["slug"] == "sokolniki"
    # Точное совпадение — не «похожее».
    assert site_search(db_session, "сокольники")["locations_similar"] is False


def test_people_by_name_and_city(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    _catalog(monkeypatch)
    five = _platform(db_session, "five_verst")
    here = _participant(db_session, five, "Городов Искомыйместный")
    there = _participant(db_session, five, "Городов Искомыйместный")
    _runs(db_session, five, here, 2, location_name="Какой-то парк", city="Мытищи")
    _runs(db_session, five, there, 2, location_name="Другой парк", city="Томск")

    page = site_search(db_session, "городов искомыйместный мытищи")

    assert page["people_place"] == "Мытищи"
    assert len(page["people"]) == 1
    assert page["people"][0]["top_location_city"] == "Мытищи"


def test_people_by_name_and_place_filters_before_candidate_limit(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """«Иванов Мытищи»: место — условием в SQL до LIMIT, а не отбором из первых кандидатов."""
    _catalog(monkeypatch)
    monkeypatch.setattr(site_search_service, "PEOPLE_CANDIDATE_LIMIT", 2)
    five = _platform(db_session, "five_verst")
    for index in range(4):
        _runs(db_session, five, _participant(db_session, five, f"Частов{index} Лимитович"), 1, city="Томск")
    local = _participant(db_session, five, "Частов Местный")
    _runs(db_session, five, local, 1, location_name="Мытищинский парк", city="Мытищи")

    page = site_search(db_session, "частов мытищи")

    assert page["people_place"] == "Мытищи"
    assert [person["display_name"] for person in page["people"]] == ["Частов Местный"]
    # Флаг «есть ещё» — после фильтра по месту: здесь нашёлся ровно один.
    assert page["people_truncated"] is False


def test_top_location_uses_site_catalog_name(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    """«Чаще всего: Izmailovo» → «Измайлово»: имя локации — как на её странице на сайте."""
    parkrun = _platform(db_session, "parkrun")
    five = _platform(db_session, "five_verst")
    old_runner = _participant(db_session, parkrun, "Каталогов Латиницын")
    _runs(db_session, parkrun, old_runner, 3, location_name="Izmailovotest", city="Moscow")
    location = (
        db_session.query(Location).join(Event, Event.location_id == Location.id)
        .join(RunResult, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id == old_runner.id)
        .first()
    )
    assert location is not None
    catalog = LocationCatalog(canonical_name="Izmailovotest")
    db_session.add(catalog)
    db_session.flush()
    # Связка без location_id — только по слагу в системе: так сведена часть узлов каталога.
    db_session.add(LocationCatalogLink(catalog_id=catalog.id, platform_id=parkrun.id, external_key=location.external_key))
    # Несведённая в каталог локация — идентичность «location:<id>».
    solo_runner = _participant(db_session, five, "Каталогов Одиночкин")
    _runs(db_session, five, solo_runner, 2, location_name="Solo park", city="Solo")
    solo = (
        db_session.query(Location).join(Event, Event.location_id == Location.id)
        .join(RunResult, RunResult.event_id == Event.id)
        .filter(RunResult.participant_id == solo_runner.id)
        .first()
    )
    assert solo is not None
    db_session.flush()
    entries = [
        {"slug": "izmailovo-test", "identity_key": f"catalog:{catalog.id}", "name": "Измайловотест",
         "city": "Москва", "platform_codes": ["five_verst"], "events_count": 1, "finishers_total": 1},
        {"slug": "solo-test", "identity_key": f"location:{solo.id}", "name": "Одиночный парк",
         "city": "Одиноград", "platform_codes": ["five_verst"], "events_count": 1, "finishers_total": 1},
    ]
    monkeypatch.setattr(site_search_service, "_location_entries", lambda _db: entries)

    people = {person["display_name"]: person for person in site_search(db_session, "каталогов")["people"]}

    assert people["Каталогов Латиницын"]["top_location_name"] == "Измайловотест"
    assert people["Каталогов Латиницын"]["top_location_city"] == "Москва"
    assert people["Каталогов Одиночкин"]["top_location_name"] == "Одиночный парк"


# ---------------------------------------------------------------------------
# Журнал: «искали и никуда не перешли»
# ---------------------------------------------------------------------------


def test_search_log_report_no_click_queries(db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    for payload in (
        {"query": "погода", "people_found": 11},
        {"query": "Погода", "pages_found": 1},
        {"query": "чего нет"},
        {"query": "сокольники", "locations_found": 1, "clicked_kind": "location", "clicked_target": "/locations/sokolniki"},
    ):
        assert record_search(db_session, payload, is_authed=False)

    report = get_search_log_report(db_session, period_days=30)

    assert report["no_click_total"] == 3
    assert [(item["query"], item["count"], item["zero_results_count"]) for item in report["no_click_queries"]] == [
        ("погода", 2, 0),
        ("чего нет", 1, 1),
    ]


# ---------------------------------------------------------------------------
# Стоимость поиска ограничена при любом вводе (ревью 26.09.2026: SRCH-DOS-1,
# search-heavy-anon, S1, SRCH-COLD-2, NUL-6)
# ---------------------------------------------------------------------------


def _capture_sql(db_session: Session) -> list[str]:
    statements: list[str] = []

    def remember(_conn, _cursor, statement, *_args) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(db_session.connection(), "before_cursor_execute", remember)
    return statements


def test_repeated_words_cost_as_one_word(db_session: Session, no_locations: None) -> None:
    """«ова» 25 раз через пробел — 75 регулярок на строку и 5 с на запрос. Теперь — как одно слово."""
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Повторов Словович")

    statements = _capture_sql(db_session)
    single = site_search(db_session, "повторов")["people"]
    single_regexes = max(statement.count(" ~ ") for statement in statements)
    statements.clear()
    # 11 раз — 98 знаков: длиннее запрос обрезается на сотом знаке, и хвост
    # («Повт») честно становится вторым словом.
    repeated = site_search(db_session, " ".join(["Повторов"] * 11))["people"]
    repeated_regexes = max(statement.count(" ~ ") for statement in statements)

    assert [person["display_name"] for person in single] == ["Повторов Словович"]
    assert repeated == single
    assert repeated_regexes == single_regexes


def test_statement_timeout_gives_locations_without_people(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Запрос ушёл в долгий план — Postgres его отменяет, а выдача остаётся: локации есть, людей нет, не 500."""
    _fake_index(monkeypatch)
    monkeypatch.setattr(participant_search_service, "SEARCH_STATEMENT_TIMEOUT_MS", 50)

    def slow_people(db: Session, *_args, **_kwargs):  # noqa: ANN202
        db.execute(text("SELECT pg_sleep(2)"))
        return [], False

    monkeypatch.setattr(site_search_service, "search_people", slow_people)

    page = site_search(db_session, "сокольники")

    assert page["people"] == []
    assert page["people_skipped"] is True
    assert page["locations"][0]["slug"] == "sokolniki"
    # Транзакцию откатили — сессия снова в строю.
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_locations_come_only_from_cache(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Каталога в Redis нет — поиск его не считает (6 с на запрос), а отдаёт людей без локаций (SRCH-COLD-2)."""

    def forbidden(*_args, **_kwargs):  # noqa: ANN202
        raise AssertionError("каталог локаций не должен считаться в запросе поиска")

    monkeypatch.setattr(location_page_service, "build_locations_index", forbidden)
    monkeypatch.setattr(location_page_service, "_compute_locations_index", forbidden)
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Холодов Кэшевич")

    page = site_search(db_session, "холодов")

    assert page["locations"] == []
    assert [person["display_name"] for person in page["people"]] == ["Холодов Кэшевич"]

    fake_redis.set(
        location_page_service.LOCATIONS_INDEX_CACHE_KEY,
        json.dumps(
            {
                "items": [
                    {"slug": "holodny", "name": "Холодный парк", "city": "Холодногорск",
                     "platform_codes": ["five_verst"], "events_count": 1, "finishers_total": 1}
                ],
                "series": [],
            }
        ),
    )
    assert [item["slug"] for item in site_search(db_session, "холодный")["locations"]] == ["holodny"]


def test_api_search_survives_nul_byte(client: TestClient, db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Нуляев Байтович")

    for query in ("ab\x00cd", "\x00\x00\x00", "нуля\x00ев"):
        response = client.get("/api/search", params={"q": query})
        assert response.status_code == 200, query
    assert [person["display_name"] for person in response.json()["people"]] == ["Нуляев Байтович"]


def test_log_beacon_with_nul_byte_is_recorded_clean(client: TestClient, db_session: Session) -> None:
    db_session.query(SearchQueryLog).delete()
    response = client.post(
        "/api/search/log",
        content='{"query": "Соколь\\u0000ники", "clicked_kind": "location", "clicked_target": "/locations/\\u0000sokolniki"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 204
    rows = _log_rows(db_session)
    assert [(row.query, row.clicked_target) for row in rows] == [("сокольники", "/locations/sokolniki")]


def test_search_gives_its_slot_back(client: TestClient, db_session: Session, no_locations: None) -> None:
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Слотов Вернувший")
    assert client.get("/api/search", params={"q": "слотов"}).json()["people"]

    slots = site_search_service._people_slots
    taken = [slots.acquire(blocking=False) for _ in range(site_search_service.PEOPLE_SEARCH_SLOTS)]
    for ok in taken:
        if ok:
            slots.release()
    assert all(taken)


def test_busy_search_slots_give_locations_and_say_people_were_skipped(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """Все места поиска заняты залпом: запрос ждёт недолго и честно говорит «людей не искали» (SKEP-2, SKEP-3)."""
    _fake_index(monkeypatch)
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    monkeypatch.setattr(site_search_service, "_people_slots", busy)
    monkeypatch.setattr(site_search_service, "PEOPLE_SLOT_WAIT_SECONDS", 0.05)
    called: list[bool] = []
    monkeypatch.setattr(site_search_service, "search_people", lambda *_a, **_k: called.append(True) or ([], False))

    body = client.get("/api/search", params={"q": "сокольники"}).json()
    assert body["locations"][0]["slug"] == "sokolniki"
    assert body["people"] == []
    assert body["people_skipped"] is True
    # Людей не искали — значит, и «ничего не нашлось» не установлено: ни
    # другой раскладки, ни «похожих локаций» на запрос, который мог быть именем.
    typo = client.get("/api/search", params={"q": "сокольнеки"}).json()
    assert typo["people_skipped"] is True
    assert typo["locations_similar"] is False
    assert typo["locations"] == []
    # Запросу, которому люди не нужны, место и не требуется — пропуском он не считается.
    abbreviation = client.get("/api/search", params={"q": "мск"}).json()
    assert abbreviation["people_skipped"] is False
    assert abbreviation["locations"]
    assert called == []


def test_people_skip_log_is_throttled(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Пропуск поиска людей — в лог раз в минуту со счётчиком, а не строкой на каждый (SKEP-3)."""
    monkeypatch.setattr(site_search_service, "_skip_logged_at", None)
    monkeypatch.setattr(site_search_service, "_skip_counts", {})

    def logged() -> list[str]:
        return [record.getMessage() for record in caplog.records if "people search skipped" in record.getMessage()]

    with caplog.at_level(logging.WARNING, logger=site_search_service.logger.name):
        for _ in range(50):
            site_search_service._note_people_skip("no_slot")
        assert logged() == ["site search: people search skipped (no_slot=1)"]

        monkeypatch.setattr(site_search_service, "_skip_logged_at", time.monotonic() - 61)
        site_search_service._note_people_skip("statement_timeout")
        assert logged()[-1] == "site search: people search skipped (no_slot=49, statement_timeout=1)"


def test_log_ignores_searches_whose_people_were_skipped(client: TestClient, db_session: Session) -> None:
    """Людей не искали (people_skipped) — «не нашлось ничего» неправда, в журнал такой поиск не идёт."""
    db_session.query(SearchQueryLog).delete()
    for body in (
        '{"query": "попов дмитрий", "people_found": 0, "people_skipped": true}',
        '{"query": "сокольники", "locations_found": 1, "people_skipped": true,'
        ' "clicked_kind": "location", "clicked_target": "/locations/sokolniki"}',
        '{"query": "иван", "people_found": 20, "people_skipped": false}',
    ):
        response = client.post("/api/search/log", content=body, headers={"Content-Type": "text/plain"})
        assert response.status_code == 204
    assert [row.query for row in _log_rows(db_session)] == ["сокольники", "иван"]


def test_punctuation_only_query_does_not_search_people(db_session: Session, no_locations: None) -> None:
    """«%%%», «___», «№№№» — не слова: без триграмм они шли полным проходом по участникам (SKEP-4)."""
    five = _platform(db_session, "five_verst")
    _participant(db_session, five, "Знаков Препинаниев")

    statements = _capture_sql(db_session)
    for query in ("%%%", "___", "№№№", "--- ___ %%%"):
        page = site_search(db_session, query)
        assert page["people"] == [], query
        assert page["people_skipped"] is False, query
    assert not any("participants" in statement for statement in statements)

    # Знак по краю слова — опечатка, а не часть имени.
    people = site_search(db_session, "Знаков, Препинаниев.")["people"]
    assert [person["display_name"] for person in people] == ["Знаков Препинаниев"]


# ---------------------------------------------------------------------------
# «Имя + место»: малое место — от людей места, большое — от имени с честным
# пределом (ревью, SKEP-1)
# ---------------------------------------------------------------------------


def _namesakes(db_session: Session) -> tuple[list[str], str]:
    """Четыре «Частова» в Томске, два в Мытищах и «Зачастов» (только из середины слова) в Мытищах."""
    five = _platform(db_session, "five_verst")
    for name in ("Частов Томский", "Частов Северский", "Частов Кедровый", "Частов Асиновский"):
        _runs(db_session, five, _participant(db_session, five, name), 1, city="Томск")
    local = ["Частов Альфин", "Частов Бетин"]
    for name in local:
        _runs(db_session, five, _participant(db_session, five, name), 1, location_name="Мытищинский парк", city="Мытищи")
    middle = "Зачастов Серединкин"
    _runs(db_session, five, _participant(db_session, five, middle), 1, location_name="Мытищинский парк", city="Мытищи")
    return local, middle


def test_small_place_checks_every_namesake(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    """«Анна Мытищи»: у малого места проверяются все тёзки, а не первые из пула по имени.

    Пул в 1200 Анн (из 5551) находил в Мытищах семь из 57, подмешивал Сюзанн
    и Жанн и при этом говорил «показаны все».
    """
    _catalog(monkeypatch)
    # Предел проверки тёзок — только для большого места; малое идёт от людей места.
    monkeypatch.setattr(site_search_service, "PEOPLE_PLACE_SCAN_LIMIT", 1)
    local, middle = _namesakes(db_session)

    page = site_search(db_session, "частов мытищи")

    assert page["people_place"] == "Мытищи"
    names = [person["display_name"] for person in page["people"]]
    assert sorted(names[:2]) == sorted(local)
    assert names[2:] == [middle]
    assert page["people"][2]["partial"] is True
    assert page["people_truncated"] is False


@pytest.mark.parametrize("event_list_limit", [0, 4000])
def test_big_place_checks_namesakes_tier_by_tier(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, event_list_limit: int
) -> None:
    """Большое место: тёзки ярусами имени, место — у каждого (списком стартов или через события)."""
    _catalog(monkeypatch)
    monkeypatch.setattr(site_search_service, "PEOPLE_FROM_PLACE_MAX_FINISHERS", 0)
    monkeypatch.setattr(site_search_service, "PLACE_EVENT_LIST_LIMIT", event_list_limit)
    local, middle = _namesakes(db_session)

    page = site_search(db_session, "частов мытищи")

    names = [person["display_name"] for person in page["people"]]
    assert sorted(names[:2]) == sorted(local)
    assert names[2:] == [middle]
    assert page["people_truncated"] is False


def test_big_place_scan_limit_is_honest(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    """Предел проверки у большого места: «есть ещё», а нижний ярус не встаёт на место непроверенных тёзок."""
    _catalog(monkeypatch)
    monkeypatch.setattr(site_search_service, "PEOPLE_FROM_PLACE_MAX_FINISHERS", 0)
    local, _middle = _namesakes(db_session)

    # Шесть «Частовых» целым словом в предел влезают, «Зачастов» — уже нет.
    monkeypatch.setattr(site_search_service, "PEOPLE_PLACE_SCAN_LIMIT", 6)
    page = site_search(db_session, "частов мытищи")
    assert sorted(person["display_name"] for person in page["people"]) == sorted(local)
    assert page["people_truncated"] is True

    # Предел меньше яруса «целиком»: найденные — только из него, и «есть ещё»,
    # даже если среди проверенных в Мытищах не бегал никто.
    monkeypatch.setattr(site_search_service, "PEOPLE_PLACE_SCAN_LIMIT", 3)
    page = site_search(db_session, "частов мытищи")
    assert page["people_truncated"] is True
    assert page["people_place"] == "Мытищи"
    assert {person["display_name"] for person in page["people"]} <= set(local)
