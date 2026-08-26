"""Подписка и отписка по ссылке из письма — без входа на сайт.

Человек нажимает ссылку прямо в почтовом клиенте, поэтому здесь нет сессии и
нет JSON: отвечаем готовой страницей. Личный кабинет для этого же есть в
настройках профиля, но требовать вход ради отписки нельзя — почтовые
провайдеры считают такое письмо спамом.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.services.newsletter_service import NewsletterTokenError, apply_token

router = APIRouter(prefix="/news", tags=["newsletter"])


def _page(title: str, message: str, settings: Settings) -> HTMLResponse:
    site = settings.app_base_url.rstrip("/")
    html = f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} — run5k.run</title>
</head>
<body style="margin:0;background:#f4f6fa;font-family:Arial,Helvetica,sans-serif;color:#1c2430;">
<div style="max-width:520px;margin:64px auto;padding:28px;background:#fff;border:1px solid #e3e7ee;border-radius:12px;">
<h1 style="margin:0 0 12px;font-size:20px;">{title}</h1>
<p style="margin:0 0 18px;font-size:15px;line-height:1.5;">{message}</p>
<a href="{site}" style="color:#3b5bfd;font-size:15px;">Перейти на run5k.run</a>
</div>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/subscribe", response_model=None)
def newsletter_subscribe(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Query()],
) -> HTMLResponse:
    try:
        subscribed, _email = apply_token(db, token, settings)
    except NewsletterTokenError:
        return _page(
            "Ссылка не сработала",
            "Похоже, ссылка испорчена при пересылке. Подписку можно включить в настройках профиля.",
            settings,
        )
    if not subscribed:
        return _page("Готово", "Настройки рассылки обновлены.", settings)
    return _page(
        "Вы подписаны",
        "Будем писать о крупных обновлениях сайта. Отписаться можно ссылкой в любом письме "
        "или в настройках профиля.",
        settings,
    )


@router.get("/unsubscribe", response_model=None)
def newsletter_unsubscribe(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str, Query()],
) -> HTMLResponse:
    try:
        apply_token(db, token, settings)
    except NewsletterTokenError:
        return _page(
            "Ссылка не сработала",
            "Похоже, ссылка испорчена при пересылке. Отписаться можно в настройках профиля.",
            settings,
        )
    return _page(
        "Вы отписались",
        "Писем о новостях больше не будет. Письма с кодом для входа приходить продолжат — "
        "без них не войти в профиль.",
        settings,
    )
