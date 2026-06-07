# 訊號觸發走 Discord bot 推完整圖卡 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 規則在後端觸發、且 `notify_discord=true` 時,Node bot 在指定頻道推出跟 `p代號` 同款三則圖卡(文字卡 + 分時圖 + 五檔圖)+ 觸發橫幅。

**Architecture:** 純 push、localhost IPC。後端 `discord_notifier.send_signal()` 簽名不變,body 從「打 webhook」改成 POST 給 bot 的 `127.0.0.1:8787/push-signal`;`signal_engine.py` 零改動。bot 新增只聽 localhost 的 HTTP 入口,立刻回 202 後背景重用既有渲染管線(`loadSlow`/`composeReply`)產三則,把橫幅疊在第一則,送到 `SIGNALS_DISCORD_CHANNEL_ID`。

**Tech Stack:** Python(FastAPI / httpx / pytest)、Node(discord.js v14 / 內建 http / resvg / vitest / tsx)。

設計來源:`docs/superpowers/specs/2026-06-08-signal-push-via-bot-design.md`

---

## 檔案結構

| 動作 | 檔案 | 責任 |
|---|---|---|
| 改 | `backend/services/discord_notifier.py` | `send_signal` body 改 POST 給 bot(簽名不變) |
| 改 | `backend/.env.example` | `SIGNALS_DISCORD_WEBHOOK_URL` → `SIGNALS_BOT_PUSH_URL` |
| 改 | `backend/tests/test_discord_notifier.py` | 測 bot-POST 行為(取代 webhook 測試) |
| 建 | `bot/src/signal.ts` | `SignalPayload` 型別 / `parseSignalPayload` / `formatBanner` / `withBanner` / `handleSignalPush` |
| 建 | `bot/src/signal.test.ts` | 上述純函式 + handler 單元測試 |
| 建 | `bot/src/messages.ts` | `buildSymbolMessages(symbol)` + 共用 `slow` 30s 快取(從 index.ts 抽出) |
| 建 | `bot/src/push-server.ts` | 只聽 127.0.0.1 的 HTTP 殼:路由 / parse / 回 202 / 轉 dispatch |
| 建 | `bot/src/push-server.test.ts` | HTTP 殼狀態碼 + 202 + onSignal 觸發 |
| 改 | `bot/src/config.ts` | 加 `signalsChannelId`、`pushPort` |
| 改 | `bot/src/index.ts` | `handle()` 改用 `buildSymbolMessages`;`ClientReady` 起 push-server |
| 改 | `bot/.env.example` | 加 `SIGNALS_DISCORD_CHANNEL_ID`、`BOT_PUSH_PORT` |

`signal_engine.py`、`reply.ts`、`render.ts`、`embed.ts`、`start.ps1`、`alerts.py`(系統異常 webhook,另一回事)**都不動**。

**指令前置:** 後端指令在 `backend/` 下跑(venv 在 `backend/.venv`);bot 指令在 `bot/` 下跑。

---

## Task 1: 後端 `discord_notifier` 改 POST 給 bot

**Files:**
- Modify: `backend/services/discord_notifier.py`(整檔重寫)
- Modify: `backend/tests/test_discord_notifier.py`(整檔重寫)
- Modify: `backend/.env.example:24-25`

- [ ] **Step 1: 重寫測試(失敗先行)**

把 `backend/tests/test_discord_notifier.py` 整檔換成:

