# Chart Polish + Signal Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一輪 bundled 改動,4 個 PR 分別處理:即時走勢圖 label 修整、五檔鎖漲跌停顯示、MA 訊號條件、CDP/MA 觸發方向 + 觸碰計次。

**Architecture:** 前端 React + SVG(IntradayChart / QuoteBook / ActiveSignalEditor / TriggerList),後端 FastAPI + Pydantic(routes/ma + quote、signal_engine、condition model)。新增 1 個前端純函式 module (chart-labels.ts) + 1 個後端 service (ma_service.py)。其餘是現成檔案的擴充。

**Tech Stack:** React 18, TypeScript, Vite, SVG, FastAPI, Pydantic v2, asyncio, pytest, vitest(新加)

**Spec:** [docs/superpowers/specs/2026-05-19-chart-and-signal-tweaks-design.md](../specs/2026-05-19-chart-and-signal-tweaks-design.md)

---

## Task 0: Set up test infrastructure (one-time)

**Files:**
- Create: `backend/tests/__init__.py`(空)
- Create: `backend/tests/conftest.py`
- Modify: `backend/pyproject.toml`(加 `[tool.pytest.ini_options]`)
- Create: `frontend/vitest.config.ts`
- Modify: `frontend/package.json`(加 vitest devDep + `test` script)
- Modify: `frontend/tsconfig.json`(`types: ["vitest/globals"]`)

- [ ] **Step 1: Create backend pytest skeleton**

```python
# backend/tests/__init__.py
# (empty)
```

```python
# backend/tests/conftest.py
"""Shared fixtures."""
import sys
from pathlib import Path

# 讓 tests 可以 import backend/ 下的 module 不用裝成 package
sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 2: Add pytest config to pyproject.toml**

Add to `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
```

Also append to `[project.optional-dependencies]` dev:

```toml
dev = ["pytest>=8", "pytest-asyncio>=0.23"]
```

- [ ] **Step 3: Install pytest-asyncio**

```bash
cd backend && .venv/Scripts/pip install pytest-asyncio
```

- [ ] **Step 4: Smoke-test backend pytest**

```python
# backend/tests/test_smoke.py
def test_smoke():
    assert True
```

Run: `cd backend && .venv/Scripts/pytest tests/test_smoke.py -v`
Expected: `1 passed`

- [ ] **Step 5: Install frontend vitest**

```bash
cd frontend && npm install -D vitest @types/node
```

- [ ] **Step 6: Add vitest config**

Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

Add to `frontend/package.json` scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Add to `frontend/tsconfig.json` `compilerOptions.types`:

```json
"types": ["vitest/globals"]
```

- [ ] **Step 7: Smoke-test vitest**

Create `frontend/src/lib/smoke.test.ts`:

```ts
import { test, expect } from "vitest";
test("smoke", () => { expect(1 + 1).toBe(2); });
```

Run: `cd frontend && npm test`
Expected: `1 passed`

Delete smoke files after verification.

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/tests/__init__.py backend/tests/conftest.py frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/tsconfig.json
git commit -m "chore(test): wire up pytest + vitest infra"
```

---

## PR #1 — Frontend chart polish (Item 1 + Item 2)

## Task 1: Item 1 — Remove MA5/MA20 text from label

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx:315`

- [ ] **Step 1: Edit label text**

Find this block (around line 308-320):

```tsx
<text x={CHART_W - PAD_R + 4} y={scaleY(v) + 3} textAnchor="start"
  className={`${labelCls} text-[12px] tabular-nums`}>
  {isShort ? "MA5" : "MA20"} {formatTickPrice(v)}
</text>
```

Replace the text content:

```tsx
<text x={CHART_W - PAD_R + 4} y={scaleY(v) + 3} textAnchor="start"
  className={`${labelCls} text-[12px] tabular-nums`}>
  {formatTickPrice(v)}
</text>
```

- [ ] **Step 2: Manual verify**

Run dev server:
```bash
cd frontend && npm run dev
```
Open browser, pick any symbol, toggle MA on. Confirm label is e.g. `123.50` without "MA5"/"MA20" prefix.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(chart): drop MA5/MA20 text prefix from labels"
```

---

## Task 2: Item 2 — `resolveCollisions` pure function (TDD)

**Files:**
- Create: `frontend/src/lib/chart-labels.ts`
- Create: `frontend/src/lib/chart-labels.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/lib/chart-labels.test.ts`:

```ts
import { describe, test, expect } from "vitest";
import { resolveCollisions, type LabelInput } from "./chart-labels";

const RANGE: [number, number] = [0, 400];

describe("resolveCollisions", () => {
  test("無重疊 - y 保持不變", () => {
    const items: LabelInput[] = [
      { originalY: 50,  text: "a", color: "red" },
      { originalY: 100, text: "b", color: "blue" },
      { originalY: 200, text: "c", color: "green" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.y)).toEqual([50, 100, 200]);
  });

  test("兩個重疊 - 下方推到 minGap 外", () => {
    const items: LabelInput[] = [
      { originalY: 100, text: "a", color: "red" },
      { originalY: 110, text: "b", color: "blue" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result[0].y).toBe(100);
    expect(result[1].y).toBe(116);
  });

  test("5 個全擠在 100~120 - 連續推開", () => {
    const items: LabelInput[] = [
      { originalY: 100, text: "a", color: "red" },
      { originalY: 105, text: "b", color: "blue" },
      { originalY: 110, text: "c", color: "green" },
      { originalY: 115, text: "d", color: "yellow" },
      { originalY: 120, text: "e", color: "purple" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.y)).toEqual([100, 116, 132, 148, 164]);
  });

  test("推出下邊界 - 回彈", () => {
    // yRange=[0,200] minGap=16,3 個 originalY 接近 200 必須回彈
    const items: LabelInput[] = [
      { originalY: 180, text: "a", color: "red" },
      { originalY: 190, text: "b", color: "blue" },
      { originalY: 195, text: "c", color: "green" },
    ];
    const result = resolveCollisions(items, 16, [0, 200]);
    // pass1: 180, 196, 212 -> 最後一個 212 > 200
    // pass2 回彈:212 -> 200, 196 -> 184, 180 -> 168
    expect(result.map((r) => r.y)).toEqual([168, 184, 200]);
  });

  test("input 順序與 originalY 不同 - 結果按 originalY 升序", () => {
    const items: LabelInput[] = [
      { originalY: 200, text: "z", color: "red" },
      { originalY: 50,  text: "a", color: "blue" },
      { originalY: 100, text: "m", color: "green" },
    ];
    const result = resolveCollisions(items, 16, RANGE);
    expect(result.map((r) => r.text)).toEqual(["a", "m", "z"]);
    expect(result.map((r) => r.y)).toEqual([50, 100, 200]);
  });

  test("空 input - 回空陣列", () => {
    expect(resolveCollisions([], 16, RANGE)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test src/lib/chart-labels.test.ts`
Expected: 6 tests fail with "Cannot find module './chart-labels'"

- [ ] **Step 3: Implement `resolveCollisions`**

Create `frontend/src/lib/chart-labels.ts`:

```ts
export interface LabelInput {
  originalY: number;
  text: string;
  color: string;
}

export interface LabelOutput extends LabelInput {
  y: number;
}

/**
 * 把一組落在不同 y 的 label 撐開到彼此距離 >= minGap。
 *
 * Pass 1:依 originalY 升序,從上往下推 — y[i] = max(y[i], y[i-1] + minGap)
 * Pass 2:若 y[last] > yRange[1],從下往上回彈 — y[i] = min(y[i], y[i+1] - minGap)
 *
 * 回傳陣列保證按 y 升序;對 input 順序不敏感(內部會排)。
 */
export function resolveCollisions(
  items: LabelInput[],
  minGap: number,
  yRange: [number, number],
): LabelOutput[] {
  if (items.length === 0) return [];

  const sorted = [...items]
    .sort((a, b) => a.originalY - b.originalY)
    .map((it) => ({ ...it, y: it.originalY }));

  for (let i = 1; i < sorted.length; i++) {
    const minY = sorted[i - 1].y + minGap;
    if (sorted[i].y < minY) sorted[i].y = minY;
  }

  const last = sorted[sorted.length - 1].y;
  if (last > yRange[1]) {
    sorted[sorted.length - 1].y = yRange[1];
    for (let i = sorted.length - 2; i >= 0; i--) {
      const maxY = sorted[i + 1].y - minGap;
      if (sorted[i].y > maxY) sorted[i].y = maxY;
    }
  }

  return sorted;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test src/lib/chart-labels.test.ts`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/chart-labels.ts frontend/src/lib/chart-labels.test.ts
