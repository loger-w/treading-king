# 日 MA5 / MA20 Chart Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** IntradayChart 加日 K MA5 / MA20 兩條水平線 + `MA` toggle,spec 見 `docs/superpowers/specs/2026-05-15-ma-cdp-proximity-design.md`。

**Architecture:** 純擴充。後端新加 1 個 route 從 `indicator_cache` 拿現有的 `sma_5` / `sma_20`(不改 schema、不加 column);前端 chart 加 toggle + 2 條水平線(沿用 CDP 線的 visibleKeys 過濾邏輯)。

**Tech Stack:** FastAPI + Python 3.12(backend),React 18 + TypeScript + Vite + Tailwind 3(frontend),Supabase(`indicator_cache` 表)。

**Test Strategy:** Codebase 無測試基建(無 `tests/` 目錄、無 vitest),沿用既有 plan convention。Backend 改動以 `curl` smoke test 驗證;Frontend 改動以 `npm run build`(型別檢查) + browser 手動 smoke 驗證。

**Reference paths:**
- Backend root: `C:\side-project\treading-king\backend`
- Frontend root: `C:\side-project\treading-king\frontend`

**啟動指令(多次會用):**
- Backend dev: `cd backend; .venv\Scripts\activate; uvicorn main:app --reload --port 8000`
- Frontend dev: `cd frontend; npm run dev`
- Frontend build(型別檢查): `cd frontend; npm run build`

---

## Task 1: Backend — 新增 `GET /api/ma/{symbol}` route

**Files:**
- Create: `backend/routes/ma.py`
- Modify: `backend/main.py`(註冊 router)

- [ ] **Step 1: 新檔 `backend/routes/ma.py`**

```python
"""GET /api/ma/{symbol} — 回日 K SMA5 / SMA20。

從 indicator_cache 拿最後一次成功 cache run 那天的 sma_5 / sma_20。
缺值(剛加入自選、indicator_cache 還沒跑到)欄位回 null,前端會靜默不畫。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from services.indicator_cache_job import get_latest_done_run
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ma/{symbol}")
async def get_ma(symbol: str) -> dict:
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": sb.last_error},
        )

    latest = await asyncio.to_thread(get_latest_done_run, sb.client)
    if latest is None:
        # cache_runs 是空的(初次部署 / 沒跑過 cache job)
        return {"symbol": symbol, "sma_5": None, "sma_20": None, "as_of_date": None}

    run_date = latest["run_date"]
    res = await asyncio.to_thread(
        lambda: sb.client.table("indicator_cache")
        .select("sma_5, sma_20")
        .eq("symbol", symbol)
        .eq("date", run_date)
        .maybe_single()
        .execute()
    )
    row = res.data or {}
    return {
        "symbol": symbol,
        "sma_5": row.get("sma_5"),
        "sma_20": row.get("sma_20"),
        "as_of_date": run_date,
    }
```

- [ ] **Step 2: 在 `backend/main.py` 註冊 router**

定位既有的 `from routes import (...)` 區塊(約 17-21 行),加 `ma`:

```python
from routes import (
    active_signals, cache, candles, cdp as cdp_route,
    ma,
    preview, quote, signals_history, symbols,
    watchlist, ws,
)  # noqa: E402
```

定位既有 `app.include_router(...)` 串(約 116-125 行),在 `cdp_route` 之後加:

```python
app.include_router(cdp_route.router)
app.include_router(ma.router)  # 新增
app.include_router(ws.router)
```

- [ ] **Step 3: 重啟 backend 並 smoke test 正常路徑**

```powershell
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

另開 PowerShell:

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe http://localhost:8000/api/ma/2330 -H "X-API-Key: $key"
```

預期:HTTP 200,body 形如 `{"symbol":"2330","sma_5":1234.5,"sma_20":1210.0,"as_of_date":"2026-05-14"}`。
sma_5 / sma_20 是 number 或 null(看 indicator_cache 有沒有跑到),`as_of_date` 是最後一次成功 cache run 的日期。

- [ ] **Step 4: Smoke test 邊界 — 不存在的 symbol**

