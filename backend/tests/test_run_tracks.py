"""Разбор треков и метрики трассы — без базы, на синтетических данных."""

from __future__ import annotations

import json
import math

import pytest

from app.services.admin_track_import_service import _suggestions
from app.services.track_metrics import assess_quality, compute_metrics
from app.services.track_parsing import (
    TrackParseError,
    _extract_activity_payload,
    garmin_activity_id,
    parse_gpx,
    parse_tcx,
    parse_upload,
)

# Круг радиусом 200 м вокруг точки в Москве: четыре таких круга дают трассу
# длиной около 5 км — как на настоящих локациях.
CENTER_LAT = 55.6671
CENTER_LON = 37.4047
LAP_RADIUS_M = 200.0
LAPS = 4
SPEED_MS = 3.5


def _circle_track(*, laps: int = LAPS, step_sec: float = 1.0) -> list[tuple[float, float, float]]:
    lap_length = 2 * math.pi * LAP_RADIUS_M
    total_sec = laps * lap_length / SPEED_MS
    points: list[tuple[float, float, float]] = []
    elapsed = 0.0
    while elapsed <= total_sec:
        travelled = SPEED_MS * elapsed
        angle = travelled / LAP_RADIUS_M
        north_m = LAP_RADIUS_M * math.sin(angle)
        east_m = LAP_RADIUS_M * (1 - math.cos(angle))
        lat = CENTER_LAT + north_m / 111320.0
        lon = CENTER_LON + east_m / (111320.0 * math.cos(math.radians(CENTER_LAT)))
        points.append((lat, lon, elapsed))
        elapsed += step_sec
    return points


def test_metrics_measure_distance_and_laps() -> None:
    metrics = compute_metrics(_circle_track())

    expected_m = LAPS * 2 * math.pi * LAP_RADIUS_M
    # Сглаживание немного срезает круг — допускаем 3%.
    assert abs(metrics["distance_m"] - expected_m) / expected_m < 0.03
    assert metrics["lap_count"] == LAPS
    assert abs(metrics["lap_length_m"] - 2 * math.pi * LAP_RADIUS_M) < 60
    # Замкнутый круг: старт и финиш в одной точке.
    assert metrics["start_end_gap_m"] < 20
    # Четыре круга — это четыре полных оборота.
    assert 1200 < metrics["turn_sum_deg"] < 1600


def test_metrics_splits_cover_every_kilometre() -> None:
    metrics = compute_metrics(_circle_track())

    # Четыре круга по 200 м радиусом — это 5026 м, то есть пять полных километров.
    full_km = [split for split in metrics["splits"] if split["km"] is not None]
    assert [split["km"] for split in full_km] == [1, 2, 3, 4, 5]
    for split in full_km:
        # Скорость постоянная: километр укладывается в 1000 / 3.5 ≈ 286 секунд.
        assert abs(split["seconds"] - 1000 / SPEED_MS) < 15


def test_quality_class_depends_on_recording_rate() -> None:
    dense_class, dense = assess_quality(_circle_track(step_sec=1.0))
    assert dense_class == "A"
    assert dense["sample_interval_sec"] == 1.0

    # «Умная запись» с точкой раз в 8 секунд в паспорт трассы не годится.
    sparse_class, _ = assess_quality(_circle_track(step_sec=8.0))
    assert sparse_class == "C"


def test_metrics_ignore_too_short_tracks() -> None:
    assert compute_metrics([(CENTER_LAT, CENTER_LON, float(i)) for i in range(5)]) == {}


def _hill_elevations(points: list[tuple[float, float, float]]) -> list[float]:
    """Одна горка на трассе: плавный подъём к середине и спуск обратно."""
    total = len(points)
    return [100.0 + 20.0 * math.sin(math.pi * index / total) for index in range(total)]