git commit -m "feat(chart): add resolveCollisions pure function"
```

---

## Task 3: Item 2 — Wire `resolveCollisions` into IntradayChart

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

- [ ] **Step 1: Add import**

At the top of `IntradayChart.tsx` (after existing imports):

```tsx
import { resolveCollisions, type LabelInput } from "../lib/chart-labels";
```

- [ ] **Step 2: Build label list inside useMemo**

In the existing `useMemo` block (starts around line 60), after `visibleMaKeys` is computed, add:

```tsx
// 收集右邊 margin 所有 label,做碰撞撐開
const labelInputs: LabelInput[] = [];

if (showCdp && cdp) {
  for (const k of visibleCdpKeys) {
    labelInputs.push({
      originalY: scaleY(cdp[k]),
      text: formatTickPrice(cdp[k]),
      color: "var(--color-accent, #e85a4f)",
    });
  }
}

if (showMa && ma) {
  for (const k of visibleMaKeys) {
    const v = roundToNearestTick(ma[k]!);
    labelInputs.push({
      originalY: scaleY(v),
      text: formatTickPrice(v),
      color: k === "sma_5" ? "var(--color-ma5)" : "var(--color-ma20)",
    });
  }
}

if (showVwap && candles.length > 0) {
  const lastAvg = candles[candles.length - 1].average;
  labelInputs.push({
    originalY: scaleY(lastAvg),
    text: formatTickPrice(lastAvg),
    color: "var(--color-ink-dim, #8a8273)",
  });
}

const resolvedLabels = resolveCollisions(
  labelInputs,
  16,
  [PAD_T, CHART_H - PAD_B],
);
```

Then add `resolvedLabels` to the return object:

```tsx
return {
  yMin, yMax, scaleX, scaleY,
  polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
  todayHigh, todayHighIdx, todayLow, todayLowIdx,
  maxVolume, scaleVolY, volBarW,
  resolvedLabels,  // NEW
};
```

And destructure it at the top of the function body:

```tsx
const {
  yMin, yMax, scaleX, scaleY,
  polyClose, polyVwap, visibleCdpKeys, visibleMaKeys,
  todayHigh, todayHighIdx, todayLow, todayLowIdx,
  maxVolume, scaleVolY, volBarW,
  resolvedLabels,  // NEW
} = useMemo(...)
```

- [ ] **Step 3: Replace existing CDP / MA / VWAP label render with unified loop**

Find the CDP label render block (around line 280-294):

```tsx
{showCdp && cdp && visibleCdpKeys.length > 0 && (
  <>
    {visibleCdpKeys.map((k) => (
      <g key={k}>
        <line x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
          stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
          strokeDasharray="4 3" opacity="0.6" />
        <text x={CHART_W - PAD_R + 4} y={scaleY(cdp[k]) + 3} textAnchor="start"
          className="fill-accent text-[12px] tabular-nums">
          {formatTickPrice(cdp[k])}
        </text>
      </g>
    ))}
  </>
)}
```

Replace with **lines only**(label 在後面另畫):

```tsx
{showCdp && cdp && visibleCdpKeys.length > 0 && (
  <>
    {visibleCdpKeys.map((k) => (
      <line key={k} x1={PAD_L} y1={scaleY(cdp[k])} x2={CHART_W - PAD_R} y2={scaleY(cdp[k])}
        stroke="var(--color-accent, #e85a4f)" strokeWidth="0.6"
        strokeDasharray="4 3" opacity="0.6" />
    ))}
  </>
)}
```

Same for MA block (around line 301-321) — keep only the `<line>` parts, remove the `<text>` part.

For VWAP — find the block that renders the last-value label (around line 328-337) and **delete it entirely** (the polyline stays).

- [ ] **Step 4: Render resolved labels in a single block**

Add this after all line renders (after VWAP polyline render):

```tsx
{/* 右邊 margin label - 自動碰撞撐開,引導線指回原 y */}
{resolvedLabels.map((lbl, i) => (
  <g key={`lbl-${i}`}>
    {lbl.y !== lbl.originalY && (
      <line x1={CHART_W - PAD_R} y1={lbl.originalY}
        x2={CHART_W - PAD_R + 4} y2={lbl.y}
        stroke={lbl.color} strokeWidth="0.7" opacity="0.5" />
    )}
    <text x={CHART_W - PAD_R + 6} y={lbl.y + 3} textAnchor="start"
      style={{ fill: lbl.color }}
      className="text-[12px] tabular-nums">
      {lbl.text}
    </text>
  </g>
))}
```

- [ ] **Step 5: Type check + manual verify**

```bash
cd frontend && npx tsc -b --noEmit
```
Expected: no type errors.

```bash
npm run dev
```
Open chart, toggle CDP + MA + VWAP all on, confirm:
- 5 條線都畫得出
- label 不重疊
- 重疊時看到引導線從原本 y 拉到撐開後的 y

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "feat(chart): collision-free right-margin labels with leader lines"
```

---

## PR #2 — Quote book limit-up/down fix (Item 3)

## Task 4: Item 3 — Backend forward isLimitUp/Down flags

**Files:**
- Modify: `backend/routes/quote.py:38-46`

- [ ] **Step 1: Edit get_quote response**

Replace lines 38-46 in `backend/routes/quote.py`:

```python
    try:
        result = await fubon.intraday_quote(symbol)
        return {
            "bids": result.get("bids", []),
            "asks": result.get("asks", []),
            "is_limit_up_bid":   result.get("isLimitUpBid", False),
            "is_limit_up_ask":   result.get("isLimitUpAsk", False),
            "is_limit_down_bid": result.get("isLimitDownBid", False),
            "is_limit_down_ask": result.get("isLimitDownAsk", False),
        }
    except Exception as e:
        logger.warning("intraday_quote(%s) failed: %s", symbol, e)
        raise HTTPException(502, detail={"error": "fubon_call_failed", "detail": str(e)})
```

- [ ] **Step 2: Write integration test**

Create `backend/tests/test_quote_limit_flags.py`:

```python
"""驗 quote endpoint forward 富邦 isLimitUp/Down flags。"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_forward_limit_up_bid_flag(client):
    """鎖漲停時 isLimitUpBid=True 要 forward 到 response。"""
    fake_result = {
        "bids": [{"price": 100.0, "size": 5000}],
        "asks": [{"price": 0.0, "size": 0}],
        "isLimitUpBid": True,
        "isLimitUpAsk": False,
        "isLimitDownBid": False,
        "isLimitDownAsk": False,
    }
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    body = r.json()
    assert body["is_limit_up_bid"] is True
    assert body["is_limit_down_ask"] is False
    assert body["bids"] == [{"price": 100.0, "size": 5000}]


def test_missing_flags_default_to_false(client):
    """富邦 response 沒帶 flag 時要預設 False(向後相容)。"""
    fake_result = {"bids": [], "asks": []}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    body = r.json()
    assert body["is_limit_up_bid"] is False
    assert body["is_limit_up_ask"] is False
    assert body["is_limit_down_bid"] is False
    assert body["is_limit_down_ask"] is False
```

