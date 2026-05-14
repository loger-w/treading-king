# Monitor UI Tweaks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 套用 9 項即時監控頁面的反饋改動，spec 見 `docs/superpowers/specs/2026-05-14-monitor-ui-tweaks-design.md`。

**Architecture:** 增量編輯，不改 schema、不改訊號規則。後端動 2 個 route + 1 個 service；前端動 1 個 page、6 個 component、3 個 hook（含 2 個新檔）+ types 集中檔。

**Tech Stack:** FastAPI + Python 3.12（backend），React 18 + TypeScript + Vite + Tailwind 3（frontend），Supabase（symbol 表），Fubon Neo SDK（即時行情）。

**Test Strategy:** Codebase 無測試基建（無 `tests/` 目錄、無 vitest）。每個 backend 改動以 `curl` smoke test 驗證；每個 frontend 改動以 `npm run build`（型別檢查）+ browser 手動 smoke 驗證。

**Reference paths:**
- Backend root: `C:\side-project\treading-king\backend`
- Frontend root: `C:\side-project\treading-king\frontend`
- 環境變數：`backend\.env` 已存在；`BFF_API_KEY` 為前後端共用 key

**啟動指令（多次會用）:**
- Backend dev: `cd backend; .venv\Scripts\activate; uvicorn main:app --reload --port 8000`
- Frontend dev: `cd frontend; npm run dev`
- Frontend build (型別檢查): `cd frontend; npm run build`

---

## Task 1: Backend — 新增 `POST /api/quotes/snapshot`

**Files:**
- Modify: `backend/routes/quote.py`

- [ ] **Step 1: 加 import + Pydantic request model + 新 handler**

在檔頂 import 區補：

```python
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.fubon_client import get_fubon
```

在檔尾既有 `get_quote` 之後加：

```python
class SnapshotRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=50)


@router.post("/api/quotes/snapshot")
async def snapshot_quotes(req: SnapshotRequest) -> dict:
    """批次拿多檔的 prev_close / last_price。

    - 任一 symbol 格式錯：整批 400
    - Fubon SDK degraded：整批 503
    - 單一 symbol 富邦失敗：該 row 兩欄都為 None，其他正常回
    """
    for sym in req.symbols:
        if not SYMBOL_RE.match(sym):
            raise HTTPException(400, f"Invalid symbol format: {sym!r}")

    fubon = get_fubon()
    if fubon.status.value != "ok":
        raise HTTPException(
            503,
            detail={
                "error": "fubon_unavailable",
                "fubon_status": fubon.status.value,
                "last_error": fubon.last_error,
            },
        )

    async def one(sym: str) -> dict:
        try:
            r = await fubon.intraday_quote(sym)
            return {
                "symbol": sym,
                "prev_close": r.get("previousClose"),
                "last_price": r.get("lastPrice"),
            }
        except Exception as e:
            logger.warning("snapshot intraday_quote(%s) failed: %s", sym, e)
            return {"symbol": sym, "prev_close": None, "last_price": None}

    results = await asyncio.gather(*(one(s) for s in req.symbols))
    return {"quotes": results}
```

- [ ] **Step 2: 重啟 backend 並 smoke test 正常路徑**

```powershell
# 停掉舊的（如果有）、重啟
# 在 backend 視窗 Ctrl+C 後重跑：
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

開另一個 PowerShell：

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe -X POST http://localhost:8000/api/quotes/snapshot `
  -H "X-API-Key: $key" -H "Content-Type: application/json" `
  -d '{\"symbols\":[\"2330\",\"2454\"]}'
```

預期：HTTP 200，body 形如 `{"quotes":[{"symbol":"2330","prev_close":605.0,"last_price":607.0},{"symbol":"2454",...}]}`（盤後可能 last_price=prev_close）。

- [ ] **Step 3: Smoke test 異常路徑**

```powershell
# (a) symbols 空陣列 → 422 (Pydantic min_length=1)
curl.exe -X POST http://localhost:8000/api/quotes/snapshot `
  -H "X-API-Key: $key" -H "Content-Type: application/json" -d '{\"symbols\":[]}'

# (b) symbols 超過 50 → 422
$big = (1..51 | ForEach-Object { '"' + (1000+$_) + '"' }) -join ','
curl.exe -X POST http://localhost:8000/api/quotes/snapshot `
  -H "X-API-Key: $key" -H "Content-Type: application/json" -d "{`"symbols`":[$big]}"

# (c) 格式錯的 symbol → 400
curl.exe -X POST http://localhost:8000/api/quotes/snapshot `
  -H "X-API-Key: $key" -H "Content-Type: application/json" -d '{\"symbols\":[\"abc\"]}'

# (d) 不存在但格式合法的 symbol → 200，prev_close/last_price 為 null
curl.exe -X POST http://localhost:8000/api/quotes/snapshot `
  -H "X-API-Key: $key" -H "Content-Type: application/json" -d '{\"symbols\":[\"9999\"]}'
