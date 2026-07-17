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

# Ошибки синка приходят с префиксом платформы («s95: …», «parkrun: …») — его
# ставит run_*_user_sync. Тексты про бан/403 обязаны называть ту платформу,
# которая реально упала: слова «cooldown until» и «ban/protection» есть у всех
# трёх платформ, и раньше бан parkrun подписывался как проблема С95.
_PLATFORM_NAMES = {
    "five_verst": "5 вёрст",
    "s95": "С95",
    "parkrun": "parkrun",
    "runpark": "RunPark",
}

_PLATFORM_PREFIX_RE = re.compile(r"^(five_verst|s95|parkrun|runpark):\s*(.*)$", re.DOTALL)


def _platform_site(platform: str | None) -> str:
    """«Сайт С95» / «Сайт 5 вёрст» / «Сайт платформы» — форма годится для любой."""
    return f"Сайт {_PLATFORM_NAMES.get(platform or '', 'платформы')}"


def _platform_prefixed_chunks(text: str) -> list[str] | None:
    """Куски «s95: …; parkrun: …» — только если платформ действительно несколько.

    У одиночной ошибки «; » часто встречается внутри самого текста (например, в
    parkrun-диагностике «markers=captcha; likely captcha/WAF»), и делить её по
    этому разделителю нельзя.
    """
    if "; " not in text:
        return None
    chunks = [chunk.strip() for chunk in text.split("; ") if chunk.strip()]
    prefixed = sum(1 for chunk in chunks if _PLATFORM_PREFIX_RE.match(chunk))
    return chunks if prefixed >= 2 else None


def _is_technical_dump(text: str) -> bool:
    if len(text) > 320:
        return True
    lower = text.lower()
    return any(marker.lower() in lower for marker in _TECHNICAL_MARKERS)


def humanize_sync_error_message(raw: str | None, platform: str | None = None) -> str | None:
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

    chunks = _platform_prefixed_chunks(text)
    if chunks is not None:
        parts: list[str] = []
        for chunk in chunks:
            match = _PLATFORM_PREFIX_RE.match(chunk)
            if match:
                code, rest = match.group(1), match.group(2)
                parts.append(f"{code}: {humanize_sync_error_message(rest, code) or rest}")
            else:
                parts.append(humanize_sync_error_message(chunk, platform) or chunk)
        return "; ".join(parts)[:2000]

    # Префикс не срезаем (сырой текст в админке должен остаться узнаваемым) —
    # берём из него только платформу для сообщений ниже.
    prefix_match = _PLATFORM_PREFIX_RE.match(text)
    if prefix_match:
        platform = prefix_match.group(1)

    lower = text.lower()
    if "uniqueviolation" in lower or "duplicate key" in lower:
        event_match = re.search(r"['\"]event_name['\"][^'\"]*['\"]([^'\"]+)['\"]", text)
        if event_match:
            return f"Конфликт данных: событие «{event_match.group(1)}» уже есть в базе."
        return "Конфликт данных: такая запись о мероприятии уже есть в базе."

    if "rate limit" in lower or "429" in text:
        return "Сайт платформы временно ограничил запросы. Попробуйте обновить позже."

    if "403" in text or "forbidden" in lower:
        return (
            f"{_platform_site(platform)} заблокировал доступ с нашего сервера (HTTP 403). "
            "Синхронизация временно приостановлена — попробуйте позже."
        )

    if "cooldown until" in lower or "ban/protection" in lower:
        return (
            f"{_platform_site(platform)} временно недоступен — недавно был отказ в доступе. "
            "Новые запросы приостановлены, затем проверим доступ снова."
        )

    if text.strip() == "Не удалось определить имя участника":
        return (
            f"{_platform_site(platform)} вернул неполную страницу профиля. "
            "Возможно, доступ с сервера ограничен — попробуйте позже."
        )

    if "timeout" in lower or "timed out" in lower:
        return "Превышено время ожидания ответа от сайта платформы."

    if "connection" in lower and ("refused" in lower or "failed" in lower):
        return "Не удалось подключиться к сайту платформы. Попробуйте позже."

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
