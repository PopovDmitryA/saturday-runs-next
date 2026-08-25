"""Единая логика статуса площадки: действует / скоро / не действует."""

from datetime import date

from app.services.location_activity_status import (
    INACTIVE_AFTER_DAYS,
    LocationActivity,
    location_activity,
    merge_activity,
)

TODAY = date(2026, 8, 20)


def test_registry_beats_the_silence_rule() -> None:
    """Заявление системы сильнее нашей догадки по датам.

    Шуваловский парк 5 вёрст держат «на паузе», хотя он там ни разу не бегал:
    без приоритета источника он попал бы в «скоро» и обещал открытие, которого
    никто не готовит (репорт Дмитрия 19.08.2026).
    """
    assert (
        location_activity(is_paused=True, is_upcoming=False, last_event_date=None, as_of=TODAY)
        is LocationActivity.inactive
    )
    assert (
        location_activity(is_paused=False, is_upcoming=True, last_event_date=TODAY, as_of=TODAY)
        is LocationActivity.upcoming
    )


def test_silence_threshold_is_a_hundred_days() -> None:
    """Ровно на пороге площадка ещё действует, за ним — уже нет."""
    last = date.fromordinal(TODAY.toordinal() - INACTIVE_AFTER_DAYS)
    assert (
        location_activity(is_paused=False, is_upcoming=False, last_event_date=last, as_of=TODAY)
        is LocationActivity.active
    )
    older = date.fromordinal(TODAY.toordinal() - INACTIVE_AFTER_DAYS - 1)
    assert (
        location_activity(is_paused=False, is_upcoming=False, last_event_date=older, as_of=TODAY)
        is LocationActivity.inactive
    )


def test_location_without_any_start_is_upcoming() -> None:
    """Площадка заведена, стартов не было ни разу — «скоро», а не «активна»:
    зелёная точка обещала бы старт, которого пока нет."""
    assert (
        location_activity(is_paused=False, is_upcoming=False, last_event_date=None, as_of=TODAY)
        is LocationActivity.upcoming
    )


def test_merge_keeps_the_park_alive_across_systems() -> None:
    """Парк, ушедший из parkrun в 5 вёрст, действует: статус физической площадки
    берётся по самой живой из её систем."""
    assert (
        merge_activity([LocationActivity.inactive, LocationActivity.active])
        is LocationActivity.active
    )
    assert (
        merge_activity([LocationActivity.inactive, LocationActivity.upcoming])
        is LocationActivity.upcoming
    )
    assert merge_activity([LocationActivity.inactive]) is LocationActivity.inactive
    assert merge_activity([]) is LocationActivity.inactive


def test_historic_platform_never_promises_an_opening() -> None:
    """У parkrun пустая история значит «протоколов не собрали», а не «скоро
    откроется»: без этого 12 его строк без событий обещали бы открытие."""
    assert (
        location_activity(
            is_paused=False,
            is_upcoming=False,
            last_event_date=None,
            as_of=TODAY,
            is_historic=True,
        )
        is LocationActivity.inactive
    )
