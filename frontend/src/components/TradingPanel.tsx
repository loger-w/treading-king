import { useEffect, useState } from "react";
import { api, type CapitalStockOrderReq } from "../lib/api";
import { useCapitalStatus, useCapitalOrders, useCapitalPositions } from "../hooks/useCapital";
import { subscribeOrderTicket, subscribeTicks } from "../hooks/useSignalsStream";
import { grossPnl, netPnl } from "../lib/capital-pnl";
import { OrderConfirmDialog } from "./OrderConfirmDialog";
import { OrdersList } from "./OrdersList";

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
  const [sending, setSending] = useState(false);

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
          <OrdersList orders={orders} env={ENV} />
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
