"""Реестр видов уведомлений.

Вид — это «о чём» мы пишем человеку; канал — «куда» (см.
services/notification_channels_service.py). Виды живут в коде, а не в БД:
новый вид = новая запись здесь, без миграции. Пользовательские переключатели
хранятся в user_notification_prefs.kinds как {код: bool}; отсутствующий ключ
означает умолчание из этого реестра.

`available=False` — вид объявлен, но в настройках не показывается и событий
под него никто не порождает (запас на случай отключения вида без миграции).
Запись на волонтёрство добавится сюда вместе с самой фичей — заранее её не
показываем (решение Дмитрия 23.09.2026).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationKind:
    code: str
    title: str
    description: str
    default_enabled: bool = True
    available: bool = True


NOTIFICATION_KINDS: tuple[NotificationKind, ...] = (
    NotificationKind(
        code="runs",
        title="Мои пробежки",
        description="Результат появился в протоколе: время и место, новые уровни челленджей "
        "и вехи истории, постер о пробежке.",
    ),
    NotificationKind(
        code="ratings",
        title="Движение в рейтингах",
        description="Раз в неделю, в воскресенье днём: как изменилось ваше место в рейтингах "
        "после того, как все протоколы субботы загрузились.",
    ),
    NotificationKind(
        code="cancellations",
        title="Отмены стартов",
        description="Ближайший старт отменён или отмена снята — по всей стране, "
        "чтобы узнать заранее и о локации, куда только собираетесь.",
    ),
    NotificationKind(
        code="backlog",
        title="Мои карточки в бэклоге",
        description="Карточка принята, новые комментарии и смена статуса — по своим карточкам "
        "и тем, за которыми вы следите.",
    ),
    NotificationKind(
        code="backlog_new_cards",
        title="Новые карточки в бэклоге",
        description="Каждая новая идея или баг, который кто-то завёл в бэклоге. Для самых любопытных.",
        default_enabled=False,
    ),
)

KIND_BY_CODE: dict[str, NotificationKind] = {kind.code: kind for kind in NOTIFICATION_KINDS}
KIND_CODES: tuple[str, ...] = tuple(kind.code for kind in NOTIFICATION_KINDS)


def kind_enabled(kinds: dict[str, object] | None, code: str) -> bool:
    """Включён ли вид с учётом умолчания реестра. Неизвестный код — выключен."""
    kind = KIND_BY_CODE.get(code)
    if kind is None or not kind.available:
        return False
    if kinds and code in kinds:
        return bool(kinds[code])
    return kind.default_enabled
