# 群益下單面板 v2 實作計畫(交易種類/TIF/快捷/閃電/庫存平倉)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 `docs/superpowers/specs/2026-06-11-capital-order-panel-v2-design.md`,把下單匣補齊四種交易種類 + ROD/IOC/FOK + 市價 + 價格/張數快捷(Phase 1),新增閃電下單分頁(Phase 2),建部位查詢鏈與庫存分頁+一鍵平倉(Phase 3)。

**Architecture:** 後端 95% 已就緒(`StockOrderRequest` 已有 trade_kind/TIF 欄位),Phase 1 主要是前端露出 + 補無券 enum;Phase 2 純前端(沿用既有送單/刪單 API,武裝開關只省「前端」確認彈窗,後端安全閘全保留);Phase 3 要從 COM 層建 `GetRealBalanceReport` 事件管線(**目前 `set_positions` 無 production 呼叫端,positions API 永遠回空**),再疊 close endpoint 與庫存 UI。

**Tech Stack:** FastAPI + comtypes(SKCOM)/ React 18 + Tailwind + vitest。後端測試 `pytest`(在 `backend/`,venv);前端測試 `npm test`(vitest,在 `frontend/`)。

**Plan 層級決策(spec 未細定、此處鎖定):**
1. **市價單的 price 必帶「閘用估價」**(買=漲停價、賣=跌停價,前端自動填):金額閘(price×qty×1000)對市價單才有意義,不破洞;`bstrPrice` 同此值送出 —— 群益 `nSpecialTradeType=1` 下是否忽略價格屬 spec 開放項 2,首測驗證(若不忽略,行為等同漲跌停限價,仍正確)。
2. **閃電分頁切走 = unmount = 武裝自動消失**(條件渲染,不留 display:none),spec 的「切分頁自動解除」由 React 生命週期保證,不需事件。
3. **Phase 3 平倉 v1 僅現股多頭可達**:`GetRealBalanceReport` 是現股庫存,信用部位資料來源是 spec 開放項 1。反向映射函式四規則全實作(可測),但 client 端在拿不到信用部位前,融資/融券/無券空單路徑 guard 拒絕。

---

## Phase 1 — 下單匣強化

### Task 1: 後端 TradeKind 加無券賣出 + sFlag=3 映射

**Files:**
- Modify: `backend/services/capital_models.py:31-34`(TradeKind enum)
- Modify: `backend/services/capital_mapping.py:14`(_FLAG)
- Test: `backend/tests/test_capital_mapping.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_capital_mapping.py` 檔尾加:

```python
def test_daytrade_sell_maps_to_three():
    f = to_stockorder_fields(
        _req(buy_sell=BuySell.SELL, trade_kind=TradeKind.DAYTRADE_SELL),
        full_account="x",
    )
    assert f["sFlag"] == 3               # 無券=3(官方範例 0/1/2/3=現股/融資/融券/無券)
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`): `python -m pytest tests/test_capital_mapping.py -v`
Expected: FAIL — `AttributeError: DAYTRADE_SELL`

- [ ] **Step 3: 實作**

`backend/services/capital_models.py` 的 `TradeKind` 加一行:

```python
class TradeKind(str, Enum):
    CASH = "cash"      # 現股
    MARGIN = "margin"  # 融資
    SHORT = "short"    # 融券
    DAYTRADE_SELL = "daytrade_sell"  # 無券賣出(現股當沖先賣;回補=現股買進自動沖銷)
```

`backend/services/capital_mapping.py` 的 `_FLAG` 改:

```python
_FLAG = {TradeKind.CASH: 0, TradeKind.MARGIN: 1, TradeKind.SHORT: 2, TradeKind.DAYTRADE_SELL: 3}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_capital_mapping.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_models.py backend/services/capital_mapping.py backend/tests/test_capital_mapping.py
git commit -m "feat(capital): TradeKind 加無券賣出(sFlag=3)"
```

### Task 2: 安全閘拒「無券+買進」

**Files:**
- Modify: `backend/services/capital_safety.py:39-50`(check_stock_order)
- Test: `backend/tests/test_capital_safety.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_capital_safety.py` 檔尾加(若該檔已有 cfg helper 沿用之;以下自包含寫法):

```python
from services.capital_models import BuySell, TradeKind


def _cfg_on():
    return SafetyConfig(order_enabled=True, max_qty=10, max_amount=10_000_000)


def test_daytrade_sell_with_buy_rejected():
    req = StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=100.0, qty=1,
                            trade_kind=TradeKind.DAYTRADE_SELL)
    r = check_stock_order(req, _cfg_on())
    assert r.allowed is False
    assert "無券" in r.reason


def test_daytrade_sell_with_sell_allowed():
    req = StockOrderRequest(stock_no="2330", buy_sell=BuySell.SELL, price=100.0, qty=1,
                            trade_kind=TradeKind.DAYTRADE_SELL)
    assert check_stock_order(req, _cfg_on()).allowed is True
```

(該檔既有 import 已含 `StockOrderRequest`/`SafetyConfig`/`check_stock_order`;缺的補上即可。)

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_safety.py -v`
Expected: `test_daytrade_sell_with_buy_rejected` FAIL(目前放行)

- [ ] **Step 3: 實作**

`backend/services/capital_safety.py`:檔頭 import 改成

```python
from services.capital_models import BuySell, StockOrderRequest, TradeKind
```

`check_stock_order` 在 `if req.qty <= 0:` 之前插入:

```python
    # 無券=現股當沖先賣;「無券+買進」不是合法組合(回補=現股買進,交易所自動沖銷)
    if req.trade_kind == TradeKind.DAYTRADE_SELL and req.buy_sell == BuySell.BUY:
        return GateResult(False, "無券賣出僅能賣出;回補請用現股買進")
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_capital_safety.py tests/test_capital_client.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_safety.py backend/tests/test_capital_safety.py
git commit -m "feat(capital): 安全閘拒無券+買進組合"
```

### Task 3: 送單 request 加 source 欄位(稽核分流)

**Files:**
- Modify: `backend/services/capital_models.py:37-44`(StockOrderRequest)
- Test: `backend/tests/test_capital_audit.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_capital_audit.py` 檔尾加(沿用該檔既有 import;`capital_audit.write` + `json` 讀回驗證):

```python
def test_source_field_lands_in_audit(tmp_path):
    import json
    from services import capital_audit
    from services.capital_models import StockOrderRequest, BuySell, OrderResult

    p = tmp_path / "audit.jsonl"
    req = StockOrderRequest(stock_no="2330", buy_sell=BuySell.BUY, price=100.0, qty=1,
                            source="flash")
    capital_audit.write(p, env="test", req=req,
                        result=OrderResult(ok=True, code=0, message="ok"), action="order")
    entry = json.loads(p.read_text(encoding="utf-8").strip())
    assert entry["req"]["source"] == "flash"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_audit.py -v`
Expected: FAIL — `ValidationError: source — Extra inputs are not permitted`(或 KeyError,取決於 pydantic 設定)

- [ ] **Step 3: 實作**

`backend/services/capital_models.py`:檔頭加 `from typing import Literal`;`StockOrderRequest` 加欄位:

```python
class StockOrderRequest(BaseModel):
    stock_no: str
    buy_sell: BuySell
    price: float
    qty: int  # 張
    price_type: PriceType = PriceType.LIMIT
    time_in_force: TimeInForce = TimeInForce.ROD
    trade_kind: TradeKind = TradeKind.CASH
    source: Literal["panel", "flash"] = "panel"  # 稽核分流:單從哪個介面送出
```

(`to_stockorder_fields` 不讀 source,COM 欄位不變;稽核 `req.model_dump()` 自動帶入。)

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_capital_audit.py tests/test_capital_mapping.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_models.py backend/tests/test_capital_audit.py
git commit -m "feat(capital): 送單 source 欄位入稽核(panel/flash)"
```

### Task 4: /api/quote 透傳 reference_price

**Files:**
- Modify: `backend/routes/quote.py:40-47`
- Test: `backend/tests/test_quote_limit_flags.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_quote_limit_flags.py` 檔尾加(沿用該檔 client fixture 與 patch 模式):

```python
def test_forward_reference_price(client):
    fake_result = {"bids": [], "asks": [], "referencePrice": 1000.0}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.status_code == 200
    assert r.json()["reference_price"] == 1000.0


def test_missing_reference_price_is_null(client):
    fake_result = {"bids": [], "asks": []}
    with patch("routes.quote.get_fubon") as mock_get:
        fubon = mock_get.return_value
        fubon.status.value = "ok"
        fubon.intraday_quote = AsyncMock(return_value=fake_result)
        r = client.get("/api/quote/2330")
    assert r.json()["reference_price"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_quote_limit_flags.py -v`
Expected: 新 2 測 FAIL(KeyError: reference_price)

- [ ] **Step 3: 實作**

`backend/routes/quote.py` `get_quote` 的 return dict 加一行:

```python
            "reference_price": result.get("referencePrice"),
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_quote_limit_flags.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routes/quote.py backend/tests/test_quote_limit_flags.py
git commit -m "feat(quote): 透傳 reference_price(平盤參考價)"
```

### Task 5: 前端 lib/tick.ts(tick 引擎 + 漲跌停價)

**Files:**
- Create: `frontend/src/lib/tick.ts`
- Test: `frontend/src/lib/tick.test.ts`

演算法移植後端 `backend/services/cdp.py` 的 `tick_size`/`limit_up_price`(整數「分」運算防浮點誤差;該檔有完整測試背書)。

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/lib/tick.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { tickSize, roundToTick, limitUp, limitDown } from "./tick";

describe("tickSize 台股六級距", () => {
  it.each([
    [5, 0.01], [9.99, 0.01],
    [10, 0.05], [49.95, 0.05],
    [50, 0.1], [99.9, 0.1],
    [100, 0.5], [499.5, 0.5],
    [500, 1], [999, 1],
    [1000, 5], [1500, 5],
  ])("price %f → tick %f", (price, tick) => {
    expect(tickSize(price)).toBe(tick);
  });
});

describe("limitUp(對齊後端 cdp.limit_up_price 既有測例)", () => {
  it("整數對齊:100 → 110", () => expect(limitUp(100)).toBe(110));
  it("尾數捨去不超 +10%:10.05 → 11.05(非 11.06)", () => expect(limitUp(10.05)).toBe(11.05));
  it("以漲停價級距取 tick:49 → 53.9", () => expect(limitUp(49)).toBe(53.9));
  it("千元股 tick=5:1000 → 1100", () => expect(limitUp(1000)).toBe(1100));
  it("低價股:5 → 5.5", () => expect(limitUp(5)).toBe(5.5));
});

describe("limitDown(向上取,不超 -10%)", () => {
  it("整數對齊:100 → 90", () => expect(limitDown(100)).toBe(90));
  it("尾數進位:10.05 → 9.05(9.045 向上取至 tick 0.01)", () => expect(limitDown(10.05)).toBe(9.05));
  it("千元股:1000 → 900", () => expect(limitDown(1000)).toBe(900));
});