```

預期：(a)(b) 422、(c) 400、(d) 200 + null。

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add backend/routes/quote.py
git commit -m "feat(backend): add POST /api/quotes/snapshot for batch prev_close + last_price"
```

---

## Task 2: Backend — `/api/quote/{symbol}` 砍 `total` 欄位

**Files:**
- Modify: `backend/routes/quote.py` (handler `get_quote`)

- [ ] **Step 1: 改 handler return 只挑欄位**

定位既有 `get_quote`，把整個 handler body 改成：

```python
@router.get("/api/quote/{symbol}")
async def get_quote(symbol: str) -> dict:
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(400, f"Invalid symbol format: {symbol!r}")

    fubon = get_fubon()
    if fubon.status.value != "ok":
        raise HTTPException(
            503,
            detail={
                "error": "fubon_unavailable",
                "fubon_status": fubon.status.value,
                "last_error": fubon.last_error,
            },
        )

    try:
        result = await fubon.intraday_quote(symbol)
        return {
            "bids": result.get("bids", []),
            "asks": result.get("asks", []),
        }
    except Exception as e:
        logger.warning("intraday_quote(%s) failed: %s", symbol, e)
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})
```

- [ ] **Step 2: Smoke test**

backend 已在 `--reload` 模式自動重啟。執行：

```powershell
curl.exe -s http://localhost:8000/api/quote/2330 -H "X-API-Key: $key"
```

預期：response 只有 `bids` + `asks`，**沒有** `total` / `lastPrice` / `info` 等欄位。

- [ ] **Step 3: Commit**

```powershell
git add backend/routes/quote.py
git commit -m "refactor(backend): strip total field from /api/quote response (UI no longer uses inner/outer %)"
```

---

## Task 3: Backend — WS tick broadcast payload 加 `size`

**Files:**
- Modify: `backend/services/fubon_ws.py`

- [ ] **Step 1: 編輯 broadcast payload**

定位 `_handle_raw_message`（line ~188-220）裡 broadcast 那行。整段現況：

```python
        # 3. broadcast tick 給前端 — 分時走勢圖最後一根 K 棒即時更新
        if self._loop is not None:
            tick_payload = {"event": "tick", "data": {"symbol": symbol, "price": float(price)}}
            self._loop.call_soon_threadsafe(
                asyncio.create_task, get_broadcaster().broadcast(tick_payload)
            )
```

改成：

```python
        # 3. broadcast tick 給前端 — 分時走勢圖最後一根 K 棒即時更新 + 明細張數
        if self._loop is not None:
            tick_payload = {
                "event": "tick",
                "data": {"symbol": symbol, "price": float(price), "size": int(size)},
            }
            self._loop.call_soon_threadsafe(
                asyncio.create_task, get_broadcaster().broadcast(tick_payload)
            )
```

注意：`size` 變數已經在第 201 行 `size = data.get("size", 0)` 取到，直接用即可。

- [ ] **Step 2: Smoke test（需盤中 / 模擬 tick）**

開盤中重啟 backend 後，frontend 連 ws 後 backend log 應每筆 tick 印出 `conn[0] subscribed: ...`。
要直接看 ws payload 可用瀏覽器 devtools → Network → WS → frame 看 incoming message，應出現 `{"event":"tick","data":{"symbol":"2330","price":607.0,"size":3}}`。

非盤中時用 backend log 確認 broadcast 不會 error 即可：

```powershell
# backend 視窗應只看到 startup OK 訊息，沒有 traceback
```

- [ ] **Step 3: Commit**

```powershell
git add backend/services/fubon_ws.py
git commit -m "feat(backend): include size in WS tick broadcast (for trade tape lot column)"
```

---

## Task 4: Frontend — `lib/api.ts` types

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 砍 `QuoteResponse.total`**

定位 `QuoteResponse` interface（line ~38-45），整個改成：

```typescript
// ---------------------------------------------------------------------------
// Quote — QuoteBook 用的五檔欄位
// ---------------------------------------------------------------------------

export interface QuoteResponse {
  bids?: Array<{ price: number; size: number }>;
  asks?: Array<{ price: number; size: number }>;
}
```

（移除 `total` 整個欄位 + 改注解）

- [ ] **Step 2: 新增 Snapshot types + api method**

在 `IntradayCandlesResponse` interface 之後（line ~158）加：

```typescript
export interface SnapshotRow {
  symbol: string;
  prev_close: number | null;
  last_price: number | null;
}

export interface SnapshotResponse {
  quotes: SnapshotRow[];
}
```

在 `api` 物件內、`cdp` 之後加 method：

```typescript
  quotesSnapshot: (symbols: string[]) =>
    fetchJSON<SnapshotResponse>("/api/quotes/snapshot", {
      method: "POST",
      body: JSON.stringify({ symbols }),
    }),
```

