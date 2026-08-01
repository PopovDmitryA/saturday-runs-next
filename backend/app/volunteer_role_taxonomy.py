"""Кросс-системная таксономия волонтёрских ролей.

Одну и ту же работу каждая система называет по-своему: «Сканирование штрих-кодов»
(5 вёрст) = «Сканирование» (RunPark) = «Сканер» (С95) = «Barcode Scanning»
(parkrun). Для рейтинга «Мультиволонтёр» (число РАЗНЫХ освоенных ролей) это
принципиально: без приведения к общему знаменателю человек, отсканировавший
коды в двух системах, получал бы две «разные» роли вместо одной.

Модуль умышленно живёт отдельно от парсеров: `app/s95/parsers/volunteer_roles.py`
приводит сербские названия к русским ярлыкам ВНУТРИ С95 (это часть синка), а
здесь — уже межсистемное схлопывание готовых ярлыков всех четырёх систем.

Что ещё делает нормализация:
- срезает parkrun-суффикс кредитов: «Marshal (12×)» → «Marshal»;
- срезает вехи С95: «Сканер 25» → «Сканер» (25 — юбилейная отметка, не роль);
- отбрасывает служебную строку parkrun «Total Credits (N×)» — это итог профиля,
  а не роль (см. app/parkrun/volunteer_credits.py);
- сводит корзины «прочего» всех систем в одну роль «Разное»: в зачёт она идёт
  (решение Дмитрия 01.08.2026), но именно одной ролью, а не тремя разными.

Неизвестный ярлык (роль появилась в системе позже этого файла) не теряется: он
получает собственный ключ `raw:<нормализованный ярлык>` и идёт в зачёт под своим
исходным названием. Так рейтинг не занижается на свежих данных прода.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Веха С95 («Сканер 25» — 25-е волонтёрство в этой роли) и кредиты parkrun
# («Marshal (12×)») — счётчики, приклеенные к названию роли, а не часть её имени.
_MILESTONE_SUFFIX_RE = re.compile(r"\s+\d+\s*$")
_CREDITS_SUFFIX_RE = re.compile(r"\s*\(\s*\d+\s*[×xX]\s*\)\s*$")

_LATIN_FOLDS = str.maketrans(
    {
        "š": "s",
        "č": "c",
        "ć": "c",
        "ž": "z",
        "đ": "dj",
    }
)

# Канонические роли: ключ → русский ярлык для витрины. Порядок — по ходу
# субботнего утра (подготовка → трасса → финиш → после), он же порядок вывода
# при равном числе волонтёрств.
CANONICAL_ROLE_LABELS: dict[str, str] = {
    "run_director": "Директор забега",
    "volunteer_coordinator": "Координация волонтёров",
    "pre_event_setup": "Подготовка мероприятия",
    "course_setup": "Разметка трассы",
    "course_check": "Проверка трассы",
    "first_timers": "Инструктаж новичков",
    "pre_run_briefing": "Предстартовый брифинг",
    "warm_up": "Проведение разминки",
    "host": "Ведущий мероприятия",
    "marshal": "Маршал",
    "pacer": "Пейсмейкер",
    "lead_bike": "Ведущий велосипед",
    "vi_guide": "Сопровождающий",
    "walk_leader": "Ведущий группы",
    "sign_language": "Сурдопереводчик",
    "parking": "Координатор парковки",
    "timekeeper": "Секундомер",
    "tail_walker": "Замыкающий",
    "funnel_manager": "Организация финиша",
    "finish_tokens": "Раздача карточек позиций",
    "finish_token_support": "Помощь в раздаче карточек",
    "barcode_scanning": "Сканирование штрих-кодов",
    "number_checker": "Проверка карточек позиций",
    "token_sorting": "Сортировка карточек",
    "bib_numbers": "Выдача номеров",
    "results_processor": "Обработка результатов",
    "course_takedown": "Сбор разметки",
    "post_event": "Завершение мероприятия",
    "equipment": "Хранение и доставка оборудования",
    "refreshments": "Организация питания",
    "photographer": "Фотограф",
    "videographer": "Видеограф",
    "designer": "Дизайнер",
    "communications": "Связи с общественностью",
    "report_writer": "Составление отчёта",
    "volunteer": "Волонтёр",
    "other": "Разное",
}

# Ярлык системы → канонический ключ. Ключи словаря пишем как есть (человекочитаемо),
# нормализация применяется к обеим сторонам при сборке справочника.
_ROLE_ALIASES: dict[str, str] = {
    # --- руководство ---
    "Организатор": "run_director",  # 5 вёрст
    "Руководитель": "run_director",  # RunPark
    "Директор": "run_director",  # С95
    "Директор забега": "run_director",
    "Direktor": "run_director",
    "Run Director": "run_director",
    "Event Director": "run_director",
    "Координация волонтёров": "volunteer_coordinator",
    "Координатор волонтёров": "volunteer_coordinator",
    "Volunteer Co-ordinator": "volunteer_coordinator",
    "Volunteer Coordinator": "volunteer_coordinator",
    # --- до старта ---
    "Подготовка мероприятия": "pre_event_setup",  # 5 вёрст
    "Предстартовая подготовка": "pre_event_setup",  # RunPark
    "Подготовка забега": "pre_event_setup",  # С95
    "Подготовка до старта": "pre_event_setup",
    "Pre-event Setup": "pre_event_setup",
    "Разметка трассы": "course_setup",
    "Подготовка трассы": "course_setup",  # RunPark
    "Course Set Up": "course_setup",
    "Проверка трассы": "course_check",
    "Event Day Course Check": "course_check",
    "Инструктаж новых участников": "first_timers",  # 5 вёрст
    "Инструктаж новичков": "first_timers",  # С95
    "Брифинг для новичков": "first_timers",  # RunPark
    "First Timers Welcome": "first_timers",
    "Проведение предстартового брифинга": "pre_run_briefing",
    "Предстартовый брифинг": "pre_run_briefing",
    "Проведение разминки": "warm_up",
    "Разминка": "warm_up",  # RunPark
    "Warm Up Leader": "warm_up",
    "Ведущий мероприятия": "host",
    # --- трасса ---
    "Маршал": "marshal",
    "Marshal": "marshal",
    "Пейсмейкер": "pacer",
    "Пейсер": "pacer",  # RunPark
    "Pacer (5k only)": "pacer",
    "Pacer": "pacer",
    "Ведущий велосипед": "lead_bike",
    "Lead Bike": "lead_bike",
    "Лидер для слабовидящих": "vi_guide",  # 5 вёрст
    "Сопровождающий": "vi_guide",  # RunPark, С95
    "Pratilac trkača": "vi_guide",
    "VI Guide": "vi_guide",
    "Guide Runner": "vi_guide",
    "Ведущий группы": "walk_leader",
    "Сурдопереводчик": "sign_language",
    "Sign Language Support": "sign_language",
    "Координатор парковки": "parking",
    "Car Park Marshal": "parking",
    # --- финиш ---
    "Секундомер": "timekeeper",
    "Хронометраж": "timekeeper",
    "Tajmer": "timekeeper",
    "Timekeeper": "timekeeper",
    # Backup Timer у parkrun — тот же хронометраж вторым человеком; отдельной
    # роли ни в одной русской системе нет, поэтому схлопываем, иначе parkrun
    # раздавал бы «лишнюю» роль просто за свою терминологию.
    "Backup Timer": "timekeeper",
    "Замыкающий": "tail_walker",
    "Tail Walker": "tail_walker",
    "Организация финиша": "funnel_manager",
    "Организация финишной воронки": "funnel_manager",  # RunPark
    "Funnel Manager": "funnel_manager",
    "Раздача карточек позиций": "finish_tokens",
    "Выдача карточек позиций": "finish_tokens",  # RunPark
    "Dodela kartica pozicija": "finish_tokens",
    "Finish Tokens": "finish_tokens",
    "Помощь в раздаче карточек позиций": "finish_token_support",
    "Помощь в раздаче карточек": "finish_token_support",
    "Finish Token Support": "finish_token_support",
    "Сканирование штрих-кодов": "barcode_scanning",
    "Сканирование": "barcode_scanning",  # RunPark
    "Сканер": "barcode_scanning",  # С95
    "Barcode Scanning": "barcode_scanning",
    "Проверка карточек позиций": "number_checker",
    "Number Checker": "number_checker",
    "Сортировка карточек": "token_sorting",
    # RunPark совмещает проверку и сортировку в одной роли — учитываем как
    # сортировку (у 5 вёрст проверка вынесена отдельно и остаётся отдельной).
    "Проверка и сортировка карточек позиций": "token_sorting",
    "Token Sorting": "token_sorting",
    "Выдача номеров": "bib_numbers",
    "Обработка результатов": "results_processor",
    "Results Processor": "results_processor",
    # --- после ---
    "Сбор разметки": "course_takedown",
    "Завершение мероприятия": "post_event",
    "Post-event Close Down": "post_event",
    "Хранение и доставка оборудования": "equipment",
    "Доставка и хранение оборудования": "equipment",  # RunPark
    "Доставка оборудования": "equipment",  # С95
    "Оборудование": "equipment",
    "Equipment Storage and Delivery": "equipment",
    "Организация питания": "refreshments",  # С95
    "Организатор чаепития": "refreshments",  # RunPark
    "Refreshments": "refreshments",
    # --- медиа и связь ---
    "Фотограф": "photographer",
    "Photographer": "photographer",
    "Видеограф": "videographer",
    "Videographer": "videographer",
    "Дизайнер": "designer",
    "Связи с общественностью": "communications",
    "Communications Person": "communications",
    "Составление отчёта": "report_writer",
    "Репортер мероприятия": "report_writer",  # RunPark
    "Report Writer": "report_writer",
    # parkrun-роль Parkwalker — тот же ведущий пешеходной группы; без алиаса
    # она уезжала в raw:parkwalker и считалась «ещё одной» ролью.
    "Parkwalker": "walk_leader",
    "Волонтёр": "volunteer",
    "Volunteer": "volunteer",
    # Корзина «прочего» есть в каждой системе под своим именем — это одна и та
    # же «какая-то ещё работа», поэтому одна каноническая роль на все системы.
    "Разное": "other",
    "Другое": "other",
    "Прочее": "other",
    "Other": "other",
    "Ostalo": "other",
}

# Служебная строка сводки parkrun-профиля («Total Credits (37×)») — итог, а не роль.
_TOTAL_CREDITS_MARKER = "total credit"

# --- Свойства ролей: чем волонтёрство «весит» в глазах сообщества ---
#
# В сообществе спорят, что считать волонтёрством: одни приходят на площадку и
# работают вместо бега, другие берут роль, которую можно выполнить из дома, а
# третьи и бегут, и волонтёрят в одну субботу. Рассудить это можно только
# фильтром, поэтому у каждой роли два признака (разметка согласована с Дмитрием
# 02.08.2026 по реальным данным прода — 598 тыс. строк, 39 ролей):
#
# - on_site: нужно физически быть на старте;
# - runnable: в этот же день можно пробежать 5 км (роль до/после забега, либо
#   выполняется прямо на дистанции — пейсмейкер, замыкающий).
#
# Строгий зачёт («пришёл и работал вместо бега») — это on_site и не runnable.
ROLE_ON_SITE: frozenset[str] = frozenset(
    {
        # до старта — можно потом бежать
        "volunteer_coordinator",
        "pre_event_setup",
        "course_setup",
        "course_check",
        "first_timers",
        "pre_run_briefing",
        "warm_up",
        # во время забега, но бег не мешает
        "funnel_manager",
        "photographer",
        "number_checker",
        "token_sorting",
        "parking",
        "sign_language",
        "bib_numbers",
        "lead_bike",
        # роль выполняется прямо на дистанции
        "pacer",
        "tail_walker",
        "vi_guide",
        "walk_leader",
        # после финиша
        "post_event",
        "course_takedown",
        "refreshments",
        "equipment",
        # общая роль без уточнения: на площадке, но в строгий зачёт не берём
        "volunteer",
        # строгое ядро — на площадке, бежать нельзя
        "timekeeper",
        "barcode_scanning",
        "marshal",
        "finish_tokens",
        "finish_token_support",
        "run_director",
        "videographer",
        "host",
    }
)


# Роли, совместимые с забегом. Всё, что on_site и НЕ здесь, — строгое ядро.
ROLE_RUNNABLE: frozenset[str] = frozenset(
    {
        "volunteer_coordinator",
        "pre_event_setup",
        "course_setup",
        "course_check",
        "first_timers",
        "pre_run_briefing",
        "warm_up",
        "funnel_manager",
        "photographer",
        "number_checker",
        "token_sorting",
        "parking",
        "sign_language",
        "bib_numbers",
        "lead_bike",
        "pacer",
        "tail_walker",
        "vi_guide",
        "walk_leader",
        "post_event",
        "course_takedown",
        "refreshments",
        "equipment",
        "volunteer",
        "videographer",
        # удалённые роли бегу тем более не мешают
        "communications",
        "results_processor",
        "report_writer",
        "designer",
        "other",
    }
)

# Наборы для кнопок пресетов. «custom» собирается пользователем галочками.
ROLE_PRESETS: tuple[str, ...] = ("all", "on_site", "on_site_no_run", "remote", "custom")


def role_is_on_site(key: str) -> bool:
    """Нужно ли быть на площадке. Неизвестная роль (raw:*) — считаем удалённой.

    Решение Дмитрия: незнакомые и служебные роли (Ambassador, Board & Committee,
    Unknown, «Разное») в зачёт «на площадке» не идут — иначе строгий рейтинг
    завышался бы на ролях, о которых мы ничего не знаем.
    """
    return key in ROLE_ON_SITE


def role_is_runnable(key: str) -> bool:
    """Можно ли в этот же день пробежать. Неизвестная роль — да (бегу не мешает)."""
    return key not in ROLE_ON_SITE or key in ROLE_RUNNABLE


def preset_role_keys(preset: str) -> frozenset[str] | None:
    """Ключи ролей пресета. None — «все роли», фильтровать не нужно."""
    if preset == "on_site":
        return ROLE_ON_SITE
    if preset == "on_site_no_run":
        return frozenset(key for key in ROLE_ON_SITE if key not in ROLE_RUNNABLE)
    if preset == "remote":
        return frozenset(key for key in CANONICAL_ROLE_LABELS if key not in ROLE_ON_SITE)
    return None


@dataclass(frozen=True)
class CanonicalRole:
    """Канонический ключ роли и её ярлык для витрины."""

    key: str
    label: str


def _lookup_key(label: str) -> str:
    """Ключ поиска: латиница свёрнута к ASCII, регистр и пунктуация убраны."""
    normalized = unicodedata.normalize("NFKD", label.strip().lower().translate(_LATIN_FOLDS))
    # Кириллицу NFKD не разбирает в ASCII, а вот латинские диакритики — да.
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^\w]+", "_", stripped, flags=re.UNICODE).strip("_")


_ALIAS_LOOKUP: dict[str, str] | None = None


def _alias_lookup() -> dict[str, str]:
    global _ALIAS_LOOKUP
    if _ALIAS_LOOKUP is None:
        lookup = {_lookup_key(alias): key for alias, key in _ROLE_ALIASES.items()}
        # Сам канонический ярлык тоже должен находиться (роль могла прийти уже
        # в каноническом написании).
        for key, label in CANONICAL_ROLE_LABELS.items():
            lookup.setdefault(_lookup_key(label), key)
        _ALIAS_LOOKUP = lookup
    return _ALIAS_LOOKUP


def strip_role_counters(role: str) -> str:
    """Убрать приклеенные к названию счётчики: кредиты parkrun и вехи С95."""
    cleaned = _CREDITS_SUFFIX_RE.sub("", role.strip())
    return _MILESTONE_SUFFIX_RE.sub("", cleaned).strip()


def canonical_volunteer_role(role: str | None) -> CanonicalRole | None:
    """Канонизировать ярлык роли. None — строка ролью не является.

    Не идут только пустые значения и служебная строка сводки parkrun.
    Незнакомая роль возвращается как есть, с собственным ключом `raw:*`.
    """
    if not role:
        return None
    cleaned = strip_role_counters(role)
    if not cleaned:
        return None
    if _TOTAL_CREDITS_MARKER in cleaned.casefold():
        return None
    key = _lookup_key(cleaned)
    if not key:
        return None
    canonical = _alias_lookup().get(key)
    if canonical is not None:
        return CanonicalRole(key=canonical, label=CANONICAL_ROLE_LABELS[canonical])
    return CanonicalRole(key=f"raw:{key}", label=cleaned)


def role_occasions(role: str | None) -> int | None:
    """Число кредитов из parkrun-ярлыка «Marshal (12×)» — 12. None, если его нет."""
    if not role:
        return None
    match = re.search(r"\(\s*(\d+)\s*[×xX]\s*\)\s*$", role.strip())
    return int(match.group(1)) if match else None