def test_elevation_profile_describes_the_hill() -> None:
    points = _circle_track()
    metrics = compute_metrics(points, _hill_elevations(points))

    profile = metrics["elevation_profile"]
    assert len(profile) == 200
    # Профиль привязан к километражу: от нуля до полной дистанции.
    assert profile[0][0] == 0
    assert abs(profile[-1][0] - metrics["distance_m"]) < 30
    # Горка высотой 20 м: столько же набора и столько же спуска.
    assert abs(metrics["elevation_gain_profile_m"] - 20) <= 2
    assert abs(metrics["elevation_loss_profile_m"] - 20) <= 2
    assert abs(metrics["elevation_max_m"] - metrics["elevation_min_m"] - 20) < 1.5
    # Горка мягкая: уклон выше 1% только у подножий, остальное считается
    # плоским. Подъём и спуск при этом зеркальны.
    assert abs(metrics["uphill_share"] - metrics["downhill_share"]) < 0.05
    assert 0.1 < metrics["uphill_share"] < 0.35
    # Главный подъём — вся первая половина, уклон мягкий.
    assert metrics["climb_from_m"] == 0
    assert abs(metrics["climb_rise_m"] - 20) <= 2
    assert 0 < metrics["climb_grade_percent"] < 2


def test_metrics_without_elevation_have_no_profile() -> None:
    metrics = compute_metrics(_circle_track())

    assert "elevation_profile" not in metrics
    assert "climb_rise_m" not in metrics


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="fenix 7" xmlns="http://www.topografix.com/GPX/1/1">
 <trk><name>Утро</name><trkseg>
  <trkpt lat="55.6671" lon="37.4047"><ele>180.0</ele><time>2026-09-05T06:01:06Z</time></trkpt>
  <trkpt lat="55.6672" lon="37.4048"><ele>182.5</ele><time>2026-09-05T06:01:07Z</time></trkpt>
  <trkpt lat="55.6673" lon="37.4049"><ele>181.0</ele><time>2026-09-05T06:01:08Z</time></trkpt>
 </trkseg></trk>
</gpx>
"""


def test_parse_gpx_reads_points_device_and_elevation() -> None:
    track = parse_gpx(GPX_SAMPLE.encode(), "activity.gpx")

    assert len(track.points) == 3
    # creator у файла с самих часов — это модель.
    assert track.device_name == "fenix 7"
    assert track.started_at is not None
    assert track.duration_sec == 2
    assert track.min_elevation_m == 180.0
    assert track.max_elevation_m == 182.5
    # Оба движения выше порога шума в 1 м, поэтому зачтены целиком.
    assert track.elevation_gain_m == 2.5
    assert track.elevation_loss_m == 1.5


def test_parse_gpx_without_points_is_rejected() -> None:
    empty = '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk/></gpx>'
    with pytest.raises(TrackParseError):
        parse_gpx(empty.encode(), "empty.gpx")


TCX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Activities><Activity Sport="Running"><Lap StartTime="2026-09-05T06:01:06Z">
   <DistanceMeters>1000.0</DistanceMeters>
   <Track>
    <Trackpoint><Time>2026-09-05T06:01:06Z</Time>
     <Position><LatitudeDegrees>55.6671</LatitudeDegrees><LongitudeDegrees>37.4047</LongitudeDegrees></Position>
     <AltitudeMeters>180.0</AltitudeMeters><DistanceMeters>0</DistanceMeters></Trackpoint>
    <Trackpoint><Time>2026-09-05T06:01:07Z</Time>
     <Position><LatitudeDegrees>55.6672</LatitudeDegrees><LongitudeDegrees>37.4048</LongitudeDegrees></Position>
     <AltitudeMeters>180.5</AltitudeMeters><DistanceMeters>13</DistanceMeters></Trackpoint>
   </Track>
  </Lap>
  <Creator xsi:type="Device_t" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
   <Name>Forerunner 965</Name>
  </Creator>
 </Activity></Activities>
</TrainingCenterDatabase>
"""


def test_parse_tcx_takes_lap_distance_not_running_total() -> None:
    track = parse_tcx(TCX_SAMPLE.encode(), "activity.tcx")

    assert len(track.points) == 2
    assert track.device_name == "Forerunner 965"
    # Дистанция круга — 1000 м; нарастающий итог внутри точек складывать нельзя.
    assert track.device_distance_m == 1000.0


