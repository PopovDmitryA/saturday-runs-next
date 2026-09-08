"""Пререндер «Обновлений»: разметка релиза, а не сырые символы.

Страница обновлений понимает «*текст*» жирным и отдельный блок «__SPLIT__» —
чертой (PortalUpdatesPage). Робот же получал абзацы как есть, со звёздочками и
словом __SPLIT__ прямо в разметке.
"""

from __future__ import annotations

from datetime import date

from app.services.release_service import ReleasesPage
from app.services.seo_service import _release_inline, _releases_body


class _Release:
    def __init__(self, body: str) -> None:
        self.version = "9.9.9"
        self.title = "Тест"
        self.released_at = date(2026, 9, 8)
        self.body = body


def _render(body: str) -> str:
    page = ReleasesPage(
        items=[_Release(body)],  # type: ignore[list-item]
        page=1,
        pages=1,
        total=1,
        page_size=10,
        latest_version="9.9.9",
    )
    return _releases_body(page)


def test_split_marker_becomes_rule() -> None:
    html = _render("Первое\n\n__SPLIT__\n\nВторое")
    assert "<hr />" in html
    assert "__SPLIT__" not in html


def test_split_marker_tolerates_extra_underscores() -> None:
    assert "<hr />" in _render("Первое\n\n___SPLIT___\n\nВторое")


def test_split_word_inside_sentence_stays_text() -> None:
    html = _render("Тут про __SPLIT__ внутри строки")
    assert "<hr />" not in html
    assert "__SPLIT__" in html


def test_bold_markup_is_rendered() -> None:
    html = _render("🚦 *Светофор ротации*\n\nТекст")
    assert "<strong>Светофор ротации</strong>" in html
    assert "*Светофор" not in html


def test_bold_inside_list_items() -> None:
    html = _render("- *Объединить* — забирает учётки\n- Просто пункт")
    assert "<li><strong>Объединить</strong> — забирает учётки</li>" in html


def test_inline_escapes_before_bolding() -> None:
    """Экранирование раньше замены: разметку из текста релиза не пропускаем."""
    assert _release_inline("<b>x</b> и *жир*") == "&lt;b&gt;x&lt;/b&gt; и <strong>жир</strong>"
