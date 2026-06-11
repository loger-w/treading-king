import { useEffect, useMemo, useRef, useState } from "react";
import { api, type CapitalOrder, type CapitalPosition } from "../lib/api";
import { useQuoteBook } from "../hooks/useQuoteBook";
import { subscribeTicks } from "../hooks/useSignalsStream";
import { buildLadder, splitMyLots } from "../lib/flash-ladder";
import { ARM_IDLE_MS, initialArm, reduceArm, type ArmState } from "../lib/flash-arm";
import { initialQtyState, manualQty, pressQuick, QTY_PRESETS, type QtyState } from "../lib/qty-quick";
import { TRADE_KINDS, TRADE_KIND_LABELS, type TradeKindValue } from "../lib/capital-labels";

interface Props {
  selected: string | null;
  ready: boolean;          // 群益 status === "ok"
  env: string;
  orders: CapitalOrder[];  // TradingPanel 既有的委託 store
  pos: CapitalPosition | null;  // 該標的庫存(無部位或庫存未載入=null)
}

export function FlashPanel({ selected, ready, env, orders, pos }: Props) {
  // 平盤參考價跟著五檔輪詢走(1Hz 天然帶重試)——一次性 fetch 失敗會讓
  // 整個 session 的階梯夾界退化成「現價±10%」漂移
  const { bids, asks, referencePrice: refPrice } = useQuoteBook(selected);
  const [last, setLast] = useState<number | null>(null);
  const [arm, setArm] = useState<ArmState>(initialArm());
  const [qtyState, setQtyState] = useState<QtyState>(initialQtyState());
  const [tradeKind, setTradeKind] = useState<TradeKindValue>("cash");
  const [hint, setHint] = useState<string | null>(null);
  const [followCenter, setFollowCenter] = useState(true);
  const [confirmAllCancel, setConfirmAllCancel] = useState(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastClick = useRef<{ key: string; ts: number } | null>(null);
  const centerRef = useRef<HTMLDivElement | null>(null);
  const progScroll = useRef(false);

  // 現價:WS tick 即時
  useEffect(() => {
    setLast(null);
    if (!selected) return;
    return subscribeTicks((t) => { if (t.symbol === selected) setLast(t.price); });
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

  // 該檔今日全部單(含已完成 — 黃括號要整日留存);活單另濾給刪單用
  const myStockOrders = useMemo(() => orders.filter((o) => o.stock_no === selected), [orders, selected]);
  const myActionable = useMemo(() => myStockOrders.filter((o) => o.actionable), [myStockOrders]);
  const myLots = useMemo(() => splitMyLots(myStockOrders), [myStockOrders]);

  const center = last ?? refPrice;
  const ladder = useMemo(
    () => (center != null ? buildLadder({ center, reference: refPrice, bids, asks, myOrders: myLots.active, myFills: myLots.fills }) : []),
    [center, refPrice, bids, asks, myLots],
  );

  // 程式捲動旗標:scroll 事件在 rAF 前發,onScroll 看旗標跳過、rAF 清旗標
  // (已置中時 scrollIntoView 不發 scroll 事件,所以不能靠 onScroll 清)。
  // 不可改 smooth —— 多幀多次 scroll 事件會在旗標清掉後誤判成手動捲動。
  useEffect(() => {
    if (!followCenter || !centerRef.current) return;
    progScroll.current = true;
    centerRef.current.scrollIntoView({ block: "center" });
    requestAnimationFrame(() => { progScroll.current = false; });
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

  // 點紅方格 → 刪該價位該方向全部活單(逐筆)
  const cancelling = useRef(false);
  const cancelAt = async (price: number, side: "B" | "S") => {
    touchIdle();
    // 連點防重複:第二輪對同批 seq_no 必被拒,失敗 hint 會蓋掉第一輪的成功訊息
    if (cancelling.current) return;
    const targets = myActionable.filter((o) => o.price === price && o.buy_sell === side);
    if (targets.length === 0) return;
    cancelling.current = true;
    try {
      const results = await Promise.allSettled(targets.map((o) => api.capitalCancelOrder({ seq_no: o.seq_no })));
      const fail = results.filter((r) => r.status === "rejected" || !(r as PromiseFulfilledResult<{ ok: boolean }>).value?.ok).length;
      setHint(fail === 0 ? `已刪 ${price.toFixed(2)} 的 ${targets.length} 筆掛單` : `✗ ${fail}/${targets.length} 筆刪單失敗`);
    } finally {
      cancelling.current = false;
    }
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
        <span className="text-sm font-bold">{selected}
          {pos && <span className="text-2xs text-ink-dim font-normal ml-2">庫存 {pos.qty} 張{pos.avg_price != null ? ` · 均 ${pos.avg_price.toFixed(2)}` : ""}</span>}
        </span>
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
                <button onClick={() => cancelAt(row.price, "B")} aria-label={`刪 ${row.price.toFixed(2)} 買單`}
                  className="my-0.5 ml-0.5 mr-1 px-1 min-w-[22px] text-2xs font-bold rounded border border-accent bg-accent/25 text-accent">
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
                <button onClick={() => cancelAt(row.price, "S")} aria-label={`刪 ${row.price.toFixed(2)} 賣單`}
                  className="my-0.5 mr-0.5 ml-1 px-1 min-w-[22px] text-2xs font-bold rounded border border-accent bg-accent/25 text-accent">
                  {row.mySellLots}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

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
        <span>掛單 {myActionable.length} 筆{pos ? ` · 部位 ${pos.qty > 0 ? "+" : ""}${pos.qty} 張` : ""}</span>
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
