"""Внешних ссылок на профили в чужих системах сайт не публикует.

Люди, чьи результаты мы собираем, согласия на обработку своих данных нам не
давали. Поэтому ни в одном ответе API не должно быть адреса их профиля на
5verst / S95 / parkrun / RunPark, а во фронтенде — кликабельной ссылки на него
(Дмитрий 04.09.2026). Исключение одно: разбор адреса, который человек вставил
сам, привязывая СВОЙ профиль.
"""

from __future__ import annotations

import pathlib
import re

SCHEMAS = pathlib.Path(__file__).resolve().parents[1] / "app" / "schemas"

# В контейнере исходники фронта смонтированы в /frontend-src, в репозитории
# лежат рядом с backend. Без этого поиска проверка молча проходила бы вхолостую
# на несуществующей папке — что и случилось при первом прогоне.
_FRONTEND_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src",
    pathlib.Path("/frontend-src"),
)


def _frontend_dir() -> pathlib.Path:
    for candidate in _FRONTEND_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"не нашёл исходники фронта: {[str(p) for p in _FRONTEND_CANDIDATES]}")

# Привязка своего профиля: человек сам вставил адрес, это его собственные данные.
ALLOWED_SCHEMAS = {"profiles.py"}


def test_api_schemas_do_not_expose_foreign_profile_urls() -> None:
    offenders: list[str] = []
    for path in sorted(SCHEMAS.glob("*.py")):
        if path.name in ALLOWED_SCHEMAS:
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if re.search(r"^\s*profile_urls?\s*:", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "адрес чужого профиля не должен уезжать в браузер:\n" + "\n".join(offenders)


def test_frontend_has_no_links_to_foreign_profiles() -> None:
    """`<a href={...profile_url...}>` во фронтенде быть не должно.

    Единственное место, где ссылка допустима, — карточка разбора вставленного
    адреса в ParticipantNameSearch: там человек привязывает свой профиль.
    """
    frontend = _frontend_dir()
    allowed = frontend / "components" / "ParticipantNameSearch.tsx"
    pattern = re.compile(r"href=\{[^}]*profile_url", re.I)
    offenders: list[str] = []
    files = list(frontend.rglob("*.tsx"))
    assert len(files) > 50, f"подозрительно мало файлов для проверки: {len(files)}"
    for path in files:
        text = path.read_text()
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                if path == allowed:
                    continue
                offenders.append(f"{path.relative_to(frontend)}:{number}: {line.strip()}")
    assert not offenders, "ссылки на чужие профили:\n" + "\n".join(offenders)
