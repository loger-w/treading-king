# Camarilla Pivot 八線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在主圖加入 Camarilla Pivot 8 條 horizontal levels(H1–H4 / L1–L4),從昨日 OHLC 算出、跟 CDP 並列。

**Architecture:** 後端 mirror CDP — `services/camarilla.py` 純函式 + Service class,`routes/camarilla.py` GET endpoint,共用既有 `daily_ohlc` 表跟 `round_to_tick_tw` helper。前端在 `IntradayChart.tsx` 加 state / fetch / 8 條線 render / `CAM` toggle,沿用 `resolveCollisions` label 機制。

**Tech Stack:** Python 3 / FastAPI / pytest(後端);React + TypeScript + SVG(前端);Supabase(daily_ohlc 表);富邦 Neo SDK(historical.candles)。

**Spec:** `docs/superpowers/specs/2026-05-22-camarilla-pivot-design.md`

---

## File Structure

| File                                              | 動作 | 責任                                                         |
|---------------------------------------------------|------|-------------------------------------------------------------|
| `backend/services/camarilla.py`                   | 新   | `compute_camarilla()` 純函式 + `CamarillaService` cache class |
| `backend/routes/camarilla.py`                     | 新   | `GET /api/camarilla/{symbol}`                               |
| `backend/main.py`                                 | 改   | `include_router(camarilla.router)`                           |
| `backend/tests/test_camarilla.py`                 | 新   | `compute_camarilla` + service refresh / backfill tests     |
| `frontend/src/lib/api.ts`                         | 改   | `CamarillaLevels` type + `api.camarilla()`                  |
| `frontend/src/components/IntradayChart.tsx`       | 改   | state、useEffect、繪製、toggle                                |

---

## Task 1: `compute_camarilla` 純函式 + tests

**Files:**
- Create: `backend/services/camarilla.py`
- Create: `backend/tests/test_camarilla.py`

- [ ] **Step 1.1: Write the failing tests**

寫入 `backend/tests/test_camarilla.py`:

```python
"""驗 compute_camarilla 8 線數學 + 台股 tick 對齊。"""
from services.camarilla import compute_camarilla


def test_compute_camarilla_reference_values():
    """H=110, L=90, C=100 → rng=20 → h4=100+11=111, l4=89, h3=100+5.5=105.5(→ 5.5 對 5.5 tick), etc.

    100 元的 tick = 0.5(100 ≤ price < 500),所以 raw 105.5 round nearest 0.5 = 105.5,
    raw 111.0 round nearest 0.5 = 111.0。
    """
    levels = compute_camarilla(h=110.0, l=90.0, c=100.0)
    # rng × 1.1 / N for N in [2,4,6,12,12,6,4,2]
    assert levels["h4"] == 111.0  # raw 111.0, tick 0.5 → 111.0
    assert levels["h3"] == 105.5  # raw 105.5, tick 0.5 → 105.5
    assert levels["h2"] == 103.5  # raw 103.667, tick 0.5 → 103.5
    assert levels["h1"] == 102.0  # raw 101.833, tick 0.5 → 102.0
    assert levels["l1"] == 98.0   # raw 98.167, tick 0.5 → 98.0
    assert levels["l2"] == 96.5   # raw 96.333, tick 0.5 → 96.5
    assert levels["l3"] == 94.5   # raw 94.5, tick 0.5 → 94.5
    assert levels["l4"] == 89.0   # raw 89.0, tick 0.5 → 89.0


def test_compute_camarilla_low_price_tick():
    """價位 < 50 用 tick 0.05。H=50, L=49.5, C=49.8 → rng=0.5 → h4=49.8+0.275=50.075, round 0.05 = 50.10.

    但 H=50 已經跨過 tick 邊界 — round_to_tick_tw 用 price 對應的 tick,
    50.075 < 50,但 round 完 50.10 ≥ 50 — round helper 用「raw price」找 tick,raw 50.075 < 100 拿 tick 0.5? 不,< 50 拿 0.05。
    驗:50.075 / 0.05 = 1001.5 → round to even = 1002 → 50.10。
    """
    levels = compute_camarilla(h=50.0, l=49.5, c=49.8)
    # rng=0.5, raw h4=49.8 + 0.5*1.1/2 = 49.8+0.275 = 50.075 → tick 0.05 → 50.10
    assert levels["h4"] == 50.10
    # raw l4=49.8 - 0.275 = 49.525 → tick 0.05 → 49.50(round half to even:49.525/0.05=990.5→990→49.50)
    assert levels["l4"] == 49.50


def test_compute_camarilla_high_price_tick():
    """價位 ≥ 1000 用 tick 5.0。H=1020, L=980, C=1000 → rng=40."""
    levels = compute_camarilla(h=1020.0, l=980.0, c=1000.0)
    # raw h4 = 1000 + 40*1.1/2 = 1022.0 → tick 5.0 → 1020.0(round half to even)
    assert levels["h4"] == 1020.0
    # raw l4 = 1000 - 22 = 978.0 → tick 5.0 → 980.0
    assert levels["l4"] == 980.0


def test_compute_camarilla_zero_range_collapses_to_close():
    """H == L 時 rng=0,所有 level 都 round 到 close 的 tick。"""
    levels = compute_camarilla(h=100.0, l=100.0, c=100.0)
    for key in ("h4", "h3", "h2", "h1", "l1", "l2", "l3", "l4"):
        assert levels[key] == 100.0
```

