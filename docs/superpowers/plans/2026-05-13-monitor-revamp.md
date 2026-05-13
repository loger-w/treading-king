# Monitor v12 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Monitor 頁從現有 2-row layout 改為 4 欄等高 layout，加上明細 (TradeTape) + 五檔 (QuoteBook) 兩個新 panel，並把搜尋從「直接加自選」改為「先預覽再決定加」。

**Architecture:** 純前端改動 — `Monitor.tsx` 重組成 grid-4 等高 layout；新增 2 個獨立 hook + 2 個獨立元件；複用既有 `GET /api/quote/{symbol}` REST 端點（每 2 秒 poll 五檔）與 WS `tick` event（給明細）。後端零改動，DSL 零改動，只改一個前端 label 字串。

**Tech Stack:** React 18 + TypeScript + Tailwind (Editorial Dark theme tokens) + Vite。WS via native `WebSocket`、REST via existing `fetchJSON`. 無前端測試框架——驗證方式為 manual UAT + `tsc --noEmit` + dev server 視覺檢查。

**Spec reference:** `docs/superpowers/specs/2026-05-13-monitor-revamp-design.md`

---

## File Structure

**Create:**
- `frontend/src/hooks/useQuoteBook.ts` — REST poll selected symbol 的 quote API，2s interval，visibility-aware
- `frontend/src/hooks/useTradeTape.ts` — 透過 WS tick event 累積 selected symbol 的最近 50 筆成交
- `frontend/src/components/QuoteBook.tsx` — 五檔顯示（買賣兩欄、量條 width-proportional）
- `frontend/src/components/TradeTape.tsx` — 明細顯示（時間/價/量/內外盤）
- `frontend/src/components/TriggerList.tsx` — 取代舊 `TriggerHistoryTable`，單欄式
- `backend/scripts/probe_quote_shape.py` — 驗證 `/api/quote/{symbol}` 回傳 bids/asks shape 與既有 `QuoteResponse` 一致

**Modify:**
- `frontend/src/components/ActiveSignalEditor.tsx:8-16` — `FIELD_LABEL.close` 改字 + 加 hint
- `frontend/src/components/IntradayChart.tsx` — 加大 + 加「+ 加入自選 / 已在自選」按鈕
- `frontend/src/components/TopToolbar.tsx` — grid 4-col 對齊 main grid、內嵌 `SymbolSearch`、加 `/` keyboard handler
- `frontend/src/pages/Monitor.tsx` — 大改：grid-4 layout、search 流程改造、整合新元件
- `frontend/src/hooks/useSignalsStream.ts` — 新增 `onTick` 不變但要讓 ticks broadcast 也能被 `useTradeTape` 監聽（透過暴露 tick 訂閱 API）

**Delete (after integration):**
- `frontend/src/components/TriggerHistoryTable.tsx` — 由 TriggerList 取代

---

## Task 1: 改 FIELD_LABEL.close 為「即時價」+ 加 hint

最小、無相依，先 ship 掉這個 quick win。

**Files:**
- Modify: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: 改 FIELD_LABEL.close 字串**

打開 `frontend/src/components/ActiveSignalEditor.tsx`，line 8-16 範圍：

```typescript
const FIELD_LABEL: Record<ConditionField, string> = {
  close: "即時價",
  change_pct: "漲跌幅 %", volume: "成交量", amount: "成交金額",
  rsi_14: "RSI(14)", macd: "MACD", macd_signal: "MACD signal",
  kdj_k: "KDJ K", kdj_d: "KDJ D", kdj_j: "KDJ J",
  sma_5: "5 日均線", sma_20: "20 日均線", sma_60: "60 日均線",
  bbands_upper: "BB 上軌", bbands_middle: "BB 中軌", bbands_lower: "BB 下軌",
  cdp_ah: "CDP AH (最高值)", cdp_nh: "CDP NH (近高)", cdp: "CDP 中軸",
  cdp_nl: "CDP NL (近低)", cdp_al: "CDP AL (最低值)",
};
```

- [ ] **Step 2: 在跨指標條件區塊加 hint**

找到 line 141 附近 `<div className="label-tiny mb-2">跨指標條件 (從快取)</div>`，改成：

```tsx
<div className="label-tiny mb-2">跨指標條件 (從快取)</div>
<p className="text-2xs text-ink-dim mb-3 leading-relaxed">
  「即時價」= 最新一筆成交價；盤後 / 未開盤時為前一日收盤。其他指標來自每日快取。
</p>
```

- [ ] **Step 3: 跑 type check**

```bash
cd frontend && npm run -s typecheck
```

Expected: no errors.

- [ ] **Step 4: 開 dev server 視覺驗證**

```bash
cd frontend && npm run dev
```

打開 http://localhost:5173/ → 訊號規則 → 新增規則 → 跨指標條件區塊。
Expected: 第一個欄位 dropdown 顯示「即時價」、上方有 hint 文字。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(rules): rename close label to 即時價 + add hint

DSL key 維持 close (signal_engine 已 confirm 用 tick.price)。
僅 UI label 改名，避免誤導使用者以為是日 K 收盤。"
```

---

## Task 2: 寫 probe script 驗證 quote API shape

確認 `/api/quote/{symbol}` 回傳跟既有 `QuoteResponse` 型別一致（特別是 bids/asks 結構）。

**Files:**
- Create: `backend/scripts/probe_quote_shape.py`

- [ ] **Step 1: 寫 probe 腳本**

```python
"""Probe /api/quote/{symbol} 回傳的 bids/asks shape。

確認：
- bids/asks 是 array of {price, size}
- 五檔順序：bids[0] 最高、asks[0] 最低 (best bid/ask)
- 量單位是「張」(假設)
"""
from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, ".")  # 從 backend/ 目錄跑

