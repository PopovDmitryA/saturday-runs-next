"""Кросс-системная канонизация волонтёрских ролей (рейтинг «Мультиволонтёр»)."""
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    canonical_volunteer_role,
    platform_role_label,
    role_display_order,
    role_is_core,
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


# --- Название роли в протоколе: по системе этого протокола -------------------


def _label(platform_code: str, raw: str) -> str:
    canonical = canonical_volunteer_role(raw)
    assert canonical is not None
    return platform_role_label(platform_code, raw, canonical)


def test_protocol_shows_the_systems_own_role_name() -> None:
    """Одна работа — четыре названия; в протоколе видно название его системы.

    До 23.08.2026 витрина показывала канонический ярлык, и в протоколе 5 вёрст
    стоял «Директор забега» — название С95 (репорт Дмитрия).
    """
    assert _label("five_verst", "Организатор") == "Организатор"
    assert _label("s95", "Директор") == "Директор"
    assert _label("runpark", "Руководитель") == "Руководитель"


def test_scanner_keeps_its_local_name() -> None:
    assert _label("five_verst", "Сканирование штрих-кодов") == "Сканирование штрих-кодов"
    assert _label("s95", "Сканер") == "Сканер"
    assert _label("runpark", "Сканирование") == "Сканирование"


def test_s95_milestone_counter_is_not_part_of_the_name() -> None:
    assert _label("s95", "Сканер 25") == "Сканер"


def test_parkrun_keeps_russian_canonical_label() -> None:
    """У parkrun свои ярлыки английские, а читают протокол по-русски."""
    assert _label("parkrun", "Run Director (3×)") == "Директор забега"
    assert _label("parkrun", "Barcode Scanning (1×)") == "Сканирование штрих-кодов"


def test_core_roles_sort_before_the_rest() -> None:
    """Ключевые (на площадке и без бега) — сверху, остальные под ними."""
    assert role_is_core("run_director")
    assert role_is_core("barcode_scanning")
    # Пейсмейкер стоит на площадке, но бежит — не ключевая.
    assert not role_is_core("pacer")
    # Фотограф бегу не мешает, связи с общественностью — вообще удалённая.
    assert not role_is_core("photographer")
    assert not role_is_core("communications")

    order = sorted(
        ["pacer", "run_director", "communications", "timekeeper"],
        key=role_display_order,
    )
    assert order[:2] == ["run_director", "timekeeper"]
    assert set(order[2:]) == {"pacer", "communications"}


def test_core_group_keeps_the_saturday_morning_order() -> None:
    """Внутри группы порядок — как в CANONICAL_ROLE_LABELS (ход утра)."""
    assert role_display_order("run_director") < role_display_order("timekeeper")
    assert role_display_order("timekeeper") < role_display_order("barcode_scanning")