- [ ] **Step 3: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_quote_limit_flags.py -v
```
Expected: `2 passed`

- [ ] **Step 4: Commit**

```bash
git add backend/routes/quote.py backend/tests/test_quote_limit_flags.py
git commit -m "feat(quote): forward isLimitUp/Down flags from fubon API"
```

---

## Task 5: Item 3 — Frontend QuoteBook 市價 display

**Files:**
- Modify: `frontend/src/lib/api.ts:37-40`
- Modify: `frontend/src/hooks/useQuoteBook.ts`
- Modify: `frontend/src/components/QuoteBook.tsx`

- [ ] **Step 1: Extend QuoteResponse type**

In `frontend/src/lib/api.ts` line 37-40:

```ts
export interface QuoteResponse {
  bids?: Array<{ price: number; size: number }>;
  asks?: Array<{ price: number; size: number }>;
  is_limit_up_bid?: boolean;
  is_limit_up_ask?: boolean;
  is_limit_down_bid?: boolean;
  is_limit_down_ask?: boolean;
}
```

- [ ] **Step 2: Extend useQuoteBook return shape**

In `frontend/src/hooks/useQuoteBook.ts`, update `QuoteBookData`:

```ts
export interface QuoteBookData {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  isLimitUp: boolean;   // bid 或 ask 任一鎖漲停
  isLimitDown: boolean; // bid 或 ask 任一鎖跌停
  error: string | null;
}
```

Add state:

```ts
const [isLimitUp, setIsLimitUp] = useState(false);
const [isLimitDown, setIsLimitDown] = useState(false);
```

Inside `fetchOnce` after `setBids` / `setAsks`:

```ts
setIsLimitUp(Boolean(r.is_limit_up_bid || r.is_limit_up_ask));
setIsLimitDown(Boolean(r.is_limit_down_bid || r.is_limit_down_ask));
```

On reset (symbol change) and on null symbol:

```ts
setIsLimitUp(false);
setIsLimitDown(false);
```

Return them:

```ts
return { bids, asks, isLimitUp, isLimitDown, error };
```

- [ ] **Step 3: Show 市價 + badge in QuoteBook**

In `frontend/src/components/QuoteBook.tsx`:

Update the hook destructure (around line 14):

```tsx
const { bids, asks, isLimitUp, isLimitDown, error } = useQuoteBook(symbol);
```

Update the header(around line 30)to include badges:

```tsx
<h3 className="font-serif font-bold text-lg tracking-[-0.3px] pb-2.5 mb-3 border-b border-line flex items-center gap-3">
  <span>委買賣 五檔</span>
  {isLimitUp && (
    <span className="px-2 py-0.5 text-2xs uppercase tracking-[1.5px] text-bull border border-bull/40">
      鎖漲停
    </span>
  )}
  {isLimitDown && (
    <span className="px-2 py-0.5 text-2xs uppercase tracking-[1.5px] text-bear border border-bear/40">
      鎖跌停
    </span>
  )}
  {error && <span className="ml-auto text-2xs uppercase tracking-[1px] text-bear">· 更新失敗</span>}
</h3>
```

Update bid row(around line 51-59):

```tsx
bids.map((b, i) => (
  <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
    <span
      className="absolute top-0 bottom-0 right-0 bg-bull/10 pointer-events-none"
      style={{ width: `${(b.size / maxQty) * 100}%` }}
    />
    <span className="relative z-[1] text-ink-muted">{b.size > 0 ? `${b.size} 張` : "—"}</span>
    <span className="relative z-[1] text-right text-bull font-medium">
      {b.price === 0 ? "市價" : b.price.toFixed(2)}
    </span>
  </div>
))
```

Update ask row(around line 67-75)同樣:

```tsx
asks.map((a, i) => (
  <div key={i} className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums">
    <span
      className="absolute top-0 bottom-0 left-0 bg-bear/10 pointer-events-none"
      style={{ width: `${(a.size / maxQty) * 100}%` }}
    />
    <span className="relative z-[1] text-bear font-medium">
      {a.price === 0 ? "市價" : a.price.toFixed(2)}
    </span>
    <span className="relative z-[1] text-right text-ink-muted">{a.size > 0 ? `${a.size} 張` : "—"}</span>
  </div>
))
```

- [ ] **Step 4: Type check**

```bash
cd frontend && npx tsc -b --noEmit
```
Expected: no errors.

- [ ] **Step 5: Manual verify**

Best:在收盤前找一檔當天鎖漲停 / 鎖跌停的(看 [https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html](https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html))點開五檔,確認:
- 對手側顯示「市價」紅或綠
- header 出現「鎖漲停」/「鎖跌停」badge

如果沒鎖漲跌停的股,跳到 Step 6 commit。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/hooks/useQuoteBook.ts frontend/src/components/QuoteBook.tsx
git commit -m "feat(quotebook): show 市價 + lock badge on limit-up/down stocks"
```

---

## PR #3 — MA signal conditions (Item 4a + 4b)

## Task 6: Item 4a — Extract `ma_service` from `routes/ma.py`

**Files:**
- Create: `backend/services/ma_service.py`
- Modify: `backend/routes/ma.py`(refactor 用 service)
- Create: `backend/tests/test_ma_service.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_ma_service.py`:

```python
"""驗 ma_service.fetch_sma_5_20 — 拉 SMA5/20、失敗欄位回 None。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import ma_service


@pytest.mark.asyncio
async def test_fetch_sma_5_20_returns_both_floats(monkeypatch):
    fake_sdk = MagicMock()
    fake_sdk.marketdata.rest_client.stock.technical.sma = MagicMock(
        side_effect=[
            {"data": [{"sma": 100.5, "date": "2026-05-19"}]},
            {"data": [{"sma": 105.2, "date": "2026-05-19"}]},
        ]
    )
    fubon = MagicMock()
    fubon.status.value = "ok"
    fubon.sdk = fake_sdk
    monkeypatch.setattr(ma_service, "get_fubon", lambda: fubon)

    sma_5, sma_20 = await ma_service.fetch_sma_5_20("2330")
    assert sma_5 == 100.5
    assert sma_20 == 105.2


@pytest.mark.asyncio
async def test_fetch_sma_5_20_handles_partial_failure(monkeypatch):
    """SMA5 OK、SMA20 失敗時 SMA20 回 None,不 raise。"""
    fake_sdk = MagicMock()
    fake_sdk.marketdata.rest_client.stock.technical.sma = MagicMock(
        side_effect=[
            {"data": [{"sma": 100.5, "date": "2026-05-19"}]},
            Exception("network error"),
        ]
    )
    fubon = MagicMock()
    fubon.status.value = "ok"
    fubon.sdk = fake_sdk
    monkeypatch.setattr(ma_service, "get_fubon", lambda: fubon)

    sma_5, sma_20 = await ma_service.fetch_sma_5_20("2330")
    assert sma_5 == 100.5
    assert sma_20 is None
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_ma_service.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Create ma_service.py**

```python
# backend/services/ma_service.py
"""SMA fetch service — 共用給 routes/ma.py 跟 signal_engine。

對應富邦 tech.sma,當日不變(用上一交易日 close 算)。失敗欄位回 None。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.fubon_client import FubonStatus, get_fubon
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


def _extract_latest(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None
    last = data[-1]
    if not isinstance(last, dict):
        return None
    v = last.get("sma")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_sma(symbol: str, period: int) -> float | None:
    """單一 period 的 SMA fetch — 失敗回 None。"""
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        return None
    try:
        await asyncio.to_thread(get_rate_limiter().acquire)
        res = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.stock.technical.sma,
            symbol=symbol, period=period,
        )
        return _extract_latest(res)
    except Exception as e:
        logger.warning("ma fetch failed: %s period=%d — %s: %s",
                       symbol, period, type(e).__name__, e)
        return None


async def fetch_sma_5_20(symbol: str) -> tuple[float | None, float | None]:
    """並行打 SMA5 + SMA20,回 (sma_5, sma_20)。"""
    sma_5, sma_20 = await asyncio.gather(
        fetch_sma(symbol, 5),
        fetch_sma(symbol, 20),
    )
    return sma_5, sma_20
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_ma_service.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Refactor routes/ma.py to use service**

Replace `backend/routes/ma.py` body(保留 router):

```python
"""GET /api/ma/{symbol} — 即時打富邦 tech.sma,回最新一根日 K 的 SMA5 / SMA20。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import ma_service
from services.fubon_client import FubonStatus, get_fubon

router = APIRouter()


@router.get("/api/ma/{symbol}")
async def get_ma(symbol: str) -> dict:
    fubon = get_fubon()
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise HTTPException(
            503,
            detail={"error": "fubon_unavailable", "last_error": fubon.last_error},
        )
    sma_5, sma_20 = await ma_service.fetch_sma_5_20(symbol)
    return {
        "symbol": symbol,
        "sma_5": sma_5,
        "sma_20": sma_20,
    }
```

注意:刪掉了原本的 `as_of_date` 欄位。檢查前端 `useIntradayCandles.ts` 或 `api.ma()` 有沒有用:

```bash
grep -rn "as_of_date" frontend/src/
```

如果有用就保留:在 `fetch_sma` 內也回 date,`fetch_sma_5_20` 多回 `as_of_date`。

- [ ] **Step 6: Run all backend tests**

```bash
cd backend && .venv/Scripts/pytest tests/ -v
```
Expected: 所有 test pass(含 ma_service 跟前面 quote 的)

- [ ] **Step 7: Commit**

```bash
git add backend/services/ma_service.py backend/routes/ma.py backend/tests/test_ma_service.py
git commit -m "refactor(ma): extract ma_service for cross-module reuse"
```

---

## Task 7: Item 4a — Add `sma_5` / `sma_20` to ConditionField

**Files:**
- Modify: `backend/models/condition.py:17-32`
- Modify: `frontend/src/lib/api.ts:46-50`
- Create: `backend/tests/test_condition_model.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_condition_model.py`:

```python
"""驗 ConditionField 含 sma_5 / sma_20,Condition 接受這些 field。"""
import pytest
from pydantic import ValidationError

