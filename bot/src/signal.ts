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
  name?: string | null;
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
    name: typeof r.name === "string" ? r.name : undefined,
    price: r.price,
    volume: r.volume,
    triggered_at: r.triggered_at,
    cdp_touch: parseTouch(r.cdp_touch),
    ma_touch: parseTouch(r.ma_touch),
  };
}

const ROLE_ZH: Record<string, string> = { support: "支撐", resistance: "壓力", touch: "觸碰", distribution: "做頭轉弱" };
const MA_LABEL: Record<string, string> = { sma_5: "MA5", sma_20: "MA20" };

// CDP 線大寫顯示(AH/NH/NL/AL);中軸線代號本身就是 cdp,大寫會變「碰 CDP CDP」撞字 → 顯示「中軸」。
// MA 內部欄位 sma_5/sma_20 → MA5/MA20。
function levelLabel(kind: "CDP" | "MA", level: string): string {
  if (kind === "MA") return MA_LABEL[level] ?? level.toUpperCase();
  return level.toLowerCase() === "cdp" ? "中軸" : level.toUpperCase();
}

function touchLine(kind: "CDP" | "MA", t: TouchMeta): string {
  // 雙峰造山「做頭轉弱」不是碰線事件,獨立文案(不走「碰 CDP <LEVEL>」)
  if (t.level === "peak") {
    return `📉 ${t.role ? (ROLE_ZH[t.role] ?? t.role) : "做頭轉弱"}`;
  }
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
  const target = p.name ? `${p.name} ${p.symbol}` : p.symbol;
  const lines = [`🔔 **${p.rule_name}** 觸發 ｜ ${target} ｜ 觸發價 ${p.price} ｜ ${taipeiTime(p.triggered_at)}`];
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
    console.warn(`[bot] 訊號頻道不可用(未設 SIGNALS_DISCORD_CHANNEL_ID 或抓不到),略過:${p.symbol} / ${p.rule_name}`);
    return;
  }
  let messages: BaseMessageOptions[];
  try {
    messages = await deps.buildSymbolMessages(p.symbol);
  } catch (e) {
    // 後端抓不到(重啟/斷線)→ 仍用 payload 送純文字橫幅,別讓訊號完全消失
    console.warn(`[bot] ${p.symbol} 圖卡資料抓取失敗,退純文字橫幅:`, e);
    messages = [{ content: "（圖卡資料暫時無法取得）" }];
  }
  if (messages.length === 0) return;
  messages[0] = withBanner(messages[0], formatBanner(p));
  for (const m of messages) await deps.sendToChannel(m);
}
