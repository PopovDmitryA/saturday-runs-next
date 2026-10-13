"""Адрес возврата после входа: страница, с которой человек нажал «Войти».

Адрес приходит от клиента — параметром `?next=` у /login, дальше едет в
state OAuth, в Redis заявки бота, в теле запроса кода. Верить ему нельзя:
это классический открытый редирект («войдите через VK» → и вы на чужом
сайте с нашей кукой в истории). Поэтому сервер пропускает только путь
внутри сайта и проверяет его заново при каждом чтении из хранилища — в
Redis мог лечь адрес, записанный до ужесточения правил.

Что пропускаем: строку от «/», не длиннее MAX_RETURN_PATH_LENGTH, без
схемы и хоста (`//evil.com`, `/\\evil.com`, `https://…` — мимо), без
управляющих символов и без сегментов «.»/«..» — браузер схлопывает их и из
`/x/..//evil.com` получает `//evil.com`. Всё, что браузер мог бы прочитать
двояко (пробел, кавычки, угловые скобки), кодируется процентами: адрес потом
попадает и в заголовок Location, и в HTML-страницу успешного OAuth.

Чего не возвращаем: сам вход и его служебные возвраты от провайдеров,
API, служебный рендер и онбординг — онбординг сам ведёт дальше по `next`.

Клиентская копия правил — frontend/src/lib/loginReturn.ts (safeReturnPath);
менять обе вместе.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

MAX_RETURN_PATH_LENGTH = 1024

# Первый сегмент пути, после которого возвращаться некуда или опасно.
EXCLUDED_FIRST_SEGMENTS = frozenset({"api", "auth", "login", "oauth", "render", "welcome"})

DOT_SEGMENTS = frozenset({".", ".."})

# Разделители пути, строки запроса и фрагмента плюс уже закодированные «%XX»
# остаются как есть; кириллица, пробелы, кавычки и <> кодируются.
_SAFE_CHARS = "/?#&=%:@!$()*+,;-._~[]"


def sanitize_return_path(raw: object) -> str | None:
    """Безопасный путь внутри сайта или None, если возвращать туда нельзя."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_RETURN_PATH_LENGTH:
        return None
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    if "\\" in candidate:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return None
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return None
    decoded_path = unquote(parts.path)
    # Сегменты «.» и «..» (и закодированные %2e) браузер схлопывает при
    # разборе адреса: «/x/..//evil.com» превращается в «//evil.com» — адрес
    # чужого сайта (находка ревью 12.10.2026). Живая страница сайта таких
    # сегментов не содержит — браузер их уже убрал из location.
    if any(segment in DOT_SEGMENTS for segment in decoded_path.split("/")):
        return None
    if decoded_path.startswith("//"):
        return None
    first_segment = decoded_path.lstrip("/").split("/", 1)[0].lower()
    if first_segment in EXCLUDED_FIRST_SEGMENTS:
        return None
    normalized = quote(candidate, safe=_SAFE_CHARS)
    if len(normalized) > MAX_RETURN_PATH_LENGTH:
        return None
    return normalized