```powershell
curl.exe http://localhost:8000/api/ma/9999 -H "X-API-Key: $key"
```

預期:HTTP 200,body `{"symbol":"9999","sma_5":null,"sma_20":null,"as_of_date":"2026-05-14"}`(因為 indicator_cache 沒這 row,maybe_single 回 None,但 cache_runs 有,所以 as_of_date 有值)。

- [ ] **Step 5: Commit**

```powershell
cd C:\side-project\treading-king
git add backend/routes/ma.py backend/main.py
git commit -m "feat(backend): add GET /api/ma/{symbol} for SMA5/SMA20 chart overlay"
```

---

## Task 2: Frontend — `api.ma()` + type

**Files:**
- Modify: `frontend/src/lib/api.ts`(加 `MaLevels` type 跟 `api.ma()`)

- [ ] **Step 1: 加 `MaLevels` interface**

定位既有 `CdpLevels` interface(約 154-161 行),其後加:

```typescript
export interface MaLevels {
  symbol: string;
  sma_5: number | null;
  sma_20: number | null;
  as_of_date: string | null;
}
```

- [ ] **Step 2: 加 `api.ma()` 到 `api` 物件**

定位既有 `cdp: (symbol: string) => ...`(約 282-283 行),其後加:

```typescript
  cdp: (symbol: string) =>
    fetchJSON<CdpLevels>(`/api/cdp/${encodeURIComponent(symbol)}`),

  ma: (symbol: string) =>
    fetchJSON<MaLevels>(`/api/ma/${encodeURIComponent(symbol)}`),
```

- [ ] **Step 3: 型別檢查通過**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期:build 成功,沒 TS error。

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): add api.ma() + MaLevels type"
```

---

## Task 3: Frontend — `IntradayChart` 加 MA toggle + render

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: 加 import + state**

定位檔頂 import 區,把 `MaLevels` 加進既有的 api type import:

```typescript
import { api, type CdpLevels, type IntradayCandle, type MaLevels } from "../lib/api";
```

定位元件函式 body 開頭的 state 區(約 22-26 行),在 `cdpError` state 之後加:

```typescript
  const [showVwap, setShowVwap] = useState(true);
  const [showCdp, setShowCdp] = useLocalToggle("tk:chart:cdp", false);
  const [showVolume, setShowVolume] = useLocalToggle("tk:chart:volume", true);
  const [showMa, setShowMa] = useLocalToggle("tk:chart:ma", false);
  const [cdp, setCdp] = useState<CdpLevels | null>(null);
  const [cdpError, setCdpError] = useState<string | null>(null);
  const [ma, setMa] = useState<MaLevels | null>(null);
```

- [ ] **Step 2: 加 MA fetch useEffect**

定位既有的 CDP fetch `useEffect`(約 28-36 行),其後加 MA 版本:

```typescript
  useEffect(() => {
    // 切 symbol 時先清舊 MA — 避免新圖上殘留舊值
    setMa(null);
    if (!showMa) return;
    api.ma(symbol).then(setMa).catch((e) => {
      console.warn("MA fetch failed:", e);
    });
  }, [symbol, showMa]);
```

- [ ] **Step 3: 在 `useMemo` 加 `visibleMaKeys` 計算**

定位 `useMemo` block 結尾的 return(約 86-92 行),把 return 物件擴充:

```typescript
    // MA 可見性過濾(超出 ±10% 範圍的不畫,沿用 CDP 同邏輯)
    const allMaKeys = ["sma_5", "sma_20"] as const;
    const visibleMaKeys: Array<typeof allMaKeys[number]> = (showMa && ma)
      ? allMaKeys.filter((k) => {
          const v = ma[k];
          return v !== null && v >= refMin && v <= refMax;
        })
      : [];

    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
      maxVolume, scaleVolY, volBarW,
    };
