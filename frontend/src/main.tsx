import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installNavigationMemory } from "./lib/navigationMemory";
import "./index.css";

// Ставится до первого рендера: обёртки над history и обработчик popstate
// должны стоять раньше роутера (см. lib/navigationMemory).
installNavigationMemory();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
