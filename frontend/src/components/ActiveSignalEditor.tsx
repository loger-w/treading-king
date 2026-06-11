import { useState } from "react";
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal,
  type BreakoutRetestStrategy, type Condition,
  type ConditionField, type ConditionOperator, type LimitUpOpenTouchStrategy,
  type StrategyConfig,
  type WindowCondition, type WindowConditionType, type WindowSeconds,
} from "../lib/api";
import { ALL_CDP_LEVELS, CDP_LEVEL_LABEL, STRATEGY_LABEL } from "../lib/signal-labels";
import { PresetStrategyFields } from "./PresetStrategyFields";

const FIELD_LABEL: Record<ConditionField, string> = {
  close: "即時價",
  cdp_ah: "CDP AH (最高值)", cdp_nh: "CDP NH (近高)", cdp: "CDP 中軸",
  cdp_nl: "CDP NL (近低)", cdp_al: "CDP AL (最低值)",
  sma_5: "MA5 (5 日均線)", sma_20: "MA20 (20 日均線)",
  day_change_pct: "目前漲幅 %", day_volume: "目前總量 (張)",
};

const OP_LABEL: Record<ConditionOperator, string> = {
  gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=",
};

const WINDOW_OPTIONS: { value: WindowSeconds; label: string }[] = [
  { value: 60, label: "1 分鐘" }, { value: 180, label: "3 分鐘" },
  { value: 300, label: "5 分鐘" }, { value: 600, label: "10 分鐘" },
  { value: 1800, label: "30 分鐘" },
];

const WINDOW_TYPE_LABEL: Record<WindowConditionType, string> = {
  price_change_pct: "漲跌幅 %", volume_burst: "累積成交量", trade_count: "成交筆數",
};

const ALL_MA_LEVELS = ["sma_5", "sma_20"] as const;

const MA_LEVEL_LABEL: Record<typeof ALL_MA_LEVELS[number], string> = {
  sma_5: "MA5", sma_20: "MA20",
};

const DEFAULT_LIMIT_UP: LimitUpOpenTouchStrategy = {
  type: "limit_up_open_touch", lock_seconds: 60,
  levels: [...ALL_CDP_LEVELS], tolerance_ticks: 1,
};
const DEFAULT_BREAKOUT: BreakoutRetestStrategy = {
  type: "breakout_retest", early_window_minutes: 10, surge_pct: 3,
  surge_window_seconds: 60, retest_within_minutes: 10,
  levels: [...ALL_CDP_LEVELS], tolerance_ticks: 1,
};

interface Props {
  initial?: ActiveSignal;
  onClose: () => void;
  onSaved: () => void;
}