```python
"""Discord notifier — 訊號觸發 POST 給 bot(失敗 silent log)。"""
from unittest.mock import AsyncMock, patch

import pytest

from services import discord_notifier


@pytest.fixture(autouse=True)
def _reset_cached_url():
    """每個 test reset module-level cache,避免測試間互相污染。"""
    discord_notifier._PUSH_URL = None
    yield
    discord_notifier._PUSH_URL = None


@pytest.mark.asyncio
async def test_send_signal_noop_when_push_url_unset(monkeypatch):
    monkeypatch.delenv("SIGNALS_BOT_PUSH_URL", raising=False)
    with patch("services.discord_notifier.httpx.AsyncClient") as mock_client:
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_send_signal_posts_to_bot_when_url_set(monkeypatch):
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock()
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="漲停打開碰CDP",
            symbol="2330",
            price=600.0,
            volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
            cdp_touch={"level": "AH", "direction": "from_above", "role": "support", "touch_index": 2},
            ma_touch=None,
        )
        fake_client.post.assert_called_once()
        call = fake_client.post.call_args
        assert call.args[0] == "http://127.0.0.1:8787/push-signal"
        body = call.kwargs["json"]
        assert body["symbol"] == "2330"
        assert body["rule_name"] == "漲停打開碰CDP"
        assert body["price"] == 600.0
        assert body["volume"] == 10
        assert body["triggered_at"] == "2026-06-08T05:30:00+00:00"
        assert body["cdp_touch"]["level"] == "AH"
        assert body["ma_touch"] is None


@pytest.mark.asyncio
async def test_send_signal_swallows_errors(monkeypatch, caplog):
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock(side_effect=Exception("connection refused"))
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        # 不該 raise
        await discord_notifier.send_signal(
            rule_name="t", symbol="2330", price=600.0, volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
    assert "Discord signal push failed" in caplog.text
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`.\.venv\Scripts\python.exe -m pytest tests/test_discord_notifier.py -v`
Expected:`test_send_signal_posts_to_bot_when_url_set` FAIL(舊 impl 讀 `SIGNALS_DISCORD_WEBHOOK_URL`、沒 POST 到 bot URL)。

- [ ] **Step 3: 重寫 `discord_notifier.py`**

把 `backend/services/discord_notifier.py` 整檔換成:

```python
"""Discord notifier — 訊號觸發 → POST 給 bot 的 localhost 入口(bot 端渲三則圖卡)。

跟 alerts.py 的系統異常 webhook 是兩條獨立路徑。
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_PUSH_URL: str | None = None


def _get_push_url() -> str | None:
    global _PUSH_URL
    if _PUSH_URL is None:
        _PUSH_URL = os.getenv("SIGNALS_BOT_PUSH_URL", "").strip() or ""
    return _PUSH_URL or None


async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
    """訊號觸發 → POST 給 bot;URL 未設則 no-op。失敗 silent log,不影響主流程。"""
    url = _get_push_url()
    if not url:
        return
    payload = {
        "symbol": symbol,
        "rule_name": rule_name,
        "price": price,
        "volume": volume,
        "triggered_at": triggered_at_iso,
        "cdp_touch": cdp_touch,
        "ma_touch": ma_touch,
    }
    try:
        # 3s timeout:bot 立刻回 202,渲圖走 bot 背景;localhost 連不上(bot 沒開)會很快失敗
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning("Discord signal push failed: %s", e)
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `backend/`):`.\.venv\Scripts\python.exe -m pytest tests/test_discord_notifier.py -v`
Expected:3 passed。

- [ ] **Step 5: 改 `backend/.env.example`**

把第 24–25 行:

```
# 訊號觸發 Discord webhook（跟 ALERTS_DISCORD_WEBHOOK_URL 分開,可同 URL 也可分流）
SIGNALS_DISCORD_WEBHOOK_URL=
```

換成:

```
# 訊號觸發 → POST 給 bot 的 localhost 入口（bot 渲三則圖卡推 Discord;空 = 不推）
SIGNALS_BOT_PUSH_URL=http://127.0.0.1:8787/push-signal
```

(第 22 行 `ALERTS_DISCORD_WEBHOOK_URL` 不要動。)

- [ ] **Step 6: 確認 signal_engine 未受影響並 commit**

Run(在 `backend/`):`.\.venv\Scripts\python.exe -m pytest tests/ -q`
Expected:全綠(`send_signal` 簽名不變,signal_engine 相關測試照過)。

```bash
git add backend/services/discord_notifier.py backend/tests/test_discord_notifier.py backend/.env.example
git commit -m "feat(notify): 訊號 discord 通知改 POST 給 bot(取代純文字 webhook)"
```

---

## Task 2: bot `signal.ts` — 型別 / 解析 / 橫幅 / handler

**Files:**
- Create: `bot/src/signal.ts`
- Test: `bot/src/signal.test.ts`

- [ ] **Step 1: 寫測試(失敗先行)**

建 `bot/src/signal.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  parseSignalPayload, formatBanner, withBanner, handleSignalPush,
  type SignalPayload, type PushDeps,
} from "./signal";
import type { BaseMessageOptions } from "discord.js";

const base: SignalPayload = {
  symbol: "2330", rule_name: "漲停打開碰CDP", price: 600.5, volume: 1234,
  triggered_at: "2026-06-08T05:30:00+00:00",
  cdp_touch: { level: "AH", role: "support", touch_index: 2 },
  ma_touch: null,
};

describe("parseSignalPayload", () => {
  it("完整 payload → 物件", () => {
    const p = parseSignalPayload({ ...base });
    expect(p).not.toBeNull();
    expect(p!.symbol).toBe("2330");
    expect(p!.cdp_touch?.level).toBe("AH");
  });
  it("缺必填(無 symbol)→ null", () => {
    expect(parseSignalPayload({ ...base, symbol: undefined })).toBeNull();
  });
  it("price 非數字 → null", () => {
    expect(parseSignalPayload({ ...base, price: "600" })).toBeNull();
  });
  it("非物件 → null", () => {
    expect(parseSignalPayload("nope")).toBeNull();
    expect(parseSignalPayload(null)).toBeNull();
  });
  it("touch 殘缺(無 level)→ 該 touch 視為 null", () => {
    const p = parseSignalPayload({ ...base, cdp_touch: { role: "support" } });
    expect(p!.cdp_touch).toBeNull();
  });
});

describe("formatBanner", () => {
  it("含 cdp_touch:規則名/觸發價/台北時間/碰CDP行", () => {
    const b = formatBanner(base);
    expect(b).toContain("漲停打開碰CDP");
    expect(b).toContain("600.5");
    expect(b).toMatch(/13:30:00/);            // 05:30 UTC → 13:30 台北(+08:00)
    expect(b).toContain("碰 CDP AH");
    expect(b).toContain("支撐");
    expect(b).toContain("第2次");
  });
  it("ma_touch 的 sma_5 → 顯示 MA5", () => {
    const b = formatBanner({ ...base, cdp_touch: null, ma_touch: { level: "sma_5", role: "resistance" } });
    expect(b).toContain("碰 MA MA5");
    expect(b).toContain("壓力");
  });
  it("無 cdp/ma → 只有標題一行", () => {
    const b = formatBanner({ ...base, cdp_touch: null, ma_touch: null });
    expect(b.split("\n")).toHaveLength(1);
  });
});

describe("withBanner", () => {
  it("第一則只有 embeds → 加 content(橫幅)", () => {
    const m: BaseMessageOptions = { embeds: [] };
    const out = withBanner(m, "BANNER");
    expect(out.content).toBe("BANNER");
    expect(out.embeds).toBe(m.embeds);
  });
  it("第一則本身有 content → 橫幅疊上方、換行接原文", () => {
    const out = withBanner({ content: "原文" }, "BANNER");
    expect(out.content).toBe("BANNER\n原文");
  });
});

describe("handleSignalPush", () => {
  const msgs: BaseMessageOptions[] = [{ embeds: [] }, { files: [] }];
  function makeDeps(over: Partial<PushDeps> = {}) {
    const sent: BaseMessageOptions[] = [];
    const deps: PushDeps = {
      channelConfigured: true,
      buildSymbolMessages: async () => msgs.map((m) => ({ ...m })),
      sendToChannel: async (m) => { sent.push(m); },
      ...over,
    };
    return { deps, sent };
  }
  it("頻道未設 → 不送", async () => {
    const { deps, sent } = makeDeps({ channelConfigured: false });
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(0);
  });
  it("頻道有設 → 送全部,第一則帶橫幅", async () => {
    const { deps, sent } = makeDeps();
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(2);
    expect(sent[0].content).toContain("漲停打開碰CDP");
  });
  it("buildSymbolMessages 回空 → 不送", async () => {
    const { deps, sent } = makeDeps({ buildSymbolMessages: async () => [] });
    await handleSignalPush(base, deps);
    expect(sent).toHaveLength(0);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `bot/`):`npx vitest run src/signal.test.ts`
Expected:FAIL(`Cannot find module './signal'`)。

- [ ] **Step 3: 實作 `bot/src/signal.ts`**

```ts
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
  const role = t.role ? ROLE_ZH[t.role] ?? t.role : "";
  const idx = t.touch_index ? `·第${t.touch_index}次` : "";
  const meta = role || idx ? `（${role}${idx}）` : "";
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

// orchestration:頻道沒設 → 略過;否則建三則、注入橫幅、依序送。
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
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `bot/`):`npx vitest run src/signal.test.ts`
Expected:全部 pass。若 `13:30:00` 斷言失敗 → 確認 Node 版本含完整 ICU(Node 18+ 預設有)。

- [ ] **Step 5: 型別檢查 + commit**

Run(在 `bot/`):`npx tsc --noEmit`
Expected:0 errors。

```bash
git add bot/src/signal.ts bot/src/signal.test.ts
git commit -m "feat(bot): 訊號 payload 解析 + 觸發橫幅 + push handler(signal.ts)"
```

---

## Task 3: bot `messages.ts` 抽出 `buildSymbolMessages` + `index.ts` 重構

這是純抽取(行為不變),由既有 `reply.test.ts` + tsc 守門。

**Files:**
- Create: `bot/src/messages.ts`
- Modify: `bot/src/index.ts:1-37`(imports + `handle()` + 移除 module 級 `slow`)

- [ ] **Step 1: 建 `bot/src/messages.ts`**

```ts
import type { BaseMessageOptions } from "discord.js";
import { getQuote } from "./data";
import { renderQuotePng, safeRender } from "./render";
import { TtlCache } from "./cache";
import { loadSlow, composeReply, type SlowResult } from "./reply";

// 慢資料(分時 K / CDP / MA / 分時圖 PNG)30s 快取;五檔 quote + quotePng 每次即時抓。
// 訊息處理器(p代號)與訊號 push 共用同一份快取,連續查同檔可重用圖。
const slow = new TtlCache<SlowResult>(30_000);

export async function buildSymbolMessages(symbol: string): Promise<BaseMessageOptions[]> {
  const [s, quote] = await Promise.all([
    slow.get(symbol, () => loadSlow(symbol)),
    getQuote(symbol).catch(() => null),  // 五檔失敗 → null,不拖垮已備好的圖/CDP/MA
  ]);
  // 五檔圖必須跟即時 quote 同產(quote 不走快取);產圖失敗 → null,只少一則
  const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
  return composeReply(symbol, s, quote, quotePng);
}
```

- [ ] **Step 2: 重構 `bot/src/index.ts`**

把第 1–37 行(imports + 開頭 `slow` + `handle()`)換成:

```ts
import { Client, GatewayIntentBits, Events, type Message, type BaseMessageOptions } from "discord.js";
import { config, requireToken } from "./config";
import { parseSymbolCommand } from "./symbol";
import { buildSymbolMessages } from "./messages";

async function handle(msg: Message, symbol: string) {
  let messages: BaseMessageOptions[];
  try {
    messages = await buildSymbolMessages(symbol);
  } catch (e) {
    msg.reply(`\`${symbol}\` 查詢失敗(行情暫時不可用)。`).catch(console.error);
    console.warn(`[bot] ${symbol} 失敗:`, e);
    return;
  }
  // 拆多則:第一則回覆原訊息(帶 tag),其餘同頻道送出 — 圖各自獨立一則才會放大、又不重複 tag。
  try {
    await msg.reply(messages[0]);
    for (let i = 1; i < messages.length; i++) {
      if ("send" in msg.channel) await msg.channel.send(messages[i]);
    }
  } catch (e) {
    console.warn(`[bot] ${symbol} 送出部分訊息失敗:`, e);
  }
}
```

(第 39 行之後的 `const client = new Client(...)`、`ClientReady`、`MessageCreate`、`client.login(...)` **本步驟不動**,Task 5 才改 `ClientReady`。)

- [ ] **Step 3: 型別檢查**

Run(在 `bot/`):`npx tsc --noEmit`
Expected:0 errors(若報「未使用的 import」表示舊 import 沒清乾淨 → 移除 `getQuote`/`render`/`cache`/`reply` 等已搬走的)。

- [ ] **Step 4: 跑全部 bot 測試確認行為不變**

Run(在 `bot/`):`npx vitest run`
Expected:全綠,數量 = 原本 + Task 2 新增的 signal 測試;`reply.test.ts` 不變。

- [ ] **Step 5: Commit**

```bash
git add bot/src/messages.ts bot/src/index.ts
git commit -m "refactor(bot): 抽出 buildSymbolMessages 供訊息處理器與訊號 push 共用"
```

---

## Task 4: bot `config.ts` + `push-server.ts`(HTTP 殼)

**Files:**
- Modify: `bot/src/config.ts:11-16`
- Create: `bot/src/push-server.ts`
- Test: `bot/src/push-server.test.ts`

- [ ] **Step 1: 加 config 欄位**

把 `bot/src/config.ts` 的 `export const config = {...}` 區塊換成:

```ts
export const config = {
  backendBaseUrl: (process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8000").trim(),
  bffApiKey: (process.env.BFF_API_KEY ?? "").trim(),
  allowedChannels: (process.env.BOT_ALLOWED_CHANNELS ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean),
  // 訊號 push:目標頻道(空 = 不推)、push-server 埠
  signalsChannelId: (process.env.SIGNALS_DISCORD_CHANNEL_ID ?? "").trim(),
  pushPort: Number(process.env.BOT_PUSH_PORT ?? "8787") || 8787,
};
```

- [ ] **Step 2: 寫 push-server 測試(失敗先行)**

建 `bot/src/push-server.test.ts`:

```ts
import { describe, it, expect, afterEach } from "vitest";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { createPushServer } from "./push-server";
import type { SignalPayload } from "./signal";

let server: Server | null = null;
afterEach(() => { server?.close(); server = null; });

async function start(onSignal: (p: SignalPayload) => void): Promise<number> {
  server = createPushServer({ onSignal });
  await new Promise<void>((r) => server!.listen(0, "127.0.0.1", () => r()));
  return (server!.address() as AddressInfo).port;
}

const VALID = { symbol: "2330", rule_name: "r", price: 1, volume: 1, triggered_at: "2026-06-08T05:30:00+00:00" };
const json = { "content-type": "application/json" };

describe("push-server HTTP 殼", () => {
  it("非 POST → 405", async () => {
    const port = await start(() => {});
    expect((await fetch(`http://127.0.0.1:${port}/push-signal`)).status).toBe(405);
  });
  it("路徑錯 → 404", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/nope`, { method: "POST", headers: json, body: "{}" });
    expect(res.status).toBe(404);
  });
  it("非 JSON content-type → 415", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: { "content-type": "text/plain" }, body: "{}" });
    expect(res.status).toBe(415);
  });
  it("壞 JSON → 400", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: "{bad" });
    expect(res.status).toBe(400);
  });
  it("payload 缺欄位 → 400", async () => {
    const port = await start(() => {});
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: JSON.stringify({ symbol: "2330" }) });
    expect(res.status).toBe(400);
  });
  it("合法 → 202 且 onSignal 收到 payload", async () => {
    const got: SignalPayload[] = [];
    const port = await start((p) => got.push(p));
    const res = await fetch(`http://127.0.0.1:${port}/push-signal`, { method: "POST", headers: json, body: JSON.stringify(VALID) });
    expect(res.status).toBe(202);
    expect(got).toHaveLength(1);
    expect(got[0].symbol).toBe("2330");
  });
});
```

- [ ] **Step 3: 跑測試確認失敗**

Run(在 `bot/`):`npx vitest run src/push-server.test.ts`
Expected:FAIL(`Cannot find module './push-server'`)。

- [ ] **Step 4: 實作 `bot/src/push-server.ts`**

```ts
import { createServer, type IncomingMessage, type ServerResponse, type Server } from "node:http";
import type { Client, BaseMessageOptions } from "discord.js";
import { config } from "./config";
import { parseSignalPayload, handleSignalPush, type SignalPayload } from "./signal";
import { buildSymbolMessages } from "./messages";

export interface PushServerHandlers {
  onSignal: (payload: SignalPayload) => void;   // fire-and-forget,在回 202 之後
}

// HTTP 殼:純路由 + parse + 回 202 + 把 payload 丟給 onSignal。不碰 discord client → 好測。
export function createPushServer(handlers: PushServerHandlers): Server {
  return createServer((req, res) => { void route(req, res, handlers); });
}

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > 64 * 1024) { reject(new Error("body too large")); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { reject(new Error("invalid json")); }
    });
    req.on("error", reject);
  });
}

async function route(req: IncomingMessage, res: ServerResponse, handlers: PushServerHandlers): Promise<void> {
  if (req.method !== "POST") { res.writeHead(405).end(); return; }
  if (req.url !== "/push-signal") { res.writeHead(404).end(); return; }
  if (!String(req.headers["content-type"] ?? "").includes("application/json")) { res.writeHead(415).end(); return; }
  let body: unknown;
  try { body = await readJsonBody(req); } catch { res.writeHead(400).end(); return; }
  const payload = parseSignalPayload(body);
  if (!payload) { res.writeHead(400).end(); return; }
  res.writeHead(202).end();          // 立刻 ACK,渲圖+送出走背景
  handlers.onSignal(payload);
}

// 真正的 dispatch:解析目標頻道 → 委派 handleSignalPush。client.login 後由 startPushServer 接上。
async function dispatch(client: Client, payload: SignalPayload): Promise<void> {
  try {
    const id = config.signalsChannelId;
    let channel: { send: (m: BaseMessageOptions) => Promise<unknown> } | null = null;
    if (id) {
      const ch = await client.channels.fetch(id).catch(() => null);
      if (ch && "send" in ch) {
        channel = ch as unknown as { send: (m: BaseMessageOptions) => Promise<unknown> };
      } else {
        console.warn(`[bot] 訊號頻道抓不到或不可發送:${id}`);
      }
    }
    await handleSignalPush(payload, {
      channelConfigured: channel !== null,
      buildSymbolMessages,
      sendToChannel: async (m) => { await channel!.send(m); },
    });
  } catch (e) {
    console.warn(`[bot] 訊號推播失敗:${payload.symbol} / ${payload.rule_name}`, e);
  }
}

// startup 用:綁 client + 只聽 127.0.0.1。
export function startPushServer(client: Client): void {
  const server = createPushServer({ onSignal: (p) => void dispatch(client, p) });
  server.on("error", (e) => console.error("[bot] push-server 錯誤:", e));
  server.listen(config.pushPort, "127.0.0.1", () =>
    console.log(`[bot] push-server 監聽 127.0.0.1:${config.pushPort}`));
}
```

- [ ] **Step 5: 跑測試確認通過 + 型別**

Run(在 `bot/`):`npx vitest run src/push-server.test.ts`
Expected:6 passed。
Run:`npx tsc --noEmit`
Expected:0 errors。

- [ ] **Step 6: Commit**

```bash
git add bot/src/config.ts bot/src/push-server.ts bot/src/push-server.test.ts
git commit -m "feat(bot): 新增只聽 localhost 的訊號 push-server(回 202 + 背景渲圖卡)"
```

---

## Task 5: 接線(`index.ts` ClientReady 起 server)+ env 範本 + 本機設定

**Files:**
- Modify: `bot/src/index.ts`(`ClientReady`)
- Modify: `bot/.env.example`
- Modify(本機、不進版控):`bot/.env`、`backend/.env`

- [ ] **Step 1: `index.ts` 在 ClientReady 啟動 push-server**

在 `bot/src/index.ts`:imports 區塊加一行:

```ts
import { startPushServer } from "./push-server";
```

把 `client.once(Events.ClientReady, ...)` 那行換成:

```ts
client.once(Events.ClientReady, (c) => {
  console.log(`[bot] 上線:${c.user.tag}`);
  startPushServer(c);   // 登入後才起 server,確保能 fetch/send 頻道
});
```

- [ ] **Step 2: 型別 + 全測試**

Run(在 `bot/`):`npx tsc --noEmit` → 0 errors;`npx vitest run` → 全綠。

- [ ] **Step 3: 更新 `bot/.env.example`**

整檔換成:

```
# Discord bot token（Developer Portal → Bot → Reset Token）
DISCORD_BOT_TOKEN=
# 後端位址（start.ps1 跑在這）
BACKEND_BASE_URL=http://127.0.0.1:8000
# 後端有設 BFF_API_KEY 時必填且需一致；沒設留空
BFF_API_KEY=
# 限制回應頻道（逗號分隔 channel id）；留空 = 任何看得到的頻道都回
BOT_ALLOWED_CHANNELS=
# 訊號圖卡要推到哪個頻道（空 = 收到訊號也不推；主動推播一定要有目標頻道）
SIGNALS_DISCORD_CHANNEL_ID=
# 訊號 push-server 監聽埠（需與後端 SIGNALS_BOT_PUSH_URL 一致）
BOT_PUSH_PORT=8787
```

- [ ] **Step 4: Commit(範本)**

```bash
git add bot/src/index.ts bot/.env.example
git commit -m "feat(bot): ClientReady 啟動 push-server + .env 範本加訊號頻道/埠"
```

- [ ] **Step 5: 本機設定(不進版控,gitignored)**

在 `bot/.env` 追加(本機真實值,channel 已由 user 提供):

```
SIGNALS_DISCORD_CHANNEL_ID=1487309833630908448
BOT_PUSH_PORT=8787
```

在 `backend/.env` 追加 / 確認:

```
SIGNALS_BOT_PUSH_URL=http://127.0.0.1:8787/push-signal
```

(若 `backend/.env` 還留著舊的 `SIGNALS_DISCORD_WEBHOOK_URL=...`,可刪;已無人讀。)

> ⚠️ bot 要能 send 到該頻道,bot 必須在該頻道所屬的 guild 內、且有發訊息權限。

---

## Task 6: 整合驗證 + 盤中手動實測

- [ ] **Step 1: 全自動測試綠燈**

Run(在 `backend/`):`.\.venv\Scripts\python.exe -m pytest tests/ -q` → 全綠。
Run(在 `bot/`):`npx vitest run` → 全綠;`npx tsc --noEmit` → 0 errors。

- [ ] **Step 2: 離線煙霧測(不必等盤中、不必開 Discord)**

開 bot(在 `bot/`:`npm run start`,需 `bot/.env` 有 token)。bot log 應出現 `push-server 監聽 127.0.0.1:8787`。
另開一個 shell 打 push-server(模擬後端):

```powershell
$body = '{"symbol":"2330","rule_name":"煙霧測試","price":600.5,"volume":1234,"triggered_at":"2026-06-08T05:30:00+00:00","cdp_touch":{"level":"AH","role":"support","touch_index":2},"ma_touch":null}'
Invoke-WebRequest -Uri http://127.0.0.1:8787/push-signal -Method POST -ContentType "application/json" -Body $body | Select-Object -ExpandProperty StatusCode
```

Expected:回 `202`;bot 在 `SIGNALS_DISCORD_CHANNEL_ID` 頻道送出三則(橫幅文字卡 + 分時圖 + 五檔圖)。頻道未設則 bot log「略過」。

- [ ] **Step 3: 盤中端到端(真實觸發)**

1. `backend/.env` 確認 `SIGNALS_BOT_PUSH_URL` 已設、重啟 backend。
2. 前端建一條**必觸發**的監聽規則(例:`close > 0`)、勾「Discord 通知」(`notify_discord`)。
3. 盤中等一個 tick → 確認該 Discord 頻道收到完整三則 + 橫幅顯示規則名/觸發價/台北時間/碰線。
4. 驗降級:暫時關 bot → 觸發 → 後端 log 出現 `Discord signal push failed`、**不崩**、訊號仍寫進 `active_signals`/`signals_log`。

- [ ] **Step 4: 收尾**

- 確認 `p代號` 互動查詢行為完全不變(送一則 `p2330` 比對)。
- 把實測結果記回 spec 或 handoff;更新記憶 [[project_discord_signal_notify_pending]] / [[project_bot_quote_image_cdp_asterisk]] 的待辦。

---

## 自我審查(寫完計畫對照 spec)

- **Spec §4 後端**:Task 1 ✓(send_signal 改 POST、env 換、簽名不變)。
- **Spec §5 bot 新檔/重構**:signal.ts→T2、messages.ts/index 重構→T3、push-server/config→T4、ClientReady/env→T5 ✓。
- **Spec §6 橫幅格式**:T2 `formatBanner`(規則名/觸發價/台北時間/CDP/MA、sma_5→MA5、role 中譯、第N次)✓。
- **Spec §7 資料流 / §8 降級**:202+背景=T4;頻道未設/抓不到/壞 payload=T2+T4;bot 沒開 swallow=T1;產圖降級=既有 composeReply(T3 沿用)✓。
- **Spec §9 安全**:只 bind 127.0.0.1(T4 `listen(...,"127.0.0.1")`)、只收 application/json(T4 415)✓。
- **Spec §10 設定**:backend env=T1S5、bot env=T5S3、本機值=T5S5 ✓。
- **Spec §11 測試**:backend=T1、formatBanner/parse/handler=T2、HTTP 殼=T4 ✓。
- **型別一致**:`SignalPayload`/`TouchMeta`/`PushDeps`(T2 定義)↔ push-server import(T4);`buildSymbolMessages`(T3)↔ dispatch import(T4);`createPushServer`/`PushServerHandlers`(T4)↔ test(T4);`config.signalsChannelId`/`pushPort`(T4)↔ dispatch/startPushServer(T4)。一致 ✓。
- **Placeholder 掃描**:無 TODO/TBD;每個 code step 都是可直接落地的完整程式碼。
