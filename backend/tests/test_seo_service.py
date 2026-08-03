from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.abuse_protection import RouteTier, classify_route
from app.services.seo_service import (
    DEFAULT_DESCRIPTION,
    DEFAULT_TITLE,
    STATIC_PAGE_META,
    PageMeta,
    build_location_meta,
    build_robots_txt,
    normalize_path,
    resolve_page_meta,
)

# Фронт лежит рядом с бэкендом в репозитории и примонтирован в /frontend-src
# внутри контейнера — так же, как у сторожа роутов в test_page_analytics_service.
_FRONTEND_SRC_CANDIDATES = (
    Path("/frontend-src"),
    Path(__file__).resolve().parents[2] / "frontend" / "src",
)


def _frontend_src() -> Path:
    for candidate in _FRONTEND_SRC_CANDIDATES:
        if (candidate / "App.tsx").exists():
            return candidate
    raise AssertionError(
        "Не найден App.tsx — сторож мета-тегов не может работать. "
        f"Искали в: {', '.join(str(c) for c in _FRONTEND_SRC_CANDIDATES)}"
    )


def _static_routes_from_app() -> list[str]:
    """Ключи STATIC_ROUTES из App.tsx (литералы + константы portalRoutes.ts)."""
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

    routes = re.findall(r'^\s*"([^"]+)"\s*:', block, re.M)
    for name in re.findall(r"^\s*\[([A-Z0-9_]+)\]\s*:", block, re.M):
        value = consts.get(name)
        assert value is not None, f"Константа {name} не найдена в portalRoutes.ts"
        routes.append(value)
    assert len(routes) > 20, "Разбор STATIC_ROUTES сломался — роутов подозрительно мало"
    return sorted(set(routes))


APP_ROUTES = _static_routes_from_app()


@pytest.mark.parametrize("path", APP_ROUTES)
def test_every_app_route_has_meta(path: str) -> None:
    """Новый роут обязан получить свой заголовок вкладки.

    Иначе страница молча уедет на дефолтный «run5k.run — статистика субботних
    пробежек», и во вкладке браузера все разделы станут одинаковыми — ровно то
    состояние, из которого эта фича и вытаскивает сайт.
    """
    meta = resolve_page_meta(path)
    # Корень законно совпадает с дефолтом — он и есть главная страница.
    if path == "/":
        return
    assert meta.title != DEFAULT_TITLE, (
        f"Роут {path} есть в App.tsx, но своих мета-тегов в seo_service.py у него "
        "нет — страница получит дефолтный заголовок главной"
    )


def _ts_static_paths() -> set[str]:
    """Ключи STATIC_PAGE_META из frontend/src/lib/pageMeta.ts."""
    src = (_frontend_src() / "lib" / "pageMeta.ts").read_text(encoding="utf-8")
    block_start = src.index("export const STATIC_PAGE_META")
    block_end = src.index("\n};", block_start)
    literals = set(re.findall(r'^\s*"([^"]+)":\s*\{', src[block_start:block_end], re.M))

    redirects_start = src.index("const REDIRECT_PATHS")
    redirects_end = src.index("];", redirects_start)
    literals |= set(re.findall(r'"([^"]+)"', src[redirects_start:redirects_end]))
    assert len(literals) > 20, "Разбор pageMeta.ts сломался — адресов подозрительно мало"
    return literals


