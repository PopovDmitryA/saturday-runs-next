from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import PageStatsDaily, PageViewEvent, User
from app.services.page_analytics_service import (
    EARLIEST_STATS_DATE,
    STATS_TIMEZONE,
    build_home_link_clicks,
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
        ("/blog", ("portal_blog", "")),
        ("/updates", ("updates", "")),
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
        ("/locations/kuzminki/participants", ("location_participants", "kuzminki")),
        ("/ratings", ("ratings_hub", "")),
        ("/ratings/runs", ("ratings_runs", "")),
        ("/ratings/wins", ("ratings_wins", "")),
        ("/ratings/win-locations", ("ratings_win_locations", "")),
        ("/backlog", ("backlog", "")),
        # Вкладка профиля — тот же просмотр профиля: в entity_key хендл, чтобы
        # просмотр доресолвился до user_id и попал в «топ профилей».
        ("/users/ivan/maps", ("profile", "ivan")),
        ("/users/12345/achievements", ("profile", "12345")),
        ("/users/ivan/co-runners", ("profile", "ivan")),
        ("/hq/hq-2kl5kfrlzmnvn8sc", ("sweep_hq", "")),
        # Превью кабинета удалено (08.2026) — адрес падает в «прочее».
        ("/new/cabinet-preview", ("other", "/new/cabinet-preview")),
        # Старые адреса кабинета сами ничего не показывают — это редиректы.
        ("/new/dashboard", ("redirect", "/new/dashboard")),
        ("/new/maps", ("redirect", "/new/maps")),
        ("/dashboards", ("redirect", "/dashboards")),
        # Несуществующие адреса по-прежнему попадают в «прочее» — бакет живой.
        ("/new", ("other", "/new")),
        ("/new/about", ("other", "/new/about")),
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
# Роуты читаем из самого App.tsx, а не держим копию списка.
#
# Копия была, и она не сработала: добавить строку в APP_ROUTES забывали ровно
# так же, как в классификатор, — тест зеленел, а раздел молча уезжал в «Прочее».
# Так пропали /backlog и победные рейтинги (47 и 50 просмотров за 30 дней).
# Теперь список берётся из исходника: забыть его обновить невозможно.
# В контейнере фронт примонтирован в /frontend-src (см. docker-compose.yml),
# при запуске из репозитория — лежит рядом. Проверяем оба места.
_FRONTEND_SRC_CANDIDATES = (
    Path("/frontend-src"),
    Path(__file__).resolve().parents[2] / "frontend" / "src",
)


def _frontend_src() -> Path:
    for candidate in _FRONTEND_SRC_CANDIDATES:
        if (candidate / "App.tsx").exists():
            return candidate
    raise AssertionError(
        "Не найден App.tsx — сторож роутов не может работать. "
        f"Искали в: {', '.join(str(c) for c in _FRONTEND_SRC_CANDIDATES)}"
    )


def _static_routes_from_app() -> list[str]:
    """Ключи STATIC_ROUTES: строковые литералы и константы из portalRoutes.ts."""
    src = _frontend_src()
    app_src = (src / "App.tsx").read_text(encoding="utf-8")
    block_start = app_src.index("const STATIC_ROUTES")
    block_end = app_src.index("\n};", block_start)
    block = app_src[block_start:block_end]

    consts = dict(
        re.findall(
            r'export const ([A-Z0-9_]+)\s*=\s*"([^"]+)"',
            (src / "lib" / "portalRoutes.ts").read_text(encoding="utf-8"),
        )
    )

    routes: list[str] = []
    for literal in re.findall(r'^\s*"([^"]+)"\s*:', block, re.M):
        routes.append(literal)
    for name in re.findall(r"^\s*\[([A-Z0-9_]+)\]\s*:", block, re.M):
        value = consts.get(name)
        assert value is not None, f"Константа {name} не найдена в portalRoutes.ts"
        routes.append(value)
    assert len(routes) > 20, "Разбор STATIC_ROUTES сломался — роутов подозрительно мало"
    return sorted(set(routes))


APP_ROUTES = _static_routes_from_app()


@pytest.mark.parametrize("path", APP_ROUTES)
def test_every_app_route_is_classified(path: str) -> None:
    page_type, _entity_key = classify_page(path)
    assert page_type != "other", f"Раздел {path} не попадает в статистику — уходит в «Прочее»"


def test_user_facing_pages_have_distinct_page_types() -> None:
    """Каждая пользовательская страница — своя строка в статистике.

    Исключения — маршруты, которым отдельная строка не нужна: /admin/* и
    /demo/* сведены в одну строку каждый (внутренняя админка и витрина целиком —
    решение Дмитрия 17.07.2026); /profiles рисует тот же компонент, что
    /dashboard; заглушки-редиректы (/sync, /queue, /admin, старые адреса
    кабинета /new/*) своей страницы не имеют; оба /oauth/*/callback — одна
    страница OAuthCallbackPage с разным провайдером.
    """
    not_own_page = {"/profiles", "/oauth/yandex/callback"}
    user_facing = [
        p
        for p in APP_ROUTES
        if not p.startswith(("/admin/", "/demo/"))
        and p not in not_own_page
        # Заглушки-редиректы своей страницы не имеют по определению: /sync,
        # /queue, /admin и старые адреса кабинета /new/* сразу уводят на другой
        # адрес, поэтому делят один page_type законно.
        and classify_page(p)[0] != "redirect"
    ]
    page_types = [classify_page(path)[0] for path in user_facing]
    assert len(page_types) == len(set(page_types)), "Разные страницы делят один page_type"


def _hub_rating_links() -> list[str]:
    """Адреса карточек из хаба рейтингов (/ratings)."""
    src = _frontend_src()
    hub = (src / "features" / "leaderboards" / "LeaderboardsHubPage.tsx").read_text(
        encoding="utf-8"
    )
    links = sorted(set(re.findall(r'href:\s*"(/ratings/[^"]+)"', hub)))
    assert len(links) > 4, "Разбор карточек хаба сломался — ссылок подозрительно мало"
    return links


@pytest.mark.parametrize("href", _hub_rating_links())
def test_every_hub_link_has_route(href: str) -> None:
    """Карточка хаба обязана вести на живой роут.

    Обратная сторона сторожа выше: тот ловит «роут есть, но не заведён в
    аналитике», а этот — «ссылка есть, а роут потерян». Так 01.08.2026 при
    мерже табло /world из App.tsx пропала строка /ratings/volunteer-locations:
    карточка «Волонтёрский туризм» осталась на месте, бэкенд-метрика работала,
    а переход выдавал «Страница не найдена» — и ни один тест этого не заметил.
    """
    assert href in APP_ROUTES, (
        f"Карточка хаба ведёт на {href}, но такого роута нет в App.tsx — "
        "переход даст «Страница не найдена»"
    )


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


def test_blog_post_click_recorded(db_session: Session) -> None:
    """Клик по посту блога: одно событие на клик, источник ("/" или "/blog")
    в entity_key, u:-ключ для залогиненного. Какой именно пост — «Популярность»
    не различает: по-постовые клики уже считает clicks_count в админке блога."""
    from app.services.page_analytics_service import record_blog_post_click

    user = _make_user(db_session)
    before = db_session.query(PageViewEvent).filter(
        PageViewEvent.page_type == "blog_post_click"
    ).count()

    record_blog_post_click(
        db_session,
        path="/",
        visitor_key="a:anon-key-1",
        viewer_user_id=None,
    )
    record_blog_post_click(
        db_session,
        path="/blog?topic=Цифры",
        visitor_key="a:anon-key-2",
        viewer_user_id=user.id,
    )

    events = (
        db_session.query(PageViewEvent)
        .filter(PageViewEvent.page_type == "blog_post_click")
        .order_by(PageViewEvent.ts)
        .all()
    )
    assert len(events) == before + 2
    anon, logged = events[-2], events[-1]
    assert (anon.path, anon.entity_key) == ("/", "/")
    assert anon.visitor_key == "a:anon-key-1"
    # Источник нормализуется (query-string отрезается) и хранится в entity_key,
    # чтобы раскладка «с главной / из блога» переживала rollup.
    assert (logged.path, logged.entity_key) == ("/blog", "/blog")
    # Для залогиненного анонимный ключ заменён на u:<user_id>, как в pageview.
    assert logged.visitor_key == f"u:{user.id}"


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


def test_build_home_link_clicks_groups_and_labels(db_session: Session) -> None:
    """Переходы с главной: локации и профили в одной таблице, имена подставлены."""
    from app.services.ab_service import record_ab_event

    slug = f"link-loc-{uuid4().int % 1_000_000}"
    user = _make_user(db_session, slug=f"link-runner-{uuid4().int % 1_000_000}")
    for _ in range(2):
        record_ab_event(
            db_session,
            experiment="home_v1",
            variant="A",
            visitor_key="a:visitor-1",
            event_type="home_link_click",
            value=f"location:{slug}",
            path="/",
        )
    record_ab_event(
        db_session,
        experiment="home_v1",
        variant="B",
        visitor_key="a:visitor-2",
        event_type="home_link_click",
        value=f"runner:{user.public_slug}",
        path="/",
    )

    # Границы отчёта — календарные даты, ts события в UTC: под полночь по Москве
    # «сегодня» разъезжается на день, поэтому берём окно ±сутки.
    today = local_today()
    rows = build_home_link_clicks(
        db_session, start=today - timedelta(days=1), end=today + timedelta(days=1)
    )
    by_key = {row["entity_key"]: row for row in rows}

    # Локации нет в БД — метка остаётся слагом, но ссылка всё равно рабочая.
    assert by_key[slug]["kind"] == "location"
    assert by_key[slug]["clicks"] == 2
    assert by_key[slug]["visitors"] == 1
    assert by_key[slug]["href"] == f"/locations/{slug}"

    runner_row = by_key[user.public_slug]
    assert runner_row["kind"] == "runner"
    assert runner_row["label"] == "Тест"
    assert runner_row["href"] == f"/users/{user.public_slug}"
