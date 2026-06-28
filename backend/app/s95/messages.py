from __future__ import annotations

from app.s95.errors import S95BanDetected, S95FetchTimeout

S95_ACCESS_DENIED_MESSAGE = (
    "С95 заблокировал доступ с нашего сервера (HTTP 403). "
    "Синхронизация временно приостановлена — попробуйте позже."
)

S95_COOLDOWN_MESSAGE = (
    "С95 временно недоступен — недавно был отказ в доступе. "
    "Новые запросы приостановлены на час, затем проверим доступ снова."
)

S95_PROTECTION_MESSAGE = (
    "С95 временно недоступен — сработала защита сайта. Попробуйте позже."
)

S95_MAINTENANCE = True

S95_MAINTENANCE_MESSAGE = (
    "Обновление данных С95 временно недоступно. "
    "Ожидаемая дата восстановления: 1 июля 2026 года, возможно ранее. "
    "Ваш запрос сохранён — данные обновятся автоматически после восстановления доступа."
)


def s95_user_facing_error(exc: Exception) -> str:
    if isinstance(exc, S95FetchTimeout):
        return "Сейчас идёт другой запрос к С95 — подождите пару минут и повторите."

    if isinstance(exc, S95BanDetected):
        lowered = str(exc).lower()
        if "403" in lowered or "forbidden" in lowered:
            return S95_ACCESS_DENIED_MESSAGE
        if "cooldown" in lowered:
            return S95_COOLDOWN_MESSAGE
        if "429" in lowered or "too many" in lowered:
            return "С95 временно ограничил запросы. Попробуйте обновить позже."
        return S95_PROTECTION_MESSAGE

    text = str(exc).strip()
    if text == "Не удалось определить имя участника":
        return (
            "С95 вернул пустую или неполную страницу профиля. "
            "Возможно, доступ с сервера ограничен — попробуйте позже."
        )
    return text