export function ActiveSignalEditor({ initial, onClose, onSaved }: Props) {
  const [name, setName] = useState(initial?.name ?? "");
  const [filter, setFilter] = useState<ActiveFilter>(initial?.filter_json ?? {
    conditions: [], window_conditions: [], logic: "AND",
  });
  const [strategy, setStrategy] = useState<StrategyConfig | null>(initial?.filter_json?.strategy ?? null);
  const [notifyDiscord, setNotifyDiscord] = useState(initial?.notify_discord ?? true);
  const [cooldown, setCooldown] = useState(initial?.cooldown_seconds ?? 1800);
  // 編輯器不提供 enabled 開關(列表的 toggle 才是入口),保留原值送回
  const enabled = initial?.enabled ?? true;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addWindow() {
    setFilter({
      ...filter,
      window_conditions: [
        ...(filter.window_conditions ?? []),
        { type: "price_change_pct", window_seconds: 300, operator: "gt", value: 2 },
      ],
    });
  }
  function updateWindow(i: number, w: WindowCondition) {
    const next = [...(filter.window_conditions ?? [])];
    next[i] = w;
    setFilter({ ...filter, window_conditions: next });
  }
  function removeWindow(i: number) {
    setFilter({ ...filter, window_conditions: (filter.window_conditions ?? []).filter((_, j) => j !== i) });
  }
  function addCond() {
    setFilter({
      ...filter,
      conditions: [...filter.conditions, { field: "close", operator: "gt", value: 0, days_ago: 0 }],
    });
  }
  function updateCond(i: number, c: Condition) {
    const next = [...filter.conditions];
    next[i] = c;
    setFilter({ ...filter, conditions: next });
  }
  function removeCond(i: number) {
    setFilter({ ...filter, conditions: filter.conditions.filter((_, j) => j !== i) });
  }

  async function save() {
    if (!name.trim()) { setError("請輸入名稱"); return; }
    let filterToSave: ActiveFilter;
    if (strategy) {
      filterToSave = { schema_version: 5, conditions: [], window_conditions: [], logic: "AND", strategy };
    } else {
      if (filter.conditions.length === 0
          && (filter.window_conditions ?? []).length === 0
          && !filter.cdp_proximity
          && !filter.ma_proximity) {
        setError("至少要有一條條件"); return;
      }
      // filter state 從 initial.filter_json 帶進來時可能殘留 strategy 鍵;
      // 後端 strategy 優先於自訂條件,不清掉的話這條規則會繼續照舊策略觸發
      filterToSave = { ...filter, strategy: null };
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        filter_json: filterToSave,
        scope: { type: "watchlist" as const },  // legacy; backend ignores
        cooldown_seconds: cooldown,
        enabled,
        notify_discord: notifyDiscord,
      };
      if (initial) await api.activeSignals.update(initial.id, payload);
      else await api.activeSignals.create(payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-50 bg-bg-deep/80 flex items-center justify-center p-4">
      <div className="bg-bg-card border border-line max-w-3xl w-full max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-baseline justify-between mb-4">
          <h3 className="h-display text-[24px]">{initial ? "編輯訊號規則" : "新增訊號規則"}</h3>
          <button type="button" onClick={onClose} className="text-ink-dim hover:text-ink text-xl">×</button>
        </div>

        <label className="block text-xs text-ink-dim mb-1">名稱</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-full bg-bg-deep border border-line px-3 py-2 mb-5 text-sm text-ink outline-none focus:border-accent" />

        {/* 預設策略 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">預設策略</div>
          <select
            value={strategy?.type ?? "none"}
            onChange={(e) => {
              const t = e.target.value;
              if (t === "limit_up_open_touch") setStrategy({ ...DEFAULT_LIMIT_UP });
              else if (t === "breakout_retest") setStrategy({ ...DEFAULT_BREAKOUT });
              else setStrategy(null);
            }}
            className="bg-bg-deep border border-line text-sm px-2 py-1 mb-3"
          >
            <option value="none">無(自訂條件)</option>
            <option value="limit_up_open_touch">{STRATEGY_LABEL.limit_up_open_touch}</option>
            <option value="breakout_retest">{STRATEGY_LABEL.breakout_retest}</option>
          </select>
          {strategy && <PresetStrategyFields value={strategy} onChange={setStrategy} />}
        </div>

        {!strategy && (<>
        {/* WindowCondition 區塊 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">即時時窗條件</div>
          {(filter.window_conditions ?? []).map((w, i) => (
            <div key={i} className="flex items-center gap-2 mb-2">
              <select value={w.type} onChange={(e) => updateWindow(i, { ...w, type: e.target.value as WindowConditionType })}
                className="bg-bg-deep border border-line text-sm px-2 py-1">
                {Object.entries(WINDOW_TYPE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <select value={w.window_seconds} onChange={(e) => updateWindow(i, { ...w, window_seconds: Number(e.target.value) as WindowSeconds })}
                className="bg-bg-deep border border-line text-sm px-2 py-1">
                {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <select value={w.operator} onChange={(e) => updateWindow(i, { ...w, operator: e.target.value as WindowCondition["operator"] })}
                className="bg-bg-deep border border-line text-sm px-2 py-1 w-12 text-center">
                {(["gt","gte","lt","lte"] as const).map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
              </select>
              <input type="number" step="any" value={w.value}
                onChange={(e) => updateWindow(i, { ...w, value: Number(e.target.value) })}
                className="bg-bg-deep border border-line text-sm px-2 py-1 w-24 tabular-nums" />
              <button type="button" onClick={() => removeWindow(i)} className="text-ink-dim hover:text-bear">×</button>
            </div>
          ))}
          <button type="button" onClick={addWindow} className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">+ 新增時窗條件</button>
        </div>

        {/* Filter.conditions 區塊 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">跨指標條件 (從快取)</div>
          <p className="text-2xs text-ink-dim mb-3 leading-relaxed">
            「即時價」= 最新一筆成交價；盤後 / 未開盤時為前一日收盤。其他指標來自每日快取。
          </p>
          {filter.conditions.map((c, i) => {
            const valIsField = typeof c.value === "string";
            return (
              <div key={i} className="flex items-center gap-2 mb-2">
                <select value={c.field} onChange={(e) => updateCond(i, { ...c, field: e.target.value as ConditionField })}
                  className="bg-bg-deep border border-line text-sm px-2 py-1">
                  {ALL_FIELDS.map(f => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
                </select>
                <select value={c.operator} onChange={(e) => updateCond(i, { ...c, operator: e.target.value as ConditionOperator })}
                  className="bg-bg-deep border border-line text-sm px-2 py-1 w-12 text-center">
                  {(["gt","gte","lt","lte","eq"] as const).map(op => <option key={op} value={op}>{OP_LABEL[op]}</option>)}
                </select>
                <div className="inline-flex border border-line">
                  <button type="button"
                    onClick={() => updateCond(i, { ...c, value: 0 })}
                    className={`px-2 py-1 text-xs ${!valIsField ? "bg-accent/20 text-accent" : "text-ink-dim"}`}>常數</button>
                  <button type="button"
                    onClick={() => updateCond(i, { ...c, value: "close" })}
                    className={`px-2 py-1 text-xs border-l border-line ${valIsField ? "bg-accent/20 text-accent" : "text-ink-dim"}`}>欄位</button>
                </div>
                {!valIsField ? (
                  <input type="number" step="any" value={c.value as number}
                    onChange={(e) => updateCond(i, { ...c, value: Number(e.target.value) })}
                    className="bg-bg-deep border border-line text-sm px-2 py-1 w-24 tabular-nums" />
                ) : (
                  <select value={c.value as string}
                    onChange={(e) => updateCond(i, { ...c, value: e.target.value as ConditionField })}
                    className="bg-bg-deep border border-line text-sm px-2 py-1">
                    {ALL_FIELDS.map(f => <option key={f} value={f}>{FIELD_LABEL[f]}</option>)}
                  </select>
                )}
                <button type="button" onClick={() => removeCond(i)} className="text-ink-dim hover:text-bear">×</button>
              </div>
            );
          })}
          <button type="button" onClick={addCond} className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">+ 新增條件</button>
        </div>

        {/* CDP / MA 觸發 — 共用一份編輯器(「至少留 1 個」「clamp 0-10」規則單一來源) */}
        <ProximityEditor
          title="CDP 觸發"
          desc="價格打到(或接近)任一所選 CDP 線即觸發。Tolerance = 0 為嚴格打到,>0 為 ± N tick 內也算。"
          tolHint="tick (0 = 嚴格打到,>0 = 接近也算)"
          allLevels={ALL_CDP_LEVELS}
          levelLabel={CDP_LEVEL_LABEL}
          value={filter.cdp_proximity}
          onChange={(next) => setFilter({ ...filter, cdp_proximity: next })}
          defaults={{ levels: [...ALL_CDP_LEVELS], tolerance_ticks: 0 }}
        />
        <ProximityEditor
          title="MA 觸發"
          desc="價格打到(或接近)所選 MA 線即觸發。SMA raw 不在合法 tick 上,Tolerance 建議 ≥ 1。"
          tolHint="tick (SMA raw 不在合法 tick,建議 ≥ 1)"
          allLevels={ALL_MA_LEVELS}
          levelLabel={MA_LEVEL_LABEL}
          value={filter.ma_proximity}
          onChange={(next) => setFilter({ ...filter, ma_proximity: next })}
          defaults={{ levels: [...ALL_MA_LEVELS], tolerance_ticks: 1 }}
        />

        </>)}

        {/* Logic / Discord / Cooldown */}
        <div className="border-t border-line pt-3 mb-4 grid grid-cols-2 gap-4">
          {!strategy && (
          <div>
            <div className="label-tiny mb-1">邏輯</div>
            <label className="text-sm mr-3"><input type="radio" checked={filter.logic === "AND"} onChange={() => setFilter({ ...filter, logic: "AND" })} className="accent-accent mr-1" />AND</label>
            <label className="text-sm"><input type="radio" checked={filter.logic === "OR"} onChange={() => setFilter({ ...filter, logic: "OR" })} className="accent-accent mr-1" />OR</label>
          </div>
          )}
          <div>
            <div className="label-tiny mb-1">Discord 通知</div>
            <label className="text-sm flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={notifyDiscord}
                onChange={(e) => setNotifyDiscord(e.target.checked)}
                className="accent-accent"
              />
              觸發時推送
            </label>
          </div>
          <div>
            <div className="label-tiny mb-1">Cooldown 秒</div>
            <input type="number" min={60} max={86400} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className="bg-bg-deep border border-line text-sm px-2 py-1 w-32 tabular-nums" />
          </div>
        </div>

        {error && <div className="border border-accent/40 bg-accent/10 px-3 py-2 mb-3 text-xs text-bear">{error}</div>}

        <div className="border-t border-line pt-3 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="text-ink-dim hover:text-ink text-sm px-4 py-2">取消</button>
          <button type="button" onClick={save} disabled={saving}
            className="border-2 border-accent text-accent px-5 py-2 text-sm uppercase tracking-[2px] hover:bg-accent/10 disabled:opacity-40">
            {saving ? "儲存中…" : (initial ? "更新並啟用" : "儲存並啟用")}
          </button>
        </div>
      </div>
    </div>
  );
}

