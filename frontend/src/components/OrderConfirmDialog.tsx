import { useEffect, type ReactNode } from "react";
import type { CapitalStockOrderReq } from "../lib/api";
import { TRADE_KIND_LABELS } from "../lib/capital-labels";

interface Props {
  req: CapitalStockOrderReq;
  env: string;                 // "test" | "prod"
  estAmount: number;
  busy?: boolean;              // 送出中:鎖確認鈕並明示,防再點被無聲吞掉
  onConfirm: () => void;
  onClose: () => void;
}

export function OrderConfirmDialog({ req, env, estAmount, busy, onConfirm, onClose }: Props) {
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
          <Row k="交易種類" v={TRADE_KIND_LABELS[req.trade_kind ?? "cash"]} />
          <Row k="條件" v={`${req.time_in_force ?? "ROD"}${req.price_type === "market" ? " · 市價" : ""}`} />
          <Row k="委託價" v={req.price_type === "market" ? `市價(閘用估價 ${req.price.toFixed(2)})` : req.price.toFixed(2)} />
          <Row k="數量" v={`${req.qty} 張`} />
          <Row k="預估金額" v={`NT$ ${estAmount.toLocaleString()}`} />
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-sm border border-line-strong text-ink-muted hover:text-ink">取消</button>
          <button onClick={onConfirm} disabled={busy}
            className={`px-4 py-2 text-sm text-bg font-medium disabled:opacity-50 ${isBuy ? "bg-bull" : "bg-bear"}`}>
            {busy ? "送出中…" : `確認${isBuy ? "買進" : "賣出"}`}
          </button>
        </div>
      </div>
    </>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return <div className="flex justify-between"><span className="text-ink-dim">{k}</span><span>{v}</span></div>;
}