def test_parse_upload_detects_format_by_content() -> None:
    assert parse_upload("noname", GPX_SAMPLE.encode()).source == "gpx"
    assert parse_upload("noname", TCX_SAMPLE.encode()).source == "tcx"
    with pytest.raises(TrackParseError):
        parse_upload("photo.jpg", b"\xff\xd8\xff\xe0 not a track")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://connect.garmin.com/modern/activity/24243157399", "24243157399"),
        ("https://connect.garmin.com/modern/activity/embed/24243157399", "24243157399"),
        ("https://connect.garmin.com/app/activity/24243157399?x=1", "24243157399"),
        ("https://example.com/activity", None),
    ],
)
def test_garmin_activity_id(url: str, expected: str | None) -> None:
    assert garmin_activity_id(url) == expected


def test_extract_activity_payload_from_embed_html() -> None:
    # Garmin отдаёт страницу кусками RSC: JSON лежит внутри строк push([1,"…"]).
    payload = {"activityData": {"activityName": "Бег", "summaryDTO": {"distance": 4906.08}}}
    raw = json.dumps(payload, ensure_ascii=False)[1:-1]  # без внешних скобок
    half = len(raw) // 2
    html = (
        "<html><script>self.__next_f.push([1," + json.dumps("6:[" + raw[:half]) + "])</script>"
        "<script>self.__next_f.push([1," + json.dumps(raw[half:] + "]") + "])</script></html>"
    )

    extracted = _extract_activity_payload(html)
    assert extracted is not None
    assert extracted["activityName"] == "Бег"
    assert extracted["summaryDTO"]["distance"] == 4906.08


def test_extract_polyline_and_device_live_outside_activity_data() -> None:
    """Геометрия и устройство — соседи activityData, а не его поля.

    Раньше мы искали их внутри сводки и всегда получали «трека нет», хотя на
    странице он есть (регресс поймали на живой ссылке 21.09.2026).
    """
    from app.services.track_parsing import _extract_map_polyline, _extract_object, _rsc_blob

    props = (
        '{"activityData":{"activityId":1,"summaryDTO":{"distance":4991.89}},'
        '"activityDetailsForMap":{"geoPolylineDTO":{"polyline":[{"lat":57.1,"lon":35.4,"time":1787983283000}]}},'
        '"deviceInfo":{"productDisplayName":"Forerunner 965"}}'
    )
    html = "<script>self.__next_f.push([1," + json.dumps("6:" + props) + "])</script>"

    blob = _rsc_blob(html)
    assert _extract_object(blob, '"activityData":') is not None
    polyline = _extract_map_polyline(blob)
    assert polyline is not None
    assert len(polyline["polyline"]) == 1
    assert _extract_object(blob, '"deviceInfo":')["productDisplayName"] == "Forerunner 965"


def test_extract_activity_payload_missing_returns_none() -> None:
    assert _extract_activity_payload("<html><body>nothing here</body></html>") is None


BASE_METRICS = {
    "distance_m": 4990.0,
    "duration_sec": 1430.0,
    "max_speed_ms": 4.5,
    "elevation_min_m": 165.0,
    "elevation_max_m": 176.0,
}


def _fitness(**overrides):
    from app.services.track_validation import evaluate_course_fitness

    kwargs = {
        "metrics": dict(BASE_METRICS),
        "quality_class": "A",
        "elevation_gain_m": 32.0,
        "protocol_delta_sec": -1,
        "has_location": True,
        "start_distance_m": 80.0,
    }
    metrics_override = overrides.pop("metrics", None)
    if metrics_override:
        kwargs["metrics"].update(metrics_override)
    kwargs.update(overrides)
    return evaluate_course_fitness(**kwargs)


def test_normal_track_goes_into_course_measurements() -> None:
    eligible, reason, _note = _fitness()
    assert eligible is True
    assert reason is None


def test_track_from_another_place_is_rejected_first() -> None:
    # Даже при идеальных метриках старт за километры от локации — чужая пробежка.
    eligible, reason, note = _fitness(start_distance_m=12400.0)
    assert eligible is False
    assert reason == "far_from_location"
    assert "12,4 км" in note


