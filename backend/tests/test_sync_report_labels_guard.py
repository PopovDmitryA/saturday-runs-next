"""Счётчик из полезной нагрузки задачи обязан иметь русскую подпись.

`field_label` при отсутствии ключа в FIELD_LABELS отдаёт сам ключ, и тот молча
уезжает в сводку «Автообновление» как есть. 13.09.2026 Дмитрий поймал в письме
две таких строки среди русских: «checked: 852» от наблюдателя за протоколами
5 вёрст и «events_unchanged: 21» от синка RunPark.

Сторож разбирает словари, которые становятся полезной нагрузкой прогона:
`return {...}` в задачах воркера и `as_dict()` у классов результата в синках —
и требует подпись для каждого ключа, похожего на счётчик. Непокрытые ключи, найденные при написании сторожа, лежат
в KNOWN_UNLABELED: это долг, а не разрешение. Список только сокращается —
стоит ключу получить подпись, тест потребует убрать его отсюда.
"""

from __future__ import annotations

import ast
import pathlib

from app.services.sync_report_labels import DETAIL_LIST_KEYS, FIELD_LABELS

# Не счётчики: их отбрасывает сам extract_metrics. «outcomes» у parkrun —
# словарь «участник → исход», а словари туда не проходят вовсе (в метрики
# берутся int/float/str/bool и длина списка); рядом уже лежит его счётчик
# «processed». Статически отличить dict от счётчика нельзя, поэтому явно.
NON_METRIC_KEYS = {"errors", "skipped", "reason", "outcomes"}

# Долга больше нет: 13.09.2026 разобрали все 46 ключей, что нашлись при
# написании сторожа. Список оставлен пустым намеренно — если новый счётчик
# почему-то нельзя подписать сразу, его кладут сюда с пояснением, и второй тест
# заставит убрать запись, как только подпись появится.
KNOWN_UNLABELED: set[str] = set()

_APP_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[1] / "app",
    pathlib.Path("/app/app"),
)


def _app_dir() -> pathlib.Path:
    for candidate in _APP_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise AssertionError(f"не нашёл app: {[str(p) for p in _APP_CANDIDATES]}")


def _scanned_files() -> list[pathlib.Path]:
    """Задачи воркера и модули синков: payload рождается там и там.

    В задачах это `return {...}`, в синках — `as_dict()` у класса результата
    (так приходит «checked» от наблюдателя за протоколами 5 вёрст).
    """
    root = _app_dir()
    tasks = sorted((root / "workers" / "tasks").rglob("*.py"))
    syncs = sorted((root / "sync").rglob("*.py"))
    return tasks + syncs


def _looks_numeric(value: ast.expr) -> bool:
    """Грубый отсев: строку и структуру в сводку всё равно не пускает агрегатор.

    Он суммирует только int/float, поэтому подпись нужна лишь тому, что похоже
    на счётчик: `result.something`, `len(...)`, арифметика, тернарник.
    """
    if isinstance(value, ast.Call):
        func = value.func
        return isinstance(func, ast.Name) and func.id == "len"
    if isinstance(value, ast.Constant):
        return isinstance(value.value, (int, float)) and not isinstance(value.value, bool)
    return isinstance(value, (ast.Attribute, ast.Name, ast.BinOp, ast.IfExp))


def _payload_keys() -> dict[str, str]:
    """Ключ счётчика → где он объявлен."""
    found: dict[str, str] = {}
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_sync = path.parent.name == "sync"
        for holder in ast.walk(tree):
            # В синках берём только as_dict: остальные словари там — не payload.
            if in_sync and not (
                isinstance(holder, ast.FunctionDef) and holder.name == "as_dict"
            ):
                continue
            for node in ast.walk(holder):
                if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                    continue
                keys, values = node.value.keys, node.value.values
                if any(
                    key is None
                    or not isinstance(key, ast.Constant)
                    or not isinstance(key.value, str)
                    for key in keys
                ):
                    continue
                for key, value in zip(keys, values, strict=True):
                    name = key.value
                    if name in NON_METRIC_KEYS or name in DETAIL_LIST_KEYS:
                        continue
                    if _looks_numeric(value):
                        found.setdefault(name, f"{path.name}:{node.lineno}")
    return found


def test_payload_counters_have_russian_labels() -> None:
    offenders = [
        f"{name} ({where})"
        for name, where in sorted(_payload_keys().items())
        if name not in FIELD_LABELS and name not in KNOWN_UNLABELED
    ]
    assert not offenders, (
        "у счётчика нет подписи — в сводке он напечатается сырым ключом. "
        "Добавьте строку в FIELD_LABELS (app/services/sync_report_labels.py):\n  "
        + "\n  ".join(offenders)
    )


def test_known_unlabeled_list_only_shrinks() -> None:
    """Получил подпись — уходи из списка долга, иначе он превращается в свалку."""
    settled = sorted(KNOWN_UNLABELED & set(FIELD_LABELS))
    assert not settled, (
        "эти ключи уже подписаны — уберите их из KNOWN_UNLABELED:\n  " + "\n  ".join(settled)
    )

    live = set(_payload_keys())
    gone = sorted(KNOWN_UNLABELED - live)
    assert not gone, (
        "этих ключей больше нет в коде — уберите их из KNOWN_UNLABELED:\n  " + "\n  ".join(gone)
    )
