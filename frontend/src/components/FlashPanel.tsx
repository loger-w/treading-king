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
        <div className="grid grid-cols-[1fr_72px_1fr] text-2xs text-ink-dim sticky top-0 bg-bg-deep border-b border-line z-[1]">
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