```

也要把空陣列 fallback(`candles.length === 0` 那段)補 `visibleMaKeys`:

```typescript
    if (candles.length === 0) {
      return {
        yMin: 0, yMax: 0,
        scaleX: () => 0, scaleY: () => 0,
        polyClose: "", polyVwap: "",
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
        visibleMaKeys: [] as Array<"sma_5" | "sma_20">,
        todayHigh: 0, todayHighIdx: -1, todayLow: 0, todayLowIdx: -1,
        maxVolume: 0, scaleVolY: (_: number) => 0, volBarW: 0,
      };
    }
```

把 useMemo 上方的 destructure 也補 `visibleMaKeys`:

```typescript
  const {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
  } = useMemo(() => {
```

把 useMemo 的 deps array 補 `ma` 跟 `showMa`:

```typescript
  }, [candles, cdp, showCdp, ma, showMa, prevClose]);
```

- [ ] **Step 4: 在 SVG render 加 MA 兩條水平線**

定位既有 CDP 線 render 區塊(約 233-249 行 `{showCdp && cdp && visibleCdpKeys.length > 0 && (...)}`),其後、VWAP 區塊之前,插入 MA render:

```typescript
          {/* MA5 / MA20 兩條水平線(超出 ±10% 的隱藏) */}
          {showMa && ma && visibleMaKeys.length > 0 && (
            <>
              {visibleMaKeys.map((k) => {
                const v = ma[k]!;  // visibleMaKeys 已過濾過 non-null
                const isShort = k === "sma_5";
                const colorCls = isShort ? "stroke-bull" : "stroke-ink-dim";
                const labelCls = isShort ? "fill-bull" : "fill-ink-dim";
                return (
                  <g key={k}>
                    <line x1={PAD_L} y1={scaleY(v)} x2={CHART_W - PAD_R} y2={scaleY(v)}
                      className={colorCls} strokeWidth="0.6"
                      strokeDasharray="2 4" opacity="0.7" />
                    <text x={CHART_W - PAD_R + 4} y={scaleY(v) + 3} textAnchor="start"
                      className={`${labelCls} text-[12px] tabular-nums`}>
                      {isShort ? "MA5" : "MA20"} {formatTickPrice(v)}
                    </text>
                  </g>
                );
              })}
            </>
          )}
```

- [ ] **Step 5: 加 MA toggle 按鈕**

定位既有 toggle 按鈕區塊(約 372-388 行),在 `VOL` 按鈕之後加 `MA`:

```typescript
        <button
          type="button"
          onClick={() => setShowVolume((v) => !v)}
          className={`px-2 py-1 border ${showVolume ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showVolume ? "✓" : ""} VOL</button>
        <button
          type="button"
          onClick={() => setShowMa((v) => !v)}
          className={`px-2 py-1 border ${showMa ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showMa ? "✓" : ""} MA</button>
```

- [ ] **Step 6: 型別檢查通過**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期:build 成功,沒 TS error。

- [ ] **Step 7: Browser smoke test**

```powershell
cd C:\side-project\treading-king\frontend
npm run dev
```

開 <http://localhost:5173>:

1. 進 Monitor 頁,選一檔自選股(如 2330)
2. 點 `MA` toggle → 預期看到 2 條水平虛線(MA5 暖紅、MA20 灰),右側 margin 印 `MA5 605.5` / `MA20 590.0` 之類
3. F12 開 network panel,看 `/api/ma/2330` 請求 200 + body 帶 sma_5/sma_20
4. 切到另一檔(如 2454),預期 MA 線位置變動(對應該檔的日 MA)
5. 關掉 `MA` toggle,線消失;重新整理頁面,toggle 狀態被 localStorage 記住
6. 切到一檔 indicator_cache 沒跑到的新自選(如剛加的冷門股),預期不畫線、不報錯(console 沒 error)

- [ ] **Step 8: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(frontend): add MA5/MA20 horizontal overlay to IntradayChart"
```

---

## Done

兩條日 MA 線 + MA toggle 上線。資料來自既有 `indicator_cache.sma_5 / sma_20`(cache job 每日跑),不影響其他功能。

下一步可在 `docs/superpowers/plans/2026-05-15-cdp-proximity.md` 接著做 CDP 觸發條件。
