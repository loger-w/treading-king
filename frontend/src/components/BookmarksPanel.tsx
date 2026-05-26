import { useEffect, useMemo, useState } from "react";
import { type ActiveSignal, type BookmarkGroup, type BookmarkItem, type MonitorListItem } from "../lib/api";
import { useBookmarks } from "../hooks/useBookmarks";
import { useAllBookmarkItems, useBookmarkItems } from "../hooks/useBookmarkItems";
import { useMonitorList } from "../hooks/useMonitorList";
import { type HitCounts } from "../hooks/useTodayHits";
import { type WatchlistQuote } from "../hooks/useWatchlistQuotes";
import { BookmarkNewDialog } from "./BookmarkNewDialog";
import { BookmarkManageDialog } from "./BookmarkManageDialog";
import { BookmarkEditMode } from "./BookmarkEditMode";
import { SignalChip } from "./SignalChip";

/**
 * 書籤面板 — 取代舊 WatchlistWithChips。
 *
 * 內嵌 sidebar(110px)+ list(剩餘)layout,沿用 Editorial Dark 風格。
 * 「全部」mode 把所有書籤的股票合併展示(去重 + section heading 分組)。
 */

const ALL_VIEW = "__all__";
const MONITOR_VIEW = "__monitor__";

interface Props {
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  quotes: Record<string, WatchlistQuote>;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onItemsChanged: (allSymbols: string[], names: Record<string, string | null>) => void;  // 給 Monitor 同步 watchlistSymbols + symbolNames
}

function rulesForSymbol(symbol: string, rules: ActiveSignal[]): ActiveSignal[] {
  return rules.filter((r) => {
    if (!r.enabled) return false;
    if (r.scope.type === "watchlist") return true;
    if (r.scope.type === "symbols") return r.scope.symbols.includes(symbol);
    return false;
  });
}

function totalHitsForSymbol(symbol: string, hitCounts: HitCounts): number {
  const m = hitCounts[symbol] ?? {};
  return Object.values(m).reduce((a, b) => a + b, 0);
}

