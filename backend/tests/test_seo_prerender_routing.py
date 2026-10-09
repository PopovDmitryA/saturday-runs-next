"""Пререндер для роботов: один адрес у страницы, честные коды и разделы с текстом.

Правки 10.2026 по выгрузкам Вебмастера (03.10.2026): дубли адресов локаций и
профилей, 404 на живых вкладках, пустые /results и главная, sitemap без lastmod,
адрес почты в заголовке профиля.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.display_name import has_email, strip_email
from app.services import seo_service
from app.services.location_page_service import identity_primary_slug
from app.services.seo_service import (
    Prerendered,
    _as_date,
    _home_body,
    _ratings_body,
    _results_body,
    _saturday_of_week,
    build_location_meta,
    build_profile_meta,
    build_protocol_meta,
    build_robots_txt,
    build_sitemap,
    is_known_path,
    is_thin_series,
    render_prerendered_page,
    resolve_page_meta,
)


class _FakeDb:
    def __init__(self) -> None:
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "slug": "kuzminki",
        "name": "Кузьминки",
        "city": "Москва",
        "platforms": [{"platform_code": "five_verst", "is_active": True, "events_count": 271}],
        "stats": {"events_count": 271, "finishers_total": 40123},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def location_page(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """build_location_page отвечает основным слагом kuzminki на любой вариант."""
    state: dict[str, Any] = {"payload": _payload(), "calls": []}

    def fake_build(_db: Any, slug: str) -> dict[str, Any] | None:
        state["calls"].append(slug)
        if "error" in state:
            raise state["error"]
        return state["payload"]

    monkeypatch.setattr(seo_service, "build_location_page", fake_build)
    return state


def _render(path: str, db: Any | None = None) -> Prerendered:
    return render_prerendered_page(db or _FakeDb(), path)


def test_location_alias_redirects_to_primary_slug(location_page: dict[str, Any]) -> None:
    """Любой вариант слага — 301 на основной, а не 200 с canonical на себя."""
    page = _render("/locations/kuzminki-park/events")
    assert page.status == 301
    assert page.location is not None
    assert page.location.endswith("/locations/kuzminki/events")


def test_primary_slug_renders_with_canonical(location_page: dict[str, Any]) -> None:
    page = _render("/locations/kuzminki")
    assert page.status == 200
    assert '<link rel="canonical" href="' in page.html
    assert "/locations/kuzminki\">" in page.html
    assert 'content="index,follow"' in page.html


@pytest.mark.parametrize("suffix", ["/tops", "/weather"])
def test_location_tabs_are_200_noindex(location_page: dict[str, Any], suffix: str) -> None:
    """Живые вкладки локации: роботу больше не 404, но и в выдачу не просятся."""
    assert is_known_path(f"/locations/kuzminki{suffix}")
    page = _render(f"/locations/kuzminki{suffix}")
    assert page.status == 200
    assert 'content="noindex,follow"' in page.html
    assert '<a href="/locations/kuzminki">' in page.html


def test_database_failure_is_503_not_404(location_page: dict[str, Any]) -> None:
    """Сбой базы — «зайди позже», иначе Яндекс выкидывает живую страницу."""
    location_page["error"] = RuntimeError("db down")
    db = _FakeDb()
    page = _render("/locations/kuzminki", db)
    assert page.status == 503
    assert page.retry_after
    assert db.rolled_back


def test_unknown_location_is_404(location_page: dict[str, Any]) -> None:
    location_page["payload"] = None
    assert _render("/locations/nowhere").status == 404


def test_renamed_location_redirects_without_lookup(location_page: dict[str, Any]) -> None:
    page = _render("/locations/kurskboevadacha")
    assert page.status == 301
    assert page.location is not None and page.location.endswith("/locations/boevadacha")
    assert location_page["calls"] == []


def test_unified_protocol_friday_redirects_to_saturday() -> None:
    """Пятничные адреса недели наплодил баг ссылки — склеиваем их с субботой."""
    assert _saturday_of_week(date(2026, 9, 18)) == date(2026, 9, 19)
    assert _saturday_of_week(date(2026, 9, 19)) == date(2026, 9, 19)
    assert _saturday_of_week(date(2026, 9, 14)) == date(2026, 9, 19)
    page = _render("/protocol/2026-09-18")
    assert page.status == 301
    assert page.location is not None and page.location.endswith("/protocol/2026-09-19")


def test_profile_number_redirects_to_nickname(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(public_slug="ivan", serial_id=None, display_name="Иван Петров", profile_private=False)
    monkeypatch.setattr("app.services.profile_slug_service.resolve_profile_handle", lambda _db, _h: user)
    page = _render("/users/1332")
    assert page.status == 301
    assert page.location is not None and page.location.endswith("/users/ivan")


def test_missing_profile_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.profile_slug_service.resolve_profile_handle", lambda _db, _h: None)
    assert _render("/users/nobody").status == 404


def test_email_never_reaches_profile_title() -> None:
    user = SimpleNamespace(display_name="ivan.petrov@company.ru")
    meta = build_profile_meta(user, {"stats": {"total_runs": 12}})
    assert "@" not in meta.title
    assert meta.title.startswith("ivan.petrov")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ivan.petrov@company.ru", "ivan.petrov"),
        ("Иван Петров", "Иван Петров"),
        ("@ivan_tg", "@ivan_tg"),
        (None, None),
    ],
)
def test_strip_email(raw: str | None, expected: str | None) -> None:
    assert strip_email(raw) == expected


def test_has_email() -> None:
    assert has_email("a.kor90@mail.ru")
    assert not has_email("Анна Корнеева")


def test_robots_closes_service_sections_and_cleans_params() -> None:
    robots = build_robots_txt()
    for closed in ("/organizer", "/auth/", "/welcome", "/backlog", "/demo", "/d/"):
        assert f"Disallow: {closed}\n" in robots
    assert "Clean-param: oauth_error&link_error /login" in robots
    assert "utm_source" in robots
    # Посадочная под «личный кабинет» по-прежнему открыта.
    assert "Disallow: /login" not in robots


def test_thin_series_is_noindex() -> None:
    one_off = _payload(name="Старты сообществ", is_series=True, stats={"events_count": 1})
    assert is_thin_series(one_off)
    assert build_location_meta(one_off).indexable is False
    regular = _payload(is_series=True, stats={"events_count": 12})
    assert build_location_meta(regular).indexable is True


def test_platform_is_not_repeated_when_name_starts_with_it() -> None:
    meta = build_location_meta(
        _payload(name="С95 и друзья", platforms=[{"platform_code": "s95", "is_active": True}], city=None)
    )
    assert "С95 С95" not in meta.title
    assert meta.title.startswith("С95 и друзья")


def test_location_title_says_extended_results() -> None:
    meta = build_location_meta(_payload())
    assert meta.title == "5 вёрст Кузьминки, Москва — расширенные результаты — run5k.run"


def test_protocol_title_names_system_and_fits_budget() -> None:
    meta = build_protocol_meta(
        {
            "name": "Парк Горького",
            "platform_code": "five_verst",
            "event_number": 214,
            "event_date": "2026-09-19",
            "summary": {"finishers": 512, "volunteers": 30},
        }
    )
    assert meta.title.startswith("5 вёрст Парк Горького №214 — ")
    assert "результаты 19.09.2026" in meta.title
    assert len(meta.title) <= seo_service.TITLE_BUDGET
    assert meta.description.startswith("Расширенные результаты старта 5 вёрст «Парк Горького»")


def _last_results() -> dict[str, Any]:
    return {
        "saturday_date": "2026-10-03",
        "items": [
            {
                "slug": "gorkypark",
                "name": "Парк Горького",
                "city": "Москва",
                "event_platform_code": "five_verst",
                "event_date": "2026-10-03",
                "event_number": 216,
                "is_last_saturday": True,
                "finishers": 480,
                "volunteers": 25,
                "best_male_time_display": "00:15:59",
                "best_female_time_display": "00:18:10",
                "has_protocol": True,
            },
            {
                "slug": "likhoslavl",
                "name": "Лихославль",
                "city": "Лихославль",
                "event_platform_code": "five_verst",
                "event_date": "2026-09-26",
                "is_last_saturday": False,
                "finishers": 20,
                "has_protocol": True,
            },
        ],
    }


def test_results_body_lists_locations_with_protocol_links() -> None:
    body = _results_body(resolve_page_meta("/results"), _last_results())
    assert '<a href="/locations/gorkypark">5 вёрст Парк Горького</a>' in body
    assert '/locations/gorkypark/protocol/five_verst/2026-10-03">расширенные результаты</a>' in body
    assert "старт №216" in body
    assert "Последний старт раньше" in body
    assert "26 сентября" in body


def test_home_and_ratings_bodies_have_links() -> None:
    home = _home_body(resolve_page_meta("/"), _last_results())
    assert '<a href="/results">' in home
    assert '<a href="/locations/gorkypark">' in home
    ratings = _ratings_body(resolve_page_meta("/ratings"))
    assert '<a href="/ratings/runs">' in ratings
    # Закрытая фича (трассы) в хаб для робота не попадает.
    assert "/ratings/courses" not in ratings


def test_every_robot_page_carries_site_navigation(location_page: dict[str, Any]) -> None:
    page = _render("/locations/kuzminki")
    assert "<nav>" in page.html
    assert '<a href="/results">' in page.html


def test_as_date_reads_cached_strings() -> None:
    assert _as_date("2026-09-27") == date(2026, 9, 27)
    assert _as_date(date(2026, 9, 27)) == date(2026, 9, 27)
    assert _as_date(None) is None
    assert _as_date("мусор") is None


def test_sitemap_keeps_cancelled_and_sets_lastmod(monkeypatch: pytest.MonkeyPatch) -> None:
    """lastmod из строки кэша; отменённый ближайший старт не выкидывает локацию."""
    index = {
        "items": [
            {"slug": "likhoslavl", "identity_key": "a", "is_cancelled": True, "last_event_date": "2026-09-26"},
            {"slug": "gorkypark", "identity_key": "b", "last_event_date": "2026-10-03"},
        ],
        "series": [{"slug": "bs", "identity_key": "c", "is_series": True, "events_count": 1}],
    }
    monkeypatch.setattr(seo_service, "build_locations_index", lambda _db: index)
    monkeypatch.setattr(seo_service, "paginate_published_releases", lambda _db: SimpleNamespace(pages=1))
    monkeypatch.setattr(
        seo_service,
        "_recent_protocol_paths",
        lambda _db, _items: [("/locations/gorkypark/protocol/five_verst/2026-10-03", date(2026, 10, 3))],
    )
    xml = build_sitemap(_FakeDb())  # type: ignore[arg-type]
    assert "/locations/likhoslavl</loc>" in xml
    assert "<lastmod>2026-10-03</lastmod>" in xml
    assert "/locations/bs</loc>" not in xml
    assert "/protocol/five_verst/2026-10-03</loc>" in xml


def _loc(key: str, official: bool = True) -> SimpleNamespace:
    return SimpleNamespace(external_key=key, is_official_map=official)


def test_identity_primary_slug_matches_catalog_rule() -> None:
    """Один основной адрес: витринная строка действующей системы, не parkrun."""
    assert identity_primary_slug([(_loc("ryazan-central"), "parkrun"), (_loc("ryazancentral"), "runpark")]) == (
        "ryazancentral"
    )
    assert identity_primary_slug([(_loc("old", official=False), "five_verst"), (_loc("new"), "s95")]) == "new"
    assert identity_primary_slug([(_loc("Starye-Sady"), "parkrun")]) == "starye-sady"


def test_weather_meta_is_known_but_not_indexable() -> None:
    assert resolve_page_meta("/locations/kuzminki/weather").indexable is False