def test_frontend_mirror_covers_same_paths() -> None:
    """Клиентское зеркало и серверный канон описывают один и тот же набор.

    Мета-теги живут в двух местах не от хорошей жизни: роботу их отдаёт
    пререндер (Python), человеку — SPA (TypeScript). Разъедутся — у робота и
    у пользователя будут разные заголовки одной страницы.
    """
    assert _ts_static_paths() == set(STATIC_PAGE_META), (
        "Набор адресов в frontend/src/lib/pageMeta.ts разошёлся с "
        "STATIC_PAGE_META в backend/app/services/seo_service.py"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/", "/"),
        ("/about/", "/about"),
        ("/locations/kuzminki?tab=1", "/locations/kuzminki"),
        ("/locations/kuzminki#records", "/locations/kuzminki"),
        ("locations", "/locations"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    ("path", "indexable"),
    [
        ("/", True),
        ("/about", True),
        ("/locations", True),
        ("/locations/kuzminki", True),
        ("/locations/kuzminki/events", True),
        ("/ratings/wins", True),
        # Личные страницы участников и мировой обход в индекс не идут —
        # решение Дмитрия 02.08.2026.
        ("/users/ivan", False),
        ("/users/ivan/runs", False),
        ("/world", False),
        ("/hq/hq-2kl5kfrlzmnvn8sc", False),
        ("/login", False),
        ("/backlog", False),
        ("/admin/users", False),
        ("/settings", False),
    ],
)
def test_indexable_flags(path: str, indexable: bool) -> None:
    assert resolve_page_meta(path).indexable is indexable


def test_unknown_path_falls_back_to_default() -> None:
    meta = resolve_page_meta("/something-strange")
    assert meta == PageMeta(title=DEFAULT_TITLE, description=DEFAULT_DESCRIPTION)
    assert meta.indexable is False


def test_every_page_title_is_distinct_enough() -> None:
    """Заголовки разделов не должны совпадать друг с другом.

    Исключения перечислены явно: оба OAuth-колбэка — один экран, а заглушки
    кабинета законно делят «Личный кабинет».
    """
    shared = {"Вход — run5k.run", "Личный кабинет — run5k.run"}
    titles = [m.title for p, m in STATIC_PAGE_META.items() if m.title not in shared]
    assert len(titles) == len(set(titles)), "Два разных раздела делят один заголовок вкладки"


def test_location_meta_uses_name_and_numbers() -> None:
    payload = {
        "slug": "kuzminki",
        "name": "Кузьминки",
        "city": "Москва",
        "stats": {
            "events_count": 271,
            "finishers_total": 40123,
            "course_records": {
                "male": {"finish_time_display": "00:15:42"},
                "female": {"finish_time_display": "00:18:03"},
            },
        },
    }
    meta = build_location_meta(payload)
    assert meta.title.startswith("Кузьминки, Москва")
    assert "271 старт" in meta.description
    assert "40123 финиша" in meta.description
    assert "15:42 / 18:03" in meta.description
    assert len(meta.description) <= 160, "Описание длиннее — поисковик обрежет"
    assert meta.indexable is True

    log_meta = build_location_meta(payload, events_log=True)
    assert "журнал протоколов" in log_meta.title


def test_location_meta_survives_empty_stats() -> None:
    """Локация без единого старта не должна ронять сборку мета-тегов."""
    meta = build_location_meta({"slug": "new-place", "name": "Новая", "city": None, "stats": {}})
    assert "Новая" in meta.title
    assert meta.description


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 старт"), (2, "2 старта"), (5, "5 стартов"), (11, "11 стартов"), (21, "21 старт")],
)
def test_location_meta_pluralizes_starts(count: int, expected: str) -> None:
    meta = build_location_meta({"name": "X", "stats": {"events_count": count}})
    assert expected in meta.description


def test_robots_lists_sitemap_and_closes_service_paths() -> None:
    robots = build_robots_txt()
    assert "Sitemap: " in robots
    assert "/sitemap.xml" in robots
    for closed in ("/api/", "/admin", "/hq/", "/settings"):
        assert f"Disallow: {closed}" in robots


@pytest.mark.parametrize("path", ["/sitemap.xml", "/robots.txt", "/__prerender/locations/kuzminki"])
def test_seo_paths_are_exempt_from_rate_limit(path: str) -> None:
    """429 в адрес поисковика останавливает обход — эти адреса вне тарифов."""
    assert classify_route(path, "GET") is RouteTier.exempt
