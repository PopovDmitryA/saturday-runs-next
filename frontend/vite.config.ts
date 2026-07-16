import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_PROXY_TARGET позволяет направить dev-прокси на нестандартный порт
// (например, API из git-worktree на :8001 рядом с основным стеком на :8000).
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/health": apiTarget,
    },
  },
});
