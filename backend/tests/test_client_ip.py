"""Адрес клиента за прокси: подменить IP заголовком нельзя.

Оба nginx ДОПИСЫВАЮТ свой адрес в X-Forwarded-For, поэтому первый элемент
списка — то, что прислал клиент. До 13.09.2026 помощник брал именно его, и
все лимиты «на IP» (вход, коды на почту, регистрации, автоблокировки)
обходились одним заголовком, а чужой адрес можно было загнать в бан.
"""

from __future__ import annotations

from starlette.requests import Request

from app.api import deps
from app.core.client_ip import get_client_ip, is_trusted_proxy


def _request(*, client: tuple[str, int] | None, **headers: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_first_forwarded_hop_is_not_trusted() -> None:
    """Сценарий прода: клиент прислал XFF: 203.0.113.7, host-nginx дописал
    настоящий адрес, docker-nginx — свой. Клиент — предпоследний, не первый."""
    request = _request(
        client=("172.18.0.5", 40000),
        **{"X-Forwarded-For": "203.0.113.7, 5.5.5.5, 127.0.0.1"},
    )
    assert get_client_ip(request) == "5.5.5.5"


def test_x_real_ip_from_our_proxy_wins() -> None:
    """docker-nginx после realip кладёт настоящий адрес в X-Real-IP — ему верим
    раньше любого X-Forwarded-For."""
    request = _request(
        client=("172.18.0.5", 40000),
        **{"X-Real-IP": "5.5.5.5", "X-Forwarded-For": "203.0.113.7, 5.5.5.5"},
    )
    assert get_client_ip(request) == "5.5.5.5"


def test_headers_are_ignored_when_peer_is_not_a_proxy() -> None:
    """Запрос пришёл напрямую с внешнего адреса — заголовки не смотрим вовсе."""
    request = _request(
        client=("198.51.100.9", 40000),
        **{"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "1.1.1.1"},
    )
    assert get_client_ip(request) == "198.51.100.9"


def test_garbage_hops_are_skipped() -> None:
    request = _request(
        client=("127.0.0.1", 1),
        **{"X-Forwarded-For": "not-an-ip, 5.5.5.5, junk, 10.0.0.3"},
    )
    assert get_client_ip(request) == "5.5.5.5"


def test_all_hops_trusted_falls_back_to_peer() -> None:
    request = _request(client=("127.0.0.1", 1), **{"X-Forwarded-For": "10.0.0.1, 172.17.0.1"})
    assert get_client_ip(request) == "127.0.0.1"


def test_no_client_is_unknown() -> None:
    assert get_client_ip(_request(client=None)) == "unknown"


def test_testclient_peer_is_returned_as_is() -> None:
    """TestClient даёт пир «testclient»: не адрес, значит не прокси — возвращаем
    как есть, чтобы тесты роутов вели себя как раньше."""
    request = _request(client=("testclient", 50000), **{"X-Forwarded-For": "1.1.1.1"})
    assert get_client_ip(request) == "testclient"


def test_trusted_proxy_ranges() -> None:
    assert is_trusted_proxy("127.0.0.1")
    assert is_trusted_proxy("172.31.255.254")
    assert is_trusted_proxy("192.168.1.26")
    assert is_trusted_proxy("::1")
    assert not is_trusted_proxy("8.8.8.8")
    assert not is_trusted_proxy("testclient")
    assert not is_trusted_proxy(None)


def test_deps_reexports_the_single_helper() -> None:
    """Вторая копия в api/deps.py удалена: роуты и middleware считают IP одинаково."""
    assert deps.get_client_ip is get_client_ip