- [ ] **Step 3: 型別檢查**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期：build 成功，**但**會出現一些 error，因為 `useQuoteBook` 還在讀 `r.total?.tradeVolumeAtBid` 等（下面 Task 6 處理）。先確認錯誤集中在 `useQuoteBook.ts`，其他檔沒事。

如果 error 還在其他檔（非 useQuoteBook），表示動到了不該動的地方，回頭看。

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/lib/api.ts
git commit -m "refactor(frontend): drop QuoteResponse.total; add SnapshotResponse + api.quotesSnapshot"
```

---

## Task 5: Frontend — `TickEvent` + `TradeTape` 加 `size`

**Files:**
- Modify: `frontend/src/hooks/useSignalsStream.ts`
- Modify: `frontend/src/hooks/useTradeTape.ts`

- [ ] **Step 1: `useSignalsStream.ts` 的 TickEvent 加欄位**

把 `TickEvent` interface（line ~8-11）改成：

```typescript
export interface TickEvent {
  symbol: string;
  price: number;
  size: number;
}
```

把 `ws.onmessage` 裡 tick 處理那行（line ~60-63）改成：

```typescript
        } else if (msg.event === "tick") {
          const tick: TickEvent = {
            symbol: msg.data.symbol,
            price: msg.data.price,
            size: msg.data.size ?? 0,
          };
          onTickRef.current?.(tick.symbol, tick.price);
          tickBus.dispatchEvent(new CustomEvent<TickEvent>("tick", { detail: tick }));
        }
```

（向後相容：舊版 broadcast 沒帶 size 時退 0）

- [ ] **Step 2: `useTradeTape.ts` 加 size 欄位**

把 `TradeRow` interface（line ~7-11）改成：

```typescript
export interface TradeRow {
  time: Date;
  price: number;
  size: number;
  side: TradeSide;
}
```

把 subscribe handler 裡 `setRows` 那行（line ~54）改成：

```typescript
      setRows((prev) => [{ time: new Date(), price: t.price, size: t.size, side }, ...prev].slice(0, MAX_TICKS));
```

- [ ] **Step 3: 型別檢查**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期：`TradeTape.tsx` 會出現 error（用到 row 但 UI 還沒加 column）— 這是預期的，下面 Task 12 處理。確認其他檔沒事。

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/hooks/useSignalsStream.ts frontend/src/hooks/useTradeTape.ts
git commit -m "feat(frontend): propagate size through tickBus into TradeRow"
```

---

## Task 6: Frontend — `useQuoteBook` 砍 inner/outer state

**Files:**
- Modify: `frontend/src/hooks/useQuoteBook.ts`

- [ ] **Step 1: 移除 inner/outer state + setter**

整檔重寫成：

```typescript
import { useEffect, useRef, useState } from "react";
import { api, type QuoteResponse } from "../lib/api";

const POLL_MS = 1000;

export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  error: string | null;
}

/**
 * Poll /api/quote/{symbol} 每 1 秒，背景 tab 時暫停。
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
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!symbol) {
      setBids([]); setAsks([]);
      setError(null);
      return;
    }

    // 切 symbol：清舊資料 + cancel pending
    abortRef.current?.abort();
    setBids([]); setAsks([]);
    setError(null);

    async function fetchOnce() {
      if (document.hidden) return;
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const r: QuoteResponse = await api.quote(symbol!);
        if (ctrl.signal.aborted) return;
        setBids(r.bids ?? []);
        setAsks(r.asks ?? []);
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

  return { bids, asks, error };
}
```

- [ ] **Step 2: 型別檢查**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期：`QuoteBook.tsx` 會 error（仍解構 `innerVolume, outerVolume`）— Task 14 處理。其他檔沒事。

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/hooks/useQuoteBook.ts
git commit -m "refactor(frontend): drop inner/outer state from useQuoteBook (API removed total)"
```

---

## Task 7: Frontend — 新 hook `useSnapshotCache`

**Files:**
- Create: `frontend/src/hooks/useSnapshotCache.ts`

- [ ] **Step 1: 寫整個 hook**

新檔內容：

```typescript
import { useEffect, useState } from "react";
import { api } from "../lib/api";

export interface SnapshotEntry {
  prevClose: number | null;
  lastPrice: number | null;
}

// Module-level cache — 跨 hook instance 共用（watchlist + trigger 同 symbol 不重抓）
const cache = new Map<string, SnapshotEntry>();
const inFlight = new Map<string, Promise<void>>();

/**
 * 對給定的 symbols 集合，把沒見過的湊批打 /api/quotes/snapshot 並寫入 cache。
 * 回傳當前 cache 中對應 symbols 的快照（snake_case → camelCase 已轉）。
 *
 * - cache 是 module-level：watchlist 抓過後 trigger 用同 symbol 不會重打
 * - in-flight dedup：同一個 symbol 同時被多個 hook 要時，只打一次 API
 * - 沒做 TTL：當日盤中 prev_close 不會變，盤後新 trading day 才會差；
 *   過 24h 仍命中舊 cache → reload 頁面解
 */
