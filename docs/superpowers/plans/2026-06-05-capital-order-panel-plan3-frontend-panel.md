# 群益下單面板 Plan 3 — 前端:移除明細 + TradingPanel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 或 superpowers:executing-plans 逐 task 實作。Steps 用 checkbox(`- [ ]`)。

**Goal:** 移除明細欄,在 Monitor 最右側新增 `TradingPanel`(下單 / 委託 兩個 tab)。下單匣可由五檔點價帶入、用 `selected` 當標的;委託 tab 顯示群益回報;部位卡用富邦 tick 即時算毛/淨損益;頂部群益連線健康燈。

**Architecture:** 沿用既有慣例 —— REST 走 `lib/api.ts` 的 `fetchJSON`(自動帶 `X-API-Key`);WS 走既有 `/ws/realtime`,在 `useSignalsStream` 加 `capital_order` 事件 → `capitalOrderBus`;五檔→下單匣帶價用新的 `orderTicketBus`(同 `tickBus` 模式);部位即時損益重用 `subscribeTicks`。

**Tech Stack:** React + TypeScript + Tailwind(既有 `text-bull/text-bear/bg-bg-card/border-line` 等 token)、vitest。

**前置:**
- Plan 2 後端已可用(`/api/capital/*` + `capital_order` WS 事件)。
- 若在 worktree 執行,frontend 需先 `npm install`(node_modules 不在 git);或在已 install 的 checkout 執行。
- 主題色與欄位以 mockup 為準:`.superpowers/brainstorm/391-1780585522/content/`(trading-panel-tabs / monitor-v2-no-tape)。買進/賺=紅 `text-bull`、賣出/賠=綠 `text-bear`。

**既有座標(實查):**
- `Monitor.tsx`:grid 容器 line 177-180 `gridTemplateColumns: "300px 460px 1fr 300px"`;明細 col4 = line 249-259;`selected` = useState line 43。
- `QuoteBook.tsx`:買檔列 62-73、賣檔列 80-91,**目前無 onClick**。
- `lib/api.ts`:`fetchJSON<T>(path, init)` 自動帶 `X-API-Key`;`api` 物件集中各端點。
- `useSignalsStream.ts`:WS `/ws/realtime`;`msg.event` 分派;`tickBus` + `subscribeTicks` 模式(line 16-27)。
- Dialog 慣例:`BookmarkNewDialog.tsx`(backdrop z-20 + modal z-21 + Escape + footer)。

---

## Task 1: `lib/api.ts` 加群益端點 + 型別

**Files:**
- Modify: `frontend/src/lib/api.ts`(加型別 + `api.capital*`)
- Test: `frontend/src/lib/capital-pnl.ts` + `frontend/src/lib/capital-pnl.test.ts`(本 task 先做純損益 helper 的 TDD)

- [ ] **Step 1: 寫失敗測試**(淨損益計算意圖:毛額 − 進出手續費 − 證交稅)

```ts
// frontend/src/lib/capital-pnl.test.ts
import { describe, it, expect } from "vitest";
import { grossPnl, netPnl } from "./capital-pnl";

describe("capital-pnl", () => {
  it("gross = qty*1000*(price-avg)", () => {
    expect(grossPnl(5, 575, 590)).toBe(75000);
  });
  it("short position gross", () => {
    expect(grossPnl(-2, 100, 95)).toBe(10000);
  });
  it("net subtracts entry+exit fee and sell tax", () => {
    // qty5 avg575 cur590 feeRate0.001425 taxRate0.003
    // gross=75000; entryFee=round(5*1000*575*0.001425)=4097
    // exitFee=round(5*1000*590*0.001425)=4204; tax=round(5*1000*590*0.003)=8850
    // net = 75000-4097-4204-8850 = 57849
    expect(netPnl(5, 575, 590, 0.001425, 0.003)).toBe(57849);
  });
  it("null price -> 0", () => {
    expect(grossPnl(5, 575, null)).toBe(0);
    expect(netPnl(5, 575, null, 0.001425, 0.003)).toBe(0);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/capital-pnl.test.ts`
