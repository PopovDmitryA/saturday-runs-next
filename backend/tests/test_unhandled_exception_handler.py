"""Глобальный обработчик 500: прод не отдаёт наружу текст исключения.

До 13.09.2026 условие было перевёрнуто — в проде (app_debug=False) ответ
содержал «TypeName: текст», то есть SQL с параметрами, пути, адреса внешних
систем; в debug — только текст. Обработчик подключаем к отдельному
приложению, чтобы не вешать «падающий» роут на боевой app.main.app.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main_module


def _client(monkeypatch: pytest.MonkeyPatch, *, debug: bool) -> TestClient:
    monkeypatch.setattr(main_module.settings, "app_debug", debug)
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, main_module.unhandled_exception_handler)

    @test_app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-sql-with-params")

    return TestClient(test_app, raise_server_exceptions=False)


def test_prod_hides_exception_text_but_gives_error_id(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, debug=False).get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert "secret" not in response.text
    assert "RuntimeError" not in response.text
    assert body["error_id"] in body["detail"]
    assert len(body["error_id"]) == 8


def test_debug_shows_type_and_message(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, debug=True).get("/boom")
    assert response.status_code == 500
    assert response.json()["detail"] == "RuntimeError: secret-sql-with-params"
