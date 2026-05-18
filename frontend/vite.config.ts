import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// proxy 必須對 relative path 生效
// 前端 fetch 一律寫 fetch('/api/...') 不要寫 fetch('http://localhost:8000/api/...')
//
// configure: 接住 ECONNREFUSED/ECONNRESET — 這是 backend 還沒起來時的正常雜訊，
// 不靜音的話每次 frontend 比 backend 早啟動就會印一坨 stack trace。其他 proxy
// 錯誤照舊印出來。
const SILENT_PROXY_ERRORS = new Set(["ECONNREFUSED", "ECONNRESET", "EPIPE"]);

function silentProxy(label: string) {
  // vite 給的 proxy 是 http-proxy Server instance（沒 export type,用 any 接）
  return (proxy: any) => {
    proxy.on("error", (err: Error & { code?: string }) => {
      const code = err.code;
      if (code && SILENT_PROXY_ERRORS.has(code)) return;
      console.error(`[vite proxy ${label}]`, err);
    });
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure: silentProxy("/api"),
      },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
        configure: silentProxy("/ws"),
      },
    },
  },
});
