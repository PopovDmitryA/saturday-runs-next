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
      // changeOrigin: с пустой локальной базой удобно смотреть вёрстку на живых
      // данных (API_PROXY_TARGET=https://run5k.run), а туда нужен свой Host —
      // иначе на сервере не сматчится vhost. Для localhost-цели безвредно.
      "/api": { target: apiTarget, changeOrigin: true },
      "/health": { target: apiTarget, changeOrigin: true },
    },
  },
});
