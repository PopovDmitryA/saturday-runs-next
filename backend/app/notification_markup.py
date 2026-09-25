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
    """VK и текстовая версия письма: форматирования нет, адрес — своей строкой.

    Подставлять адрес прямо в строку («Ахтубинск: https://… · 5 вёрст») нельзя:
    длинная ссылка разрывает фразу пополам, и хвост после неё не читается.
    Поэтому в строке остаётся подпись, а каждый адрес уезжает строкой ниже —
    VK и почтовые клиенты делают такую ссылку кликабельной сами.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        urls = [match.group(2) for match in _LINK.finditer(line)]
        stripped = _LINK.sub(lambda m: _BOLD.sub(lambda b: b.group(1), m.group(1)), line)
        lines.append(_BOLD.sub(lambda m: m.group(1), stripped))
        lines.extend(urls)
    return "\n".join(lines)


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
            # Подпись ссылки может быть жирной: `[**Мещерский**](…)` — название
            # локации и кликабельно, и выделено.
            label = _BOLD.sub(lambda b: f"<b>{escape(b.group(1))}</b>", escape(m.group(2)))
            out.append(f'<a href="{escape(m.group(3), quote=True)}">{label}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out).replace("\n", newline)