Expected: FAIL(找不到 `./capital-pnl`)

- [ ] **Step 3: 寫 helper**

```ts
// frontend/src/lib/capital-pnl.ts
/** 未實現損益。qty 為張(放空為負)。 */
export function grossPnl(qty: number, avgPrice: number, currentPrice: number | null): number {
  if (currentPrice == null) return 0;
  return qty * 1000 * (currentPrice - avgPrice);
}

/** 淨損益 = 毛 − 進場手續費 − 出場手續費 − 證交稅(出場)。 */
export function netPnl(
  qty: number, avgPrice: number, currentPrice: number | null,
  feeRate: number, taxRate: number,
): number {
  if (currentPrice == null) return 0;
  const shares = Math.abs(qty) * 1000;
  const entryFee = Math.round(shares * avgPrice * feeRate);
  const exitFee = Math.round(shares * currentPrice * feeRate);
  const tax = Math.round(shares * currentPrice * taxRate);
  return grossPnl(qty, avgPrice, currentPrice) - entryFee - exitFee - tax;
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/lib/capital-pnl.test.ts`
Expected: PASS(4 passed)

- [ ] **Step 5: 在 `lib/api.ts` 加群益型別 + 端點**

在 `api` 物件內(其他方法旁)加:
```ts
  capitalStatus: () =>
    fetchJSON<{ status: string; last_error?: string | null }>("/api/capital/status"),
  capitalOrders: () =>
    fetchJSON<{ orders: CapitalOrder[] }>("/api/capital/orders"),
  capitalPositions: () =>
    fetchJSON<{ positions: CapitalPosition[] }>("/api/capital/positions"),
  capitalSubmitStock: (req: CapitalStockOrderReq) =>
    fetchJSON<CapitalOrderResult>("/api/capital/order/stock", {
      method: "POST",
      body: JSON.stringify(req),
    }),
```
並在檔案上方(型別區)加:
```ts
export interface CapitalOrder {
  seq_no: string; stock_no: string | null; book_no: string | null;
  status_raw: string | null; status_label: string | null;
  price: number | null; qty: number;
}
export interface CapitalPosition {
  stock_no: string; name: string; qty: number; avg_price: number;
}
export interface CapitalStockOrderReq {
  stock_no: string; buy_sell: "buy" | "sell"; price: number; qty: number;
  price_type?: "limit" | "market"; time_in_force?: "ROD" | "IOC" | "FOK";
  trade_kind?: "cash" | "margin" | "short";
}
export interface CapitalOrderResult {
  ok: boolean; code: number; message: string; seq_no: string | null;
}
```

- [ ] **Step 6: 型別檢查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無錯誤

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/capital-pnl.ts frontend/src/lib/capital-pnl.test.ts
git commit -m "feat(capital-fe): api 端點/型別 + 損益計算 helper(毛/淨)+測試"
```

---

## Task 2: WS `capital_order` 事件 + `orderTicketBus`

**Files:**
- Modify: `frontend/src/hooks/useSignalsStream.ts`

- [ ] **Step 1: 加 capital_order 分派 + 兩條 bus**

在 `tickBus` 宣告附近(line 16-27 同層)加:
```ts
const capitalOrderBus = new EventTarget();
export function subscribeCapitalOrders(handler: () => void): () => void {
  const fn = () => handler();
  capitalOrderBus.addEventListener("capital_order", fn);
  return () => capitalOrderBus.removeEventListener("capital_order", fn);
}

