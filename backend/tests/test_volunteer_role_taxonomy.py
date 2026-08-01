"""Кросс-системная канонизация волонтёрских ролей (рейтинг «Мультиволонтёр»)."""
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    canonical_volunteer_role,
    role_occasions,
    strip_role_counters,
)


def _key(role: str) -> str | None:
    canonical = canonical_volunteer_role(role)
    return canonical.key if canonical is not None else None


def test_same_work_in_four_systems_is_one_role() -> None:
    # Ради этого всё и затевалось: сканирование кодов во всех системах —
    # одна роль, а не четыре.
    keys = {
        _key("Сканирование штрих-кодов"),  # 5 вёрст
        _key("Сканирование"),  # RunPark
        _key("Сканер"),  # С95
        _key("Barcode Scanning (7×)"),  # parkrun
    }
    assert keys == {"barcode_scanning"}


def test_director_labels_collapse() -> None:
    assert (
        _key("Организатор")  # 5 вёрст
        == _key("Руководитель")  # RunPark
        == _key("Директор")  # С95
        == _key("Direktor")  # С95, сербская локаль
        == _key("Run Director (11×)")  # parkrun
        == "run_director"
    )


def test_s95_milestone_suffix_is_not_a_new_role() -> None:
    # «Сканер 25» — веха (25-е волонтёрство в роли), а не отдельная роль.
    assert _key("Сканер 25") == _key("Сканер") == "barcode_scanning"


def test_parkrun_credits_suffix_stripped() -> None:
    assert strip_role_counters("Marshal (12×)") == "Marshal"
    assert role_occasions("Marshal (12×)") == 12
    assert role_occasions("Маршал") is None


def test_total_credits_row_is_not_a_role() -> None:
    # Служебная строка сводки профиля parkrun — итог, а не роль.
    assert canonical_volunteer_role("Total Credits (115×)") is None


def test_misc_bucket_is_one_role_across_systems() -> None:
    # Корзина «прочего» в зачёт идёт (решение Дмитрия 01.08.2026), но одной
    # ролью на все системы — иначе «Разное» + «Другое» + «Other» дали бы три.
    keys = {
        _key("Разное"),  # 5 вёрст, С95
        _key("Разное 50"),  # С95 с вехой
        _key("Другое"),  # RunPark
        _key("Other (29×)"),  # parkrun
    }
    assert keys == {"other"}
    role = canonical_volunteer_role("Другое")
    assert role is not None and role.label == "Разное"


def test_empty_role_is_skipped() -> None:
    assert canonical_volunteer_role(None) is None
    assert canonical_volunteer_role("") is None
    assert canonical_volunteer_role("   ") is None


def test_unknown_role_survives_with_own_key() -> None:
    # Новая роль в системе не должна теряться из зачёта: у неё свой ключ и
    # исходный ярлык.
    unknown = canonical_volunteer_role("Дрессировщик собак-пейсеров")
    assert unknown is not None
    assert unknown.key.startswith("raw:")
    assert unknown.label == "Дрессировщик собак-пейсеров"
    # Тот же ярлык из другой системы схлопывается сам с собой, даже если
    # написан в другом регистре.
    assert _key("дрессировщик собак-пейсеров") == unknown.key


def test_every_alias_target_has_a_label() -> None:
    from app.volunteer_role_taxonomy import _ROLE_ALIASES

    assert set(_ROLE_ALIASES.values()) <= set(CANONICAL_ROLE_LABELS)


def test_canonical_labels_are_unique() -> None:
    # Два канонических ключа с одинаковым ярлыком в таблице выглядели бы как
    # одна роль, но считались бы как две.
    labels = list(CANONICAL_ROLE_LABELS.values())
    assert len(labels) == len(set(labels))