export function useSnapshotCache(symbols: string[]): Record<string, SnapshotEntry> {
  const [version, setVersion] = useState(0);  // bump 觸發 re-render

  useEffect(() => {
    const missing = symbols.filter((s) => !cache.has(s) && !inFlight.has(s));
    if (missing.length === 0) return;

    const promise = (async () => {
      try {
        const r = await api.quotesSnapshot(missing);
        for (const row of r.quotes) {
          cache.set(row.symbol, {
            prevClose: row.prev_close,
            lastPrice: row.last_price,
          });
        }
      } catch (e) {
        console.warn("useSnapshotCache fetch failed:", e);
      } finally {
        for (const s of missing) inFlight.delete(s);
        setVersion((v) => v + 1);
      }
    })();

    for (const s of missing) inFlight.set(s, promise);
  }, [symbols.join(",")]);  // deps 用 stringified 避免每 render 變陣列實例就 re-fire

  // 從 cache 組 view（沒 cache 的 entry 兩欄 null）
  const out: Record<string, SnapshotEntry> = {};
  for (const s of symbols) {
    out[s] = cache.get(s) ?? { prevClose: null, lastPrice: null };
  }
  // version 變數讓 React 認為 deps 變了 — 即使 out 物件 ref 同
  void version;
  return out;
}
```

- [ ] **Step 2: 型別檢查**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期：build 成功（hook 內部完備，沒人 import 時 build 不會 fail）。
若 ts 抱怨 `out` ref instability 影響 callers，下游再處理；目前無 caller。

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/hooks/useSnapshotCache.ts
git commit -m "feat(frontend): add useSnapshotCache hook with module-level prev_close cache"
```

---

## Task 8: Frontend — 新 hook `useWatchlistQuotes`

**Files:**
- Create: `frontend/src/hooks/useWatchlistQuotes.ts`

- [ ] **Step 1: 寫整個 hook**

新檔內容：

```typescript
import { useEffect, useState } from "react";
import { subscribeTicks } from "./useSignalsStream";
import { useSnapshotCache } from "./useSnapshotCache";

export interface WatchlistQuote {
  price: number | null;     // 最新 price（snapshot.last_price 或 WS tick）
  prevClose: number | null;
  changePct: number | null; // (price - prevClose) / prevClose * 100
}

/**
 * Watchlist 即時報價聚合：
 *  - prevClose / 初始 price 從 useSnapshotCache 拿
 *  - 後續 price 由 module-level tickBus（useSignalsStream 已導出）即時推
 *  - changePct 由前兩者算
 *
 * Symbols 變動時：自動拉新檔 snapshot；舊檔的 livePrice 保留。
 */
export function useWatchlistQuotes(symbols: string[]): Record<string, WatchlistQuote> {
  const snapshot = useSnapshotCache(symbols);
  // livePrice 用 state — tick 進來就更新該 symbol
  const [livePrices, setLivePrices] = useState<Record<string, number>>({});

  useEffect(() => {
    const unsub = subscribeTicks((t) => {
      setLivePrices((prev) => ({ ...prev, [t.symbol]: t.price }));
    });
    return unsub;
  }, []);

  const out: Record<string, WatchlistQuote> = {};
  for (const sym of symbols) {
    const entry = snapshot[sym] ?? { prevClose: null, lastPrice: null };
    const price = livePrices[sym] ?? entry.lastPrice;
    const prevClose = entry.prevClose;
    const changePct =
      price !== null && prevClose !== null && prevClose !== 0
        ? ((price - prevClose) / prevClose) * 100
        : null;
    out[sym] = { price, prevClose, changePct };
  }
  return out;
}
```

- [ ] **Step 2: 型別檢查**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期：build 成功。

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/hooks/useWatchlistQuotes.ts
git commit -m "feat(frontend): add useWatchlistQuotes (snapshot prev_close + live WS price)"
```

---

## Task 9: Frontend — Monitor 鎖視窗高度

**Files:**
- Modify: `frontend/src/pages/Monitor.tsx`

- [ ] **Step 1: 改 `<main>` + 內層容器結構**

定位 `<main>` 那段（line ~121-214），把整段改成：

```tsx
      <main className="h-screen flex flex-col overflow-hidden">
        <div className="mx-auto w-full max-w-[1960px] px-9 pt-3 pb-12 max-md:px-6 flex-1 min-h-0">
          <div
            className="grid items-stretch gap-6 max-[1200px]:grid-cols-1 h-full"
            style={{ gridTemplateColumns: "300px 340px 1fr 300px" }}
          >
