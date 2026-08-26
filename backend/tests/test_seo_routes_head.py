"""Корневые SEO-роуты обязаны отвечать и на HEAD.

Краулеры превью (Telegram, ВК, WhatsApp) часто шлют HEAD первым — узнать
тип и размер, — и только потом GET. Роуты были объявлены как @router.get,
FastAPI сам HEAD не подхватывает, и бот получал 405 вместо заголовков.
Человек дефекта не видел: он попадает в статику nginx, которая HEAD умеет,
поэтому поймать это можно было только запросом с ботским User-Agent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

BOT_UA = {"User-Agent": "TelegramBot (like TwitterBot)"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/robots.txt", "/__prerender/locations/kuzminki", "/__prerender/"],
)
def test_head_is_allowed(client: TestClient, path: str) -> None:
    response = client.head(path, headers=BOT_UA)
    assert response.status_code != 405, f"{path} отвечает 405 на HEAD"


def test_head_matches_get_headers(client: TestClient) -> None:
    """У HEAD те же заголовки, что у GET, но без тела."""
    head = client.head("/robots.txt", headers=BOT_UA)
    get = client.get("/robots.txt", headers=BOT_UA)
    assert head.status_code == get.status_code
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.content == b""
    assert get.content


def test_head_keeps_honest_404(client: TestClient) -> None:
    """Несуществующий адрес и по HEAD отвечает 404, а не 200 и не 405."""
    response = client.head("/__prerender/something-strange", headers=BOT_UA)
    assert response.status_code == 404
