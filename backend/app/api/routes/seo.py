"""Корневые SEO-адреса: sitemap.xml, robots.txt и пререндер для роботов.

Роутер монтируется в main.py БЕЗ префикса /api — роботы ходят по обычным
адресам сайта. Проксирование этих путей на бэкенд настроено в
nginx/conf.d/default.conf.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.ab_service import record_ab_event
from app.services.page_analytics_service import SHARE_EXPERIMENT, classify_page
from app.services.seo_service import (
    build_robots_txt,
    build_sitemap,
    render_prerendered_page,
)

# Разворачиватели ссылок в чатах — для метрики «ссылку кинули в чат»
# (og_preview_fetch → «Разворачивания ссылок» в /admin/page-analytics).
# Поисковых роботов сознательно не пишем: их обходы — SEO, а не шаринг.
_MESSENGER_BOT_RE = re.compile(
    r"telegrambot|vkshare|vkrobot|whatsapp|viber|facebookexternalhit|twitterbot"
    r"|discordbot|slackbot|linkedinbot",
    re.IGNORECASE,
)


def _messenger_bot_name(user_agent: str) -> str | None:
    match = _MESSENGER_BOT_RE.search(user_agent)
    return match.group(0).lower() if match else None

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
def prerender(
    full_path: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """HTML для робота по адресу страницы.

    Адрес приходит от nginx как остаток пути: /__prerender/locations/kuzminki.
    Отдаём всегда 200 — даже для несуществующего адреса: робот пришёл по ссылке,
    и пустая болванка SPA его устроила бы хуже, чем страница с мета-тегами.
    """
    path = f"/{full_path}"
    bot = _messenger_bot_name(request.headers.get("user-agent", ""))
    if bot is not None:
        # Бот мессенджера разворачивает ссылку — значит, её кинули в чат.
        try:
            page_type, entity_key = classify_page(path)
            record_ab_event(
                db,
                experiment=SHARE_EXPERIMENT,
                variant="-",
                visitor_key=f"bot:{bot}",
                event_type="og_preview_fetch",
                value=f"{page_type}:{entity_key or ''}",
                path=path,
            )
        except Exception:  # noqa: BLE001 — аналитика не должна ломать пререндер
            db.rollback()
    return Response(
        content=render_prerendered_page(db, path),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=600"},
    )
