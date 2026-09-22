"""Разбор треков: GPX, TCX, FIT и публичная ссылка на активность Garmin.

Все источники приводятся к одному виду (ParsedTrack): точки с координатами,
временем и высотой плюс то, что источник знает о приборе и о самой пробежке.

Про высоту: набор берём из прибора, а не считаем по точкам — барометрические
часы на равнинных трассах точнее высотных моделей (замер 09.09.2026: SRTM дал
39 м набора там, где реальный перепад 6 м). Если прибор набор не сообщил,
считаем по высотам точек с порогом, чтобы не собирать шум.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

# Высоты дрожат даже у барометра: подъёмы меньше порога в набор не идут.
ELEVATION_NOISE_THRESHOLD_M = 1.0
GARMIN_EMBED_URL = "https://connect.garmin.com/modern/activity/embed/{activity_id}"
GARMIN_TIMEOUT_SEC = 20.0
# Часы без альтиметра: набор высоты у них считается по GPS и годится только
# для личного просмотра.
BAROMETER_CAPABILITY = "ALTIMETER_CAPABLE"


class TrackParseError(ValueError):
    """Файл или ссылку разобрать не удалось — текст показываем участнику."""


@dataclass
class TrackPoint:
    lat: float
    lon: float
    # Абсолютное время точки; у файлов без времени остаётся None.
    at: datetime | None = None
    elevation_m: float | None = None


@dataclass
class ParsedTrack:
    source: str
    source_ref: str
    points: list[TrackPoint] = field(default_factory=list)
    started_at: datetime | None = None
    duration_sec: int | None = None
    device_distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    min_elevation_m: float | None = None
    max_elevation_m: float | None = None
    device_name: str | None = None
    device_firmware: str | None = None
    has_barometer: bool | None = None
    source_url: str | None = None
    activity_name: str | None = None


def parse_upload(filename: str, data: bytes) -> ParsedTrack:
    """Определяет формат по имени файла и содержимому."""
    name = (filename or "").lower()
    if name.endswith(".fit") or data[8:12] == b".FIT":
        return parse_fit(data, filename)
    head = data[:400].lstrip()
    if name.endswith(".tcx") or b"TrainingCenterDatabase" in head:
        return parse_tcx(data, filename)
    if name.endswith(".gpx") or head.startswith(b"<?xml") or b"<gpx" in head:
        return parse_gpx(data, filename)
    raise TrackParseError("Не понимаю формат файла. Подойдут GPX, TCX или FIT с часов.")


def _tag(element: ET.Element) -> str:
    """Имя тега без пространства имён — их у GPX и TCX несколько версий."""
    return element.tag.rsplit("}", 1)[-1]


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_gpx(data: bytes, filename: str = "") -> ParsedTrack:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise TrackParseError("Файл GPX повреждён или это не GPX.") from exc

    points: list[TrackPoint] = []
    for element in root.iter():
        if _tag(element) != "trkpt":
            continue
        lat = _float(element.get("lat"))
        lon = _float(element.get("lon"))
        if lat is None or lon is None:
            continue
        at = None
        elevation = None
        for child in element:
            name = _tag(child)
            if name == "time":
                at = _parse_time(child.text)
            elif name == "ele":
                elevation = _float(child.text)
        points.append(TrackPoint(lat=lat, lon=lon, at=at, elevation_m=elevation))

    if not points:
        raise TrackParseError("В файле нет ни одной точки трека.")

    # creator у GPX с часов — модель («fenix 7»), у выгрузки из сервиса — имя
    # сервиса («Garmin Connect», «StravaGPX iPhone»).
    creator = (root.get("creator") or "").strip() or None
    track = ParsedTrack(source="gpx", source_ref=filename or "track.gpx", points=points, device_name=creator)
    _fill_from_points(track)
    return track


def parse_tcx(data: bytes, filename: str = "") -> ParsedTrack:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise TrackParseError("Файл TCX повреждён или это не TCX.") from exc

    points: list[TrackPoint] = []
    distance_m: float | None = None
    device_name: str | None = None
    for element in root.iter():
        name = _tag(element)
        if name == "Trackpoint":
            lat = lon = at = elevation = None
            for child in element.iter():
                child_name = _tag(child)
                if child_name == "LatitudeDegrees":
                    lat = _float(child.text)
                elif child_name == "LongitudeDegrees":
                    lon = _float(child.text)
                elif child_name == "Time":
                    at = _parse_time(child.text)
                elif child_name == "AltitudeMeters":
                    elevation = _float(child.text)
            if lat is not None and lon is not None:
                points.append(TrackPoint(lat=lat, lon=lon, at=at, elevation_m=elevation))
        elif name == "Lap":
            # Дистанция круга — прямой потомок Lap; такой же тег внутри
            # Trackpoint хранит нарастающий итог, его складывать нельзя.
            for child in element:
                if _tag(child) == "DistanceMeters":
                    value = _float(child.text)
                    if value:
                        distance_m = (distance_m or 0) + value
        elif name == "Creator":
            for child in element:
                if _tag(child) == "Name" and child.text:
                    device_name = child.text.strip()

    if not points:
        raise TrackParseError("В файле нет ни одной точки трека.")

    track = ParsedTrack(
        source="tcx",
        source_ref=filename or "track.tcx",
        points=points,
        device_distance_m=distance_m,
        device_name=device_name,
    )
    _fill_from_points(track)
    return track


def parse_fit(data: bytes, filename: str = "") -> ParsedTrack:
    try:
        import fitdecode  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — зависимость есть в образе
        raise TrackParseError("На сервере не установлен разборщик FIT. Приложите GPX или ссылку.") from exc

    import io

    points: list[TrackPoint] = []
    device_name: str | None = None
    firmware: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    distance_m = ascent = descent = None
    has_barometer: bool | None = None

    def semicircles(value: int | None) -> float | None:
        # FIT хранит координаты в «полукругах»: 2^31 полукругов = 180°.
        return None if value is None else value * (180.0 / 2**31)

    try:
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "record":
                    lat = semicircles(_fit_value(frame, "position_lat"))
                    lon = semicircles(_fit_value(frame, "position_long"))
                    if lat is None or lon is None:
                        continue
                    at = _fit_value(frame, "timestamp")
                    if isinstance(at, datetime):
                        at = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
                    else:
                        at = None
                    elevation = _fit_value(frame, "enhanced_altitude")
                    if elevation is None:
                        elevation = _fit_value(frame, "altitude")
                    points.append(TrackPoint(lat=lat, lon=lon, at=at, elevation_m=elevation))
                elif frame.name == "session":
                    distance_m = distance_m or _fit_value(frame, "total_distance")
                    ascent = ascent if ascent is not None else _fit_value(frame, "total_ascent")
                    descent = descent if descent is not None else _fit_value(frame, "total_descent")
                elif frame.name == "file_id":
                    manufacturer = manufacturer or _fit_str(_fit_value(frame, "manufacturer"))
                    product = product or _fit_str(
                        _fit_value(frame, "garmin_product") or _fit_value(frame, "product")
                    )
                elif frame.name == "device_info":
                    name = _fit_str(_fit_value(frame, "product_name")) or _fit_str(_fit_value(frame, "garmin_product"))
                    if name and not device_name:
                        device_name = name
                    version = _fit_value(frame, "software_version")
                    if version is not None and not firmware:
                        firmware = str(version)
    except Exception as exc:  # noqa: BLE001 — любой сбой разбора показываем как понятную ошибку
        raise TrackParseError("Не удалось разобрать FIT-файл.") from exc

    if not points:
        raise TrackParseError("В FIT-файле нет координат — часы не поймали GPS.")

    if not device_name:
        device_name = " ".join(part for part in (manufacturer, product) if part) or None
    if ascent is not None:
        # Набор из прибора есть — значит альтиметр отработал.
        has_barometer = True

    track = ParsedTrack(
        source="fit",
        source_ref=filename or "activity.fit",
        points=points,
        device_distance_m=distance_m,
        elevation_gain_m=ascent,
        elevation_loss_m=descent,
        device_name=device_name,
        device_firmware=firmware,
        has_barometer=has_barometer,
    )
    _fill_from_points(track)
    return track


def _fit_value(frame: Any, name: str) -> Any:
    try:
        if frame.has_field(name):
            return frame.get_value(name)
    except (KeyError, ValueError):
        return None
    return None


def _fit_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


GARMIN_ACTIVITY_ID_RE = re.compile(r"/activity/(?:embed/)?(\d{6,})")
_RSC_CHUNK_RE = re.compile(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)", re.DOTALL)


def garmin_activity_id(url: str) -> str | None:
    match = GARMIN_ACTIVITY_ID_RE.search(url or "")
    return match.group(1) if match else None


def parse_garmin_link(url: str, *, client: httpx.Client | None = None) -> ParsedTrack:
    """Достаёт активность по публичной ссылке Garmin Connect.

    Данные лежат внутри встраиваемой страницы: обычный адрес активности
    отдаёт только оболочку приложения. Это не документированный API, поэтому
    любые изменения на стороне Garmin ловим и показываем понятной ошибкой.
    """
    activity_id = garmin_activity_id(url)
    if not activity_id:
        raise TrackParseError("Не похоже на ссылку активности Garmin Connect.")

    own_client = client is None
    client = client or httpx.Client(timeout=GARMIN_TIMEOUT_SEC, follow_redirects=True)
    try:
        response = client.get(
            GARMIN_EMBED_URL.format(activity_id=activity_id),
            headers={"User-Agent": "Mozilla/5.0 (compatible; run5k.run track import)"},
        )
    except httpx.HTTPError as exc:
        raise TrackParseError("Garmin Connect не ответил. Попробуйте позже или приложите файл.") from exc
    finally:
        if own_client:
            client.close()

    if response.status_code == 404:
        raise TrackParseError("Активность не найдена. Проверьте ссылку.")
    if response.status_code != 200:
        raise TrackParseError("Garmin Connect не отдал активность. Попробуйте позже или приложите файл.")

    blob = _rsc_blob(response.text)
    payload = _extract_object(blob, '"activityData":')
    if payload is None:
        raise TrackParseError(
            "Активность закрыта настройками приватности. Откройте её или приложите файл с часов."
        )
    return _garmin_payload_to_track(
        payload,
        activity_id,
        url,
        polyline=_extract_map_polyline(blob),
        # deviceInfo, как и геометрия, лежит соседним свойством, не внутри сводки.
        device=_extract_object(blob, '"deviceInfo":'),
    )


def _rsc_blob(html: str) -> str:
    """Склеивает куски, которыми Next.js отдаёт страницу активности."""
    parts: list[str] = []
    for match in _RSC_CHUNK_RE.finditer(html):
        try:
            parts.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def _extract_object(blob: str, marker: str) -> dict[str, Any] | None:
    """Вырезает из склеенной строки JSON-объект, идущий сразу за маркером."""
    start = blob.find(marker)
    if start < 0:
        return None
    start += len(marker)
    depth = 0
    for index in range(start, len(blob)):
        char = blob[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed: dict[str, Any] = json.loads(blob[start : index + 1])
                    return parsed
                except json.JSONDecodeError:
                    return None
    return None


def _extract_activity_payload(html: str) -> dict[str, Any] | None:
    """Сводка активности: дистанция, набор, устройство, время старта."""
    return _extract_object(_rsc_blob(html), '"activityData":')


def _extract_map_polyline(html_or_blob: str) -> dict[str, Any] | None:
    """Геометрия трека.

    Лежит НЕ внутри activityData, а соседним свойством того же компонента
    (activityDetailsForMap). Раньше мы искали её внутри сводки и всегда
    получали пусто, хотя трек на странице есть.
    """
    details = _extract_object(html_or_blob, '"activityDetailsForMap":')
    if not details:
        return None
    polyline = details.get("geoPolylineDTO")
    return polyline if isinstance(polyline, dict) else None


def _garmin_payload_to_track(
    payload: dict[str, Any],
    activity_id: str,
    url: str,
    *,
    polyline: dict[str, Any] | None = None,
    device: dict[str, Any] | None = None,
) -> ParsedTrack:
    summary = payload.get("summaryDTO") or {}
    metadata = payload.get("metadataDTO") or {}
    if device is None:
        device = payload.get("deviceInfo") or {}
    if polyline is None:
        polyline = ((payload.get("activityDetailsForMap") or {}).get("geoPolylineDTO")) or {}
    raw_points = polyline.get("polyline") or []

    points: list[TrackPoint] = []
    for raw in raw_points:
        lat, lon = raw.get("lat"), raw.get("lon")
        if lat is None or lon is None:
            continue
        at = None
        if raw.get("time"):
            at = datetime.fromtimestamp(raw["time"] / 1000, tz=UTC)
        points.append(TrackPoint(lat=lat, lon=lon, at=at, elevation_m=raw.get("altitude")))

    if not points:
        raise TrackParseError(
            "У этой активности нет трека — часы не поймали GPS или карта скрыта настройками приватности."
        )

    capabilities = {item.get("name") for item in (device.get("capabilities") or []) if isinstance(item, dict)}
    track = ParsedTrack(
        source="garmin_link",
        source_ref=activity_id,
        source_url=url,
        points=points,
        device_distance_m=summary.get("distance"),
        elevation_gain_m=summary.get("elevationGain"),
        elevation_loss_m=summary.get("elevationLoss"),
        min_elevation_m=summary.get("minElevation"),
        max_elevation_m=summary.get("maxElevation"),
        device_name=device.get("productDisplayName"),
        device_firmware=device.get("versionString"),
        has_barometer=BAROMETER_CAPABILITY in capabilities if capabilities else None,
        activity_name=payload.get("activityName"),
    )
    started_at = _parse_time(summary.get("startTimeGMT"))
    if started_at:
        track.started_at = started_at
    duration = summary.get("duration")
    if duration:
        track.duration_sec = int(round(duration))
    _fill_from_points(track, keep_existing=True)
    # Флаг Garmin: правил ли сервис высоту по картам вместо барометра.
    if metadata.get("elevationCorrected") is True:
        track.has_barometer = False
    return track


def _fill_from_points(track: ParsedTrack, *, keep_existing: bool = False) -> None:
    """Досчитывает по точкам то, чего источник не сообщил."""
    times = [point.at for point in track.points if point.at]
    if times:
        if track.started_at is None or not keep_existing:
            track.started_at = min(times)
        if track.duration_sec is None or not keep_existing:
            track.duration_sec = int(round((max(times) - min(times)).total_seconds()))

    elevations = [point.elevation_m for point in track.points if point.elevation_m is not None]
    if not elevations:
        return
    if track.min_elevation_m is None:
        track.min_elevation_m = min(elevations)
    if track.max_elevation_m is None:
        track.max_elevation_m = max(elevations)
    if track.elevation_gain_m is None:
        gain = loss = 0.0
        reference = elevations[0]
        for value in elevations[1:]:
            delta = value - reference
            if abs(delta) < ELEVATION_NOISE_THRESHOLD_M:
                continue
            if delta > 0:
                gain += delta
            else:
                loss -= delta
            reference = value
        track.elevation_gain_m = round(gain, 1)
        track.elevation_loss_m = round(loss, 1)
