"""Имя участника, которое можно показать посторонним.

Яндекс ID у части аккаунтов (почта на своём домене, «лёгкие» аккаунты)
отдаёт логином и display_name весь адрес почты. Он уезжал в имя профиля, а
оттуда — в <title> публичной карточки: на 03.10.2026 в индексе Яндекса было
пять профилей с адресом почты в заголовке. Адрес — персональные данные, и
показывать его чужим нельзя ни роботу, ни человеку.

Правило то же, что у входа по коду на почту (email_auth_service): от адреса
остаётся часть до «@».
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def strip_email(name: str | None) -> str | None:
    """«ivan.p@mail.ru» → «ivan.p»; имя без адреса возвращается как есть."""
    if name is None:
        return None
    cleaned = _EMAIL_RE.sub(lambda match: match.group(1), name).strip()
    return cleaned or None


def has_email(name: str | None) -> bool:
    return bool(name) and _EMAIL_RE.search(name or "") is not None
