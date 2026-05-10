import { useEffect, useState } from "react";
import { Health } from "./pages/Health";

export default function App() {
  // Simple tab switcher (Plan §Phase 1: useState tab，不引 react-router)
  const [page] = useState<"health">("health");

  // Soft fade-in to mimic editorial reading-rhythm
  useEffect(() => {
    document.body.classList.add("opacity-100");
  }, []);

  return (
    <>
      <Masthead />
      <Nav active={page} />
      {page === "health" && <Health />}
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

function Nav({ active }: { active: string }) {
  const items: Array<{ id: string; label: string; disabled?: boolean }> = [
    { id: "health", label: "Health" },
    { id: "screener", label: "Screener", disabled: true },
    { id: "signals", label: "Signals", disabled: true },
    { id: "watchlist", label: "Watchlist", disabled: true },
  ];

  return (
    <nav className="border-y border-line bg-bg-card/40">
      <div className="mx-auto flex max-w-[1200px] gap-0 px-12 max-md:px-6">
        {items.map((it) => (
          <span
            key={it.id}
            className={`px-4 py-3 text-xs uppercase tracking-[2px] ${
              active === it.id
                ? "border-b-2 border-accent text-ink"
                : "text-ink-dim"
            } ${it.disabled ? "opacity-40" : "cursor-pointer hover:text-ink"}`}
            title={it.disabled ? "(Phase 2+)" : undefined}
          >
            {it.label}
          </span>
        ))}
      </div>
    </nav>
  );
}
