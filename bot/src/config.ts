import "dotenv/config";

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
