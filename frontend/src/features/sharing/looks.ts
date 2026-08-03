// Луки постеров: фон + тон текста. Свайп в шторке листает именно луки —
// данные и композиция остаются, меняется «одежда» (паттерн Strava/Wrapped:
// выбор через ассортимент, а не конструктор).
//
// Палитры фиксированы и от темы сайта не зависят: постер уходит наружу.
// Значения — производные от портальных токенов (index.css), чтобы карточки
// узнавались как «те же цвета, что на сайте».

export type ShareTone = "light" | "dark";

export type ShareLook = {
  id: string;
  label: string;
  /** CSS background корня карточки. */
  background: string;
  /** Тон контента: dark = светлый текст на тёмном фоне. */
  tone: ShareTone;
  /** Слегка тонированная подложка метрик-плиток. */
  tileBackground: string;
  /** Цвет акцентной плашки и чипа. */
  accent: string;
  accentText: string;
};

/** Луки личных сюжетов (участник). */
export const RUNNER_LOOKS: ShareLook[] = [
  {
    id: "indigo",
    label: "Индиго",
    background: "linear-gradient(160deg, #312e81 0%, #4f46e5 55%, #7c3aed 100%)",
    tone: "dark",
    tileBackground: "rgba(255, 255, 255, 0.12)",
    accent: "#fbbf24",
    accentText: "#451a03",
  },
  {
    id: "night",
    label: "Ночь",
    background: "linear-gradient(170deg, #0f172a 0%, #101827 60%, #1e1b4b 100%)",
    tone: "dark",
    tileBackground: "rgba(255, 255, 255, 0.08)",
    accent: "#fbbf24",
    accentText: "#451a03",
  },
  {
    id: "porcelain",
    label: "Светлый",
    background: "linear-gradient(175deg, #f8fafc 0%, #eef2ff 100%)",
    tone: "light",
    tileBackground: "#ffffff",
    accent: "#4f46e5",
    accentText: "#ffffff",
  },
  {
    id: "sunrise",
    label: "Рассвет",
    background: "linear-gradient(160deg, #7c2d12 0%, #b45309 45%, #d97706 100%)",
    tone: "dark",
    tileBackground: "rgba(255, 255, 255, 0.14)",
    accent: "#fef3c7",
    accentText: "#78350f",
  },
];

/** Луки локаций — зелёно-бирюзовая ветка, отличаются от личных с первого взгляда. */
export const LOCATION_LOOKS: ShareLook[] = [
  {
    id: "forest",
    label: "Лес",
    background: "linear-gradient(165deg, #064e3b 0%, #0f766e 70%, #0d9488 100%)",
    tone: "dark",
    tileBackground: "rgba(255, 255, 255, 0.12)",
    accent: "#ffffff",
    accentText: "#064e3b",
  },
  {
    id: "night",
    label: "Ночь",
    background: "linear-gradient(170deg, #0f172a 0%, #101827 60%, #042f2e 100%)",
    tone: "dark",
    tileBackground: "rgba(255, 255, 255, 0.08)",
    accent: "#14b8a6",
    accentText: "#042f2e",
  },
  {
    id: "porcelain",
    label: "Светлый",
    background: "linear-gradient(175deg, #f8fafc 0%, #ccfbf1 100%)",
    tone: "light",
    tileBackground: "#ffffff",
    accent: "#0d9488",
    accentText: "#ffffff",
  },
];

/**
 * Лук «Своё фото»: фон — фото пользователя (затемняющий оверлей рисует
 * карточка), контент всегда светлый.
 */
export const PHOTO_LOOK: ShareLook = {
  id: "photo",
  label: "Своё фото",
  background: "#334155",
  tone: "dark",
  tileBackground: "rgba(15, 23, 42, 0.45)",
  accent: "#fbbf24",
  accentText: "#451a03",
};

export function looksFor(audience: "runner" | "location"): ShareLook[] {
  return audience === "location" ? LOCATION_LOOKS : RUNNER_LOOKS;
}

/** Трансформация своего фото под конкретный формат (позиция + масштаб). */
export type PhotoTransform = {
  /** Смещение центра в долях размера карточки: 0 = центр. */
  offsetX: number;
  offsetY: number;
  /** Дополнительный зум поверх cover-масштаба, ≥ 1. */
  scale: number;
};

export const DEFAULT_PHOTO_TRANSFORM: PhotoTransform = { offsetX: 0, offsetY: 0, scale: 1 };

/** Состояние своего фото: объект-URL и трансформации по форматам. */
export type SharePhoto = {
  objectUrl: string;
  /** naturalWidth/naturalHeight, чтобы считать cover-масштаб. */
  width: number;
  height: number;
  transforms: Partial<Record<string, PhotoTransform>>;
};
