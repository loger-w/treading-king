import { useState } from "react";
import { useCapitalStatus, useCapitalOrders, useCapitalPositions } from "../hooks/useCapital";
import { OrderTicket } from "./OrderTicket";
import { FlashPanel } from "./FlashPanel";
import { OrdersList } from "./OrdersList";
import { PositionsList } from "./PositionsList";

const ENV = (import.meta.env.VITE_CAPITAL_ENV ?? "test") as string;

export function TradingPanel({ selected, onPick }: { selected: string | null; onPick?: (s: string) => void }) {
  const { status, lastError } = useCapitalStatus();
  const orders = useCapitalOrders();
  const positions = useCapitalPositions();
  const [tab, setTab] = useState<"order" | "flash" | "list" | "positions">("order");

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
        <button onClick={() => setTab("flash")} className={`flex-1 py-2 ${tab === "flash" ? "text-accent border-b-2 border-accent" : "text-ink-dim"}`}>⚡閃電</button>
        <button onClick={() => setTab("list")} className={`flex-1 py-2 ${tab === "list" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>委託 {orders.length > 0 && <span className="text-accent">{orders.length}</span>}</button>
        <button onClick={() => setTab("positions")} className={`flex-1 py-2 ${tab === "positions" ? "text-ink border-b-2 border-accent" : "text-ink-dim"}`}>庫存 {positions.length > 0 && <span className="text-accent">{positions.length}</span>}</button>
      </div>

      {/* flash 分頁=條件渲染:切走 unmount → 武裝 state 自然消失(spec「切分頁解除」);
          階梯自帶捲動容器,不能被外層 overflow 搶 */}
      <div className={`flex-1 min-h-0 ${tab === "flash" ? "" : "overflow-y-auto pr-1 scroll-editorial"}`}>
        {tab === "order" && <OrderTicket selected={selected} ready={ready} env={ENV} pos={pos} />}
        {tab === "flash" && <FlashPanel selected={selected} ready={ready} env={ENV} orders={orders} pos={pos} />}
        {tab === "list" && <OrdersList orders={orders} env={ENV} />}
        {tab === "positions" && <PositionsList positions={positions} env={ENV} onPick={(s) => { onPick?.(s); setTab("order"); }} />}
      </div>
    </section>
  );
}
