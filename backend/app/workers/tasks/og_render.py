"""Прегенерация OG-картинок локаций (Л19).

Playwright открывает внутренний фронтовый роут /render/og/location/{slug}
(wide-«Визитка» 1200×630 на тех же React-компонентах, что и шторка
«Поделиться») и снимает скриншот в settings.og_image_dir/locations/.
Nginx раздаёт папку статически как /og/locations/*, пререндер для роботов
подставляет адрес картинки в og:image (см. seo_service.location_og_image_url).

Очередь parkrun: Chromium установлен только в образе worker-parkrun
(Dockerfile.parkrun). ~110 локаций × ~1 сек — единицы минут, поэтому полный
прогон без инкрементальности: проще и надёжнее.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, cast

from app.config import get_settings
from app.db.session import get_session_factory
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Сколько ждать готовности карточки (маркер #og-ready ставит фронт, когда
# данные загружены и шрифты применены).
_READY_TIMEOUT_MS = 20_000


def _location_slugs(db: Any) -> list[str]:
    """Каноничные слаги всех неотменённых локаций — как в sitemap."""
    from app.services.location_page_service import build_locations_index

    index = build_locations_index(db)
    items = cast("list[dict[str, Any]]", index.get("items") or [])
    return [
        str(item["slug"])
        for item in items
        if item.get("slug") and not item.get("is_cancelled")
    ]


def _public_profile_keys(db: Any) -> list[str]:
    """Номера участников с публичным профилем.

    Скрытые профили (profile_private) не рендерим вовсе: превью такой ссылки
    показывает дефолтную карточку сайта, без единой личной цифры.
    """
    from app.models import User

    rows = (
        db.query(User.serial_id)
        .filter(User.serial_id.isnot(None))
        .filter(User.profile_private.is_(False))
        .all()
    )
    return [str(row[0]) for row in rows]


def _render_batch(keys: list[str], *, kind: str, out_dir: Path) -> dict[str, object]:
    """Снимает карточки по списку ключей: /render/og/{kind}/{key} → {key}.png."""
    from playwright.sync_api import sync_playwright

    base = get_settings().og_render_base_url.rstrip("/")
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered = 0
    failed: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        try:
            for key in keys:
                url = f"{base}/render/og/{kind}/{key}"
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=_READY_TIMEOUT_MS)
                    page.wait_for_selector("#og-ready", timeout=_READY_TIMEOUT_MS)
                    # Скриншот в байты: у screenshot(path=...) жёсткая проверка
                    # расширения, а нам нужен временный файл для атомарной подмены.
                    image = page.screenshot()
                    tmp_path = out_dir / f"{key}.png.tmp"
                    tmp_path.write_bytes(image)
                    # Атомарная подмена: nginx не отдаст недописанный файл.
                    os.replace(tmp_path, out_dir / f"{key}.png")
                    rendered += 1
                except Exception:  # noqa: BLE001 — одна битая страница не валит прогон
                    logger.exception("og_render: не удалось отрендерить %s/%s", kind, key)
                    failed.append(key)
        finally:
            browser.close()

    logger.info("og_render(%s): готово %d, ошибок %d", kind, rendered, len(failed))
    return {"rendered": rendered, "failed": failed, "total": len(keys)}


@celery_app.task(name="og_render.render_user_images", queue="parkrun")
def og_render_user_images_task(handles: list[str] | None = None) -> dict[str, object]:
    """Рендерит OG-картинки участников; handles=None — все публичные профили.

    Ленивый догон: при промахе кэша пререндер ставит сюда одиночную задачу,
    поэтому список и принимается параметром.
    """
    if handles is None:
        db = get_session_factory()()
        try:
            handles = _public_profile_keys(db)
        finally:
            db.close()

    return _render_batch(handles, kind="user", out_dir=Path(get_settings().og_image_dir) / "users")


@celery_app.task(name="og_render.render_location_images", queue="parkrun")
def og_render_location_images_task(slugs: list[str] | None = None) -> dict[str, object]:
    """Рендерит OG-картинки локаций; slugs=None — все локации каталога."""
    if slugs is None:
        db = get_session_factory()()
        try:
            slugs = _location_slugs(db)
        finally:
            db.close()

    return _render_batch(
        slugs, kind="location", out_dir=Path(get_settings().og_image_dir) / "locations"
    )
