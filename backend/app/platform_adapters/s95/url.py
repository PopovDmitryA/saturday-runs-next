from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

S95_DOMAINS = ("s95.ru", "s95.rs", "s95.by")
ATHLETE_PATH_RE = re.compile(r"/athletes/(\d+)/?", re.IGNORECASE)


class InvalidProfileUrlError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedAthleteUrl:
    domain: str
    external_user_id: str
    canonical_url: str


def is_valid_profile_url(url: str) -> bool:
    try:
        parse_athlete_url(url)
        return True
    except InvalidProfileUrlError:
        return False


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
        raise InvalidProfileUrlError("Ожидается ссылка вида https://s95.ru/athletes/12345/")

    external_user_id = match.group(1)
    canonical = f"https://{host}/athletes/{external_user_id}/"
    return ParsedAthleteUrl(domain=host, external_user_id=external_user_id, canonical_url=canonical)


def athlete_url(external_user_id: str, *, domain: str = "s95.ru") -> str:
    return f"https://{domain}/athletes/{external_user_id}/"
