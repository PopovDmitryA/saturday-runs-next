"""Аналитика «Поделиться 2.0» и OG-превью (Л19).

Канал experiment="share" в ab_events: события шторки с фронта и серверное
og_preview_fetch из пререндера; агрегаты для секций «Шаринг» и
«Разворачивания ссылок» в /admin/page-analytics.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.api.routes.seo import _messenger_bot_name
from app.models import AbEvent
from app.services.ab_service import record_ab_event
from app.services.page_analytics_service import build_og_fetch_stats, build_share_stats
from app.services.seo_service import (
    _og_image_tags,
    build_profile_meta,
    location_og_image_url,
    profile_og_image_url,
)


def _clear_share_events(db: Session) -> None:
    """Тесты идут по общей dev-БД: выметаем живые события канала внутри
    тестовой транзакции (откатится вместе с ней), иначе агрегаты не сойдутся."""
    db.query(AbEvent).filter(AbEvent.experiment == "share").delete()
    db.commit()


def _add_event(db: Session, event_type: str, value: str, visitor: str = "a:v1") -> None:
    db.add(
        AbEvent(
            experiment="share",
            variant="-",
            visitor_key=visitor,
            event_type=event_type,
            value=value,
            path="/",
            cohort="",
            ts=datetime.now(timezone.utc),
        )
    )
    db.commit()


def test_record_ab_event_accepts_share_channel(db_session: Session) -> None:
    event = record_ab_event(
        db_session,
        experiment="share",
        variant="-",
        visitor_key="a:abc",
        event_type="share_success",
        value="system:milestone",
        path="/users/1",
    )
    assert event is not None
    assert event.experiment == "share"
    assert event.event_type == "share_success"


def test_record_ab_event_accepts_og_preview_fetch(db_session: Session) -> None:
    event = record_ab_event(
        db_session,
        experiment="share",
        variant="-",
        visitor_key="bot:telegrambot",
        event_type="og_preview_fetch",
        value="location:kuzminki",
        path="/locations/kuzminki",
    )
    assert event is not None


def test_build_share_stats_funnel_and_breakdowns(db_session: Session) -> None:
    _clear_share_events(db_session)
    _add_event(db_session, "share_moment_shown", "run:dashboard", "a:v1")
    _add_event(db_session, "share_moment_shown", "milestone:history", "a:v2")
    _add_event(db_session, "share_open", "run:dashboard", "a:v1")
    _add_event(db_session, "share_open", "run:gallery", "a:v2")
    _add_event(db_session, "share_template_switch", "look:night", "a:v1")
    _add_event(db_session, "share_template_switch", "format:wide", "a:v1")
    _add_event(db_session, "share_customize", "photo", "a:v1")
    _add_event(db_session, "share_success", "system:run", "a:v1")
    _add_event(db_session, "share_success", "download:run", "a:v2")

    today = date.today()
    stats = build_share_stats(db_session, start=today, end=today)

    funnel = {row["event_type"]: row for row in stats["funnel"]}
    assert funnel["share_moment_shown"]["events"] == 2
    assert funnel["share_moment_shown"]["visitors"] == 2
    assert funnel["share_open"]["events"] == 2
    assert funnel["share_success"]["events"] == 2
    # Порядок ступеней фиксированный: показы → открытия → успехи.
    order = [row["event_type"] for row in stats["funnel"]]
    assert order.index("share_moment_shown") < order.index("share_open") < order.index("share_success")

    # Главный разрез — пары «сюжет + вход»: где именно жмут «Поделиться».
    pairs = {(row["subject"], row["entry"]): row for row in stats["pairs"]}
    assert pairs[("run", "dashboard")]["shown"] == 1
    assert pairs[("run", "dashboard")]["opens"] == 1
    assert pairs[("run", "gallery")]["opens"] == 1
    assert pairs[("milestone", "history")]["shown"] == 1

    channels = {row["channel"]: row["successes"] for row in stats["channels"]}
    assert channels == {"system": 1, "download": 1}

    assert stats["looks"] == [{"value": "night", "count": 1}]
    assert stats["formats"] == [{"value": "wide", "count": 1}]
    assert stats["photo_added"] == 1


def test_build_og_fetch_stats_groups_by_value(db_session: Session) -> None:
    _clear_share_events(db_session)
    _add_event(db_session, "og_preview_fetch", "location:no-such-slug", "bot:telegrambot")
    _add_event(db_session, "og_preview_fetch", "location:no-such-slug", "bot:whatsapp")
    _add_event(db_session, "og_preview_fetch", "portal_home:", "bot:telegrambot")

    today = date.today()
    rows = build_og_fetch_stats(db_session, start=today, end=today)
    by_key = {(row["page_type"], row["entity_key"]): row for row in rows}

    location_row = by_key[("location", "no-such-slug")]
    assert location_row["fetches"] == 2
    assert location_row["bots"] == 2
    # Неизвестный slug получает фолбэк-ссылку на страницу локации.
    assert location_row["href"] == "/locations/no-such-slug"

    home_row = by_key[("portal_home", "")]
    assert home_row["fetches"] == 1
    assert home_row["href"] is None


def test_location_og_image_url_requires_rendered_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "og_image_dir", str(tmp_path))
    payload = {"slug": "kuzminki", "stats": {"last_event_date": date(2026, 8, 1)}}

    # Файла ещё нет — картинки нет, пререндер отдаст дефолтную.
    assert location_og_image_url(payload) is None

    (tmp_path / "locations").mkdir()
    (tmp_path / "locations" / "kuzminki.png").write_bytes(b"png")
    url = location_og_image_url(payload)
    assert url is not None
    assert url.endswith("/og/locations/kuzminki.png?v=2026-08-01")


def test_profile_og_image_url_requires_rendered_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "og_image_dir", str(tmp_path))

    class _User:
        serial_id = 12
        public_slug = "dmitry"
        updated_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

    user = _User()
    # Картинки ещё нет — пререндер подставит дефолтную.
    assert profile_og_image_url(user) is None

    (tmp_path / "users").mkdir()
    (tmp_path / "users" / "12.png").write_bytes(b"png")
    url = profile_og_image_url(user)
    assert url is not None
    # Имя файла — по номеру участника: vanity-slug можно сменить, номер нет.
    assert url.endswith("/og/users/12.png?v=2026-08-10")


def test_build_profile_meta_uses_real_numbers() -> None:
    class _User:
        display_name = "Дмитрий Попов"

    payload = {
        "stats": {
            "total_runs": 128,
            "total_volunteering": 45,
            "analytics": {"unique_locations": 90},
        }
    }
    meta = build_profile_meta(_User(), payload)
    assert "Дмитрий Попов" in meta.title
    assert "128 пробежек" in meta.description
    assert "45 волонтёрств" in meta.description
    assert "90 локаций" in meta.description
    # С 15.08.2026 карточка индексируется — см. test_profile_meta_is_indexable.
    assert meta.indexable is True


def test_profile_meta_is_indexable() -> None:
    """Карточка участника индексируется — иначе ВК и Telegram показывают
    превью ссылки без картинки (проверено на живых ссылках 15.08.2026)."""

    class _User:
        display_name = "Дмитрий Попов"

    meta = build_profile_meta(_User(), {"stats": {"total_runs": 128}})
    assert meta.indexable is True


def test_build_profile_meta_survives_empty_stats() -> None:
    class _User:
        display_name = None

    meta = build_profile_meta(_User(), None)
    assert "Участник" in meta.title
    assert meta.description


def test_og_image_tags_default_and_custom() -> None:
    default_tags = "\n".join(_og_image_tags(None))
    assert "/og/default.png" in default_tags
    assert "summary_large_image" in default_tags

    custom_tags = "\n".join(_og_image_tags("https://run5k.run/og/locations/x.png?v=1"))
    assert "/og/locations/x.png?v=1" in custom_tags


def test_messenger_bot_name_matches_only_messengers() -> None:
    assert _messenger_bot_name("TelegramBot (like TwitterBot)") == "telegrambot"
    assert _messenger_bot_name("WhatsApp/2.23.20") == "whatsapp"
    # Поисковые роботы — не шаринг: событие не пишем.
    assert _messenger_bot_name("Mozilla/5.0 (compatible; YandexBot/3.0)") is None
    assert _messenger_bot_name("Mozilla/5.0 (compatible; Googlebot/2.1)") is None
