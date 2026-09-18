"""Безымянные строки протокола — не личности, а заглушки.

В протоколах всех систем есть финишёры без штрихкода: «НЕИЗВЕСТНЫЙ» у
5 вёрст и S95, «Неизвестный бегун» у RunPark. Строку всё равно надо куда-то
привязать, поэтому под неё заводится одноразовая личность:

* S95 и 5 вёрст — ``unknown:<локация>:<дата>:<место>``;
* RunPark — ``anon:<id строки протокола>``.

Ключ уникален для КАЖДОЙ строки: на проде у всех 18 тысяч таких личностей
ровно по одному забегу, второй раз тот же человек никогда не «узнаётся». Для
любой метрики «впервые» это яд: каждая безымянная строка автоматически
оказывается и первым стартом в системе, и первым на площадке. До 18.09.2026
так и было — 44% «новичков» RunPark и 28% S95 были такими заглушками.

Поэтому правило простое: безымянная строка ни в чём «впервые» не участвует.
У 5 вёрст оно соблюдалось само собой (флаги приезжают с бейджей протокола, и
безымянному бейдж не выдают) — здесь то же самое для выводимых систем.
"""

from __future__ import annotations

ANONYMOUS_EXTERNAL_ID_PREFIXES: tuple[str, ...] = ("unknown:", "anon:")

# Имена-заглушки: у RunPark 138 личностей приехали с настоящим GUID, но под
# именем «Неизвестный бегун» — по ключу их не отличить. Сравниваем имя целиком,
# иначе живой «Андрей НЕИЗВЕСТНЫХ» попал бы в заглушки.
UNKNOWN_DISPLAY_NAMES = frozenset(
    {
        "неизвестный",
        "неизвестная",
        "неизвестный бегун",
        "неизвестный участник",
        "неизвестно",
        "неизвестен",
        "nepoznato",
        "unknown",
    }
)


def is_anonymous_participant(external_user_id: str | None, display_name: str | None = None) -> bool:
    """Личность-заглушка под безымянную строку протокола: по ключу или по имени."""
    if not external_user_id:
        return True
    if external_user_id.startswith(ANONYMOUS_EXTERNAL_ID_PREFIXES):
        return True
    return (display_name or "").strip().casefold() in UNKNOWN_DISPLAY_NAMES


def anonymous_participant_sql(external_id_column: str, display_name_column: str) -> str:
    """То же правило для сырого SQL."""
    prefixes = " OR ".join(
        f"{external_id_column} LIKE '{prefix}%'" for prefix in ANONYMOUS_EXTERNAL_ID_PREFIXES
    )
    names = ", ".join(f"'{name}'" for name in sorted(UNKNOWN_DISPLAY_NAMES))
    return (
        f"({external_id_column} IS NULL OR {prefixes}"
        f" OR lower(btrim({display_name_column})) IN ({names}))"
    )
