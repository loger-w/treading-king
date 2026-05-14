/**
 * 單個規則 chip — 顯示「規則名 + （命中時）上標當日次數」。
 *
 * 視覺：
 * - 預設：line-strong 邊框 + ink-muted 文字
 * - 命中（count > 0）：accent 紅邊框 + 微紅底 + accent 上標數字
 */

const SUPERSCRIPT_DIGITS = ["⁰", "¹", "²", "³", "⁴", "⁵", "⁶", "⁷", "⁸", "⁹"];

function toSuperscript(n: number): string {
  if (n < 10) return SUPERSCRIPT_DIGITS[n];
  // 兩位數以上：每位數轉
  return String(n).split("").map(d => SUPERSCRIPT_DIGITS[Number(d)]).join("");
}

interface Props {
  ruleName: string;
  count?: number;  // undefined or 0 = 沒命中
}

export function SignalChip({ ruleName, count = 0 }: Props) {
  const hit = count > 0;
  return (
    <span
      className={[
        "inline-flex items-baseline gap-1 px-[11px] py-1 text-[13px] tracking-[0.3px] border",
        hit
          ? "border-accent/50 text-ink bg-accent/[0.05]"
          : "border-line-strong text-ink-muted",
        "transition-colors duration-150",
      ].join(" ")}
    >
      {ruleName}
      {hit && (
        <sup className="text-[10px] text-accent font-semibold tabular-nums ml-0.5 leading-none">
          {toSuperscript(count)}
        </sup>
      )}
    </span>
  );
}
