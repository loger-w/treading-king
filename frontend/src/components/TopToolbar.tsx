import { type WSStatus } from "../hooks/useSignalsStream";

/**
 * Nav 下方 utility bar：
 * - 左：● 連線中 / ● 連線中… / ● 已斷線
 * - 右：⚙ 訊號規則 [badge] 按鈕（dialogOpen 時實心 accent）
 *
 * 對應 spec §7.2 / v11 mockup toolbar。無 border, transparent。
 */
interface Props {
  wsStatus: WSStatus;
  rulesCount: number;
  dialogOpen: boolean;
  onOpenRules: () => void;
}

function statusText(s: WSStatus): { text: string; color: string } {
  if (s === "open") return { text: "連線中", color: "text-bear" };
  if (s === "connecting") return { text: "連線中…", color: "text-accent" };
  return { text: "已斷線", color: "text-accent" };
}

export function TopToolbar({ wsStatus, rulesCount, dialogOpen, onOpenRules }: Props) {
  const { text, color } = statusText(wsStatus);
  return (
    <div className="bg-transparent">
      <div className="mx-auto max-w-[1600px] px-[60px] pt-[26px] pb-2.5 flex items-center justify-between gap-4 max-md:px-6">
        <span className="inline-flex items-baseline gap-2 text-[11px] uppercase tracking-[1.5px] text-ink-dim">
          <span className={`${color} text-[13px] leading-none`}>●</span>
          {text}
        </span>

        <button
          type="button"
          onClick={onOpenRules}
          className={[
            "inline-flex items-center gap-2.5 px-[18px] py-2 text-[12px] uppercase tracking-[1.8px] font-medium border transition-all duration-150 cursor-pointer",
            dialogOpen
              ? "bg-accent text-bg border-accent"
              : "bg-transparent text-accent border-accent hover:bg-accent/10",
          ].join(" ")}
        >
          <span className="text-[15px] leading-none">⚙</span>
          訊號規則
          <span
            className={[
              "text-[11px] px-1.5 py-[1px] font-semibold transition-colors duration-150",
              dialogOpen ? "bg-bg text-accent" : "bg-accent text-bg",
            ].join(" ")}
          >
            {rulesCount}
          </span>
        </button>
      </div>
    </div>
  );
}
