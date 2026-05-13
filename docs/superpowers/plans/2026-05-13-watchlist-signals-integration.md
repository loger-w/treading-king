# Watchlist + Signals 整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `pages/Watchlist.tsx` + `pages/Signals.tsx` 合併成單一「即時監控」頁，加 Scope chip / 命中置頂 / 觸發歷史全寬連動 / 規則 Dialog，並修 backend `routes/watchlist.py` 不刷新 signal_engine field_cache 的 bug。

**Architecture:** Frontend 用一個 `Monitor.tsx` 組合 watchlist + chart + history + dialog，靠 `selectedSymbol` state 串接三處同步。新 hook `useTodayHits` 管當日命中累計（init 打 `GET /api/signals/today_counts` 拿基準、WS event 累加）。Backend 在 watchlist add/remove 結尾呼叫 `signal_engine.refresh_active_signals()`，並新增 today_counts endpoint 給前端拿基準。

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v2 / supabase-py (sync via `asyncio.to_thread`) · React 18 / TypeScript / Vite / Tailwind (Editorial Dark theme) · Probe-based backend testing（無 pytest，跑 `python scripts/probe_xxx.py` 看 stdout）/ Frontend 無 unit test framework，用 `npx tsc -b` 驗 type + 盤中 manual UAT。

**Spec:** `docs/superpowers/specs/2026-05-13-watchlist-signals-integration-design.md`

---

## File Structure

### Create
| Path | Responsibility |
|---|---|
| `backend/scripts/probe_watchlist_refresh.py` | 驗 watchlist add 後 signal_engine field_cache 含新 symbol |
| `backend/scripts/probe_today_counts.py` | 驗新 endpoint 回 raw rows 正確 |
| `frontend/src/hooks/useTodayHits.ts` | 當日命中 baseline + WS bump 累計 |
| `frontend/src/components/SignalChip.tsx` | 單個規則 chip（命中時紅邊 + 上標數字） |
| `frontend/src/components/WatchlistWithChips.tsx` | 自選 list（命中置頂排序 + chip 渲染） |
| `frontend/src/components/TopToolbar.tsx` | 連線狀態 + ⚙ 訊號規則按鈕 |
| `frontend/src/components/TriggerHistoryTable.tsx` | 全寬 4-col 觸發歷史 + onSelect callback |
| `frontend/src/components/SignalRulesDialog.tsx` | 規則 CRUD modal（內嵌 ActiveSignalEditor） |
| `frontend/src/pages/Monitor.tsx` | 主整合頁 |

### Modify
| Path | 改動 |
|---|---|
| `backend/routes/watchlist.py` | POST/DELETE 結尾加 `await get_signal_engine().refresh_active_signals()` |
| `backend/routes/signals_history.py` | 加 `GET /api/signals/today_counts` |
| `frontend/src/lib/api.ts` | 加 `signals.todayCounts()` + `TodayCountsResponse` type |
| `frontend/src/App.tsx` | Nav 「自選」+「即時訊號」兩 tab 合併成「即時監控」單 tab |

### Delete (Task 11 最後做)
- `frontend/src/pages/Watchlist.tsx`
- `frontend/src/pages/Signals.tsx`

---

## Task 順序與依賴

```
Backend:
  Task 1 (watchlist bug fix) ─┐
  Task 2 (today_counts ep)   ─┤
                              │
Frontend foundation:          │
  Task 3 (api type) ──────────┘ (depends on Task 2)
  Task 4 (useTodayHits) ──── (depends on Task 3)

Frontend components (independent):
  Task 5 (SignalChip)
  Task 6 (WatchlistWithChips) ── (depends on Task 5)
  Task 7 (TopToolbar)
  Task 8 (TriggerHistoryTable)
  Task 9 (SignalRulesDialog)

Frontend integration:
  Task 10 (Monitor.tsx) ──── (depends on Task 4, 6, 7, 8, 9)
  Task 11 (App.tsx + delete old pages) ── (depends on Task 10)
```

每個 task 完成都要 `git commit`。執行時若用 subagent-driven，每 task 一次 review；若 inline，建議 Task 2 / Task 4 / Task 9 / Task 11 設 checkpoint。

---

## Task 1: Fix watchlist add/remove → refresh signal_engine field_cache

**Files:**
- Modify: `backend/routes/watchlist.py:59-89` (POST), `:92-105` (DELETE)
- Create: `backend/scripts/probe_watchlist_refresh.py`

**Why:** 目前 watchlist POST 只做 `ws_pool.subscribe()` 跟 `cdp.backfill`，沒呼叫 `signal_engine.refresh_active_signals()`。Evaluator 的 `_scope_includes` 對 `scope.type=watchlist` 用 `symbol in self._field_cache` 判定，新加 symbol 因 field_cache 沒重 fill 永遠 False → 新自選股對 scope=watchlist 訊號**不會觸發**。

- [ ] **Step 1: 寫 probe 驗預期失敗**

Create `backend/scripts/probe_watchlist_refresh.py`:

```python
"""驗 watchlist add 後 signal_engine._field_cache 含新 symbol。

預期：
1. 起初 _field_cache 不含 "2454"
2. POST /api/watchlist {symbol: "2454"} 後，等 1s
3. _field_cache 應含 "2454"（refresh_active_signals 把它載入了）
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend))

import httpx

from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase


BASE_URL = "http://localhost:8000"
TEST_SYMBOL = "2454"  # 聯發科 — 一般不在自選內


async def main() -> None:
    sb = get_supabase()
    if sb.client is None:
        print("✗ supabase 未連上"); sys.exit(1)

    # 確認至少有一個 scope=watchlist 的 enabled active_signal，否則 refresh 不會把 watchlist symbols 載入 field_cache
    res = sb.client.table("active_signals").select("id, scope, enabled").eq("enabled", True).execute()
    has_watchlist_scope = any(
        (r.get("scope") or {}).get("type") == "watchlist"
        for r in (res.data or [])
    )
    if not has_watchlist_scope:
        print("⚠ 無 enabled 的 scope=watchlist active_signal — probe 略過")
        print("  請先建一個 scope=watchlist 的 active_signal 再跑")
        sys.exit(0)

    engine = get_signal_engine()
    # 先觸發一次 refresh 拿乾淨 baseline
    await engine.refresh_active_signals()
    before = TEST_SYMBOL in engine._field_cache
    print(f"[before] {TEST_SYMBOL} in field_cache: {before}")

    # cleanup any leftover (前一輪沒清掉)
    sb.client.table("watchlist").delete().eq("symbol", TEST_SYMBOL).execute()

    print(f"[1] POST /api/watchlist {{symbol: {TEST_SYMBOL}}}")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        r = await cli.post("/api/watchlist", json={"symbol": TEST_SYMBOL})
        if r.status_code not in (201, 409):
            print(f"  ✗ unexpected status {r.status_code}: {r.text}"); sys.exit(1)
        print(f"  ✓ status {r.status_code}")

    # 等 refresh 完成（POST 結尾 await refresh_active_signals，理論上 response 回來時已完成；保險等 0.5s）
    await asyncio.sleep(0.5)

    after = TEST_SYMBOL in engine._field_cache
    print(f"[after] {TEST_SYMBOL} in field_cache: {after}")
    if not after:
        print(f"  ✗ FAIL: refresh_active_signals 沒把 {TEST_SYMBOL} 載入 field_cache"); sys.exit(1)

    # cleanup
    print(f"[cleanup] DELETE /api/watchlist/{TEST_SYMBOL}")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        await cli.delete(f"/api/watchlist/{TEST_SYMBOL}")

    print("\nAll watchlist refresh probe passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 先跑 probe 驗預期失敗（fix 之前）**

啟動 backend (`cd backend; .\.venv\Scripts\Activate.ps1; uvicorn main:app --reload --port 8000`)，另一個 terminal：

```powershell
& C:\side-project\trading-king\backend\.venv\Scripts\python.exe C:\side-project\trading-king\backend\scripts\probe_watchlist_refresh.py
```

Expected: `[after] 2454 in field_cache: False` → `✗ FAIL: refresh_active_signals 沒把 2454 載入 field_cache`，exit 1。

> **若 probe 顯示「⚠ 無 enabled 的 scope=watchlist active_signal — probe 略過」**：先用 `POST /api/active_signals` 建一條 `{name: "probe-rule", filter_json: {market: ["TWSE","OTC"], exclude_etf: true, conditions: [{field: "close", operator: "gt", value: 0}], window_conditions: [], logic: "AND"}, scope: {type: "watchlist"}, cooldown_seconds: 1800, ignore_auctions: true, enabled: true}` 再跑 probe。最後 cleanup 該規則。

- [ ] **Step 3: 套 fix — `routes/watchlist.py` POST**

Modify `backend/routes/watchlist.py`，在 POST 的 `cdp backfill` 之後（line ~87 之前 `return` 之前）加：

```python
# refresh signal_engine field_cache so scope=watchlist signals start evaluating this symbol
try:
    from services.signal_engine import get_signal_engine
    await get_signal_engine().refresh_active_signals()
