import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_PROXY_TARGET позволяет направить dev-прокси на нестандартный порт
// (например, API из git-worktree на :8001 рядом с основным стеком на :8000).
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

// Вендоры — отдельными чанками с собственным хэшем: релиз меняет только код
// сайта, а react/leaflet/html-to-image/qrcode остаются в кэше браузера между
// деплоями (они весят больше половины сборки и меняются раз в месяцы).
// Разделы сайта режутся сами по React.lazy (см. src/lib/lazyPage.tsx).
const VENDOR_CHUNKS: Array<[RegExp, string]> = [
  [/node_modules\/(react|react-dom|scheduler)\//, "vendor-react"],
  [/node_modules\/leaflet\//, "vendor-leaflet"],
  [/node_modules\/html-to-image\//, "vendor-html-to-image"],
  [/node_modules\/(qrcode|dijkstrajs|pngjs)\//, "vendor-qrcode"],
];

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          // CSS вендора (leaflet.css) остаётся в общем стиле сайта — см. main.tsx;
          // иначе стартовый чанк статически потянул бы за ним весь leaflet.
          if (id.endsWith(".css")) {
            return undefined;
          }
          for (const [pattern, name] of VENDOR_CHUNKS) {
            if (pattern.test(id)) {
              return name;
            }
          }
          return undefined;
        },
      },
    },
  },
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