from models.condition import ALL_FIELDS, Condition


def test_sma_fields_in_all_fields():
    assert "sma_5"  in ALL_FIELDS
    assert "sma_20" in ALL_FIELDS


def test_condition_accepts_sma_5_field():
    c = Condition(field="sma_5", operator="gte", value=100.0)
    assert c.field == "sma_5"


def test_condition_value_can_reference_sma_20():
    """value=sma_20 表示「跟 sma_20 比較」(cross-field)。"""
    c = Condition(field="close", operator="gte", value="sma_20")
    assert c.value == "sma_20"


def test_condition_rejects_unknown_field():
    with pytest.raises(ValidationError):
        Condition(field="sma_60", operator="gte", value=100.0)  # type: ignore
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_condition_model.py -v
```
Expected: FAIL

- [ ] **Step 3: Update ConditionField + ALL_FIELDS**

In `backend/models/condition.py`:

```python
ConditionField = Literal[
    "close",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
    "sma_5", "sma_20",
]

ALL_FIELDS: tuple[ConditionField, ...] = (
    "close",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
    "sma_5", "sma_20",
)
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_condition_model.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Mirror to frontend api.ts**

In `frontend/src/lib/api.ts` lines 46-50:

```ts
export const ALL_FIELDS = [
  "close",
  "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
  "sma_5", "sma_20",
] as const;
```

- [ ] **Step 6: Commit**

```bash
git add backend/models/condition.py backend/tests/test_condition_model.py frontend/src/lib/api.ts
git commit -m "feat(signals): add sma_5/sma_20 to ConditionField"
```

---

## Task 8: Item 4a — signal_engine MA refill into field_cache

**Files:**
- Modify: `backend/services/signal_engine.py:108-156`
- Create: `backend/tests/test_signal_engine_ma_refill.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_signal_engine_ma_refill.py`:

```python
"""驗 _refill_field_cache 會把 MA 寫進 field_cache。"""
from unittest.mock import AsyncMock, patch

import pytest

from models.condition import ActiveFilter, Condition, WatchlistScope
from services.signal_engine import SignalEngine


@pytest.mark.asyncio
async def test_refill_writes_sma_into_field_cache():
    engine = SignalEngine()
    # 預設 active:scope=symbols=["2330"],condition: close >= sma_5
    from models.condition import ActiveSignalOut
    engine._active = [
        ActiveSignalOut(
            id="x", name="t",
            filter_json=ActiveFilter(
                conditions=[Condition(field="close", operator="gte", value="sma_5")],
            ),
            scope={"type": "symbols", "symbols": ["2330"]},
            cooldown_seconds=60, enabled=True, created_at="2026-05-19",
        )
    ]

    # CDP service 回空、ma_service 回 (100.5, 105.2)
    with patch("services.signal_engine.get_cdp_service") as mock_cdp, \
         patch("services.signal_engine.ma_service") as mock_ma, \
         patch("services.signal_engine.get_supabase") as mock_sb:
        mock_cdp.return_value.get = AsyncMock(return_value=None)
        mock_ma.fetch_sma_5_20 = AsyncMock(return_value=(100.5, 105.2))
        mock_sb.return_value.client = None  # 走「沒 supabase」路徑也能寫 cache

        await engine._refill_field_cache()

    assert engine._field_cache["2330"]["sma_5"]  == 100.5
    assert engine._field_cache["2330"]["sma_20"] == 105.2
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_ma_refill.py -v
```
Expected: FAIL

- [ ] **Step 3: Patch `_refill_field_cache`**

In `backend/services/signal_engine.py`, find `_refill_field_cache`(starts around line 108). Currently:

```python
async def _refill_field_cache(self) -> None:
    sb = get_supabase()
    if sb.client is None:
        return
    # ... gather symbols ...
    cdp = get_cdp_service()
    for sym in symbols_needed:
        levels = await cdp.get(sym)
        if levels:
            d = self._field_cache.setdefault(sym, {})
            d["cdp_ah"] = levels["ah"]
            d["cdp_nh"] = levels["nh"]
            d["cdp"]    = levels["cdp"]
            d["cdp_nl"] = levels["nl"]
            d["cdp_al"] = levels["al"]

    self._last_field_refill_date = date.today()
```

Change two things:

1. At top of file, add import:

```python
from services import ma_service
```

2. Remove the early `return` when `sb.client is None`(test 要走 sb=None 路徑;原本是為了避免 watchlist query 失敗,改成只 skip watchlist refresh):

```python
async def _refill_field_cache(self) -> None:
    sb = get_supabase()

    symbols_needed: set[str] = set()
    watchlist_fetched = False
    for a in self._active:
        scope = a.scope
        if isinstance(scope, dict):
            scope_type = scope.get("type")
            scope_symbols = scope.get("symbols", [])
        else:
            scope_type = getattr(scope, "type", None)
            scope_symbols = getattr(scope, "symbols", [])
        if scope_type == "symbols":
            symbols_needed.update(scope_symbols)
        elif scope_type == "watchlist" and not watchlist_fetched and sb.client is not None:
            res = await asyncio.to_thread(
                lambda: sb.client.table("watchlist")
                .select("symbol")
                .eq("user_label", get_user_label())
                .execute()
            )
            for row in (res.data or []):
                symbols_needed.add(row["symbol"])
            watchlist_fetched = True

    # CDP refill
    cdp = get_cdp_service()
    for sym in symbols_needed:
        levels = await cdp.get(sym)
        if levels:
            d = self._field_cache.setdefault(sym, {})
            d["cdp_ah"] = levels["ah"]
            d["cdp_nh"] = levels["nh"]
            d["cdp"]    = levels["cdp"]
            d["cdp_nl"] = levels["nl"]
            d["cdp_al"] = levels["al"]

    # MA refill (NEW)
    for sym in symbols_needed:
        sma_5, sma_20 = await ma_service.fetch_sma_5_20(sym)
        if sma_5 is not None or sma_20 is not None:
            d = self._field_cache.setdefault(sym, {})
            if sma_5  is not None: d["sma_5"]  = sma_5
            if sma_20 is not None: d["sma_20"] = sma_20

    self._last_field_refill_date = date.today()
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_ma_refill.py -v
```
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_ma_refill.py
git commit -m "feat(signals): refill sma_5/sma_20 into field_cache"
```

---

## Task 9: Item 4a — Frontend FIELD_LABEL adds MA

**Files:**
- Modify: `frontend/src/components/ActiveSignalEditor.tsx:8-12`

- [ ] **Step 1: Update FIELD_LABEL**

In `frontend/src/components/ActiveSignalEditor.tsx` around line 8-12:

```tsx
const FIELD_LABEL: Record<ConditionField, string> = {
  close: "即時價",
  cdp_ah: "CDP AH (最高值)", cdp_nh: "CDP NH (近高)", cdp: "CDP 中軸",
  cdp_nl: "CDP NL (近低)", cdp_al: "CDP AL (最低值)",
  sma_5: "MA5 (5 日均線)", sma_20: "MA20 (20 日均線)",
};
```

- [ ] **Step 2: Type check + manual verify**

```bash
cd frontend && npx tsc -b --noEmit
```

Run dev, open「新增訊號規則」 dialog,展開「跨指標條件」 field dropdown — 確認 MA5 / MA20 有出現。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(signals): expose MA5/MA20 in condition field selector"
```

---

## Task 10: Item 4b — `MAProximityCondition` model

**Files:**
- Modify: `backend/models/condition.py` (add new class + bump schema)
- Modify: `frontend/src/lib/api.ts` (mirror types)
- Modify: `backend/tests/test_condition_model.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_condition_model.py`:

```python
def test_ma_proximity_default_levels_both():
    from models.condition import MAProximityCondition
    p = MAProximityCondition()
    assert p.levels == ["sma_5", "sma_20"]
    assert p.tolerance_ticks == 0


def test_ma_proximity_rejects_invalid_level():
    from pydantic import ValidationError
    from models.condition import MAProximityCondition
    with pytest.raises(ValidationError):
        MAProximityCondition(levels=["sma_60"])  # type: ignore


def test_active_filter_schema_bumps_to_3():
    from models.condition import ActiveFilter, Condition
    f = ActiveFilter(conditions=[Condition(field="close", operator="gt", value=100)])
    assert f.schema_version == 3
    assert f.ma_proximity is None


def test_active_filter_loads_old_schema_2_data():
    """schema_version=2 的舊 filter_json 要能正常 load,ma_proximity 自動補 None。"""
    from models.condition import ActiveFilter
    old = {
        "schema_version": 2,
        "conditions": [{"field": "close", "operator": "gt", "value": 100}],
        "logic": "AND",
        "window_conditions": [],
        "cdp_proximity": None,
    }
    f = ActiveFilter(**old)
    assert f.ma_proximity is None
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_condition_model.py -v
```
Expected: 4 new tests fail (MAProximityCondition not found)

- [ ] **Step 3: Add `MAProximityCondition` + bump `ActiveFilter`**

In `backend/models/condition.py`, after `CdpProximityCondition`:

```python
class MAProximityCondition(BaseModel):
    """MA 觸發條件 — tick price 落在所選 MA 線的 ±N tick 範圍內。

    SMA raw 通常不在合法 tick 上,tolerance=0 嚴格打到實務上很難命中;
    UI 應預設 tolerance=1 以上。
    """

    levels: list[Literal["sma_5", "sma_20"]] = Field(
        default_factory=lambda: ["sma_5", "sma_20"],
        min_length=1,
    )
    tolerance_ticks: int = Field(default=0, ge=0, le=10)
```

Update `ActiveFilter`:

```python
class ActiveFilter(Filter):
    schema_version: int = 3  # 2→3,加 ma_proximity
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None
    ma_proximity:  MAProximityCondition  | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):
        if (not self.conditions
                and not self.window_conditions
                and self.cdp_proximity is None
                and self.ma_proximity is None):
            raise ValueError("至少要有一個 condition / window_condition / cdp_proximity / ma_proximity")
        return self
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_condition_model.py -v
```
Expected: all pass

- [ ] **Step 5: Mirror types in frontend**

In `frontend/src/lib/api.ts`,find `CdpProximity` (around line 97-100), add after it:

```ts
export interface MAProximity {
  levels: Array<"sma_5" | "sma_20">;
  tolerance_ticks: number;
}
```

Find `ActiveFilter` type definition,add field:

```ts
export interface ActiveFilter {
  schema_version?: number;
  conditions: Condition[];
  window_conditions?: WindowCondition[];
  cdp_proximity?: CdpProximity | null;
  ma_proximity?: MAProximity | null;  // NEW
  logic: "AND" | "OR";
}
```

(注意:現在 ActiveFilter type 可能不存在 frontend api.ts,要 grep 確認 — 如果沒有就用既有的 active_signals filter_json shape 修改)。

```bash
grep -n "ActiveFilter" frontend/src/lib/api.ts
```

- [ ] **Step 6: Commit**

```bash
git add backend/models/condition.py backend/tests/test_condition_model.py frontend/src/lib/api.ts
git commit -m "feat(signals): add MAProximityCondition model, schema 2→3"
```

---

## Task 11: Item 4b — signal_engine `_eval_ma_proximity` + `_eval_cdp_proximity` returns tuple

**Files:**
- Modify: `backend/services/signal_engine.py`
- Create: `backend/tests/test_signal_engine_proximity.py`

(此 task 同時把 `_eval_cdp_proximity` 改成回 `tuple[bool, str|None]`,Task 13 才用得到 level 資訊)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_signal_engine_proximity.py`:

```python
"""驗 _eval_cdp_proximity / _eval_ma_proximity 回 (bool, level)。"""
import pytest

from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _tick(price: float, t: float = 1700000000.0) -> Tick:
    return Tick(price=price, size=1, time=t)


def test_cdp_proximity_returns_level_when_hit():
    engine = SignalEngine()
    engine._field_cache["2330"] = {
        "cdp_ah": 100.0, "cdp_nh": 99.0, "cdp": 98.0,
        "cdp_nl": 97.0, "cdp_al": 96.0,
    }
    prox = {"levels": ["ah", "nh"], "tolerance_ticks": 0}
    ok, level = engine._eval_cdp_proximity("2330", _tick(100.0), prox)
    assert ok is True
    assert level == "ah"


def test_cdp_proximity_no_hit_returns_none_level():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    prox = {"levels": ["ah"], "tolerance_ticks": 0}
    ok, level = engine._eval_cdp_proximity("2330", _tick(95.0), prox)
    assert ok is False
    assert level is None


def test_ma_proximity_returns_level_when_hit_within_tolerance():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"sma_5": 100.47, "sma_20": 105.20}
    prox = {"levels": ["sma_5", "sma_20"], "tolerance_ticks": 1}
    # 100.5 within 1 tick of 100.47 (tick_size at 100 = 0.05)
    ok, level = engine._eval_ma_proximity("2330", _tick(100.5), prox)
    assert ok is True
    assert level == "sma_5"


def test_ma_proximity_no_hit_returns_false():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"sma_5": 100.0}
    prox = {"levels": ["sma_5"], "tolerance_ticks": 0}
    ok, level = engine._eval_ma_proximity("2330", _tick(102.0), prox)
    assert ok is False
    assert level is None


def test_ma_proximity_missing_cache_returns_false():
    engine = SignalEngine()
    engine._field_cache["2330"] = {}  # no sma
    prox = {"levels": ["sma_5"], "tolerance_ticks": 5}
    ok, level = engine._eval_ma_proximity("2330", _tick(100.0), prox)
    assert ok is False
    assert level is None
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_proximity.py -v
```
Expected: 1 test fails on `_eval_cdp_proximity` returning bool not tuple,4 fail on `_eval_ma_proximity` missing.

- [ ] **Step 3: Change `_eval_cdp_proximity` to return tuple**

In `backend/services/signal_engine.py`, find `_eval_cdp_proximity` (around line 267). Update signature + body:

```python
def _eval_cdp_proximity(self, symbol: str, tick: Tick, prox) -> tuple[bool, str | None]:
    from services.cdp import tick_size

    cache = self._field_cache.get(symbol, {})
    levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
    tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                 else prox.tolerance_ticks)

    field_map = {
        "ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
        "nl": "cdp_nl", "al": "cdp_al",
    }
    for level in levels:
        v = cache.get(field_map[level])
        if v is None:
            continue
        tol = tol_ticks * tick_size(v)
        if abs(tick.price - v) <= tol:
            return True, level
    return False, None
```

Update `_eval_conditions` to handle tuple return:

```python
def _eval_conditions(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
    f = active.filter_json
    results: list[bool] = []
    for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
        results.append(self._eval_window(symbol, tick, wc))
    for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
        results.append(self._eval_filter_cond(symbol, tick, c))
    cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                else getattr(f, "cdp_proximity", None))
    if cdp_prox is not None:
        ok, _ = self._eval_cdp_proximity(symbol, tick, cdp_prox)
        results.append(ok)
    ma_prox = (f.get("ma_proximity") if isinstance(f, dict)
               else getattr(f, "ma_proximity", None))
    if ma_prox is not None:
        ok, _ = self._eval_ma_proximity(symbol, tick, ma_prox)
        results.append(ok)
    if not results:
        return False
    logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
    return all(results) if logic == "AND" else any(results)
```

- [ ] **Step 4: Add `_eval_ma_proximity`**

After `_eval_cdp_proximity` in `signal_engine.py`:

```python
def _eval_ma_proximity(self, symbol: str, tick: Tick, prox) -> tuple[bool, str | None]:
    """tick.price 落在所選 MA 線的 ±N tick → (True, 哪條觸發)。

    cache 內 sma 是 raw 算術平均,常落在非合法 tick;tolerance=0 實務上很難命中。
    """
    from services.cdp import tick_size

    cache = self._field_cache.get(symbol, {})
    levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
    tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                 else prox.tolerance_ticks)

    for level in levels:  # "sma_5" or "sma_20"
        v = cache.get(level)
        if v is None:
            continue
        tol = tol_ticks * tick_size(v)
        if abs(tick.price - v) <= tol:
            return True, level
    return False, None
```

- [ ] **Step 5: Run tests**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_proximity.py -v
```
Expected: `5 passed`

- [ ] **Step 6: Run all backend tests**

```bash
cd backend && .venv/Scripts/pytest tests/ -v
```
Expected: 全部 pass(回歸驗既有 cdp_proximity 沒被改壞)