// CDP / MA 觸發共用編輯器 — 「至少留 1 個」與「tolerance clamp 0-10」只寫一份
function ProximityEditor<L extends string>({
  title, desc, tolHint, allLevels, levelLabel, value, onChange, defaults,
}: {
  title: string;
  desc: string;
  tolHint: string;
  allLevels: readonly L[];
  levelLabel: Record<L, string>;
  value: { levels: L[]; tolerance_ticks: number } | null | undefined;
  onChange: (next: { levels: L[]; tolerance_ticks: number } | null) => void;
  defaults: { levels: L[]; tolerance_ticks: number };
}) {
  function toggleLevel(level: L) {
    if (!value) return;
    const checked = value.levels.includes(level);
    if (checked && value.levels.length <= 1) return;  // 至少留 1 個
    const levels = checked ? value.levels.filter((l) => l !== level) : [...value.levels, level];
    onChange({ ...value, levels });
  }
  function setTolerance(tol: number) {
    if (!value) return;
    onChange({ ...value, tolerance_ticks: Math.max(0, Math.min(10, Math.round(tol))) });
  }
  return (
    <div className="border-t border-line pt-3 mb-4">
      <div className="label-tiny mb-2">{title}</div>
      <p className="text-2xs text-ink-dim mb-3 leading-relaxed">{desc}</p>
      {value == null ? (
        <button type="button" onClick={() => onChange({ ...defaults })}
          className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">
          + 啟用 {title}
        </button>
      ) : (
        <div className="border border-line p-3 space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            {allLevels.map((lv) => {
              const checked = value.levels.includes(lv);
              const isLastChecked = checked && value.levels.length === 1;
              return (
                <label key={lv} className={`text-sm flex items-center gap-1 ${isLastChecked ? "opacity-60" : "cursor-pointer"}`}>
                  <input type="checkbox" checked={checked}
                    disabled={isLastChecked}
                    onChange={() => toggleLevel(lv)}
                    className="accent-accent" />
                  {levelLabel[lv]}
                </label>
              );
            })}
            <button type="button" onClick={() => onChange(null)}
              className="ml-auto text-ink-dim hover:text-bear text-xs">移除</button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-ink-muted">Tolerance:</span>
            <input type="number" min={0} max={10} step={1}
              value={value.tolerance_ticks}
              onChange={(e) => setTolerance(Number(e.target.value))}
              className="bg-bg-deep border border-line text-sm px-2 py-1 w-20 tabular-nums" />
            <span className="text-xs text-ink-dim">{tolHint}</span>
          </div>
        </div>
      )}
    </div>
  );
}
