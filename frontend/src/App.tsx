import { useEffect, useState } from "react";
import { useMe } from "./hooks/useMe";
import { Health } from "./pages/Health";
import { Monitor } from "./pages/Monitor";

type Page = "health" | "monitor";

export default function App() {
  const [page, setPage] = useState<Page>("health");

  useEffect(() => {
    document.body.classList.add("opacity-100");
  }, []);

  return (
    <>
      <Masthead />
      <Nav active={page} onNavigate={setPage} />
      {page === "health" && <Health />}
      {page === "monitor" && <Monitor />}
    </>
  );
}

function Masthead() {
  return (
    <header className="border-t-4 border-accent bg-bg-card">
      <div className="mx-auto flex max-w-[1960px] flex-wrap items-baseline justify-between gap-4 px-9 pb-4 pt-6 max-md:px-6">
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
  const me = useMe();
  const today = new Date().toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div className="flex flex-wrap items-center gap-x-3 text-xs text-ink-dim tracking-[0.3px]">
      {me && (
        <>
          <span className="text-ink">
            You are: <strong className="font-semibold">{me.user_label}</strong>
            {me.is_cache_owner && (
              <span className="ml-2 rounded border border-accent px-1.5 py-0.5 text-[10px] uppercase tracking-[1px] text-accent">
                cache owner
              </span>
            )}
          </span>
          <span className="opacity-40">·</span>
        </>
      )}
      <span>{today}</span>
      <span className="opacity-40">·</span>
      <span>盤中連續競價</span>
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
  const items: Array<{ id: Page; label: string }> = [
    { id: "health", label: "系統狀態" },
    { id: "monitor", label: "即時監控" },
  ];

  return (
    <nav className="border-y border-line bg-bg-card/40">
      <div className="mx-auto flex max-w-[1960px] gap-0 px-9 max-md:px-6">
        {items.map((it) => {
          const isActive = active === it.id;
          return (
            <button
              key={it.id}
              type="button"
              onClick={() => onNavigate(it.id)}
              className={`px-4 py-3 text-xs uppercase tracking-[2px] cursor-pointer hover:text-ink bg-transparent border-b-2 ${
                isActive ? "border-accent text-ink" : "text-ink-dim border-transparent"
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