export function BookmarksPanel({
  rules, hitCounts, quotes, selectedSymbol, onSelectSymbol, onItemsChanged,
}: Props) {
  const { groups, refresh: refreshGroups, create, remove: removeGroup, rename } = useBookmarks();
  const { items: monitorItems, remove: removeFromMonitor } = useMonitorList();
  const [selectedGroupId, setSelectedGroupId] = useState<string>(ALL_VIEW);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [editMode, setEditMode] = useState(false);

  // 拿單一書籤 items(selectedGroupId !== ALL_VIEW)
  const singleGroupId = selectedGroupId === ALL_VIEW ? null : selectedGroupId;
  const { items: singleItems, refresh: refreshSingle, removeItem } =
    useBookmarkItems(singleGroupId);

  // 拿「全部」items
  const { byGroup, bySymbolFirst, refresh: refreshAll } = useAllBookmarkItems(groups);

  // 通知 Monitor: 所有書籤的 symbols(union) + name map — 用於 useWatchlistQuotes 訂閱
  useEffect(() => {
    const symbols = Array.from(bySymbolFirst.keys());
    const names: Record<string, string | null> = {};
    for (const [sym, entry] of bySymbolFirst) {
      names[sym] = entry.item.name ?? null;
    }
    onItemsChanged(symbols, names);
  }, [bySymbolFirst, onItemsChanged]);

  // 選定書籤 — 如果切到非「全部」、退出 editMode
  function pickGroup(gid: string) {
    setSelectedGroupId(gid);
    setEditMode(false);
  }

  // 目前選中書籤(物件)
  const selectedGroup = groups.find((g) => g.id === selectedGroupId) ?? null;
  const canEdit = selectedGroup && !selectedGroup.is_system;

  // 合併 refresh helper(items 改動後叫他)
  async function refreshAfterMutation() {
    await Promise.all([refreshGroups(), refreshSingle(), refreshAll()]);
  }

  return (
    <>
      {/* Header */}
      <div className="flex items-baseline gap-2.5 mb-4 flex-shrink-0">
        <h2 className="font-serif font-bold text-2xl tracking-[-0.5px] leading-[1.05]">
          書籤
        </h2>
        <span className="font-sans font-normal text-sm text-ink-dim">
          ({groups.reduce((acc, g) => acc + g.count, 0)})
        </span>
        <button
          type="button"
          onClick={() => setManageOpen(true)}
          className="ml-auto text-xs text-ink-dim hover:text-accent"
        >
          ⚙ 管理
        </button>
      </div>

      {/* Body: 130 + 1fr — sidebar 名稱長度 truncate,不影響 list */}
      <div className="grid border border-line flex-1 min-h-0 overflow-hidden"
           style={{ gridTemplateColumns: "130px 1fr" }}>

        {/* Sidebar */}
        <div className="border-r border-line py-2 overflow-y-auto scroll-editorial">
          <SidebarItem
            label="監聽"
            count={monitorItems.length}
            selected={selectedGroupId === MONITOR_VIEW}
            system={true}
            onClick={() => pickGroup(MONITOR_VIEW)}
          />
          <SidebarItem
            label="全部"
            count={bySymbolFirst.size}
            selected={selectedGroupId === ALL_VIEW}
            onClick={() => pickGroup(ALL_VIEW)}
          />
          {groups.map((g) => (
            <SidebarItem
              key={g.id}
              label={g.name}
              count={g.count}
              selected={selectedGroupId === g.id}
              system={g.is_system}
              onClick={() => pickGroup(g.id)}
            />
          ))}
          <button
            type="button"
            onClick={() => setNewDialogOpen(true)}
            className="mx-3 mt-3 mb-1 w-[calc(100%-24px)] px-2 py-2 border border-dashed border-line-strong text-xs text-ink-dim hover:border-accent hover:text-accent"
          >
            + 新增
          </button>
        </div>

        {/* Main list */}
        <div className="overflow-y-auto scroll-editorial">
          {selectedGroupId === MONITOR_VIEW ? (
            <MonitorListView
              items={monitorItems}
              quotes={quotes}
              rules={rules}
              hitCounts={hitCounts}
              selectedSymbol={selectedSymbol}
              onSelect={onSelectSymbol}
              onRemove={removeFromMonitor}
            />
          ) : selectedGroupId === ALL_VIEW ? (
            <AllView
              groups={groups}
              byGroup={byGroup}
              bySymbolFirst={bySymbolFirst}
              quotes={quotes}
              rules={rules}
              hitCounts={hitCounts}
              selectedSymbol={selectedSymbol}
              onSelect={onSelectSymbol}
            />
          ) : editMode && canEdit && selectedGroup ? (
            <BookmarkEditMode
              group={selectedGroup}
              items={singleItems}
              groups={groups}
              quotes={quotes}
              onExit={() => setEditMode(false)}
              onChanged={refreshAfterMutation}
            />
          ) : (
            <SingleListView
              items={singleItems}
              quotes={quotes}
              rules={rules}
              hitCounts={hitCounts}
              selectedSymbol={selectedSymbol}
              onSelect={onSelectSymbol}
              onRemove={removeItem}
              canEdit={!!canEdit}
              onStartEdit={() => setEditMode(true)}
              isEmpty={singleItems.length === 0}
              isSystem={!!selectedGroup?.is_system}
              emptyHint={
                selectedGroup?.is_system
                  ? "等待排程更新中…"
                  : "這個書籤還沒有股票 — 上方搜尋加入第一檔"
              }
            />
          )}
        </div>
      </div>

      {/* Modals */}
      {newDialogOpen && (
        <BookmarkNewDialog
          onClose={() => setNewDialogOpen(false)}
          onCreate={async (name) => {
            await create(name);
            setNewDialogOpen(false);
          }}
        />
      )}
      {manageOpen && (
        <BookmarkManageDialog
          groups={groups}
          onClose={() => setManageOpen(false)}
          onRename={async (id, name) => { await rename(id, name); }}
          onDelete={async (id) => { await removeGroup(id); }}
          onCreate={async (name) => { await create(name); }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Sidebar item
// ---------------------------------------------------------------------------

function SidebarItem({ label, count, selected, system, onClick }: {
  label: string; count: number; selected: boolean; system?: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "block w-full text-left px-3 py-2.5 relative transition-colors",
        selected ? "bg-bg-card" : "hover:bg-bg-card/40",
      ].join(" ")}
    >
      {selected && (
        <span className="absolute left-0 top-3 w-[3px] h-[18px] bg-accent" aria-hidden />
      )}
      <div className={[
        "text-sm leading-tight overflow-hidden text-ellipsis whitespace-nowrap",
        selected ? "text-ink font-medium" : "text-ink-muted",
      ].join(" ")} title={label}>
        {system && <span className="text-accent">☆ </span>}
        {label}
      </div>
      <div className="text-xs text-ink-dim mt-0.5">{count}</div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// 「全部」view — 用 section 分組
// ---------------------------------------------------------------------------

function AllView({ groups, byGroup, bySymbolFirst, quotes, rules, hitCounts, selectedSymbol, onSelect }: {
  groups: BookmarkGroup[];
  byGroup: Map<string, BookmarkItem[]>;
  bySymbolFirst: Map<string, { item: BookmarkItem; groupId: string }>;
  quotes: Record<string, WatchlistQuote>;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
}) {
  // 為了去重,每個 group 只渲染「第一次出現該 symbol」的那 group(依 groups 順序)
  if (bySymbolFirst.size === 0) {
    return <EmptyState text="所有書籤都還是空的 — 上方搜尋加入第一檔" />;
  }

  return (
    <div>
      {groups.map((g) => {
        const groupItems = (byGroup.get(g.id) || []).filter(
          (it) => bySymbolFirst.get(it.symbol)?.groupId === g.id,
        );
        if (groupItems.length === 0) return null;
        return (
          <div key={g.id}>
            <div className="px-3.5 pt-3 pb-2 flex items-center gap-2 font-serif italic text-sm text-ink-muted">
              {g.is_system && <span className="text-accent">☆</span>}
              <span>{g.name}</span>
              <span className="flex-1 h-px bg-line"></span>
            </div>
            <ul>
              {groupItems.map((it) => (
                <ItemRow
                  key={`${g.id}-${it.symbol}`}
                  item={it}
                  quote={quotes[it.symbol]}
                  rules={rules}
                  hitCounts={hitCounts}
                  selectedSymbol={selectedSymbol}
                  onSelect={onSelect}
                  showRemove={false}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 單一書籤 view
// ---------------------------------------------------------------------------

function SingleListView({
  items, quotes, rules, hitCounts, selectedSymbol, onSelect, onRemove,
  canEdit, onStartEdit, isEmpty, emptyHint, isSystem,
}: {
  items: BookmarkItem[];
  quotes: Record<string, WatchlistQuote>;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove: (s: string) => void;
  canEdit: boolean;
  onStartEdit: () => void;
  isEmpty: boolean;
  emptyHint: string;
  isSystem: boolean;
}) {
  // sort:has-hit 置頂(by total desc)、其餘維持原順序
  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      const ha = totalHitsForSymbol(a.symbol, hitCounts);
      const hb = totalHitsForSymbol(b.symbol, hitCounts);
      if (ha !== hb) return hb - ha;
      return 0;
    });
  }, [items, hitCounts]);

  if (isEmpty) {
    return <EmptyState text={emptyHint} />;
  }

  return (
    <div>
      {canEdit && (
        <div className="px-3.5 py-2 border-b border-line flex justify-end">
          <button
            type="button"
            onClick={onStartEdit}
            className="text-xs text-ink-dim hover:text-accent"
          >
            ✎ 編輯
          </button>
        </div>
      )}
      <ul>
        {sorted.map((it) => (
          <ItemRow
            key={it.symbol}
            item={it}
            quote={quotes[it.symbol]}
            rules={rules}
            hitCounts={hitCounts}
            selectedSymbol={selectedSymbol}
            onSelect={onSelect}
            onRemove={!isSystem ? onRemove : undefined}
            showRemove={!isSystem}
          />
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 單一 row — 沿用 WatchlistWithChips 的視覺
// ---------------------------------------------------------------------------

function ItemRow({ item, quote, rules, hitCounts, selectedSymbol, onSelect, onRemove, showRemove }: {
  item: BookmarkItem;
  quote: WatchlistQuote | undefined;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove?: (s: string) => void;
  showRemove: boolean;
}) {
  const symRules = rulesForSymbol(item.symbol, rules);
  const isSel = item.symbol === selectedSymbol;
  const totalHits = totalHitsForSymbol(item.symbol, hitCounts);
  const hasHit = totalHits > 0;

  const price = quote?.price;
  const pct = quote?.changePct ?? null;
  const priceCls = pct == null ? "text-ink-dim"
    : pct > 0 ? "text-bull"
    : pct < 0 ? "text-bear" : "text-ink-dim";
  const isDown = pct != null && pct < 0;
  const markerBg = isDown ? "bg-bear" : "bg-accent";
  const markerBorder = isDown ? "border-l-bear" : "border-l-accent";

  return (
    <li
      className={[
        "relative px-3.5 py-4 border-b border-line cursor-pointer transition-colors duration-200",
        isSel ? `bg-bg-card border-l-2 ${markerBorder} pl-3` : "hover:bg-bg-card/40",
      ].join(" ")}
      onClick={() => onSelect(item.symbol)}
    >
      {hasHit && !isSel && (
        <span className={`absolute left-0 top-4 w-[3px] h-[22px] ${markerBg}`} aria-hidden />
      )}

      <div className="flex items-baseline gap-2 min-w-0 mb-2.5 pr-7">
        <span className="text-[19px] font-medium shrink-0 text-ink">{item.symbol}</span>
        <span className="text-sm text-ink-muted truncate">
          {item.name ?? "(無名稱)"}
        </span>
        <span className={`shrink-0 text-sm tabular-nums ${priceCls}`}>
          {price != null ? price.toFixed(2) : "—"}
          {pct != null && (
            <span className="ml-1 text-xs">
              {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
            </span>
          )}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {symRules.map((r) => (
          <SignalChip
            key={r.id}
            ruleName={r.name}
            count={hitCounts[item.symbol]?.[r.id] ?? 0}
          />
        ))}
      </div>

      {showRemove && onRemove && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRemove(item.symbol); }}
          className="absolute right-2.5 top-3 text-base text-ink-dim hover:text-accent px-1"
          aria-label={`移除 ${item.symbol}`}
        >
          ×
        </button>
      )}
    </li>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="p-6 text-center text-ink-dim font-serif italic text-sm">
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 監聽清單 view
// ---------------------------------------------------------------------------

function MonitorListView({
  items, quotes, rules, hitCounts, selectedSymbol, onSelect, onRemove,
}: {
  items: MonitorListItem[];
  quotes: Record<string, WatchlistQuote>;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove: (s: string) => void;
}) {
  if (items.length === 0) {
    return <EmptyState text="監聽清單還是空的 — 上方搜尋或從書籤加入" />;
  }
  return (
    <ul>
      {items.map((it) => (
        <MonitorRow
          key={it.symbol}
          symbol={it.symbol}
          name={it.name}
          quote={quotes[it.symbol]}
          rules={rules}
          hitCounts={hitCounts}
          selectedSymbol={selectedSymbol}
          onSelect={onSelect}
          onRemove={onRemove}
        />
      ))}
    </ul>
  );
}

function MonitorRow({
  symbol, name, quote, rules, hitCounts, selectedSymbol, onSelect, onRemove,
}: {
  symbol: string;
  name: string | null;
  quote: WatchlistQuote | undefined;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove: (s: string) => void;
}) {
  const symRules = rulesForSymbol(symbol, rules);
  const isSel = symbol === selectedSymbol;
  const totalHits = totalHitsForSymbol(symbol, hitCounts);
  const hasHit = totalHits > 0;

  const price = quote?.price;
  const pct = quote?.changePct ?? null;
  const priceCls = pct == null ? "text-ink-dim"
    : pct > 0 ? "text-bull"
    : pct < 0 ? "text-bear" : "text-ink-dim";
  const isDown = pct != null && pct < 0;
  const markerBg = isDown ? "bg-bear" : "bg-accent";
  const markerBorder = isDown ? "border-l-bear" : "border-l-accent";

  return (
    <li
      className={[
        "relative px-3.5 py-4 border-b border-line cursor-pointer transition-colors duration-200",
        isSel ? `bg-bg-card border-l-2 ${markerBorder} pl-3` : "hover:bg-bg-card/40",
      ].join(" ")}
      onClick={() => onSelect(symbol)}
    >
      {hasHit && !isSel && (
        <span className={`absolute left-0 top-4 w-[3px] h-[22px] ${markerBg}`} aria-hidden />
      )}
      <div className="flex items-baseline gap-2 min-w-0 mb-2.5 pr-7">
        <span className="text-[19px] font-medium shrink-0 text-ink">{symbol}</span>
        <span className="text-sm text-ink-muted truncate">{name ?? "(無名稱)"}</span>
        <span className={`shrink-0 text-sm tabular-nums ${priceCls}`}>
          {price != null ? price.toFixed(2) : "—"}
          {pct != null && (
            <span className="ml-1 text-xs">
              {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
            </span>
          )}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {symRules.map((r) => (
          <SignalChip key={r.id} ruleName={r.name} count={hitCounts[symbol]?.[r.id] ?? 0} />
        ))}
      </div>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onRemove(symbol); }}
        className="absolute right-2.5 top-3 text-base text-ink-dim hover:text-accent px-1"
        aria-label={`從監聽清單移除 ${symbol}`}
      >
        ×
      </button>
    </li>
  );
}