- [ ] **Step 1.2: Run tests to verify they fail**

```powershell
cd C:\side-project\treading-king\backend; .\.venv\Scripts\activate; pytest tests/test_camarilla.py -v
```
Expected: 4 個測試全部 FAIL with `ModuleNotFoundError: No module named 'services.camarilla'`。

- [ ] **Step 1.3: Implement `compute_camarilla`**

寫入 `backend/services/camarilla.py`:

```python
"""Camarilla Pivot 8 線 — 從昨日 OHLC 算 8 個值,盤中為固定值。

公式(Nick Stott 原版):
  rng = H - L
  H4/L4 = C ± rng × 1.1 / 2    ← 突破位
  H3/L3 = C ± rng × 1.1 / 4    ← 反轉位
  H2/L2 = C ± rng × 1.1 / 6
  H1/L1 = C ± rng × 1.1 / 12   ← 最靠近昨收
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TypedDict

from services.cdp import round_to_tick_tw  # 共用 tick helper、不複製

logger = logging.getLogger(__name__)


class CamarillaLevels(TypedDict):
    h4: float
    h3: float
    h2: float
    h1: float
    l1: float
    l2: float
    l3: float
    l4: float
    as_of_date: str
    prev_close: float


def compute_camarilla(h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 從昨日 H/L/C 算 8 線,全部對齊台股 tick。"""
    rng = h - l
    raw = {
        "h4": c + rng * 1.1 / 2,
        "h3": c + rng * 1.1 / 4,
        "h2": c + rng * 1.1 / 6,
        "h1": c + rng * 1.1 / 12,
        "l1": c - rng * 1.1 / 12,
        "l2": c - rng * 1.1 / 6,
        "l3": c - rng * 1.1 / 4,
        "l4": c - rng * 1.1 / 2,
    }
    return {k: round_to_tick_tw(v, "nearest") for k, v in raw.items()}
```

- [ ] **Step 1.4: Run tests to verify they pass**

```powershell
pytest tests/test_camarilla.py -v
```
Expected: 4 個 PASS。

如果 `test_compute_camarilla_low_price_tick` 或其他價位 case fail,**先 print 一下 raw 跟 round 結果再 fix expected value**(可能 Python `round()` half-to-even 跟我估的不同):

```powershell
python -c "from services.camarilla import compute_camarilla; print(compute_camarilla(50.0, 49.5, 49.8))"
```
然後對照實際輸出更新 test 的 expected value(數學公式是對的,差異純粹是 round half-to-even 偶數捨入細節)。

- [ ] **Step 1.5: Commit**

```bash
git add backend/services/camarilla.py backend/tests/test_camarilla.py
git commit -m "feat(camarilla): add compute_camarilla pure function with TW tick rounding"
```

---

## Task 2: `CamarillaService` cache class + tests

**Files:**
- Modify: `backend/services/camarilla.py`(在既有檔案後加)
- Modify: `backend/tests/test_camarilla.py`(加 service tests)

- [ ] **Step 2.1: Write failing tests for service**

在 `backend/tests/test_camarilla.py` 後面加:

