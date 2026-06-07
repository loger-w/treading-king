import type { BaseMessageOptions } from "discord.js";

export interface TouchMeta {
  level: string;
  direction?: string;
  role?: string;
  touch_index?: number;
}

export interface SignalPayload {
  symbol: string;
  rule_name: string;
  price: number;
  volume: number;
  triggered_at: string;   // UTC ISO(後端 datetime.isoformat())
  cdp_touch?: TouchMeta | null;
  ma_touch?: TouchMeta | null;
}

function parseTouch(t: unknown): TouchMeta | null {
  if (typeof t !== "object" || t === null) return null;
  const o = t as Record<string, unknown>;
  if (typeof o.level !== "string") return null;
  return {
    level: o.level,
    direction: typeof o.direction === "string" ? o.direction : undefined,
    role: typeof o.role === "string" ? o.role : undefined,
    touch_index: typeof o.touch_index === "number" ? o.touch_index : undefined,
  };
}

// 後端可能送任意 body → 嚴格驗必填,壞的回 null(由 push-server 回 400)。
export function parseSignalPayload(raw: unknown): SignalPayload | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.symbol !== "string" || !r.symbol) return null;
  if (typeof r.rule_name !== "string" || !r.rule_name) return null;
  if (typeof r.price !== "number") return null;
  if (typeof r.volume !== "number") return null;
  if (typeof r.triggered_at !== "string") return null;
  if (Number.isNaN(new Date(r.triggered_at).getTime())) return null;  // 非法日期字串擋掉,避免 formatBanner 的 Date 解析丟 RangeError
  return {
    symbol: r.symbol,
    rule_name: r.rule_name,
    price: r.price,
    volume: r.volume,
    triggered_at: r.triggered_at,
    cdp_touch: parseTouch(r.cdp_touch),
    ma_touch: parseTouch(r.ma_touch),
  };
}

const ROLE_ZH: Record<string, string> = { support: "支撐", resistance: "壓力", touch: "觸碰" };
const MA_LABEL: Record<string, string> = { sma_5: "MA5", sma_20: "MA20" };

// CDP 線一律大寫顯示(AH/NH/CDP/NL/AL);MA 內部欄位 sma_5/sma_20 → MA5/MA20
function levelLabel(kind: "CDP" | "MA", level: string): string {
  return kind === "MA" ? MA_LABEL[level] ?? level.toUpperCase() : level.toUpperCase();
}

function touchLine(kind: "CDP" | "MA", t: TouchMeta): string {
  // role / touch_index 各自可有可無:用 parts 組,避免缺 role 時出現孤兒分隔符「（·第N次）」
  const parts: string[] = [];
  if (t.role) parts.push(ROLE_ZH[t.role] ?? t.role);
  if (t.touch_index != null && t.touch_index > 0) parts.push(`第${t.touch_index}次`);
  const meta = parts.length ? `（${parts.join("·")}）` : "";
  return `碰 ${kind} ${levelLabel(kind, t.level)}${meta}`;
}

// UTC ISO → 台北 HH:mm:ss(用 formatToParts + h23,避免 locale/午夜 24:00 邊界問題)
function taipeiTime(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${get("hour")}:${get("minute")}:${get("second")}`;
}

export function formatBanner(p: SignalPayload): string {
  const lines = [`🔔 **${p.rule_name}** 觸發 ｜ 觸發價 ${p.price} ｜ ${taipeiTime(p.triggered_at)}`];
  if (p.cdp_touch) lines.push(touchLine("CDP", p.cdp_touch));
  if (p.ma_touch) lines.push(touchLine("MA", p.ma_touch));
  return lines.join("\n");
}

// 把橫幅疊在第一則上方:embed 那則加 content;本身有 content(空盤前)則換行接原文。
export function withBanner(first: BaseMessageOptions, banner: string): BaseMessageOptions {
  const existing = typeof first.content === "string" && first.content ? "\n" + first.content : "";
  return { ...first, content: banner + existing };
}

// orchestration:頻道沒設 → 略過;否則取得 symbol 的所有訊息、注入橫幅、依序送。
// 外部相依(產訊息 / 送頻道 / 頻道是否就緒)抽成 deps,單元可注入。
export interface PushDeps {
  channelConfigured: boolean;
  buildSymbolMessages: (symbol: string) => Promise<BaseMessageOptions[]>;
  sendToChannel: (msg: BaseMessageOptions) => Promise<void>;
}

export async function handleSignalPush(p: SignalPayload, deps: PushDeps): Promise<void> {
  if (!deps.channelConfigured) {
    console.warn(`[bot] 訊號頻道未設定(SIGNALS_DISCORD_CHANNEL_ID),略過:${p.symbol} / ${p.rule_name}`);
    return;
  }
  const messages = await deps.buildSymbolMessages(p.symbol);
  if (messages.length === 0) return;
  messages[0] = withBanner(messages[0], formatBanner(p));
  for (const m of messages) await deps.sendToChannel(m);
}
