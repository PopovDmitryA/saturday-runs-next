from __future__ import annotations

import logging
import sys
import time

import httpx

from vk_bot.pipeline import PIPELINES, enqueue_pipeline
from vk_bot.settings import VkBotSettings, get_vk_bot_settings, vk_bot_headers
from vk_bot.stats_format import format_admin_stats
from vk_bot.status_format import format_pipeline_status
from vk_bot.vk_api import VkApiError, VkLongPoll, send_message

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Бот администратора Saturday Runs (VK).\n\n"
    "Команды:\n"
    "/stats [дней] — статистика ЛК\n"
    "/status — запущенные пайплайны и время старта\n"
    "/sync — список пайплайнов 5 вёрst\n"
    "/sync registry — реестр /events/\n"
    "/sync latest — /results/latest/\n"
    "/sync rotation — ротация локаций (20 summary)\n"
    "/sync reconcile — сверка 100 старых протоколов\n"
    "/sync location <slug> — одна локация\n"
    "/sync protocol <url> — протокол 5 вёрst или s95 по ссылке\n\n"
    "Координаты с95: ответьте (Reply) на сообщение бота координатами latitude:longitude, "
    "затем Reply «ок» на проверку карты.\n\n"
    "Отчёты о запуске и завершении пайплайнов приходят автоматически."
)


def _settings() -> VkBotSettings:
    return get_vk_bot_settings()


def _is_admin(user_id: int, settings: VkBotSettings) -> bool:
    return settings.vk_admin_user_id > 0 and user_id == settings.vk_admin_user_id


