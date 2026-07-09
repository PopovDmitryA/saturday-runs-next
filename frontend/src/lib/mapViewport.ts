import type { MutableRefObject } from "react";

// Общий вьюпорт (центр + зум), чтобы карта локаций и карта регионов
// сохраняли положение при переключении между собой.
export type MapViewport = { lat: number; lng: number; zoom: number };

export type MapViewportRef = MutableRefObject<MapViewport | null>;
