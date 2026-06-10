import { useEffect, useRef, useState } from "react";
import { api, ApiError, type CapitalOrderResult } from "../lib/api";
import { buildOrderRow, type CapitalOrder, type OrderRowVM } from "../lib/capital-orders";

/** 委託清單(聚合列)+ 活單刪/改。結果靠 OnNewData 回報刷新,不樂觀更新。 */
export function OrdersList({ orders, env }: { orders: CapitalOrder[]; env: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  if (orders.length === 0) return <div className="text-xs text-ink-dim py-4 text-center">今日尚無委託</div>;
  return (
    <div className="space-y-0">
      {orders.map((o) => (
        <OrderRow key={o.seq_no} row={buildOrderRow(o)} env={env} onResult={setMsg} />
      ))}
      {msg && <div className="text-center text-xs mt-2 text-ink-muted">{msg}</div>}
    </div>
  );
}

type PendingAction =
  | { kind: "cancel" }
  | { kind: "correct_price"; price: number }
  | { kind: "decrease"; qty: number };

function OrderRow({ row, env, onResult }: { row: OrderRowVM; env: string; onResult: (m: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [price, setPrice] = useState("");
  const [decQty, setDecQty] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const latestPending = useRef(pending);   // doSend 完成時比對歸屬:Esc 後重開的新動作不可被舊回應關掉
  latestPending.current = pending;

  // 台股 tick 最小 0.01:>2 位小數會被後端 %.2f 無聲四捨五入,送出值≠確認框顯示值,前端先擋
  const priceOk = /^\d+(\.\d{1,2})?$/.test(price.trim()) && Number(price) > 0;
  // 減量是整數;小數會被 pydantic 422 短路(進不了安全閘=不留稽核),前端就要擋
  const decOk = /^\d+$/.test(decQty.trim()) && Number(decQty) > 0;

  const doSend = async () => {
    if (!pending || busy) return;
    const myAction = pending;
    const what = myAction.kind === "cancel" ? "刪單" : myAction.kind === "correct_price" ? "改價" : "減量";
    setBusy(true);
    try {
      let r: CapitalOrderResult;
      if (myAction.kind === "cancel") r = await api.capitalCancelOrder({ seq_no: row.seqNo });
      else if (myAction.kind === "correct_price") r = await api.capitalCorrectPrice({ seq_no: row.seqNo, price: myAction.price });
      else r = await api.capitalDecreaseQty({ seq_no: row.seqNo, qty: myAction.qty });
      onResult(`${r.ok ? "✓" : "✗"} ${what}:${r.message}`);
    } catch (e) {
      onResult(e instanceof ApiError ? `✗ ${what} 送出失敗(HTTP ${e.status})` : `✗ ${what} 送出失敗`);
    } finally {
      setBusy(false);
    }
    if (latestPending.current === myAction) {
      setPending(null);
      setEditing(false);
    }
  };

  return (
    <div className="border-b border-line py-2.5 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-serif font-medium">{row.title}</span>
        <span className={`text-xs ${row.sideClass}`}>{row.sideLabel}{row.flagLabel ? `·${row.flagLabel}` : ""}</span>
        {row.preOrder && <span className="text-2xs px-1 border border-line text-ink-dim rounded">預約</span>}
        <span className={`ml-auto text-xs px-2 py-0.5 rounded bg-bg-deep ${row.statusClass}`}>{row.statusLabel}</span>
      </div>
      <div className="text-xs text-ink-dim tabular-nums mt-1">
        {row.priceText} · {row.qtyText}{row.avgText ? ` · ${row.avgText}` : ""}{row.timeText ? ` · ${row.timeText}` : ""}
      </div>
      {row.errorMsg && <div className="text-2xs text-bear mt-0.5">{row.errorMsg}</div>}

      {row.actionable && (
        <div className="flex gap-2 mt-1.5 text-xs">
          <button onClick={() => setPending({ kind: "cancel" })}
            className="px-2 py-0.5 border border-line-strong text-ink-muted hover:text-bear hover:border-bear rounded">刪單</button>
          <button onClick={() => setEditing((v) => !v)}
            className="px-2 py-0.5 border border-line-strong text-ink-muted hover:text-ink rounded">改</button>
        </div>
      )}

      {editing && row.actionable && (
        <div className="mt-2 p-2 border border-line rounded space-y-1.5 text-xs">
          <div className="flex items-center gap-2">
            <span className="text-ink-dim w-12">改價</span>
            <input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" placeholder={row.priceText}
              className="flex-1 bg-bg-deep border border-line px-2 py-1 tabular-nums outline-none focus:border-accent" />
            <button disabled={!priceOk} onClick={() => setPending({ kind: "correct_price", price: Number(price) })}
              className="px-2 py-1 border border-line-strong disabled:opacity-40 rounded">送出</button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-ink-dim w-12">減量</span>
            <input value={decQty} onChange={(e) => setDecQty(e.target.value)} inputMode="numeric" placeholder={row.unit}
              className="flex-1 bg-bg-deep border border-line px-2 py-1 tabular-nums outline-none focus:border-accent" />
            <button disabled={!decOk} onClick={() => setPending({ kind: "decrease", qty: Number(decQty) })}
              className="px-2 py-1 border border-line-strong disabled:opacity-40 rounded">送出</button>
          </div>
        </div>
      )}

      {pending && row.actionable && (
        <ActionConfirm row={row} action={pending} env={env} busy={busy}
          onConfirm={doSend} onClose={() => setPending(null)} />
      )}
    </div>
  );
}

function ActionConfirm({ row, action, env, busy, onConfirm, onClose }: {
  row: OrderRowVM; action: PendingAction; env: string; busy: boolean; onConfirm: () => void; onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const prod = env === "prod";
  const desc = action.kind === "cancel" ? "刪單"
    : action.kind === "correct_price" ? `改價 → ${action.price.toFixed(2)}`
    : `減量 ${action.qty} ${row.unit}`;
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-20 bg-bg-deep/85" style={{ backdropFilter: "blur(2px)" }} />
      <div role="dialog" aria-modal="true"
        className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border p-5 w-[min(340px,90vw)] ${prod ? "border-bull" : "border-line-strong"}`}
        style={{ transform: "translate(-50%, -50%)" }}>
        <h3 className="font-serif font-bold text-lg mb-1">確認{action.kind === "cancel" ? "刪單" : "修改委託"}</h3>
        <p className={`text-xs mb-3 ${prod ? "text-bull font-bold" : "text-bear"}`}>
          {prod ? "⚠ 正式環境(真錢)" : env === "test" ? "測試環境" : "環境未知"}
        </p>
        <div className="text-sm space-y-1 tabular-nums">
          <div className="flex justify-between"><span className="text-ink-dim">標的</span><span>{row.title}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">原委託</span><span>{row.priceText} · {row.qtyText}</span></div>
          <div className="flex justify-between"><span className="text-ink-dim">動作</span><span className="text-bear">{desc}</span></div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-3 py-1.5 text-sm border border-line-strong text-ink-muted hover:text-ink">取消</button>
          <button onClick={onConfirm} disabled={busy} className="px-3 py-1.5 text-sm text-bg font-medium bg-bull disabled:opacity-40">確認</button>
        </div>
      </div>
    </>
  );
}
