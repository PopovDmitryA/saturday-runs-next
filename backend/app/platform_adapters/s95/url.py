from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

S95_DOMAINS = ("s95.ru", "s95.rs", "s95.by")
ATHLETE_PATH_RE = re.compile(r"/athletes/(\d+)/?", re.IGNORECASE)
# ID участника из ссылки: https://s95.ru/athletes/5207/ → 5207
ATHLETE_ID_RE = re.compile(r"^\d{1,9}$")
# Штрихкод (A7035519) — это parkrun-код из профиля, а НЕ ID участника С95.
BARCODE_RE = re.compile(r"^[AА]\d{4,12}$", re.IGNORECASE)


class InvalidProfileUrlError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedAthleteUrl:
    domain: str
    external_user_id: str
    canonical_url: str


def is_valid_profile_url(url: str) -> bool:
    try:
        parse_profile_input(url)
        return True
    except InvalidProfileUrlError:
        return False


def parse_profile_input(raw: str) -> ParsedAthleteUrl:
    """Ссылка на профиль или числовой ID участника из неё.

    В ссылке https://s95.ru/athletes/5207/ значащая часть — только 5207, и на
    телефоне проще ввести это число, чем копировать весь адрес. Принимаем оба
    варианта, а также ссылку без схемы (s95.ru/athletes/5207).
    """
    value = raw.strip()
    if not value:
        raise InvalidProfileUrlError("Укажите ссылку на профиль С95 или ID участника")

    if ATHLETE_ID_RE.match(value):
        return parse_athlete_url(athlete_url(value))

    if BARCODE_RE.match(value):
        raise InvalidProfileUrlError(
            "Это штрихкод, а для С95 нужен числовой ID участника из ссылки на профиль — "
            "например 5207 для https://s95.ru/athletes/5207/"
        )

    # Без этой проверки «hello» превращается в https://hello и человек получает
    # ответ про поддерживаемые домены вместо подсказки про формат ввода.
    if not any(char in value for char in ":/."):
        raise InvalidProfileUrlError(
            "Ожидается ссылка вида https://s95.ru/athletes/5207/ или ID участника (5207)"
        )

    if "://" not in value:
        value = f"https://{value}"
    return parse_athlete_url(value)


def parse_athlete_url(url: str) -> ParsedAthleteUrl:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise InvalidProfileUrlError("URL должен начинаться с http:// или https://")
    if not parsed.netloc:
        raise InvalidProfileUrlError("Некорректный URL профиля")

    host = parsed.netloc.lower().removeprefix("www.")
    if not any(host == domain or host.endswith(f".{domain}") for domain in S95_DOMAINS):
        raise InvalidProfileUrlError("Поддерживаются только домены s95.ru, s95.rs и s95.by")

    match = ATHLETE_PATH_RE.search(parsed.path)
    if not match:
        raise InvalidProfileUrlError(
            "Ожидается ссылка вида https://s95.ru/athletes/12345/ или ID участника (12345)"
        )

    external_user_id = match.group(1)
    canonical = f"https://{host}/athletes/{external_user_id}/"
    return ParsedAthleteUrl(domain=host, external_user_id=external_user_id, canonical_url=canonical)


def athlete_url(external_user_id: str, *, domain: str = "s95.ru") -> str:
    return f"https://{domain}/athletes/{external_user_id}/"
