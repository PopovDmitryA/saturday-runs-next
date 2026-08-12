"""Авто-выбор домашней локации: три ступени отбора (решение Дмитрия 03.08.2026).

Пробежки → волонтёрства → самая ранняя пробежка. Тесты юнитовые: логика отбора
не зависит от БД, а ставки высокие — от домашней локации считается вся
«дальность от дома».
"""

from __future__ import annotations

from app.services.home_location_service import HomeLocationCandidate, _auto_home_location


def _candidate(
    name: str,
    *,
    runs: int,
    volunteering: int = 0,
    first_run: str | None = None,
) -> HomeLocationCandidate:
    return HomeLocationCandidate(
        catalog_identity_key=f"key-{name}",
        name=name,
        city=None,
        region=None,
        run_count=runs,
        volunteer_count=volunteering,
        platform_codes=["five_verst"],
        first_run_date=first_run,
    )


def test_no_candidates_gives_nothing() -> None:
    assert _auto_home_location([]) is None


def test_runs_decide_first() -> None:
    """Волонтёрства не перебивают пробежки, даже если их сильно больше."""
    picked = _auto_home_location(
        [
            _candidate("Мало пробежек, гора волонтёрств", runs=3, volunteering=100),
            _candidate("Много пробежек", runs=10, volunteering=0),
        ]
    )
    assert picked is not None
    assert picked.name == "Много пробежек"


def test_volunteering_breaks_a_tie_on_runs() -> None:
    picked = _auto_home_location(
        [
            _candidate("Только бегал", runs=10, volunteering=2),
            _candidate("Бегал и помогал", runs=10, volunteering=7),
            _candidate("Далёкий третий", runs=4, volunteering=99),
        ]
    )
    assert picked is not None
    assert picked.name == "Бегал и помогал"


def test_volunteering_compared_only_among_run_leaders() -> None:
    """Лидер по волонтёрствам вне топа по пробежкам в отбор не попадает."""
    picked = _auto_home_location(
        [
            _candidate("Лидер по пробежкам", runs=10, volunteering=0),
            _candidate("Волонтёрский чемпион", runs=9, volunteering=50),
        ]
    )
    assert picked is not None
    assert picked.name == "Лидер по пробежкам"


def test_earliest_run_breaks_a_full_tie() -> None:
    picked = _auto_home_location(
        [
            _candidate("Начал позже", runs=10, volunteering=3, first_run="2023-05-06"),
            _candidate("Начал раньше", runs=10, volunteering=3, first_run="2019-08-17"),
        ]
    )
    assert picked is not None
    assert picked.name == "Начал раньше"


def test_unknown_first_run_loses_to_a_known_date() -> None:
    """Пустая дата не должна выигрывать пустотой."""
    picked = _auto_home_location(
        [
            _candidate("Без даты", runs=10, volunteering=3, first_run=None),
            _candidate("С датой", runs=10, volunteering=3, first_run="2024-01-06"),
        ]
    )
    assert picked is not None
    assert picked.name == "С датой"


def test_full_tie_is_resolved_stably() -> None:
    """Совсем одинаковые — берём первого по списку (он отсортирован по имени),
    чтобы выбор не прыгал от запроса к запросу."""
    candidates = [
        _candidate("Альфа", runs=5, volunteering=1, first_run="2024-01-06"),
        _candidate("Бета", runs=5, volunteering=1, first_run="2024-01-06"),
    ]
    assert _auto_home_location(candidates) is _auto_home_location(candidates)
    picked = _auto_home_location(candidates)
    assert picked is not None
    assert picked.name == "Альфа"


def test_all_three_steps_together() -> None:
    """Сквозной случай: пробежки отсекают половину, волонтёрства — ещё, дата решает."""
    picked = _auto_home_location(
        [
            _candidate("Мало бегал", runs=2, volunteering=9, first_run="2015-01-03"),
            _candidate("Топ, мало помогал", runs=8, volunteering=1, first_run="2016-01-02"),
            _candidate("Топ, помогал, поздний", runs=8, volunteering=4, first_run="2022-03-05"),
            _candidate("Топ, помогал, ранний", runs=8, volunteering=4, first_run="2018-07-14"),
        ]
    )
    assert picked is not None
    assert picked.name == "Топ, помогал, ранний"
