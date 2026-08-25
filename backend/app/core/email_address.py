"""Разбор и нормализация почтовых адресов.

Задача одна: понять, что два адреса ведут в один и тот же ящик. Без этого
вход по почте открывает ровно ту дыру, из-за которой мы всё и затеяли —
«пятнадцать почт на одного человека»: `ivan+1@`, `ivan+2@`, `i.van@` и
`ivan@ya.ru` выглядят как разные люди, а письма падают в один ящик.

Что учитываем:

* плюс-алиасы (`ivan+run@`) — срезаем у всех: если провайдер плюс-адресацию
  не поддерживает, такого ящика попросту не существует;
* Gmail не различает точки в имени и живёт на двух доменах;
* у Яндекса точка равна дефису, а `ya.ru`, `yandex.com`, `yandex.by` и
  прочие — синонимы одного ящика.

Чего НЕ делаем: не склеиваем `mail.ru` с `inbox.ru`, `bk.ru` и `list.ru`.
Это выглядит похоже, но там разные ящики у разных людей, и склейка отдала бы
чужую почту.

Нормализованный вид — только для сравнения и счётчиков. Письма шлём на адрес
в том виде, в каком его ввёл человек.
"""

from __future__ import annotations

import re

# Намеренно не RFC-полная: почтовый мир допускает адреса, которые ни один
# провайдер не выдаёт. Нам нужно отсечь опечатки, а владение ящиком всё равно
# проверит код из письма.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}
_YANDEX_DOMAINS = {
    "yandex.ru",
    "yandex.com",
    "yandex.by",
    "yandex.kz",
    "yandex.ua",
    "yandex.com.tr",
    "ya.ru",
}

# Ящики «на десять минут». Список короткий и намеренно неполный: он отсекает
# ленивый обход, а не всех решительных. Полноту тут не догнать — домены
# заводятся быстрее, чем мы их вписываем; настоящий барьер это лимит
# регистраций и то, что аккаунт без привязки бегового ID ничего не весит.
_DISPOSABLE_DOMAINS = frozenset(
    {
        "10minutemail.com",
        "20minutemail.com",
        "33mail.com",
        "burnermail.io",
        "dropmail.me",
        "emailondeck.com",
        "fakeinbox.com",
        "getnada.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "guerrillamail.org",
        "maildrop.cc",
        "mailinator.com",
        "mailnesia.com",
        "mohmal.com",
        "mytemp.email",
        "sharklasers.com",
        "spam4.me",
        "spamgourmet.com",
        "temp-mail.io",
        "temp-mail.org",
        "tempail.com",
        "tempmail.dev",
        "tempmailo.com",
        "throwawaymail.com",
        "trashmail.com",
        "trashmail.de",
        "yopmail.com",
        "yopmail.fr",
        "yopmail.net",
    }
)


def is_valid(raw: str) -> bool:
    value = raw.strip()
    return bool(value) and len(value) <= 254 and bool(_EMAIL_RE.match(value))


def split(raw: str) -> tuple[str, str]:
    """Вернуть (локальная часть, домен) в нижнем регистре."""
    local, _, domain = raw.strip().lower().partition("@")
    return local, domain


def is_disposable(raw: str) -> bool:
    _, domain = split(raw)
    if not domain:
        return False
    if domain in _DISPOSABLE_DOMAINS:
        return True
    # Сервисы одноразовой почты раздают поддомены пачками (`foo.yopmail.com`).
    return any(domain.endswith(f".{known}") for known in _DISPOSABLE_DOMAINS)


def normalize(raw: str) -> str:
    """Свести адрес к виду, по которому сравниваем ящики.

    Для неизвестных провайдеров ограничиваемся регистром и плюс-алиасом:
    точки в имени у большинства почт значимы, и трогать их нельзя.
    """
    local, domain = split(raw)
    if not local or not domain:
        return raw.strip().lower()

    local = local.split("+", 1)[0]

    if domain in _GMAIL_DOMAINS:
        return f"{local.replace('.', '')}@gmail.com"

    if domain in _YANDEX_DOMAINS:
        return f"{local.replace('.', '-')}@yandex.ru"

    return f"{local}@{domain}"


def same_mailbox(first: str, second: str) -> bool:
    return normalize(first) == normalize(second)