def test_protocol_mismatch_is_rejected() -> None:
    # Щёлково 16.05.2026: запись класса A, но время протокола правили руками.
    eligible, reason, note = _fitness(metrics={"distance_m": 5220.0}, protocol_delta_sec=83)
    assert eligible is False
    # Длину проверяем раньше расхождения — 5220 м ещё в допуске, поэтому
    # срабатывает именно расхождение с протоколом.
    assert reason == "protocol_mismatch"
    assert "83" in note


def test_outliers_are_rejected() -> None:
    assert _fitness(metrics={"distance_m": 12000.0})[1] == "distance_out_of_range"
    assert _fitness(metrics={"max_speed_ms": 14.0})[1] == "speed_spike"
    assert _fitness(elevation_gain_m=900.0)[1] == "elevation_spike"
    assert _fitness(metrics={"elevation_max_m": 900.0})[1] == "elevation_span_spike"
    assert _fitness(metrics={"duration_sec": 6000.0})[1] == "implausible_pace"
    assert _fitness(quality_class="C")[1] == "low_quality"
    assert _fitness(has_location=False)[1] == "no_location"


def test_geometry_fingerprint_tells_same_course_from_another_one() -> None:
    """Отпечаток геометрии: та же трасса совпадает, соседний парк — нет."""
    from app.services.location_course_service import _cells, similarity

    base = _circle_track()
    same = [(lat + 0.00002, lon + 0.00002, time) for lat, lon, time in base]  # сдвиг 2 м
    other = [(lat + 0.01, lon + 0.01, time) for lat, lon, time in base]  # километр в сторону

    base_cells = _cells([[lat, lon] for lat, lon, _ in base])
    assert similarity(base_cells, _cells([[lat, lon] for lat, lon, _ in same])) > 0.8
    assert similarity(base_cells, _cells([[lat, lon] for lat, lon, _ in other])) == 0.0


# --- что предлагаем взять при импорте из админки -----------------------------


class _FakeTrack:
    """Минимум полей RunTrack, на которые смотрит отбор."""

    def __init__(
        self,
        name: str,
        *,
        run_result_id: object | None,
        distance_m: float,
        protocol_delta_sec: int | None = 0,
        is_course_eligible: bool = True,
    ) -> None:
        self.id = name
        self.run_result_id = run_result_id
        self.distance_m = distance_m
        self.protocol_delta_sec = protocol_delta_sec
        self.is_course_eligible = is_course_eligible


def test_import_suggests_everything_except_clear_outliers() -> None:
    # Случай Дмитрия 03.06.2023: к пробежке в Дружбе приехали два трека —
    # настоящая пятёрка и обрывок на 370 метров. Галочка должна остаться
    # только у пятёрки, и разбираться руками с этим админ не обязан.
    good = _FakeTrack("good", run_result_id="run-1", distance_m=5010.0)
    scrap = _FakeTrack("scrap", run_result_id="run-1", distance_m=370.0, protocol_delta_sec=-1384)
    weekday = _FakeTrack("weekday", run_result_id=None, distance_m=6610.0)
    verdict = _suggestions([good, scrap, weekday])

    assert verdict["good"] == (True, None)
    assert verdict["scrap"][0] is False
    assert "обрывок" in verdict["scrap"][1]
    assert verdict["weekday"][0] is False
    assert "протокол" in verdict["weekday"][1]


def test_import_keeps_the_better_of_two_tracks_for_one_run() -> None:
    # Оба трека похожи на пятёрку — тогда выигрывает тот, что ближе к
    # протоколу, а второй остаётся в списке со снятой галочкой.
    close = _FakeTrack("close", run_result_id="run-2", distance_m=5020.0, protocol_delta_sec=2)
    far = _FakeTrack("far", run_result_id="run-2", distance_m=5040.0, protocol_delta_sec=40)
    verdict = _suggestions([far, close])

    assert verdict["close"] == (True, None)
    assert verdict["far"][0] is False
    assert "лучше" in verdict["far"][1]
