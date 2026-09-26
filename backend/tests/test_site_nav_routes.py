"""Сторож дерева навигации: каждая страница сайта где-то есть в меню.

Навигация сайта (рельс, колонка, нижняя панель, «Меню», шапка, подвал, поиск
по страницам) рисуется из одного дерева — frontend/src/features/portal/nav/
siteNav.ts. Страница, которую забыли туда вписать, не видна ни в одном меню и
не находится поиском, а на ней самой не подсвечивается раздел — так раньше
пропадали «Погода» и рейтинги.

Тест читает STATIC_ROUTES из App.tsx (как сторож аналитики в
test_page_analytics_service) и проверяет, что каждый адрес либо ведёт из
дерева (href), либо лежит под адресом раздела (SECTION_PATH_PREFIXES), либо
явно назван служебным ниже.

Упал — впишите страницу в siteNav.ts (с синонимами для поиска) или, если это
служебный адрес без меню, добавьте его в NOT_IN_NAV с объяснением.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# В контейнере фронт примонтирован в /frontend-src (см. docker-compose.yml),
# при запуске из репозитория — лежит рядом.
_FRONTEND_SRC_CANDIDATES = (
    Path("/frontend-src"),
    Path(__file__).resolve().parents[2] / "frontend" / "src",
)

# Адреса без места в меню — и почему.
NOT_IN_NAV: dict[str, str] = {
    "/login": "вход: кнопка «Войти» в шапке и раздел «Кабинет» у гостя",
    "/oauth/yandex/callback": "возврат от провайдера входа",
    "/oauth/vk/callback": "возврат от провайдера входа",
    "/auth/telegram/return": "возврат от Telegram",
    "/welcome": "онбординг сразу после первого входа",
    "/dashboard": "старый адрес кабинета — перенаправление",
    "/profiles": "старый адрес кабинета — перенаправление",
    "/runs": "старый адрес кабинета — перенаправление",
    "/achievements": "старый адрес кабинета — перенаправление",
    "/co-runners": "старый адрес кабинета — перенаправление",
    "/volunteering": "старый адрес кабинета — перенаправление",
    "/maps": "старый адрес кабинета — перенаправление",
    "/history": "старый адрес кабинета — перенаправление",
    "/sync": "старый адрес админки — перенаправление",
    "/queue": "старый адрес админки — перенаправление",
}


def _frontend_src() -> Path:
    for candidate in _FRONTEND_SRC_CANDIDATES:
        if (candidate / "App.tsx").exists():
            return candidate
    raise AssertionError(
        "Не найден App.tsx — сторож навигации не может работать. "
        f"Искали в: {', '.join(str(c) for c in _FRONTEND_SRC_CANDIDATES)}"
    )


def _string_consts(*sources: str) -> dict[str, str]:
    consts: dict[str, str] = {}
    for source in sources:
        consts.update(re.findall(r'export const ([A-Z0-9_]+)\s*=\s*"([^"]+)"', source))
    return consts


def _static_routes(src: Path, consts: dict[str, str]) -> list[str]:
    app_src = (src / "App.tsx").read_text(encoding="utf-8")
    block_start = app_src.index("const STATIC_ROUTES")
    block_end = app_src.index("\n};", block_start)
    block = app_src[block_start:block_end]
    routes = set(re.findall(r'^\s*"([^"]+)"\s*:', block, re.M))
    for name in re.findall(r"^\s*\[([A-Z0-9_]+)\]\s*:", block, re.M):
        assert name in consts, f"Константа {name} не найдена в portalRoutes.ts"
        routes.add(consts[name])
    assert len(routes) > 20, "Разбор STATIC_ROUTES сломался — роутов подозрительно мало"
    return sorted(routes)


def _resolve(token: str, consts: dict[str, str]) -> str | None:
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return consts.get(token)


def _nav_hrefs_and_prefixes(src: Path, consts: dict[str, str]) -> tuple[set[str], list[str]]:
    nav_src = (src / "features" / "portal" / "nav" / "siteNav.ts").read_text(encoding="utf-8")
    hrefs: set[str] = set()
    for token in re.findall(r"\bhref:\s*(\"[^\"]+\"|[A-Z0-9_]+)", nav_src):
        value = _resolve(token, consts)
        if value:
            hrefs.add(value)

    block_start = nav_src.index("export const SECTION_PATH_PREFIXES")
    block_end = nav_src.index("\n};", block_start)
    block = nav_src[block_start:block_end]
    prefixes: list[str] = []
    for token in re.findall(r"(\"[^\"]+\"|\b[A-Z][A-Z0-9_]+\b)", block):
        value = _resolve(token, consts)
        if value and value.startswith("/"):
            prefixes.append(value)
    assert len(prefixes) >= 7, "Разбор SECTION_PATH_PREFIXES сломался — адресов разделов подозрительно мало"
    # Корень под любым разделом означал бы «всё покрыто» — тест бы ослеп.
    assert "/" not in prefixes, "В SECTION_PATH_PREFIXES попал корень сайта"
    return hrefs, prefixes


def _has_prefix(path: str, prefix: str) -> bool:
    # Та же проверка, что pathHasPrefix в siteNav.ts: по границе сегмента.
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(f"{prefix}/")


_SRC = _frontend_src()
_CONSTS = _string_consts(
    (_SRC / "lib" / "portalRoutes.ts").read_text(encoding="utf-8"),
    (_SRC / "features" / "portal" / "nav" / "siteNav.ts").read_text(encoding="utf-8"),
)
APP_ROUTES = _static_routes(_SRC, _CONSTS)
NAV_HREFS, SECTION_PREFIXES = _nav_hrefs_and_prefixes(_SRC, _CONSTS)


@pytest.mark.parametrize("path", APP_ROUTES)
def test_every_app_route_is_in_site_nav(path: str) -> None:
    if path in NOT_IN_NAV:
        return
    in_tree = path in NAV_HREFS or any(_has_prefix(path, prefix) for prefix in SECTION_PREFIXES)
    assert in_tree, (
        f"Страницы {path} нет в дереве навигации (siteNav.ts): её не будет ни в одном "
        "меню, поиск её не найдёт, а раздел на ней не подсветится. Впишите её в дерево "
        "или, если это служебный адрес, в NOT_IN_NAV этого теста."
    )


@pytest.mark.parametrize("path", sorted(NOT_IN_NAV))
def test_not_in_nav_list_is_not_stale(path: str) -> None:
    assert path in APP_ROUTES, f"{path} больше нет в STATIC_ROUTES — уберите его из NOT_IN_NAV"
