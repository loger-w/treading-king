import "dotenv/config";

// 注意:本檔在 import 時就驗證並可能 process.exit(1)。任何 import 鏈會拉到 config 的測試
// (例如 import data.ts)在沒設 DISCORD_BOT_TOKEN 的環境會直接終止進程、且無 vitest 錯誤訊息。
function required(name: string): string {
  const v = (process.env[name] ?? "").trim();
  if (!v) { console.error(`[bot] 缺少必要環境變數 ${name}`); process.exit(1); }
  return v;
}

export const config = {
  token: required("DISCORD_BOT_TOKEN"),
  backendBaseUrl: (process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8000").trim(),
  bffApiKey: (process.env.BFF_API_KEY ?? "").trim(),
  allowedChannels: (process.env.BOT_ALLOWED_CHANNELS ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean),
};
