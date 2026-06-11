import { useEffect, useRef } from "react";
import { type WSStatus } from "../hooks/useSignalsStream";
import { SymbolSearch } from "./SymbolSearch";

/**
 * Toolbar：grid 4-col 對齊 main grid。
 *   col 1-2 (left):   ● 連線狀態
 *   col 3:            ⌕ 搜尋框（內嵌 SymbolSearch）
 *   col 4 (right):    ⚙ 訊號規則 按鈕
 *
 * 鍵盤：按 `/` 聚焦搜尋輸入框（input/textarea 已聚焦或有 modifier key 時跳過）。
 */
interface Props {
  wsStatus: WSStatus;
  rulesCount: number;
  dialogOpen: boolean;
  onOpenRules: () => void;
  onPickSymbol: (symbol: string, name: string | null) => void;
}

function statusText(s: WSStatus): { text: string; color: string } {
  if (s === "open") return { text: "連線中", color: "text-bear" };
  if (s === "connecting") return { text: "連線中…", color: "text-accent" };
  return { text: "已斷線", color: "text-accent" };
}

export function TopToolbar({ wsStatus, rulesCount, dialogOpen, onOpenRules, onPickSymbol }: Props) {
  const { text, color } = statusText(wsStatus);
  const searchWrapRef = useRef<HTMLDivElement | null>(null);

  // `/` 聚焦 input
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const tgt = e.target as HTMLElement | null;
      if (tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable)) return;
      const inp = searchWrapRef.current?.querySelector("input");
      if (inp) {
        e.preventDefault();
        inp.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="bg-transparent">
      <div
        className="mx-auto max-w-[1960px] px-9 pt-[26px] pb-2.5 grid items-center gap-6 max-md:px-6 max-md:grid-cols-1"
        style={{ gridTemplateColumns: "300px 460px 1fr 380px" }}  /* 與 Monitor 主 grid 同步,col 3 才對齊 */
      >
        <span
          className="inline-flex items-baseline gap-2 text-2xs uppercase tracking-[1.5px] text-ink-dim"
          style={{ gridColumn: "1 / 3", justifySelf: "start" }}
        >
          <span className={`${color} text-sm leading-none`}>●</span>
          {text}
        </span>

        <div ref={searchWrapRef} style={{ gridColumn: "3 / 4" }}>
          <SymbolSearch onPick={onPickSymbol} placeholder="搜尋股票代號或名稱…（按 / 聚焦）" />
        </div>

        <button
          type="button"
          onClick={onOpenRules}
          style={{ gridColumn: "4 / 5", justifySelf: "end" }}
          className={[
            "inline-flex items-center gap-2.5 px-[18px] py-2 text-xs uppercase tracking-[1.8px] font-medium border transition-all duration-150 cursor-pointer",
            dialogOpen
              ? "bg-accent text-bg border-accent"
              : "bg-transparent text-accent border-accent hover:bg-accent/10",
          ].join(" ")}
        >
          <span className="text-sm leading-none">⚙</span>
          訊號規則
          <span className={[
            "text-xs px-1.5 py-[1px] font-semibold transition-colors duration-150",
            dialogOpen ? "bg-bg text-accent" : "bg-accent text-bg",
          ].join(" ")}>
            {rulesCount}
          </span>
        </button>
      </div>
    </div>
  );
}
