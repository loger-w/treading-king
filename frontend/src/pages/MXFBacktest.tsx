import { MXFIntradayChart } from "../components/MXFIntradayChart";

export function MXFBacktest() {
  return (
    <div className="h-full flex flex-col gap-6 px-8 py-6">
      <header>
        <span className="label-tiny mb-1">Module · Live + Backtest</span>
        <h1 className="h-display text-2xl text-ink">小台指(MXF)</h1>
      </header>

      <section className="rounded-lg border border-line p-4">
        <div className="label mb-3">即時分時走勢</div>
        <MXFIntradayChart />
      </section>

      <section className="rounded-lg border border-line p-4 text-ink-muted">
        <div className="label mb-3">回測介面</div>
        <p>策略回測引擎開發中 — 多週期 K 線、視覺化策略編輯、績效報表。</p>
      </section>
    </div>
  );
}
