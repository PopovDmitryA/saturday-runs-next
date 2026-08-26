"""Запись волонтёров с официальной страницы 5 вёрст.

У каждой локации 5 вёрст есть страница https://5verst.ru/{slug}/volunteer/ с
таблицей «роль × ближайшие даты»: какие позиции локация считает обязательными
и кто уже записался. Для «Нужны волонтёры» это идеальный источник (идея
Дмитрия 17.08.2026): пустая клетка = вакансия, сам список ролей = ключевые
позиции, без которых старт не состоится.

Кэш короткий (10 минут): запись живая, люди вписываются в течение недели.
Ошибки сети деградируют в None — пост соберётся по запасному списку ролей.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import redis
from bs4 import BeautifulSoup

from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

ROSTER_CACHE_TTL_SECONDS = 10 * 60
_FETCH_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "Mozilla/5.0 (compatible; run5k.run organizer cabinet)"


def roster_url(five_verst_slug: str) -> str:
    return f"https://5verst.ru/{five_verst_slug}/volunteer/"


def roster_cache_key(five_verst_slug: str) -> str:
    return f"organizer:roster:v1:{five_verst_slug}"


def fetch_volunteer_roster(five_verst_slug: str, *, use_cache: bool = True) -> dict[str, Any] | None:
    """Таблица записи волонтёров: даты, роли, кто записан. None — не достали."""
    cache_key = roster_cache_key(five_verst_slug)
    if use_cache:
        try:
            raw = get_redis_client().get(cache_key)
        except redis.RedisError:
            raw = None
        if raw:
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                pass

    payload = _fetch_and_parse(five_verst_slug)
    if payload is not None and use_cache:
        try:
            get_redis_client().setex(cache_key, ROSTER_CACHE_TTL_SECONDS, json.dumps(payload))
        except redis.RedisError:
            pass
    return payload


def _fetch_and_parse(five_verst_slug: str) -> dict[str, Any] | None:
    url = roster_url(five_verst_slug)
    try:
        response = httpx.get(
            url,
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("volunteer roster: fetch failed for %s", url)
        return None
    return parse_volunteer_roster(response.text, url)


def parse_volunteer_roster(html: str, source_url: str) -> dict[str, Any] | None:
    """Разбор таблицы «Роль × даты». Формат — resultsTable на 5verst.ru."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="resultsTable") or soup.find("table")
    if table is None:
        return None
    rows = table.find_all("tr")
    if not rows:
        return None

    header_cells = rows[0].find_all(["th", "td"])
    # Первая колонка — «Роль», дальше даты dd.mm.yyyy.
    dates = [cell.get_text(strip=True) for cell in header_cells[1:]]
    if not dates:
        return None

    roles: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        role = cells[0].get_text(strip=True)
        if not role:
            continue
        filled = {}
        for date_label, cell in zip(dates, cells[1:], strict=False):
            name = cell.get_text(strip=True)
            if name:
                filled[date_label] = name
        roles.append({"role": role, "filled": filled})
    if not roles:
        return None
    return {"source_url": source_url, "dates": dates, "roles": roles}