- [ ] **Step 7: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_proximity.py
git commit -m "feat(signals): MA proximity eval + cdp_proximity returns (bool, level)"
```

---

## Task 12: Item 4b — Frontend MA proximity editor UI

**Files:**
- Modify: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: Add MA proximity helpers**

In `frontend/src/components/ActiveSignalEditor.tsx`,after `updateCdpTolerance`(around line 111):

```tsx
const ALL_MA_LEVELS = ["sma_5", "sma_20"] as const;

const MA_LEVEL_LABEL: Record<typeof ALL_MA_LEVELS[number], string> = {
  sma_5: "MA5", sma_20: "MA20",
};

function enableMaProx() {
  setFilter({
    ...filter,
    ma_proximity: { levels: [...ALL_MA_LEVELS], tolerance_ticks: 1 },
  });
}
function disableMaProx() {
  setFilter({ ...filter, ma_proximity: null });
}
function toggleMaLevel(level: typeof ALL_MA_LEVELS[number]) {
  const prox = filter.ma_proximity;
  if (!prox) return;
  const checked = prox.levels.includes(level);
  let next: MAProximity["levels"];
  if (checked) {
    if (prox.levels.length <= 1) return;
    next = prox.levels.filter((l) => l !== level);
  } else {
    next = [...prox.levels, level];
  }
  setFilter({ ...filter, ma_proximity: { ...prox, levels: next } });
}
function updateMaTolerance(tol: number) {
  const prox = filter.ma_proximity;
  if (!prox) return;
  const clamped = Math.max(0, Math.min(10, Math.round(tol)));
  setFilter({ ...filter, ma_proximity: { ...prox, tolerance_ticks: clamped } });
}
```

Import `MAProximity` at top:

```tsx
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal, type CdpProximity,
  type Condition, type ConditionField, type ConditionOperator,
  type MAProximity,  // NEW
  type Scope, type WindowCondition, type WindowConditionType, type WindowSeconds,
} from "../lib/api";
```

- [ ] **Step 2: Add MA proximity UI block**

Find the「CDP 觸發區塊」(around line 215-255),add this immediately after that block:

```tsx
{/* MA 觸發區塊 */}
<div className="border-t border-line pt-3 mb-4">
  <div className="label-tiny mb-2">MA 觸發</div>
  <p className="text-2xs text-ink-dim mb-3 leading-relaxed">
    價格打到(或接近)所選 MA 線即觸發。SMA 不在合法 tick 上,Tolerance 建議 ≥ 1 tick。
  </p>
  {filter.ma_proximity === null || filter.ma_proximity === undefined ? (
    <button type="button" onClick={enableMaProx}
      className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">
      + 啟用 MA 觸發
    </button>
  ) : (
    <div className="border border-line p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {ALL_MA_LEVELS.map((lv) => {
          const checked = filter.ma_proximity!.levels.includes(lv);
          const isLastChecked = checked && filter.ma_proximity!.levels.length === 1;
          return (
            <label key={lv} className={`text-sm flex items-center gap-1 ${isLastChecked ? "opacity-60" : "cursor-pointer"}`}>
              <input type="checkbox" checked={checked}
                disabled={isLastChecked}
                onChange={() => toggleMaLevel(lv)}
                className="accent-accent" />
              {MA_LEVEL_LABEL[lv]}
            </label>
          );
        })}
        <button type="button" onClick={disableMaProx}
          className="ml-auto text-ink-dim hover:text-bear text-xs">移除</button>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm text-ink-muted">Tolerance:</span>
        <input type="number" min={0} max={10} step={1}
          value={filter.ma_proximity.tolerance_ticks}
          onChange={(e) => updateMaTolerance(Number(e.target.value))}
          className="bg-bg-deep border border-line text-sm px-2 py-1 w-20 tabular-nums" />
        <span className="text-xs text-ink-dim">tick (SMA raw 不在合法 tick,建議 ≥ 1)</span>
      </div>
    </div>
  )}
</div>
```

- [ ] **Step 3: Update save() validation**

In `save()`(around line 113-119),include ma_proximity in the「至少一條」check:

```tsx
if (filter.conditions.length === 0
    && (filter.window_conditions ?? []).length === 0
    && !filter.cdp_proximity
    && !filter.ma_proximity) {
  setError("至少要有一條條件"); return;
}
```

- [ ] **Step 4: Type check + manual verify**

```bash
cd frontend && npx tsc -b --noEmit
```

Run dev,新增訊號規則,確認:
- 「MA 觸發」區塊出現在「CDP 觸發」下面
- 點「+ 啟用 MA 觸發」展開
- 預設兩個都勾、tolerance=1
- 可以正常存(送 POST 看不噴 schema 錯)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(signals): MA proximity editor UI"
```

---

## PR #4 — CDP/MA direction + touch count (Q1 + Q2)

## Task 13: Q1 — `_direction_of_touch` helper

**Files:**
- Modify: `backend/services/signal_engine.py`
- Create: `backend/tests/test_direction_of_touch.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_direction_of_touch.py
"""驗 _direction_of_touch — 比較 prev / curr 跟 threshold 判方向。"""
import pytest

from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _tick(p: float) -> Tick:
    return Tick(price=p, size=1, time=0.0)


def test_from_below_when_prev_lower_curr_higher():
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99), _tick(101), 100) == "from_below"


def test_from_above_when_prev_higher_curr_lower():
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(101), _tick(99), 100) == "from_above"


def test_from_below_when_curr_equals_threshold():
    """剛好打到也算 from_below(prev < threshold <= curr)。"""
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99), _tick(100), 100) == "from_below"


def test_horizontal_when_prev_none():
    eng = SignalEngine()
    assert eng._direction_of_touch(None, _tick(100), 100) == "horizontal"


def test_horizontal_when_both_sides_of_same_side():
    """prev 跟 curr 都在 threshold 同側,沒跨越。"""
    eng = SignalEngine()
    assert eng._direction_of_touch(_tick(99.5), _tick(99.8), 100) == "horizontal"
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_direction_of_touch.py -v
```
Expected: FAIL

- [ ] **Step 3: Add helper method**

In `backend/services/signal_engine.py`,add inside `SignalEngine` class(somewhere near other helpers):

```python
@staticmethod
def _direction_of_touch(prev: Tick | None, curr: Tick, threshold: float) -> str:
    """判斷 curr.price 相對 threshold 從哪個方向跨越過來。

    回傳 "from_below" / "from_above" / "horizontal"。
    """
    if prev is None:
        return "horizontal"
    if prev.price < threshold and curr.price >= threshold:
        return "from_below"
    if prev.price > threshold and curr.price <= threshold:
        return "from_above"
    return "horizontal"
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_direction_of_touch.py -v
```
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_direction_of_touch.py
git commit -m "feat(signals): _direction_of_touch helper"
```

---

## Task 14: Q1 + Q2 — `_evaluate` wires direction + touch count + fanout payload

**Files:**
- Modify: `backend/services/signal_engine.py`
- Create: `backend/tests/test_signal_engine_touch_metadata.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_signal_engine_touch_metadata.py
"""驗 fanout payload 帶 cdp_touch / ma_touch 含 direction、role、touch_index。"""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from models.condition import ActiveSignalOut, ActiveFilter, CdpProximityCondition
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


def _make_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="x", name="t",
        filter_json=ActiveFilter(
            cdp_proximity=CdpProximityCondition(levels=["ah"], tolerance_ticks=0),
        ),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60,
        enabled=True,
        created_at="2026-05-19",
    )


