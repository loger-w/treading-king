import { useEffect, useMemo, useState } from "react";
import { api, type CapitalPosition } from "../lib/api";
import { subscribeTicks } from "../hooks/useSignalsStream";
import { grossPnl, pickPrice, snapshotPrices } from "../lib/capital-pnl";
import { limitDown, limitUp } from "../lib/tick";

/** 庫存總覽:每列 代號/張數/均價/現價/未實現損益;點列帶標的回下單匣;「平」=反向市價單(確認後送)。
 *  現價:WS tick 有訂的即時跳;其餘開分頁時 snapshot 批次補、30 秒刷新。 */
export function PositionsList({ positions, env, onPick }: {
  positions: CapitalPosition[]; env: string; onPick: (symbol: string) => void;
}) {
  const [tick, setTick] = useState<Record<string, { price: number; ts: number }>>({});  // WS 即時價(帶時戳,逾期退快照)
  const [snap, setSnap] = useState<Record<string, number>>({});   // 30s 快照價(每輪全量覆寫)
  const [closing, setClosing] = useState<CapitalPosition | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const symbols = useMemo(() => positions.map((p) => p.stock_no), [positions]);

  // WS tick 即時價
  useEffect(() => {
    if (symbols.length === 0) return;
    const set = new Set(symbols);
    return subscribeTicks((t) => {
      if (set.has(t.symbol)) setTick((m) => (m[t.symbol]?.price === t.price ? m : { ...m, [t.symbol]: { price: t.price, ts: Date.now() } }));
    });
  }, [symbols]);

  // snapshot 批次補(沒訂 tick 的標的也要有現價)+ 30s 刷新;顯示時 tick 優先
  useEffect(() => {
    if (symbols.length === 0) return;
    let alive = true;
    const load = async () => {
      try {
        const r = await api.quotesSnapshot(symbols);
        if (!alive) return;
        setSnap(snapshotPrices(r.quotes));
      } catch { /* keep */ }
    };
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, [symbols]);

  if (positions.length === 0) return <div className="text-xs text-ink-dim py-4 text-center">目前無庫存部位</div>;

  // 快照 30s 重載觸發 re-render,新鮮度判斷至少每輪重算一次
  const priceOf = (s: string) => pickPrice(tick[s], snap[s], Date.now());
  const total = positions.reduce((sum, p) => sum + grossPnl(p.qty, p.avg_price, priceOf(p.stock_no)), 0);
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
        const cur = priceOf(p.stock_no);
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
        <ClosePositionDialog pos={closing} env={env} cur={priceOf(closing.stock_no)}
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
