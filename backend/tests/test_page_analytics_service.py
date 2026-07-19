from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import PageStatsDaily, PageViewEvent, User
from app.services.page_analytics_service import (
    EARLIEST_STATS_DATE,
    STATS_TIMEZONE,
    build_page_analytics,
    classify_page,
    cleanup_old_events,
    local_today,
    record_page_leave,
    record_page_view,
    resolve_period,
    rollup_day,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", ("portal_home", "")),
        ("/about", ("portal_about", "")),
        ("/login", ("portal_login", "")),
        ("/dashboard", ("dashboard", "")),
        ("/profiles", ("dashboard", "")),
        ("/runs?page=2", ("runs", "")),
        ("/runs/", ("runs", "")),
        ("/users/ivan", ("profile", "ivan")),
        ("/users/12345", ("profile", "12345")),
        ("/locations", ("locations_index", "")),
        ("/locations/kuzminki", ("location", "kuzminki")),
        ("/locations/kuzminki/events", ("location_events", "kuzminki")),
        ("/ratings", ("ratings_hub", "")),
        ("/ratings/runs", ("ratings_runs", "")),
        ("/new", ("redirect", "/new")),
        ("/new/about", ("redirect", "/new/about")),
        ("/new/map-lab", ("portal_map_lab", "")),
        # Демо — одной строкой; подстраница сохраняется в entity_key на будущее.
        ("/demo", ("demo", "")),
        ("/demo/runs", ("demo", "runs")),
        ("/admin/users", ("admin", "users")),
        ("/admin", ("redirect", "/admin")),
        ("/sync", ("redirect", "/sync")),
        ("/oauth/vk/callback", ("oauth_callback", "")),
        ("/something-strange", ("other", "/something-strange")),
    ],
)
def test_classify_page(path: str, expected: tuple[str, str]) -> None:
    assert classify_page(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        # Пути, которые фронт отдаёт Grafana (isLegacyGrafanaPath в siteBrand.ts).
        # Важен слэш: "/dashboard/" — Grafana, "/dashboard" — наш личный кабинет.
        "/d/ce5xtszxy4074e/glavnaja",
        "/dashboard/",
        "/public/some-asset",
    ],
)
def test_classify_legacy_grafana_paths(path: str) -> None:
    page_type, entity_key = classify_page(path)
    assert page_type == "legacy_grafana"
    assert entity_key == path


# Все маршруты приложения — копия STATIC_ROUTES + динамических веток renderRoute
# из frontend/src/App.tsx. Страховка от «добавили раздел, забыли в аналитике»:
# новый роут без записи в _STATIC_PAGE_TYPES свалится в "other" и уронит тест.
APP_ROUTES = [
    "/",
    "/new",
    "/new/about",
    "/new/login",
    "/new/map-lab",
    "/login",
    "/oauth/yandex/callback",
    "/oauth/vk/callback",
    "/demo",
    "/demo/runs",
    "/demo/co-runners",
    "/demo/volunteering",
    "/demo/maps",
    "/demo/history",
    "/dashboard",
    "/profiles",
    "/runs",
    "/achievements",
    "/co-runners",
    "/volunteering",
    "/maps",
    "/locations",
    "/history",
    "/ratings",
    "/ratings/runs",
    "/ratings/volunteering",
    "/ratings/locations",
    "/share",
    "/sync",
    "/queue",
    "/admin",
    "/admin/queue",
    "/admin/users",
    "/admin/abuse",
    "/admin/profile-slugs",
    "/admin/stats",
    "/admin/page-analytics",
    "/admin/ratings",
    "/admin/event-report",
    "/admin/records-digest",
    "/admin/location-contacts",
    "/settings",
    "/about",
    "/users/ivan",
    "/locations/kuzminki",
    "/locations/kuzminki/events",
]


@pytest.mark.parametrize("path", APP_ROUTES)
def test_every_app_route_is_classified(path: str) -> None:
    page_type, _entity_key = classify_page(path)
    assert page_type != "other", f"Раздел {path} не попадает в статистику — уходит в «Прочее»"


def test_user_facing_pages_have_distinct_page_types() -> None:
    """Каждая пользовательская страница — своя строка в статистике.

    Исключения — маршруты, которым отдельная строка не нужна: /admin/* и
    /demo/* сведены в одну строку каждый (внутренняя админка и витрина целиком —
    решение Дмитрия 17.07.2026); /profiles рисует тот же компонент, что
    /dashboard; /sync, /queue, /admin и /new* (старые адреса тёмного запуска
    портала) — заглушки-редиректы; оба /oauth/*/callback — одна страница
    OAuthCallbackPage с разным провайдером.
    """
    not_own_page = {
        "/profiles",
        "/sync",
        "/queue",
        "/admin",
        "/new",
        "/new/about",
        "/new/login",
        "/oauth/yandex/callback",
    }
    user_facing = [
        p
        for p in APP_ROUTES
        if not p.startswith(("/admin/", "/demo/")) and p not in not_own_page
    ]
    page_types = [classify_page(path)[0] for path in user_facing]
    assert len(page_types) == len(set(page_types)), "Разные страницы делят один page_type"


def test_resolve_period_defaults_to_last_n_days() -> None:
    today = local_today()

    start, end = resolve_period(period_days=7, date_from=None, date_to=None)
    assert end == today
    assert start == today - timedelta(days=6)  # 7 дней включая сегодня

    start, end = resolve_period(period_days=1, date_from=None, date_to=None)
    assert (start, end) == (today, today)

    # Без параметров вообще — 30 дней.
    start, end = resolve_period(period_days=None, date_from=None, date_to=None)
    assert start == today - timedelta(days=29)