// 五檔點價 → 下單匣帶價
export interface OrderTicketHint { symbol: string | null; price: number; }
const orderTicketBus = new EventTarget();
export function emitOrderTicket(hint: OrderTicketHint): void {
  orderTicketBus.dispatchEvent(new CustomEvent<OrderTicketHint>("ticket", { detail: hint }));
}
export function subscribeOrderTicket(handler: (h: OrderTicketHint) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<OrderTicketHint>).detail);
  orderTicketBus.addEventListener("ticket", fn);
  return () => orderTicketBus.removeEventListener("ticket", fn);
}
```

在 `onmessage` 的 `else if (msg.event === "mxf_candle")` 之後加:
```ts
        } else if (msg.event === "capital_order") {
          capitalOrderBus.dispatchEvent(new Event("capital_order"));
```

- [ ] **Step 2: 型別檢查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無錯誤

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSignalsStream.ts
git commit -m "feat(capital-fe): WS capital_order 事件 + orderTicketBus(五檔帶價)"
```

---

## Task 3: 群益資料 hooks

**Files:**
- Create: `frontend/src/hooks/useCapital.ts`

- [ ] **Step 1: 寫 hooks**(status / orders / positions;委託在收到 `capital_order` WS 時 refetch)

```ts
// frontend/src/hooks/useCapital.ts
import { useEffect, useState } from "react";
import { api, type CapitalOrder, type CapitalPosition } from "../lib/api";
import { subscribeCapitalOrders } from "./useSignalsStream";

export function useCapitalStatus(pollMs = 10000) {
  const [status, setStatus] = useState<string>("disabled");
  const [lastError, setLastError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.capitalStatus();
        if (!alive) return;
        setStatus(r.status);
        setLastError(r.last_error ?? null);
      } catch { /* keep */ }
    };
    tick();
    const id = setInterval(tick, pollMs);
    return () => { alive = false; clearInterval(id); };
  }, [pollMs]);
  return { status, lastError };
}

export function useCapitalOrders() {
  const [orders, setOrders] = useState<CapitalOrder[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try { const r = await api.capitalOrders(); if (alive) setOrders(r.orders); }
      catch { /* keep */ }
    };
    load();
    const unsub = subscribeCapitalOrders(load);   // 回報一來就刷新
    return () => { alive = false; unsub(); };
  }, []);
  return orders;
}

export function useCapitalPositions(pollMs = 15000) {
  const [positions, setPositions] = useState<CapitalPosition[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try { const r = await api.capitalPositions(); if (alive) setPositions(r.positions); }
      catch { /* keep */ }
    };
    load();
    const id = setInterval(load, pollMs);
    const unsub = subscribeCapitalOrders(load);
    return () => { alive = false; clearInterval(id); unsub(); };
  }, [pollMs]);
  return positions;
}
```

- [ ] **Step 2: 型別檢查 → Commit**

Run: `cd frontend && npx tsc --noEmit`(無錯)
```bash
git add frontend/src/hooks/useCapital.ts
git commit -m "feat(capital-fe): useCapitalStatus/Orders/Positions hooks"
```

---

## Task 4: QuoteBook 五檔點價 → 帶入下單匣

**Files:**
- Modify: `frontend/src/components/QuoteBook.tsx`

> QuoteBook 需知道目前 symbol 才能帶。確認 props 是否已有 `symbol`;若無,從 Monitor 傳入 `selected`(見 Task 5)。

- [ ] **Step 1: 買檔列(line 62-73)外層 `<div>` 加 onClick**

把買檔 `.map` 的外層 `<div className="relative grid grid-cols-2 ...">` 改為加:
```tsx
    onClick={() => b.price > 0 && emitOrderTicket({ symbol, price: b.price })}
    className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums cursor-pointer hover:bg-bg-card/40"
```
賣檔列(line 80-91)同樣:
```tsx
    onClick={() => a.price > 0 && emitOrderTicket({ symbol, price: a.price })}
    className="relative grid grid-cols-2 gap-2.5 px-2 py-1.5 border-b border-line text-sm tabular-nums cursor-pointer hover:bg-bg-card/40"
```
檔頭 import:`import { emitOrderTicket } from "../hooks/useSignalsStream";`
確認 component props 有 `symbol: string | null`(若無則加入 props 並由 Monitor 傳 `selected`)。

- [ ] **Step 2: 型別檢查 → Commit**

Run: `cd frontend && npx tsc --noEmit`(無錯)
```bash
git add frontend/src/components/QuoteBook.tsx
git commit -m "feat(capital-fe): 五檔點價 → emitOrderTicket 帶入下單匣"
```

---

## Task 5: 移除明細 + Monitor 接 TradingPanel

**Files:**
- Delete: `frontend/src/components/TradeTape.tsx`
- Delete: `frontend/src/hooks/useTradeTape.ts`
- Modify: `frontend/src/pages/Monitor.tsx`

- [ ] **Step 1: 刪除明細兩檔**

```bash
git rm frontend/src/components/TradeTape.tsx frontend/src/hooks/useTradeTape.ts
```

- [ ] **Step 2: `Monitor.tsx` 改 grid + 換掉 col4**

(a) grid 容器(line 177-180)寬度改為下單面板:
```tsx
  style={{ gridTemplateColumns: "300px 460px 1fr 380px" }}
```
(b) 把 col4 明細整段(line 249-259)整段換成:
```tsx
        {/* COL 4: 下單面板(群益) */}
        <TradingPanel selected={selected} />
```
(c) 檔頭把 `import { TradeTape } from "../components/TradeTape";` 改為
```tsx
import { TradingPanel } from "../components/TradingPanel";
```
(d) 確認 QuoteBook 有收到 symbol:在 col3 的 `<QuoteBook ... />` 補 `symbol={selected}`(若 Task 4 需要)。

- [ ] **Step 3: 型別檢查**(此時 TradingPanel 尚未建會報錯 —— 先建 Task 6 再驗;本 step 暫略,Task 6 一起驗)

- [ ] **Step 4: Commit**(連同 Task 6 一起 commit;見 Task 6 Step 5)

---

## Task 6: TradingPanel 元件(下單 / 委託 tab)

**Files:**
- Create: `frontend/src/components/TradingPanel.tsx`
- Create: `frontend/src/components/OrderConfirmDialog.tsx`

- [ ] **Step 1: 二次確認 dialog**(沿用 `BookmarkNewDialog` modal 慣例)

```tsx
// frontend/src/components/OrderConfirmDialog.tsx
import { useEffect } from "react";
import type { CapitalStockOrderReq } from "../lib/api";

interface Props {
  req: CapitalStockOrderReq;
  env: string;                 // "test" | "prod"
  estAmount: number;
  onConfirm: () => void;
  onClose: () => void;
}

export function OrderConfirmDialog({ req, env, estAmount, onConfirm, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isBuy = req.buy_sell === "buy";
  const prod = env === "prod";
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-20 bg-bg-deep/85" style={{ backdropFilter: "blur(2px)" }} />
      <div role="dialog" aria-modal="true"
        className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border p-6 w-[min(380px,90vw)] ${prod ? "border-bull" : "border-line-strong"}`}
        style={{ transform: "translate(-50%, -50%)" }}>
        <h3 className="font-serif font-bold text-xl mb-1">確認送出委託</h3>
        <p className={`text-xs mb-4 ${prod ? "text-bull font-bold" : "text-bear"}`}>
          {prod ? "⚠ 正式環境(真錢)" : "測試環境"}
        </p>
        <div className="h-px bg-line mb-4" />
        <div className="space-y-1.5 text-sm tabular-nums">
          <Row k="標的" v={req.stock_no} />
          <Row k="買賣別" v={<span className={isBuy ? "text-bull" : "text-bear"}>{isBuy ? "買進" : "賣出"}</span>} />
          <Row k="委託價" v={req.price_type === "market" ? "市價" : req.price.toFixed(2)} />
          <Row k="數量" v={`${req.qty} 張`} />
          <Row k="預估金額" v={`NT$ ${estAmount.toLocaleString()}`} />
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm border border-line-strong text-ink-muted hover:text-ink">取消</button>
          <button onClick={onConfirm}
            className={`px-4 py-2 text-sm text-bg font-medium ${isBuy ? "bg-bull" : "bg-bear"}`}>
            確認{isBuy ? "買進" : "賣出"}
          </button>
        </div>
      </div>
    </>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between"><span className="text-ink-dim">{k}</span><span>{v}</span></div>;
}
```

- [ ] **Step 2: TradingPanel(下單匣 + 委託 tab + 部位卡 + 健康燈)**

```tsx
// frontend/src/components/TradingPanel.tsx
import { useEffect, useState } from "react";
import { api, type CapitalStockOrderReq } from "../lib/api";
import { useCapitalStatus, useCapitalOrders, useCapitalPositions } from "../hooks/useCapital";
import { subscribeOrderTicket, subscribeTicks } from "../hooks/useSignalsStream";
import { grossPnl, netPnl } from "../lib/capital-pnl";
import { OrderConfirmDialog } from "./OrderConfirmDialog";