@pytest.mark.asyncio
async def test_fanout_payload_includes_cdp_touch_from_below():
    engine = SignalEngine()
    engine._active = [_make_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    captured = {}
    async def fake_broadcast(payload):
        captured.update(payload)

    with patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value.append = lambda _: None

        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["event"] == "signal"
    assert captured["data"]["cdp_touch"] == {
        "level": "ah", "direction": "from_below",
        "role": "resistance", "touch_index": 1,
    }


@pytest.mark.asyncio
async def test_touch_index_increments_on_repeat_trigger():
    engine = SignalEngine()
    active = _make_active()
    active.cooldown_seconds = 0  # 不檔重複觸發
    engine._active = [active]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}

    captured_indices = []
    async def fake_broadcast(payload):
        captured_indices.append(payload["data"]["cdp_touch"]["touch_index"])

    with patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_supabase_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value.append = lambda _: None

        for i in range(3):
            engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=float(i))
            await engine._evaluate("2330", Tick(price=100.0, size=1, time=float(i+1)))

    assert captured_indices == [1, 2, 3]
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_touch_metadata.py -v
```
Expected: FAIL

- [ ] **Step 3: Add state + wire `_evaluate`**

In `SignalEngine.__init__`,add:

```python
self._prev_tick: dict[str, Tick] = {}
self._cdp_touch_count: dict[tuple[str, str, date], int] = {}
self._ma_touch_count:  dict[tuple[str, str, date], int] = {}
```

Import `date` if not already:

```python
from datetime import date, datetime, timezone
```

Replace `_evaluate`:

```python
async def _evaluate(self, symbol: str, tick: Tick) -> None:
    """對每個涉及這 symbol 的 active_signal 跑條件,觸發時 fanout 帶 touch metadata。"""
    for active in self._active:
        if not self._scope_includes(active, symbol):
            continue

        cdp_touch, ma_touch = self._eval_with_touch_meta(active, symbol, tick)
        if cdp_touch is None and ma_touch is None and not self._eval_non_proximity(active, symbol, tick):
            # 沒有任何子條件成立
            continue

        # cooldown 檢查
        key = (active.id, symbol)
        now = time.time()
        last_ts = self._cooldown.get(key, 0)
        if now - last_ts < active.cooldown_seconds:
            continue
        self._cooldown[key] = now

        # touch_count(僅 proximity 觸發才計次)
        today = date.today()
        if cdp_touch is not None:
            count_key = (symbol, cdp_touch["level"], today)
            self._cdp_touch_count[count_key] = self._cdp_touch_count.get(count_key, 0) + 1
            cdp_touch["touch_index"] = self._cdp_touch_count[count_key]
        if ma_touch is not None:
            count_key = (symbol, ma_touch["level"], today)
            self._ma_touch_count[count_key] = self._ma_touch_count.get(count_key, 0) + 1
            ma_touch["touch_index"] = self._ma_touch_count[count_key]

        await self._fanout(active, symbol, tick, cdp_touch, ma_touch)

    # update prev_tick(放最後,給下一輪 evaluate 算 direction 用)
    self._prev_tick[symbol] = tick
```

Add helper `_eval_with_touch_meta`:

```python
def _eval_with_touch_meta(
    self, active: ActiveSignalOut, symbol: str, tick: Tick,
) -> tuple[dict | None, dict | None]:
    """跑 cdp/ma proximity,回 (cdp_touch_dict, ma_touch_dict)。

    None 表示該 proximity 沒設或沒命中。
    """
    f = active.filter_json
    prev = self._prev_tick.get(symbol)

    cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                else getattr(f, "cdp_proximity", None))
    cdp_touch: dict | None = None
    if cdp_prox is not None:
        ok, level = self._eval_cdp_proximity(symbol, tick, cdp_prox)
        if ok:
            field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
                         "nl": "cdp_nl", "al": "cdp_al"}
            v = self._field_cache.get(symbol, {}).get(field_map[level])
            direction = self._direction_of_touch(prev, tick, v) if v is not None else "horizontal"
            role = {"from_below": "resistance", "from_above": "support"}.get(direction, "touch")
            cdp_touch = {"level": level, "direction": direction, "role": role}

    ma_prox = (f.get("ma_proximity") if isinstance(f, dict)
               else getattr(f, "ma_proximity", None))
    ma_touch: dict | None = None
    if ma_prox is not None:
        ok, level = self._eval_ma_proximity(symbol, tick, ma_prox)
        if ok:
            v = self._field_cache.get(symbol, {}).get(level)
            direction = self._direction_of_touch(prev, tick, v) if v is not None else "horizontal"
            role = {"from_below": "resistance", "from_above": "support"}.get(direction, "touch")
            ma_touch = {"level": level, "direction": direction, "role": role}

    return cdp_touch, ma_touch
