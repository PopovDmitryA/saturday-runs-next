from __future__ import annotations

import pytest

from app.core import email_address


@pytest.mark.parametrize(
    "value",
    ["runner@example.com", "ivan.petrov@yandex.ru", "a+b@gmail.com", "Runner@Mail.RU"],
)
def test_valid_addresses(value: str) -> None:
    assert email_address.is_valid(value)


@pytest.mark.parametrize(
    "value",
    ["", "   ", "runner", "runner@", "@example.com", "runner@example", "a b@example.com"],
)
def test_invalid_addresses(value: str) -> None:
    assert not email_address.is_valid(value)


def test_plus_alias_is_the_same_mailbox() -> None:
    """Самый дешёвый способ наплодить «разные» адреса."""
    assert email_address.same_mailbox("ivan+run@example.com", "ivan@example.com")


def test_gmail_ignores_dots_and_second_domain() -> None:
    assert email_address.same_mailbox("i.van.petrov@gmail.com", "ivanpetrov@gmail.com")
    assert email_address.same_mailbox("ivan@googlemail.com", "ivan@gmail.com")


def test_yandex_synonyms_and_dot_dash() -> None:
    assert email_address.same_mailbox("ivan.petrov@ya.ru", "ivan-petrov@yandex.ru")
    assert email_address.same_mailbox("ivan@yandex.com", "ivan@yandex.ru")
    assert email_address.same_mailbox("ivan@yandex.by", "ivan@yandex.ru")


def test_mailru_family_are_different_mailboxes() -> None:
    """inbox/bk/list — разные ящики разных людей, склеивать нельзя."""
    assert not email_address.same_mailbox("ivan@mail.ru", "ivan@inbox.ru")
    assert not email_address.same_mailbox("ivan@bk.ru", "ivan@list.ru")


def test_dots_are_significant_for_unknown_providers() -> None:
    assert not email_address.same_mailbox("i.van@example.com", "ivan@example.com")


def test_case_is_ignored() -> None:
    assert email_address.same_mailbox("Ivan@Example.COM", "ivan@example.com")


def test_disposable_domains_are_detected() -> None:
    assert email_address.is_disposable("someone@mailinator.com")
    assert email_address.is_disposable("someone@temp-mail.org")
    # Одноразовые сервисы раздают поддомены пачками.
    assert email_address.is_disposable("someone@inbox.yopmail.com")


def test_normal_domains_are_not_disposable() -> None:
    for value in ["runner@gmail.com", "runner@yandex.ru", "runner@mail.ru", "runner@run5k.run"]:
        assert not email_address.is_disposable(value)