const ENV = (import.meta.env.VITE_CAPITAL_ENV ?? "test") as string;
const FEE = Number(import.meta.env.VITE_CAPITAL_FEE_RATE ?? "0.001425");
const TAX = Number(import.meta.env.VITE_CAPITAL_TAX_RATE ?? "0.003");

export function TradingPanel({ selected }: { selected: string | null }) {
  const { status } = useCapitalStatus();
  const orders = useCapitalOrders();
  const positions = useCapitalPositions();
  const [tab, setTab] = useState<"order" | "list">("order");

  const [buySell, setBuySell] = useState<"buy" | "sell">("buy");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("1");
  const [confirm, setConfirm] = useState<CapitalStockOrderReq | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // 五檔點價 → 帶入委託價
  useEffect(() => subscribeOrderTicket((h) => {
    if (!selected || h.symbol === selected || h.symbol == null) setPrice(h.price.toFixed(2));
  }), [selected]);

  const ready = status === "ok";
  const pos = positions.find((p) => p.stock_no === selected) ?? null;

  const submit = () => {
    if (!selected) return;
    setConfirm({
      stock_no: selected, buy_sell: buySell,
      price: Number(price) || 0, qty: Number(qty) || 0,
    });
  };
  const doSend = async () => {
    if (!confirm) return;
    try {
      const r = await api.capitalSubmitStock(confirm);
      setMsg(`${r.ok ? "✓" : "✗"} ${r.message}`);
    } catch (e) { setMsg("✗ 送單失敗"); }
    setConfirm(null);
  };

  const estAmount = (Number(price) || 0) * (Number(qty) || 0) * 1000;
  const isBuy = buySell === "buy";

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

      {/* tabs */}
      <div className="flex border-b border-line-strong mb-3 flex-shrink-0 text-sm">
        <button onClick={() => setTab("order")} className={`flex-1 py-2 ${tab === "order" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>下單</button>
        <button onClick={() => setTab("list")} className={`flex-1 py-2 ${tab === "list" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>委託 {orders.length > 0 && <span className="text-accent">{orders.length}</span>}</button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto pr-1 scroll-editorial">
        {tab === "order" ? (
          <>
            <div className="label-tiny mb-1">標的</div>
            <div className="bg-bg-deep border border-line px-3 py-2 text-sm tabular-nums mb-3">{selected ?? "—"}</div>

            <div className="flex gap-2 mb-3">
              <button onClick={() => setBuySell("buy")} className={`flex-1 py-2.5 font-bold rounded ${isBuy ? "bg-bull text-bg" : "border border-line text-ink-dim"}`}>買進</button>
              <button onClick={() => setBuySell("sell")} className={`flex-1 py-2.5 font-bold rounded ${!isBuy ? "bg-bear text-bg" : "border border-line text-ink-dim"}`}>賣出</button>
            </div>

            <div className="flex gap-2 mb-3">
              <div className="flex-1"><div className="label-tiny mb-1">委託價</div>
                <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal"
                  className="w-full bg-bg-deep border border-line px-3 py-2 text-sm tabular-nums outline-none focus:border-accent" /></div>
              <div className="flex-1"><div className="label-tiny mb-1">數量(張)</div>
                <input value={qty} onChange={(e) => setQty(e.target.value)} inputMode="numeric"
                  className="w-full bg-bg-deep border border-line px-3 py-2 text-sm tabular-nums outline-none focus:border-accent" /></div>
            </div>

            <button onClick={submit} disabled={!ready || !selected}
              className={`w-full py-2.5 font-bold rounded text-bg disabled:opacity-40 ${isBuy ? "bg-bull" : "bg-bear"}`}>
              {isBuy ? "買進" : "賣出"} 送出
            </button>
            <div className="text-center text-2xs text-ink-dim mt-1.5">送出前會二次確認</div>
            {msg && <div className="text-center text-xs mt-2 text-ink-muted">{msg}</div>}

            {/* 目前標的部位卡 */}
            <PositionCard symbol={selected} pos={pos} />
          </>
        ) : (
          <OrdersList orders={orders} />
        )}
      </div>

      {confirm && (
        <OrderConfirmDialog req={confirm} env={ENV} estAmount={estAmount}
          onConfirm={doSend} onClose={() => setConfirm(null)} />
      )}
    </section>
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

function OrdersList({ orders }: { orders: { seq_no: string; stock_no: string | null; status_label: string | null; price: number | null; qty: number }[] }) {
  if (orders.length === 0) return <div className="text-xs text-ink-dim py-4 text-center">今日尚無委託</div>;
  return (
    <div className="space-y-0">
      {orders.map((o) => (
        <div key={o.seq_no} className="border-b border-line py-2.5 text-sm">
          <div className="flex items-center gap-2">
            <span className="font-serif font-medium">{o.stock_no ?? "—"}</span>
            <span className="ml-auto text-xs px-2 py-0.5 rounded bg-bg-deep text-ink-muted">{o.status_label ?? "—"}</span>
          </div>
          <div className="text-xs text-ink-dim tabular-nums mt-1">
            {o.price != null ? o.price.toFixed(2) : "—"} · {o.qty} 張 · #{o.seq_no}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: 型別檢查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無錯誤(Monitor 已引用 TradingPanel、QuoteBook 已收 symbol)

- [ ] **Step 4: build 驗證**

Run: `cd frontend && npm run build`
Expected: build 成功(無型別/編譯錯)

- [ ] **Step 5: Commit**(含 Task 5 的 Monitor/刪檔)

```bash
git add frontend/src/components/TradingPanel.tsx frontend/src/components/OrderConfirmDialog.tsx frontend/src/pages/Monitor.tsx frontend/src/components/QuoteBook.tsx
git rm frontend/src/components/TradeTape.tsx frontend/src/hooks/useTradeTape.ts 2>/dev/null || true
git commit -m "feat(capital-fe): 移除明細 + TradingPanel(下單/委託/部位/健康燈)"
```

---

## Task 7: 全前端驗證 + 視覺檢查

- [ ] **Step 1: 型別 + build + 既有測試**

Run: `cd frontend && npx tsc --noEmit && npm run build && npx vitest run`
Expected: 全綠

- [ ] **Step 2: 視覺檢查(手動,需後端起 + 群益 ok 或 disabled)**

`.\start.ps1` 後開前端:
- 最右側出現 TradingPanel,明細不見了,版面不擠(grid 380px)。
- 點五檔某價 → 委託價自動帶入。
- 健康燈:群益未設定時顯示 disabled、未就緒時不可送出。
- 切「委託」tab 顯示今日委託(送一筆測試單後出現)。
> 對照 mockup:`.superpowers/brainstorm/391-1780585522/content/trading-panel-tabs.html`、`monitor-v2-no-tape.html`。

- [ ] **Step 3: Commit**(若有微調)

```bash
git add -A && git commit -m "chore(capital-fe): v1 前端面板驗證微調"
```

---

## 完成準則(Plan 3)
- `npx tsc --noEmit` + `npm run build` + `npx vitest run` 全綠。
- 明細移除、TradingPanel 進場、五檔帶價、部位卡即時毛/淨損益、委託 tab 顯示回報、健康燈。
- 不影響富邦行情/訊號/自選/分時(只動 col4 + 既有 hook 加事件,不改 tick 廣播)。

## v1 完成後
- v2:庫存 tab(全部部位 + 額度列)+ 改價/刪單 + 一鍵平倉(對應後端加 cancel/modify/close routes + 帳務查詢)。
- v3:閃電下單 + 期貨(TF)+ 智慧單。
