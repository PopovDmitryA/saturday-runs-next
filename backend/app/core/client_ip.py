"""Адрес клиента за прокси — один помощник на всё приложение.

Топология прода: host-nginx (TLS) → docker-nginx → uvicorn. Оба nginx пишут
X-Forwarded-For через $proxy_add_x_forwarded_for, то есть ДОПИСЫВАЮТ свой
$remote_addr к тому, что прислал клиент. Значит первый элемент списка — то,
что клиент написал сам, и верить ему нельзя: до 13.09.2026 две копии этого
помощника (здесь и в api/deps.py) брали именно его, и любой мог подменить
адрес одним заголовком — лимиты входа и кодов на почту, автоблокировки,
лимит регистраций, журнал входов считались по чужому IP.

Кому верим. docker-nginx (nginx/conf.d/default.conf) через realip-модуль
восстанавливает настоящий адрес из X-Forwarded-For, доверяя только host-nginx
и docker-сети, и кладёт его в X-Real-IP. Отсюда порядок:

1. заголовки вообще учитываются, только если сам TCP-пир — доверенный прокси
   (loopback или приватная сеть): запрос, пришедший в uvicorn напрямую с
   внешнего адреса, подменить IP не может;
2. X-Real-IP — его ставит наш nginx, и после realip там настоящий клиент;
3. иначе X-Forwarded-For справа налево, пропуская доверенные адреса: первый
   чужой и есть клиент (ровно то, что делает real_ip_recursive on);
4. иначе — адрес соединения.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

# Loopback + приватные диапазоны: host-nginx ходит с 127.0.0.1, docker-сети
# берут адреса из 172.16/12 и 192.168/16 (пулы Docker по умолчанию), 10/8 —
# на случай своей сети. Публичный адрес прокси здесь появиться не должен.
TRUSTED_PROXY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)

UNKNOWN_IP = "unknown"


def _parse_ip(value: str | None) -> str | None:
    """Строка → нормализованный адрес; мусор (в т.ч. «testclient») → None."""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def is_trusted_proxy(host: str | None) -> bool:
    ip = _parse_ip(host)
    if ip is None:
        return False
    addr = ipaddress.ip_address(ip)
    return any(addr in network for network in TRUSTED_PROXY_NETWORKS)


def get_client_ip(request: Request) -> str:
    """Best-effort адрес клиента за nginx; «unknown», если соединения нет."""
    peer = request.client.host if request.client is not None else None
    if not is_trusted_proxy(peer):
        # Пир не наш прокси — заголовкам веры нет, адрес соединения и есть клиент.
        return peer or UNKNOWN_IP

    real_ip = _parse_ip(request.headers.get("X-Real-IP"))
    if real_ip is not None:
        return real_ip

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    for hop in reversed(forwarded_for.split(",")):
        ip = _parse_ip(hop)
        if ip is None:
            # Не адрес — пропускаем, не доверяя и не останавливаясь.
            continue
        if not is_trusted_proxy(ip):
            return ip

    return peer or UNKNOWN_IP