def _parse_command(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    cmd = parts[0].lower()
    if cmd.startswith("/"):
        cmd = cmd[1:]
    return cmd, parts[1:]


def _fetch_stats(settings: VkBotSettings, period_days: int) -> str:
    headers = vk_bot_headers(settings)
    url = f"{settings.api_base_url.rstrip('/')}/api/internal/vk-bot/stats"
    response = httpx.get(
        url,
        params={"vk_user_id": settings.vk_admin_user_id, "period_days": period_days},
        headers=headers,
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"stats API {response.status_code}: {detail}")
    return format_admin_stats(response.json())


def _fetch_pipeline_status(settings: VkBotSettings) -> str:
    headers = vk_bot_headers(settings)
    url = f"{settings.api_base_url.rstrip('/')}/api/internal/vk-bot/sync-status"
    response = httpx.get(
        url,
        params={"vk_user_id": settings.vk_admin_user_id},
        headers=headers,
        timeout=30.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-status API {response.status_code}: {detail}")
    return format_pipeline_status(response.json())


def _sync_protocol_url(settings: VkBotSettings, url: str) -> str:
    headers = vk_bot_headers(settings)
    response = httpx.post(
        f"{settings.api_base_url.rstrip('/')}/api/internal/vk-bot/sync-protocol",
        params={"vk_user_id": settings.vk_admin_user_id},
        json={"url": url},
        headers=headers,
        timeout=120.0,
    )
    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise RuntimeError(f"sync-protocol API {response.status_code}: {detail}")
    data = response.json()
    platform = data.get("platform", "?")
    location = data.get("location_slug", "?")
    event_date = data.get("event_date", "?")
    runs = data.get("run_results_upserted", 0)
    vols = data.get("volunteer_results_upserted", 0)
    changed = "да" if data.get("protocol_changed") else "нет"
    return (
        f"✅ Протокол обновлён ({platform})\n"
        f"Локация: {location}\n"
        f"Дата: {event_date}\n"
        f"Пробежек: {runs}, волонтёров: {vols}\n"
        f"Протокол изменился: {changed}"
    )


def _handle_coordinate_message(
    settings: VkBotSettings,
    peer_id: int,
    text: str,
    reply_to_message_id: int | None,
) -> str | None:
    headers = vk_bot_headers(settings)
    payload = {
        "vk_peer_id": peer_id,
        "text": text.strip(),
        "reply_to_message_id": reply_to_message_id,
    }
    response = httpx.post(
        f"{settings.api_base_url.rstrip('/')}/api/internal/vk-bot/coordinate-message",
        json=payload,
        headers=headers,
        timeout=30.0,
    )
    if response.status_code != 200:
        return None
    data = response.json()
    if not data.get("handled"):
        return None
    return data.get("reply") or ""


def _handle_message(peer_id: int, from_id: int, text: str, message: dict) -> None:
    settings = _settings()
    if not _is_admin(from_id, settings):
        send_message(peer_id, "Доступ только для администратора.")
        return

    reply_to: int | None = None
    reply_message = message.get("reply_message")
    if isinstance(reply_message, dict):
        reply_to = reply_message.get("id")
        if reply_to is not None:
            reply_to = int(reply_to)

    stripped = text.strip()
    if not stripped.startswith("/"):
        coord_reply = _handle_coordinate_message(settings, peer_id, stripped, reply_to)
        if coord_reply:
            send_message(peer_id, coord_reply, reply_to=reply_to)
        return

    cmd, args = _parse_command(stripped)

    if cmd in {"start", "help", "помощь"}:
        send_message(peer_id, HELP_TEXT)
        return

    if cmd == "stats":
        period_days = 30
        if args:
            try:
                period_days = max(1, min(365, int(args[0])))
            except ValueError:
                send_message(peer_id, "Использование: /stats [дней]")
                return
        try:
            send_message(peer_id, _fetch_stats(settings, period_days))
        except Exception as exc:
            send_message(peer_id, f"Не удалось получить статистику: {exc}")
        return

    if cmd == "status":
        try:
            send_message(peer_id, _fetch_pipeline_status(settings))
        except Exception as exc:
            send_message(peer_id, f"Не удалось получить статус: {exc}")
        return

    if cmd == "sync":
        if not args:
            lines = ["Пайплайны 5 вёрst:"]
            for key, (label, _) in sorted(PIPELINES.items()):
                lines.append(f"• /sync {key} — {label}")
            lines.append("• /sync location <slug> — одна локация")
            send_message(peer_id, "\n".join(lines))
            return
        if args[0].lower() == "protocol":
            if len(args) < 2:
                send_message(
                    peer_id,
                    "Использование: /sync protocol <url>\n"
                    "Примеры:\n"
                    "• https://5verst.ru/zil/results/31.05.2025/\n"
                    "• https://s95.ru/activities/4124",
                )
                return
            try:
                send_message(peer_id, _sync_protocol_url(settings, args[1]))
            except Exception as exc:
                send_message(peer_id, f"Не удалось обновить протокол: {exc}")
            return
        try:
            location_slug = args[1] if args[0].lower() == "location" and len(args) > 1 else None
            reply = enqueue_pipeline(args[0], location_slug=location_slug)
            send_message(peer_id, reply)
        except ValueError as exc:
            send_message(peer_id, str(exc))
        return

    send_message(peer_id, "Неизвестная команда. /help")


def _handle_update(update: dict) -> None:
    if update.get("type") != "message_new":
        return
    obj = update.get("object") or {}
    message = obj.get("message") or {}
    text = str(message.get("text") or "")
    peer_id = int(message.get("peer_id") or 0)
    from_id = int(message.get("from_id") or peer_id)
    if not peer_id or not text:
        return
    _handle_message(peer_id, from_id, text, message)


def run() -> None:
    settings = _settings()
    if not settings.vk_bot_group_token:
        raise RuntimeError("VK_BOT_GROUP_TOKEN is not set")
    if not settings.vk_admin_user_id:
        raise RuntimeError("VK_ADMIN_USER_ID is not set")
    if not settings.vk_bot_group_id:
        raise RuntimeError("VK_BOT_GROUP_ID is not set")

    logger.info("Starting VK admin bot (group_id=%s)", settings.vk_bot_group_id)
    long_poll = VkLongPoll()
    while True:
        try:
            for update in long_poll.poll():
                try:
                    _handle_update(update)
                except Exception:
                    logger.exception("Failed to handle VK update")
        except VkApiError:
            logger.exception("VK long poll error, retry in 5s")
            time.sleep(5)
        except httpx.HTTPError:
            logger.exception("VK long poll HTTP error, retry in 5s")
            time.sleep(5)


if __name__ == "__main__":
    run()