```

Add helper `_eval_non_proximity` — 重用 `_eval_conditions` 但只跑 window_conditions + conditions:

```python
def _eval_non_proximity(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
    """跑非 proximity 的條件(window + cross-field)。回 True 表示有設且有任一成立。"""
    f = active.filter_json
    results: list[bool] = []
    for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
        results.append(self._eval_window(symbol, tick, wc))
    for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
        results.append(self._eval_filter_cond(symbol, tick, c))
    if not results:
        return False
    logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
    return all(results) if logic == "AND" else any(results)
```

Update `_fanout` signature:

```python
async def _fanout(
    self, active: ActiveSignalOut, symbol: str, tick: Tick,
    cdp_touch: dict | None = None, ma_touch: dict | None = None,
) -> None:
    from services.supabase_writer import get_supabase_writer
    data = {
        "active_signal_id": active.id,
        "active_signal_name": active.name,
        "symbol": symbol,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
    }
    if cdp_touch: data["cdp_touch"] = cdp_touch
    if ma_touch:  data["ma_touch"]  = ma_touch
    payload = {"event": "signal", "data": data}
    await get_broadcaster().broadcast(payload)
    get_supabase_writer().append({
        "active_signal_id": active.id,
        "symbol": symbol,
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
        "context_json": {
            "latest_tick_time": tick.time,
            **({"cdp_touch": cdp_touch} if cdp_touch else {}),
            **({"ma_touch":  ma_touch}  if ma_touch  else {}),
        },
        "user_label": get_user_label(),
    })
```

注意:既有的 `_eval_conditions` 跟 `_evaluate` 的舊呼叫不要刪 — `_eval_conditions` 已經被 `_evaluate` 用 `_eval_with_touch_meta` + `_eval_non_proximity` 取代。確認沒有其他地方還 import `_eval_conditions`:

```bash
grep -rn "_eval_conditions" backend/
```

如果只剩 `signal_engine.py` 自己用就 OK,可以保留為 dead code(下個 PR 清),或現在刪。

- [ ] **Step 4: Run tests**

```bash
cd backend && .venv/Scripts/pytest tests/test_signal_engine_touch_metadata.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Run all backend tests**

```bash
cd backend && .venv/Scripts/pytest tests/ -v
```
Expected: all pass(回歸驗 _eval_with_touch_meta 沒打壞既有 cdp_proximity 行為)

- [ ] **Step 6: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_touch_metadata.py
git commit -m "feat(signals): touch direction + per-day count in fanout payload"
```

---

## Task 15: Q2 — Cross-day GC in heartbeat

**Files:**
- Modify: `backend/services/signal_engine.py` (`_heartbeat_loop`)
- Create: `backend/tests/test_touch_count_gc.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_touch_count_gc.py
"""驗 跨日 heartbeat 清掉前一日的 touch_count key。"""
from datetime import date, timedelta

import pytest

from services.signal_engine import SignalEngine


def test_gc_keeps_today_drops_yesterday():
    engine = SignalEngine()
    today = date.today()
    yesterday = today - timedelta(days=1)

    engine._cdp_touch_count = {
        ("2330", "ah", today):     3,
        ("2330", "ah", yesterday): 5,
        ("2454", "cdp", yesterday): 2,
    }
    engine._ma_touch_count = {
        ("2330", "sma_5", today):     1,
        ("2330", "sma_5", yesterday): 7,
    }

    engine._gc_touch_counts()

    assert engine._cdp_touch_count == {("2330", "ah", today): 3}
    assert engine._ma_touch_count  == {("2330", "sma_5", today): 1}
```

- [ ] **Step 2: Run to verify fail**

```bash
cd backend && .venv/Scripts/pytest tests/test_touch_count_gc.py -v
```
Expected: FAIL (`_gc_touch_counts` not found)

- [ ] **Step 3: Implement GC + wire into heartbeat**

In `signal_engine.py`,add method:

```python
def _gc_touch_counts(self) -> None:
    """清掉非當天的 touch_count key — 跨午夜 heartbeat 呼叫。"""
    today = date.today()
    self._cdp_touch_count = {
        k: v for k, v in self._cdp_touch_count.items() if k[2] == today
    }
    self._ma_touch_count = {
        k: v for k, v in self._ma_touch_count.items() if k[2] == today
    }
```

In `_heartbeat_loop`,find the cross-midnight refill block(around line 184-192):

```python
today = date.today()
if self._last_field_refill_date != today:
    try:
        await self._refill_field_cache()
        logger.info("signal_engine: daily field_cache refilled for %s", today)
    except Exception as e:
        logger.warning("signal_engine: daily field_cache refill failed: %s", e)
```

Add GC call after the refill block (before symbols iteration):

```python
today = date.today()
if self._last_field_refill_date != today:
    try:
        await self._refill_field_cache()
        self._gc_touch_counts()  # NEW
        logger.info("signal_engine: daily field_cache refilled for %s", today)
    except Exception as e:
        logger.warning("signal_engine: daily field_cache refill failed: %s", e)
```

- [ ] **Step 4: Run test**

```bash
cd backend && .venv/Scripts/pytest tests/test_touch_count_gc.py -v
```
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_touch_count_gc.py
git commit -m "feat(signals): cross-day GC for touch_count dicts"
```

---

## Task 16: Q1 + Q2 — Frontend TriggerList display

**Files:**
- Modify: `frontend/src/lib/api.ts:210-219`(extend SignalEvent + SignalLogRow context_json shape)
- Create: `frontend/src/lib/signal-format.ts`
- Modify: `frontend/src/components/TriggerList.tsx`

**注意**:`SignalChip.tsx` 不動 — chip 是 per-rule 的彙整顯示,touch_index 是 per-event,放 chip 內不對位。

- [ ] **Step 1: Extend types in api.ts**

In `frontend/src/lib/api.ts` line 210 onwards,找到 `SignalEvent` interface (line 210-219),改成:

```ts
export interface TouchMeta {
  level: string;           // "ah"/"nh"/"cdp"/"nl"/"al" 或 "sma_5"/"sma_20"
  direction: "from_below" | "from_above" | "horizontal";
  role: "resistance" | "support" | "touch";
  touch_index: number;
}

export interface SignalEvent {
  event: "signal";
  data: {
    active_signal_id: string;
    active_signal_name: string;
    symbol: string;
    triggered_at: string;
    trigger_price: number;
    trigger_volume: number;
    cdp_touch?: TouchMeta;
    ma_touch?: TouchMeta;
  };
}
```

`SignalLogRow.context_json` 已經是 `Record<string, unknown> | null`,可承載 `cdp_touch` / `ma_touch`,不用改 type。讀取時 cast 即可。

- [ ] **Step 2: Create formatter helper**

Create `frontend/src/lib/signal-format.ts`:

```ts
import type { TouchMeta } from "./api";

const ROLE_ZH: Record<TouchMeta["role"], string> = {
  resistance: "碰到壓力",
  support: "碰到支撐",
  touch: "平觸",
};

const LEVEL_ZH: Record<string, string> = {
  ah: "CDP AH", nh: "CDP NH", cdp: "CDP 中軸",
  nl: "CDP NL", al: "CDP AL",
  sma_5: "MA5", sma_20: "MA20",
};

export function formatTouch(t: TouchMeta): string {
  const role = ROLE_ZH[t.role];
  const level = LEVEL_ZH[t.level] ?? t.level;
  return `第 ${t.touch_index} 次${role} · ${level}`;
}

/** SignalLogRow.context_json 內取 cdp_touch / ma_touch(可能不存在或 cast 失敗,回 undefined)。 */
export function extractTouch(
  context: Record<string, unknown> | null | undefined,
  key: "cdp_touch" | "ma_touch",
): TouchMeta | undefined {
  if (!context || typeof context !== "object") return undefined;
  const v = context[key];
  if (!v || typeof v !== "object") return undefined;
  const obj = v as Partial<TouchMeta>;
  if (typeof obj.level === "string"
      && typeof obj.direction === "string"
      && typeof obj.role === "string"
      && typeof obj.touch_index === "number") {
    return obj as TouchMeta;
  }
  return undefined;
}
```

- [ ] **Step 3: Extend UnifiedRow + carry touch metadata**

In `frontend/src/components/TriggerList.tsx`,update imports(line 1):

```tsx
import { type ActiveSignal, type SignalLogRow, type SignalEvent, type TouchMeta } from "../lib/api";
import { formatTouch, extractTouch } from "../lib/signal-format";
```

Update `UnifiedRow` interface(line 20-29):

```tsx
interface UnifiedRow {
  key: string;
  time: string;
  symbol: string;
  name: string | null;
  ruleName: string;
  price: number;
  isoTime: string;
  isFresh: boolean;
  cdpTouch?: TouchMeta;
  maTouch?: TouchMeta;
}
```

Update `recentRows` map(line 42-51)— 加 touch 欄位:

```tsx
const recentRows: UnifiedRow[] = recent.map((e) => ({
  key: `recent-${e.active_signal_id}-${e.triggered_at}-${e.symbol}`,
  time: formatTime(e.triggered_at),
  symbol: e.symbol,
  name: symbolNames[e.symbol] ?? null,
  ruleName: e.active_signal_name ?? ruleNameById[e.active_signal_id] ?? "(unknown)",
  price: e.trigger_price,
  isoTime: e.triggered_at,
  isFresh: true,
  cdpTouch: e.cdp_touch,
  maTouch: e.ma_touch,
}));
```

Update `historicalRows` map(line 53-62)— 從 context_json 抽:

```tsx
const historicalRows: UnifiedRow[] = historical.map((h) => ({
  key: `hist-${h.id}`,
  time: formatTime(h.triggered_at),
  symbol: h.symbol,
  name: symbolNames[h.symbol] ?? null,
  ruleName: ruleNameById[h.active_signal_id ?? ""] ?? "(unknown)",
  price: h.trigger_price ?? 0,
  isoTime: h.triggered_at,
  isFresh: false,
  cdpTouch: extractTouch(h.context_json, "cdp_touch"),
  maTouch:  extractTouch(h.context_json, "ma_touch"),
}));
```

- [ ] **Step 4: Render touch line in row**

In `TriggerList.tsx`,find the `<li>` body(line 87-135)。在第二行 `<div className="flex items-baseline justify-between gap-2 mt-1">`(line 107)的整個 `</div>` 之後、`</li>` 之前插入第三行:

```tsx
{(r.cdpTouch || r.maTouch) && (
  <div className="text-2xs text-ink-dim mt-1.5 tabular-nums">
    {r.cdpTouch && (
      <span className="mr-3">{formatTouch(r.cdpTouch)}</span>
    )}
    {r.maTouch && (
      <span>{formatTouch(r.maTouch)}</span>
    )}
  </div>
)}
```

- [ ] **Step 5: Type check**

```bash
cd frontend && npx tsc -b --noEmit
```
Expected: no type errors.

- [ ] **Step 6: Manual verify**

Run dev,設一個帶 cdp_proximity 的訊號 + scope=watchlist,等實際觸發(或臨時改 `cooldown=10` 加速)。確認 TriggerList 第三行出現「第 1 次碰到壓力 · CDP AH」之類。

如果當下沒符合條件的市場狀況,可以 backend manually 觸發測試:

```bash
# 用 supabase SQL 插一筆 signals_log 帶 context_json,reload 頁面查 TriggerList
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/signal-format.ts frontend/src/components/TriggerList.tsx
git commit -m "feat(signals): show touch direction + count in TriggerList rows"
```

---

## Self-Review checklist (run after writing all tasks)

- [x] **Spec coverage** — 每個 spec section 都有對應 task:
  - Item 1 → Task 1
  - Item 2 → Tasks 2 + 3
  - Item 3 → Tasks 4 + 5
  - Item 4a → Tasks 6 + 7 + 8 + 9
  - Item 4b → Tasks 10 + 11 + 12
  - Q1 → Tasks 13 + 14
  - Q2 → Tasks 14 + 15
  - Frontend Q1/Q2 display → Task 16
- [x] **Placeholder scan** — 無「TODO」「TBD」「適當處理」等字眼;所有測試碼跟實作碼都實寫
- [x] **Type consistency** — `LabelInput` / `LabelOutput` / `TouchMeta` / `MAProximityCondition` / `_eval_ma_proximity` 簽名跨 task 一致
- [x] **PR mapping** — Tasks 1-3 = PR #1;4-5 = PR #2;6-12 = PR #3;13-16 = PR #4

---

## PR commit-to-PR mapping

| PR | Tasks | Commits | 預估 LoC |
|---|---|---|---|
| #0 chore: test infra | 0 | 1 | ~50 |
| #1 chart polish | 1-3 | 3 | ~120 |
| #2 quotebook fix | 4-5 | 2 | ~100 |
| #3 MA signals | 6-12 | 7 | ~350 |
| #4 touch metadata | 13-16 | 4 | ~250 |

PR #0(test infra)可以 squash 進 PR #1。PR #3 / #4 改 signal_engine,要排隊不要同時 merge。
