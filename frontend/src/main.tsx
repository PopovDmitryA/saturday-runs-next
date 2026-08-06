import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initMetrika } from "./lib/metrika";
import "./index.css";

initMetrika();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
