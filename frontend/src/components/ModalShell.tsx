import { type ReactNode } from "react";
import { useEscapeKey } from "../hooks/useEscapeKey";

/**
 * Modal 殼 — backdrop(blur+點擊關閉)+ Esc 關閉 + 置中卡。
 * 原本 7+ 個 dialog 各手刻一份且已漂移(FlashPanel 的少了 blur 與 Esc、
 * 環境警示三態只有一處有)——殼與環境語意collect在這裡,dialog 只寫內容。
 *
 * env 給值(capital 確認框)時:prod=綠框+真錢警示,test=測試環境,
 * 其他字串=環境未知(從嚴顯示,不可默認成測試)。
 */
export function ModalShell({ onClose, width, env, scroll, className = "p-6", children }: {
  onClose: () => void;
  width: string;            // CSS 寬度上限,如 "420px"
  env?: string;             // 給值才渲染環境邊框(capital 確認框)
  scroll?: boolean;         // 長內容:max-h + overflow
  className?: string;       // 卡片內距等附加樣式
  children: ReactNode;
}) {
  useEscapeKey(onClose);
  const border = env === "prod" ? "border-bull" : "border-line-strong";
  return (
    <>
      <div onClick={onClose} className="fixed inset-0 z-20 bg-bg-deep/85" style={{ backdropFilter: "blur(2px)" }} />
      <div role="dialog" aria-modal="true"
        className={`fixed top-1/2 left-1/2 z-[21] bg-bg-card border ${border} ${scroll ? "max-h-[82vh] overflow-y-auto scroll-editorial" : ""} ${className}`}
        style={{ transform: "translate(-50%, -50%)", width: `min(${width}, 90vw)` }}>
        {children}
      </div>
    </>
  );
}

/** capital 確認框的環境警示列 — 三態:prod / test / 未知(未知不可默認成測試) */
export function EnvNotice({ env }: { env: string }) {
  if (env === "prod") return <p className="text-xs mb-3 text-bull font-bold">⚠ 正式環境(真錢)</p>;
  if (env === "test") return <p className="text-xs mb-3 text-bear">測試環境</p>;
  return <p className="text-xs mb-3 text-bear">環境未知</p>;
}