```

關鍵改動：
- `<main>` 加 `h-screen flex flex-col overflow-hidden`
- 內層 `mx-auto` 容器加 `w-full flex-1 min-h-0`
- grid 加 `h-full`

注意：`<main>` 包進 `<>` (Fragment) 第二個子元素，與 `<TopToolbar>` 並列。`h-screen` = 100vh，但 TopToolbar 也在頁面上，所以實際 grid 區會被擠出視窗——這是預期的（toolbar 約 80px）。如果想完全填滿，後續再用 `h-[calc(100vh-80px)]`。本步先 `h-screen + overflow-hidden`，三欄各自 `overflow-y-auto` 已就位（line 138, 160, 207），會內捲。

- [ ] **Step 2: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173：
- 加入 5+ 檔自選股 → 自選欄應內捲、不撐高整頁
- 觸發歷史 / 明細欄同理
- 整頁不該有外層 scroll bar（toolbar 之外）

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/pages/Monitor.tsx
git commit -m "fix(frontend): lock Monitor to viewport height; columns scroll internally"
```

---

## Task 10: Frontend — Watchlist 加股價 + 漲幅

**Files:**
- Modify: `frontend/src/pages/Monitor.tsx`
- Modify: `frontend/src/components/WatchlistWithChips.tsx`

- [ ] **Step 1: `Monitor.tsx` import hook + 傳 prop**

在 import 區加：

```tsx
import { useWatchlistQuotes } from "../hooks/useWatchlistQuotes";
```

在 `watchlistSymbols` useMemo 之後加一行：

```tsx
  const watchlistQuotes = useWatchlistQuotes(watchlistSymbols);
```

在 `<WatchlistWithChips ... />`（line ~161-168）多傳 prop：

```tsx
                <WatchlistWithChips
                  items={watchlistItems}
                  rules={rules}
                  hitCounts={counts}
                  quotes={watchlistQuotes}
                  selectedSymbol={selected}
                  onSelect={setSelected}
                  onRemove={remove}
                />
```

- [ ] **Step 2: `WatchlistWithChips.tsx` 加 quote 顯示**

`Props` interface 加：

```typescript
import { type ActiveSignal, type WatchlistRow } from "../lib/api";
import { SignalChip } from "./SignalChip";
import { type HitCounts } from "../hooks/useTodayHits";
import { type WatchlistQuote } from "../hooks/useWatchlistQuotes";

interface Props {
  items: WatchlistRow[];
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  quotes: Record<string, WatchlistQuote>;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
  onRemove: (symbol: string) => void;
}
```

`export function WatchlistWithChips({...})` 解構加 `quotes`：

```typescript
export function WatchlistWithChips({
  items, rules, hitCounts, quotes, selectedSymbol, onSelect, onRemove,
}: Props) {
```

把 row 內 `<span className="block text-[19px] ...">{it.symbol}</span>` + name 那兩行（line ~80-81）整段改成：

```tsx
            <div className="flex items-baseline justify-between gap-2 mb-0.5">
              <span className="text-[19px] font-medium text-ink">{it.symbol}</span>
              {(() => {
                const q = quotes[it.symbol];
                const price = q?.price;
                const pct = q?.changePct;
                const dirCls = pct == null
                  ? "text-ink-dim"
                  : pct > 0 ? "text-bull"
                  : pct < 0 ? "text-bear"
                  : "text-ink-muted";
                return (
                  <span className={`text-right text-sm tabular-nums ${dirCls}`}>
                    {price != null ? price.toFixed(2) : "—"}
                    {pct != null && (
                      <span className="ml-1.5 text-xs">
                        {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                      </span>
                    )}
                  </span>
                );
              })()}
            </div>
            <div className="text-[15px] text-ink-muted mb-2.5">{it.name ?? "—"}</div>
```

- [ ] **Step 3: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173 → 自選清單每 row 右側應出現「605.00 +1.85%」格式的字。
- 開盤中：價會跳動（WS tick）
- 盤後 / Fubon degraded：顯示 `—`

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/pages/Monitor.tsx frontend/src/components/WatchlistWithChips.tsx
git commit -m "feat(frontend): show price + change% per watchlist row"
```

---

## Task 11: Frontend — TriggerList 字加大 + 加漲幅

**Files:**
- Modify: `frontend/src/pages/Monitor.tsx`
- Modify: `frontend/src/components/TriggerList.tsx`

- [ ] **Step 1: `Monitor.tsx` import + 算 trigger symbols + 拉 snapshot**

在 import 區加：

```tsx
import { useSnapshotCache } from "../hooks/useSnapshotCache";
```

在 `watchlistQuotes` 那行之後加：

```tsx
  const triggerSymbols = useMemo(() => {
    const set = new Set<string>();
    for (const h of historicalToday) set.add(h.symbol);
    for (const r of recent) set.add(r.symbol);
    return Array.from(set);
  }, [historicalToday, recent]);
  const triggerSnapshot = useSnapshotCache(triggerSymbols);
  const prevCloseMap = useMemo(() => {
    const m: Record<string, number | null> = {};
    for (const s of triggerSymbols) m[s] = triggerSnapshot[s]?.prevClose ?? null;
    return m;
  }, [triggerSymbols, triggerSnapshot]);
