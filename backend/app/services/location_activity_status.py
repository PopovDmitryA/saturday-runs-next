"""Статус площадки: действует / скоро / не действует, плюс отмена ближайшего старта.

Единая точка правды для карт, каталога и страницы локации (решение Дмитрия
20.08.2026). До этого статус собирался по месту: карта считала любой parkrun
«отменённым», каталог звал паузой и закрытые навсегда площадки, и новые тоже.

Правила, по убыванию приоритета:

1. Что сказала сама система, важнее наших догадок. Реестр 5 вёрст помечает
   площадку «(отмена)», «(на паузе)» или «(скоро)», s95 отдаёт закрытые
   карточки — это заявление владельца площадки, ему верим сразу и не ждём,
   пока накопится статистика молчания.
2. Молчание дольше INACTIVE_AFTER_DAYS — наша догадка «площадка больше не
   работает». Сезонные площадки в него попадают (Бузулук зимовал 126 дней) и
   выходят обратно с первым же стартом: статус пересчитывается от данных, а не
   выставляется навсегда.
3. Ни одного старта за всю историю — площадка заведена, но не открылась.

Отмена ближайшего старта живёт отдельным флагом: она ортогональна всему
остальному. Действующая площадка может отменить одну субботу и остаться
действующей — это разные вопросы, «работает ли она вообще» и «побегут ли в эту
субботу».
"""

from __future__ import annotations

import enum
from datetime import date, timedelta

# Порог молчания, после которого площадка считается недействующей (решение
# Дмитрия 20.08.2026). Сотня дней — это примерно три с половиной месяца, то
# есть длиннее любого разумного ремонта, но короче зимы: сезонная площадка
# успевает попасть в статус и выйти из него, и это осознанный компромисс.
INACTIVE_AFTER_DAYS = 100


class LocationActivity(str, enum.Enum):
    """Что показываем про площадку в целом."""

    active = "active"
    upcoming = "upcoming"
    inactive = "inactive"


def location_activity(
    *,
    is_paused: bool,
    is_upcoming: bool,
    last_event_date: date | None,
    as_of: date | None = None,
    is_historic: bool = False,
) -> LocationActivity:
    """Статус одной строки локации.

    is_paused и is_upcoming приходят из реестра системы (см. синки), последний
    старт — из протоколов. Порядок проверок и есть приоритет источника над
    догадкой по датам.

    is_historic — строка системы, которая у нас больше не ведётся (parkrun).
    Там пустая история означает «протоколов не собрали», а не «площадка вот-вот
    откроется»: 12 таких parkrun-строк без событий иначе обещали бы открытие.
    """
    if is_paused:
        return LocationActivity.inactive
    if is_historic:
        # Историческая система не открывается заново — только «не действует».
        return LocationActivity.inactive
    if is_upcoming:
        return LocationActivity.upcoming
    if last_event_date is None:
        # Стартов не было ни разу: площадка заведена, но не открылась. Звать её
        # действующей нельзя — человек приедет к пустому парку.
        return LocationActivity.upcoming
    cutoff = (as_of or date.today()) - timedelta(days=INACTIVE_AFTER_DAYS)
    if last_event_date < cutoff:
        return LocationActivity.inactive
    return LocationActivity.active


def merge_activity(statuses: list[LocationActivity]) -> LocationActivity:
    """Статус физической площадки, собранной из строк нескольких систем.

    Площадка действует, если действует хоть в одной системе: парк, ушедший из
    parkrun в 5 вёрст, живой. «Скоро» сильнее «не действует» по той же причине —
    там, где одна система закрылась, а другая готовится открыться, людям важнее
    ожидание, чем прошлое.
    """
    if not statuses:
        return LocationActivity.inactive
    if LocationActivity.active in statuses:
        return LocationActivity.active
    if LocationActivity.upcoming in statuses:
        return LocationActivity.upcoming
    return LocationActivity.inactive
