"""Корневые SEO-адреса: sitemap.xml, robots.txt и пререндер для роботов.

Роутер монтируется в main.py БЕЗ префикса /api — роботы ходят по обычным
адресам сайта. Проксирование этих путей на бэкенд настроено в
nginx/conf.d/default.conf.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.seo_service import (
    build_robots_txt,
    build_sitemap,
    render_prerendered_page,
)

router = APIRouter(tags=["seo"], include_in_schema=False)

# Часовой кэш: sitemap считается из каталога локаций (тот сам за Redis-кэшем),
# но незачем дёргать его на каждый визит робота.
_SITEMAP_CACHE_CONTROL = "public, max-age=3600"


@router.get("/sitemap.xml")
def sitemap(db: Annotated[Session, Depends(get_db)]) -> Response:
    return Response(
        content=build_sitemap(db),
        media_type="application/xml",
        headers={"Cache-Control": _SITEMAP_CACHE_CONTROL},
    )


@router.get("/robots.txt")
def robots() -> Response:
    return Response(
        content=build_robots_txt(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": _SITEMAP_CACHE_CONTROL},
    )


@router.get("/__prerender/{full_path:path}")
def prerender(full_path: str, db: Annotated[Session, Depends(get_db)]) -> Response:
    """HTML для робота по адресу страницы.

    Адрес приходит от nginx как остаток пути: /__prerender/locations/kuzminki.
    Отдаём всегда 200 — даже для несуществующего адреса: робот пришёл по ссылке,
    и пустая болванка SPA его устроила бы хуже, чем страница с мета-тегами.
    """
    return Response(
        content=render_prerendered_page(db, f"/{full_path}"),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )
