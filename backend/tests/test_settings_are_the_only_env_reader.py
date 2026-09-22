"""Сторож: приложение читает окружение только через Settings.

Почему это важно. Settings (pydantic-settings) берёт значения и из .env,
смонтированного в контейнер, а `os.getenv` видит лишь окружение процесса.
Разница уже стоила рабочего табло: PM_WORLD_DSN лежал в .env, воркер его не
видел, и снимок /hq молча возвращал «не задан» — пока переменную не
продублировали в docker-compose. Каждое новое прямое чтение окружения — та же
засада, отложенная на потом (21.09.2026 последние такие чтения переведены в
поля Settings: legacy_database_url, parkrun_monitoring_dir,
parkrun_fetch_proxies).

Исключения — три места, где Settings принципиально нет:
  * app/config.py — сама реализация настроек;
  * app/db/session.py — ручки движка БД, которые читаются до сборки Settings
    и различаются у api/воркеров (DB_STATEMENT_TIMEOUT, PROD_DB_TARGET);
  * app/core/runtime_env.py — определение «мы внутри pytest».
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {
    APP_DIR / "config.py",
    APP_DIR / "db" / "session.py",
    APP_DIR / "core" / "runtime_env.py",
}


def _reads_environment(tree: ast.AST) -> list[int]:
    """Строки, где берут os.environ / os.getenv (в любом виде обращения)."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in {"environ", "getenv"}:
            lines.append(node.lineno)
    return lines


def test_no_direct_environment_access_in_app() -> None:
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [f"{path.relative_to(APP_DIR.parent)}:{line}" for line in _reads_environment(tree)]
    assert not offenders, "окружение читается мимо Settings: " + ", ".join(offenders)


def test_settings_carry_the_fields_that_used_to_be_read_from_env() -> None:
    from app.config import Settings

    for field in ("legacy_database_url", "parkrun_monitoring_dir", "parkrun_fetch_proxies"):
        assert field in Settings.model_fields, field