except Exception as e:
    logger.warning("watchlist add: refresh signal_engine failed: %s", e)
```

完整 POST 改後（reference — 重點是 `return` 前一行加上述）：

```python
@router.post("/api/watchlist", status_code=201)
async def add_watchlist(payload: WatchlistAdd) -> dict:
    sb = _ensure_supabase()
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol").eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found", "symbol": payload.symbol})

    try:
        await asyncio.to_thread(
            lambda: sb.client.table("watchlist").insert({
                "symbol": payload.symbol, "note": payload.note,
            }).execute()
        )
    except Exception as e:
        raise HTTPException(409, detail={"error": "already_in_watchlist", "detail": str(e)})

    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id="watchlist")
    except RuntimeError as e:
        logger.warning("watchlist add: ws subscribe failed: %s", e)

    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # refresh signal_engine field_cache so scope=watchlist signals start evaluating this symbol
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}
```

- [ ] **Step 4: 套 fix — DELETE 同樣**

Modify `backend/routes/watchlist.py` DELETE，在 `cdp.discard()` 之後加 refresh：

```python
@router.delete("/api/watchlist/{symbol}", status_code=204)
async def remove_watchlist(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("watchlist").delete().eq("symbol", symbol).execute()
    )
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id="watchlist")
    except Exception as e:
        logger.warning("watchlist remove: ws unsubscribe failed: %s", e)
    get_cdp_service().discard(symbol)

    # refresh signal_engine: remove this symbol from any cached scope
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("watchlist remove: refresh signal_engine failed: %s", e)

    return None
```

- [ ] **Step 5: 重啟 backend，再跑 probe 驗 PASS**

uvicorn `--reload` 偵測到改動會自動重啟（看 stdout 確認 reload 完成）。

```powershell
& C:\side-project\trading-king\backend\.venv\Scripts\python.exe C:\side-project\trading-king\backend\scripts\probe_watchlist_refresh.py
```

Expected: `[after] 2454 in field_cache: True` → `All watchlist refresh probe passed ✓`，exit 0。

- [ ] **Step 6: Commit**

```powershell
git add backend/routes/watchlist.py backend/scripts/probe_watchlist_refresh.py
git commit -m "fix(watchlist): refresh signal_engine field_cache on add/remove

新加自選股原本對 scope=watchlist 訊號不會觸發 (_scope_includes 用 symbol in
field_cache 判定，add 時沒重 fill)。POST/DELETE 結尾加
refresh_active_signals() 修正。

加 probe_watchlist_refresh.py 驗：加新 symbol → engine._field_cache 含該 symbol。"
```

---

## Task 2: 新 endpoint `GET /api/signals/today_counts`

**Files:**
- Modify: `backend/routes/signals_history.py`
- Create: `backend/scripts/probe_today_counts.py`

- [ ] **Step 1: 寫 probe**

Create `backend/scripts/probe_today_counts.py`:

```python
"""驗 GET /api/signals/today_counts 回 today TW 的 signals_log raw rows。

預期：
1. 起初 today_counts 回 N 筆
2. 插 3 筆 mock signals_log (triggered_at=now, context_json.probe=true)
3. today_counts 應回 N+3 筆
4. cleanup: DELETE WHERE context_json->>'probe' = 'true'
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend))

import httpx

from services.supabase_client import get_supabase


BASE_URL = "http://localhost:8000"


async def main() -> None:
    sb = get_supabase()
    if sb.client is None:
        print("✗ supabase 未連上"); sys.exit(1)

    # cleanup leftovers
    sb.client.table("signals_log").delete().eq("context_json->>probe", "true").execute()

    # baseline
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        r = await cli.get("/api/signals/today_counts")
        if r.status_code != 200:
            print(f"  ✗ status {r.status_code}: {r.text}"); sys.exit(1)
        data = r.json()
        baseline_count = len(data.get("counts", []))
        print(f"[baseline] today_counts: {baseline_count} 筆")
        print(f"  today_start: {data.get('today_start')}")

    # 需要至少一個 active_signal 才能插 signals_log (FK)
    sig_res = sb.client.table("active_signals").select("id").limit(1).execute()
    if not (sig_res.data or []):
        print("⚠ 無 active_signal — 無法插 mock signals_log。先建一條再跑")
        sys.exit(0)
    fake_signal_id = sig_res.data[0]["id"]

    # 插 3 筆 mock today TW
    now_tw = datetime.now(ZoneInfo("Asia/Taipei"))
    mock_rows = []
    for i in range(3):
        mock_rows.append({
            "active_signal_id": fake_signal_id,
            "symbol": "TEST",
            "triggered_at": now_tw.isoformat(),
            "trigger_price": 100.0 + i,
            "trigger_volume": 10,
            "context_json": {"probe": "true", "iter": i},
        })
    sb.client.table("signals_log").insert(mock_rows).execute()
    print(f"[insert] 3 筆 mock signals_log (probe=true)")

    # re-fetch
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as cli:
        r = await cli.get("/api/signals/today_counts")
        data = r.json()
        new_count = len(data.get("counts", []))
        print(f"[after] today_counts: {new_count} 筆")
        if new_count - baseline_count != 3:
            print(f"  ✗ FAIL: expected +3 (got +{new_count - baseline_count})"); sys.exit(1)

    # verify shape: each row has symbol + active_signal_id
    sample = data["counts"][0] if data["counts"] else {}
    if "symbol" not in sample or "active_signal_id" not in sample:
        print(f"  ✗ FAIL: row shape wrong, got keys: {list(sample.keys())}"); sys.exit(1)
    print(f"  ✓ row shape OK: {list(sample.keys())}")

    # cleanup
    sb.client.table("signals_log").delete().eq("context_json->>probe", "true").execute()
    print("[cleanup] 已刪除 probe=true rows")

    print("\nAll today_counts probe passed ✓")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑 probe 驗預期失敗**

```powershell
& C:\side-project\trading-king\backend\.venv\Scripts\python.exe C:\side-project\trading-king\backend\scripts\probe_today_counts.py
```

Expected: status 404（endpoint 還沒實作）→ probe 報 `✗ status 404: ...`，exit 1。

- [ ] **Step 3: 加 endpoint**

Modify `backend/routes/signals_history.py`：加 import + 新 endpoint。

```python
"""GET /api/signals/history?... — 訊號歷史查詢。
GET /api/signals/today_counts — 今日累計命中數（給前端 chip 上標）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from services.supabase_client import SupabaseStatus, get_supabase

