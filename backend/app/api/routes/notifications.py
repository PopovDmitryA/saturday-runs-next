"""Отписка по ссылке из уведомления — без входа на сайт.

Ссылка есть в каждом сообщении (Telegram, VK, письмо). Человек нажимает её из
мессенджера или почтового клиента, поэтому здесь нет сессии и нет JSON —
отвечаем готовой страницей, как у рассылки новостей (routes/newsletter.py).
Первый клик отключает тот вид, о котором было сообщение; на странице есть
второй шаг — выключить всё — и ссылка в настройки.
"""

from __future__ import annotations

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.services.notification_service import (
    UNSUBSCRIBE_ALL,
    NotificationTokenError,
    apply_unsubscribe,
    parse_unsubscribe_token,
    settings_url,
    unsubscribe_url,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _page(title: str, message: str, settings: Settings, *, links: list[tuple[str, str]]) -> HTMLResponse:
    site = settings.app_base_url.rstrip("/")
    anchors = "".join(
        f'<p style="margin:0 0 10px;"><a href="{escape(href, quote=True)}" style="color:#3b5bfd;font-size:15px;">'
        f"{escape(label)}</a></p>"
        for label, href in links
    )
    html = f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)} — run5k.run</title>
</head>
<body style="margin:0;background:#f4f6fa;font-family:Arial,Helvetica,sans-serif;color:#1c2430;">
<div style="max-width:520px;margin:64px auto;padding:28px;background:#fff;border:1px solid #e3e7ee;border-radius:12px;">
<h1 style="margin:0 0 12px;font-size:20px;">{escape(title)}</h1>
<p style="margin:0 0 18px;font-size:15px;line-height:1.5;">{escape(message)}</p>
{anchors}
<p style="margin:18px 0 0;"><a href="{site}" style="color:#6b7280;font-size:14px;">Перейти на run5k.run</a></p>
</div>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/unsubscribe", response_model=None)
def notifications_unsubscribe(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Query()],
) -> HTMLResponse:
    try:
        user_id, _scope = parse_unsubscribe_token(token, settings.app_secret_key)
        scope, kind_title = apply_unsubscribe(db, token, settings)
    except NotificationTokenError:
        return _page(
            "Ссылка не сработала",
            "Похоже, ссылка испорчена при пересылке. Уведомления можно выключить в настройках профиля.",
            settings,
            links=[("Настройки уведомлений", settings_url(settings))],
        )
    if scope == UNSUBSCRIBE_ALL:
        return _page(
            "Уведомления выключены",
            "Сайт больше не будет писать вам ни о чём. Включить обратно можно в настройках профиля.",
            settings,
            links=[("Настройки уведомлений", settings_url(settings))],
        )
    what = f"«{kind_title}»" if kind_title else "этого вида"
    return _page(
        "Готово",
        f"Уведомления {what} выключены. Остальные продолжат приходить.",
        settings,
        links=[
            ("Выключить все уведомления", unsubscribe_url(settings, user_id, UNSUBSCRIBE_ALL)),
            ("Настройки уведомлений", settings_url(settings)),
        ],
    )
