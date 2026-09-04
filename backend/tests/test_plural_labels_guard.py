"""Число в подписи не должно печататься дважды.

`pluralizeRu` возвращает число ВМЕСТЕ с формой («13 недель»), а не одну форму.
Поставленное перед ней число давало в фильтре «Пропуск не больше» варианты
вида «13 13 недель» (Дмитрий 04.09.2026). Для случая, когда нужна только форма,
есть отдельная `pluralFormRu`.

Заодно проверяем сам набор форм: тройка идёт «неделя, недели, недель» —
именительный, родительный единственного, родительный множественного. Сдвиг в
ней тихо ломает согласование: «2 недель» вместо «2 недели».
"""

from __future__ import annotations

import pathlib
import re

_FRONTEND_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src",
    pathlib.Path("/frontend-src"),
)


def _frontend_dir() -> pathlib.Path:
    for candidate in _FRONTEND_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"не нашёл исходники фронта: {[str(p) for p in _FRONTEND_CANDIDATES]}")


# Число прямо перед вызовом: `${value} ${pluralizeRu(...)}` или `{n} {pluralizeRu(...)}`.
_DOUBLE_COUNT = re.compile(r"\$\{[^{}]+\}\s*\$\{pluralizeRu\(|\{\w+\}\s*\{pluralizeRu\(")


def test_no_duplicated_count_before_pluralize() -> None:
    offenders: list[str] = []
    root = _frontend_dir()
    for path in sorted(root.rglob("*.tsx")):
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if "pluralizeRu(" in line and _DOUBLE_COUNT.search(line):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert not offenders, (
        "число печатается дважды — pluralizeRu уже включает его. "
        "Нужна только форма — берите pluralFormRu:\n" + "\n".join(offenders)
    )


def test_week_forms_are_not_shifted() -> None:
    """Тройка форм недели должна начинаться с именительного падежа."""
    offenders: list[str] = []
    root = _frontend_dir()
    for path in sorted(root.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\[\s*\"(недел[^\"]*)\"\s*,\s*\"(недел[^\"]*)\"", text):
            if match.group(1) != "неделя" or match.group(2) != "недели":
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(root)}:{line}: {match.group(0)}")
    assert not offenders, (
        "формы недели сдвинуты, ожидается [\"неделя\", \"недели\", \"недель\"]:\n"
        + "\n".join(offenders)
    )