```

在 `<TriggerList ... />`（line ~139-146）加 prop：

```tsx
                <TriggerList
                  historical={historicalToday}
                  recent={recent}
                  rules={rules}
                  symbolNames={symbolNames}
                  prevCloseMap={prevCloseMap}
                  selectedSymbol={selected}
                  onSelect={handleSelect}
                />
```

- [ ] **Step 2: `TriggerList.tsx` 字級 + 加漲幅 column**

`Props` interface 加 `prevCloseMap`：

```typescript
interface Props {
  historical: SignalLogRow[];
  recent: SignalEvent["data"][];
  rules: ActiveSignal[];
  symbolNames: Record<string, string | null>;
  prevCloseMap: Record<string, number | null>;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
}
```

`export function TriggerList({...})` 解構：

```typescript
export function TriggerList({
  historical, recent, rules, symbolNames, prevCloseMap, selectedSymbol, onSelect,
}: Props) {
```

把 row body（line ~95-113）整塊改成：

```tsx
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-serif font-bold text-lg tracking-[-0.2px]">
                {r.symbol}
                {r.name && (
                  <span className="ml-1.5 font-serif italic font-normal text-sm text-ink-muted">
                    {r.name}
                  </span>
                )}
              </span>
              <span className="text-sm text-ink-dim tabular-nums">{r.time}</span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-sm text-ink-dim uppercase tracking-[0.5px]">
                {r.ruleName}
              </span>
              <span className="flex items-baseline gap-2">
                {(() => {
                  const prev = prevCloseMap[r.symbol];
                  if (prev == null || prev === 0) return null;
                  const pct = ((r.price - prev) / prev) * 100;
                  const cls = pct > 0 ? "text-bull" : pct < 0 ? "text-bear" : "text-ink-muted";
                  return (
                    <span className={`text-xs tabular-nums ${cls}`}>
                      {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
                    </span>
                  );
                })()}
                <span className="text-base tabular-nums text-bull font-medium">
                  {r.price.toFixed(2)}
                </span>
              </span>
            </div>
```

注意：價位現在固定用 `text-bull`（保持與既有設計一致），漲幅另外用實際正負紅綠。如要兩者一致紅綠，把 `text-bull` 換成跟 `cls` 同邏輯算 — 但 spec 沒指定，保守維持。

- [ ] **Step 3: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173：
- 觸發歷史每 row 字應變大
- 第二行除了原本的 rule name + price，多一個小字漲幅（紅綠）
- prev_close 沒拉到的 symbol（盤後 / 無資料）→ 漲幅不顯示，但 row 其他字仍正常

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/pages/Monitor.tsx frontend/src/components/TriggerList.tsx
git commit -m "feat(frontend): bump TriggerList font sizes + show change% vs prev_close"
```

---

## Task 12: Frontend — TradeTape 加張數欄

**Files:**
- Modify: `frontend/src/components/TradeTape.tsx`

- [ ] **Step 1: 加 4 欄 grid + size column**

整檔改成：

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
 * 4 欄：時間 / 價 / 向 / 張數。Header sticky，內容滾動。
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
      <div className="grid grid-cols-[60px_1fr_28px_56px] gap-1.5 px-1 py-2 border-b border-line-strong text-2xs uppercase tracking-[1.2px] text-ink-dim">
        <div>時間</div>
        <div className="text-right">價</div>
        <div className="text-center">向</div>
        <div className="text-right">張</div>
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-10 text-center text-ink-dim font-serif italic text-sm">
          等待第一筆成交…
        </div>
      ) : (
        rows.map((r, i) => (
          <div
            key={i}
            className="grid grid-cols-[60px_1fr_28px_56px] gap-1.5 px-1 py-1.5 border-b border-line text-xs tabular-nums"
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
            <span className="text-right text-ink-muted">
              {r.size > 0 ? r.size.toLocaleString() : "—"}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
```

- [ ] **Step 2: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173 + 開盤中 → 明細應出現 4 欄；張數隨 tick 顯示（從 backend Task 3 來）。
盤後 → 沒有新 tick，明細是空的或顯示歷史；張數欄為 `—`（size=0 fallback）。

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/components/TradeTape.tsx
git commit -m "feat(frontend): add lot-size column to TradeTape"
```

---

## Task 13: Frontend — IntradayChart CDP/VWAP/字級

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: CDP label 砍 key prefix**

定位 CDP 渲染區塊（line ~232-248）裡的 `<text>`，把：

```tsx
                  <text x={CHART_W - PAD_R + 4} y={scaleY(cdp[k]) + 3} textAnchor="start"
                    className="fill-accent text-[10px] uppercase">
                    {k.toUpperCase()} {formatTickPrice(cdp[k])}
                  </text>
```

改成：

```tsx
                  <text x={CHART_W - PAD_R + 4} y={scaleY(cdp[k]) + 3} textAnchor="start"
                    className="fill-accent text-[12px] tabular-nums">
                    {formatTickPrice(cdp[k])}
                  </text>
```

（`uppercase` 沒意義了所以拿掉、改 `tabular-nums`）

- [ ] **Step 2: VWAP 加 label**

在 VWAP polyline 渲染（line ~250-254）之後加一個 label：

```tsx
          {/* VWAP */}
          {showVwap && polyVwap && (
            <polyline points={polyVwap} fill="none"
              stroke="var(--color-ink-dim, #8a8273)" strokeWidth="1" strokeDasharray="3 2" />
          )}
          {showVwap && candles.length > 0 && (() => {
            const lastIdx = candles.length - 1;
            const lastAvg = candles[lastIdx].average;
            return (
              <text x={scaleX(lastIdx) + 4} y={scaleY(lastAvg) + 3} textAnchor="start"
                className="fill-ink-dim text-[12px] tabular-nums">
                {formatTickPrice(lastAvg)}
              </text>
            );
          })()}
```

- [ ] **Step 3: 字級加大 — Y 軸、X 軸、今日高低、hover**

在以下位置把 `text-[10px]` 改成 `text-[12px]`：

- Y 軸 baseline label（line ~224）：`text-[10px] tabular-nums` → `text-[12px] tabular-nums`
- 今日最高（line ~282）：`text-[10px] tabular-nums font-medium` → `text-[12px] tabular-nums font-medium`
- 今日最低（line ~296）：`text-[10px] tabular-nums font-medium` → `text-[12px] tabular-nums font-medium`
- X 軸時間（line ~313）：`text-[10px] tabular-nums` → `text-[12px] tabular-nums`
- Hover Y label（line ~343）：`text-[10px] tabular-nums font-medium` → `text-[12px] tabular-nums font-medium`
- Hover X label（line ~351）：`text-[10px] tabular-nums font-medium` → `text-[12px] tabular-nums font-medium`

逐一 Edit replacement（每次只 replace 該行原始字串，避免衝突）。可用 grep 確認：

```powershell
cd C:\side-project\treading-king
Select-String -Path frontend\src\components\IntradayChart.tsx -Pattern 'text-\[10px\]'
```

應該 0 筆（VWAP label 也是 `text-[12px]`）。

- [ ] **Step 4: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173 + 選一檔自選股 + 開啟 CDP toggle：
- Y 軸 / X 軸字明顯變大
- CDP 線右側只顯示價位（無 AH/NH 等前綴）
- VWAP 線（虛線）右端有灰色價位 label
- hover 圖區的 X / Y 黑底白字 label 字變大

- [ ] **Step 5: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(frontend): drop CDP key prefix, add VWAP price label, bump chart text to 12px"
```

---

## Task 14: Frontend — QuoteBook 砍內外盤

**Files:**
- Modify: `frontend/src/components/QuoteBook.tsx`

- [ ] **Step 1: 砍內外盤區塊 + 解構 + 計算**

`useQuoteBook` 解構（line ~18）改成：

```tsx
  const { bids, asks, error } = useQuoteBook(symbol);
```

砍掉 `sumIO / innerPct / outerPct` 計算（line ~33-34，整段）。

砍掉內外盤 bar + 量字整塊（line ~47-60）。即從：

```tsx
      <div className="mb-4">
        <div className="flex items-baseline justify-between text-2xs uppercase tracking-[1px] mb-1">
          ... 內 X% / 外 X%
        </div>
        ...
        <div className="flex items-baseline justify-between text-xs text-ink-muted tabular-nums mt-1">
          ...
        </div>
      </div>
```

整段刪除。

預期 QuoteBook 最後長這樣（給對齊參考）：

```tsx
import { useQuoteBook } from "../hooks/useQuoteBook";

interface Props {
  symbol: string | null;
}

/**
 * 委買賣五檔 — 走 REST poll (useQuoteBook)，1 秒更新一次。
 *
 * Totals row：委買總量(紅大字、左)/ 委賣總量(綠大字、右)— 五檔加總。
 * Body：左 5 檔買、右 5 檔賣，量條 width 用兩邊共用 maxQty 做 normalize。
 */
export function QuoteBook({ symbol }: Props) {
  const { bids, asks, error } = useQuoteBook(symbol);

  if (!symbol) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        ← 從自選 / 搜尋挑一檔看五檔
      </div>
    );
  }

  const maxQty = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));
  const bidTotal = bids.reduce((sum, b) => sum + b.size, 0);
  const askTotal = asks.reduce((sum, a) => sum + a.size, 0);

  return (
    <div className="border border-line bg-bg-card/50 p-[22px]">
      <h3 className="font-serif font-bold text-lg tracking-[-0.3px] pb-2.5 mb-3 border-b border-line">
        委買賣 五檔
        {error && <span className="ml-3 text-2xs uppercase tracking-[1px] text-bear">· 更新失敗</span>}
      </h3>

      <div className="flex items-baseline justify-between mb-4">
        <span className="text-2xl font-bold text-bull tabular-nums tracking-tight">
          {bidTotal.toLocaleString()}
          <span className="ml-1.5 text-sm font-normal text-bull/70">張</span>
        </span>
        <span className="text-2xl font-bold text-bear tabular-nums tracking-tight">
          {askTotal.toLocaleString()}
          <span className="ml-1.5 text-sm font-normal text-bear/70">張</span>
        </span>
      </div>

      <div className="grid grid-cols-2 gap-8">
        <div>
          {bids.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            bids.map((b, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 right-0 bg-bull/10 pointer-events-none"
                  style={{ width: `${(b.size / maxQty) * 100}%` }}
                />
                <span className="relative z-[1] text-ink-muted">{b.size} 張</span>
                <span className="relative z-[1] text-right text-bull font-medium">{b.price.toFixed(2)}</span>
              </div>
            ))
          )}
        </div>
        <div>
          {asks.length === 0 ? (
            <div className="text-xs text-ink-dim italic py-2">—</div>
          ) : (
            asks.map((a, i) => (
              <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
                <span
                  className="absolute top-0 bottom-0 left-0 bg-bear/10 pointer-events-none"
                  style={{ width: `${(a.size / maxQty) * 100}%` }}
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

- [ ] **Step 2: 型別檢查 + 手動 smoke**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
npm run dev
```

開 http://localhost:5173 → 五檔區應**沒有**內 X% / 外 X% bar、也**沒有**累積量字。
剩下的：title、委買/委賣總量大字、左右五檔。

- [ ] **Step 3: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/components/QuoteBook.tsx
git commit -m "feat(frontend): remove inner/outer % bar + volume from QuoteBook"
```

---

## Task 15: 全域整合 smoke test

**Files:**
- 無編輯

- [ ] **Step 1: 重啟 backend + frontend，全項驗收**

```powershell
# 視窗 A：backend
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000

# 視窗 B：frontend
cd C:\side-project\treading-king\frontend
npm run dev
```

開 http://localhost:5173，照下表逐項對 spec：

| # | 驗證 | 怎麼看 |
|---|------|--------|
| 1 | CDP 只顯示價位 | 開分時 → toggle CDP → 線右側 label 形如 `605.00`（無 `AH` / `NH`） |
| 2 | 均線（VWAP）加價位 | VWAP 虛線右端有灰色價位字 |
| 3 | 自選清單股價 + 漲幅 | 每 row 右側「605.00 +1.85%」格式（紅綠按正負） |
| 4 | 觸發歷史字加大 + 漲幅 | row 字比改前明顯大；第二行 price 旁有 `+x.xx%` 小字 |
| 5 | 三欄不超過畫面 | 加 6+ 檔自選 → 該欄出現內捲，但整頁無外層 scroll bar |
| 6 | 內外% 砍 | QuoteBook 區無 內/外 bar、無累積量字；DevTools Network 看 `/api/quote/2330` 回應**沒有** `total` 欄位 |
| 7 | 明細加張數 | 開盤中：明細出現 4 欄；右欄為張數 |
| 9 | 分時數字加大 | Y 軸 / X 軸 / 今日高低 / hover label 字比改前明顯大 |

- [ ] **Step 2: 整合 commit（如有未 commit 的 lint fix 等小尾巴）**

```powershell
git -C C:\side-project\treading-king status
# 應該是 clean 的；如果有殘留就最後一個 commit 收尾
```

- [ ] **Step 3: 完成**

整套 9 項改動完成。如有反饋再開新 spec。

---

## 自我檢查（plan 寫完後）

**Spec coverage（對照 spec 9 項）:**
- #1 CDP 顯示價位 → Task 13 Step 1
- #2 均線加價位 → Task 13 Step 2
- #3 自選股價 + 漲幅 → Task 1 (backend) + Task 4 (api) + Task 7-8 (hooks) + Task 10 (UI)
- #4 觸發歷史字 + 漲幅 → Task 1 (backend) + Task 7 (hook) + Task 11 (UI)
- #5 三欄不超過畫面 → Task 9
- #6 內外% 砍 → Task 2 (backend) + Task 4 (api) + Task 6 (hook) + Task 14 (UI)
- #7 明細張數 → Task 3 (backend) + Task 5 (hook) + Task 12 (UI)
- #8 純問答（已在 spec 回答）→ 無 task
- #9 分時數字加大 → Task 13 Step 3

**Type consistency:**
- `SnapshotRow` 在 backend 是 snake_case (`prev_close`, `last_price`)；frontend types 用 snake_case（與既有 `IntradayCandlesResponse.prev_close` 一致）；hook 邊界轉成 camelCase（`prevClose`, `lastPrice`）— 一致。
- `TickEvent` 同步加 `size: number`，兩端都改（Task 3 backend、Task 5 frontend）。
- `TradeRow.size` 預設 0（向後相容舊 broadcast）。
