"""Разметка текста уведомлений и её рендер под каждый канал.

Текст сообщения пишется один раз в крошечной разметке — `**жирный**` и
`[подпись](https://…)`, переводы строк как есть — и хранится в журнале
доставок в таком виде. Дальше каждый канал рисует его своими средствами:
Telegram — HTML (parse_mode=HTML), письмо — HTML-абзацы, VK — чистый текст
(форматирования в сообщениях VK нет, ссылки он подсвечивает сам).

Разметка нарочно минимальная: двух приёмов хватает, чтобы выделить главное и
дать кликабельные ссылки, а разбор остаётся двумя регулярками без сюрпризов.
"""

from __future__ import annotations

import re
from html import escape

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def bold(text: str) -> str:
    return f"**{text}**"


def link(label: str, url: str) -> str:
    return f"[{label}]({url})"


def to_plain(text: str) -> str:
    """VK и текстовая версия письма: жирный снимается, ссылка — «подпись: адрес»."""
    text = _LINK.sub(lambda m: f"{m.group(1)}: {m.group(2)}", text)
    return _BOLD.sub(lambda m: m.group(1), text)


def to_telegram_html(text: str) -> str:
    """HTML Telegram: только <b> и <a>, всё остальное экранируется."""
    return _render_html(text, newline="\n")


def to_email_html(text: str) -> str:
    """Фрагмент для письма: те же <b>/<a>, переводы строк — <br>."""
    return _render_html(text, newline="<br>")


def _render_html(text: str, *, newline: str) -> str:
    # Сначала режем по ссылкам и жирному, экранируя только «сырые» куски:
    # экранировать весь текст целиком нельзя — сломаются сами теги.
    out: list[str] = []
    pos = 0
    token = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)", re.DOTALL)
    for m in token.finditer(text):
        out.append(escape(text[pos : m.start()]))
        if m.group(1) is not None:
            out.append(f"<b>{escape(m.group(1))}</b>")
        else:
            out.append(f'<a href="{escape(m.group(3), quote=True)}">{escape(m.group(2))}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out).replace("\n", newline)
