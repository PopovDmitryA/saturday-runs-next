"""Ссылка на профиль RunPark — только по идентификатору аккаунта.

Страница кармы открывается по GUID (проверено 14.09.2026: чужой профиль
из-под логина открывается нормально). А ключи «barcode:A…» и «anon:…» мы
придумали сами, чтобы различать строки протокола без аккаунта: профиля у таких
людей на RunPark нет, и ссылка вела в никуда. На проде таких было 465.
"""

from __future__ import annotations

from app.runpark.mappings import runpark_profile_url


def test_account_guid_gets_a_link() -> None:
    guid = "02723844-FD1E-4656-B656-288A42566B22"
    assert runpark_profile_url(guid) == f"https://runpark.ru/Account/Karmas/{guid}"


def test_lowercase_guid_also_works() -> None:
    assert runpark_profile_url("02723844-fd1e-4656-b656-288a42566b22") is not None


def test_barcode_and_anon_keys_get_no_link() -> None:
    # Это не идентификаторы аккаунта, а наши внутренние ключи.
    assert runpark_profile_url("barcode:A790152825") is None
    assert runpark_profile_url("anon:6E99C76A-5F73-4BFC-9256-513738BD3C8B") is None
    assert runpark_profile_url("A790152825") is None
    assert runpark_profile_url("") is None
    assert runpark_profile_url(None) is None
