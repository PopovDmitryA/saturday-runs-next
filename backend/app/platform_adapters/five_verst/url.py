from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

USERSTATS_URL_RE = re.compile(
    r"^https?://(?:www\.)?5verst\.ru/userstats/(?P<user_id>\d+)/?$",
    re.IGNORECASE,
)
# Код участника / штрихкод: 790096427, A790096427, А790096427
BARCODE_RE = re.compile(r"^([AАaа])?(\d{5,12})$")

_BARCODE_PREFIXES = ("A", "a", "\u0410", "\u0430")  # Latin A / Cyrillic А


@dataclass(frozen=True)
class ParsedProfileUrl:
    user_id: str
    canonical_url: str


class InvalidProfileUrlError(ValueError):
    pass


def _normalize_user_id(raw: str) -> str:
    cleaned = raw.strip().replace(" ", "")
    if len(cleaned) > 1 and cleaned[0] in _BARCODE_PREFIXES:
        return cleaned[1:]
    return cleaned


def _parse_profile_url(url: str) -> ParsedProfileUrl:
    trimmed = url.strip()
    if not trimmed:
        raise InvalidProfileUrlError("URL профиля не может быть пустым")

    if not trimmed.startswith(("http://", "https://")):
        trimmed = f"https://{trimmed}"

    match = USERSTATS_URL_RE.match(trimmed)
    if not match:
        raise InvalidProfileUrlError(
            "Некорректная ссылка. Ожидается формат: https://5verst.ru/userstats/{id}/"
        )

    user_id = match.group("user_id")
    return ParsedProfileUrl(user_id=user_id, canonical_url=f"https://5verst.ru/userstats/{user_id}/")


def parse_profile_input(raw: str) -> ParsedProfileUrl:
    value = raw.strip()
    if not value:
        raise InvalidProfileUrlError("Укажите ссылку на профиль 5 вёрст или код участника")

    lower = value.lower()
    if "://" in value or "5verst.ru" in lower or lower.startswith("www."):
        return _parse_profile_url(value if "://" in value else f"https://{value}")

    compact = value.replace(" ", "")
    barcode_match = BARCODE_RE.match(compact)
    if barcode_match:
        user_id = _normalize_user_id(compact)
        if not user_id.isdigit():
            raise InvalidProfileUrlError("Код участника должен содержать только цифры")
        return ParsedProfileUrl(user_id=user_id, canonical_url=userstats_url(user_id))

    raise InvalidProfileUrlError(
        "Ожидается ссылка https://5verst.ru/userstats/…, числовой код (790096427) "
        "или штрихкод A790096427 / А790096427 (латиница или кириллица)"
    )


def normalize_profile_url(url: str) -> ParsedProfileUrl:
    """Совместимость: принимает ссылку или код участника."""
    return parse_profile_input(url)


def is_valid_profile_url(url: str) -> bool:
    try:
        parse_profile_input(url)
        return True
    except InvalidProfileUrlError:
        return False


def userstats_url(user_id: str) -> str:
    parsed = urlparse(f"https://5verst.ru/userstats/{user_id}/")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
