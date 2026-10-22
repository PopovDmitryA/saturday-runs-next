import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installNavigationMemory } from "./lib/navigationMemory";
// Стили leaflet — здесь, а не в ленивых чанках карт: index.css переопределяет
// .leaflet-container (подложка под тайлами) и должен идти ПОСЛЕ них, а CSS
// ленивого чанка подключается позже всего и перебил бы это переопределение.
import "leaflet/dist/leaflet.css";
import "./index.css";

// Адрес с двойным слэшем (run5k.run//locations/x — так склеивается ссылка из
// чата с адресом сайта) страница открывала, но запись истории от него падала:
// путь «//locations/x» браузер читает как адрес на чужом хосте, и
// replaceState/pushState, собранные из pathname, бросали ошибку — и страница,
// правящая адрес после загрузки или при закрытии окна, ломалась на полпути
// (ревью 22.10.2026). Склеиваем слэши один раз, до роутера.
if (/\/{2,}/.test(window.location.pathname)) {
  const url = new URL(window.location.href);
  url.pathname = url.pathname.replace(/\/{2,}/g, "/");
  window.history.replaceState(window.history.state, "", url.href);
}

// Ставится до первого рендера: обёртки над history и обработчик popstate
// должны стоять раньше роутера (см. lib/navigationMemory).
installNavigationMemory();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
