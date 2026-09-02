"""Пул исходящих прокси для фетча parkrun."""

from __future__ import annotations

from app.parkrun.fetch.proxy_pool import ENV_VAR, ProxyPool, load_proxies

THREE = [
    "socks5://127.0.0.1:10865",
    "socks5://127.0.0.1:10866",
    "socks5://127.0.0.1:10867",
]


def test_empty_pool_means_direct_connection() -> None:
    """Без прокси всё должно работать как раньше — на проде их нет."""
    pool = ProxyPool([])
    assert not pool.enabled
    assert len(pool) == 0
    assert pool.current() is None
    assert pool.rotate() is None
    assert "прям" in pool.describe()


def test_rotation_goes_in_circle() -> None:
    pool = ProxyPool(THREE, shuffle=False)
    assert pool.current() == THREE[0]
    assert pool.rotate() == THREE[1]
    assert pool.rotate() == THREE[2]
    assert pool.rotate() == THREE[0]
    assert pool.rotations == 3


def test_start_position_is_random_but_valid() -> None:
    """Каждый прогон начинается со случайного выхода.

    Иначе первый в списке собирает все капчи, а остальные простаивают.
    """
    starts = {ProxyPool(THREE).current() for _ in range(60)}
    assert starts <= set(THREE)
    assert len(starts) > 1, "начальная точка обязана меняться между прогонами"


def test_parsing_ignores_blanks_and_spaces() -> None:
    assert load_proxies(" a , , b ,c ") == ["a", "b", "c"]
    assert load_proxies("") == []
    assert load_proxies("   ") == []


def test_parsing_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, "socks5://x:1,socks5://y:2")
    assert load_proxies() == ["socks5://x:1", "socks5://y:2"]
    monkeypatch.delenv(ENV_VAR)
    assert load_proxies() == []
