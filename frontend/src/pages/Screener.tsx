import { useEffect, useMemo, useState } from "react";
import {
  ALL_FIELDS,
  ALL_OPERATORS,
  api,
  ApiError,
  type CacheStatusResponse,
  type Condition,
  type ConditionField,
  type ConditionOperator,
  type Filter,
  type ScreenResponse,
  type ScreenResultRow,
  type Strategy,
} from "../lib/api";

// ---------------------------------------------------------------------------
// 顯示用對照表（i18n + UX 友善）
// ---------------------------------------------------------------------------

const FIELD_LABEL: Record<ConditionField, string> = {
  close: "收盤價",
  change_pct: "漲跌幅 %",
  volume: "成交量",
  amount: "成交金額",
  rsi_14: "RSI(14)",
  macd: "MACD",
  macd_signal: "MACD signal",
  kdj_k: "KDJ K",
  kdj_d: "KDJ D",
  kdj_j: "KDJ J",
  sma_5: "5 日均線",
  sma_20: "20 日均線",
  sma_60: "60 日均線",
  bbands_upper: "BB 上軌",
  bbands_middle: "BB 中軌",
  bbands_lower: "BB 下軌",
};

const OP_LABEL: Record<ConditionOperator, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  eq: "=",
};

function defaultFilter(): Filter {
  return {
    schema_version: 1,
    market: ["TWSE", "OTC"],
    exclude_etf: true,
    conditions: [
      { field: "rsi_14", operator: "lt", value: 30, days_ago: 0 },
    ],
    logic: "AND",
    limit: 200,
  };
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export function Screener() {
  const [filter, setFilter] = useState<Filter>(defaultFilter());
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [result, setResult] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    refreshStrategies();
  }, []);

  async function refreshStrategies() {
    try {
      const res = await api.strategies.list();
      setStrategies(res.strategies);
    } catch (e) {
      setError(formatError(e));
    }
  }

  async function search() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.screen(filter);
      setResult(r);
    } catch (e) {
      setResult(null);
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }

  async function saveAsNew() {
    const name = window.prompt("策略名稱？");
    if (!name?.trim()) return;
    try {
      await api.strategies.create({ name: name.trim(), filter_json: filter });
      await refreshStrategies();
    } catch (e) {
      setError(formatError(e));
    }
  }

  async function deleteStrategy(s: Strategy) {
    if (!window.confirm(`確定刪除策略「${s.name}」？`)) return;
    try {
      await api.strategies.delete(s.id);
      await refreshStrategies();
    } catch (e) {
      setError(formatError(e));
    }
  }

  function applyStrategy(s: Strategy) {
    setFilter(s.filter_json);
    setResult(null);
  }

  return (
    <div className="mx-auto max-w-[1200px] px-12 py-12 max-md:px-6 max-md:py-6 space-y-12">
      <CachePanel />
      <StrategyPanel
        filter={filter}
        setFilter={setFilter}
        strategies={strategies}
        onApply={applyStrategy}
        onDelete={deleteStrategy}
        onSave={saveAsNew}
        onSearch={search}
        loading={loading}
        error={error}
      />
      {result && <ResultsPanel result={result} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CachePanel — 緊湊一行
// ---------------------------------------------------------------------------

function CachePanel() {
  const [status, setStatus] = useState<CacheStatusResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    async function tick() {
      if (!alive) return;
      try {
        const res = await api.cacheStatus();
        if (alive) setStatus(res);
      } catch (e) {
        if (alive) setErr(formatError(e));
      }
      const interval = status?.running ? 3000 : 30000;
      timer = setTimeout(tick, interval);
    }
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.running]);

  async function trigger(limit?: number) {
    setSubmitting(true);
    setErr(null);
    try {
      await api.cacheRefresh(limit);
      const res = await api.cacheStatus();
      setStatus(res);
    } catch (e) {
      setErr(formatError(e));
    } finally {
      setSubmitting(false);
    }
  }

  const run = status?.latest_run;
  const isRunning = status?.running ?? false;
  const total = run?.symbols_total ?? 0;
  const done = run?.symbols_done ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <section>
      <div className="label-small text-accent mb-2.5">壹</div>
      <h2 className="h-display text-[28px] mb-4">指標快取</h2>

      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-line py-3.5">
        <div className="flex-1 min-w-[200px]">
          {!run ? (
            <div className="text-base text-ink">尚未建立 cache — 先跑一次</div>
          ) : (
            <>
              <div className="text-base font-medium text-ink">
                {run.run_date}
                <span className={`ml-3 font-serif italic ${
                  isRunning || run.status === "running"
                    ? "text-accent"
                    : run.status === "done"
                    ? "text-bull"
                    : run.status === "failed"
                    ? "text-bear"
                    : "text-ink-dim"
                }`}>
                  {isRunning ? `執行中 ${pct}%` : statusLabel(run.status)}
                </span>
              </div>
              <div className="mt-0.5 text-xs text-ink-dim">
                {done.toLocaleString()} / {total.toLocaleString()} 檔
                {run.error_text && (
                  <> — <span className="text-bear">{run.error_text}</span></>
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => trigger(undefined)}
            disabled={submitting || isRunning}
            className="border-2 border-accent bg-transparent px-4 py-1.5 text-xs uppercase tracking-[2px] text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            跑全市場
          </button>
          <button
            type="button"
            onClick={() => trigger(10)}
            disabled={submitting || isRunning}
            className="border border-line-strong bg-transparent px-4 py-1.5 text-xs uppercase tracking-[2px] text-ink-dim hover:text-ink hover:border-ink-muted disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Dev:10
          </button>
        </div>
      </div>

      {(isRunning || run?.status === "running") && (
        <div className="mt-2 h-0.5 bg-line/40 overflow-hidden">
          <div
            className="h-full bg-accent transition-[width] duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      {err && (
        <div className="mt-3 text-xs text-bear">{err}</div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// StrategyPanel — picker + 條件編輯器 + controls
// ---------------------------------------------------------------------------

interface StrategyPanelProps {
  filter: Filter;
  setFilter: (f: Filter) => void;
  strategies: Strategy[];
  onApply: (s: Strategy) => void;
  onDelete: (s: Strategy) => void;
  onSave: () => void;
  onSearch: () => void;
  loading: boolean;
  error: string | null;
}

function StrategyPanel(p: StrategyPanelProps) {
  return (
    <section>
      <div className="label-small text-accent mb-2.5">貳</div>
      <h2 className="h-display text-[36px] mb-6">策略與條件</h2>

      <StrategyPicker
        strategies={p.strategies}
        onApply={p.onApply}
        onDelete={p.onDelete}
      />

      <div className="mt-6 bg-bg-card border border-line p-6 max-md:p-4">
        <ConditionEditor filter={p.filter} setFilter={p.setFilter} />

        <FilterControls filter={p.filter} setFilter={p.setFilter} />

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <button
            type="button"
            onClick={p.onSave}
            className="text-xs uppercase tracking-[2px] text-ink-dim hover:text-ink"
          >
            儲存為新策略
          </button>
          <button
            type="button"
            onClick={p.onSearch}
            disabled={p.loading || p.filter.conditions.length === 0}
            className="border-2 border-accent bg-transparent px-6 py-2 text-sm uppercase tracking-[2px] text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {p.loading ? "搜尋中…" : "搜尋 →"}
          </button>
        </div>

        {p.error && (
          <div className="mt-4 border border-accent/40 bg-accent/10 px-4 py-3 text-sm">
            <div className="font-medium text-accent mb-1">錯誤</div>
            <div className="text-ink-muted text-xs font-mono break-all">
              {p.error}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// StrategyPicker
// ---------------------------------------------------------------------------

function StrategyPicker({
  strategies,
  onApply,
  onDelete,
}: {
  strategies: Strategy[];
  onApply: (s: Strategy) => void;
  onDelete: (s: Strategy) => void;
}) {
  if (strategies.length === 0) {
    return (
      <div className="text-xs text-ink-dim italic font-serif">
        尚無儲存的策略。下方組好條件按「儲存為新策略」存起來。
      </div>
    );
  }

  return (
    <div>
      <div className="label-tiny mb-2">已儲存策略</div>
      <div className="flex flex-wrap gap-2">
        {strategies.map((s) => {
          const isPreset = s.name.startsWith("[預設]");
          return (
            <div
              key={s.id}
              className="inline-flex items-center border border-line bg-bg-deep group hover:border-line-strong"
            >
              <button
                type="button"
                onClick={() => onApply(s)}
                title={s.description ?? ""}
                className="px-3 py-1.5 text-sm text-ink hover:text-accent"
              >
                {s.name}
              </button>
              {!isPreset && (
                <button
                  type="button"
                  onClick={() => onDelete(s)}
                  title="刪除"
                  className="px-2 py-1.5 text-xs text-ink-dim hover:text-bear border-l border-line"
                >
                  ×
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConditionEditor
// ---------------------------------------------------------------------------

function ConditionEditor({
  filter,
  setFilter,
}: {
  filter: Filter;
  setFilter: (f: Filter) => void;
}) {
  function updateCondition(idx: number, c: Condition) {
    const next = [...filter.conditions];
    next[idx] = c;
    setFilter({ ...filter, conditions: next });
  }

  function removeCondition(idx: number) {
    setFilter({
      ...filter,
      conditions: filter.conditions.filter((_, i) => i !== idx),
    });
  }

  function addCondition() {
    setFilter({
      ...filter,
      conditions: [
        ...filter.conditions,
        { field: "close", operator: "gt", value: 0, days_ago: 0 },
      ],
    });
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <div className="label-tiny">條件</div>
        <div className="text-xs text-ink-dim">
          {filter.conditions.length} 條
        </div>
      </div>

      <div className="space-y-2">
        {filter.conditions.map((c, i) => (
          <ConditionRow
            key={i}
            condition={c}
            onChange={(next) => updateCondition(i, next)}
            onRemove={() => removeCondition(i)}
            canRemove={filter.conditions.length > 1}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={addCondition}
        className="mt-3 text-sm text-ink-dim hover:text-accent border border-dashed border-line hover:border-accent px-4 py-1.5"
      >
        + 新增條件
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConditionRow — field [op] value [×]
// value 模式可切換「常數 / 其他欄位」
// ---------------------------------------------------------------------------

function ConditionRow({
  condition,
  onChange,
  onRemove,
  canRemove,
}: {
  condition: Condition;
  onChange: (c: Condition) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const valueIsField = typeof condition.value === "string";

  function setField(field: ConditionField) {
    onChange({ ...condition, field });
  }

  function setOperator(operator: ConditionOperator) {
    onChange({ ...condition, operator });
  }

  function setValueMode(mode: "const" | "field") {
    if (mode === "const" && valueIsField) {
      onChange({ ...condition, value: 0 });
    } else if (mode === "field" && !valueIsField) {
      onChange({ ...condition, value: "sma_20" });
    }
  }

  function setConstValue(v: number) {
    onChange({ ...condition, value: v });
  }

  function setFieldValue(field: ConditionField) {
    onChange({ ...condition, value: field });
  }

  return (
    <div className="flex items-center gap-2 border border-line bg-bg-deep px-3 py-2 max-md:flex-wrap">
      <select
        value={condition.field}
        onChange={(e) => setField(e.target.value as ConditionField)}
        className="bg-transparent border border-line text-sm text-ink px-2 py-1 outline-none focus:border-accent min-w-[110px]"
      >
        {ALL_FIELDS.map((f) => (
          <option key={f} value={f} className="bg-bg-card">
            {FIELD_LABEL[f]}
          </option>
        ))}
      </select>

      <select
        value={condition.operator}
        onChange={(e) => setOperator(e.target.value as ConditionOperator)}
        className="bg-transparent border border-line text-sm text-ink px-2 py-1 outline-none focus:border-accent w-12 text-center font-medium"
      >
        {ALL_OPERATORS.map((op) => (
          <option key={op} value={op} className="bg-bg-card">
            {OP_LABEL[op]}
          </option>
        ))}
      </select>

      <div className="inline-flex border border-line">
        <button
          type="button"
          onClick={() => setValueMode("const")}
          className={`px-2 py-1 text-xs ${
            !valueIsField ? "bg-accent/20 text-accent" : "text-ink-dim hover:text-ink"
          }`}
        >
          常數
        </button>
        <button
          type="button"
          onClick={() => setValueMode("field")}
          className={`px-2 py-1 text-xs border-l border-line ${
            valueIsField ? "bg-accent/20 text-accent" : "text-ink-dim hover:text-ink"
          }`}
        >
          欄位
        </button>
      </div>

      {!valueIsField ? (
        <input
          type="number"
          step="any"
          value={condition.value as number}
          onChange={(e) => setConstValue(Number(e.target.value))}
          className="bg-transparent border border-line text-sm text-ink px-2 py-1 outline-none focus:border-accent w-24 tabular-nums"
        />
      ) : (
        <select
          value={condition.value as string}
          onChange={(e) => setFieldValue(e.target.value as ConditionField)}
          className="bg-transparent border border-line text-sm text-ink px-2 py-1 outline-none focus:border-accent min-w-[110px]"
        >
          {ALL_FIELDS.map((f) => (
            <option key={f} value={f} className="bg-bg-card">
              {FIELD_LABEL[f]}
            </option>
          ))}
        </select>
      )}

      <div className="flex-1 min-w-0" />

      {canRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-ink-dim hover:text-bear text-lg px-2"
          title="刪除這條"
        >
          ×
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FilterControls — AND/OR + market + limit
// ---------------------------------------------------------------------------

function FilterControls({
  filter,
  setFilter,
}: {
  filter: Filter;
  setFilter: (f: Filter) => void;
}) {
  function toggleMarket(m: "TWSE" | "OTC") {
    const has = filter.market.includes(m);
    let next = has ? filter.market.filter((x) => x !== m) : [...filter.market, m];
    if (next.length === 0) next = [m]; // 至少一個
    setFilter({ ...filter, market: next });
  }

  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line pt-4">
      <div className="flex items-center gap-3">
        <span className="label-tiny">邏輯</span>
        <label className="text-sm cursor-pointer flex items-center gap-1.5">
          <input
            type="radio"
            checked={filter.logic === "AND"}
            onChange={() => setFilter({ ...filter, logic: "AND" })}
            className="accent-accent"
          />
          AND
        </label>
        <label className="text-sm cursor-pointer flex items-center gap-1.5">
          <input
            type="radio"
            checked={filter.logic === "OR"}
            onChange={() => setFilter({ ...filter, logic: "OR" })}
            className="accent-accent"
          />
          OR
        </label>
      </div>

      <div className="flex items-center gap-3">
        <span className="label-tiny">市場</span>
        {(["TWSE", "OTC"] as const).map((m) => (
          <label key={m} className="text-sm cursor-pointer flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={filter.market.includes(m)}
              onChange={() => toggleMarket(m)}
              className="accent-accent"
            />
            {m === "TWSE" ? "上市" : "上櫃"}
          </label>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <span className="label-tiny">上限</span>
        <input
          type="number"
          min={1}
          max={2000}
          value={filter.limit ?? 200}
          onChange={(e) =>
            setFilter({ ...filter, limit: Math.max(1, Math.min(2000, Number(e.target.value))) })
          }
          className="bg-transparent border border-line text-sm text-ink px-2 py-1 outline-none focus:border-accent w-20 tabular-nums"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResultsPanel
// ---------------------------------------------------------------------------

function ResultsPanel({ result }: { result: ScreenResponse }) {
  // 從 filter 抽出本次用到的所有欄位（含跨欄位 value）作為動態欄
  const usedFields = useMemo(() => {
    const set = new Set<ConditionField>();
    for (const c of result.filter.conditions) {
      set.add(c.field);
      if (typeof c.value === "string") set.add(c.value as ConditionField);
    }
    // 預設一定顯示這幾個
    set.delete("amount");
    return Array.from(set);
  }, [result.filter]);

  return (
    <section>
      <div className="label-small text-accent mb-2.5">參</div>
      <h2 className="h-display text-[36px] mb-4">結果</h2>

      <div className="text-xs text-ink-dim mb-4">
        截止 <span className="text-ink">{result.as_of_date}</span> ·
        已掃 <span className="text-ink">{result.total_scanned.toLocaleString()}</span> 檔 ·
        <span className="text-ink"> {result.count}</span> 檔符合
      </div>

      {result.count === 0 ? (
        <div className="text-center py-12 text-ink-dim font-serif italic border border-line">
          沒有股票符合目前條件。
        </div>
      ) : (
        <div className="overflow-x-auto border border-line">
          <table className="w-full">
            <thead className="bg-bg-card">
              <tr className="border-b border-line-strong">
                <th className="text-left label-tiny py-2 px-3">代號</th>
                <th className="text-left label-tiny py-2 px-3">名稱</th>
                <th className="text-right label-tiny py-2 px-3">收盤</th>
                <th className="text-right label-tiny py-2 px-3">漲跌幅</th>
                {usedFields
                  .filter((f) => f !== "close" && f !== "change_pct")
                  .map((f) => (
                    <th key={f} className="text-right label-tiny py-2 px-3">
                      {FIELD_LABEL[f]}
                    </th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {result.results.map((r) => (
                <ResultRow key={r.symbol} row={r} extraFields={usedFields.filter((f) => f !== "close" && f !== "change_pct")} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ResultRow({
  row,
  extraFields,
}: {
  row: ScreenResultRow;
  extraFields: ConditionField[];
}) {
  const change = row.change_pct ?? 0;
  const dir =
    change > 0 ? "text-bull" : change < 0 ? "text-bear" : "text-ink-muted";
  return (
    <tr className="border-b border-line/50 hover:bg-bg-card/40">
      <td className="py-2 px-3 font-medium text-ink tabular-nums">
        {row.symbol}
        {row.market && (
          <span className="ml-2 text-2xs text-ink-dim">{row.market}</span>
        )}
      </td>
      <td className="py-2 px-3 text-sm text-ink-muted">{row.name ?? "—"}</td>
      <td className="py-2 px-3 text-right tabular-nums text-sm">
        {fmtN(row.close)}
      </td>
      <td className={`py-2 px-3 text-right tabular-nums text-sm ${dir}`}>
        {fmtN(row.change_pct, 2)}%
      </td>
      {extraFields.map((f) => (
        <td
          key={f}
          className="py-2 px-3 text-right tabular-nums text-sm text-ink-dim"
        >
          {fmtN(row[f], decimalsFor(f))}
        </td>
      ))}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function statusLabel(s: string): string {
  return (
    {
      done: "完成",
      running: "執行中",
      failed: "失敗",
      skipped: "略過",
    } as Record<string, string>
  )[s] ?? s;
}

function formatError(e: unknown): string {
  if (e instanceof ApiError) {
    const detail =
      typeof e.detail === "object" && e.detail !== null
        ? JSON.stringify(e.detail)
        : String(e.detail);
    return `${e.status} — ${detail}`;
  }
  return e instanceof Error ? e.message : String(e);
}

function fmtN(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function decimalsFor(f: ConditionField): number {
  if (f === "volume" || f === "amount") return 0;
  if (f === "rsi_14" || f.startsWith("kdj_")) return 1;
  if (f === "change_pct") return 2;
  if (f === "macd" || f === "macd_signal") return 3;
  return 2;
}
