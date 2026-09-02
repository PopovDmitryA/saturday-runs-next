"""Сверка версии кода и версии схемы базы."""

from __future__ import annotations

import pytest

from app.db import schema_guard


def _patch(monkeypatch, mine: str | None, theirs: str | None) -> None:
    monkeypatch.setattr(schema_guard, "code_head", lambda *a, **k: mine)
    monkeypatch.setattr(schema_guard, "db_head", lambda *a, **k: theirs)


def test_matching_versions_pass(monkeypatch) -> None:
    _patch(monkeypatch, "065_user_geo_pings", "065_user_geo_pings")
    schema_guard.assert_schema_matches(object())


def test_mismatch_names_both_versions(monkeypatch) -> None:
    """Сообщение обязано назвать обе версии — иначе чинить не по чему."""
    _patch(monkeypatch, "075_page_view_is_bot", "065_user_geo_pings")
    with pytest.raises(schema_guard.SchemaMismatch) as err:
        schema_guard.assert_schema_matches(object(), what="разбор очереди")
    text = str(err.value)
    assert "075_page_view_is_bot" in text
    assert "065_user_geo_pings" in text
    assert "разбор очереди" in text


def test_unknown_version_does_not_block(monkeypatch) -> None:
    """Не смогли определить версию — не мешаем работать.

    Проверка страхует от рассинхрона, но не должна сама становиться причиной
    простоя, если alembic недоступен или таблицы версий нет.
    """
    _patch(monkeypatch, None, "065_user_geo_pings")
    schema_guard.assert_schema_matches(object())
    _patch(monkeypatch, "065_user_geo_pings", None)
    schema_guard.assert_schema_matches(object())


def test_real_code_head_is_readable() -> None:
    """На настоящем дереве голова миграций должна определяться."""
    assert schema_guard.code_head() is not None
