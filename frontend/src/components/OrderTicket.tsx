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
