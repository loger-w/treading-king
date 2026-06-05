import { type StrategyConfig } from "../lib/api";

const ALL_CDP_LEVELS = ["ah", "nh", "cdp", "nl", "al"] as const;
const CDP_LEVEL_LABEL: Record<typeof ALL_CDP_LEVELS[number], string> = {
  ah: "AH (最高值)", nh: "NH (近高)", cdp: "CDP 中線", nl: "NL (近低)", al: "AL (最低值)",
};

interface Props {
  value: StrategyConfig;
  onChange: (next: StrategyConfig) => void;
}

/** 兩個 preset 策略共用的參數編輯(線多選 + 各自的數字參數)。 */
export function PresetStrategyFields({ value, onChange }: Props) {
  function toggleLevel(level: typeof ALL_CDP_LEVELS[number]) {
    const has = value.levels.includes(level);
    if (has && value.levels.length <= 1) return;   // 至少留 1 條
    const levels = has ? value.levels.filter((l) => l !== level) : [...value.levels, level];
    onChange({ ...value, levels });
  }
  function setNum(key: string, n: number) {
    onChange({ ...value, [key]: n } as StrategyConfig);
  }

  return (
    <div className="border border-line p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {ALL_CDP_LEVELS.map((lv) => {
          const checked = value.levels.includes(lv);
          const lock = checked && value.levels.length === 1;
          return (
            <label key={lv} className={`text-sm flex items-center gap-1 ${lock ? "opacity-60" : "cursor-pointer"}`}>
              <input type="checkbox" checked={checked} disabled={lock}
                onChange={() => toggleLevel(lv)} className="accent-accent" />
              {CDP_LEVEL_LABEL[lv]}
            </label>
          );
        })}
      </div>

      {value.type === "limit_up_open_touch" ? (
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">鎖死秒數
            <input type="number" min={5} max={600} value={value.lock_seconds}
              onChange={(e) => setNum("lock_seconds", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-20 tabular-nums" /></label>
          <label className="flex items-center gap-2">Tolerance
            <input type="number" min={0} max={10} value={value.tolerance_ticks}
              onChange={(e) => setNum("tolerance_ticks", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />tick</label>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">早盤視窗
            <input type="number" min={1} max={60} value={value.early_window_minutes}
              onChange={(e) => setNum("early_window_minutes", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />分</label>
          <label className="flex items-center gap-2">爆拉門檻
            <input type="number" step="any" value={value.surge_pct}
              onChange={(e) => setNum("surge_pct", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-20 tabular-nums" />%</label>
          <label className="flex items-center gap-2">回踩時限
            <input type="number" min={1} max={120} value={value.retest_within_minutes}
              onChange={(e) => setNum("retest_within_minutes", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />分</label>
          <label className="flex items-center gap-2">Tolerance
            <input type="number" min={0} max={10} value={value.tolerance_ticks}
              onChange={(e) => setNum("tolerance_ticks", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />tick</label>
        </div>
      )}
    </div>
  );
}