router = APIRouter()


@router.get("/api/signals/history")
async def signals_history(
    symbol: str | None = Query(None),
    active_signal_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO datetime"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    def _q():
        q = sb.client.table("signals_log").select(
            "id, active_signal_id, symbol, triggered_at, trigger_price, trigger_volume, context_json"
        ).order("triggered_at", desc=True).limit(limit)
        if symbol:
            q = q.eq("symbol", symbol)
        if active_signal_id:
            q = q.eq("active_signal_id", active_signal_id)
        if since:
            q = q.gte("triggered_at", since)
        return q.execute()

    res = await asyncio.to_thread(_q)
    return {"signals": res.data or [], "count": len(res.data or [])}


@router.get("/api/signals/today_counts")
async def today_counts() -> dict:
    """回 today (Asia/Taipei) 的 signals_log raw rows (symbol + active_signal_id)。
    前端 group by (symbol, active_signal_id) 算 count。
    Row 量小（cooldown ≥ 1800s × N 規則 × N 自選），不需 backend aggregate。
    """
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    tz_tw = ZoneInfo("Asia/Taipei")
    today_start_tw = datetime.now(tz_tw).replace(hour=0, minute=0, second=0, microsecond=0)

    def _q():
        return (
            sb.client.table("signals_log")
            .select("symbol, active_signal_id")
            .gte("triggered_at", today_start_tw.isoformat())
            .execute()
        )

    res = await asyncio.to_thread(_q)
    return {
        "as_of": datetime.now(tz_tw).isoformat(),
        "today_start": today_start_tw.isoformat(),
        "counts": res.data or [],
    }
```

- [ ] **Step 4: 重啟 backend，跑 probe 驗 PASS**

uvicorn `--reload` 會抓到改動自動重啟。

```powershell
& C:\side-project\trading-king\backend\.venv\Scripts\python.exe C:\side-project\trading-king\backend\scripts\probe_today_counts.py
```

Expected: `[after] today_counts: {N+3} 筆` → `All today_counts probe passed ✓`，exit 0。

- [ ] **Step 5: Commit**

```powershell
git add backend/routes/signals_history.py backend/scripts/probe_today_counts.py
git commit -m "feat(signals): add GET /api/signals/today_counts endpoint

回 today (Asia/Taipei) 的 signals_log raw rows，前端 group by (symbol,
active_signal_id) 算當日命中計數作為 chip 上標基準（後續 WS event 累加）。

加 probe_today_counts.py 驗 endpoint shape + 插 3 筆 mock 驗 count +3。"
```

---

## Task 3: API client 加 `signals.todayCounts()` + types

**Files:**
- Modify: `frontend/src/lib/api.ts:308-311` (insert before `SignalEvent`)

- [ ] **Step 1: 加 type + method**

Modify `frontend/src/lib/api.ts`，在現有 `SignalsHistoryResponse`（line ~311）之後、`SignalEvent`（line ~313）之前插入：

```typescript
export interface TodayCountsRow {
  symbol: string;
  active_signal_id: string;
}

export interface TodayCountsResponse {
  as_of: string;
  today_start: string;
  counts: TodayCountsRow[];
}
```

並在 `api` object（line ~330）內，`signalsHistory` 之後加 method：

```typescript
  signalsTodayCounts: () =>
    fetchJSON<TodayCountsResponse>("/api/signals/today_counts"),
```

完整 patch（context）— 找到 `signalsHistory: (params: ...)` 那個 method，在它後面加 `signalsTodayCounts`：

```typescript
  signalsHistory: (params: {
    symbol?: string; active_signal_id?: string;
    since?: string; limit?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    if (params.symbol) qs.set("symbol", params.symbol);
    if (params.active_signal_id) qs.set("active_signal_id", params.active_signal_id);
    if (params.since) qs.set("since", params.since);
    if (params.limit) qs.set("limit", String(params.limit));
    return fetchJSON<SignalsHistoryResponse>(`/api/signals/history?${qs.toString()}`);
  },

  signalsTodayCounts: () =>
    fetchJSON<TodayCountsResponse>("/api/signals/today_counts"),
```

- [ ] **Step 2: 驗 type 正確**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error 輸出，exit 0。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/lib/api.ts
git commit -m "feat(api): add signalsTodayCounts() + TodayCounts types"
```

---

## Task 4: 新 hook `useTodayHits.ts`

**Files:**
- Create: `frontend/src/hooks/useTodayHits.ts`

- [ ] **Step 1: 寫 hook**

Create `frontend/src/hooks/useTodayHits.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

/**
 * 管 (symbol, active_signal_id) → 今日命中次數。
 *
 * Mount 時打 GET /api/signals/today_counts 拿基準（group by 兩 key）。
 * 之後靠 useSignalsStream 的 onSignal 呼叫 bump() 累加。
 *
 * Fail-soft：endpoint 失敗時 fallback 全 0 baseline，UI 不擋。
 */
export type HitCounts = Record<string, Record<string, number>>;
// {[symbol]: {[active_signal_id]: count}}

export function useTodayHits() {
  const [counts, setCounts] = useState<HitCounts>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // initial baseline
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.signalsTodayCounts();
        if (cancelled) return;
        const grouped: HitCounts = {};
        for (const row of r.counts) {
          if (!grouped[row.symbol]) grouped[row.symbol] = {};
          grouped[row.symbol][row.active_signal_id] =
            (grouped[row.symbol][row.active_signal_id] ?? 0) + 1;
        }
        setCounts(grouped);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        // fallback: 保持 {} (全 0)
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const bump = useCallback((symbol: string, activeSignalId: string) => {
    setCounts((prev) => {
      const cur = prev[symbol] ?? {};
      return {
        ...prev,
        [symbol]: {
          ...cur,
          [activeSignalId]: (cur[activeSignalId] ?? 0) + 1,
        },
      };
    });
  }, []);

  const getCount = useCallback(
    (symbol: string, activeSignalId: string): number =>
      counts[symbol]?.[activeSignalId] ?? 0,
    [counts],
  );

  const getTotalForSymbol = useCallback(
    (symbol: string): number => {
      const m = counts[symbol] ?? {};
      return Object.values(m).reduce((a, b) => a + b, 0);
    },
    [counts],
  );

  return { counts, loading, error, bump, getCount, getTotalForSymbol };
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/hooks/useTodayHits.ts
git commit -m "feat(hooks): add useTodayHits for chip count baseline + WS bump"
```

---

## Task 5: 新 component `SignalChip.tsx`

**Files:**
- Create: `frontend/src/components/SignalChip.tsx`

- [ ] **Step 1: 寫 component**

Create `frontend/src/components/SignalChip.tsx`:

```typescript
/**
 * 單個規則 chip — 顯示「規則名 + （命中時）上標當日次數」。
 *
 * 視覺：
 * - 預設：line-strong 邊框 + ink-muted 文字
 * - 命中（count > 0）：accent 紅邊框 + 微紅底 + accent 上標數字
 *
 * 對應 spec §7.3 / v11 mockup chip 樣式。
 */

const SUPERSCRIPT_DIGITS = ["⁰", "¹", "²", "³", "⁴", "⁵", "⁶", "⁷", "⁸", "⁹"];

function toSuperscript(n: number): string {
  if (n < 10) return SUPERSCRIPT_DIGITS[n];
  // 兩位數以上：每位數轉
  return String(n).split("").map(d => SUPERSCRIPT_DIGITS[Number(d)]).join("");
}

interface Props {
  ruleName: string;
  count?: number;  // undefined or 0 = 沒命中
}

export function SignalChip({ ruleName, count = 0 }: Props) {
  const hit = count > 0;
  return (
    <span
      className={[
        "inline-flex items-baseline gap-1 px-[11px] py-1 text-[13px] tracking-[0.3px] border",
        hit
          ? "border-accent/50 text-ink bg-accent/[0.05]"
          : "border-line-strong text-ink-muted",
        "transition-colors duration-150",
      ].join(" ")}
    >
      {ruleName}
      {hit && (
        <sup className="text-[10px] text-accent font-semibold tabular-nums ml-0.5 leading-none">
          {toSuperscript(count)}
        </sup>
      )}
    </span>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/SignalChip.tsx
git commit -m "feat(components): add SignalChip with superscript hit count"
```

---

## Task 6: 新 component `WatchlistWithChips.tsx`

**Files:**
- Create: `frontend/src/components/WatchlistWithChips.tsx`

- [ ] **Step 1: 寫 component**

Create `frontend/src/components/WatchlistWithChips.tsx`:

```typescript
import { type ActiveSignal, type WatchlistRow } from "../lib/api";
import { SignalChip } from "./SignalChip";
import { type HitCounts } from "../hooks/useTodayHits";

/**
 * 自選 list + Scope chip 顯示。
 *
 * 排序：has-hit 置頂（按 total hit desc），無命中按 added_at desc（原順序）。
 * 命中股票左側 3px accent marker（spec §7.3）。
 *
 * 點 row → onSelect(symbol)；點 × → onRemove(symbol)。
 */
interface Props {
  items: WatchlistRow[];
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
  onRemove: (symbol: string) => void;
}

function rulesForSymbol(symbol: string, rules: ActiveSignal[]): ActiveSignal[] {
  return rules.filter((r) => {
    if (!r.enabled) return false;
    if (r.scope.type === "watchlist") return true;
    if (r.scope.type === "symbols") return r.scope.symbols.includes(symbol);
    return false;
  });
}

function totalHitsForSymbol(symbol: string, hitCounts: HitCounts): number {
  const m = hitCounts[symbol] ?? {};
  return Object.values(m).reduce((a, b) => a + b, 0);
}

export function WatchlistWithChips({
  items, rules, hitCounts, selectedSymbol, onSelect, onRemove,
}: Props) {
  // sort: has-hit desc, by total hits desc; rest 維持原順序
  const sorted = [...items].sort((a, b) => {
    const ha = totalHitsForSymbol(a.symbol, hitCounts);
    const hb = totalHitsForSymbol(b.symbol, hitCounts);
    if (ha !== hb) return hb - ha;
    return 0;  // stable: 原順序維持
  });

  if (items.length === 0) {
    return (
      <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
        自選清單還是空的 — 上面搜尋加入第一檔股票
      </div>
    );
  }

  return (
    <ul className="border-t border-line">
      {sorted.map((it) => {
        const symRules = rulesForSymbol(it.symbol, rules);
        const isSel = it.symbol === selectedSymbol;
        const totalHits = totalHitsForSymbol(it.symbol, hitCounts);
        const hasHit = totalHits > 0;

        return (
          <li
            key={it.symbol}
            className={[
              "relative px-3.5 py-4 border-b border-line cursor-pointer transition-colors duration-200",
              isSel ? "bg-bg-card border-l-2 border-l-accent pl-3" : "hover:bg-bg-card/40",
            ].join(" ")}
            onClick={() => onSelect(it.symbol)}
          >
            {/* has-hit marker (覆蓋於 selected 時隱藏 — selected 自己有 left border) */}
            {hasHit && !isSel && (
              <span
                className="absolute left-0 top-4 w-[3px] h-[22px] bg-accent"
                aria-hidden
              />
            )}

            <span className="block text-[19px] font-medium text-ink mb-0.5">{it.symbol}</span>
            <div className="text-[15px] text-ink-muted mb-2.5">{it.name ?? "—"}</div>

            <div className="flex flex-wrap gap-1.5">
              {symRules.map((r) => (
                <SignalChip
                  key={r.id}
                  ruleName={r.name}
                  count={hitCounts[it.symbol]?.[r.id] ?? 0}
                />
              ))}
            </div>

            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRemove(it.symbol); }}
              className="absolute right-2.5 top-3 text-base text-ink-dim hover:text-accent px-1"
              aria-label={`移除 ${it.symbol}`}
            >
              ×
            </button>
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/WatchlistWithChips.tsx
git commit -m "feat(components): add WatchlistWithChips (Scope chip + 命中置頂)"
```

---

## Task 7: 新 component `TopToolbar.tsx`

**Files:**
- Create: `frontend/src/components/TopToolbar.tsx`

- [ ] **Step 1: 寫 component**

Create `frontend/src/components/TopToolbar.tsx`:

```typescript
import { type WSStatus } from "../hooks/useSignalsStream";

/**
 * Nav 下方 utility bar：
 * - 左：● 連線中 / ● 連線中… / ● 已斷線
 * - 右：⚙ 訊號規則 [badge] 按鈕（dialogOpen 時實心 accent）
 *
 * 對應 spec §7.2 / v11 mockup toolbar。無 border, transparent。
 */
interface Props {
  wsStatus: WSStatus;
  rulesCount: number;
  dialogOpen: boolean;
  onOpenRules: () => void;
}

function statusText(s: WSStatus): { text: string; color: string } {
  if (s === "open") return { text: "連線中", color: "text-bear" };
  if (s === "connecting") return { text: "連線中…", color: "text-accent" };
  return { text: "已斷線", color: "text-accent" };
}

export function TopToolbar({ wsStatus, rulesCount, dialogOpen, onOpenRules }: Props) {
  const { text, color } = statusText(wsStatus);
  return (
    <div className="bg-transparent">
      <div className="mx-auto max-w-[1600px] px-[60px] pt-[26px] pb-2.5 flex items-center justify-between gap-4 max-md:px-6">
        <span className="inline-flex items-baseline gap-2 text-[11px] uppercase tracking-[1.5px] text-ink-dim">
          <span className={`${color} text-[13px] leading-none`}>●</span>
          {text}
        </span>

        <button
          type="button"
          onClick={onOpenRules}
          className={[
            "inline-flex items-center gap-2.5 px-[18px] py-2 text-[12px] uppercase tracking-[1.8px] font-medium border transition-all duration-150 cursor-pointer",
            dialogOpen
              ? "bg-accent text-bg border-accent"
              : "bg-transparent text-accent border-accent hover:bg-accent/10",
          ].join(" ")}
        >
          <span className="text-[15px] leading-none">⚙</span>
          訊號規則
          <span
            className={[
              "text-[11px] px-1.5 py-[1px] font-semibold transition-colors duration-150",
              dialogOpen ? "bg-bg text-accent" : "bg-accent text-bg",
            ].join(" ")}
          >
            {rulesCount}
          </span>
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/TopToolbar.tsx
git commit -m "feat(components): add TopToolbar (連線狀態 + 訊號規則按鈕)"
```

---

## Task 8: 新 component `TriggerHistoryTable.tsx`

**Files:**
- Create: `frontend/src/components/TriggerHistoryTable.tsx`

- [ ] **Step 1: 寫 component**

Create `frontend/src/components/TriggerHistoryTable.tsx`:

```typescript
import { type ActiveSignal, type SignalLogRow, type SignalEvent } from "../lib/api";

/**
 * 全寬 4-col 觸發歷史：時間 / 股票 / 規則 / 觸發資訊。
 *
 * 資料來源：
 *   - `historical`：mount 時 GET /api/signals/history 拿到的歷史 (today)
 *   - `recent`：useSignalsStream 收到的即時 events
 * 兩者合併、按 triggered_at desc，最新的標 fresh。
 *
 * 點 row → onSelect(symbol)，連動 chart + watchlist。
 *
 * 對應 spec §7.4 / v11 mockup history table。
 */
interface Props {
  historical: SignalLogRow[];       // from /api/signals/history
  recent: SignalEvent["data"][];    // from useSignalsStream
  rules: ActiveSignal[];            // 用來 lookup rule name (history.active_signal_id → name)
  symbolNames: Record<string, string | null>;  // symbol → name (from watchlist)
  onSelect: (symbol: string) => void;
}

interface UnifiedRow {
  key: string;
  time: string;          // HH:MM:SS
  date: string;          // YYYY/M/D
  symbol: string;
  name: string | null;
  ruleName: string;
  price: number;
  vol: number;
  isoTime: string;       // for sort
  isFresh: boolean;      // 來自 recent
}

function formatTime(iso: string): { time: string; date: string } {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
    date: `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`,
  };
}

export function TriggerHistoryTable({
  historical, recent, rules, symbolNames, onSelect,
}: Props) {
  const ruleNameById = Object.fromEntries(rules.map((r) => [r.id, r.name]));

  // 合併：recent 在前（已最新→舊），historical 在後（已 desc by triggered_at）
  // recent.active_signal_id 來自 WS event；historical.active_signal_id 來自 DB
  const recentRows: UnifiedRow[] = recent.map((e) => {
    const { time, date } = formatTime(e.triggered_at);
    return {
      key: `recent-${e.active_signal_id}-${e.triggered_at}-${e.symbol}`,
      time, date,
      symbol: e.symbol,
      name: symbolNames[e.symbol] ?? null,
      ruleName: e.active_signal_name ?? ruleNameById[e.active_signal_id] ?? "(unknown)",
      price: e.trigger_price,
      vol: e.trigger_volume,
      isoTime: e.triggered_at,
      isFresh: true,
    };
  });

  const historicalRows: UnifiedRow[] = historical.map((h) => {
    const { time, date } = formatTime(h.triggered_at);
    return {
      key: `hist-${h.id}`,
      time, date,
      symbol: h.symbol,
      name: symbolNames[h.symbol] ?? null,
      ruleName: ruleNameById[h.active_signal_id ?? ""] ?? "(unknown)",
      price: h.trigger_price ?? 0,
      vol: h.trigger_volume ?? 0,
      isoTime: h.triggered_at,
      isFresh: false,
    };
  });

  // Dedup: recent events 可能 server 已寫進 signals_log 然後 /history 又 fetch 到 → 用 (sym, time, rule) dedup
  const seen = new Set<string>();
  const combined: UnifiedRow[] = [];
  for (const r of [...recentRows, ...historicalRows]) {
    const k = `${r.symbol}|${r.ruleName}|${r.isoTime}`;
    if (seen.has(k)) continue;
    seen.add(k);
    combined.push(r);
  }
  combined.sort((a, b) => b.isoTime.localeCompare(a.isoTime));
  // Mark first one as fresh (most recent)
  if (combined.length > 0 && !combined[0].isFresh) {
    combined[0] = { ...combined[0], isFresh: false };  // 沒 recent 時不標 fresh
  }

  return (
    <div className="border-t border-line max-h-[480px] overflow-y-auto">
      {/* sticky header */}
      <div className="sticky top-0 z-[1] grid grid-cols-[120px_200px_1fr_280px] gap-8 px-4 py-2.5 border-b border-line bg-bg text-[10px] uppercase tracking-[2px] text-ink-dim">
        <div>時間</div>
        <div>股票</div>
        <div>規則</div>
        <div className="text-right">觸發資訊</div>
      </div>

      {combined.length === 0 ? (
        <div className="border-b border-line px-4 py-10 text-center text-ink-dim font-serif italic text-[15px]">
          等待第一筆訊號…
        </div>
      ) : (
        combined.map((r) => (
          <div
            key={r.key}
            onClick={() => onSelect(r.symbol)}
            className={[
              "grid grid-cols-[120px_200px_1fr_280px] gap-8 px-4 py-4 border-b border-line cursor-pointer transition-colors duration-200 items-baseline",
              r.isFresh
                ? "bg-accent/[0.04] border-l-2 border-l-accent pl-3.5"
                : "hover:bg-bg-card/40",
            ].join(" ")}
          >
            <div className="text-[14px] text-ink-muted tabular-nums tracking-[0.5px]">
              {r.time}
              <span className="block text-[11px] text-ink-dim tracking-[1px] mt-0.5">{r.date}</span>
            </div>
            <div className="flex flex-col">
              <span className="text-[18px] font-medium text-ink">{r.symbol}</span>
              {r.name && (
                <span className="text-[13px] text-ink-muted mt-0.5">{r.name}</span>
              )}
            </div>
            <div className="font-serif italic font-bold text-[18px] text-accent tracking-[-0.3px]">
              {r.ruleName}
            </div>
            <div className="text-right tabular-nums">
              <span className="block text-[18px] text-ink font-medium tracking-[-0.3px]">
                {r.price.toFixed(2)}
              </span>
              <span className="block text-[12px] text-ink-dim mt-0.5 tracking-[0.5px]">
                vol {r.vol}
              </span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/TriggerHistoryTable.tsx
git commit -m "feat(components): add TriggerHistoryTable (4-col grid + 點 row 切換)"
```

---

## Task 9: 新 component `SignalRulesDialog.tsx`

**Files:**
- Create: `frontend/src/components/SignalRulesDialog.tsx`

- [ ] **Step 1: 寫 component**

Create `frontend/src/components/SignalRulesDialog.tsx`:

```typescript
import { useEffect, useState } from "react";
import { ActiveSignalEditor } from "./ActiveSignalEditor";
import { api, type ActiveSignal } from "../lib/api";

/**
 * 訊號規則 Dialog — 列表 + 新增/編輯 + toggle 啟用 + 刪除。
 *
 * 內嵌 ActiveSignalEditor 做新增/編輯（nested modal — z-index 由內部 fixed inset-0 處理）。
 * 對應 spec §7.5 / v11 mockup dialog 樣式。
 *
 * 關閉：點 × / 點 backdrop / 按 Esc。
 */
interface Props {
  open: boolean;
  rules: ActiveSignal[];
  onClose: () => void;
  onChanged: () => void;  // 任何 CRUD 操作後通知 parent refresh (useActiveSignals.refresh)
}

function pillsForRule(r: ActiveSignal): string[] {
  const scope = r.scope.type === "watchlist"
    ? "自選全部"
    : `指定 ${r.scope.symbols.length} 檔`;
  const cd = `cd ${r.cooldown_seconds}s`;
  const conditions = (r.filter_json.conditions?.length ?? 0)
    + (r.filter_json.window_conditions?.length ?? 0);
  const logic = `${r.filter_json.logic} · ${conditions} 條件`;
  return [scope, cd, logic];
}

export function SignalRulesDialog({ open, rules, onClose, onChanged }: Props) {
  const [editing, setEditing] = useState<ActiveSignal | null>(null);
  const [creating, setCreating] = useState(false);

  // Esc 關閉
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function toggleEnabled(r: ActiveSignal) {
    await api.activeSignals.update(r.id, {
      name: r.name,
      filter_json: r.filter_json,
      scope: r.scope,
      cooldown_seconds: r.cooldown_seconds,
      ignore_auctions: r.ignore_auctions,
      enabled: !r.enabled,
    });
    onChanged();
  }

  async function removeRule(r: ActiveSignal) {
    if (!confirm(`刪除「${r.name}」？`)) return;
    await api.activeSignals.delete(r.id);
    onChanged();
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className={[
          "fixed inset-0 z-20 bg-bg-deep/85 transition-opacity duration-200",
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none",
        ].join(" ")}
        style={{ backdropFilter: "blur(2px)" }}
      />

      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="rules-dialog-title"
        className={[
          "fixed top-1/2 left-1/2 z-[21] bg-bg-card border border-line-strong flex flex-col",
          "w-[min(740px,90vw)] max-h-[82vh]",
          "transition-[opacity,transform] duration-200",
          open
            ? "opacity-100 pointer-events-auto -translate-x-1/2 -translate-y-1/2"
            : "opacity-0 pointer-events-none -translate-x-1/2 -translate-y-[calc(50%-20px)]",
        ].join(" ")}
      >
        <div className="flex items-baseline justify-between px-8 pt-7 pb-4 border-b border-line">
          <div>
            <div className="text-[12px] uppercase tracking-[1.8px] text-ink-dim font-medium">設定</div>
            <h3 id="rules-dialog-title" className="font-serif font-bold text-[28px] tracking-[-0.5px] leading-[1.05] mt-1">訊號規則</h3>
            <div className="text-[12px] uppercase tracking-[1.5px] text-ink-dim mt-1">
              {rules.length} 條規則 · {rules.filter(r => r.enabled).length} 啟用中
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="關閉"
            className="text-2xl text-ink-dim hover:text-ink px-2 leading-none cursor-pointer"
          >
            ×
          </button>
        </div>

        <div className="px-8 pt-4 pb-7 overflow-y-auto">
          {rules.length === 0 ? (
            <div className="border border-line p-6 text-center text-ink-dim font-serif italic">
              還沒有訊號規則 — 點下方「+ 新增」設第一條
            </div>
          ) : (
            <div className="border-t border-line">
              {rules.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between px-2.5 py-4 border-b border-line hover:bg-bg-deep transition-colors duration-200"
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-[18px] font-medium text-ink mb-1">{r.name}</div>
                    <div className="text-[13px] text-ink-dim leading-relaxed">
                      {pillsForRule(r).map((p, i) => (
                        <span
                          key={i}
                          className="inline-block px-2.5 py-px mr-1.5 border border-line-strong text-[11px] text-ink-muted tracking-[0.3px]"
                          style={{ verticalAlign: "1px" }}
                        >
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <button
                      type="button"
                      onClick={() => toggleEnabled(r)}
                      aria-label={r.enabled ? "停用" : "啟用"}
                      className={[
                        "relative w-9 h-5 rounded-full transition-colors duration-200 cursor-pointer border-0 p-0",
                        r.enabled ? "bg-accent/40" : "bg-line-strong",
                      ].join(" ")}
                    >
                      <span
                        className={[
                          "absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200",
                          r.enabled ? "left-[18px] bg-accent" : "left-0.5 bg-ink-muted",
                        ].join(" ")}
                      />
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(r)}
                      className="text-[11px] uppercase tracking-[1.2px] text-ink-dim hover:text-ink px-1"
                    >
                      編輯
                    </button>
                    <button
                      type="button"
                      onClick={() => removeRule(r)}
                      className="text-[11px] uppercase tracking-[1.2px] text-ink-dim hover:text-accent px-1"
                    >
                      刪除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            type="button"
            onClick={() => setCreating(true)}
            className="block w-full text-center mt-4 px-4 py-4 text-[12px] uppercase tracking-[1.5px] text-ink-dim border border-dashed border-line-strong hover:text-accent hover:border-accent transition-colors cursor-pointer bg-transparent"
          >
            + 新增規則
          </button>
        </div>
      </div>

      {/* 內嵌 ActiveSignalEditor (新增/編輯) */}
      {(creating || editing) && (
        <ActiveSignalEditor
          initial={editing ?? undefined}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { onChanged(); setCreating(false); setEditing(null); }}
        />
      )}
    </>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: Commit**

```powershell
git add frontend/src/components/SignalRulesDialog.tsx
git commit -m "feat(components): add SignalRulesDialog (規則 CRUD modal)"
```

---

## Task 10: 新 page `Monitor.tsx` 整合所有

**Files:**
- Create: `frontend/src/pages/Monitor.tsx`

- [ ] **Step 1: 寫 page**

Create `frontend/src/pages/Monitor.tsx`:

```typescript
import { useEffect, useMemo, useRef, useState } from "react";
import { IntradayChart } from "../components/IntradayChart";
import { SignalRulesDialog } from "../components/SignalRulesDialog";
import { SymbolSearch } from "../components/SymbolSearch";
import { TopToolbar } from "../components/TopToolbar";
import { TriggerHistoryTable } from "../components/TriggerHistoryTable";
import { WatchlistWithChips } from "../components/WatchlistWithChips";
import { useActiveSignals } from "../hooks/useActiveSignals";
import { useIntradayCandles } from "../hooks/useIntradayCandles";
import { useSignalsStream } from "../hooks/useSignalsStream";
import { useTodayHits } from "../hooks/useTodayHits";
import { useWatchlist } from "../hooks/useWatchlist";
import { api, type SignalLogRow } from "../lib/api";

/**
 * 即時監控頁 — 整合 watchlist + chart + history + rules dialog。
 *
 * Layout (spec §3 / v11 mockup):
 *   上半 grid-2: 自選 480 + 分時 1fr
 *   下半全寬：觸發歷史 4-col grid
 *
 * 共用 selectedSymbol state — 點自選 row / history row 都驅動 chart 切換。
 */
export function Monitor() {
  const { items: watchlistItems, add, remove } = useWatchlist();
  const { items: rules, refresh: refreshRules } = useActiveSignals();
  const { counts, bump } = useTodayHits();

  const [selected, setSelected] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [historicalToday, setHistoricalToday] = useState<SignalLogRow[]>([]);

  const chartRef = useRef<HTMLDivElement | null>(null);

  // Selected default：第一檔自選股
  useEffect(() => {
    if (!selected && watchlistItems.length > 0) {
      setSelected(watchlistItems[0].symbol);
    }
  }, [watchlistItems, selected]);

  // 拉 today 的 signals_log (給 history table 顯示)
  useEffect(() => {
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    (async () => {
      try {
        const r = await api.signalsHistory({
          since: todayStart.toISOString(),
          limit: 500,
        });
        setHistoricalToday(r.signals);
      } catch (e) {
        console.warn("load history failed:", e);
      }
    })();
  }, []);

  // Intraday chart (selected symbol) — onTick callback 在下面 useSignalsStream 內共用
  const { candles, loading: candlesLoading, onTick } = useIntradayCandles(selected);

  // 單一 WS 連線：onSignal 累加命中、onTick 給 chart
  const { status: wsStatus, recent } = useSignalsStream({
    onSignal: (s) => bump(s.symbol, s.active_signal_id),
    onTick,
  });

  const symbolNames = useMemo(() => {
    const m: Record<string, string | null> = {};
    for (const it of watchlistItems) m[it.symbol] = it.name;
    return m;
  }, [watchlistItems]);

  function handleSelect(sym: string) {
    setSelected(sym);
    // 從 history 點時 scroll 回 chart
    if (chartRef.current) {
      chartRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  async function handleAdd(symbol: string) {
    try { await add(symbol); } catch (e) { console.warn("add failed:", e); }
  }

  return (
    <>
      <TopToolbar
        wsStatus={wsStatus}
        rulesCount={rules.length}
        dialogOpen={dialogOpen}
        onOpenRules={() => setDialogOpen((v) => !v)}
      />

      <main>
        <div className="mx-auto max-w-[1600px] px-[60px] pt-7 pb-12 max-md:px-6">

          {/* 上半 grid: 自選 + 分時 */}
          <div className="grid grid-cols-[480px_1fr] gap-14 max-md:grid-cols-1">
            <section>
              <div className="mb-5">
                <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                  自選清單
                  <span className="font-sans font-normal text-[15px] text-ink-dim ml-2">
                    ({watchlistItems.length})
                  </span>
                </h2>
              </div>
              <div className="mb-5">
                <SymbolSearch onPick={handleAdd} />
              </div>
              <WatchlistWithChips
                items={watchlistItems}
                rules={rules}
                hitCounts={counts}
                selectedSymbol={selected}
                onSelect={setSelected}
                onRemove={remove}
              />
            </section>

            <section ref={chartRef}>
              <div className="mb-5">
                <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                  分時走勢
                </h2>
              </div>
              {!selected ? (
                <div className="h-[460px] flex items-center justify-center border border-line text-ink-dim font-serif italic">
                  ← 點選左邊任一檔股票看分時走勢
                </div>
              ) : (
                <div className="border border-line p-7">
                  <div className="text-xs text-ink-dim mb-2">{selected}</div>
                  <IntradayChart symbol={selected} candles={candles} loading={candlesLoading} />
                </div>
              )}
            </section>
          </div>

          {/* 下半全寬: 觸發歷史 */}
          <section className="mt-14">
            <div className="mb-5">
              <h2 className="font-serif font-bold text-[30px] tracking-[-0.5px] leading-[1.05]">
                觸發歷史
                <span className="font-sans font-normal text-[15px] text-ink-dim ml-2">
                  ({historicalToday.length + recent.length})
                </span>
              </h2>
            </div>
            <TriggerHistoryTable
              historical={historicalToday}
              recent={recent}
              rules={rules}
              symbolNames={symbolNames}
              onSelect={handleSelect}
            />
          </section>

        </div>
      </main>

      <SignalRulesDialog
        open={dialogOpen}
        rules={rules}
        onClose={() => setDialogOpen(false)}
        onChanged={refreshRules}
      />
    </>
  );
}
```

- [ ] **Step 2: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

- [ ] **Step 3: 啟 dev server + 手動 smoke test**

```powershell
cd C:\side-project\trading-king\frontend
npm run dev
```

Open `http://localhost:5173`，**因為 App.tsx 還沒 wire `Monitor` 進來**，這步只是確認 page 編譯成功、dev server 不會崩。Browser console 應該無 error（會看到原本 watchlist/signals tab 的舊頁面，因為 Monitor 還沒掛上）。

> 完整 UAT 留到 Task 11 結束（App.tsx 接好之後）。

- [ ] **Step 4: Commit**

```powershell
git add frontend/src/pages/Monitor.tsx
git commit -m "feat(pages): add Monitor page integrating watchlist + chart + history + dialog"
```

---

## Task 11: App.tsx nav 整合 + 刪除舊 page

**Files:**
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/pages/Watchlist.tsx`, `frontend/src/pages/Signals.tsx`

- [ ] **Step 1: 改 App.tsx — 換 Page type + import + 路由 + Nav items**

Modify `frontend/src/App.tsx`。完整新版（保留 Masthead / Meta 不動，只改 Page type、import、router、Nav）：

```typescript
import { useEffect, useState } from "react";
import { Health } from "./pages/Health";
import { Monitor } from "./pages/Monitor";
import { Screener } from "./pages/Screener";

type Page = "health" | "screener" | "monitor";

export default function App() {
  const [page, setPage] = useState<Page>("health");

  useEffect(() => {
    document.body.classList.add("opacity-100");
  }, []);

  return (
    <>
      <Masthead />
      <Nav active={page} onNavigate={setPage} />
      {page === "health" && <Health />}
      {page === "screener" && <Screener />}
      {page === "monitor" && <Monitor />}
    </>
  );
}

function Masthead() {
  return (
    <header className="border-t-4 border-accent bg-bg-card">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-baseline justify-between gap-4 px-[60px] pb-4 pt-6 max-md:px-6">
        <h1 className="font-serif text-3xl font-bold tracking-editorial text-ink">
          treading{" "}
          <span className="font-light text-ink-muted">·</span>{" "}
          <em className="font-serif italic font-bold">king</em>
        </h1>
        <Meta />
      </div>
    </header>
  );
}

function Meta() {
  const today = new Date().toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs text-ink-dim tracking-[0.3px]">
      <span>{today}</span>
      <span className="opacity-40">·</span>
      <span>盤中連續競價</span>
    </div>
  );
}

function Nav({
  active,
  onNavigate,
}: {
  active: Page;
  onNavigate: (p: Page) => void;
}) {
  const items: Array<{ id: Page; label: string }> = [
    { id: "health", label: "系統狀態" },
    // { id: "screener", label: "篩股" },  // 暫時隱藏，待 cache job 自動化後啟用
    { id: "monitor", label: "即時監控" },
  ];

  return (
    <nav className="border-y border-line bg-bg-card/40">
      <div className="mx-auto flex max-w-[1600px] gap-0 px-[60px] max-md:px-6">
        {items.map((it) => {
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              onClick={() => onNavigate(it.id)}
              className={`px-4 py-3 text-xs uppercase tracking-[2px] cursor-pointer hover:text-ink bg-transparent border-b-2 ${
                isActive ? "border-accent text-ink" : "text-ink-dim border-transparent"
              }`}
            >
              {it.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
```

> **Width change note:** masthead + nav 的 `max-w-[1200px]` 改成 `max-w-[1600px]` 對齊 Monitor 頁的 1600px content max-width（spec §3）。

- [ ] **Step 2: 刪除舊 pages**

```powershell
git rm frontend/src/pages/Watchlist.tsx frontend/src/pages/Signals.tsx
```

- [ ] **Step 3: 驗 type**

```powershell
cd C:\side-project\trading-king\frontend
npx tsc -b
```

Expected: 無 error。

> **若報 error**：可能還有別處 import `pages/Watchlist` 或 `pages/Signals`。`Grep` 搜尋 `from.*pages/(Watchlist|Signals)` 確認沒有殘留 import。

- [ ] **Step 4: 啟 dev server + 完整 manual UAT**

```powershell
cd C:\side-project\trading-king\frontend
npm run dev
```

打開 `http://localhost:5173`，按 spec §10.2 的 UAT checklist 逐項試：

- [ ] 進「即時監控」tab → 看到 toolbar (連線狀態 + 訊號規則按鈕) + 自選 + chart + history
- [ ] 加新 symbol 到自選（譬如 2454 聯發科）→ 出現在 list + ws 訂閱
- [ ] 設一條 scope=watchlist 的訊號規則 → 自選每檔 chip 都顯示該規則
- [ ] （盤中）等命中發生 → chip 紅邊 + 上標 +1 / history 加 row
- [ ] 命中股票自動置頂排序
- [ ] 點 history row → chart 切換到該檔 + 自選對應 row 高亮 + smooth scroll 回 chart
- [ ] 點 watchlist row → chart 切換 + selected 高亮
- [ ] 點 ⚙ 訊號規則 → dialog 開啟 + button 變實心
- [ ] dialog 內：toggle 啟用切換 / 編輯（開內嵌 ActiveSignalEditor） / 刪除 / + 新增規則
- [ ] Esc / × / 點背景 → dialog 關閉
- [ ] 重整頁 → today_counts 重抓，chip 數字校正

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/App.tsx
git commit -m "feat(app): integrate Monitor page, remove Watchlist/Signals tabs

Nav 從「系統狀態 / 即時訊號 / 自選」三 tab 合併成「系統狀態 / 即時監控」兩
tab。Monitor 頁取代 Watchlist.tsx + Signals.tsx，新功能：Scope chip + 命
中置頂 + 觸發歷史全寬 + 點 row 同步 chart。

刪除 pages/Watchlist.tsx + pages/Signals.tsx。masthead+nav max-w 從
1200 加到 1600 對齊 Monitor 內容寬度。"
```

---

## Self-Review Checklist

實作完成（Task 11 結束）後跑這個 self-review：

### 1. Spec coverage
逐項對照 spec：
- §2.1 Scope chip + 命中上標 → Task 5 (SignalChip) + Task 6 (WatchlistWithChips)
- §2.2 命中置頂 → Task 6 sort logic
- §2.3 觸發歷史全寬 + 點 row 切 chart → Task 8 (TriggerHistoryTable) + Task 10 (Monitor handleSelect)
- §2.4 規則 Dialog → Task 9 (SignalRulesDialog) + Task 7 (TopToolbar 按鈕)
- §2.5 watchlist refresh bug → Task 1
- §6.2 today_counts endpoint → Task 2

### 2. Type consistency
- `HitCounts` type 同名定義於 `useTodayHits.ts` 跟使用於 `WatchlistWithChips.tsx`：✓ 兩處都 import from `../hooks/useTodayHits`
- `WSStatus` type 跨 hook 跟 component：`TopToolbar` import from `useSignalsStream`
- API response shape (`TodayCountsResponse.counts: TodayCountsRow[]`) 跟 backend `{counts: rows}` 一致

### 3. Backend probe 可重跑
- probe_watchlist_refresh.py：cleanup 在 try/except 內，多次跑不會 leak
- probe_today_counts.py：cleanup `WHERE context_json->>probe = 'true'`，多次跑不會 leak

### 4. No dangling references
- 舊 Watchlist.tsx / Signals.tsx 刪除後，全部 import 都改成 Monitor — Task 11 Step 3 grep 驗

---

## Execution 注意事項

**盤中 vs 盤後：**
- Task 1 / 2 (backend) 可盤後跑 — 不依賴 ws data tick
- Task 10 / 11 (frontend) 最好盤中驗 — chip 命中、history row 進來、chart tick 都要 ws 開盤
- 若盤後實作，UAT 改為「啟動後手動 POST 一筆 signals_log 進 DB」驗 history 渲染 + 自選排序

**Worktree / Branch：**
- 建議用 git worktree 在 `feature/watchlist-signals-integration` branch 上做（spec 跟 plan 已在 main）
- 或不開新 worktree 直接在 main 上做（小型 feature，~2 天）

**Dependency 之間的 commit order：**
- 順序執行 Task 1 → 11
- 不要跳，因為 frontend tasks 互相 import：Task 6 用 Task 5、Task 10 用 Task 4/6/7/8/9
