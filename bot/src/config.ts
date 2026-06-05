import "dotenv/config";

function required(name: string): string {
  const v = (process.env[name] ?? "").trim();
  if (!v) throw new Error(`[bot] 缺少必要環境變數 ${name}`);
  return v;
}

// token 不在這裡讀:config 物件載入無副作用,任何 import 鏈(data.ts/index.ts/測試)都能安全載入。
// token 改由 startup(client.login)呼叫 requireToken() 取得;缺則 throw(loud,不靜默 process.exit)。
export const config = {
  backendBaseUrl: (process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8000").trim(),
  bffApiKey: (process.env.BFF_API_KEY ?? "").trim(),
  allowedChannels: (process.env.BOT_ALLOWED_CHANNELS ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean),
};

export function requireToken(): string {
  return required("DISCORD_BOT_TOKEN");
}
