import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Трек рисуем сегментами: цвет — темп на этом отрезке, от медленного к быстрому.
// Шкала та же, что в карточке трассы: тёмно-фиолетовый → оранжевый.
const PACE_RAMP: [number, number, number][] = [
  [59, 38, 96],
  [94, 60, 140],
  [168, 74, 120],
  [225, 110, 70],
  [245, 183, 60],
];
// По сколько точек склеиваем в один сегмент: на секундной записи 3 точки —
// это ~10 метров, цвет читается, а полилиний не тысячи.
const SEGMENT_POINTS = 3;

type TrackPoint = [number, number, number, number | null];

function rampColor(value: number): string {
  const clamped = Math.max(0, Math.min(1, value));
  const position = clamped * (PACE_RAMP.length - 1);
  const low = Math.floor(position);
  const high = Math.min(low + 1, PACE_RAMP.length - 1);
  const fraction = position - low;
  const channel = (index: number) =>
    Math.round(PACE_RAMP[low][index] + (PACE_RAMP[high][index] - PACE_RAMP[low][index]) * fraction);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function metersBetween(a: TrackPoint, b: TrackPoint): number {
  return L.latLng(a[0], a[1]).distanceTo(L.latLng(b[0], b[1]));
}

export function RunTrackMap({ points }: { points: TrackPoint[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || points.length < 2) {
      return;
    }

    const map = L.map(container, { scrollWheelZoom: false, attributionControl: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    // prefix:false убирает из подписи флаг, который Leaflet рисует по умолчанию.
    L.control.attribution({ prefix: false }).addTo(map);
    mapRef.current = map;

    // Скорость на каждом сегменте, чтобы разложить цвета по перцентилям: так
    // раскраска читается и у быстрых, и у медленных пробежек.
    const speeds: number[] = [];
    for (let index = 0; index + SEGMENT_POINTS < points.length; index += SEGMENT_POINTS) {
      const from = points[index];
      const to = points[index + SEGMENT_POINTS];
      const seconds = to[2] - from[2];
      speeds.push(seconds > 0 ? metersBetween(from, to) / seconds : 0);
    }
    const sorted = [...speeds].filter((value) => value > 0).sort((a, b) => a - b);
    const low = sorted[Math.floor(sorted.length * 0.05)] ?? 0;
    const high = sorted[Math.floor(sorted.length * 0.95)] ?? low + 1;

    let cursor = 0;
    for (let index = 0; index + SEGMENT_POINTS < points.length; index += SEGMENT_POINTS) {
      const from = points[index];
      const to = points[index + SEGMENT_POINTS];
      const speed = speeds[cursor] ?? 0;
      cursor += 1;
      L.polyline(
        [
          [from[0], from[1]],
          [to[0], to[1]],
        ],
        {
          color: rampColor((speed - low) / (high - low || 1)),
          weight: 4,
          opacity: 0.95,
          lineCap: "round",
        },
      ).addTo(map);
    }

    const start = points[0];
    const finish = points[points.length - 1];
    L.circleMarker([start[0], start[1]], {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: "#2f7d5d",
      fillOpacity: 1,
    })
      .bindTooltip("Старт")
      .addTo(map);
    L.circleMarker([finish[0], finish[1]], {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: "#c2521f",
      fillOpacity: 1,
    })
      .bindTooltip("Финиш")
      .addTo(map);

    map.fitBounds(L.latLngBounds(points.map((point) => [point[0], point[1]] as [number, number])), {
      padding: [18, 18],
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [points]);

  if (points.length < 2) {
    return null;
  }
  return <div className="run-track-map" ref={containerRef} />;
}