```python
"""Service tests — refresh / backfill / cache."""
from datetime import date
from unittest.mock import MagicMock

import pytest

from services import camarilla as cam_module


@pytest.mark.asyncio
async def test_service_refresh_caches_levels(monkeypatch):
    """mock daily_ohlc 回一筆 row → refresh → cache 命中 → has() True、get_cached() 回值。"""
    fake_supabase = MagicMock()
    fake_supabase.client = MagicMock()
    fake_supabase.client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"date": "2026-05-21", "high": 110.0, "low": 90.0, "close": 100.0}
    ]
    monkeypatch.setattr(cam_module, "get_supabase", lambda: fake_supabase)

    svc = cam_module.CamarillaService()
    await svc.refresh("2330")
    assert svc.has("2330")
    levels = svc._cache["2330"]  # 直接讀 cache 驗 schema
    assert levels["h4"] == 111.0
    assert levels["l4"] == 89.0
    assert levels["as_of_date"] == "2026-05-21"
    assert levels["prev_close"] == 100.0


@pytest.mark.asyncio
async def test_service_get_triggers_daily_backfill_once(monkeypatch):
    """同一 symbol 同一日,get() 只 trigger backfill 一次。"""
    svc = cam_module.CamarillaService()
    backfill_calls = []

    async def fake_backfill(symbol):
        backfill_calls.append(symbol)
        return True

    svc.backfill_from_fubon = fake_backfill  # type: ignore[method-assign]

    async def fake_refresh(symbol):
        svc._cache[symbol] = {
            "h4": 0, "h3": 0, "h2": 0, "h1": 0,
            "l1": 0, "l2": 0, "l3": 0, "l4": 0,
            "as_of_date": "2026-05-21", "prev_close": 0.0,
        }

    svc.refresh = fake_refresh  # type: ignore[method-assign]

    await svc.get("2330")
    await svc.get("2330")
    assert backfill_calls == ["2330"]  # 第二次不再 backfill


@pytest.mark.asyncio
async def test_service_refresh_missing_row_returns_silently(monkeypatch):
    """daily_ohlc 沒這 symbol 的 row → refresh 不 raise、cache 不命中。"""
    fake_supabase = MagicMock()
    fake_supabase.client = MagicMock()
    fake_supabase.client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    monkeypatch.setattr(cam_module, "get_supabase", lambda: fake_supabase)

    svc = cam_module.CamarillaService()
    await svc.refresh("UNKNOWN")
    assert not svc.has("UNKNOWN")


def test_get_camarilla_service_singleton():
    """get_camarilla_service() 回相同 instance(module-level singleton)。"""
    a = cam_module.get_camarilla_service()
    b = cam_module.get_camarilla_service()
    assert a is b
```

- [ ] **Step 2.2: Run tests to verify they fail**

```powershell
pytest tests/test_camarilla.py -v
```
Expected:Task 1 的 4 個 PASS,新 4 個 FAIL(`AttributeError: module 'services.camarilla' has no attribute 'CamarillaService'` 等)。

- [ ] **Step 2.3: Implement `CamarillaService`**

在 `backend/services/camarilla.py` 末尾(於 `compute_camarilla` 後)加:

```python
class CamarillaService:
    """In-memory cache + 從 daily_ohlc 抓昨日 OHLC 算 8 線。

    跟 CdpService 同設計:每天首次呼叫 trigger backfill_from_fubon 一次。
    """

    def __init__(self) -> None:
        self._cache: dict[str, CamarillaLevels] = {}
        self._lock = asyncio.Lock()
        self._last_backfill_attempt: dict[str, date] = {}

    async def get(self, symbol: str) -> CamarillaLevels | None:
        today = date.today()
        if self._last_backfill_attempt.get(symbol) != today:
            self._last_backfill_attempt[symbol] = today
            await self.backfill_from_fubon(symbol)

        if symbol not in self._cache:
            await self.refresh(symbol)

        return self._cache.get(symbol)

    async def refresh(self, symbol: str) -> None:
        """從 daily_ohlc 抓最近一筆 OHLC → 算 → 進 cache。"""
        from services.supabase_client import get_supabase

        sb = get_supabase()
        if sb.client is None:
            logger.warning("camarilla.refresh: supabase not ready")
            return

        res = (
            sb.client.table("daily_ohlc")
            .select("date, high, low, close")
            .eq("symbol", symbol)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            logger.info("camarilla.refresh: no daily_ohlc for %s yet", symbol)
            return
        row = rows[0]
        try:
            levels = compute_camarilla(
                float(row["high"]), float(row["low"]), float(row["close"]),
            )
            self._cache[symbol] = {
                "h4": levels["h4"], "h3": levels["h3"], "h2": levels["h2"], "h1": levels["h1"],
                "l1": levels["l1"], "l2": levels["l2"], "l3": levels["l3"], "l4": levels["l4"],
                "as_of_date": row["date"],
                "prev_close": float(row["close"]),
            }
            logger.debug("camarilla cached %s: %s", symbol, self._cache[symbol])
        except (ValueError, TypeError) as e:
            logger.warning("camarilla.refresh %s: bad data %s — %s", symbol, row, e)

    def discard(self, symbol: str) -> None:
        self._cache.pop(symbol, None)

    def has(self, symbol: str) -> bool:
        return symbol in self._cache

    async def backfill_from_fubon(self, symbol: str) -> bool:
        """打富邦 historical.candles 拉昨日 OHLC → upsert daily_ohlc → refresh cache。

        共用既有 historical rate limiter(60 req/min)。
        """
        from services.fubon_client import FubonStatus, get_fubon
        from services.rate_limiter import get_historical_rate_limiter
        from services.supabase_client import get_supabase

        fubon = get_fubon()
        sb = get_supabase()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.warning("camarilla.backfill: fubon not OK")
            return False
        if sb.client is None:
            logger.warning("camarilla.backfill: supabase not OK")
            return False

        today = date.today()
        last_week = today - timedelta(days=10)

        try:
            await asyncio.to_thread(get_historical_rate_limiter().acquire)
            r = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.stock.historical.candles,
                symbol=symbol,
                from_=last_week.isoformat(),
                to=today.isoformat(),
            )
        except Exception as e:
            logger.warning("camarilla.backfill %s: fubon error %s", symbol, e)
            return False

        rows = (r or {}).get("data") or []
        if not rows:
            logger.info("camarilla.backfill %s: no historical data", symbol)
            return False

        upserts = []
        for row in rows:
            d = row.get("date")
            if not d or d == today.isoformat():
                continue
            upserts.append({
                "symbol": symbol, "date": d,
                "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
            })

        if not upserts:
            logger.info("camarilla.backfill %s: only today data (no past)", symbol)
            return False

        try:
            await asyncio.to_thread(
                lambda: sb.client.table("daily_ohlc")
                .upsert(upserts, on_conflict="symbol,date")
                .execute()
            )
        except Exception as e:
            logger.error("camarilla.backfill %s: supabase upsert failed: %s", symbol, e)
            return False

        await self.refresh(symbol)
        logger.info("camarilla.backfill %s: %d days OHLC stored", symbol, len(upserts))
        return True


_service: CamarillaService | None = None


def get_camarilla_service() -> CamarillaService:
    global _service
    if _service is None:
        _service = CamarillaService()
    return _service
```

注意 — `from services.supabase_client import get_supabase` 是延遲 import(在 method 內),為了讓 test 用 `monkeypatch.setattr(cam_module, "get_supabase", ...)` 能蓋掉。看 test 設計:它 patch 的是 `services.camarilla.get_supabase`,所以**必須**在 module top-level 也 import 一次,讓 monkeypatch 找得到屬性。修正:

把 `from services.supabase_client import get_supabase` 從 method 內挪到 module top(`from services.cdp import round_to_tick_tw` 旁),這樣 `cam_module.get_supabase` 才存在可 patch。`refresh` 內直接呼叫 `get_supabase()` 即可,不再延遲 import。

最終 top imports 應為:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TypedDict

