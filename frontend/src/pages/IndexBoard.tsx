import { useLocalToggle } from "../hooks/useLocalToggle";
import { IndexIntradayChart } from "../components/IndexIntradayChart";
import { IndexOverlayChart } from "../components/IndexOverlayChart";
import { INDEX_SYMBOLS } from "../lib/index-symbols";

export function IndexBoard() {
  const [overlay, setOverlay] = useLocalToggle("tk:index:overlay", false);

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex flex-col gap-6 px-8 py-6 max-w-[1400px]">
        <header className="flex items-center justify-between">
          <div>
            <span className="label-tiny mb-1">Module · 大盤</span>
            <h1 className="h-display text-2xl text-ink">大盤指數</h1>
          </div>
          <div className="flex border border-line rounded-md overflow-hidden text-sm">
            <button
              type="button"
              onClick={() => setOverlay(false)}
              className={`px-4 py-1.5 transition-colors ${!overlay ? "bg-accent/[0.12] text-accent font-medium" : "text-ink-dim hover:text-ink"}`}
            >左右並排</button>
            <button
              type="button"
              onClick={() => setOverlay(true)}
              className={`px-4 py-1.5 transition-colors ${overlay ? "bg-accent/[0.12] text-accent font-medium" : "text-ink-dim hover:text-ink"}`}
            >重疊 %</button>
          </div>
        </header>

        {overlay ? (
          <section className="rounded-lg border border-line p-4">
            <div className="label mb-3">今日漲跌 % 對比</div>
            <IndexOverlayChart />
          </section>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {INDEX_SYMBOLS.map((s) => (
              <section key={s.code} className="rounded-lg border border-line p-4">
                <IndexIntradayChart code={s.code} name={s.name} />
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
