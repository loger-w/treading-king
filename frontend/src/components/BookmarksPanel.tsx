import { useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { type ActiveSignal, type BookmarkGroup, type BookmarkItem, type MonitorListItem } from "../lib/api";
import { applyDragToOrder } from "../lib/reorder";
import { useBookmarks } from "../hooks/useBookmarks";
import { useAllBookmarkItems, useBookmarkItems } from "../hooks/useBookmarkItems";
import { useMonitorList } from "../hooks/useMonitorList";
import { type HitCounts } from "../hooks/useTodayHits";
import { useWatchlistQuotes, type WatchlistQuote } from "../hooks/useWatchlistQuotes";
import { BookmarkNewDialog } from "./BookmarkNewDialog";
import { BookmarkManageDialog } from "./BookmarkManageDialog";
import { BookmarkEditMode } from "./BookmarkEditMode";
import { SignalChip } from "./SignalChip";

/**
 * 書籤面板。
 *
 * 內嵌 sidebar(110px)+ list(剩餘)layout,沿用 Editorial Dark 風格。
 * 「全部」mode 把所有書籤的股票合併展示(去重 + section heading 分組)。
 */

const ALL_VIEW = "__all__";
const MONITOR_VIEW = "__monitor__";

// click 與 drag 的分界:6px 內是點擊(選股/切書籤),超過才啟動拖拉
function useDragSensors() {
  return useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
}

// dnd-kit 接線共用:回傳掛在容器元素上的 ref/transform/手勢 listeners。
// 只 spread listeners、不 spread attributes — attributes 會把元素標成可鍵盤
// 排序的 button,但這裡只裝 PointerSensor,鍵盤操作並不存在(誤導 a11y)
function useSortableContainer(id: string) {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return {
    containerRef: setNodeRef,
    containerStyle: {
      transform: CSS.Transform.toString(transform),
      transition,
      opacity: isDragging ? 0.5 : undefined,
    } as React.CSSProperties,
    containerProps: { ...listeners },
  };
}

interface Props {
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  refreshToken?: number;   // 外部 mutation(加入書籤 dialog)後 bump,觸發重抓而非 remount
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
  onItemsChanged: (allSymbols: string[], names: Record<string, string | null>) => void;  // 給 Monitor 同步 watchlistSymbols + symbolNames
}

export function BookmarksPanel({
  rules, hitCounts, refreshToken = 0, selectedSymbol, onSelectSymbol, onItemsChanged,
}: Props) {
  const { groups, loading: groupsLoading, error: groupsError, refresh: refreshGroups, create, remove: removeGroup, rename, reorderGroups } = useBookmarks();
  const { items: monitorItems, remove: removeFromMonitor, reorder: reorderMonitor } = useMonitorList();
  const [selectedGroupId, setSelectedGroupId] = useState<string>(ALL_VIEW);
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [editMode, setEditMode] = useState(false);

  // 每條啟用的規則都對 monitor_list 裡的每個 symbol 觸發(post-refactor scope 不再控制範圍)。
  // 書籤列也顯示 chip 是 v1 妥協:不在 monitor_list 的書籤實際不會觸發——
  // IntradayChart 的「+ 加入監聽」按鈕才是監聽資格的正式入口。
  const enabledRules = useMemo(() => rules.filter((r) => r.enabled), [rules]);

  // 拿單一書籤 items(selectedGroupId !== ALL_VIEW)
  const singleGroupId = selectedGroupId === ALL_VIEW ? null : selectedGroupId;
  const { items: singleItems, refresh: refreshSingle, removeItem, reorder: reorderItems } =
    useBookmarkItems(singleGroupId);

  // 拿「全部」items
  const { byGroup, bySymbolFirst, refresh: refreshAll } = useAllBookmarkItems(groups);

  // 即時報價在本面板訂(唯一消費者):每個 tick 的 setState 半徑侷限在書籤欄,
  // 不再打到頁根讓四欄(含下單面板)全量重繪
  const allWatchSymbols = useMemo(
    () => Array.from(new Set([...bySymbolFirst.keys(), ...monitorItems.map((m) => m.symbol)])),
    [bySymbolFirst, monitorItems],
  );
  const quotes = useWatchlistQuotes(allWatchSymbols);

  // 通知 Monitor: 所有書籤的 symbols(union) + name map — 用於 useWatchlistQuotes 訂閱
  useEffect(() => {
    const symbols = Array.from(bySymbolFirst.keys());
    const names: Record<string, string | null> = {};
    for (const [sym, entry] of bySymbolFirst) {
      names[sym] = entry.item.name ?? null;
    }
    onItemsChanged(symbols, names);
  }, [bySymbolFirst, onItemsChanged]);

  // 在管理 dialog 刪掉目前選中的書籤後,選擇會懸空:畫面停在已刪 group 的
  // stale items、× 按鈕還會對已刪 group 打 404 — groups 更新後檢查並退回「全部」
  useEffect(() => {
    if (selectedGroupId === ALL_VIEW || selectedGroupId === MONITOR_VIEW) return;
    if (groups.length > 0 && !groups.some((g) => g.id === selectedGroupId)) {
      setSelectedGroupId(ALL_VIEW);
    }
  }, [groups, selectedGroupId]);

  // 選定書籤 — 如果切到非「全部」、退出 editMode
  function pickGroup(gid: string) {
    setSelectedGroupId(gid);
    setEditMode(false);
  }

  // Sidebar user 群組拖拉(系統書籤固定殿後、不參與)
  const userGroups = useMemo(() => groups, [groups]);
  // id 陣列 reference 要穩定:本面板每個行情 tick 重繪,新陣列會打穿
  // dnd-kit SortableContext 的 items memo、迫使全部 sortable 列重算
  const userGroupIds = useMemo(() => userGroups.map((g) => g.id), [userGroups]);
  const sidebarSensors = useDragSensors();

  function handleGroupDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    reorderGroups(applyDragToOrder(userGroupIds, String(active.id), String(over.id)));
  }

  // 「全部」view 的 byGroup 不會因 item 重排自動更新(group 集合沒變),拖完顯式同步
  async function reorderItemsEverywhere(symbols: string[]) {
    await reorderItems(symbols);
    await refreshAll();
  }

  // 目前選中書籤(物件)
  const selectedGroup = groups.find((g) => g.id === selectedGroupId) ?? null;
  const canEdit = !!selectedGroup;

  // 合併 refresh helper(items 改動後叫他)
  async function refreshAfterMutation() {
    await Promise.all([refreshGroups(), refreshSingle(), refreshAll()]);
  }

  // 外部 mutation(chart header 的加入書籤 dialog)→ 重抓,不 remount
  const lastToken = useRef(refreshToken);
  useEffect(() => {
    if (refreshToken === lastToken.current) return;
    lastToken.current = refreshToken;
    refreshAfterMutation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  // 單列 × 移除也要走同一條 refresh 路:只 refreshSingle 的話 sidebar 計數、
  // 「全部」view 與報價訂閱 union(onItemsChanged)全部不會同步
  async function removeItemEverywhere(symbol: string) {
    await removeItem(symbol);   // 內部已 refreshSingle
    await Promise.all([refreshGroups(), refreshAll()]);
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
          <DndContext sensors={sidebarSensors} onDragEnd={handleGroupDragEnd}>
            <SortableContext items={userGroupIds}
              strategy={verticalListSortingStrategy}>
              {userGroups.map((g) => (
                <SortableSidebarItem
                  key={g.id}
                  id={g.id}
                  label={g.name}
                  count={g.count}
                  selected={selectedGroupId === g.id}
                  onClick={() => pickGroup(g.id)}
                />
              ))}
            </SortableContext>
          </DndContext>
          <button
            type="button"
            onClick={() => setNewDialogOpen(true)}
            className="mx-3 mt-3 mb-1 w-[calc(100%-24px)] px-2 py-2 border border-dashed border-line-strong text-xs text-ink-dim hover:border-accent hover:text-accent"
          >
            + 新增
          </button>
        </div>

        {/* Main list — 首載與後端失聯要跟「真的空」區分,不可顯示誤導性空狀態 */}
        <div className="overflow-y-auto scroll-editorial">
          {groupsLoading ? (
            <EmptyState text="載入中…" />
          ) : groupsError && groups.length === 0 ? (
            <div className="p-6 text-center text-sm">
              <div className="text-bear mb-2">書籤載入失敗(後端未連線?)</div>
              <button type="button" onClick={() => refreshGroups()}
                className="text-xs text-ink-dim border border-line px-3 py-1 hover:text-accent hover:border-accent">重試</button>
            </div>
          ) : selectedGroupId === MONITOR_VIEW ? (
            <MonitorListView
              items={monitorItems}
              quotes={quotes}
              rules={enabledRules}
              hitCounts={hitCounts}
              selectedSymbol={selectedSymbol}
              onSelect={onSelectSymbol}
              onRemove={removeFromMonitor}
              onReorder={reorderMonitor}
            />
          ) : selectedGroupId === ALL_VIEW ? (
            <AllView
              groups={groups}
              byGroup={byGroup}
              bySymbolFirst={bySymbolFirst}
              quotes={quotes}
              rules={enabledRules}
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
              onReorder={reorderItemsEverywhere}
            />
          ) : (
            <SingleListView
              items={singleItems}
              quotes={quotes}
              rules={enabledRules}
              hitCounts={hitCounts}
              selectedSymbol={selectedSymbol}
              onSelect={onSelectSymbol}
              onRemove={removeItemEverywhere}
              canEdit={!!canEdit}
              onStartEdit={() => setEditMode(true)}
              isEmpty={singleItems.length === 0}
              emptyHint="這個書籤還沒有股票 — 上方搜尋加入第一檔"
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

function SidebarItem({ label, count, selected, system, onClick, containerRef, containerStyle, containerProps }: {
  label: string; count: number; selected: boolean; system?: boolean; onClick: () => void;
  containerRef?: (node: HTMLElement | null) => void;
  containerStyle?: React.CSSProperties;
  containerProps?: React.HTMLAttributes<HTMLButtonElement>;
}) {
  return (
    <button
      ref={containerRef}
      style={containerStyle}
      {...containerProps}
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

function SortableSidebarItem(props: Parameters<typeof SidebarItem>[0] & { id: string }) {
  return <SidebarItem {...props} {...useSortableContainer(props.id)} />;
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
  canEdit, onStartEdit, isEmpty, emptyHint,
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
}) {
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
        {items.map((it) => (
          <ItemRow
            key={it.symbol}
            item={it}
            quote={quotes[it.symbol]}
            rules={rules}
            hitCounts={hitCounts}
            selectedSymbol={selectedSymbol}
            onSelect={onSelect}
            onRemove={onRemove}
            showRemove
          />
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 單一 row — 書籤列與監聽列共用(item 只需代號/名稱,change_pct 為書籤獨有的退階欄位)
// ---------------------------------------------------------------------------

function ItemRow({ item, quote, rules, hitCounts, selectedSymbol, onSelect, onRemove, showRemove, removeLabel, containerRef, containerStyle, containerProps }: {
  item: { symbol: string; name: string | null; change_pct?: number | null };
  quote: WatchlistQuote | undefined;
  rules: ActiveSignal[];   // 已過濾 enabled
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove?: (s: string) => void;
  showRemove: boolean;
  removeLabel?: string;
  containerRef?: (node: HTMLElement | null) => void;
  containerStyle?: React.CSSProperties;
  containerProps?: React.HTMLAttributes<HTMLLIElement>;
}) {
  const symRules = rules;
  const isSel = item.symbol === selectedSymbol;

  const price = quote?.price;
  const pct = quote?.changePct ?? null;
  const priceCls = pct == null ? "text-ink-dim"
    : pct > 0 ? "text-bull"
    : pct < 0 ? "text-bear" : "text-ink-dim";
  const isDown = pct != null && pct < 0;
  const markerBorder = isDown ? "border-l-bear" : "border-l-accent";

  return (
    <li
      ref={containerRef}
      style={containerStyle}
      {...containerProps}
      className={[
        "relative px-3.5 py-4 border-b border-line cursor-pointer transition-colors duration-200",
        isSel ? `bg-bg-card border-l-2 ${markerBorder} pl-3` : "hover:bg-bg-card/40",
      ].join(" ")}
      onClick={() => onSelect(item.symbol)}
    >
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
          aria-label={removeLabel ?? `移除 ${item.symbol}`}
        >
          ×
        </button>
      )}
    </li>
  );
}

function SortableItemRow(props: Parameters<typeof ItemRow>[0]) {
  return <ItemRow {...props} {...useSortableContainer(props.item.symbol)} />;
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
  items, quotes, rules, hitCounts, selectedSymbol, onSelect, onRemove, onReorder,
}: {
  items: MonitorListItem[];
  quotes: Record<string, WatchlistQuote>;
  rules: ActiveSignal[];
  hitCounts: HitCounts;
  selectedSymbol: string | null;
  onSelect: (s: string) => void;
  onRemove: (s: string) => void;
  onReorder: (symbols: string[]) => void;
}) {
  const [editMode, setEditMode] = useState(false);
  const itemIds = useMemo(() => items.map((it) => it.symbol), [items]);
  const sensors = useDragSensors();

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    onReorder(applyDragToOrder(itemIds, String(active.id), String(over.id)));
  }

  if (items.length === 0) {
    return <EmptyState text="監聽清單還是空的 — 上方搜尋或從書籤加入" />;
  }

  if (editMode) {
    return (
      <div>
        <div className="px-3.5 py-2 border-b border-line flex justify-end">
          <button
            type="button"
            onClick={() => setEditMode(false)}
            className="text-xs text-ink-dim hover:text-accent"
          >
            ✕ 結束編輯
          </button>
        </div>
        <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
          <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
            <ul>
              {items.map((it) => (
                <SortableItemRow
                  key={it.symbol}
                  item={{ symbol: it.symbol, name: it.name }}
                  quote={quotes[it.symbol]}
                  rules={rules}
                  hitCounts={hitCounts}
                  selectedSymbol={selectedSymbol}
                  onSelect={onSelect}
                  onRemove={onRemove}
                  showRemove
                  removeLabel={`從監聽清單移除 ${it.symbol}`}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      </div>
    );
  }

  return (
    <div>
      <div className="px-3.5 py-2 border-b border-line flex justify-end">
        <button
          type="button"
          onClick={() => setEditMode(true)}
          className="text-xs text-ink-dim hover:text-accent"
        >
          ✎ 編輯
        </button>
      </div>
      <ul>
        {items.map((it) => (
          <ItemRow
            key={it.symbol}
            item={{ symbol: it.symbol, name: it.name }}
            quote={quotes[it.symbol]}
            rules={rules}
            hitCounts={hitCounts}
            selectedSymbol={selectedSymbol}
            onSelect={onSelect}
            onRemove={onRemove}
            showRemove
            removeLabel={`從監聽清單移除 ${it.symbol}`}
          />
        ))}
      </ul>
    </div>
  );
}
