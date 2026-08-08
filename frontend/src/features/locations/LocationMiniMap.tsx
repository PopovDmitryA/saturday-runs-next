import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { addZoomControl } from "../../lib/mapZoomControl";

type LocationMiniMapProps = {
  latitude: number;
  longitude: number;
  name: string;
};

const startIcon = L.divIcon({
  className: "map-marker map-marker-visited",
  html: '<span aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

export function LocationMiniMap({ latitude, longitude, name }: LocationMiniMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = L.map(containerRef.current, {
      center: [latitude, longitude],
      zoom: 14,
      scrollWheelZoom: false,
      // Как и на остальных картах сайта — без плашки атрибуции (у Leaflet в ней флаг).
      attributionControl: false,
      zoomControl: false,
    });
    addZoomControl(map);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
    L.marker([latitude, longitude], { icon: startIcon, title: name }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [latitude, longitude, name]);

  return <div ref={containerRef} className="loc-mini-map" role="img" aria-label={`Точка старта: ${name}`} />;
}
