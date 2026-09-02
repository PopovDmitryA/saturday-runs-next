"""Сверка версии кода и версии схемы базы.

Зачем. Код сайта теперь исполняется не только в контейнерах на проде, но и на
домашнем сервере — там разбирается очередь профилей. Оба ходят в ОДНУ базу,
поэтому обязаны совпадать по схеме.

02.09.2026 они разошлись, и это стоило часа разбирательств: домашняя копия была
на десять миграций новее продовой базы, а проявилось это как

    column users.display_name_style does not exist

внутри разбора очереди — сообщение, по которому причина не видна совсем.

Проверка ниже делает расхождение громким и понятным до первого запроса.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SchemaMismatch(RuntimeError):
    """Код и база разошлись — работать нельзя."""


def code_head(backend_root: Path | None = None) -> str | None:
    """Головная миграция в ЭТОМ коде. None — alembic недоступен."""
    root = backend_root or Path(__file__).resolve().parents[2]
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:  # noqa: BLE001 — проверка не должна ронять запуск сама
        logger.warning("не удалось определить головную миграцию кода: %r", exc)
        return None


def db_head(db) -> str | None:  # noqa: ANN001 — Session, но без импорта ради лёгкости
    from sqlalchemy import text

    try:
        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning("не удалось прочитать версию схемы из базы: %r", exc)
        return None


def assert_schema_matches(db, *, what: str = "процесс") -> None:
    """Останавливает запуск, если код новее или старше базы.

    Не выясняем, кто из них прав: чинить всё равно человеку, а нам важно
    назвать обе версии и не делать вид, что всё в порядке.
    """
    mine, theirs = code_head(), db_head(db)
    if mine is None or theirs is None:
        logger.info("сверка схемы пропущена (код %s, база %s)", mine, theirs)
        return
    if mine == theirs:
        logger.info("схема совпадает: %s", mine)
        return
    raise SchemaMismatch(
        f"{what} остановлен: код и база разошлись.\n"
        f"  миграции в коде : {mine}\n"
        f"  версия в базе   : {theirs}\n"
        "Обнови копию кода до той версии, что выкачена на прод "
        "(см. правило про синхронизацию домашнего сервера в CLAUDE.md)."
    )
