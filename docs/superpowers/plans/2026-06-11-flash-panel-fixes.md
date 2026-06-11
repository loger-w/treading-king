# 閃電下單修正 + 庫存資料鏈修復 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依 `docs/superpowers/specs/2026-06-11-flash-panel-fixes-design.md`:閃電面板點擊語意改版(紅方格=刪、黃括號=成交紀錄、點量區=連續加掛)、跟隨置中修復、頂部庫存顯示、庫存損益凍結修復、委託排序帶日期。

**Architecture:** 前端為主 — 階梯聚合邏輯在 `flash-ladder.ts` 純函式(vitest),`FlashPanel.tsx` 只渲染。後端僅加回報日期欄位(idx23)與排序。庫存欄位校準(spec D2)是盤中人工流程,**不在本計畫**,計畫完成後提醒使用者執行。

**Tech Stack:** React + TypeScript + vitest(frontend/)、FastAPI + pytest(backend/)。

**分支:** `feat/capital-order-panel-v2`(直接續做,PR #22)

**測試指令:**
- 前端:`cd frontend && npx vitest run`(單檔:`npx vitest run src/lib/flash-ladder.test.ts`)
- 後端:`cd backend && python -m pytest -q`(單檔:`python -m pytest tests/test_capital_reply.py -v`)

---

### Task 1: flash-ladder.ts — splitMyLots 純函式 + 成交(黃括號)聚合

**Files:**
- Modify: `frontend/src/lib/flash-ladder.ts`
- Test: `frontend/src/lib/flash-ladder.test.ts`

- [ ] **Step 1: 寫失敗測試**

在 `frontend/src/lib/flash-ladder.test.ts` 追加(import 改為
`import { buildLadder, splitMyLots, type MyOrderLot, type MyOrderSource } from "./flash-ladder";`):

```ts
describe("splitMyLots 方格(active)/黃括號(fills)來源拆分", () => {
  const o = (over: Partial<MyOrderSource>): MyOrderSource => ({
    price: 100, buy_sell: "B", order_qty: 5, filled_qty: 0, actionable: true, ...over,
  });

  it("活單:未成交張數進 active、已成交張數進 fills(掛5成4 → 方格1+括號4)", () => {
    const { active, fills } = splitMyLots([o({ order_qty: 5, filled_qty: 4 })]);
    expect(active).toEqual([{ price: 100, buySell: "B", lots: 1 }]);
    expect(fills).toEqual([{ price: 100, buySell: "B", lots: 4 }]);
  });

  it("已完成單不進 active;成交過的進 fills(黃括號整日留存)、純刪單不留痕", () => {
    const fullyFilled = o({ actionable: false, order_qty: 5, filled_qty: 5 });
    const cancelledNoFill = o({ actionable: false, order_qty: 3, filled_qty: 0 });
    const { active, fills } = splitMyLots([fullyFilled, cancelledNoFill]);
    expect(active).toEqual([]);
    expect(fills).toEqual([{ price: 100, buySell: "B", lots: 5 }]);
  });

  it("市價單(price=null)/側別不明 → 不上階梯", () => {
    const { active, fills } = splitMyLots([
      o({ price: null, filled_qty: 2 }),
      o({ buy_sell: null, filled_qty: 2 }),
    ]);
    expect(active).toEqual([]);
    expect(fills).toEqual([]);
  });
});

describe("buildLadder myFills 聚合", () => {
  it("同價多單成交加總、買賣分欄;無成交的列為 0", () => {
    const fills: MyOrderLot[] = [
      { price: 99.5, buySell: "B", lots: 1 },
      { price: 99.5, buySell: "B", lots: 3 },
      { price: 100.5, buySell: "S", lots: 2 },
    ];
    const rows = buildLadder({ center: 100, reference: 100, ...noDepth, myOrders: [], myFills: fills, rows: 10 });
    expect(rows.find((r) => r.price === 99.5)!.myBuyFills).toBe(4);
    expect(rows.find((r) => r.price === 100.5)!.mySellFills).toBe(2);
    expect(rows.find((r) => r.price === 100)!.myBuyFills).toBe(0);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/flash-ladder.test.ts`
Expected: FAIL — `splitMyLots` / `MyOrderSource` 不存在、`myFills` 型別錯誤。

- [ ] **Step 3: 實作**

`frontend/src/lib/flash-ladder.ts`:

(a) `MyOrderLot` 介面後追加:

```ts
// CapitalOrder 的結構子集 — lib 不 import api 型別,保持零依賴可測
export interface MyOrderSource {
  price: number | null;
  buy_sell: string | null;
  order_qty: number;   // 顯示單位(張)
  filled_qty: number;
  actionable: boolean;
}

/** 該檔今日全部委託 → 方格(active=活單未成交)與黃括號(fills=成交紀錄,含已完成單)。
 *  成交歸屬委託價(券商慣例);市價單無價不上階梯。 */
export function splitMyLots(orders: MyOrderSource[]): { active: MyOrderLot[]; fills: MyOrderLot[] } {
  const active: MyOrderLot[] = [];
  const fills: MyOrderLot[] = [];
  for (const o of orders) {
    if (o.price == null || (o.buy_sell !== "B" && o.buy_sell !== "S")) continue;
    const side = o.buy_sell;
    const remaining = o.order_qty - o.filled_qty;
    if (o.actionable && remaining > 0) active.push({ price: o.price, buySell: side, lots: remaining });
    if (o.filled_qty > 0) fills.push({ price: o.price, buySell: side, lots: o.filled_qty });
  }
  return { active, fills };
}
```

(b) `LadderRow` 加兩欄(`mySellLots` 之後):

```ts
  myBuyFills: number;      // 我今日在該價位的成交張數(黃括號;含已完成單)
  mySellFills: number;
```

(c) `buildLadder` opts 加 `myFills?: MyOrderLot[];`(`myOrders` 之後),
函式內 `myBuy`/`mySell` 聚合之後加:

```ts
  const fillBuy = new Map<number, number>();
  const fillSell = new Map<number, number>();
  for (const o of opts.myFills ?? []) {
    const m = o.buySell === "B" ? fillBuy : fillSell;
    m.set(o.price, (m.get(o.price) ?? 0) + o.lots);
  }
```

回傳 map 加:

```ts
    myBuyFills: fillBuy.get(price) ?? 0,
    mySellFills: fillSell.get(price) ?? 0,
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/lib/flash-ladder.test.ts`
Expected: 全 PASS(含既有 6 個測試)。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/flash-ladder.ts frontend/src/lib/flash-ladder.test.ts
git commit -m "feat(capital): 閃電階梯聚合拆分——方格(活單)/黃括號(今日成交)純函式"
```

---

### Task 2: FlashPanel — 點擊語意改版 + 表頭置中鈕 + 跟隨修復

**Files:**
- Modify: `frontend/src/components/FlashPanel.tsx`

無新增單元測試(DOM 行為),靠既有測試不破 + Task 7 手動驗。

- [ ] **Step 1: 聚合來源改用 splitMyLots**

import 改:

```ts
import { buildLadder, splitMyLots } from "../lib/flash-ladder";
```

(`type MyOrderLot` 不再需要)。把現有 `myActionable`/`myLots` 兩個 useMemo(約 59-69 行)換成:

```tsx
  // 該檔今日全部單(含已完成 — 黃括號要整日留存);活單另濾給刪單用
  const myStockOrders = useMemo(() => orders.filter((o) => o.stock_no === selected), [orders, selected]);
  const myActionable = useMemo(() => myStockOrders.filter((o) => o.actionable), [myStockOrders]);
  const myLots = useMemo(() => splitMyLots(myStockOrders), [myStockOrders]);
```

`buildLadder` 呼叫(約 72-75 行)改:

```tsx
  const ladder = useMemo(
    () => (center != null ? buildLadder({ center, reference: refPrice, bids, asks, myOrders: myLots.active, myFills: myLots.fills }) : []),
    [center, refPrice, bids, asks, myLots],
  );
```

- [ ] **Step 2: 跟隨置中修復(程式捲動旗標)**

`centerRef` 宣告旁加:

```tsx
  const progScroll = useRef(false);
```

自動置中 effect(約 78-80 行)換成:

```tsx
  // 程式捲動旗標:scroll 事件在 rAF 前發,onScroll 看旗標跳過、rAF 清旗標
  // (已置中時 scrollIntoView 不發 scroll 事件,所以不能靠 onScroll 清)。
  // 不可改 smooth —— 多幀多次 scroll 事件會在旗標清掉後誤判成手動捲動。
  useEffect(() => {
    if (!followCenter || !centerRef.current) return;
    progScroll.current = true;
    centerRef.current.scrollIntoView({ block: "center" });
    requestAnimationFrame(() => { progScroll.current = false; });
  }, [ladder, followCenter]);
```

- [ ] **Step 3: 階梯區 JSX 改版**

把現有階梯區塊 —— 從 `{/* 階梯 */}` 的 `<div className="flex-1 min-h-0 overflow-y-auto ...">` 起、
到 `{!followCenter && (...◎ 回到現價...)}` 止(約 143-173 行)—— 整段換成:

```tsx
      {/* 表頭移出捲動容器 + 跟隨置中常駐鈕(表頭正下方) */}
      <div className="grid grid-cols-[1fr_72px_1fr] text-2xs text-ink-dim bg-bg-deep border border-b-0 border-line rounded-t flex-shrink-0">
        <span className="text-right pr-2 py-1">委買</span><span className="text-center py-1">價格</span><span className="pl-2 py-1">委賣</span>
      </div>
      <button onClick={() => setFollowCenter(true)}
        className={`text-2xs py-1 border-x border-line flex-shrink-0 ${followCenter ? "text-accent bg-accent/10" : "text-ink-dim bg-bg-deep"}`}>
        ◎ 跟隨置中:{followCenter ? "開" : "關(點我回中)"}
      </button>
      {/* 階梯:量區=加掛(連點繼續加)、紅方格=刪該價該向活單、黃括號=今日成交紀錄 */}
      <div className="flex-1 min-h-0 overflow-y-auto border border-line rounded-b bg-bg-card"
        onScroll={() => { if (progScroll.current) return; setFollowCenter(false); }}>
        {ladder.map((row) => (
          <div key={row.price} ref={row.isCenter ? centerRef : undefined}
            className={`grid grid-cols-[1fr_72px_1fr] h-[26px] text-xs border-b border-line/40 ${row.isCenter ? "bg-accent/10" : ""}`}>
            <div className="flex items-stretch">
              {/* 方格刪單不設 disabled:刪單是降風險操作,灰區/無券鎖買都不該擋 */}
              {row.myBuyLots > 0 && (
                <button onClick={() => cancelAt(row.price, "B")}
                  className="my-0.5 ml-0.5 px-1 min-w-[22px] text-2xs font-bold rounded border border-accent bg-accent/25 text-accent">
                  {row.myBuyLots}
                </button>
              )}
              <button disabled={!row.clickable || tradeKind === "daytrade_sell"}
                onClick={() => clickPrice(row.price, "buy", row.clickable)}
                className={`flex-1 flex items-center justify-end gap-1 pr-2 tabular-nums ${row.clickable && tradeKind !== "daytrade_sell" ? "text-bull hover:bg-bull/10 cursor-pointer" : "text-ink-dim/40"}`}>
                {row.myBuyFills > 0 && <span className="text-2xs text-ma5">({row.myBuyFills})</span>}
                {row.buyVol ?? ""}
              </button>
            </div>
            <span className={`flex items-center justify-center tabular-nums border-x border-line ${row.isCenter ? "text-accent font-bold" : "text-ink-muted"}`}>
              {row.price.toFixed(2)}{row.isCenter ? " ◄" : ""}
            </span>
            <div className="flex items-stretch">
              <button disabled={!row.clickable}
                onClick={() => clickPrice(row.price, "sell", row.clickable)}
                className={`flex-1 flex items-center justify-start gap-1 pl-2 tabular-nums ${row.clickable ? "text-bear hover:bg-bear/10 cursor-pointer" : "text-ink-dim/40"}`}>
                {row.sellVol ?? ""}
                {row.mySellFills > 0 && <span className="text-2xs text-ma5">({row.mySellFills})</span>}
              </button>
              {row.mySellLots > 0 && (
                <button onClick={() => cancelAt(row.price, "S")}
                  className="my-0.5 mr-0.5 px-1 min-w-[22px] text-2xs font-bold rounded border border-accent bg-accent/25 text-accent">
                  {row.mySellLots}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
```

重點差異(reviewer 用):
- 量區 onClick 不再有 `row.myBuyLots > 0 ? cancelAt(...) : clickPrice(...)` 三元 — 永遠 `clickPrice`(連點=加掛,同格 500ms 防抖在 `clickPrice` 內保留)
- 刪單獨立成紅方格按鈕,**不**受 `clickable`/`tradeKind` disabled(順手修了舊版「價格漂出 ±5% 後刪不到單」的問題)
- 黃括號 `({fills})` 用 `text-ma5`(琥珀黃,theme 既有),purely display
- 表頭從捲動容器內的 sticky 移出來,置中鈕常駐其下

- [ ] **Step 4: 編譯 + 既有測試**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 編譯無錯、測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FlashPanel.tsx
git commit -m "feat(capital): 閃電點擊語意改版——量區連點加掛/紅方格刪單/黃括號成交+跟隨置中修復"
```

---

### Task 3: FlashPanel 頂部庫存顯示(posQty → pos)

**Files:**
- Modify: `frontend/src/components/FlashPanel.tsx`
- Modify: `frontend/src/components/TradingPanel.tsx:44`

- [ ] **Step 1: Props 換型別 + 頂部/底部顯示**

`FlashPanel.tsx` import 加 `type CapitalPosition`:

```ts
import { api, type CapitalOrder, type CapitalPosition } from "../lib/api";
```

Props 介面把 `posQty: number | null;` 換成:

```ts
  pos: CapitalPosition | null;  // 該標的庫存(無部位或庫存未載入=null)
```

函式簽名 `{ selected, ready, env, orders, posQty }` → `{ selected, ready, env, orders, pos }`。

頂部標的列(`{selected}` 那個 span)改:

```tsx
        <span className="text-sm font-bold">{selected}
          {pos && <span className="text-2xs text-ink-dim font-normal ml-2">庫存 {pos.qty} 張 · 均 {pos.avg_price.toFixed(2)}</span>}
        </span>
```

底部狀態列 `{posQty != null ? ` · 部位 ${posQty > 0 ? "+" : ""}${posQty} 張` : ""}` 改:

```tsx
        <span>掛單 {myActionable.length} 筆{pos ? ` · 部位 ${pos.qty > 0 ? "+" : ""}${pos.qty} 張` : ""}</span>
```

- [ ] **Step 2: TradingPanel 接線**

`TradingPanel.tsx` 44 行 `posQty={pos?.qty ?? null}` → `pos={pos}`。

- [ ] **Step 3: 編譯 + 測試**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 全綠。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/FlashPanel.tsx frontend/src/components/TradingPanel.tsx
git commit -m "feat(capital): 閃電頂部顯示庫存張數+均價"
```

---

### Task 4: PositionsList 損益凍結修復(tick/快照分層)

**Files:**
- Modify: `frontend/src/lib/capital-pnl.ts`
- Modify: `frontend/src/components/PositionsList.tsx`
- Test: `frontend/src/lib/capital-pnl.test.ts`

- [ ] **Step 1: 寫失敗測試**

`capital-pnl.test.ts` 追加(import 加 `snapshotPrices`):

```ts
describe("snapshotPrices 快照價全量重建", () => {
  it("每輪回傳全新 map(凍結 bug 回歸:舊值不得殘留)、null 略過", () => {
    // 舊 bug:「已有值就不蓋」合併讓第一輪快照價永久凍結,損益不動
    const r1 = snapshotPrices([{ symbol: "2330", last_price: 100 }, { symbol: "3357", last_price: null }]);
    expect(r1).toEqual({ "2330": 100 });
    const r2 = snapshotPrices([{ symbol: "2330", last_price: 101.5 }]);
    expect(r2).toEqual({ "2330": 101.5 });
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/capital-pnl.test.ts`
Expected: FAIL — `snapshotPrices` 不存在。

- [ ] **Step 3: 實作純函式**

`capital-pnl.ts` 末尾加:

```ts
/** 快照價每輪全量重建(不吃前值 — 簽名就杜絕「已有值就不蓋」的凍結 bug)。
 *  tick 價另存一層,顯示時 tick 優先。 */
export function snapshotPrices(rows: Array<{ symbol: string; last_price: number | null }>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) if (r.last_price != null) out[r.symbol] = r.last_price;
  return out;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/lib/capital-pnl.test.ts`
Expected: PASS。

- [ ] **Step 5: PositionsList 改雙層價格**

`PositionsList.tsx`:

import 加 `snapshotPrices`:

```ts
import { grossPnl, snapshotPrices } from "../lib/capital-pnl";
```

`const [live, setLive] = useState<Record<string, number>>({});` 換成:

```tsx
  const [tick, setTick] = useState<Record<string, number>>({});   // WS 即時價(有訂閱的標的)
  const [snap, setSnap] = useState<Record<string, number>>({});   // 30s 快照價(每輪全量覆寫)
```

WS tick effect 裡 `setLive(...)` → `setTick(...)`(邏輯不變)。

快照 effect 裡的 `setLive((m) => {...})` 整段換成:

```tsx
        setSnap(snapshotPrices(r.quotes));
```

顯示層:component 內加一個 helper、三處 `live[...]` 換掉:

```tsx
  const priceOf = (s: string) => tick[s] ?? snap[s] ?? null;
```

- `total` reduce:`grossPnl(p.qty, p.avg_price, priceOf(p.stock_no))`
- 列內:`const cur = priceOf(p.stock_no);`
- ClosePositionDialog:`cur={priceOf(closing.stock_no)}`

- [ ] **Step 6: 編譯 + 全測試**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 全綠。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/capital-pnl.ts frontend/src/lib/capital-pnl.test.ts frontend/src/components/PositionsList.tsx
git commit -m "fix(capital): 庫存損益凍結——快照價全量重建+tick優先雙層合併"
```

---

### Task 5: 後端 — 回報日期欄位(idx23)+ 委託排序帶日期

**Files:**
- Modify: `backend/services/capital_reply.py`
- Modify: `backend/services/capital_store.py`
- Modify: `backend/services/capital_models.py`(OrderRecord)
- Test: `backend/tests/test_capital_reply.py`、`backend/tests/test_capital_store.py`

- [ ] **Step 1: 寫失敗測試**

`test_capital_reply.py` 追加(用既有 `RAW_N_PREORDER` fixture):

```python
def test_parse_date_field():
    """idx23=委託建立日 — 排序鍵用,昨日預約單才不會壓在今日單上面。"""
    r = parse_onnewdata(RAW_N_PREORDER)
    assert r.date == "20260610"
```

`test_capital_store.py` 的 `_evt` helper 簽名加 `date="20260610"`,
`arr[23], arr[24] = "20260610", time` 改 `arr[23], arr[24] = date, time`,並追加:

```python
def test_orders_sorted_by_date_then_time():
    """昨日收盤後掛的預約單(時間 14:59)不得壓在今日早盤單(09:05)上面。"""
    s = CapitalStore()
    s.apply_reply(_evt(seq=SEQ_A, date="20260610", time="14:59:48", pre="B"))
    s.apply_reply(_evt(seq=SEQ_B, date="20260611", time="09:05:01"))
    assert [o.seq_no for o in s.orders()] == [SEQ_B, SEQ_A]
    assert s.orders()[1].date == "20260610"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_capital_reply.py tests/test_capital_store.py -v`
Expected: 兩個新測試 FAIL(`date` 欄位不存在);既有測試 PASS。

- [ ] **Step 3: 實作**

(a) `capital_reply.py` — `ReplyRecord` 在 `time` 欄位前加:

```python
    date: str | None = None          # idx23 YYYYMMDD(委託建立日;C/D 事件實測仍為原單日期)
```

`parse_onnewdata` 回傳在 `time=_at(arr, 24),` 前加:

```python
        date=_at(arr, 23),
```

(b) `capital_store.py` — `_Agg` 在 `time` 前加 `date: str | None = None`;
`apply_reply` 共通欄位 tuple 加 `"date"`:

```python
            for f in ("stock_no", "market", "buy_sell", "flag_label", "book_no", "date"):
```

`orders()` 排序鍵改(docstring 同步改為「日期+時間倒序」):

```python
            aggs = sorted(self._orders.values(),
                          key=lambda a: (a.date or "", a.time or "", arrival[a.seq_no]), reverse=True)
```

`_to_record` 回傳加 `date=a.date,`(放 `time=a.time,` 旁)。

(c) `capital_models.py` — `OrderRecord` 在 `time` 欄位前加:

```python
    date: str | None = None           # 委託建立日 YYYYMMDD(排序/前端跨日顯示用)
```

route 是 `model_dump()` 全欄位下發,不用改。

- [ ] **Step 4: 跑全後端測試**

Run: `cd backend && python -m pytest -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/capital_reply.py backend/services/capital_store.py backend/services/capital_models.py backend/tests/test_capital_reply.py backend/tests/test_capital_store.py
git commit -m "feat(capital): 回報解析idx23日期+委託排序帶日期(昨日預約單不浮頂)"
```

---

### Task 6: 前端委託列 — 非今日單顯示日期

**Files:**
- Modify: `frontend/src/lib/capital-orders.ts`
- Modify: `frontend/src/components/OrdersList.tsx`
- Test: `frontend/src/lib/capital-orders.test.ts`

- [ ] **Step 1: 寫失敗測試**

`capital-orders.test.ts`:base fixture 加 `date: "20260610",`(放 `time` 旁),
import 加 `localYmd`,追加:

```ts
describe("跨日顯示", () => {
  it("非今日的單時間前帶日期;今日單不帶(昨日預約單混在今日清單的辨識)", () => {
    expect(buildOrderRow({ ...base, date: "20260610" }, "20260611").timeText).toBe("06/10 14:59:48");
    expect(buildOrderRow({ ...base, date: "20260611" }, "20260611").timeText).toBe("14:59:48");
  });

  it("todayYmd 未傳(舊呼叫)或 date 缺值 → 行為不變", () => {
    expect(buildOrderRow(base).timeText).toBe("14:59:48");
    expect(buildOrderRow({ ...base, date: null }, "20260611").timeText).toBe("14:59:48");
  });

  it("localYmd 本地時區 YYYYMMDD", () => {
    expect(localYmd(new Date(2026, 5, 11))).toBe("20260611");  // 月是 0-based
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/capital-orders.test.ts`
Expected: FAIL — `date` 欄位 / `localYmd` 不存在。

- [ ] **Step 3: 實作**

`capital-orders.ts`:

(a) `CapitalOrder` 介面 `time: string | null;` 旁加 `date: string | null;`

(b) `buildOrderRow` 簽名加第二參數,`timeText` 改:

```ts
export function buildOrderRow(o: CapitalOrder, todayYmd?: string): OrderRowVM {
```

```ts
  const crossDay = o.date && todayYmd && o.date !== todayYmd;
  // ...
    timeText: o.time && crossDay ? `${o.date!.slice(4, 6)}/${o.date!.slice(6, 8)} ${o.time}` : o.time,
```

(c) 檔尾加:

```ts
/** 本地時區 YYYYMMDD(回報日期同為台股交易日曆,直接比對)。 */
export function localYmd(d: Date): string {
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}
```

(d) `OrdersList.tsx`:import 加 `localYmd`,component 內:

```tsx
  const todayYmd = localYmd(new Date());
```

`buildOrderRow(o)` → `buildOrderRow(o, todayYmd)`。

- [ ] **Step 4: 編譯 + 全前端測試**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 全綠。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/capital-orders.ts frontend/src/lib/capital-orders.test.ts frontend/src/components/OrdersList.tsx
git commit -m "feat(capital): 委託列非今日單帶日期顯示"
```

---

### Task 7: 全套驗證 + 手動驗收

**Files:** 無新改動(驗證 task)

- [ ] **Step 1: 全測試 + build**

```bash
cd frontend && npx vitest run && npm run build
cd ../backend && python -m pytest -q
```

Expected: 全綠、build 成功。任何紅都回對應 task 修,不得跳過。

- [ ] **Step 2: 手動驗收(dev server,可在收盤後做版面部分)**

`.\start.ps1` 後開閃電分頁,核對 spec 驗收標準:

1. 表頭「委買 價格 委賣」下方有常駐「◎ 跟隨置中:開」;手動捲動 → 變「關」;點它 → 回中且狀態回「開」
2. 跟隨開啟時現價變動(盤中)階梯持續置中,**不會自己跳回「關」**(舊 bug 回歸重點)
3. 未武裝點價 → 只提示不送單(行為不變);武裝後點量區同價連點兩下(間隔 >0.5s)→ 掛出兩筆;紅方格出現並顯示未成交張數;點方格 → 該價該向全刪;有成交後黃括號出現且刪單後仍在
4. 頂部顯示「庫存 N 張 · 均 X」(庫存欄位校準前數字可能不對 — 看顯示機制有動就好)
5. 委託分頁:昨日預約單帶 `06/10` 前綴且排在今日單下面
6. 庫存分頁:現價/損益 30 秒內有跳動(非訂閱標的也要動 — D1 回歸重點)

- [ ] **Step 3: 提醒使用者(spec D2 盤中校準,本計畫不含)**

完成回報時明確告知:庫存張數/均價在欄位校準前不可信;請擇一盤中時段(當日最好有買賣)跑
`backend/scripts/capital_smoke.py --balance` 把原始字串貼回來,校準 `capital_balance.py`
的 `_IDX_*` 假設表並補真實樣本測試。

---

## Self-Review 紀錄

- **Spec 覆蓋**:A=Task 1+2、B=Task 2、C=Task 3、D1=Task 4、D2=Task 7 Step 3(流程性,不可程式化)、E=Task 5+6、「不做」清單無對應 task ✓
- **佔位符**:無 TBD/「適當處理」;每個程式步驟都有完整碼 ✓
- **型別一致**:`MyOrderSource` 結構子集 = `CapitalOrder` 欄位名(price/buy_sell/order_qty/filled_qty/actionable)✓;`myLots.active`/`myLots.fills` 在 Task 1 定義、Task 2 使用 ✓;`date` 欄位鏈 reply→store→OrderRecord→CapitalOrder(Task 5→6)✓
