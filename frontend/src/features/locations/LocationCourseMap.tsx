// Линия трассы на карте: типичный проход по данным треков участников.

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

export function LocationCourseMap({ geometry }: { geometry: [number, number][] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || geometry.length < 2) {
      return;
    }

    const map = L.map(container, { scrollWheelZoom: false, attributionControl: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    // prefix:false убирает из подписи флаг, который Leaflet рисует по умолчанию.
    L.control.attribution({ prefix: false }).addTo(map);

    const line = L.polyline(geometry, {
      color: "#c2521f",
      weight: 4,
      opacity: 0.9,
      lineJoin: "round",
    }).addTo(map);

    const start = geometry[0];
    const finish = geometry[geometry.length - 1];
    L.circleMarker(start, { radius: 7, color: "#fff", weight: 2, fillColor: "#2f7d5d", fillOpacity: 1 })
      .bindTooltip("Старт")
      .addTo(map);
    L.circleMarker(finish, { radius: 7, color: "#fff", weight: 2, fillColor: "#c2521f", fillOpacity: 1 })
      .bindTooltip("Финиш")
      .addTo(map);

    map.fitBounds(line.getBounds(), { padding: [18, 18] });
    return () => {
      map.remove();
    };
  }, [geometry]);

  if (geometry.length < 2) {
    return null;
  }
  return <div className="loc-course-map" ref={containerRef} />;
}
