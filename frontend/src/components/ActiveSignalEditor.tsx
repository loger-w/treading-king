import { useState } from "react";
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal, type Condition,
  type ConditionField, type ConditionOperator, type Scope, type WindowCondition,
  type WindowConditionType, type WindowSeconds,
} from "../lib/api";

const FIELD_LABEL: Record<ConditionField, string> = {
  close: "即時價", change_pct: "漲跌幅 %", volume: "成交量", amount: "成交金額",
  rsi_14: "RSI(14)", macd: "MACD", macd_signal: "MACD signal",
  kdj_k: "KDJ K", kdj_d: "KDJ D", kdj_j: "KDJ J",
  sma_5: "5 日均線", sma_20: "20 日均線", sma_60: "60 日均線",
  bbands_upper: "BB 上軌", bbands_middle: "BB 中軌", bbands_lower: "BB 下軌",
  cdp_ah: "CDP AH (最高值)", cdp_nh: "CDP NH (近高)", cdp: "CDP 中軸",
  cdp_nl: "CDP NL (近低)", cdp_al: "CDP AL (最低值)",
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
  const [scope, setScope] = useState<Scope>(initial?.scope ?? { type: "watchlist" });
  const [cooldown, setCooldown] = useState(initial?.cooldown_seconds ?? 1800);
  const [ignoreAuctions, setIgnoreAuctions] = useState(initial?.ignore_auctions ?? true);
  const [enabled] = useState(initial?.enabled ?? true);
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
    if (filter.conditions.length === 0 && (filter.window_conditions ?? []).length === 0) {
      setError("至少要有一條條件"); return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = { name: name.trim(), filter_json: filter, scope, cooldown_seconds: cooldown, ignore_auctions: ignoreAuctions, enabled };
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
                    onClick={() => updateCond(i, { ...c, value: "sma_20" })}
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

        {/* Logic / Scope / Cooldown */}
        <div className="border-t border-line pt-3 mb-4 grid grid-cols-2 gap-4">
          <div>
            <div className="label-tiny mb-1">邏輯</div>
            <label className="text-sm mr-3"><input type="radio" checked={filter.logic === "AND"} onChange={() => setFilter({ ...filter, logic: "AND" })} className="accent-accent mr-1" />AND</label>
            <label className="text-sm"><input type="radio" checked={filter.logic === "OR"} onChange={() => setFilter({ ...filter, logic: "OR" })} className="accent-accent mr-1" />OR</label>
          </div>
          <div>
            <div className="label-tiny mb-1">套用範圍</div>
            <label className="text-sm mr-3"><input type="radio" checked={scope.type === "watchlist"} onChange={() => setScope({ type: "watchlist" })} className="accent-accent mr-1" />自選清單全部</label>
            <label className="text-sm"><input type="radio" checked={scope.type === "symbols"} onChange={() => setScope({ type: "symbols", symbols: [] })} className="accent-accent mr-1" />指定股票</label>
          </div>
          <div>
            <div className="label-tiny mb-1">Cooldown 秒</div>
            <input type="number" min={60} max={86400} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              className="bg-bg-deep border border-line text-sm px-2 py-1 w-32 tabular-nums" />
          </div>
          <div>
            <div className="label-tiny mb-1">集合競價時段忽略 volume_burst</div>
            <label className="text-sm"><input type="checkbox" checked={ignoreAuctions} onChange={(e) => setIgnoreAuctions(e.target.checked)} className="accent-accent mr-1" />開啟</label>
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
