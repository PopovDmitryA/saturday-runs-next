"""Роутер должен слышать любую смену записи истории, а не только ссылки.

`useAppPath` держит текущий путь и синхронизируется по popstate и по кликам на
`<a href>`. Но часть страниц меняет адрес напрямую через `history.pushState` —
вкладки чужого профиля и страницы «Обновлений». Такой вызов popstate не
порождает, и роутер о нём не узнаёт.

Само по себе это было бы полбеды, но `lib/historyEntry` оборачивает pushState и
меняет ключ записи истории, а App рисует маршрут как `<Fragment key={entryKey}>`.
Ключ сменился — поддерево перемонтировалось, и маршрут построился по СТАРОМУ
пути: вкладка «Пробежки» на чужом профиле открывалась и тут же схлопывалась в
«Главную» (репорт Дмитрия 14.09.2026, /users/petrov/runs).

Лечится подпиской `useAppPath` на `onEntryChange`. Проверка нужна потому, что
связь неочевидная: она между двумя механизмами истории, и её легко потерять при
следующей правке хука.
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


def test_use_app_path_subscribes_to_history_entries() -> None:
    source = (_frontend_dir() / "hooks" / "useAppPath.ts").read_text(encoding="utf-8")
    assert "onEntryChange" in source, (
        "useAppPath не подписан на смену записи истории: прямой history.pushState "
        "сменит ключ записи и перемонтирует маршрут по старому пути"
    )


def test_direct_push_state_callers_are_known() -> None:
    """Прямой pushState — приём с подвохом, пусть новые случаи попадаются на глаза.

    Не запрещаем: адрес под вкладку или страницу таблицы иначе не поправить.
    Но каждый такой вызов держится на подписке выше, поэтому список явный.
    """
    known = {
        "features/public_profile/PublicProfilePage.tsx",  # вкладки чужого профиля
        "features/portal/PortalUpdatesPage.tsx",  # страницы «Обновлений»
        "lib/historyEntry.ts",  # сама обёртка
        "hooks/useAppPath.ts",  # перехват кликов по ссылкам
    }
    root = _frontend_dir()
    found = {
        str(path.relative_to(root))
        for path in root.rglob("*.ts*")
        if re.search(r"history\.pushState\(", path.read_text(encoding="utf-8"))
    }
    new = sorted(found - known)
    assert not new, (
        "новый прямой history.pushState — проверьте, что роутер узнаёт о смене "
        "адреса (см. useAppPath и onEntryChange), и добавьте файл в список:\n  "
        + "\n  ".join(new)
    )