def test_resolve_period_explicit_dates_win_over_period_days() -> None:
    start, end = resolve_period(
        period_days=365,
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 10),
    )
    assert (start, end) == (date(2026, 7, 1), date(2026, 7, 10))


def test_resolve_period_open_ended_and_swapped_bounds() -> None:
    today = local_today()

    # Только «с» — по сегодня.
    start, end = resolve_period(period_days=None, date_from=date(2026, 7, 5), date_to=None)
    assert (start, end) == (date(2026, 7, 5), today)

    # Только «по» — с самого начала данных.
    start, end = resolve_period(period_days=None, date_from=None, date_to=date(2026, 7, 5))
    assert (start, end) == (EARLIEST_STATS_DATE, date(2026, 7, 5))

    # Границы перепутаны местами — молча меняем, а не отдаём пустоту.
    start, end = resolve_period(
        period_days=None, date_from=date(2026, 7, 20), date_to=date(2026, 7, 10)
    )
    assert (start, end) == (date(2026, 7, 10), date(2026, 7, 20))


def test_build_page_analytics_respects_explicit_range(db_session: Session) -> None:
    entity_key = f"test-{uuid4()}"
    for day, views in ((date(2026, 7, 10), 3), (date(2026, 7, 20), 5)):
        db_session.add(
            PageStatsDaily(
                date=day,
                page_type="location",
                entity_key=entity_key,
                views=views,
                unique_viewers=views,
            )
        )
    db_session.commit()

    payload = build_page_analytics(db_session, start=date(2026, 7, 1), end=date(2026, 7, 15))

    assert payload["date_from"] == date(2026, 7, 1)
    assert payload["date_to"] == date(2026, 7, 15)
    mine = [row for row in payload["top_locations"] if row["entity_key"] == entity_key]  # type: ignore[index,union-attr]
    assert len(mine) == 1
    assert mine[0]["views"] == 3  # день 20.07 за границей диапазона


def _make_user(db_session: Session, *, slug: str | None = None) -> User:
    user = User(display_name="Тест", public_slug=slug)
    db_session.add(user)
    db_session.commit()
    return user


def test_record_profile_view_resolves_owner_and_self(db_session: Session) -> None:
    owner = _make_user(db_session, slug="testrunner")
    viewer = _make_user(db_session)

    record_page_view(
        db_session,
        view_id=uuid4(),
        path="/users/testrunner",
        visitor_key=f"u:{viewer.id}",
        viewer_user_id=viewer.id,
    )
    record_page_view(
        db_session,
        view_id=uuid4(),
        path="/users/testrunner",
        visitor_key=f"u:{owner.id}",
        viewer_user_id=owner.id,
    )

    events = (
        db_session.query(PageViewEvent)
        .filter(PageViewEvent.page_type == "profile", PageViewEvent.entity_key == str(owner.id))
        .order_by(PageViewEvent.id)
        .all()
    )
    assert len(events) == 2
    assert events[0].is_self is False
    assert events[1].is_self is True


def test_record_duplicate_view_id_is_ignored(db_session: Session) -> None:
    view_id = uuid4()
    for _ in range(2):
        record_page_view(
            db_session,
            view_id=view_id,
            path="/runs",
            visitor_key="a:abc12345",
            viewer_user_id=None,
        )
    count = db_session.query(PageViewEvent).filter(PageViewEvent.view_id == view_id).count()
    assert count == 1


def test_page_leave_keeps_max_duration(db_session: Session) -> None:
    view_id = uuid4()
    record_page_view(
        db_session, view_id=view_id, path="/runs", visitor_key="a:abc12345", viewer_user_id=None
    )
    record_page_leave(db_session, view_id=view_id, duration_sec=30)
    record_page_leave(db_session, view_id=view_id, duration_sec=10)
    event = db_session.query(PageViewEvent).filter(PageViewEvent.view_id == view_id).one()
    assert event.duration_sec == 30


def test_rollup_day_aggregates_and_is_idempotent(db_session: Session) -> None:
    today = local_today()
    ts = datetime.now(STATS_TIMEZONE)
    # Уникальный entity_key: rollup сворачивает ВСЕ события дня из БД (включая
    # настоящий трафик дев/прод-стенда), поэтому проверяем только свою группу.
    entity_key = f"test-{uuid4()}"
    for visitor, duration in (("a:one11111", 20), ("a:one11111", None), ("a:two22222", 40)):
        db_session.add(
            PageViewEvent(
                view_id=uuid4(),
                ts=ts,
                path=f"/locations/{entity_key}",
                page_type="location",
                entity_key=entity_key,
                visitor_key=visitor,
                duration_sec=duration,
            )
        )
    db_session.commit()

    for _ in range(2):  # повторный прогон не задваивает
        rollup_day(db_session, today)

    row = (
        db_session.query(PageStatsDaily)
        .filter(PageStatsDaily.date == today, PageStatsDaily.entity_key == entity_key)
        .one()
    )
    assert row.views == 3
    assert row.unique_viewers == 2
    assert row.total_duration_sec == 60
    assert row.duration_views == 2


def test_cleanup_old_events(db_session: Session) -> None:
    old_ts = datetime.now(STATS_TIMEZONE) - timedelta(days=120)
    db_session.add(
        PageViewEvent(
            view_id=uuid4(),
            ts=old_ts,
            path="/runs",
            page_type="runs",
            entity_key="",
            visitor_key="a:old111111",
        )
    )
    fresh_id = uuid4()
    db_session.add(
        PageViewEvent(view_id=fresh_id, path="/runs", page_type="runs", entity_key="", visitor_key="a:new111111")
    )
    db_session.commit()

    deleted = cleanup_old_events(db_session, retention_days=90)

    assert deleted >= 1
    remaining = db_session.query(PageViewEvent).filter(PageViewEvent.view_id == fresh_id).count()
    assert remaining == 1
