"""Какие ошибки очереди профилей считаются окончательными.

Строки с окончательной ошибкой не воскрешаются при сбросе. До правки они
крутились вечно: попытки исчерпывались, строка падала в failed без признака,
сброс возвращал её в очередь — и так каждые 20 минут.
"""

from __future__ import annotations

from app.parkrun.errors import ParkrunProfileNotFound, ParkrunProfileParseError
from app.services.profile_fetch_pending_service import _is_permanent_profile_error


class _WithStatus(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


def test_missing_profile_is_permanent() -> None:
    assert _is_permanent_profile_error(ParkrunProfileNotFound("нет такого"))
    assert _is_permanent_profile_error(_WithStatus("not found", 404))


def test_conflict_is_permanent() -> None:
    """409 — профиль уже привязан; разрешается только человеком."""
    assert _is_permanent_profile_error(
        _WithStatus("Профиль на этой платформе уже привязан к вашему аккаунту", 409)
    )
    assert _is_permanent_profile_error(
        _WithStatus("Этот профиль уже привязан к другому аккаунту", 409)
    )


def test_unparseable_page_is_permanent() -> None:
    """parkrun отдаёт заглушку с кодом 200 вместо 404 на несуществующий номер."""
    assert _is_permanent_profile_error(
        ParkrunProfileParseError("Не удалось найти имя и штрихкод на странице parkrun")
    )


def test_found_through_cause_chain() -> None:
    """preview-путь заворачивает исходную ошибку в ProfileLinkingError."""
    inner = ParkrunProfileParseError("нет имени и штрихкода")
    outer = _WithStatus("не удалось получить профиль", 400)
    outer.__cause__ = inner
    assert _is_permanent_profile_error(outer)


def test_transient_errors_stay_retryable() -> None:
    """Временное не должно закрываться навсегда — иначе потеряем живые профили."""
    assert not _is_permanent_profile_error(TimeoutError("сеть отвалилась"))
    assert not _is_permanent_profile_error(_WithStatus("сервис недоступен", 503))
    assert not _is_permanent_profile_error(_WithStatus("плохой запрос", 400))


def test_cause_chain_is_bounded() -> None:
    """Зацикленная цепочка причин не должна вешать проверку."""
    a = TimeoutError("a")
    b = TimeoutError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not _is_permanent_profile_error(a)
