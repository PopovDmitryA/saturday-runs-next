import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installNavigationMemory } from "./lib/navigationMemory";
// Стили leaflet — здесь, а не в ленивых чанках карт: index.css переопределяет
// .leaflet-container (подложка под тайлами) и должен идти ПОСЛЕ них, а CSS
// ленивого чанка подключается позже всего и перебил бы это переопределение.
import "leaflet/dist/leaflet.css";
import "./index.css";

// Ставится до первого рендера: обёртки над history и обработчик popstate
// должны стоять раньше роутера (см. lib/navigationMemory).
installNavigationMemory();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