describe("roundToTick 跨級距", () => {
  it("向下:53.94 → 53.9(tick 0.1)", () => expect(roundToTick(53.94, "down")).toBe(53.9));
  it("向上:53.91 → 54.0", () => expect(roundToTick(53.91, "up")).toBe(54));
  it("浮點陷阱:53.9 down 不可誤捨成 53.8", () => expect(roundToTick(53.9, "down")).toBe(53.9));
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `frontend/`): `npx vitest run src/lib/tick.test.ts`
Expected: FAIL — Cannot find module './tick'

- [ ] **Step 3: 實作**

Create `frontend/src/lib/tick.ts`:

```typescript
// 台股 tick 級距與漲跌停價。演算法移植 backend/services/cdp.py(整數「分」運算防浮點誤差):
// 漲停 = 參考價 ×1.1 尾數捨去(絕不超過 +10%);跌停 = ×0.9 尾數進位(絕不超過 -10%);
// tick 以「換算後價位」所在級距為準。
const TICK_LADDER: ReadonlyArray<readonly [number, number]> = [
  [10, 0.01], [50, 0.05], [100, 0.1], [500, 0.5], [1000, 1], [Infinity, 5],
];

export function tickSize(price: number): number {
  for (const [upper, tick] of TICK_LADDER) if (price < upper) return tick;
  return 5;
}

export function roundToTick(price: number, dir: "up" | "down"): number {
  const tick = tickSize(price);
  const cents = Math.round(price * 100);       // 先殺浮點雜訊再除,53.9/0.1 才不會誤捨
  const tickCents = Math.round(tick * 100);
  const units = dir === "down" ? Math.floor(cents / tickCents) : Math.ceil(cents / tickCents);
  return Math.round(units * tickCents) / 100;
}

export function limitUp(reference: number): number {
  return roundToTick(reference * 1.1, "down");
}

export function limitDown(reference: number): number {
  return roundToTick(reference * 0.9, "up");
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/lib/tick.test.ts`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/tick.ts frontend/src/lib/tick.test.ts
git commit -m "feat(capital): tick 引擎+漲跌停價純函式(移植後端演算法)"
```

### Task 6: 前端 lib/qty-quick.ts(張數快捷狀態機)

**Files:**
- Create: `frontend/src/lib/qty-quick.ts`
- Test: `frontend/src/lib/qty-quick.test.ts`

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/lib/qty-quick.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { initialQtyState, pressQuick, manualQty, QTY_PRESETS } from "./qty-quick";

describe("張數快捷:單點填入、同顆累加、切顆重填", () => {
  it("presets 固定 1/3/5/10", () => expect(QTY_PRESETS).toEqual([1, 3, 5, 10]));

  it("首點 5 → 填入 5", () => {
    const s = pressQuick(initialQtyState(), 5);
    expect(s.qty).toBe(5);
  });

  it("再點 5 → 累加成 10", () => {
    const s = pressQuick(pressQuick(initialQtyState(), 5), 5);
    expect(s.qty).toBe(10);
  });

  it("點 5 再點 3 → 重填為 3(切顆不累加)", () => {
    const s = pressQuick(pressQuick(initialQtyState(), 5), 3);
    expect(s.qty).toBe(3);
  });

  it("手動輸入後再點快捷 → 重填(輸入打斷累加鏈)", () => {
    let s = pressQuick(initialQtyState(), 5);   // 5
    s = manualQty(s, 7);                          // 手動 7
    s = pressQuick(s, 5);                         // 點 5 → 填入 5(非 12)
    expect(s.qty).toBe(5);
  });

  it("manualQty 下限 1:0 與負值收斂到 1", () => {
    expect(manualQty(initialQtyState(), 0).qty).toBe(1);
    expect(manualQty(initialQtyState(), -3).qty).toBe(1);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/lib/qty-quick.test.ts`
Expected: FAIL — Cannot find module './qty-quick'

- [ ] **Step 3: 實作**

Create `frontend/src/lib/qty-quick.ts`:

```typescript
// 張數快捷:快捷鈕單點=填入、再點同一顆=累加、點不同顆或手動輸入=重置累加鏈。
export const QTY_PRESETS = [1, 3, 5, 10] as const;

export interface QtyState {
  qty: number;
  lastPreset: number | null; // 上一次按的快捷值;手動輸入後為 null(下次點快捷=填入)
}

export function initialQtyState(qty = 1): QtyState {
  return { qty: Math.max(1, qty), lastPreset: null };
}

export function pressQuick(s: QtyState, preset: number): QtyState {
  if (s.lastPreset === preset) return { qty: s.qty + preset, lastPreset: preset };
  return { qty: preset, lastPreset: preset };
}

export function manualQty(s: QtyState, qty: number): QtyState {
  return { qty: Math.max(1, Math.floor(qty)), lastPreset: null };
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/lib/qty-quick.test.ts`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/qty-quick.ts frontend/src/lib/qty-quick.test.ts
git commit -m "feat(capital): 張數快捷狀態機(填入/同顆累加/切顆重填)"
```

### Task 7: 前端型別 + 標籤表 + 確認彈窗顯示新欄位

**Files:**
- Create: `frontend/src/lib/capital-labels.ts`
- Modify: `frontend/src/lib/api.ts:349-353`(CapitalStockOrderReq)、`:38-45`(QuoteResponse)
- Modify: `frontend/src/components/OrderConfirmDialog.tsx:32-38`

- [ ] **Step 1: 建標籤表**

Create `frontend/src/lib/capital-labels.ts`:

```typescript
// 群益下單選項的顯示標籤 — OrderTicket / FlashPanel / OrderConfirmDialog 共用一份
export const TRADE_KINDS = ["cash", "margin", "short", "daytrade_sell"] as const;
export type TradeKindValue = (typeof TRADE_KINDS)[number];
export const TRADE_KIND_LABELS: Record<TradeKindValue, string> = {
  cash: "現股", margin: "融資", short: "融券", daytrade_sell: "無券",
};

export const TIF_VALUES = ["ROD", "IOC", "FOK"] as const;
export type TifValue = (typeof TIF_VALUES)[number];
```

- [ ] **Step 2: api.ts 型別擴充**

`frontend/src/lib/api.ts`:

`QuoteResponse` 加一行:

```typescript
export interface QuoteResponse {
  bids?: Array<{ price: number; size: number }>;
  asks?: Array<{ price: number; size: number }>;
  is_limit_up_bid?: boolean;
  is_limit_up_ask?: boolean;
  is_limit_down_bid?: boolean;
  is_limit_down_ask?: boolean;
  reference_price?: number | null;
}
```

`CapitalStockOrderReq` 改:

```typescript
export interface CapitalStockOrderReq {
  stock_no: string; buy_sell: "buy" | "sell"; price: number; qty: number;
  price_type?: "limit" | "market"; time_in_force?: "ROD" | "IOC" | "FOK";
  trade_kind?: "cash" | "margin" | "short" | "daytrade_sell";
  source?: "panel" | "flash";
}
```

- [ ] **Step 3: OrderConfirmDialog 顯示交易種類 / 條件**

`frontend/src/components/OrderConfirmDialog.tsx`:檔頭加 import

```typescript
import { TRADE_KIND_LABELS } from "../lib/capital-labels";
```

`<div className="space-y-1.5 …">` 內的 Rows 改為(在「買賣別」與「委託價」之間插兩列):

```tsx
          <Row k="標的" v={req.stock_no} />
          <Row k="買賣別" v={<span className={isBuy ? "text-bull" : "text-bear"}>{isBuy ? "買進" : "賣出"}</span>} />
          <Row k="交易種類" v={TRADE_KIND_LABELS[req.trade_kind ?? "cash"]} />
          <Row k="條件" v={`${req.time_in_force ?? "ROD"}${req.price_type === "market" ? " · 市價" : ""}`} />
          <Row k="委託價" v={req.price_type === "market" ? `市價(閘用估價 ${req.price.toFixed(2)})` : req.price.toFixed(2)} />
          <Row k="數量" v={`${req.qty} 張`} />
          <Row k="預估金額" v={`NT$ ${estAmount.toLocaleString()}`} />
```

- [ ] **Step 4: 型別檢查**

Run(在 `frontend/`): `npx tsc -b`
Expected: 無錯誤

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/capital-labels.ts frontend/src/lib/api.ts frontend/src/components/OrderConfirmDialog.tsx
git commit -m "feat(capital): 前端型別+標籤表+確認彈窗顯示種類/條件"
```

### Task 8: OrderTicket 抽出 + 下單匣完整 UI

**Files:**
- Create: `frontend/src/components/OrderTicket.tsx`(下單 tab 內容,自管表單 state)
- Modify: `frontend/src/components/TradingPanel.tsx`(瘦身為容器:健康燈+tabs+清單)

**設計:** TradingPanel 即將容納 4 個分頁,先把「下單匣+部位卡」抽成 `OrderTicket.tsx`(單一職責),TradingPanel 只留健康燈/分頁切換/清單。無自動化元件測試(專案慣例:邏輯都在 lib 純函式,Task 5/6 已測),本 task 以 `tsc -b` + 手動驗證收尾。

- [ ] **Step 1: 建 OrderTicket.tsx**

Create `frontend/src/components/OrderTicket.tsx`(把 TradingPanel 現有下單 tab 的 state/JSX 搬入並擴充;`PositionCard` 函式原樣搬過來):

```tsx
import { useEffect, useState } from "react";
import { api, type CapitalStockOrderReq } from "../lib/api";
import { subscribeOrderTicket, subscribeTicks } from "../hooks/useSignalsStream";
import { grossPnl, netPnl } from "../lib/capital-pnl";
import { limitUp, limitDown } from "../lib/tick";
import { initialQtyState, manualQty, pressQuick, QTY_PRESETS, type QtyState } from "../lib/qty-quick";
import { TIF_VALUES, TRADE_KINDS, TRADE_KIND_LABELS, type TifValue, type TradeKindValue } from "../lib/capital-labels";
import { OrderConfirmDialog } from "./OrderConfirmDialog";

const FEE = Number(import.meta.env.VITE_CAPITAL_FEE_RATE ?? "0.001425");
const TAX = Number(import.meta.env.VITE_CAPITAL_TAX_RATE ?? "0.003");

interface Props {
  selected: string | null;
  ready: boolean;
  env: string;
  pos: { qty: number; avg_price: number; name: string } | null;
}

export function OrderTicket({ selected, ready, env, pos }: Props) {
  const [buySell, setBuySell] = useState<"buy" | "sell">("buy");
  const [tradeKind, setTradeKind] = useState<TradeKindValue>("cash");
  const [tif, setTif] = useState<TifValue>("ROD");
  const [isMarket, setIsMarket] = useState(false);
  const [price, setPrice] = useState("");
  const [qtyState, setQtyState] = useState<QtyState>(initialQtyState());
  const [refPrice, setRefPrice] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<CapitalStockOrderReq | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  // 五檔點價 → 帶入委託價(沿用既有 bus)
  useEffect(() => subscribeOrderTicket((h) => {
    if (!selected || h.symbol === selected || h.symbol == null) setPrice(h.price.toFixed(2));
  }), [selected]);

  // 平盤參考價:一天一值,換標的時抓一次即可(漲跌停快捷與市價閘用估價都靠它)
  useEffect(() => {
    setRefPrice(null);
    if (!selected) return;
    let alive = true;
    api.quote(selected).then((r) => { if (alive) setRefPrice(r.reference_price ?? null); }).catch(() => {});
    return () => { alive = false; };
  }, [selected]);

  // 無券只能賣出
  const pickKind = (k: TradeKindValue) => {
    setTradeKind(k);
    if (k === "daytrade_sell") setBuySell("sell");
  };

  // 市價單:價格欄反灰,自動帶「閘用估價」(買=漲停、賣=跌停)— 金額閘與稽核才有依據
  useEffect(() => {
    if (!isMarket || refPrice == null) return;
    setPrice((buySell === "buy" ? limitUp(refPrice) : limitDown(refPrice)).toFixed(2));
  }, [isMarket, buySell, refPrice]);

  const qty = qtyState.qty;
  // 價格限 2 位小數(>2 位會被後端 %.2f 無聲四捨五入);市價時價格由系統帶,不驗使用者輸入
  const priceOk = /^\d+(\.\d{1,2})?$/.test(price.trim()) && Number(price) > 0;
  const inputOk = (isMarket ? Number(price) > 0 : priceOk) && qty > 0;
  const isBuy = buySell === "buy";

  const submit = () => {
    if (!selected) return;
    setConfirm({
      stock_no: selected, buy_sell: buySell,
      price: Number(price) || 0, qty,
      price_type: isMarket ? "market" : "limit",
      time_in_force: tif, trade_kind: tradeKind, source: "panel",
    });
  };
  const doSend = async () => {
    if (!confirm || sending) return;
    setSending(true);
    try {
      const r = await api.capitalSubmitStock(confirm);
      setMsg(`${r.ok ? "✓" : "✗"} ${r.message}`);
    } catch {
      setMsg("✗ 送單失敗");
    } finally {
      setSending(false);
    }
    setConfirm(null);
  };

  const estAmount = (Number(price) || 0) * qty * 1000;
  const segBtn = (active: boolean) =>
    `flex-1 py-1.5 text-xs rounded border ${active ? "bg-accent text-bg border-accent font-bold" : "border-line text-ink-dim hover:text-ink"}`;

  return (
    <>
      <div className="label-tiny mb-1">標的</div>
      <div className="bg-bg-deep border border-line px-3 py-2 text-sm tabular-nums mb-3">{selected ?? "—"}</div>

      <div className="flex gap-2 mb-2">
        <button onClick={() => setBuySell("buy")} disabled={tradeKind === "daytrade_sell"}
          className={`flex-1 py-2.5 font-bold rounded disabled:opacity-30 ${isBuy ? "bg-bull text-bg" : "border border-line text-ink-dim"}`}>買進</button>
        <button onClick={() => setBuySell("sell")}
          className={`flex-1 py-2.5 font-bold rounded ${!isBuy ? "bg-bear text-bg" : "border border-line text-ink-dim"}`}>賣出</button>
      </div>

      {/* 交易種類:四鈕常駐(混合型態不藏下拉) */}
      <div className="flex gap-1 mb-2">
        {TRADE_KINDS.map((k) => (
          <button key={k} onClick={() => pickKind(k)} className={segBtn(tradeKind === k)}>{TRADE_KIND_LABELS[k]}</button>
        ))}
      </div>

      {/* TIF */}
      <div className="flex gap-1 mb-3">
        {TIF_VALUES.map((t) => (
          <button key={t} onClick={() => setTif(t)} className={segBtn(tif === t)}>{t}</button>
        ))}
      </div>

      <div className="flex gap-2 mb-1.5 items-end">
        <div className="flex-1">
          <div className="label-tiny mb-1 flex justify-between">
            <span>委託價</span>
            <label className="flex items-center gap-1 cursor-pointer text-ink-dim">
              <input type="checkbox" checked={isMarket} onChange={(e) => setIsMarket(e.target.checked)} />市價
            </label>
          </div>
          <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" disabled={isMarket}
            className="w-full bg-bg-deep border border-line px-3 py-2 text-sm tabular-nums outline-none focus:border-accent disabled:opacity-40" />
        </div>
        <div className="flex-1">
          <div className="label-tiny mb-1">數量(張)</div>
          <div className="flex items-center border border-line bg-bg-deep">
            <button onClick={() => setQtyState((s) => manualQty(s, s.qty - 1))} className="px-2.5 py-2 text-ink-dim hover:text-ink">−</button>
            <input value={String(qty)} onChange={(e) => setQtyState((s) => manualQty(s, Number(e.target.value) || 1))}
              inputMode="numeric" className="w-full bg-transparent text-center text-sm tabular-nums outline-none" />
            <button onClick={() => setQtyState((s) => manualQty(s, s.qty + 1))} className="px-2.5 py-2 text-ink-dim hover:text-ink">+</button>
          </div>
        </div>
      </div>

      {/* 價格快捷(需 reference_price)+ 張數快捷 */}
      <div className="flex gap-1 mb-1.5">
        {([["跌停", () => refPrice != null && setPrice(limitDown(refPrice).toFixed(2))],
           ["平盤", () => refPrice != null && setPrice(refPrice.toFixed(2))],
           ["漲停", () => refPrice != null && setPrice(limitUp(refPrice).toFixed(2))]] as const
        ).map(([label, fn]) => (
          <button key={label} onClick={fn} disabled={refPrice == null || isMarket}
            className="flex-1 py-1 text-2xs border border-line text-ink-dim rounded hover:text-ink disabled:opacity-30">{label}</button>
        ))}
      </div>
      <div className="flex gap-1 mb-3">
        {QTY_PRESETS.map((p) => (
          <button key={p} onClick={() => setQtyState((s) => pressQuick(s, p))}
            className="flex-1 py-1 text-xs border border-line text-ink rounded hover:border-accent tabular-nums">{p}</button>
        ))}
      </div>

      <button onClick={submit} disabled={!ready || !selected || !inputOk}
        className={`w-full py-2.5 font-bold rounded text-bg disabled:opacity-40 ${isBuy ? "bg-bull" : "bg-bear"}`}>
        {isBuy ? "買進" : "賣出"} 送出
      </button>
      <div className="text-center text-2xs text-ink-dim mt-1.5">送出前會二次確認</div>
      {msg && <div className="text-center text-xs mt-2 text-ink-muted">{msg}</div>}

      <PositionCard symbol={selected} pos={pos} />

      {confirm && (
        <OrderConfirmDialog req={confirm} env={env} estAmount={estAmount}
          onConfirm={doSend} onClose={() => setConfirm(null)} />
      )}
    </>
  );
}

function PositionCard({ symbol, pos }: { symbol: string | null; pos: { qty: number; avg_price: number; name: string } | null }) {
  const [live, setLive] = useState<number | null>(null);
  useEffect(() => {
    setLive(null);
    if (!symbol) return;
    return subscribeTicks((t) => { if (t.symbol === symbol) setLive(t.price); });
  }, [symbol]);
  if (!pos) return <div className="mt-4 text-xs text-ink-dim border-t border-line pt-3">目前標的無部位</div>;
  const gross = grossPnl(pos.qty, pos.avg_price, live);
  const net = netPnl(pos.qty, pos.avg_price, live, FEE, TAX);
  const up = gross >= 0;
  return (
    <div className="mt-4 border border-line-strong rounded p-3 bg-bg-card">
      <div className="label-tiny mb-2">目前標的部位 · 即時</div>
      <div className="flex justify-between items-baseline">
        <span className="text-sm">{pos.qty} 張 · 均 {pos.avg_price.toFixed(2)}</span>
        <span className={`text-lg font-bold tabular-nums ${up ? "text-bull" : "text-bear"}`}>{up ? "+" : ""}{gross.toLocaleString()}</span>
      </div>
      <div className="flex justify-between text-xs text-ink-dim mt-1 tabular-nums">
        <span>現價 {live != null ? live.toFixed(2) : "—"}</span>
        <span>淨 {up ? "+" : ""}{net.toLocaleString()}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: TradingPanel 瘦身為容器**

`frontend/src/components/TradingPanel.tsx` 全檔改為:

```tsx
import { useState } from "react";
import { useCapitalStatus, useCapitalOrders, useCapitalPositions } from "../hooks/useCapital";
import { OrderTicket } from "./OrderTicket";
import { OrdersList } from "./OrdersList";

const ENV = (import.meta.env.VITE_CAPITAL_ENV ?? "test") as string;

export function TradingPanel({ selected }: { selected: string | null }) {
  const { status, lastError } = useCapitalStatus();
  const orders = useCapitalOrders();
  const positions = useCapitalPositions();
  const [tab, setTab] = useState<"order" | "list">("order");

  const ready = status === "ok";
  const pos = positions.find((p) => p.stock_no === selected) ?? null;

  return (
    <section className="flex flex-col min-w-0 min-h-0 border-l border-line pl-3">
      {/* 健康燈 + 環境 */}
      <div className="flex items-center gap-2 mb-3 flex-shrink-0">
        <span className={`w-2 h-2 rounded-full ${ready ? "bg-bear" : "bg-ink-dim"}`} />
        <span className="text-xs text-ink-dim">群益 {ready ? "已連線" : status}</span>
        <span className={`ml-auto text-xs px-2 py-0.5 rounded border ${ENV === "prod" ? "border-bull text-bull" : "border-bear/40 text-bear"}`}>
          {ENV === "prod" ? "正式" : "測試環境"}
        </span>
      </div>
      {/* 回報通道掛了(connect_reply 失敗)時 status 仍 ok、可送單但收不到回報 — 必須讓人看見 */}
      {lastError && <div className="text-2xs text-bear mb-2 flex-shrink-0">⚠ {lastError}</div>}

      {/* tabs */}
      <div className="flex border-b border-line-strong mb-3 flex-shrink-0 text-sm">
        <button onClick={() => setTab("order")} className={`flex-1 py-2 ${tab === "order" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>下單</button>
        <button onClick={() => setTab("list")} className={`flex-1 py-2 ${tab === "list" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>委託 {orders.length > 0 && <span className="text-accent">{orders.length}</span>}</button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto pr-1 scroll-editorial">
        {tab === "order" && <OrderTicket selected={selected} ready={ready} env={ENV} pos={pos} />}
        {tab === "list" && <OrdersList orders={orders} env={ENV} />}
      </div>
    </section>
  );
}
```

(閃電/庫存分頁在 Task 12 / Task 18 加入;此處刻意先維持兩分頁,行為與現況等價。)

- [ ] **Step 3: 型別檢查 + 全前端測試**

Run: `npx tsc -b && npm test`
Expected: 無型別錯誤、既有測試全 PASS

- [ ] **Step 4: 手動驗證(dev server)**

Run: `npm run dev`(或 user 既有 dev server),Monitor 頁確認:
1. 四種類鈕切換;選「無券」→ 買進鈕反灰且自動跳「賣出」
2. TIF 三鈕切換;市價勾選 → 價格欄反灰並自動帶漲停(買)/跌停(賣)值
3. 跌停/平盤/漲停快捷帶價(對照富邦 App 漲跌停價);無 reference_price(收盤後可能 null)→ 三鈕反灰
4. 張數:點 5→5、再點 5→10、點 3→3;stepper ± 正常
5. 送出 → 確認彈窗顯示「交易種類/條件」兩列新資訊
6. 委託 tab 行為不變

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/OrderTicket.tsx frontend/src/components/TradingPanel.tsx
git commit -m "feat(capital): 下單匣強化——四種類/TIF/市價/價格與張數快捷(OrderTicket 抽出)"
```

---

## Phase 2 — 閃電下單

### Task 9: lib/flash-ladder.ts(階梯生成 + 灰區 + 我N聚合)

**Files:**
- Create: `frontend/src/lib/flash-ladder.ts`
- Test: `frontend/src/lib/flash-ladder.test.ts`

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/lib/flash-ladder.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { buildLadder, type MyOrderLot } from "./flash-ladder";

const noDepth = { bids: [], asks: [] };

describe("buildLadder 階梯生成", () => {
  it("以現價對齊 tick 為中心,高價在前(陣列頭),步進跨級距正確", () => {
    // center 49.9(tick 0.05),向上跨入 50+(tick 0.1):…50.1, 50.0, 49.95, 49.9, 49.85…
    const rows = buildLadder({ center: 49.9, reference: 49.9, ...noDepth, myOrders: [], rows: 3 });
    expect(rows.map((r) => r.price)).toEqual([50.1, 50.0, 49.95, 49.9, 49.85, 49.8, 49.75]);
    expect(rows[3].isCenter).toBe(true);
  });

  it("範圍夾在漲跌停之間:reference=100 → 不超過 110 / 不低於 90", () => {
    const rows = buildLadder({ center: 109.5, reference: 100, ...noDepth, myOrders: [], rows: 30 });
    expect(Math.max(...rows.map((r) => r.price))).toBeLessThanOrEqual(110);
    expect(Math.min(...rows.map((r) => r.price))).toBeGreaterThanOrEqual(90);
  });

  it("±5% 外 clickable=false(fat-finger 灰區)", () => {
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: [], rows: 30 });
    const at = (p: number) => rows.find((r) => r.price === p)!;
    expect(at(105).clickable).toBe(true);    // 恰在 +5% 邊界(含)
    expect(at(105.5).clickable).toBe(false); // 超過
    expect(at(95).clickable).toBe(true);
    expect(at(94.5).clickable).toBe(false);
  });

  it("五檔量對到價位列;範圍外為 null", () => {
    const rows = buildLadder({
      center: 100, reference: 100,
      bids: [{ price: 99.9, size: 45 }], asks: [{ price: 100.5, size: 88 }],
      myOrders: [], rows: 10,
    });
    expect(rows.find((r) => r.price === 99.9)!.buyVol).toBe(45);
    expect(rows.find((r) => r.price === 100.5)!.sellVol).toBe(88);
    expect(rows.find((r) => r.price === 101)!.sellVol).toBeNull();
  });

  it("我N聚合:同價多單張數加總、買賣分欄", () => {
    const my: MyOrderLot[] = [
      { price: 99.5, buySell: "B", lots: 2 },
      { price: 99.5, buySell: "B", lots: 3 },
      { price: 100.5, buySell: "S", lots: 5 },
    ];
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: my, rows: 10 });
    expect(rows.find((r) => r.price === 99.5)!.myBuyLots).toBe(5);
    expect(rows.find((r) => r.price === 100.5)!.mySellLots).toBe(5);
  });

  it("reference=null → 用 center 估漲跌停並標 estimated", () => {
    const r = buildLadder({ center: 100, reference: null, ...noDepth, myOrders: [], rows: 5 });
    expect(r.every((row) => row.price <= 110 && row.price >= 90)).toBe(true);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/lib/flash-ladder.test.ts`
Expected: FAIL — Cannot find module './flash-ladder'

- [ ] **Step 3: 實作**

Create `frontend/src/lib/flash-ladder.ts`:

```typescript
// 閃電階梯純函式:吃 現價/平盤價/五檔/我的活單 → 回列陣列(高價在前)。
// 元件只負責渲染 — 對齊「無 hook 測試環境就抽 lib」慣例。
import { limitDown, limitUp, roundToTick, tickSize } from "./tick";

export interface MyOrderLot {
  price: number;
  buySell: "B" | "S";
  lots: number; // 未成交張數(order_qty - filled_qty)
}

export interface LadderRow {
  price: number;
  buyVol: number | null;   // 該價位委買量(五檔範圍外 = null)
  sellVol: number | null;
  myBuyLots: number;       // 我在該價位的活單張數
  mySellLots: number;
  isCenter: boolean;
  clickable: boolean;      // 離現價 ±5% 內才可點(fat-finger 灰區)
}

const CLICK_BAND = 0.05;

function stepUp(price: number): number {
  // 下一檔向上:加上「當前價」的 tick;跨級距時以新價位 round 修正
  return roundToTick(Math.round((price + tickSize(price)) * 100) / 100, "down");
}
function stepDown(price: number): number {
  const t = tickSize(Math.round((price - 0.001) * 100) / 100); // 50.0 往下一檔應該用 <50 的 tick
  return roundToTick(Math.round((price - t) * 100) / 100, "up");
}

export function buildLadder(opts: {
  center: number;
  reference: number | null;
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  myOrders: MyOrderLot[];
  rows?: number;
}): LadderRow[] {
  const { center, reference, bids, asks, myOrders } = opts;
  const half = opts.rows ?? 30;
  const ref = reference ?? center; // 缺平盤價 → 以現價估(面板標「估」)
  const up = limitUp(ref);
  const down = limitDown(ref);

  const c = roundToTick(center, "down");
  const prices: number[] = [c];
  let p = c;
  for (let i = 0; i < half && p < up; i++) { p = stepUp(p); if (p <= up) prices.unshift(p); }
  p = c;
  for (let i = 0; i < half && p > down; i++) { p = stepDown(p); if (p >= down) prices.push(p); }

  const bidMap = new Map(bids.map((b) => [b.price, b.size]));
  const askMap = new Map(asks.map((a) => [a.price, a.size]));
  const myBuy = new Map<number, number>();
  const mySell = new Map<number, number>();
  for (const o of myOrders) {
    const m = o.buySell === "B" ? myBuy : mySell;
    m.set(o.price, (m.get(o.price) ?? 0) + o.lots);
  }

  return prices.map((price) => ({
    price,
    buyVol: bidMap.get(price) ?? null,
    sellVol: askMap.get(price) ?? null,
    myBuyLots: myBuy.get(price) ?? 0,
    mySellLots: mySell.get(price) ?? 0,
    isCenter: price === c,
    clickable: Math.abs(price - center) / center <= CLICK_BAND + 1e-9,
  }));
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/lib/flash-ladder.test.ts src/lib/tick.test.ts`
Expected: 全 PASS(stepUp/stepDown 跨級距案例若 fail,修 step 函式而非測試 —— 測試值是台股真實 tick 序列)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/flash-ladder.ts frontend/src/lib/flash-ladder.test.ts
git commit -m "feat(capital): 閃電階梯純函式(生成/夾界/灰區/我N聚合)"
```

### Task 10: lib/flash-arm.ts(武裝狀態 reducer)

**Files:**
- Create: `frontend/src/lib/flash-arm.ts`
- Test: `frontend/src/lib/flash-arm.test.ts`

- [ ] **Step 1: 寫失敗測試**

Create `frontend/src/lib/flash-arm.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { ARM_IDLE_MS, initialArm, reduceArm } from "./flash-arm";

describe("武裝開關狀態機", () => {
  it("預設未武裝", () => expect(initialArm().armed).toBe(false));

  it("toggle 開/關,開時失敗計數歸零", () => {
    let s = reduceArm({ armed: false, failStreak: 2 }, { type: "toggle" });
    expect(s).toEqual({ armed: true, failStreak: 0 });
    s = reduceArm(s, { type: "toggle" });
    expect(s.armed).toBe(false);
  });

  it("換標的 / 連線斷 / 閒置逾時 → 解除武裝", () => {
    const armed = { armed: true, failStreak: 0 };
    for (const t of ["symbol_changed", "conn_lost", "idle_timeout"] as const) {
      expect(reduceArm(armed, { type: t }).armed).toBe(false);
    }
  });

  it("連續 3 次送單失敗 → 自動解除;成功會重置計數", () => {
    let s = { armed: true, failStreak: 0 };
    s = reduceArm(s, { type: "send_fail" });
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(true);
    s = reduceArm(s, { type: "send_ok" });      // 重置
    s = reduceArm(s, { type: "send_fail" });
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(true);                  // 只累積 2
    s = reduceArm(s, { type: "send_fail" });
    expect(s.armed).toBe(false);                 // 第 3 次 → 解除
    expect(s.failStreak).toBe(0);
  });

  it("閒置時限 = 5 分鐘", () => expect(ARM_IDLE_MS).toBe(5 * 60 * 1000));
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `npx vitest run src/lib/flash-arm.test.ts`
Expected: FAIL — Cannot find module './flash-arm'

- [ ] **Step 3: 實作**

Create `frontend/src/lib/flash-arm.ts`:

```typescript
// 閃電武裝開關狀態機。武裝=點價直送(無確認彈窗),是唯一繞過二次確認的路徑,
// 所以解除要寬鬆觸發:換標的/斷線/閒置 5 分鐘/連 3 次失敗。切分頁=unmount,state 自然消失。
export const ARM_IDLE_MS = 5 * 60 * 1000;
const FAIL_LIMIT = 3;

export interface ArmState {
  armed: boolean;
  failStreak: number;
}

export type ArmEvent =
  | { type: "toggle" }
  | { type: "symbol_changed" }
  | { type: "conn_lost" }
  | { type: "idle_timeout" }
  | { type: "send_ok" }
  | { type: "send_fail" };

export function initialArm(): ArmState {
  return { armed: false, failStreak: 0 };
}

export function reduceArm(s: ArmState, e: ArmEvent): ArmState {
  switch (e.type) {
    case "toggle":
      return { armed: !s.armed, failStreak: 0 };
    case "symbol_changed":
    case "conn_lost":
    case "idle_timeout":
      return { ...s, armed: false };
    case "send_ok":
      return { ...s, failStreak: 0 };
    case "send_fail": {
      const n = s.failStreak + 1;
      if (n >= FAIL_LIMIT) return { armed: false, failStreak: 0 };
      return { ...s, failStreak: n };
    }
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `npx vitest run src/lib/flash-arm.test.ts`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/flash-arm.ts frontend/src/lib/flash-arm.test.ts
git commit -m "feat(capital): 閃電武裝狀態機(toggle/自動解除/連3失敗)"
```

### Task 11: FlashPanel.tsx(閃電分頁元件)

**Files:**
- Create: `frontend/src/components/FlashPanel.tsx`

布局照核可 mockup(`.superpowers/brainstorm/363-1781095628/content/flash-layout.html`):標的+現價列 → 武裝開關 → 階梯 → 回中鈕 → 張數快捷 → 種類四鈕 → 狀態列+全刪。

- [ ] **Step 1: 建元件**

Create `frontend/src/components/FlashPanel.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type CapitalOrder } from "../lib/api";
import { useQuoteBook } from "../hooks/useQuoteBook";
import { subscribeTicks } from "../hooks/useSignalsStream";
import { buildLadder, type MyOrderLot } from "../lib/flash-ladder";
import { ARM_IDLE_MS, initialArm, reduceArm, type ArmState } from "../lib/flash-arm";
import { initialQtyState, manualQty, pressQuick, QTY_PRESETS, type QtyState } from "../lib/qty-quick";
import { TRADE_KINDS, TRADE_KIND_LABELS, type TradeKindValue } from "../lib/capital-labels";

interface Props {
  selected: string | null;
  ready: boolean;          // 群益 status === "ok"
  env: string;
  orders: CapitalOrder[];  // TradingPanel 既有的委託 store
  posQty: number | null;   // 該標的部位張數(無=null)
}

export function FlashPanel({ selected, ready, env, orders, posQty }: Props) {
  const { bids, asks } = useQuoteBook(selected);
  const [last, setLast] = useState<number | null>(null);
  const [refPrice, setRefPrice] = useState<number | null>(null);
  const [arm, setArm] = useState<ArmState>(initialArm());
  const [qtyState, setQtyState] = useState<QtyState>(initialQtyState());
  const [tradeKind, setTradeKind] = useState<TradeKindValue>("cash");
  const [hint, setHint] = useState<string | null>(null);
  const [followCenter, setFollowCenter] = useState(true);
  const [confirmAllCancel, setConfirmAllCancel] = useState(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastClick = useRef<{ key: string; ts: number } | null>(null);
  const centerRef = useRef<HTMLDivElement | null>(null);

  // 現價:WS tick 即時
  useEffect(() => {
    setLast(null);
    if (!selected) return;
    return subscribeTicks((t) => { if (t.symbol === selected) setLast(t.price); });
  }, [selected]);

  // 平盤參考價(一天一值)
  useEffect(() => {
    setRefPrice(null);
    if (!selected) return;
    let alive = true;
    api.quote(selected).then((r) => { if (alive) setRefPrice(r.reference_price ?? null); }).catch(() => {});
    return () => { alive = false; };
  }, [selected]);

  // 自動解除:換標的 / 連線斷
  useEffect(() => { setArm((s) => reduceArm(s, { type: "symbol_changed" })); }, [selected]);
  useEffect(() => { if (!ready) setArm((s) => reduceArm(s, { type: "conn_lost" })); }, [ready]);

  // 閒置 5 分鐘解除:任何互動 reset
  const touchIdle = () => {
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => setArm((s) => reduceArm(s, { type: "idle_timeout" })), ARM_IDLE_MS);
  };
  useEffect(() => () => { if (idleTimer.current) clearTimeout(idleTimer.current); }, []);

  // 我的活單(該標的)→ 我N徽章 + 全刪來源
  const myActionable = useMemo(
    () => orders.filter((o) => o.actionable && o.stock_no === selected),
    [orders, selected],
  );
  const myLots: MyOrderLot[] = useMemo(
    () => myActionable
      .filter((o) => o.price != null && (o.buy_sell === "B" || o.buy_sell === "S"))
      .map((o) => ({ price: o.price as number, buySell: o.buy_sell as "B" | "S", lots: Math.max(o.order_qty - o.filled_qty, 0) })),
    [myActionable],
  );

  const center = last ?? refPrice;
  const ladder = useMemo(
    () => (center != null ? buildLadder({ center, reference: refPrice, bids, asks, myOrders: myLots }) : []),
    [center, refPrice, bids, asks, myLots],
  );

  // 現價列自動置中
  useEffect(() => {
    if (followCenter) centerRef.current?.scrollIntoView({ block: "center" });
  }, [ladder, followCenter]);

  const clickPrice = async (price: number, side: "buy" | "sell", clickable: boolean) => {
    touchIdle();
    if (!selected || !ready || !clickable) return;
    if (tradeKind === "daytrade_sell" && side === "buy") return; // 無券鎖買側(UI 也反灰)
    if (!arm.armed) { setHint("未武裝 — 點價不送單"); return; }
    const key = `${side}:${price}`;
    const now = Date.now();
    if (lastClick.current && lastClick.current.key === key && now - lastClick.current.ts < 500) return; // 同格防抖
    lastClick.current = { key, ts: now };
    try {
      const r = await api.capitalSubmitStock({
        stock_no: selected, buy_sell: side, price, qty: qtyState.qty,
        price_type: "limit", time_in_force: "ROD", trade_kind: tradeKind, source: "flash",
      });
      setHint(`${r.ok ? "⚡" : "✗"} ${side === "buy" ? "買" : "賣"} ${price.toFixed(2)} × ${qtyState.qty}:${r.message}`);
      setArm((s) => reduceArm(s, { type: r.ok ? "send_ok" : "send_fail" }));
    } catch {
      setHint("✗ 送單失敗");
      setArm((s) => reduceArm(s, { type: "send_fail" }));
    }
  };

  // 點「我N」→ 刪該價位該方向全部活單(逐筆)
  const cancelAt = async (price: number, side: "B" | "S") => {
    touchIdle();
    const targets = myActionable.filter((o) => o.price === price && o.buy_sell === side);
    if (targets.length === 0) return;
    const results = await Promise.allSettled(targets.map((o) => api.capitalCancelOrder({ seq_no: o.seq_no })));
    const fail = results.filter((r) => r.status === "rejected" || !(r as PromiseFulfilledResult<{ ok: boolean }>).value?.ok).length;
    setHint(fail === 0 ? `已刪 ${price.toFixed(2)} 的 ${targets.length} 筆掛單` : `✗ ${fail}/${targets.length} 筆刪單失敗`);
  };

  const cancelAll = async () => {
    setConfirmAllCancel(false);
    const results = await Promise.allSettled(myActionable.map((o) => api.capitalCancelOrder({ seq_no: o.seq_no })));
    const fail = results.filter((r) => r.status === "rejected" || !(r as PromiseFulfilledResult<{ ok: boolean }>).value?.ok).length;
    setHint(fail === 0 ? `已送出全部刪單(${results.length} 筆)` : `✗ ${fail}/${results.length} 筆刪單失敗`);
  };

  if (!selected) return <div className="text-xs text-ink-dim py-4 text-center">先從自選/五檔選一檔標的</div>;
  const prod = env === "prod";
  const estimated = refPrice == null;

  return (
    <div className="flex flex-col min-h-0 h-full" onPointerDown={touchIdle}>
      {/* 標的 + 現價 */}
      <div className="flex justify-between items-baseline mb-2 flex-shrink-0">
        <span className="text-sm font-bold">{selected}</span>
        <span className="text-sm tabular-nums">{last != null ? last.toFixed(2) : "—"}{estimated && <span className="text-2xs text-ink-dim ml-1">估</span>}</span>
      </div>

      {/* 武裝開關 */}
      <button onClick={() => { touchIdle(); setArm((s) => reduceArm(s, { type: "toggle" })); }}
        className={`flex items-center gap-2 border rounded px-3 py-1.5 mb-2 flex-shrink-0 text-xs
          ${arm.armed ? (prod ? "border-bull bg-bull/20 text-bull font-bold" : "border-accent bg-accent/10 text-accent font-bold") : "border-line-strong bg-bg-deep text-ink-dim"}`}>
        <span className={`w-8 h-4 rounded-full relative ${arm.armed ? "bg-accent" : "bg-line-strong"}`}>
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-bg transition-all ${arm.armed ? "left-4" : "left-0.5"}`} />
        </span>
        {arm.armed ? (prod ? "⚠ 已武裝(正式環境)— 點價直接送單" : "已武裝 — 點價直接送單") : "未武裝 — 點價不送單"}
      </button>

      {/* 階梯 */}
      <div className="flex-1 min-h-0 overflow-y-auto border border-line rounded bg-bg-card"
        onScroll={() => setFollowCenter(false)}>
        <div className="grid grid-cols-[1fr_72px_1fr] text-2xs text-ink-dim sticky top-0 bg-bg-deep border-b border-line">
          <span className="text-right pr-2 py-1">委買</span><span className="text-center py-1">價格</span><span className="pl-2 py-1">委賣</span>
        </div>
        {ladder.map((row) => (
          <div key={row.price} ref={row.isCenter ? centerRef : undefined}
            className={`grid grid-cols-[1fr_72px_1fr] h-[26px] text-xs border-b border-line/40 ${row.isCenter ? "bg-accent/10" : ""}`}>
            <button disabled={!row.clickable || tradeKind === "daytrade_sell"}
              onClick={() => row.myBuyLots > 0 ? cancelAt(row.price, "B") : clickPrice(row.price, "buy", row.clickable)}
              className={`flex items-center justify-end pr-2 tabular-nums ${row.clickable && tradeKind !== "daytrade_sell" ? "text-bull hover:bg-bull/10 cursor-pointer" : "text-ink-dim/40"}`}>
              {row.myBuyLots > 0 && <span className="text-2xs bg-accent text-bg rounded px-1 mr-1 font-bold">我{row.myBuyLots}</span>}
              {row.buyVol ?? ""}
            </button>
            <span className={`flex items-center justify-center tabular-nums border-x border-line ${row.isCenter ? "text-accent font-bold" : "text-ink-muted"}`}>
              {row.price.toFixed(2)}{row.isCenter ? " ◄" : ""}
            </span>
            <button disabled={!row.clickable}
              onClick={() => row.mySellLots > 0 ? cancelAt(row.price, "S") : clickPrice(row.price, "sell", row.clickable)}
              className={`flex items-center justify-start pl-2 tabular-nums ${row.clickable ? "text-bear hover:bg-bear/10 cursor-pointer" : "text-ink-dim/40"}`}>
              {row.sellVol ?? ""}
              {row.mySellLots > 0 && <span className="text-2xs bg-accent text-bg rounded px-1 ml-1 font-bold">我{row.mySellLots}</span>}
            </button>
          </div>
        ))}
      </div>
      {!followCenter && (
        <button onClick={() => setFollowCenter(true)}
          className="text-2xs text-accent py-1 border border-t-0 border-line rounded-b bg-bg-deep flex-shrink-0">◎ 回到現價</button>
      )}

      {/* 張數快捷 + stepper */}
      <div className="flex gap-1 mt-2 items-center flex-shrink-0">
        {QTY_PRESETS.map((p) => (
          <button key={p} onClick={() => { touchIdle(); setQtyState((s) => pressQuick(s, p)); }}
            className="flex-1 py-1 text-xs border border-line text-ink rounded hover:border-accent tabular-nums">{p}</button>
        ))}
        <div className="flex items-center border border-line-strong rounded bg-bg-card">
          <button onClick={() => setQtyState((s) => manualQty(s, s.qty - 1))} className="px-2 py-1 text-ink-dim">−</button>
          <span className="text-sm font-bold tabular-nums min-w-[22px] text-center text-accent">{qtyState.qty}</span>
          <button onClick={() => setQtyState((s) => manualQty(s, s.qty + 1))} className="px-2 py-1 text-ink-dim">+</button>
        </div>
      </div>

      {/* 交易種類 */}
      <div className="flex gap-1 mt-1.5 flex-shrink-0">
        {TRADE_KINDS.map((k) => (
          <button key={k} onClick={() => { touchIdle(); setTradeKind(k); }}
            className={`flex-1 py-1 text-2xs rounded border ${tradeKind === k ? "bg-accent text-bg border-accent font-bold" : "border-line text-ink-dim"}`}>
            {TRADE_KIND_LABELS[k]}
          </button>
        ))}
      </div>

      {/* 狀態列 + 全刪 */}
      <div className="flex justify-between items-center mt-2 pt-1.5 border-t border-line text-2xs text-ink-dim flex-shrink-0">
        <span>掛單 {myActionable.length} 筆{posQty != null ? ` · 部位 ${posQty > 0 ? "+" : ""}${posQty} 張` : ""}</span>
        <button onClick={() => setConfirmAllCancel(true)} disabled={myActionable.length === 0}
          className="px-2 py-0.5 border border-bull text-bull rounded disabled:opacity-30">全部刪單</button>
      </div>
      {hint && <div className="text-center text-2xs mt-1 text-ink-muted flex-shrink-0">{hint}</div>}

      {/* 全刪確認(唯一保留彈窗的閃電操作) */}
      {confirmAllCancel && (
        <>
          <div onClick={() => setConfirmAllCancel(false)} className="fixed inset-0 z-20 bg-bg-deep/85" />
          <div role="dialog" aria-modal="true"
            className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border p-5 w-[min(300px,90vw)] ${prod ? "border-bull" : "border-line-strong"}`}
            style={{ transform: "translate(-50%, -50%)" }}>
            <h3 className="font-serif font-bold text-lg mb-2">刪除全部掛單?</h3>
            <p className="text-xs text-ink-dim mb-4">{selected} 共 {myActionable.length} 筆活單將逐筆送出刪單。</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmAllCancel(false)} className="px-3 py-1.5 text-sm border border-line-strong text-ink-muted">取消</button>
              <button onClick={cancelAll} className="px-3 py-1.5 text-sm text-bg font-medium bg-bull">全部刪單</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 型別檢查**

Run: `npx tsc -b`
Expected: 無錯誤(FlashPanel 尚未被引用,下一 task 接入)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/FlashPanel.tsx
git commit -m "feat(capital): FlashPanel 閃電分頁(武裝/點價直送/我N即點即刪/全刪確認)"
```

### Task 12: TradingPanel 接入閃電分頁 + 手動驗證

**Files:**
- Modify: `frontend/src/components/TradingPanel.tsx`(tab 加 "flash",順序 下單|⚡閃電|委託)

- [ ] **Step 1: 接入**

`frontend/src/components/TradingPanel.tsx`:

import 加:

```tsx
import { FlashPanel } from "./FlashPanel";
```

tab state 改:

```tsx
  const [tab, setTab] = useState<"order" | "flash" | "list">("order");
```

tabs 區塊改(順序:下單|⚡閃電|委託):

```tsx
      <div className="flex border-b border-line-strong mb-3 flex-shrink-0 text-sm">
        <button onClick={() => setTab("order")} className={`flex-1 py-2 ${tab === "order" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>下單</button>
        <button onClick={() => setTab("flash")} className={`flex-1 py-2 ${tab === "flash" ? "text-accent border-b-2 border-accent" : "text-ink-dim"}`}>⚡閃電</button>
        <button onClick={() => setTab("list")} className={`flex-1 py-2 ${tab === "list" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>委託 {orders.length > 0 && <span className="text-accent">{orders.length}</span>}</button>
      </div>
```

內容區改(flash 分頁不要外層 overflow 容器搶捲動,給滿高):

```tsx
      <div className={`flex-1 min-h-0 ${tab === "flash" ? "" : "overflow-y-auto pr-1 scroll-editorial"}`}>
        {tab === "order" && <OrderTicket selected={selected} ready={ready} env={ENV} pos={pos} />}
        {tab === "flash" && <FlashPanel selected={selected} ready={ready} env={ENV} orders={orders} posQty={pos?.qty ?? null} />}
        {tab === "list" && <OrdersList orders={orders} env={ENV} />}
      </div>
```

(切走 flash 分頁=條件渲染 unmount → 武裝 state 消失=自動解除,spec「切分頁解除」由此保證。)

- [ ] **Step 2: 型別檢查 + 全測試**

Run: `npx tsc -b && npm test`
Expected: 無錯誤、全 PASS

- [ ] **Step 3: 手動驗證(dev server,測試環境 CAPITAL_ENV=test)**

1. ⚡閃電分頁:階梯以現價置中、上下檔價位符合該股 tick(對照五檔)
2. 未武裝點價 → 只出提示不送單
3. 武裝 → 點 ±5% 內價位直送(委託分頁/群益 App 看到單);±5% 外反灰不可點
4. 掛單價位出現金色「我N」;點它 → 刪單(委託分頁看到已刪)
5. 手動捲動 → 出現「回到現價」;點了恢復跟隨
6. 切到委託分頁再回來 → 武裝已自動解除
7. 換標的 → 武裝解除
8. 全部刪單 → 彈確認 → 逐筆刪
9. 無券種類:買側整排反灰

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/TradingPanel.tsx
git commit -m "feat(capital): 閃電分頁接入(下單|⚡閃電|委託)"
```

---

## Phase 3 — 庫存總覽 + 一鍵平倉

### Task 13: COM 層 get_real_balance + OrderLib 事件 sink

**Files:**
- Modify: `backend/services/capital_com.py`(Protocol、SkcomCapitalCom、新 _OrderEvents)
- Modify: `backend/tests/test_capital_client.py:11`(FakeCom.setup 簽名同步)
- Test: `backend/tests/test_capital_com.py`

**注意:** `GetRealBalanceReport` 結果走 `SKOrderLib` 的 `OnRealBalanceReport` 事件 —— 目前只有 ReplyLib 有事件 sink,要新建 OrderLib sink。

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_capital_com.py` 檔尾加(該檔既有測試是測純邏輯/protocol,不載入真 COM;沿用):

```python
def test_order_events_sink_forwards_balance_and_swallows_exception():
    from services.capital_com import _OrderEvents

    got = []
    sink = _OrderEvents(on_balance=got.append)
    sink.OnRealBalanceReport("TS,1234567,2330,...")
    assert got == ["TS,1234567,2330,..."]

    def boom(_): raise RuntimeError("handler 炸了")
    sink2 = _OrderEvents(on_balance=boom)
    sink2.OnRealBalanceReport("x")   # 不可往 COM 事件迴圈丟例外


def test_order_events_sink_none_handler_is_noop():
    from services.capital_com import _OrderEvents
    _OrderEvents(on_balance=None).OnRealBalanceReport("x")  # 不炸即可
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_com.py -v`
Expected: FAIL — cannot import `_OrderEvents`

- [ ] **Step 3: 實作**

`backend/services/capital_com.py`:

Protocol 兩處修改:

```python
class CapitalCom(Protocol):
    def setup(self, on_reply: "Callable[[str], None] | None" = None,
              on_balance: "Callable[[str], None] | None" = None) -> None: ...
    # …既有方法不動,檔尾加:
    def get_real_balance(self, user_id: str, full_account: str) -> int: ...
```

`SkcomCapitalCom.__init__` 加兩個欄位(與 reply 對稱,advise 連線存住防 GC Unadvise):

```python
        self._order_sink = None
        self._order_conn = None
```

`setup` 簽名與內容改(在 `self._reply_conn = …` 之後加兩行):

```python
    def setup(self, on_reply: "Callable[[str], None] | None" = None,
              on_balance: "Callable[[str], None] | None" = None) -> None:
        # …既有內容到 self._reply_conn = comtypes.client.GetEvents(self._reply, self._reply_sink) 為止不動
        self._order_sink = _OrderEvents(on_balance)
        self._order_conn = comtypes.client.GetEvents(self._order, self._order_sink)
```

類別方法加:

```python
    def get_real_balance(self, user_id: str, full_account: str) -> int:
        # 非同步查詢:nCode 同步回,結果走 OnRealBalanceReport 事件
        return self._order.GetRealBalanceReport(user_id, full_account)
```

檔尾加 sink(comtypes 對 sink 缺的事件方法只 log 不炸,僅實作需要的):

```python
class _OrderEvents:
    """SKOrderLib 事件 sink。目前只接即時庫存;回呼例外不可炸掉 COM 事件迴圈。"""

    def __init__(self, on_balance: "Callable[[str], None] | None" = None) -> None:
        self._on_balance = on_balance

    def OnRealBalanceReport(self, bstrData):
        if self._on_balance:
            try:
                self._on_balance(bstrData)
            except Exception:
                pass
```

`backend/tests/test_capital_client.py` 的 `FakeCom`:

```python
    def setup(self, on_reply=None, on_balance=None): ...
    def get_real_balance(self, user_id, full_account):
        self.sent.append(("get_real_balance", full_account))
        return 0
```

(`RecordingCom.setup` 同步改簽名:`def setup(self, on_reply=None, on_balance=None): self.calls.append("setup")`。)

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_capital_com.py tests/test_capital_client.py tests/test_capital_client_reply.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_com.py backend/tests/test_capital_com.py backend/tests/test_capital_client.py
git commit -m "feat(capital): COM 層即時庫存查詢+OrderLib 事件 sink"
```

### Task 14: 解析器 capital_balance.py(假設表 + 校準迴圈)

**Files:**
- Create: `backend/services/capital_balance.py`
- Test: `backend/tests/test_capital_balance.py`

**⚠ 欄位假設聲明(spec 開放項 1 的執行面):** `OnRealBalanceReport` 的逗號字串欄位 index 以下列「假設表」實作 —— 參考群益官方範例慣例,**未經實測釘死**。Task 15 的 probe 會印真實字串;首測時對照群益 App 持倉校準 index,並把真實樣本(去敏後)換進測試。解析器以「欄位數不足/數字解析失敗 → 整筆略過 + log」防禦,錯誤假設不會讓 positions 出現垃圾資料。

假設表(probe 後校準):`[0]市場別 [1]帳號 [2]商品代號 [3]昨日餘額(股) [4]今日買進(股) [5]今日賣出(股) [6]現股餘額(股) [7]均價`;查詢結束時群益慣例推一筆 `##`-開頭(或空)字串作結束標記。

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_capital_balance.py`:

```python
"""OnRealBalanceReport 解析。樣本依「假設表」構造 — 首測後以真實字串(去敏)替換並校準 index。"""
from services.capital_balance import BalanceCollector, parse_balance_line


def test_parse_single_line_to_position():
    p = parse_balance_line("TS,1234567,2330,1000,2000,0,3000,985.5")
    assert p is not None
    assert p.stock_no == "2330"
    assert p.qty == 3          # 3000 股 → 3 張
    assert p.avg_price == 985.5


def test_unparseable_or_short_line_skipped():
    assert parse_balance_line("##") is None                  # 結束/雜訊標記
    assert parse_balance_line("TS,1234567") is None          # 欄位不足
    assert parse_balance_line("TS,x,2330,a,b,c,not_num,z") is None  # 數字欄壞 → 整筆略過


def test_zero_qty_line_skipped():
    # 已出清的標的(餘額 0)不該佔一列
    assert parse_balance_line("TS,1234567,2330,1000,0,1000,0,985.5") is None


def test_collector_flush_on_end_marker():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.feed("TS,1234567,2317,1000,0,0,1000,100.0")
    assert got == []                       # 未收到結束標記不 flush
    c.feed("##")
    assert len(got) == 1
    assert [p.stock_no for p in got[0]] == ["2330", "2317"]


def test_collector_timeout_flush():
    got = []
    c = BalanceCollector(on_complete=got.append, timeout_s=0.0)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.poll(now_monotonic=10.0)             # 超過 timeout → flush
    assert len(got) == 1


def test_collector_new_query_resets_staging():
    got = []
    c = BalanceCollector(on_complete=got.append)
    c.feed("TS,1234567,2330,0,3000,0,3000,985.5")
    c.reset()                              # 新一輪查詢
    c.feed("##")
    assert got == [[]]                     # staging 已清,flush 空集合(全部出清的合法狀態)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_balance.py -v`
Expected: FAIL — No module named capital_balance

- [ ] **Step 3: 實作**

Create `backend/services/capital_balance.py`:

```python
"""OnRealBalanceReport(即時庫存)解析與收集。

⚠ 欄位 index 為「假設表」(參考官方範例慣例,未實測):
  [0]市場別 [1]帳號 [2]商品代號 [3]昨日餘額 [4]今日買進 [5]今日賣出 [6]現股餘額(股) [7]均價
首測流程:scripts/capital_smoke.py --balance 印原始字串 → 對照群益 App 持倉校準 index
→ 真實樣本(去敏)換進 test_capital_balance.py。解析失敗整筆略過 + log,
錯誤假設只會讓清單缺列,不會出垃圾。

事件節奏未知(可能每檔一事件、結尾 ## 標記)→ BalanceCollector 雙保險:
收到結束標記 flush,或 timeout 後由 COM 執行緒 poll() flush。
"""
from __future__ import annotations
import logging
import time
from typing import Callable

from services.capital_models import Position

logger = logging.getLogger(__name__)

_IDX_STOCK_NO = 2
_IDX_SHARES = 6
_IDX_AVG = 7
_MIN_FIELDS = 8


def parse_balance_line(raw: str) -> Position | None:
    """一筆事件字串 → Position;結束標記/欄位不足/數字壞/餘額 0 → None。"""
    if not raw or raw.startswith("#"):
        return None
    parts = raw.split(",")
    if len(parts) < _MIN_FIELDS:
        return None
    try:
        shares = int(float(parts[_IDX_SHARES]))
        avg = float(parts[_IDX_AVG])
    except ValueError:
        logger.warning("balance line 解析失敗(index 假設可能要校準): %r", raw)
        return None
    if shares == 0:
        return None
    stock_no = parts[_IDX_STOCK_NO].strip()
    if not stock_no:
        return None
    return Position(stock_no=stock_no, qty=shares // 1000, avg_price=avg)


class BalanceCollector:
    """收集一輪查詢的多筆事件,結束標記或 timeout 後一次 flush(全量替換語意)。
    只在 COM 執行緒上被呼叫(feed=事件、poll=幫浦圈、reset=發查詢前),無鎖。"""

    def __init__(self, on_complete: Callable[[list[Position]], None], timeout_s: float = 1.0) -> None:
        self._on_complete = on_complete
        self._timeout_s = timeout_s
        self._staging: list[Position] = []
        self._last_feed: float | None = None

    def reset(self) -> None:
        self._staging = []
        self._last_feed = None

    def feed(self, raw: str) -> None:
        if raw and raw.startswith("#"):     # 結束標記
            self._flush()
            return
        p = parse_balance_line(raw)
        self._last_feed = time.monotonic()
        if p is not None:
            self._staging.append(p)

    def poll(self, now_monotonic: float | None = None) -> None:
        """COM 幫浦圈呼叫:有 staging 且距最後一筆事件超過 timeout → flush(沒等到 ## 的保險)。"""
        if self._last_feed is None:
            return
        now = time.monotonic() if now_monotonic is None else now_monotonic
        if now - self._last_feed >= self._timeout_s:
            self._flush()

    def _flush(self) -> None:
        out, self._staging, self._last_feed = self._staging, [], None
        self._on_complete(out)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_capital_balance.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_balance.py backend/tests/test_capital_balance.py
git commit -m "feat(capital): 即時庫存解析器+收集器(欄位假設表,首測校準)"
```

### Task 15: client 接線(查詢觸發/快取/推播)+ probe

**Files:**
- Modify: `backend/services/capital_client.py`(_handle_balance、request_balance、_run 觸發、_init_com)
- Modify: `backend/scripts/capital_smoke.py`(--balance flag)
- (WS 推播後端零修改:`main.py:93-97` 的 set_broadcast 透傳 payload,新 event 名自動下發)
- Modify: `frontend/src/hooks/useSignalsStream.ts:124-126`(WS case)與 `frontend/src/hooks/useCapital.ts:49-64`(訂閱)
- Test: `backend/tests/test_capital_client.py`

- [ ] **Step 1: 寫失敗測試**

在 `backend/tests/test_capital_client.py` 檔尾加:

```python
def test_handle_balance_lines_then_end_marker_updates_store(tmp_path):
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    client._handle_balance("TS,1234567,2330,0,3000,0,3000,985.5")
    client._handle_balance("##")
    pos = client.store.positions()
    assert len(pos) == 1
    assert pos[0].stock_no == "2330"
    assert pos[0].qty == 3


def test_fill_reply_marks_balance_dirty(tmp_path):
    """成交回報(D)後要排程一次庫存重查(debounce 由 _run 圈消化)。"""
    com = FakeCom()
    client = _client(com, enabled=True, audit_path=tmp_path / "a.jsonl")
    # 最小成交回報:走真實 parse_onnewdata 太長,直接驗 dirty 旗標 API
    client._mark_balance_dirty()
    assert client._balance_due is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_client.py -v`
Expected: 新 2 測 FAIL(無 _handle_balance/_mark_balance_dirty)

- [ ] **Step 3: 實作 client 接線**

`backend/services/capital_client.py`:

import 加:

```python
import time
from services.capital_balance import BalanceCollector
```

`__init__` 末尾加:

```python
        self._balance = BalanceCollector(on_complete=self._on_balance_complete)
        self._balance_due: float | None = None      # monotonic;成交後 debounce 重查
        self._balance_last_ts: float = 0.0           # 定時重查用
```

類別內加三個方法:

```python
    def _handle_balance(self, raw: str) -> None:
        """OnRealBalanceReport 事件(COM 執行緒)。"""
        self._balance.feed(raw)

    def _on_balance_complete(self, positions) -> None:
        self.store.set_positions(positions)
        self._balance_last_ts = time.monotonic()
        if self._broadcast:
            self._broadcast({"event": "capital_position", "data": {"count": len(positions)}})

    def _mark_balance_dirty(self, delay_s: float = 2.0) -> None:
        """成交回報後排程重查(debounce:連續成交只查尾端一次)。"""
        self._balance_due = time.monotonic() + delay_s

    def _maybe_query_balance(self) -> None:
        """_run 幫浦圈呼叫(COM 執行緒):due 到了或距上次查詢逾 60s → 發查詢。"""
        if self._status != "ok":
            return
        now = time.monotonic()
        due = self._balance_due is not None and now >= self._balance_due
        stale = now - self._balance_last_ts >= 60.0
        if not due and not stale:
            return
        self._balance_due = None
        self._balance_last_ts = now                 # 先記,失敗也不連發
        self._balance.reset()
        rc = self._com.get_real_balance(self._user_id, self._full_account)
        if rc != 0:
            logger.warning("GetRealBalanceReport rc=%s: %s", rc, self._com.return_code_message(rc))
```

`_handle_reply` 末尾(`if self._broadcast and rec.seq_no:` 區塊之前)加:

```python
        if rec.status_raw == "D":      # 成交 → 排程庫存重查
            self._mark_balance_dirty()
```

`_init_com` 的 `self._com.setup(self._handle_reply)` 改:

```python
            self._com.setup(self._handle_reply, self._handle_balance)
```

`_run` 的 while 迴圈裡、`self._com.pump()` 之後加:

```python
            self._balance.poll()
            self._maybe_query_balance()
```

- [ ] **Step 4: 跑後端測試**

Run: `python -m pytest tests/ -v -k capital`
Expected: 全 PASS

- [ ] **Step 5: probe + WS + 前端訂閱**

`backend/scripts/capital_smoke.py`:argparse 加 `ap.add_argument("--balance", action="store_true")`;`main` 簽名改 `main(send_test: bool, balance: bool)`,登入成功後加:

```python
    if balance:
        # 直接戳 client 的查詢排程,等事件進來;原始字串看 backend log(capital_balance 的 warning/解析)
        client._mark_balance_dirty(delay_s=0.0)
        await asyncio.sleep(5)
        for p in client.store.positions():
            print(f"持倉: {p.stock_no} {p.qty} 張 均 {p.avg_price}")
        print("(若清單空但群益 App 有持倉 → 看 log 的 balance line 警告,校準 capital_balance.py 假設表)")
```

末行改:`raise SystemExit(asyncio.run(main(args.send_test, args.balance)))`(先 `args = ap.parse_args()`)。

WS 推播:**後端零修改** —— `backend/main.py:93-97` 的 `set_broadcast` lambda 透傳整個 payload 給 broadcaster,`{"event": "capital_position"}` 自動走同一條鏈下發。

`frontend/src/hooks/useSignalsStream.ts`:`capital_order` case 旁加(同一個 bus,語意=「群益狀態變了,重抓」):

```typescript
        } else if (msg.event === "capital_position") {
          capitalOrderBus.dispatchEvent(new Event("capital_order"));
```

(`useCapitalPositions` 已訂 `subscribeCapitalOrders` → 自動受益,不用改。)

- [ ] **Step 6: 跑全測試 + Commit**

Run: `python -m pytest tests/ -q`(backend)、`npx tsc -b && npm test`(frontend)
Expected: 全 PASS

```bash
git add backend/services/capital_client.py backend/scripts/capital_smoke.py frontend/src/hooks/useSignalsStream.ts
git commit -m "feat(capital): 庫存查詢接線(成交後debounce+60s定時)+probe --balance+WS推播"
```

- [ ] **Step 7: 【首測校準閘】**

測試環境跑 `python scripts/capital_smoke.py --balance`,對照群益 App 持倉:
1. 印出的持倉列正確 → 把 log 中真實 raw(去敏:帳號改假)換進 `test_capital_balance.py` 樣本,commit
2. 不正確/空 → 校準 `capital_balance.py` 的 `_IDX_*` 假設表與結束標記判斷 → 測試樣本同步改 → 重跑 1
3. 確認「均價」欄真的存在於此報表;若無 → `avg_price` 暫填 0,UI 顯「—」,在 spec 開放項補記 follow-up(另查成本報表 API)

### Task 16: 平倉後端(反向映射 + close_position + route)

**Files:**
- Modify: `backend/services/capital_models.py`(PositionCloseRequest)
- Create: `backend/services/capital_close.py`(反向映射純函式)
- Modify: `backend/services/capital_client.py`(close_position;submit_stock_order 加 action 參數)
- Modify: `backend/routes/capital.py`(POST /api/capital/position/close)
- Test: `backend/tests/test_capital_close.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_capital_close.py`:

```python
"""平倉反向映射(spec §6.2 四規則)+ close 驗量。"""
import asyncio
import pytest
from services.capital_models import (
    BuySell, Position, PositionCloseRequest, TradeKind,
)
from services.capital_close import build_close_order


def test_long_cash_closes_with_cash_sell():
    pos = Position(stock_no="2330", qty=3, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", price=450.0)
    order = build_close_order(pos, req, pos_kind="cash")
    assert order.buy_sell == BuySell.SELL
    assert order.trade_kind == TradeKind.CASH
    assert order.qty == 3                      # 預設全部
    assert order.source == "panel"


def test_partial_qty_close():
    pos = Position(stock_no="2330", qty=5, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", qty=2, price=450.0)
    assert build_close_order(pos, req, pos_kind="cash").qty == 2


def test_qty_over_holding_rejected():
    pos = Position(stock_no="2330", qty=2, avg_price=500.0)
    req = PositionCloseRequest(stock_no="2330", qty=3, price=450.0)
    with pytest.raises(ValueError, match="超過持有"):
        build_close_order(pos, req, pos_kind="cash")


def test_margin_long_closes_with_margin_sell():
    pos = Position(stock_no="2330", qty=1, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=450.0), pos_kind="margin")
    assert order.buy_sell == BuySell.SELL
    assert order.trade_kind == TradeKind.MARGIN


def test_short_position_closes_with_short_buy():
    pos = Position(stock_no="2330", qty=-2, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=550.0), pos_kind="short")
    assert order.buy_sell == BuySell.BUY
    assert order.trade_kind == TradeKind.SHORT
    assert order.qty == 2                      # 取絕對值


def test_daytrade_short_closes_with_cash_buy():
    pos = Position(stock_no="2330", qty=-1, avg_price=500.0)
    order = build_close_order(pos, PositionCloseRequest(stock_no="2330", price=550.0), pos_kind="daytrade_sell")
    assert order.buy_sell == BuySell.BUY
    assert order.trade_kind == TradeKind.CASH  # 無券空單回補=現股買進(交易所自動沖銷)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_capital_close.py -v`
Expected: FAIL — no module capital_close / no PositionCloseRequest

- [ ] **Step 3: 實作**

`backend/services/capital_models.py` 檔尾加:

```python
class PositionCloseRequest(BaseModel):
    stock_no: str
    qty: int | None = None                    # None=全部
    price_type: PriceType = PriceType.MARKET
    price: float | None = None                # market=閘用估價(前端帶);limit=委託價
    source: Literal["panel", "flash"] = "panel"
```

(檔頭已在 Task 3 import Literal。)

Create `backend/services/capital_close.py`:

```python
"""平倉反向單組裝 — 純函式。部位種類 → 回補單(spec §6.2 固定映射):
現股多→現股賣;融資多→融資賣;融券空→融券買;無券空→現股買(交易所自動沖銷)。
v1 部位資料僅現股(GetRealBalanceReport),信用 pos_kind 由呼叫端在資料就緒後傳入。"""
from __future__ import annotations

from services.capital_models import (
    BuySell, Position, PositionCloseRequest, PriceType, StockOrderRequest, TradeKind,
)

# (部位種類, 是否多頭) → (回補方向, 回補交易種類)
_CLOSE_MAP: dict[tuple[str, bool], tuple[BuySell, TradeKind]] = {
    ("cash", True): (BuySell.SELL, TradeKind.CASH),
    ("margin", True): (BuySell.SELL, TradeKind.MARGIN),
    ("short", False): (BuySell.BUY, TradeKind.SHORT),
    ("daytrade_sell", False): (BuySell.BUY, TradeKind.CASH),
}


def build_close_order(pos: Position, req: PositionCloseRequest, *, pos_kind: str) -> StockOrderRequest:
    holding = abs(pos.qty)
    if holding == 0:
        raise ValueError(f"{req.stock_no} 無部位可平")
    lots = req.qty if req.qty is not None else holding
    if lots <= 0:
        raise ValueError("平倉數量必須大於 0")
    if lots > holding:
        raise ValueError(f"平倉 {lots} 張超過持有 {holding} 張")
    key = (pos_kind, pos.qty > 0)
    if key not in _CLOSE_MAP:
        raise ValueError(f"部位種類 {pos_kind} 與方向不符,無法平倉")
    side, kind = _CLOSE_MAP[key]
    if req.price is None or req.price <= 0:
        raise ValueError("缺平倉價格(市價單也需帶閘用估價)")
    return StockOrderRequest(
        stock_no=req.stock_no, buy_sell=side, price=req.price, qty=lots,
        price_type=req.price_type, trade_kind=kind, source=req.source,
    )
```

`backend/services/capital_client.py`:

`submit_stock_order` 簽名改(預設不變,稽核可分流):

```python
    async def submit_stock_order(self, req: StockOrderRequest, *, action: str = "order") -> OrderResult:
        def _do() -> tuple[str, int]:
            fields = to_stockorder_fields(req, self._full_account)
            return self._com.send_stock_order(self._user_id, fields)

        return await self._execute_write(
            action=action, req=req,
            gate=check_stock_order(req, self._safety), com_call=_do)
```

加方法(import 加 `from services.capital_close import build_close_order` 與 models 的 `PositionCloseRequest`):

```python
    async def close_position(self, req: PositionCloseRequest) -> OrderResult:
        pos = self.store.position_for(req.stock_no)
        if pos is None or pos.qty == 0:
            reason = f"{req.stock_no} 無部位可平"
            capital_audit.write(self._audit_path, env=self._env, req=req, blocked=reason, action="close")
            return OrderResult(ok=False, code=-1, message=reason)
        try:
            # v1 部位來源=現股報表 → pos_kind 恆 "cash"。信用部位資料接上後,從部位資料帶種類。
            order = build_close_order(pos, req, pos_kind="cash")
        except ValueError as e:
            capital_audit.write(self._audit_path, env=self._env, req=req, blocked=str(e), action="close")
            return OrderResult(ok=False, code=-1, message=str(e))
        return await self.submit_stock_order(order, action="close")
```

`backend/routes/capital.py`:import 加 `PositionCloseRequest`,檔尾加:

```python
@router.post("/api/capital/position/close")
async def capital_position_close(req: PositionCloseRequest) -> dict:
    res = await _require_capital().close_position(req)
    return res.model_dump(mode="json")
```

- [ ] **Step 4: client 整合測試**

在 `backend/tests/test_capital_close.py` 檔尾加:

```python
def test_close_no_position_blocked_and_audited(tmp_path):
    from tests.test_capital_client import FakeCom, _client
    client = _client(FakeCom(), enabled=True, audit_path=tmp_path / "a.jsonl")
    res = asyncio.run(client.close_position(PositionCloseRequest(stock_no="2330", price=100.0)))
    assert res.ok is False
    assert "無部位" in res.message
    assert (tmp_path / "a.jsonl").exists()    # 被拒也留稽核
```

Run: `python -m pytest tests/test_capital_close.py tests/ -q -k capital`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_models.py backend/services/capital_close.py backend/services/capital_client.py backend/routes/capital.py backend/tests/test_capital_close.py
git commit -m "feat(capital): 一鍵平倉——反向映射四規則+驗量+close endpoint"
```

### Task 17: 前端庫存分頁 + 平倉彈窗

**Files:**
- Modify: `frontend/src/lib/api.ts`(close API + 型別)
- Create: `frontend/src/components/PositionsList.tsx`
- Modify: `frontend/src/components/TradingPanel.tsx`(第四分頁「庫存」)

- [ ] **Step 1: api.ts 加 close**

`frontend/src/lib/api.ts`:Capital 區塊加型別與方法:

```typescript
export interface CapitalCloseReq {
  stock_no: string; qty?: number;
  price_type?: "limit" | "market"; price: number;  // market 時=閘用估價
  source?: "panel" | "flash";
}
```

api 物件(capitalDecreaseQty 之後)加:

```typescript
  capitalClosePosition: (req: CapitalCloseReq) =>
    fetchJSON<CapitalOrderResult>("/api/capital/position/close", {
      method: "POST",
      body: JSON.stringify(req),
    }),
```

- [ ] **Step 2: 建 PositionsList.tsx**

Create `frontend/src/components/PositionsList.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { api, type CapitalPosition } from "../lib/api";
import { subscribeTicks } from "../hooks/useSignalsStream";
import { grossPnl } from "../lib/capital-pnl";
import { limitDown, limitUp } from "../lib/tick";

/** 庫存總覽:每列 代號/張數/均價/現價/未實現損益;點列帶標的回下單匣;「平」=反向市價單(確認後送)。
 *  現價:WS tick 有訂的即時跳;其餘開分頁時 snapshot 批次補、30 秒刷新。 */
export function PositionsList({ positions, env, onPick }: {
  positions: CapitalPosition[]; env: string; onPick: (symbol: string) => void;
}) {
  const [live, setLive] = useState<Record<string, number>>({});
  const [closing, setClosing] = useState<CapitalPosition | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const symbols = useMemo(() => positions.map((p) => p.stock_no), [positions]);

  // WS tick 即時價
  useEffect(() => {
    if (symbols.length === 0) return;
    const set = new Set(symbols);
    return subscribeTicks((t) => {
      if (set.has(t.symbol)) setLive((m) => (m[t.symbol] === t.price ? m : { ...m, [t.symbol]: t.price }));
    });
  }, [symbols]);

  // snapshot 批次補(沒訂 tick 的標的也要有現價)+ 30s 刷新
  useEffect(() => {
    if (symbols.length === 0) return;
    let alive = true;
    const load = async () => {
      try {
        const r = await api.quotesSnapshot(symbols);
        if (!alive) return;
        setLive((m) => {
          const next = { ...m };
          for (const row of r.quotes) if (row.last_price != null) next[row.symbol] = next[row.symbol] ?? row.last_price;
          return next;
        });
      } catch { /* keep */ }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, [symbols]);

  if (positions.length === 0) return <div className="text-xs text-ink-dim py-4 text-center">目前無庫存部位</div>;

  const total = positions.reduce((sum, p) => sum + grossPnl(p.qty, p.avg_price, live[p.stock_no] ?? null), 0);
  const totalUp = total >= 0;

  return (
    <div>
      <div className="flex justify-between items-baseline border-b border-line-strong pb-2 mb-1">
        <span className="label-tiny">總未實現損益</span>
        <span className={`text-lg font-bold tabular-nums ${totalUp ? "text-bull" : "text-bear"}`}>
          {totalUp ? "+" : ""}{total.toLocaleString()}
        </span>
      </div>
      {positions.map((p) => {
        const cur = live[p.stock_no] ?? null;
        const pnl = grossPnl(p.qty, p.avg_price, cur);
        const up = pnl >= 0;
        const pct = cur != null && p.avg_price > 0 ? ((cur - p.avg_price) / p.avg_price) * 100 * Math.sign(p.qty) : null;
        return (
          <div key={p.stock_no} className="border-b border-line py-2 text-sm cursor-pointer hover:bg-bg-card"
            onClick={() => onPick(p.stock_no)}>
            <div className="flex items-center gap-2">
              <span className="font-serif font-medium">{p.stock_no} {p.name}</span>
              <span className="text-xs text-ink-dim tabular-nums">{p.qty} 張 · 均 {p.avg_price.toFixed(2)}</span>
              <button onClick={(e) => { e.stopPropagation(); setClosing(p); }}
                className="ml-auto px-2 py-0.5 text-xs border border-line-strong text-ink-muted hover:text-bear hover:border-bear rounded">平</button>
            </div>
            <div className="flex justify-between text-xs tabular-nums mt-0.5">
              <span className="text-ink-dim">現價 {cur != null ? cur.toFixed(2) : "—"}</span>
              <span className={up ? "text-bull" : "text-bear"}>
                {up ? "+" : ""}{pnl.toLocaleString()}{pct != null ? `(${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)` : ""}
              </span>
            </div>
          </div>
        );
      })}
      {msg && <div className="text-center text-xs mt-2 text-ink-muted">{msg}</div>}
      {closing && (
        <ClosePositionDialog pos={closing} env={env} cur={live[closing.stock_no] ?? null}
          onDone={(m) => { setMsg(m); setClosing(null); }} onClose={() => setClosing(null)} />
      )}
    </div>
  );
}

function ClosePositionDialog({ pos, env, cur, onDone, onClose }: {
  pos: CapitalPosition; env: string; cur: number | null; onDone: (msg: string) => void; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isLong = pos.qty > 0;
  const prod = env === "prod";
  // 市價平倉的「閘用估價」:賣出用跌停、買回用漲停(最保守的金額上限);基準=現價,缺現價用均價
  const base = cur ?? pos.avg_price;
  const gatePrice = isLong ? limitDown(base) : limitUp(base);

  const send = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.capitalClosePosition({
        stock_no: pos.stock_no, price_type: "market", price: gatePrice, source: "panel",
      });
      onDone(`${r.ok ? "✓" : "✗"} 平倉:${r.message}`);
    } catch {
      onDone("✗ 平倉送出失敗");
      setBusy(false);
    }
  };

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-20 bg-bg-deep/85" style={{ backdropFilter: "blur(2px)" }} />
      <div role="dialog" aria-modal="true"
        className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border p-5 w-[min(340px,90vw)] ${prod ? "border-bull" : "border-line-strong"}`}
        style={{ transform: "translate(-50%, -50%)" }}>
        <h3 className="font-serif font-bold text-lg mb-1">確認平倉</h3>
        <p className={`text-xs mb-3 ${prod ? "text-bull font-bold" : "text-bear"}`}>
          {prod ? "⚠ 正式環境(真錢)" : "測試環境"}
        </p>
        <div className="text-sm space-y-1 tabular-nums">
          <div className="flex justify-between"><span className="text-ink-dim">標的</span><span>{pos.stock_no} {pos.name}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">部位</span><span>{pos.qty} 張 · 均 {pos.avg_price.toFixed(2)}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">反向單</span>
            <span className={isLong ? "text-bear" : "text-bull"}>{isLong ? "賣出" : "買進"} {Math.abs(pos.qty)} 張 · 市價</span></div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-line-strong text-ink-muted hover:text-ink">取消</button>
          <button onClick={send} disabled={busy}
            className={`px-3 py-1.5 text-sm text-bg font-medium disabled:opacity-40 ${isLong ? "bg-bear" : "bg-bull"}`}>
            確認平倉
          </button>
        </div>
      </div>
    </>
  );
}
```

(`api.quotesSnapshot(symbols)` 既有,回 `SnapshotResponse { quotes: Array<{ symbol; prev_close; last_price }> }` —— `frontend/src/lib/api.ts:283-291, 498-502`。)

- [ ] **Step 3: TradingPanel 加第四分頁**

`frontend/src/components/TradingPanel.tsx`:

import 加 `import { PositionsList } from "./PositionsList";`;props 改 `{ selected, onPick }: { selected: string | null; onPick?: (s: string) => void }`。`frontend/src/pages/Monitor.tsx:248` 的 `<TradingPanel selected={selected} />` 改為 `<TradingPanel selected={selected} onPick={setSelected} />`(`setSelected` 即該頁 `useState` 的 setter,`Monitor.tsx:44`)。

tab state 與按鈕列改成四分頁:

```tsx
  const [tab, setTab] = useState<"order" | "flash" | "list" | "positions">("order");
```

```tsx
        <button onClick={() => setTab("positions")} className={`flex-1 py-2 ${tab === "positions" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>庫存 {positions.length > 0 && <span className="text-accent">{positions.length}</span>}</button>
```

內容區加:

```tsx
        {tab === "positions" && <PositionsList positions={positions} env={ENV} onPick={(s) => { onPick?.(s); setTab("order"); }} />}
```

- [ ] **Step 4: 型別檢查 + 全測試 + 手動驗證**

Run: `npx tsc -b && npm test`
Expected: 全 PASS

手動(測試環境,Task 15 校準閘過後):
1. 庫存分頁列出持倉(對照群益 App)、總損益隨 tick 跳動
2. 點列 → 帶標的回下單匣
3. 「平」→ 彈窗預覽反向單 → 確認 → 委託分頁出現市價賣單;回報成交後庫存自動刷新(WS capital_position)
4. 平超過持有量(改後端 req 手測)→ 被拒且稽核留底

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/PositionsList.tsx frontend/src/components/TradingPanel.tsx
git commit -m "feat(capital): 庫存分頁+一鍵平倉(總損益/點列帶單/反向市價)"
```

---

## 收尾

- [ ] **盤中實測清單(spec §8,測試環境先行)**:四種類各一筆(無券需可先賣標的)→ IOC/FOK 實際行為 → 市價單 bstrPrice 行為(開放項 2)→ 閃電全流程 → 平倉全鏈 → 庫存欄位校準(Task 15 Step 7)
- [ ] 全測試:`python -m pytest tests/ -q`(backend)+ `npm test && npx tsc -b`(frontend)
- [ ] 更新 `docs/superpowers/specs/2026-06-11-capital-order-panel-v2-design.md` 開放項句點(校準結果回填)
