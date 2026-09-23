"""Отправители по каналам: Telegram, VK, почта.

Каждый принимает адрес и готовое сообщение и возвращает исход. Сеть и чужие
таймауты живут только здесь; кто и по какому поводу пишет — дело
notification_service. На прогоне тестов ни один отправитель в сеть не ходит.

Текст сообщения приходит в разметке app/notification_markup.py и здесь
переводится в то, что умеет канал: Telegram — HTML с <b> и <a>, письмо —
HTML-абзацы, VK — чистый текст (форматирования в его сообщениях нет).

Исход различает временную ошибку (сеть, 5xx — стоит повторить позже) и
постоянную (человек заблокировал бота, запретил сообщения сообществу): по
постоянной канал пропускается сразу, и уведомление уходит в следующий.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.core.mailer import MailerError, send_email
from app.core.runtime_env import is_test_run
from app.notification_markup import to_plain, to_telegram_html
from app.services.vk_client import VkApiError, send_vk_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingMessage:
    """Одно уведомление в виде, готовом для любого канала.

    title — заголовок (жирная первая строка, тема письма); text — тело в
    разметке; url/url_label — главная ссылка-действие; unsubscribe_url —
    отписка от этого вида.
    """

    title: str
    text: str
    url: str | None
    url_label: str
    unsubscribe_url: str
    settings_url: str
    html: str | None = None

    def telegram_html(self) -> str:
        """Подвал ведёт в настройки, а не на мгновенную отписку: человеку чаще
        нужно донастроить, а не отрезать всё сразу — выключить вид там же в
        два клика. Одноразовая ссылка отписки остаётся в письмах, где её
        требуют почтовые провайдеры."""
        parts = [f"<b>{to_telegram_html(self.title)}</b>", "", to_telegram_html(self.text)]
        if self.url:
            parts += ["", f'🔗 <a href="{self.url}">{to_telegram_html(self.url_label)}</a>']
        parts += ["", f'⚙️ <a href="{self.settings_url}">Настроить уведомления</a>']
        return "\n".join(parts)

    def plain_text(self) -> str:
        """VK: без форматирования, ссылки открытым адресом — VK подсветит сам."""
        lines = [to_plain(self.title), "", to_plain(self.text)]
        if self.url:
            lines += ["", f"🔗 {to_plain(self.url_label)}: {self.url}"]
        lines += ["", f"⚙️ Настроить уведомления: {self.settings_url}"]
        return "\n".join(lines)

    def email_text(self) -> str:
        lines = [to_plain(self.title), "", to_plain(self.text)]
        if self.url:
            lines += ["", f"{to_plain(self.url_label)}: {self.url}"]
        lines += ["", f"Отписаться от таких писем: {self.unsubscribe_url}", f"Настроить: {self.settings_url}"]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    error: str | None = None
    # Постоянная ошибка: повторять в этот канал бессмысленно (бот заблокирован,
    # сообщения от сообщества запрещены, адреса нет).
    permanent: bool = False


def send_telegram(target: str, message: OutgoingMessage) -> SendOutcome:
    if is_test_run():
        return SendOutcome(ok=False, error="test run")
    settings = get_settings()
    if not settings.telegram_bot_token:
        return SendOutcome(ok=False, error="bot token not configured", permanent=True)
    return send_telegram_html(target, message.telegram_html())


def send_telegram_html(target: str, html: str) -> SendOutcome:
    """Сырой HTML в чат: им же уходит копия админу."""
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": target, "text": html, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        response = httpx.post(url, json=payload, proxy=settings.telegram_proxy_url or None, timeout=20.0)
    except httpx.HTTPError as exc:
        logger.warning("notify: telegram send failed for chat %s: %s", target, exc)
        return SendOutcome(ok=False, error=f"network: {exc}"[:200])
    if response.status_code == 200:
        return SendOutcome(ok=True)
    # 403 — бот заблокирован или чат недоступен, 400 chat not found — адрес
    # мёртв: оба случая лечатся только тем, что человек откроет бота.
    body = response.text[:200]
    permanent = response.status_code == 403 or (response.status_code == 400 and "chat not found" in body)
    logger.warning("notify: telegram send failed for chat %s: %s %s", target, response.status_code, body)
    return SendOutcome(ok=False, error=f"telegram {response.status_code}: {body[:160]}", permanent=permanent)


def send_vk(target: str, message: OutgoingMessage) -> SendOutcome:
    if is_test_run():
        return SendOutcome(ok=False, error="test run")
    token = get_settings().vk_bot_group_token
    if not token:
        return SendOutcome(ok=False, error="vk group token not configured", permanent=True)
    try:
        send_vk_message(token, int(target), message.plain_text())
    except VkApiError as exc:
        text = str(exc)
        # 901 — «нельзя отправить сообщение пользователю без разрешения»,
        # 902 — настройки приватности: без действий человека не пробиться.
        permanent = "'error_code': 901" in text or "'error_code': 902" in text
        logger.warning("notify: vk send failed for %s: %s", target, text[:200])
        return SendOutcome(ok=False, error=f"vk: {text[:160]}", permanent=permanent)
    except (ValueError, httpx.HTTPError) as exc:
        logger.warning("notify: vk send failed for %s: %s", target, exc)
        return SendOutcome(ok=False, error=f"vk: {exc}"[:200])
    return SendOutcome(ok=True)


def send_email_channel(target: str, message: OutgoingMessage) -> SendOutcome:
    if is_test_run():
        return SendOutcome(ok=False, error="test run")
    settings = get_settings()
    try:
        send_email(
            settings,
            to=target,
            subject=to_plain(message.title),
            text_body=message.email_text(),
            html_body=message.html,
            extra_headers={
                "List-Unsubscribe": f"<{message.unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
    except MailerError as exc:
        logger.warning("notify: email send failed for %s: %s", target, exc)
        return SendOutcome(ok=False, error=f"email: {exc}"[:200])
    return SendOutcome(ok=True)


SENDERS = {
    "telegram": send_telegram,
    "vk": send_vk,
    "email": send_email_channel,
}