from services.fubon_client import get_fubon


async def main(symbol: str = "2330") -> None:
    fubon = get_fubon()
    await fubon.init()
    if fubon.status.value != "ok":
        print(f"FAIL: fubon status={fubon.status.value}, error={fubon.last_error}")
        return

    result = await fubon.intraday_quote(symbol)
    print(f"=== quote({symbol}) keys ===")
    print(sorted(result.keys()))

    print(f"\n=== bids ({len(result.get('bids', []))}) ===")
    for i, b in enumerate(result.get("bids", [])):
        print(f"  [{i}] price={b.get('price')} size={b.get('size')}  keys={list(b.keys())}")

    print(f"\n=== asks ({len(result.get('asks', []))}) ===")
    for i, a in enumerate(result.get("asks", [])):
        print(f"  [{i}] price={a.get('price')} size={a.get('size')}  keys={list(a.keys())}")

    print(f"\n=== raw (truncated) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "2330"
    asyncio.run(main(sym))
```

- [ ] **Step 2: 跑 probe，記下實際結構**

```bash
cd backend && python -m scripts.probe_quote_shape 2330
```

Expected output 含：
- `bids` 5 個元素，每個有 `price` 與 `size` (number 型別)
- `asks` 5 個元素，順序 best-first
- `bids[0].price >= bids[1].price`（價高的在前）
- `asks[0].price <= asks[1].price`（價低的在前）

如果 shape **不一致**（譬如欄位叫 `qty` 不是 `size`），更新 `frontend/src/lib/api.ts` 的 `QuoteResponse` interface 並記錄到 commit message。

- [ ] **Step 3: Commit probe（無論結果都留檔）**

```bash
git add backend/scripts/probe_quote_shape.py
git commit -m "chore(probe): verify /api/quote bids/asks shape for QuoteBook

Confirms bids[0]=best (highest) / asks[0]=best (lowest), price+size fields.
Used as baseline for QuoteBook 五檔 UI implementation."
```

---

## Task 3: 新增 useQuoteBook hook (REST poll 五檔)

**Files:**
- Create: `frontend/src/hooks/useQuoteBook.ts`

- [ ] **Step 1: 寫 hook**

```typescript
import { useEffect, useRef, useState } from "react";
import { api, type QuoteResponse } from "../lib/api";

const POLL_MS = 2000;

export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  lastSuccessAt: Date | null;
  error: string | null;
}

/**
 * Poll /api/quote/{symbol} 每 2 秒，背景 tab 時暫停。
 *
 * Symbol 切換時：
 *  - 立即 fetch
 *  - 取消前一個未完成的 request（AbortController）
 *  - 清空舊資料 + 清 error
 *
 * 失敗時：保留前一次成功的 bids/asks（不閃白），只更新 error。
 */
