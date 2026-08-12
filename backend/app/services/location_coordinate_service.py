"""Заявки на координаты новых локаций: диалог с админом Reply-сообщениями.

Канал — Telegram (`admin_notify.notify_admin_dialog`). Пока admin-бот жил в ВК,
запросы уходили туда; после перевода бота на Telegram ответы в ВК стало некому
разбирать, и с 03.08.2026 диалог снова идёт там же, где бот.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.core.admin import is_admin_telegram_id
from app.geo.reverse_geocode import lookup_region
from app.models import Location, LocationCoordinateRequest, Platform
from app.services.admin_notify import admin_dialog_chat_id, notify_admin_dialog

logger = logging.getLogger(__name__)

COORD_PAIR_RE = re.compile(
    r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[:,;]\s*(-?\d{1,2}(?:\.\d+)?)\s*$"
)
OK_RE = re.compile(r"^(ок|ok|да|yes)\s*\.?\s*$", re.I)

REPLY_REQUIRED_HINT = (
    "Ответьте Reply на сообщение бота о локации.\n"
    "Координаты — ответом на запрос локации (latitude:longitude).\n"
    "«ок» — ответом на сообщение с проверкой Яндекс-карты."
)


def yandex_verification_url(latitude: float, longitude: float) -> str:
    return f"https://yandex.ru/maps/?pt={longitude},{latitude}&z=17&l=map"


def parse_coordinate_pair(text: str) -> tuple[float, float] | None:
    match = COORD_PAIR_RE.match(text.strip())
    if not match:
        return None
    first = float(match.group(1))
    second = float(match.group(2))
    if abs(first) > 90:
        return second, first
    return first, second


def send_admin_message_sync(
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> tuple[int, int] | None:
    """Сообщение админу в Telegram; возвращает (chat_id, message_id) для Reply-связки."""
    return notify_admin_dialog(text, reply_to_message_id=reply_to_message_id)


def _admin_peer_id() -> int | None:
    return admin_dialog_chat_id()


def find_request_by_reply_message(
    db: Session,
    chat_id: int,
    reply_to_message_id: int,
) -> LocationCoordinateRequest | None:
    return (
        db.query(LocationCoordinateRequest)
        .options(joinedload(LocationCoordinateRequest.location))
        .filter(
            LocationCoordinateRequest.admin_telegram_chat_id == chat_id,
            (
                (LocationCoordinateRequest.request_telegram_message_id == reply_to_message_id)
                | (LocationCoordinateRequest.verify_telegram_message_id == reply_to_message_id)
            ),
        )
        .order_by(LocationCoordinateRequest.created_at.desc())
        .first()
    )


def maybe_request_coordinates_for_new_location(
    db: Session,
    location: Location,
    *,
    is_new: bool,
    map_url: str | None = None,
) -> LocationCoordinateRequest | None:
    if location.latitude is not None and location.longitude is not None:
        return None

    get_settings()
    admin_peer_id = _admin_peer_id()
    if not admin_peer_id:
        logger.info("admin messenger not configured, skip coordinate request for %s", location.external_key)
        return None

    pending = (
        db.query(LocationCoordinateRequest)
        .filter(
            LocationCoordinateRequest.location_id == location.id,
            LocationCoordinateRequest.status.in_(("awaiting_coordinates", "awaiting_confirmation")),
        )
        .first()
    )
    if pending is not None:
        return pending

    if not is_new:
        already_handled = (
            db.query(LocationCoordinateRequest)
            .filter(
                LocationCoordinateRequest.location_id == location.id,
                LocationCoordinateRequest.status == "confirmed",
            )
            .first()
        )
        if already_handled is not None:
            return None

    if map_url:
        location.map_url = map_url

    request = LocationCoordinateRequest(
        location_id=location.id,
        map_url=map_url or location.map_url,
        status="awaiting_coordinates",
        admin_telegram_chat_id=admin_peer_id,
    )
    db.add(request)
    db.flush()

    platform = db.query(Platform).filter(Platform.id == location.platform_id).one()
    source = location.source_url or "—"
    map_line = f"\nКарта на сайте: {map_url or location.map_url}" if (map_url or location.map_url) else ""
    title = "Новая локация с95 без координат" if is_new else "Локация с95 без координат"
    message = (
        f"{title}\n"
        f"Платформа: {platform.code}\n"
        f"Название: {location.name}\n"
        f"Ключ: {location.external_key}\n"
        f"Страница: {source}{map_line}\n\n"
        f"Ответьте Reply на это сообщение координатами старта:\n"
        f"latitude:longitude (например 55.703:37.623)"
    )
    sent = send_admin_message_sync(message)
    if sent is not None:
        _, message_id = sent
        request.request_telegram_message_id = message_id
    db.commit()
    return request


def propose_coordinates(
    db: Session,
    request: LocationCoordinateRequest,
    latitude: float,
    longitude: float,
) -> str:
    request.proposed_latitude = latitude
    request.proposed_longitude = longitude
    request.status = "awaiting_confirmation"
    request.updated_at = datetime.now(timezone.utc)
    db.flush()

    location = request.location
    verify_url = yandex_verification_url(latitude, longitude)
    verify_text = (
        f"Локация: {location.name} ({location.external_key})\n"
        f"Проверьте точку старта: {verify_url}\n\n"
        f"Ответьте Reply на это сообщение «ок», если точка верна.\n"
        f"Иначе — Reply снова с latitude:longitude."
    )
    sent = send_admin_message_sync(verify_text)
    if sent is not None:
        _, verify_message_id = sent
        request.verify_telegram_message_id = verify_message_id
    db.commit()
    return ""


def confirm_coordinates(db: Session, request: LocationCoordinateRequest) -> str:
    if request.proposed_latitude is None or request.proposed_longitude is None:
        raise ValueError("Coordinates not proposed yet")

    location = request.location
    location.latitude = request.proposed_latitude
    location.longitude = request.proposed_longitude
    region = lookup_region(request.proposed_latitude, request.proposed_longitude)
    if region:
        location.region = region

    request.status = "confirmed"
    request.updated_at = datetime.now(timezone.utc)
    db.commit()

    city = location.city or "—"
    region_name = location.region or "—"
    return (
        f"Координаты применены для {location.name}.\n"
        f"latitude={location.latitude}, longitude={location.longitude}\n"
        f"Город (из страницы): {city}\n"
        f"Регион (geocode): {region_name}"
    )


def handle_admin_coordinate_message(
    db: Session,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None,
    messenger: str = "telegram",
) -> str | None:
    settings = get_settings()
    if messenger != "telegram":
        return None
    if not is_admin_telegram_id(chat_id, settings):
        return None

    if reply_to_message_id is None:
        if parse_coordinate_pair(text) or OK_RE.match(text.strip()):
            return REPLY_REQUIRED_HINT
        return None

    request = find_request_by_reply_message(db, chat_id, reply_to_message_id)
    if request is None:
        # В админском чате бот ведёт и другие диалоги, поэтому чужой Reply отдаём
        # дальше по цепочке обработчиков и подсказываем, только если админ явно
        # прислал координаты или «ок» — но не тому сообщению.
        if parse_coordinate_pair(text) or OK_RE.match(text.strip()):
            return (
                "Не найдена заявка по этому сообщению.\n"
                "Ответьте Reply именно на сообщение бота о локации или на проверку карты."
            )
        return None

    is_verify_reply = request.verify_telegram_message_id == reply_to_message_id
    is_request_reply = request.request_telegram_message_id == reply_to_message_id

    if OK_RE.match(text.strip()):
        if not is_verify_reply:
            return "«ок» нужно отправить Reply на сообщение с проверкой Яндекс-карты."
        if request.status != "awaiting_confirmation":
            return "Сначала пришлите координаты Reply на сообщение о локации."
        return confirm_coordinates(db, request)

    pair = parse_coordinate_pair(text)
    if pair is None:
        return "Ожидаются координаты latitude:longitude или «ок» (Reply на нужное сообщение бота)."

    if not is_request_reply and not is_verify_reply:
        return REPLY_REQUIRED_HINT

    latitude, longitude = pair
    if is_verify_reply and request.status == "awaiting_coordinates":
        return "Сначала пришлите координаты Reply на сообщение о локации."

    propose_coordinates(db, request, latitude, longitude)
    return (
        f"Принято: {latitude}:{longitude} для {request.location.name}.\n"
        f"Проверьте следующее сообщение бота с картой и ответьте на него «ок»."
    )
