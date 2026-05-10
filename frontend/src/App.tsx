import { useEffect, useState } from "react";
import { Health } from "./pages/Health";
import { Screener } from "./pages/Screener";

type Page = "health" | "screener";

export default function App() {
  const [page, setPage] = useState<Page>("health");

  // Soft fade-in to mimic editorial reading-rhythm
  useEffect(() => {
    document.body.classList.add("opacity-100");
  }, []);

  return (
    <>
      <Masthead />
      <Nav active={page} onNavigate={setPage} />
      {page === "health" && <Health />}
      {page === "screener" && <Screener />}
    </>
  );
}

function Masthead() {
  return (
    <header className="border-t-4 border-accent bg-bg-card">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-baseline justify-between gap-4 px-12 pb-4 pt-6 max-md:px-6">
        <h1 className="font-serif text-3xl font-bold tracking-editorial text-ink">
          treading{" "}
          <span className="font-light text-ink-muted">·</span>{" "}
          <em className="font-serif italic font-bold">king</em>
        </h1>
        <Meta />
      </div>
    </header>
  );
}

function Meta() {
  const today = new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).toUpperCase();

  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs text-ink-dim tracking-[0.3px]">
      <span>{today}</span>
      <span className="opacity-40">·</span>
      <span>SESSION CONTINUOUS</span>
    </div>
  );
}

function Nav({
  active,
  onNavigate,
}: {
  active: Page;
  onNavigate: (p: Page) => void;
}) {
  const items: Array<{ id: Page | string; label: string; disabled?: boolean }> = [
    { id: "health", label: "Health" },
    { id: "screener", label: "Screener" },
    { id: "signals", label: "Signals", disabled: true },
    { id: "watchlist", label: "Watchlist", disabled: true },
  ];

  return (
    <nav className="border-y border-line bg-bg-card/40">
      <div className="mx-auto flex max-w-[1200px] gap-0 px-12 max-md:px-6">
        {items.map((it) => {
          const isActive = active === it.id;
          const cls = `px-4 py-3 text-xs uppercase tracking-[2px] ${
            isActive
              ? "border-b-2 border-accent text-ink"
              : "text-ink-dim"
          } ${it.disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:text-ink"}`;

          if (it.disabled) {
            return (
              <span key={it.id} className={cls} title="(Phase 3)">
                {it.label}
              </span>
            );
          }

          return (
            <button
              key={it.id}
              type="button"
              onClick={() => onNavigate(it.id as Page)}
              className={`${cls} bg-transparent border-none border-b-2 ${
                isActive ? "border-accent" : "border-transparent"
              }`}
            >
              {it.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