export function useQuoteBook(symbol: string | null): QuoteBookData {
  const [bids, setBids] = useState<QuoteBookData["bids"]>([]);
  const [asks, setAsks] = useState<QuoteBookData["asks"]>([]);
  const [lastSuccessAt, setLastSuccessAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!symbol) {
      setBids([]); setAsks([]); setLastSuccessAt(null); setError(null);
      return;
    }

    // 切 symbol：清舊資料 + cancel pending
    abortRef.current?.abort();
    setBids([]); setAsks([]); setError(null);

    async function fetchOnce() {
      if (document.hidden) return;  // tab 背景時跳過
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const r: QuoteResponse = await api.quote(symbol!);
        if (ctrl.signal.aborted) return;
        setBids(r.bids ?? []);
        setAsks(r.asks ?? []);
        setLastSuccessAt(new Date());
        setError(null);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    }

    fetchOnce();
    timerRef.current = setInterval(fetchOnce, POLL_MS);
    return () => {
      abortRef.current?.abort();
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [symbol]);

  return { bids, asks, lastSuccessAt, error };
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

Expected: no errors。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useQuoteBook.ts
git commit -m "feat(hooks): add useQuoteBook (REST poll 2s, abort + visibility-aware)"
```

---

## Task 4: 新增 QuoteBook 元件

**Files:**
- Create: `frontend/src/components/QuoteBook.tsx`

- [ ] **Step 1: 寫元件**

```tsx
import { useQuoteBook } from "../hooks/useQuoteBook";

interface Props {
  symbol: string | null;
}

/**
 * 委買賣五檔 — 走 REST poll (useQuoteBook)，2 秒更新一次。
 *
 * Layout：左 5 檔買（紅）、右 5 檔賣（綠），每 row 顯示價+量+量條。
 * 量條 width 用該邊最大量做 normalize（買賣分開算）。
 *
 * 切 symbol / fetch 失敗時：見 useQuoteBook 行為說明。
 */
export function QuoteBook({ symbol }: Props) {
  const { bids, asks, lastSuccessAt, error } = useQuoteBook(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看五檔
      </div>
    );
  }

  const maxBidQty = Math.max(1, ...bids.map((b) => b.size));
  const maxAskQty = Math.max(1, ...asks.map((a) => a.size));

  const timeStr = lastSuccessAt
    ? lastSuccessAt.toLocaleTimeString("zh-TW", { hour12: false })
    : "—";

  return (
    <div className="border border-line bg-bg-card/50 p-[22px]">
      <div className="flex items-baseline justify-between pb-2.5 mb-3.5 border-b border-line">
        <h3 className="font-serif font-bold text-lg tracking-[-0.3px]">委買賣 五檔</h3>
        <span className="text-2xs uppercase tracking-[1px] text-ink-dim">
          每 2 秒 refresh · {timeStr}
          {error && <span className="ml-2 text-bear">· 更新失敗</span>}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-8">
        <div>
          <h4 className="text-2xs uppercase tracking-[1.5px] text-ink-dim mb-2">委買 BID</h4>
          {bids.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            bids.map((b, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 right-0 bg-bull/10 pointer-events-none"
                  style={{ width: `${(b.size / maxBidQty) * 100}%` }}
                />
                <span className="relative z-[1] text-bull font-medium">{b.price.toFixed(2)}</span>
                <span className="relative z-[1] text-right text-ink-muted">{b.size} 張</span>
              </div>
            ))
          )}
        </div>
        <div>
          <h4 className="text-2xs uppercase tracking-[1.5px] text-ink-dim mb-2">委賣 ASK</h4>
          {asks.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            asks.map((a, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 left-0 bg-bear/10 pointer-events-none"
                  style={{ width: `${(a.size / maxAskQty) * 100}%` }}
                />
                <span className="relative z-[1] text-bear font-medium">{a.price.toFixed(2)}</span>
                <span className="relative z-[1] text-right text-ink-muted">{a.size} 張</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/QuoteBook.tsx
git commit -m "feat(components): add QuoteBook 五檔 (REST poll based)"
```

---

## Task 5: 暴露 WS tick 訂閱介面（useSignalsStream）

`useSignalsStream` 目前 `onTick` 是 single-listener。TradeTape 要再加一個 listener，但不破壞 IntradayChart 既有的 onTick。改法：把 tick 處理改為 **EventTarget pattern**，hooks 各自 subscribe。

**Files:**
- Modify: `frontend/src/hooks/useSignalsStream.ts`

- [ ] **Step 1: 在 useSignalsStream 內加一個 EventTarget broadcaster + 暴露 subscribe API**

整個檔案改成：

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { type SignalEvent } from "../lib/api";

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export type WSStatus = "connecting" | "open" | "closed";

export interface TickEvent {
  symbol: string;
  price: number;
}

// Module-level EventTarget — 跨 hook instance 共用同一個 WS tick stream
const tickBus = new EventTarget();

/**
 * 任意元件可 import 此 helper 訂閱 tick。
 * 回傳 unsubscribe function（呼叫即 detach）。
 */
export function subscribeTicks(handler: (t: TickEvent) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<TickEvent>).detail);
  tickBus.addEventListener("tick", fn);
  return () => tickBus.removeEventListener("tick", fn);
}

export function useSignalsStream(opts?: {
  onSignal?: (s: SignalEvent["data"]) => void;
  onTick?: (symbol: string, price: number) => void;
}) {
  const [status, setStatus] = useState<WSStatus>("connecting");
  const [recent, setRecent] = useState<SignalEvent["data"][]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const onSignalRef = useRef(opts?.onSignal);
  const onTickRef = useRef(opts?.onTick);

  useEffect(() => { onSignalRef.current = opts?.onSignal; }, [opts?.onSignal]);
  useEffect(() => { onTickRef.current = opts?.onTick; }, [opts?.onTick]);

  const connect = useCallback(() => {
    setStatus("connecting");
    const apiKey = (import.meta.env.VITE_BFF_API_KEY ?? "") as string;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/realtime?api_key=${encodeURIComponent(apiKey)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("open");
      attemptRef.current = 0;
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.event === "signal") {
          const data = msg.data as SignalEvent["data"];
          setRecent((prev) => [data, ...prev].slice(0, 50));
          onSignalRef.current?.(data);
        } else if (msg.event === "tick") {
          const tick: TickEvent = { symbol: msg.data.symbol, price: msg.data.price };
          onTickRef.current?.(tick.symbol, tick.price);
          tickBus.dispatchEvent(new CustomEvent<TickEvent>("tick", { detail: tick }));
        }
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      setStatus("closed");
      const delay = RECONNECT_DELAYS_MS[Math.min(attemptRef.current, RECONNECT_DELAYS_MS.length - 1)];
      attemptRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => { /* close 會跟著觸發 */ };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, recent };
}
```

關鍵：除了原本 `onTick` callback 仍 forward 給 IntradayChart 用，**也**把每筆 tick dispatch 到 `tickBus`。TradeTape 透過 `subscribeTicks()` 監聽，與 IntradayChart 各自獨立。

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSignalsStream.ts
git commit -m "refactor(useSignalsStream): expose tickBus for multi-listener subscription

Original onTick callback unchanged (IntradayChart 仍正常用)。
新增 subscribeTicks() helper 給 TradeTape 等獨立 hook 用。"
```

---

## Task 6: 新增 useTradeTape hook

**Files:**
- Create: `frontend/src/hooks/useTradeTape.ts`

- [ ] **Step 1: 寫 hook**

```typescript
import { useEffect, useRef, useState } from "react";
import { subscribeTicks } from "./useSignalsStream";

const MAX_TICKS = 50;

export type TradeSide = "buy" | "sell" | "neutral";

export interface TradeRow {
  time: Date;
  price: number;
  side: TradeSide;
}

/**
 * 累積 selected symbol 的最近 50 筆 tick，並判定內外盤。
 *
 * 內外盤判定（簡化）：
 *  - 比前一筆價高 → buy (外盤，紅)
 *  - 比前一筆價低 → sell (內盤，綠)
 *  - 平盤 → 沿用上一筆方向（首筆預設 neutral）
 *
 * 注意：本期不顯示單量（WS tick payload 目前不含 size — 見 fubon_ws.py 第 217 行
 * broadcast payload 只有 symbol/price）。等 backend broadcast 加 size 後 hook 補上 vol。
 */
export function useTradeTape(symbol: string | null): TradeRow[] {
  const [rows, setRows] = useState<TradeRow[]>([]);
  const lastPriceRef = useRef<number | null>(null);
  const lastSideRef = useRef<TradeSide>("neutral");

  useEffect(() => {
    if (!symbol) {
      setRows([]);
      lastPriceRef.current = null;
      lastSideRef.current = "neutral";
      return;
    }

    // Symbol 切換時清空
    setRows([]);
    lastPriceRef.current = null;
    lastSideRef.current = "neutral";

    const unsub = subscribeTicks((t) => {
      if (t.symbol !== symbol) return;
      let side: TradeSide = "neutral";
      if (lastPriceRef.current !== null) {
        if (t.price > lastPriceRef.current) side = "buy";
        else if (t.price < lastPriceRef.current) side = "sell";
        else side = lastSideRef.current;  // 平盤沿用上次
      }
      lastPriceRef.current = t.price;
      lastSideRef.current = side;

      setRows((prev) => [{ time: new Date(), price: t.price, side }, ...prev].slice(0, MAX_TICKS));
    });
    return unsub;
  }, [symbol]);

  return rows;
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useTradeTape.ts
git commit -m "feat(hooks): add useTradeTape (WS tick subscribe, 50 row cap)

內外盤用相鄰 tick 比較判定。平盤沿用上次方向。
Volume 暫不顯示 — fubon_ws broadcast payload 目前只含 symbol/price。"
```

---

## Task 7: 新增 TradeTape 元件

**Files:**
- Create: `frontend/src/components/TradeTape.tsx`

- [ ] **Step 1: 寫元件**

```tsx
import { useTradeTape } from "../hooks/useTradeTape";

interface Props {
  symbol: string | null;
}

function formatTime(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * 明細 — 最近 50 筆成交 (selected symbol)。
 *
 * Header sticky，內容滾動。空狀態：等第一筆 tick 進來。
 */
export function TradeTape({ symbol }: Props) {
  const rows = useTradeTape(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看明細
      </div>
    );
  }

  return (
    <div className="border-t border-line">
      <div className="grid grid-cols-[64px_1fr_36px] gap-1.5 px-1 py-2 border-b border-line-strong text-2xs uppercase tracking-[1.2px] text-ink-dim">
        <div>時間</div>
        <div className="text-right">價</div>
        <div className="text-center">向</div>
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-10 text-center text-ink-dim font-serif italic text-sm">
          等待第一筆成交…
        </div>
      ) : (
        rows.map((r, i) => (
          <div
            key={i}
            className="grid grid-cols-[64px_1fr_36px] gap-1.5 px-1 py-1.5 border-b border-line text-xs tabular-nums"
          >
            <span className="text-ink-muted">{formatTime(r.time)}</span>
            <span className={[
              "text-right font-medium",
              r.side === "buy" ? "text-bull" : r.side === "sell" ? "text-bear" : "text-ink",
            ].join(" ")}>
              {r.price.toFixed(2)}
            </span>
            <span className={[
              "text-center text-xs",
              r.side === "buy" ? "text-bull" : r.side === "sell" ? "text-bear" : "text-ink-dim",
            ].join(" ")}>
              {r.side === "buy" ? "外" : r.side === "sell" ? "內" : "—"}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TradeTape.tsx
git commit -m "feat(components): add TradeTape 明細 (50 ticks, inner/outer board hint)"
```

---

## Task 8: 新增 TriggerList 元件（取代 TriggerHistoryTable，單欄式）

**Files:**
- Create: `frontend/src/components/TriggerList.tsx`

- [ ] **Step 1: 寫元件**

```tsx
import { type ActiveSignal, type SignalLogRow, type SignalEvent } from "../lib/api";

/**
 * 觸發歷史 — 單欄列表（適合 ≤ 300px 寬欄位）。
 *
 * 每 row 兩行：
 *   line1: 股票代號 + name (italic)        時間
 *   line2: 規則名稱                         觸發價
 *
 * 資料合併與 dedup 邏輯沿用原 TriggerHistoryTable。
 */
interface Props {
  historical: SignalLogRow[];
  recent: SignalEvent["data"][];
  rules: ActiveSignal[];
  symbolNames: Record<string, string | null>;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
}

interface UnifiedRow {
  key: string;
  time: string;
  symbol: string;
  name: string | null;
  ruleName: string;
  price: number;
  isoTime: string;
  isFresh: boolean;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function TriggerList({
  historical, recent, rules, symbolNames, selectedSymbol, onSelect,
}: Props) {
  const ruleNameById = Object.fromEntries(rules.map((r) => [r.id, r.name]));

  const recentRows: UnifiedRow[] = recent.map((e) => ({
    key: `recent-${e.active_signal_id}-${e.triggered_at}-${e.symbol}`,
    time: formatTime(e.triggered_at),
    symbol: e.symbol,
    name: symbolNames[e.symbol] ?? null,
    ruleName: e.active_signal_name ?? ruleNameById[e.active_signal_id] ?? "(unknown)",
    price: e.trigger_price,
    isoTime: e.triggered_at,
    isFresh: true,
  }));

  const historicalRows: UnifiedRow[] = historical.map((h) => ({
    key: `hist-${h.id}`,
    time: formatTime(h.triggered_at),
    symbol: h.symbol,
    name: symbolNames[h.symbol] ?? null,
    ruleName: ruleNameById[h.active_signal_id ?? ""] ?? "(unknown)",
    price: h.trigger_price ?? 0,
    isoTime: h.triggered_at,
    isFresh: false,
  }));

  const seen = new Set<string>();
  const combined: UnifiedRow[] = [];
  for (const r of [...recentRows, ...historicalRows]) {
    const k = `${r.symbol}|${r.ruleName}|${r.isoTime}`;
    if (seen.has(k)) continue;
    seen.add(k);
    combined.push(r);
  }
  combined.sort((a, b) => b.isoTime.localeCompare(a.isoTime));

  if (combined.length === 0) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        等待第一筆訊號…
      </div>
    );
  }

  return (
    <ul className="border-t border-line">
      {combined.map((r) => {
        const isSel = r.symbol === selectedSymbol;
        return (
          <li
            key={r.key}
            onClick={() => onSelect(r.symbol)}
            className={[
              "px-1 py-3 border-b border-line cursor-pointer transition-colors duration-150",
              isSel ? "bg-bg-card border-l-2 border-l-accent pl-2.5" : "hover:bg-bg-card/40",
              r.isFresh && !isSel ? "bg-accent/[0.04]" : "",
            ].join(" ")}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-serif font-bold text-base tracking-[-0.2px]">
                {r.symbol}
                {r.name && (
                  <span className="ml-1.5 font-serif italic font-normal text-xs text-ink-muted">
                    {r.name}
                  </span>
                )}
              </span>
              <span className="text-xs text-ink-dim tabular-nums">{r.time}</span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-xs text-ink-dim uppercase tracking-[0.5px]">
                {r.ruleName}
              </span>
              <span className="text-sm tabular-nums text-bull font-medium">
                {r.price.toFixed(2)}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TriggerList.tsx
git commit -m "feat(components): add TriggerList (single-column 觸發歷史)

取代 TriggerHistoryTable（4-col 寬 grid）。
沿用同樣的 dedup + sort 邏輯，UI 改為適合 300px 窄欄。"
```

---

## Task 9: IntradayChart 加大 + 加「加入自選」按鈕

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: Props 新增兩個欄位**

`IntradayChart.tsx` 第 6-11 行 `interface Props`：

```typescript
interface Props {
  symbol: string;
  name: string | null;
  candles: IntradayCandle[];
  prevClose: number | null;
  inWatchlist: boolean;             // 新增
  onAddToWatchlist: () => void;     // 新增
}
```

函式簽名同步加：

```typescript
export function IntradayChart({
  symbol, name, candles, prevClose, inWatchlist, onAddToWatchlist,
}: Props) {
```

- [ ] **Step 2: 改 chart 大小常數**

第 13-14 行：

```typescript
const CHART_W = 820;   // 原 720
const CHART_H = 460;   // 原 360
```

- [ ] **Step 3: Header 加按鈕**

找到 chart header（顯示 symbol/name/price 的區塊），在右側 price-block 之後加：

```tsx
<button
  type="button"
  onClick={onAddToWatchlist}
  disabled={inWatchlist}
  className={[
    "inline-flex items-center gap-1.5 px-3.5 py-1.5 text-2xs uppercase tracking-[1.5px] transition-all duration-150",
    inWatchlist
      ? "border border-line text-ink-dim cursor-default"
      : "border border-ink-dim text-ink-muted hover:border-accent hover:text-accent cursor-pointer",
  ].join(" ")}
  aria-label={inWatchlist ? "已在自選清單" : "加入自選清單"}
>
  {inWatchlist ? "已在自選 ✓" : "+ 加入自選"}
</button>
```

（具體位置：找到 chart header 的 right-side container，在 price 之後 append。如果現在 chart header 沒有明確的 flex container，包一個 `<div className="flex items-baseline justify-between gap-4">` 把 left=symbol/name、right=price+button 分開。）

- [ ] **Step 4: type check**

```bash
cd frontend && npm run -s typecheck
```

Expected: error — Monitor.tsx 還沒傳新 props。先忽略 Monitor.tsx 的 error（下個 task 修），但確認 IntradayChart 內部沒有 type 錯誤。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(IntradayChart): enlarge to 820x460 + add 加入自選 button

Header 右側按鈕：inWatchlist=true 顯示「已在自選 ✓」(disabled)，
否則「+ 加入自選」(accent hover)。Monitor.tsx 下 task 接上 props。"
```

---

## Task 10: TopToolbar 改 grid 4-col + 內嵌 SymbolSearch + `/` keyboard

**Files:**
- Modify: `frontend/src/components/TopToolbar.tsx`

- [ ] **Step 1: 改 Props 與整個元件**

整個 `TopToolbar.tsx` 改為：

```tsx
import { useEffect, useRef } from "react";
import { type WSStatus } from "../hooks/useSignalsStream";
import { SymbolSearch } from "./SymbolSearch";

/**
 * Toolbar：grid 4-col 對齊 main grid。
 *   col 1-2 (left):   ● 連線狀態
 *   col 3:            ⌕ 搜尋框（內嵌 SymbolSearch）
 *   col 4 (right):    ⚙ 訊號規則 按鈕
 *
 * 鍵盤：按 `/` 聚焦搜尋輸入框（input/textarea 已聚焦或有 modifier key 時跳過）。
 */
interface Props {
  wsStatus: WSStatus;
  rulesCount: number;
  dialogOpen: boolean;
  onOpenRules: () => void;
  onPickSymbol: (symbol: string) => void;
}

function statusText(s: WSStatus): { text: string; color: string } {
  if (s === "open") return { text: "連線中", color: "text-bear" };
  if (s === "connecting") return { text: "連線中…", color: "text-accent" };
  return { text: "已斷線", color: "text-accent" };
}

export function TopToolbar({ wsStatus, rulesCount, dialogOpen, onOpenRules, onPickSymbol }: Props) {
  const { text, color } = statusText(wsStatus);
  const searchWrapRef = useRef<HTMLDivElement | null>(null);

  // `/` 聚焦 input
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const tgt = e.target as HTMLElement | null;
      if (tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable)) return;
      const inp = searchWrapRef.current?.querySelector("input");
      if (inp) {
        e.preventDefault();
        inp.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="bg-transparent">
      <div
        className="mx-auto max-w-[1960px] px-9 pt-[26px] pb-2.5 grid items-center gap-6 max-md:px-6 max-md:grid-cols-1"
        style={{ gridTemplateColumns: "300px 340px 1fr 300px" }}
      >
        <span
          className="inline-flex items-baseline gap-2 text-2xs uppercase tracking-[1.5px] text-ink-dim"
          style={{ gridColumn: "1 / 3", justifySelf: "start" }}
        >
          <span className={`${color} text-sm leading-none`}>●</span>
          {text}
        </span>

        <div ref={searchWrapRef} style={{ gridColumn: "3 / 4" }}>
          <SymbolSearch onPick={onPickSymbol} placeholder="搜尋股票代號或名稱…（按 / 聚焦）" />
        </div>

        <button
          type="button"
          onClick={onOpenRules}
          style={{ gridColumn: "4 / 5", justifySelf: "end" }}
          className={[
            "inline-flex items-center gap-2.5 px-[18px] py-2 text-xs uppercase tracking-[1.8px] font-medium border transition-all duration-150 cursor-pointer",
            dialogOpen
              ? "bg-accent text-bg border-accent"
              : "bg-transparent text-accent border-accent hover:bg-accent/10",
          ].join(" ")}
        >
          <span className="text-sm leading-none">⚙</span>
          訊號規則
          <span className={[
            "text-xs px-1.5 py-[1px] font-semibold transition-colors duration-150",
            dialogOpen ? "bg-bg text-accent" : "bg-accent text-bg",
          ].join(" ")}>
            {rulesCount}
          </span>
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: type check**

```bash
cd frontend && npm run -s typecheck
```

Expected: error — Monitor.tsx 還沒傳 `onPickSymbol` prop。下 task 修。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TopToolbar.tsx
git commit -m "feat(TopToolbar): grid 4-col aligned + embedded SymbolSearch + / hotkey

Toolbar 變 4 欄 grid 對齊下方 main grid (Monitor.tsx 下 task 改 max-w 1960)。
SymbolSearch 內嵌到 col 3 (對齊分時走勢欄)。
`/` 鍵盤聚焦 input，input/textarea/modifier 鍵時跳過。"
```

---

## Task 11: Monitor.tsx 大改 — grid-4 layout + 整合新元件 + search 流程改造

**Files:**
- Modify: `frontend/src/pages/Monitor.tsx`

- [ ] **Step 1: 改 import**

頂部 import 改成：

```typescript
import { useEffect, useMemo, useRef, useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { QuoteBook } from "../components/QuoteBook";
import { SignalRulesDialog } from "../components/SignalRulesDialog";
import { TopToolbar } from "../components/TopToolbar";
import { TradeTape } from "../components/TradeTape";
import { TriggerList } from "../components/TriggerList";
import { WatchlistWithChips } from "../components/WatchlistWithChips";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useTodayHits } from "../hooks/useTodayHits";
import { useWatchlist } from "../hooks/useWatchlist";
import { api, type SignalLogRow } from "../lib/api";
```

注意：移除 `SymbolSearch` import（已內嵌到 TopToolbar）、移除 `TriggerHistoryTable` import（由 TriggerList 取代）。

- [ ] **Step 2: 改 handleAdd（搜尋現在不直接加，只選；加按鈕由 chart header 提供）**

把 `handleAdd` 替換為 `handleSearchPick` + `handleAddCurrent`：

```typescript
function handleSearchPick(sym: string) {
  // 搜尋現在「先預覽」：只 setSelected，不 add。
  setSelected(sym);
  if (chartRef.current) {
    chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function handleAddCurrent() {
  if (!selected) return;
  try { await add(selected); } catch (e) { console.warn("add failed:", e); }
}
```

刪掉舊的 `handleAdd`。

- [ ] **Step 3: 計算 inWatchlist**

在 `handleAdd` 移除後、render 之前加：

```typescript
const inWatchlist = useMemo(
  () => selected !== null && watchlistItems.some((w) => w.symbol === selected),
  [watchlistItems, selected]
);
```

- [ ] **Step 4: 改整段 return 為 grid-4 layout**

把 `<main>` 內整段（從 `<div className="mx-auto max-w-[1600px]..."` 開始到 `</main>` 前）替換為：

```tsx
<main>
  <div
    className="mx-auto max-w-[1960px] px-9 pt-3 pb-12 max-md:px-6"
  >
    <div
      className="grid items-stretch gap-6 max-[1200px]:grid-cols-1"
      style={{ gridTemplateColumns: "300px 340px 1fr 300px" }}
    >

      {/* COL 1: 觸發歷史 */}
      <section className="flex flex-col min-w-0 min-h-0">
        <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
          <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
            觸發歷史
          </h2>
          <span className="font-sans font-normal text-sm text-ink-dim">
            ({historicalToday.length + recent.length})
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
          <TriggerList
            historical={historicalToday}
            recent={recent}
            rules={rules}
            symbolNames={symbolNames}
            selectedSymbol={selected}
            onSelect={handleSelect}
          />
        </div>
      </section>

      {/* COL 2: 自選清單 */}
      <section className="flex flex-col min-w-0 min-h-0">
        <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
          <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
            自選清單
          </h2>
          <span className="font-sans font-normal text-sm text-ink-dim">
            ({watchlistItems.length})
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
          <WatchlistWithChips
            items={watchlistItems}
            rules={rules}
            hitCounts={counts}
            selectedSymbol={selected}
            onSelect={setSelected}
            onRemove={remove}
          />
        </div>
      </section>

      {/* COL 3: 分時走勢 + 五檔 */}
      <section ref={chartRef} className="flex flex-col min-w-0 gap-6">
        <div className="flex-shrink-0">
          <div className="flex items-baseline gap-2.5 mb-4">
            <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
              分時走勢
            </h2>
          </div>
          {!selected ? (
            <div className="h-[460px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
              ← 從觸發歷史 / 自選 / 上方搜尋挑一檔
            </div>
          ) : (
            <div className="border border-line p-6">
              <IntradayChart
                symbol={selected}
                name={symbolNames[selected] ?? null}
                candles={candles}
                prevClose={prevClose}
                inWatchlist={inWatchlist}
                onAddToWatchlist={handleAddCurrent}
              />
            </div>
          )}
        </div>
        <QuoteBook symbol={selected} />
      </section>

      {/* COL 4: 明細 */}
      <section className="flex flex-col min-w-0 min-h-0">
        <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
          <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
            明細
          </h2>
          <span className="font-sans font-normal text-sm text-ink-dim">
            {selected ? `(${selected})` : ""}
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto pr-1.5">
          <TradeTape symbol={selected} />
        </div>
      </section>

    </div>
  </div>
</main>
```

- [ ] **Step 5: TopToolbar 傳 onPickSymbol**

`<TopToolbar>` 改為：

```tsx
<TopToolbar
  wsStatus={wsStatus}
  rulesCount={rules.length}
  dialogOpen={dialogOpen}
  onOpenRules={() => setDialogOpen((v) => !v)}
  onPickSymbol={handleSearchPick}
/>
```

- [ ] **Step 6: type check**

```bash
cd frontend && npm run -s typecheck
```

Expected: pass。如果還有 error 看哪個 prop 沒接好。

- [ ] **Step 7: Visual smoke**

```bash
cd frontend && npm run dev
```

打開 http://localhost:5173/，目視確認 4 欄 layout、等高、scroll panel 行為。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Monitor.tsx
git commit -m "feat(Monitor): grid-4 layout + integrate TriggerList/QuoteBook/TradeTape

新 layout (max-w 1960, 觸發歷史 300 / 自選 340 / 分時+五檔 1fr / 明細 300)。
4 欄等高 (align-items: stretch + flex column + scroll-panel flex: 1)。
< 1200px 折成單欄。
搜尋流程：先 setSelected 預覽，IntradayChart header 顯示「+ 加入自選 / 已在自選 ✓」。"
```

---

## Task 12: 刪掉 TriggerHistoryTable.tsx

**Files:**
- Delete: `frontend/src/components/TriggerHistoryTable.tsx`

- [ ] **Step 1: 確認無人 import**

```bash
cd frontend && grep -rn "TriggerHistoryTable" src/
```

Expected: 0 matches（Monitor.tsx 上個 task 已經移除 import）。

- [ ] **Step 2: 刪檔**

```bash
rm frontend/src/components/TriggerHistoryTable.tsx
```

- [ ] **Step 3: type check + dev server**

```bash
cd frontend && npm run -s typecheck
```

Expected: pass。

- [ ] **Step 4: Commit**

```bash
git add -A frontend/src/components/TriggerHistoryTable.tsx
git commit -m "chore: remove TriggerHistoryTable (replaced by TriggerList)"
```

---

## Task 13: 盤中 UAT + 寫 docs/decisions

最後一步：實際在盤中（或盤後 stub 資料）跑一次端到端流程，寫 decision 紀錄。

**Files:**
- Create: `docs/decisions/2026-05-13-monitor-v12.md`

- [ ] **Step 1: 啟 backend + frontend**

```bash
# Terminal 1
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

- [ ] **Step 2: 跑 UAT checklist（spec §8）**

打開瀏覽器，逐項驗證：

| 項 | Expected |
|---|---|
| 點自選任一檔 | chart / book / tape 都載入該 symbol |
| 點觸發歷史一列 | 3 panel 同步切換 |
| 搜尋一檔不在自選的（譬如 1101） | chart 切過去 + 顯示「+ 加入自選」 |
| 點「+ 加入自選」 | 自選清單出現該檔，按鈕變「已在自選 ✓」 |
| 按 `/` | 搜尋框聚焦 |
| 訊號規則 dialog 開啟，跨指標條件 dropdown | 第一個顯示「即時價」+ 上方 hint |
| 視窗縮到 1100px | 折成單欄 |
| 切 browser tab 5 分鐘回來 | QuoteBook 顯示新時間戳，無 burst request |
| `python -m scripts.probe_e2e_signal` | 仍通 |

任一項失敗：開新 task fix（不在這個 plan 範圍內，後續處理）。

- [ ] **Step 3: 寫 decision 紀錄**

`docs/decisions/2026-05-13-monitor-v12.md`：

```markdown
# Monitor v12 — Decisions & Open Questions

**Date:** 2026-05-13
**Plan:** `docs/superpowers/plans/2026-05-13-monitor-revamp.md`
**Spec:** `docs/superpowers/specs/2026-05-13-monitor-revamp-design.md`

## 已實作

- 4 欄等高 layout (max-w 1960)
- TriggerList / QuoteBook / TradeTape 三個新元件
- TopToolbar grid 對齊 + 內嵌 SymbolSearch + `/` hotkey
- 搜尋流程改造（select-then-add）
- ActiveSignalEditor: close → 即時價 label + hint

## 已知簡化（trade-off）

1. **TradeTape 不顯示單量** — 因為 `fubon_ws.py` broadcast tick 目前 payload 只含 `symbol` + `price`。
   - **未來：** backend broadcast 加 `size` 後，`useTradeTape` 補上。
2. **內外盤用相鄰價判定** — 沒有委買賣資訊比對。
   - **未來：** 同上，broadcast 加 `bid`/`ask` 後做正確判定。
3. **五檔走 REST poll 2s** — 不是真即時。
   - **未來：** 升級為 WS `books` channel；要先 probe trades+books 共連線容量。

## DSL ±% 條件（spec §2 不做清單）

使用者明確指示先不實作。下次提時：
- 加 `value_pct: float | None` 到 `Condition` schema
- 後端 `_eval_filter_cond` 把 `rhs *= (1 + value_pct/100)` 套上去
- UI 在 value=field 時加 `±__%` 輸入框

## 風險回顧

- REST poll 2s × selected symbol 切換：實測 selected 切換時 abort 正常，未觀察到 rate limit
- tickBus EventTarget 模式：多元件共用 WS tick 無 leak（unsub 確認）
- 4 欄等高 1200px 斷點折單欄：實機驗證 OK
```

- [ ] **Step 4: Commit decision doc**

```bash
git add docs/decisions/2026-05-13-monitor-v12.md
git commit -m "docs: monitor v12 UAT + decisions"
```

---

## Self-Review 結果

**1. Spec coverage:**
- §3.1 整體框 → Task 11 step 4 ✓
- §3.2 Toolbar → Task 10 ✓
- §3.3 4 columns → Task 11 step 4 (整段 layout) ✓
- §4.1 Monitor.tsx → Task 11 ✓
- §4.2 TopToolbar → Task 10 ✓
- §4.3 SymbolSearch onPick 語意改 → Task 11 step 2 (handleSearchPick 不再 add) ✓
- §4.4 IntradayChart 加大 + 按鈕 → Task 9 ✓
- §4.5 WatchlistWithChips → 無需改（onSelect 已 wired，搜尋框移除是 Monitor.tsx 動的；元件本身不動）✓
- §4.6 TriggerList → Task 8 ✓
- §4.7 ActiveSignalEditor → Task 1 ✓
- §4.8 TradeTape → Task 6+7 ✓
- §4.9 QuoteBook → Task 3+4 ✓
- §5 Backend probe → Task 2 ✓
- §8 測試 → Task 13 ✓

**2. Placeholder scan:** 無 TBD / "add error handling" / vague references. 所有 step 都有可執行的 code 或指令。

**3. Type consistency:**
- `useTradeTape` 回傳 `TradeRow[]`，TradeTape 元件 import 並使用 ✓
- `useQuoteBook` 回傳 `QuoteBookData` (`bids/asks/lastSuccessAt/error`)，QuoteBook 元件 destructure 同名 ✓
- `subscribeTicks` (Task 5) 跟 `useTradeTape` (Task 6) 簽名一致：`(handler: (t: TickEvent) => void) => () => void` ✓
- `TopToolbar` Props 新增 `onPickSymbol`，Monitor.tsx Task 11 step 5 傳 `handleSearchPick` ✓
- `IntradayChart` Props 新增 `inWatchlist`/`onAddToWatchlist`，Monitor.tsx Task 11 step 4 傳 `inWatchlist`/`handleAddCurrent` ✓

無發現 inconsistency。
