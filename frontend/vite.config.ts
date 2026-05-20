import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// proxy 必須對 relative path 生效
// 前端 fetch 一律寫 fetch('/api/...') 不要寫 fetch('http://localhost:8000/api/...')
//
// configure: 接住 ECONNREFUSED/ECONNRESET/EPIPE/ECONNABORTED — 這是 backend 還沒
// 起來或正在重啟時的正常雜訊,不靜音的話每次重啟就會印一坨 stack trace。
// 其他 proxy 錯誤照舊印出來。
const SILENT_PROXY_ERRORS = new Set([
  "ECONNREFUSED", "ECONNRESET", "EPIPE", "ECONNABORTED",
]);

// 各 proxy 各自記錄上次是不是斷線狀態,讓「重連成功」訊息只在「斷線 → 重連」
// 轉移時印一次,平時 traffic 不噴 log。
function silentProxy(label: string, isWS = false) {
  let down = false;
  return (proxy: any) => {
    proxy.on("error", (err: Error & { code?: string }) => {
      const code = err.code;
      if (code && SILENT_PROXY_ERRORS.has(code)) {
        down = true;  // 標記斷線,下次成功 emit 時印 reconnected
        return;
      }
      console.error(`[vite proxy ${label}]`, err);
    });
    const onSuccess = () => {
      if (down) {
        console.log(`[vite proxy ${label}] backend reconnected`);
        down = false;
      }
    };
    // HTTP proxy 用 proxyRes(每個成功 response 觸發);WS proxy 用 open(upstream WS 連上)
    proxy.on(isWS ? "open" : "proxyRes", onSuccess);
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
        configure: silentProxy("/ws", true),
      },
    },
  },
});
