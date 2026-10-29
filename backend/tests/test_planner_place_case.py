"""«Бегу в Битцу»: винительный падеж названия локации в окне постоянного плана.

Хелпер живёт на фронте (frontend/src/features/planner/placeCase.ts), тестов у
фронта нет. Сторож читает оттуда таблицу окончаний и примеры, повторяет то же
правило и сверяет: правка таблицы, которая ломает пример («бегу в Битца»,
«бегу в Набережнаю»), не пройдёт.
"""

from __future__ import annotations

import pathlib
import re

_FRONTEND_CANDIDATES = (
    pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src",
    pathlib.Path("/frontend-src"),
)

_SOURCE = "features/planner/placeCase.ts"


def _source() -> str:
    for candidate in _FRONTEND_CANDIDATES:
        path = candidate / _SOURCE
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"не нашёл {_SOURCE}: {[str(p) for p in _FRONTEND_CANDIDATES]}")


def _pairs(source: str, name: str) -> list[tuple[str, str]]:
    match = re.search(rf"export const {name}[^=]*=\s*\[(.*?)\n\];", source, re.S)
    assert match, f"в {_SOURCE} нет таблицы {name}"
    return re.findall(r'\[\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\]', match.group(1))


def _strings(source: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}[^=]*=\s*\[([^\]]*)\];", source)
    assert match, f"в {_SOURCE} нет списка {name}"
    return tuple(re.findall(r'"([^"]*)"', match.group(1)))


_WORD_RE = re.compile(r"^[А-ЯЁа-яё][а-яё-]*$")
_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def _word(word: str, endings: list[tuple[str, str]]) -> str | None:
    if len(word) < 3 or not _WORD_RE.match(word):
        return None
    for old, new in endings:
        if word.endswith(old):
            return word[: -len(old)] + new
    return None


def _accusative(
    name: str, endings: list[tuple[str, str]], genitive: tuple[str, ...], possessive: tuple[str, ...]
) -> str:
    words = name.split()
    first = _word(words[0], endings)
    if first is None:
        return name
    if len(words) == 1:
        return first
    head, second = words[0], words[1]
    if head.endswith(("ая", "яя")) or (head.endswith(possessive) and second.endswith(("а", "я"))):
        words[0] = first
        words[1] = _word(second, endings) or second
        return " ".join(words)
    if second.endswith(genitive):
        words[0] = first
        return " ".join(words)
    return name


def _run_to(name: str, endings: list[tuple[str, str]], genitive: tuple[str, ...], possessive: tuple[str, ...]) -> str:
    place = _accusative(name, endings, genitive, possessive)
    preposition = "во" if len(place) > 1 and place[0] in "ВвФф" and place[1] not in _VOWELS else "в"
    return f"бегу {preposition} {place}"


def test_place_accusative_examples() -> None:
    source = _source()
    endings = _pairs(source, "ACCUSATIVE_ENDINGS")
    examples = _pairs(source, "RUN_TO_EXAMPLES")
    genitive = _strings(source, "GENITIVE_ENDINGS")
    possessive = _strings(source, "POSSESSIVE_ENDINGS")
    assert endings and examples and genitive and possessive
    # Длинные окончания первыми: иначе «-ая» поймало бы правило «-а».
    for index, (old, _new) in enumerate(endings):
        for later, _ in endings[index + 1 :]:
            assert not (later.endswith(old) and len(later) > len(old)), endings
    # Без примеров из замечания Дмитрия (26.10.2026) сторож не сторожит.
    names = {name for name, _ in examples}
    assert {"Битца", "Набережная", "Кусково", "Парк Горького"} <= names
    # И «Город + локация» из ревью 26.10.2026 (UI-6): склонённым первое слово быть не должно.
    assert {"Тула Центральный парк", "Мега Парк Кудрово", "Боева Дача"} <= names
    wrong = [
        f"{name}: ждали «{expected}», вышло «{_run_to(name, endings, genitive, possessive)}»"
        for name, expected in examples
        if _run_to(name, endings, genitive, possessive) != expected
    ]
    assert not wrong, "\n".join(wrong)