from services.cdp import round_to_tick_tw
from services.supabase_client import get_supabase
```

注意 `backfill_from_fubon` 內的 `get_fubon` / `get_historical_rate_limiter` 也建議移到 top(若 test 之後想 patch fubon,需要 module-level attribute)。本任務不測 backfill,可以暫時留延遲 import。

- [ ] **Step 2.4: Run tests to verify they pass**

```powershell
pytest tests/test_camarilla.py -v
```
Expected: 8 個全部 PASS。

- [ ] **Step 2.5: Commit**

```bash
git add backend/services/camarilla.py backend/tests/test_camarilla.py
git commit -m "feat(camarilla): add CamarillaService with daily_ohlc cache + fubon backfill"
```

---

## Task 3: `GET /api/camarilla/{symbol}` route

**Files:**
- Create: `backend/routes/camarilla.py`
- Modify: `backend/main.py`

Route handler 跟 `routes/cdp.py` 完全鏡像,不寫 unit test(handler 邏輯極薄、靠 service tests + 手動 curl 即可)。

- [ ] **Step 3.1: Write route**

寫入 `backend/routes/camarilla.py`:

```python
"""GET /api/camarilla/{symbol} — 回 Camarilla 8 線值。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.camarilla import get_camarilla_service

router = APIRouter()


@router.get("/api/camarilla/{symbol}")
async def get_camarilla(symbol: str) -> dict:
    levels = await get_camarilla_service().get(symbol)
    if levels is None:
        ok = await get_camarilla_service().backfill_from_fubon(symbol)
        if not ok:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable", "symbol": symbol})
        levels = await get_camarilla_service().get(symbol)
        if levels is None:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable_after_backfill"})
    return levels  # type: ignore[return-value]
```

- [ ] **Step 3.2: Register router in `main.py`**

在 `backend/main.py:17-22` 的 import block 改:

```python
from routes import (
    active_signals, bookmarks, camarilla, candles, cdp as cdp_route,
    ma,
    preview, quote, signals_history, symbols,
    watchlist, ws,
)  # noqa: E402
```

在 `backend/main.py:135`(`app.include_router(cdp_route.router)`)下一行加:

```python
app.include_router(camarilla.router)
```

確認最終 router block 是:

```python
app.include_router(candles.router)
app.include_router(cdp_route.router)
app.include_router(camarilla.router)
app.include_router(ma.router)
app.include_router(ws.router)
```

- [ ] **Step 3.3: Manual smoke test**

啟動 backend:

```powershell
cd C:\side-project\treading-king\backend; .\.venv\Scripts\activate; uvicorn main:app --reload
```

另開 PowerShell 視窗 curl(替換實際 USER_LABEL 對應 BFF_API_KEY):

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String 'BFF_API_KEY=' | ForEach-Object { $_.Line -replace 'BFF_API_KEY=', '' })
curl http://localhost:8000/api/camarilla/2330 -H "X-API-Key: $key"
```

Expected: JSON 含 8 個 level + as_of_date + prev_close,符合 spec API contract。

若回 503,先確認:
- `/api/cdp/2330` 是否能正常回(同樣依賴 daily_ohlc + fubon backfill)— 能回 = Camarilla 也應該能回
- backend log 有沒有 `camarilla.backfill` 字樣
- supabase `daily_ohlc` 表是否有 2330 的 row

- [ ] **Step 3.4: Commit**

```bash
git add backend/routes/camarilla.py backend/main.py
git commit -m "feat(camarilla): add GET /api/camarilla/{symbol} route"
```

---

## Task 4: Frontend `api.ts` — type + fetcher

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 4.1: Add `CamarillaLevels` type**

在 `frontend/src/lib/api.ts:199`(`CdpLevels` interface 下面)加:

```typescript
export interface CamarillaLevels {
  h4: number;
  h3: number;
  h2: number;
  h1: number;
  l1: number;
  l2: number;
  l3: number;
  l4: number;
  as_of_date: string;
  prev_close: number;
}
```

- [ ] **Step 4.2: Add `api.camarilla()` fetcher**

在 `frontend/src/lib/api.ts:384`(`cdp: (symbol: string) => ...` 下面)加:

```typescript
  camarilla: (symbol: string) =>
    fetchJSON<CamarillaLevels>(`/api/camarilla/${encodeURIComponent(symbol)}`),
```

最終 `api` object 順序應為:`cdp`, `camarilla`, `ma`, `quotesSnapshot`。

- [ ] **Step 4.3: Verify TypeScript compiles**

```powershell
cd C:\side-project\treading-king\frontend; npm run build
```

Expected: 編譯通過、沒新 error。如果 `tsc` 報 unused type,先 ignore — 下個任務會用到。

- [ ] **Step 4.4: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(camarilla): add CamarillaLevels type and api.camarilla() fetcher"
```

---

## Task 5: IntradayChart — state + toggle button

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 5.1: Import `CamarillaLevels` type**

修改 `frontend/src/components/IntradayChart.tsx:2`:

```typescript
import { api, type CamarillaLevels, type CdpLevels, type IntradayCandle, type MaLevels } from "../lib/api";
```

(加 `type CamarillaLevels,` 在 `type CdpLevels` 前面,字母序)

- [ ] **Step 5.2: Add `showCamarilla` toggle state**

在 `frontend/src/components/IntradayChart.tsx:47`(`const [showCdp, setShowCdp] = ...`)下一行加:

```typescript
  const [showCamarilla, setShowCamarilla] = useLocalToggle("tk:chart:camarilla", false);
```

- [ ] **Step 5.3: Add `camarilla` data state + fetch effect**

在 `frontend/src/components/IntradayChart.tsx:53-55`(`const [cdp, setCdp] = useState ...` 跟 `cdpError` 那兩行)下一行加:

```typescript
  const [camarilla, setCamarilla] = useState<CamarillaLevels | null>(null);
  const [camarillaError, setCamarillaError] = useState<string | null>(null);
```

在 `frontend/src/components/IntradayChart.tsx:65`(`api.cdp(symbol).then(setCdp).catch(...)` 的 `useEffect` 結束的 `}, [symbol, showCdp]);` 下一行)加新的 useEffect:

```typescript
  useEffect(() => {
    setCamarilla(null);
    setCamarillaError(null);
    if (!showCamarilla) return;
    api.camarilla(symbol).then(setCamarilla).catch((e) =>
      setCamarillaError(e instanceof Error ? e.message : String(e))
    );
  }, [symbol, showCamarilla]);
```

- [ ] **Step 5.4: Add `CAM` toggle button**

在 `frontend/src/components/IntradayChart.tsx:892`(`{showCdp ? "✓" : ""} CDP</button>` 下一行)加:

```tsx
        <button
          type="button"
          onClick={() => setShowCamarilla((v) => !v)}
          className={`px-2 py-1 border ${showCamarilla ? "border-accent text-accent" : "border-line text-ink-dim"}`}
        >{showCamarilla ? "✓" : ""} CAM</button>
```

- [ ] **Step 5.5: Add error UI**

在 `frontend/src/components/IntradayChart.tsx:921`(`{showCdp && cdpError && ...` 那個 div 下一行)加:

```tsx
      {showCamarilla && camarillaError && (
        <div className="mt-1 text-xs text-bear">Camarilla 無資料:{camarillaError}</div>
      )}
```

- [ ] **Step 5.6: Smoke test in browser**

啟動 frontend(若還沒):

```powershell
cd C:\side-project\treading-king\frontend; npm run dev
```

打開 `http://localhost:5173/`、選一個有 CDP 資料的 symbol(例:2330)、點 `CAM` toggle。

Expected:
- toggle 按鈕亮起(border-accent)
- 沒繪製任何東西(下個任務做)
- Console 沒 fetch error(可在 Network tab 看到 200 回 8 個 level)

如果 toggle 報錯,先 fix。

- [ ] **Step 5.7: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(camarilla): add CAM toggle state and fetch in IntradayChart"
```

---

## Task 6: IntradayChart — 繪製 8 條線 + 4 條 label

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 6.1: 在 `useMemo` 計算 visible keys + 加入 label inputs**

找到 `IntradayChart.tsx:113-117` 的 CDP visible keys 計算:

```typescript
    const allCdpKeys = ["ah", "nh", "cdp", "nl", "al"] as const;
    const visibleCdpKeys: Array<typeof allCdpKeys[number]> = (showCdp && cdp)
      ? allCdpKeys.filter((k) => cdp[k] >= refMin && cdp[k] <= refMax)
      : [];
```

在下面**緊接著**加 Camarilla visible keys:

```typescript
    const allCamKeys = ["h4", "h3", "h2", "h1", "l1", "l2", "l3", "l4"] as const;
    const visibleCamKeys: Array<typeof allCamKeys[number]> = (showCamarilla && camarilla)
      ? allCamKeys.filter((k) => camarilla[k] >= refMin && camarilla[k] <= refMax)
      : [];
    // 只顯示 4 條主 level 的 label(H3/L3/H4/L4),H1/H2/L1/L2 不顯示 label 避免擠
    const labeledCamKeys: Array<typeof allCamKeys[number]> = visibleCamKeys.filter(
      (k) => k === "h3" || k === "h4" || k === "l3" || k === "l4"
    );
```

找到 `IntradayChart.tsx:172-181` 的 labelInputs CDP 段:

```typescript
    if (showCdp && cdp) {
      for (const k of visibleCdpKeys) {
        labelInputs.push({
          originalY: scaleY(cdp[k]),
          text: formatTickPrice(cdp[k]),
          color: "#e85a4f",  // accent 紅
        });
      }
    }
```

在它**後面緊接著**加(在 MA labelInputs 之前):

```typescript
    if (showCamarilla && camarilla) {
      for (const k of labeledCamKeys) {
        labelInputs.push({
          originalY: scaleY(camarilla[k]),
          text: formatTickPrice(camarilla[k]),
          color: "#3b82f6",  // Camarilla 藍
        });
      }
    }
```

- [ ] **Step 6.2: 在 useMemo return 加 visibleCamKeys**

`IntradayChart.tsx:211-219` 是 useMemo return object。**新增** `visibleCamKeys` 到 return:

```typescript
    return {
      yMin, yMax, scaleX, scaleY,
      polyClose, polyVwap, visibleCdpKeys, visibleCamKeys, visibleMaKeys,
      todayHigh, todayHighIdx, todayLow, todayLowIdx,
      maxVolume, scaleVolY, volBarW,
      resolvedLabels,
      elderRay, fisher, stc,
      volPane, elderPane, fisherPane, stcPane, totalH,
    };
```

在 `IntradayChart.tsx:84-103` 的 candles.length === 0 早返 object 也加同樣 key 避免 undefined:

```typescript
      return {
        // ... 原本欄位 ...
        visibleCdpKeys: [] as Array<"ah" | "nh" | "cdp" | "nl" | "al">,
        visibleCamKeys: [] as Array<"h4" | "h3" | "h2" | "h1" | "l1" | "l2" | "l3" | "l4">,
        visibleMaKeys: [] as Array<"sma_5" | "sma_20">,
        // ... 其他原本欄位 ...
      };
```

在 `IntradayChart.tsx:76-83` 的 useMemo destructure 加:

```typescript
  const {
    yMin, yMax, scaleX, scaleY,
    polyClose, polyVwap, visibleCdpKeys, visibleCamKeys, visibleMaKeys,
    todayHigh, todayHighIdx, todayLow, todayLowIdx,
    maxVolume, scaleVolY, volBarW,
    resolvedLabels,
    elderRay, fisher, stc,
    volPane, elderPane, fisherPane, stcPane, totalH,
  } = useMemo(() => {
```

- [ ] **Step 6.3: 加 useMemo deps**

`IntradayChart.tsx:220-221` useMemo deps 結尾 `}, [...])`,加 `camarilla`, `showCamarilla`:

```typescript
  }, [candles, cdp, showCdp, camarilla, showCamarilla, ma, showMa, showVwap, prevClose,
      showVolume, showElderRay, showFisher, showSTC]);
```

- [ ] **Step 6.4: 繪製 8 條 Camarilla line**

找到 `IntradayChart.tsx:361-370` CDP 繪製區段:

```tsx
          {/* CDP 5 線(超出 ±10% 範圍的隱藏)— label 留給統一 resolvedLabels render */}
          {showCdp && cdp && visibleCdpKeys.length > 0 && (
            <>
              {visibleCdpKeys.map((k) => (
                <line key={k}
                  x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
                  stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
                  strokeDasharray="4 3" opacity="0.6" />
              ))}
            </>
          )}
```

在它**後面緊接著**加 Camarilla 繪製:

```tsx
          {/* Camarilla 8 線 — H3/L3/H4/L4 主反轉/突破位較粗、H1/H2/L1/L2 弱化 */}
          {showCamarilla && camarilla && visibleCamKeys.length > 0 && (
            <>
              {visibleCamKeys.map((k) => {
                const isMain = k === "h3" || k === "h4" || k === "l3" || k === "l4";
                return (
                  <line key={`cam-${k}`}
                    x1={PAD_L} y1={scaleY(camarilla[k])} x2={CHART_W - PAD_R} y2={scaleY(camarilla[k])}
                    stroke="#3b82f6"
                    strokeWidth={isMain ? "0.8" : "0.6"}
                    strokeDasharray="2 4"
                    opacity={isMain ? "0.75" : "0.4"} />
                );
              })}
            </>
          )}
```

- [ ] **Step 6.5: TypeScript build check**

```powershell
cd C:\side-project\treading-king\frontend; npm run build
```

Expected: 編譯通過。如果有 type error,通常是 destructure 漏 / deps 漏 / null 沒處理 — 對照上面步驟修正。

- [ ] **Step 6.6: Visual smoke test in browser**

啟動 frontend(若還沒):

```powershell
cd C:\side-project\treading-king\frontend; npm run dev
```

打開 `http://localhost:5173/`,測試 checklist:

- [ ] 選 symbol 2330,點 CAM toggle → 主圖出現 8 條藍色 dotted line
- [ ] H3 / L3 / H4 / L4 較粗,H1 / H2 / L1 / L2 較淡
- [ ] 右邊 margin 出現 4 個藍色 label(對應 H3/L3/H4/L4),不重疊既有 CDP/MA/VWAP label
- [ ] 同時開 CDP + CAM → 兩組線並存、紅藍不混淆
- [ ] 切到另一 symbol(例 2317)→ Camarilla 重抓、不殘留前一個的線
- [ ] 點 CAM 兩次(off → on → off)→ 線消失/重出現正常
- [ ] 點一個沒 daily_ohlc 的 symbol(如剛 IPO 股)→ 看到 `Camarilla 無資料:…` error message

- [ ] **Step 6.7: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(camarilla): render 8 horizontal lines with H3/L3/H4/L4 emphasized labels"
```

---

## Task 7: 最後驗收 + Sanity check

**Files:** 無變更,純 manual verification

- [ ] **Step 7.1: 後端測試全跑一次**

```powershell
cd C:\side-project\treading-king\backend; .\.venv\Scripts\activate; pytest tests/ -v
```

Expected: 所有 test PASS(包含既有 CDP / signal_engine 等 + 新 Camarilla 8 個)。

如果既有 test 因為我的改動 fail,**立即 fix**(不要繼續往下)。

- [ ] **Step 7.2: 前端 build 一次**

```powershell
cd C:\side-project\treading-king\frontend; npm run build
```

Expected: PASS、無 type error。

- [ ] **Step 7.3: End-to-end manual flow**

啟動完整 stack:

```powershell
cd C:\side-project\treading-king; .\start.ps1
```

走完使用者流程:

1. 開瀏覽器 `http://localhost:5173/`
2. 選台積電 2330
3. 同時開啟 VWAP / CDP / CAM / MA 四種主圖 overlay
4. 驗證視覺:藍 8 條 + 紅 5 條 + 紫黃 2 條 + 灰 VWAP 都清晰可辨,右邊 label 不重疊
5. 切到另一個 symbol(2317)→ 全部正確切換
6. 關掉 CAM toggle → 線跟 label 一起消失
7. 重整網頁 → CAM 預設關閉(localStorage 沒記到 true,如果記到了就確認 toggle 重新打開後一切正常)

- [ ] **Step 7.4: 文件更新 + spec 標記 Implemented**

修改 `docs/superpowers/specs/2026-05-22-camarilla-pivot-design.md` 第三行:

```markdown
**Status**: Implemented
```

可選 — 加實作 commit hash 一行:

```markdown
**Date**: 2026-05-22
**Status**: Implemented(commit `<TASK 6.7 commit hash>`)
```

- [ ] **Step 7.5: Final commit + cleanup**

```bash
git add docs/superpowers/specs/2026-05-22-camarilla-pivot-design.md
git commit -m "docs(camarilla): mark spec as implemented"
```

確認 git log:

```powershell
git log --oneline -10
```

Expected: 看到本 plan 對應的 ~6 個 commit(可能合併),最後一個是 docs commit。

---

## Self-Review

**1. Spec coverage:**
- Spec § Goal — 後端 + endpoint + UI ✓ (Task 1-6)
- Spec § Architecture 後端 services/camarilla.py ✓ (Task 1-2)
- Spec § Architecture 後端 routes/camarilla.py ✓ (Task 3)
- Spec § Architecture main.py include_router ✓ (Task 3.2)
- Spec § Architecture frontend api.ts type + fetcher ✓ (Task 4)
- Spec § Architecture frontend IntradayChart ✓ (Task 5-6)
- Spec § API contract 503 + json schema ✓ (Task 3.1)
- Spec § UI 樣式 表(line width / opacity / dasharray) ✓ (Task 6.4)
- Spec § Label 只顯示 4 條 H3/L3/H4/L4 ✓ (Task 6.1)
- Spec § ±10% Y 軸 visibility 過濾 ✓ (Task 6.1)
- Spec § 測試 backend pytest 4 種 case ✓ (Task 1.1)
- Spec § 測試 service refresh / backfill ✓ (Task 2.1)
- Spec § Non-goals 無 signal detection / 無 DB persistence — plan 中沒加這些 ✓

**2. Placeholder scan:** 全部 step 都有 exact 路徑 + exact code + exact 命令、無 TBD / TODO / "similar to" — ✓

**3. Type consistency:**
- `CamarillaLevels` (Task 1.3 backend TypedDict、Task 4.1 frontend interface)欄位順序跟名字一致:`h4 h3 h2 h1 l1 l2 l3 l4 as_of_date prev_close` ✓
- `allCamKeys` array 順序在 Task 6.1 跟 Task 6.2 早返 object 一致:`["h4", "h3", "h2", "h1", "l1", "l2", "l3", "l4"]` ✓
- `get_camarilla_service` (Task 2.3) 跟 Task 3.1 引用名 一致 ✓
- API path `/api/camarilla/{symbol}` 後端 (Task 3.1) 跟前端 (Task 4.2) 一致 ✓
