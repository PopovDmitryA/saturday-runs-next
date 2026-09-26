"""Публичный поиск по сайту и журнал поисковых запросов.

Стережём то, что в поиске легко сломать незаметно: приватность (закрытый
профиль и «Иван П.» не должны вести на аккаунт, внешних ссылок на профили в
системах нет вовсе), склейку нескольких систем одного человека в одну строку
и приём бекона с телом text/plain.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.abuse_protection import RouteTier, classify_route
from app.db.session import get_db
from app.main import app
from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, SearchQueryLog, User
from app.services import site_search_service
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
    assert classify_route("/api/search/log", "POST") is RouteTier.search
    assert classify_route("/api/searching-else", "GET") is RouteTier.default


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


def test_people_need_three_chars(db_session: Session, no_locations: None) -> None:
    assert site_search(db_session, "ив")["people"] == []


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

    names = [person["display_name"] for person in site_search(db_session, "кимоглыз")["people"]]

    assert names[:2] == ["Кимоглыз Юлия", "Юлия Кимоглызовская"]
    assert set(names[2:]) == {"Хакимоглызов Рустам", "Екимоглыз Анна"}


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
