// Кнопка «Проложить маршрут» на странице локации.
//
// Выбор навигатора приходится делать самим: узнать из браузера, какие карты
// стоят у человека, нельзя — такого API нет. Схема `geo:` показала бы на
// Android системный список, но на iOS не работает вовсе, а во встроенном
// браузере Telegram (откуда к нам идёт основной трафик) кастомные схемы часто
// глохнут молча — тап в никуда. Поэтому обычные https-ссылки: на телефоне они
// сами предлагают открыть приложение, на компьютере открывают сайт.
//
// Первая точка маршрута везде пустая — это и значит «от моего местоположения»,
// геопозицию спрашивают уже сами карты.

import { useState } from "react";
import { DetailModal } from "../../components/DetailModal";

type LocationRouteButtonProps = {
  latitude: number;
  longitude: number;
};

type RouteService = {
  id: string;
  title: string;
  /** Знак вместо логотипа: чужие лого тащить не хочется, а буква узнаётся. */
  mark: string;
  color: string;
  href: (latitude: number, longitude: number) => string;
};

/** Координаты в ссылке — фиксированной точности: иначе float даёт хвост вида 55.7000000000001. */
function coord(value: number): string {
  return value.toFixed(6);
}

const SERVICES: RouteService[] = [
  {
    id: "yandex",
    title: "Яндекс Карты",
    mark: "Я",
    color: "#fc3f1d",
    // `~` на месте точки А — «от моего местоположения». Тип маршрута не
    // навязываем: кто-то поедет на машине, кто-то на электричке, и Карты
    // откроются с тем способом, которым человек пользуется обычно.
    href: (lat, lon) => `https://yandex.ru/maps/?rtext=~${coord(lat)},${coord(lon)}`,
  },
  {
    id: "2gis",
    title: "2ГИС",
    mark: "2",
    color: "#19aa1e",
    // У 2ГИС координаты наоборот (долгота первой), а пустое место перед `|` —
    // та самая точка А «где я сейчас».
    href: (lat, lon) => `https://2gis.ru/directions/points/|${coord(lon)},${coord(lat)}`,
  },
  {
    id: "google",
    title: "Google Карты",
    mark: "G",
    color: "#4285f4",
    // Параметр origin опущен намеренно — Google подставляет текущее место сам.
    href: (lat, lon) => `https://www.google.com/maps/dir/?api=1&destination=${coord(lat)},${coord(lon)}`,
  },
];

export function LocationRouteButton({ latitude, longitude }: LocationRouteButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" className="loc-quick-link loc-route-quick" onClick={() => setOpen(true)}>
        <span aria-hidden="true">🧭</span>
        <span className="loc-quick-full">Проложить маршрут</span>
        <span className="loc-quick-short">Маршрут</span>
      </button>
      <DetailModal open={open} title="Проложить маршрут" width="narrow" onClose={() => setOpen(false)}>
        <p className="muted loc-route-hint">
          Маршрут построится от вашего текущего местоположения — карты спросят геопозицию сами.
        </p>
        <div className="loc-route-options">
          {SERVICES.map((service) => (
            <a
              key={service.id}
              className="loc-route-option"
              href={service.href(latitude, longitude)}
              target="_blank"
              rel="noreferrer"
              onClick={() => setOpen(false)}
            >
              <span className="loc-route-option-mark" style={{ background: service.color }} aria-hidden>
                {service.mark}
              </span>
              <span className="loc-route-option-name">{service.title}</span>
              <span className="loc-route-option-go" aria-hidden>
                →
              </span>
            </a>
          ))}
        </div>
      </DetailModal>
    </>
  );
}
