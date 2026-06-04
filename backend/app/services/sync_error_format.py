from __future__ import annotations

import re

UNLINK_CANCELLED_MESSAGE = "Синхронизация отменена: профиль отвязан"

_TECHNICAL_MARKERS = (
    "INSERT INTO",
    "[SQL:",
    "(Background on this error",
    "sqlalchemy",
    "psycopg",
    "IntegrityError",
)


def _is_technical_dump(text: str) -> bool:
    if len(text) > 320:
        return True
    lower = text.lower()
    return any(marker.lower() in lower for marker in _TECHNICAL_MARKERS)


def humanize_sync_error_message(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    if UNLINK_CANCELLED_MESSAGE in text:
        return UNLINK_CANCELLED_MESSAGE

    if "отвязан" in text.lower() and len(text) < 120:
        return text[:500]

    for friendly in (
        "Нет привязанных профилей",
        "не была обработана воркером",
        "Превышено время ожидания",
        "Нажмите «Обновить»",
    ):
        if friendly in text:
            return text[:500]

    lower = text.lower()
    if "uniqueviolation" in lower or "duplicate key" in lower:
        event_match = re.search(r"['\"]event_name['\"][^'\"]*['\"]([^'\"]+)['\"]", text)
        if event_match:
            return f"Конфликт данных: событие «{event_match.group(1)}» уже есть в базе."
        return "Конфликт данных: такая запись о мероприятии уже есть в базе."

    if "rate limit" in lower or "429" in text:
        return "Сайт платформы временно ограничил запросы. Попробуйте обновить позже."

    if "timeout" in lower or "timed out" in lower:
        return "Превышено время ожидания ответа от сайта платформы."

    if "connection" in lower and ("refused" in lower or "failed" in lower):
        return "Не удалось подключиться к сайту платформы. Попробуйте позже."

    if "; " in text and re.search(r"\b(five_verst|s95|parkrun):", text):
        parts: list[str] = []
        for chunk in text.split("; "):
            chunk = chunk.strip()
            if not chunk:
                continue
            if re.match(r"^(five_verst|s95|parkrun):", chunk):
                prefix, _, rest = chunk.partition(":")
                human_rest = humanize_sync_error_message(rest) or rest
                parts.append(f"{prefix}: {human_rest}")
            else:
                parts.append(humanize_sync_error_message(chunk) or chunk)
        return "; ".join(parts)[:2000]

    if _is_technical_dump(text):
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or len(candidate) > 220:
                continue
            if any(marker.lower() in candidate.lower() for marker in _TECHNICAL_MARKERS):
                continue
            if candidate.startswith("["):
                continue
            return candidate[:500]
        return "Ошибка при синхронизации данных. Повторите обновление позже."

    return text[:500]


def present_sync_error(raw: str | None) -> tuple[str | None, str | None]:
    """User-facing message and optional technical details for the UI."""
    if not raw or not raw.strip():
        return None, None
    human = humanize_sync_error_message(raw)
    if human is None:
        return None, None
    details = raw.strip() if _is_technical_dump(raw.strip()) and human != raw.strip() else None
    return human, details
