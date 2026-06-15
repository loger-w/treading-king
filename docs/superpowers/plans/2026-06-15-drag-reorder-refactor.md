# 拖拉排序重構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將拖拉排序改為「編輯模式才能拖」，取消訊號命中置頂，監聽清單加入 position + 拖拉排序支援。

**Architecture:** 後端 config_store 加 monitor reorder 方法 + route 端點；前端 SingleListView 拆掉隨時拖 + 置頂，BookmarkEditMode 加 dnd-kit 拖拉，MonitorListView 加編輯模式 + 拖拉。Sidebar 群組拖拉不動。

**Tech Stack:** FastAPI, Pydantic, local JSON store, React, @dnd-kit/core + @dnd-kit/sortable, Vitest, pytest

**Spec:** `docs/superpowers/specs/2026-06-15-drag-reorder-refactor-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/services/local_store/config_store.py` | Modify | add_monitor 加 position, 新增 reorder_monitor |
| `backend/routes/monitor_list.py` | Modify | GET 改排序, 新增 PATCH reorder 端點 |
| `backend/tests/test_config_store.py` | Modify | reorder_monitor 測試 |
| `backend/tests/test_monitor_list_route.py` | Modify | PATCH reorder 路由測試 |
| `frontend/src/lib/api.ts` | Modify | monitorList.reorder 方法 |
| `frontend/src/hooks/useMonitorList.tsx` | Modify | reorder callback + seqRef |
| `frontend/src/lib/reorder.ts` | Modify | 刪 partitionByHits, 清理註解 |
| `frontend/src/lib/reorder.test.ts` | Modify | 刪 partitionByHits 測試, 清理註解 |
| `frontend/src/components/BookmarksPanel.tsx` | Modify | SingleListView 拆拖拉+置頂, MonitorListView 加編輯+拖拉, 接線 |
| `frontend/src/components/BookmarkEditMode.tsx` | Modify | 加 dnd-kit 拖拉排序 |

---

### Task 1: 後端 — config_store 加 monitor position + reorder

**Files:**
- Modify: `backend/services/local_store/config_store.py:278-295`
- Test: `backend/tests/test_config_store.py`

- [ ] **Step 1: 寫 reorder_monitor 的失敗測試**

在 `backend/tests/test_config_store.py` 末尾加：

```python
def test_reorder_monitor_rewrites_positions(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    cfg.add_monitor("2330")
    cfg.add_monitor("2317")
    cfg.add_monitor("2454")
    assert cfg.reorder_monitor(["2454", "2330", "2317"]) is True
    pos = {m["symbol"]: m["position"] for m in cfg.list_monitor()}
    assert pos == {"2454": 0, "2330": 1, "2317": 2}
    cfg2 = ConfigStore(path)
    cfg2.load()
    pos2 = {m["symbol"]: m["position"] for m in cfg2.list_monitor()}
    assert pos2 == pos


def test_reorder_monitor_rejects_symbol_mismatch(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    cfg.add_monitor("2330")
    assert cfg.reorder_monitor(["2330", "9999"]) is False
    assert cfg.reorder_monitor([]) is False


def test_add_monitor_assigns_position_new_on_top(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    a = cfg.add_monitor("2330")
    b = cfg.add_monitor("2317")
    assert a["position"] == 0
    assert b["position"] == -1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_config_store.py::test_reorder_monitor_rewrites_positions tests/test_config_store.py::test_reorder_monitor_rejects_symbol_mismatch tests/test_config_store.py::test_add_monitor_assigns_position_new_on_top -v`
Expected: 3 FAIL (reorder_monitor 不存在, add_monitor 回傳無 position)

- [ ] **Step 3: 實作 add_monitor 加 position + reorder_monitor**

修改 `backend/services/local_store/config_store.py`：

`add_monitor` (line 278-285) 改為加 position 欄位：

```python
def add_monitor(self, symbol: str) -> dict:
    for m in self._data["monitor_list"]:
        if m["symbol"] == symbol:
            return m
    positions = [m["position"] for m in self._data["monitor_list"]
                 if m.get("position") is not None]
    m = {"symbol": symbol, "added_at": _now_iso(),
         "position": (min(positions) - 1) if positions else 0}
    self._data["monitor_list"].append(m)
    self._persist()
    return m
```

在 `remove_monitor` 之後 (line 295)、`# ---- export / import ----` 之前，新增：

```python
def reorder_monitor(self, symbols: list[str]) -> bool:
    items = self._data["monitor_list"]
    if sorted(symbols) != sorted(m["symbol"] for m in items):
        return False
    pos = {s: i for i, s in enumerate(symbols)}
    for m in items:
        m["position"] = pos[m["symbol"]]
    self._persist()
    return True
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && python -m pytest tests/test_config_store.py -v`
Expected: ALL PASS（包含既有測試）

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/config_store.py backend/tests/test_config_store.py
git commit -m "feat(store): monitor_list 加 position 欄位 + reorder_monitor 方法"
```

---

### Task 2: 後端 — monitor_list route 加 PATCH reorder + GET 改排序

**Files:**
- Modify: `backend/routes/monitor_list.py`
- Test: `backend/tests/test_monitor_list_route.py`

- [ ] **Step 1: 寫 route 測試**

在 `backend/tests/test_monitor_list_route.py` 末尾加：

```python
def test_reorder_monitor_list(local_store_tmp, monkeypatch):
    """PATCH reorder → 200,GET 回傳新順序。"""
    store = get_local_store()
    store.config.add_monitor("2330")
    store.config.add_monitor("2317")
    store.config.add_monitor("2454")

    r = client.patch("/api/monitor_list/reorder", json={"symbols": ["2454", "2330", "2317"]})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    syms = [it["symbol"] for it in client.get("/api/monitor_list").json()["items"]]
    assert syms == ["2454", "2330", "2317"]


def test_reorder_monitor_list_mismatch_returns_400(local_store_tmp):
    """symbols 與現況不符 → 400。"""
    get_local_store().config.add_monitor("2330")
    r = client.patch("/api/monitor_list/reorder", json={"symbols": ["2330", "9999"]})
    assert r.status_code == 400


def test_list_monitor_respects_position_order(local_store_tmp):
    """有 position 的 items 照 position 排序,而非 added_at。"""
    store = get_local_store()
    store.config.add_monitor("2330")
    store.config.add_monitor("2317")
    store.config.add_monitor("2454")
    store.config.reorder_monitor(["2317", "2454", "2330"])

    syms = [it["symbol"] for it in client.get("/api/monitor_list").json()["items"]]
    assert syms == ["2317", "2454", "2330"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && python -m pytest tests/test_monitor_list_route.py::test_reorder_monitor_list tests/test_monitor_list_route.py::test_list_monitor_respects_position_order -v`
Expected: FAIL (PATCH 405 Method Not Allowed; GET 排序錯)

- [ ] **Step 3: 實作 route 改動**

修改 `backend/routes/monitor_list.py`：

1) 在 `MonitorListAdd` class 下面 (line 29)，加 reorder model：

```python
class MonitorListReorder(BaseModel):
    symbols: list[str] = Field(min_length=1)
```

2) 把 GET handler (line 31-37) 的排序改成 position-based（fallback added_at desc 相容舊資料）：

```python
@router.get("/api/monitor_list")
async def list_monitor() -> dict:
    store = get_local_store()
    items = sorted(
        store.config.list_monitor(),
        key=lambda m: (m.get("position") if m.get("position") is not None else float("inf"),
                       m["added_at"]),
    )
    out = [enrich_item(m, store.market) for m in items]
    return {"items": out, "count": len(out)}
```

3) 在 DELETE route **之前** (line 70 之前) 加 PATCH reorder 端點（路由順序關鍵：reorder 必須在 `/{symbol}` 之前）：

```python
@router.patch("/api/monitor_list/reorder")
async def reorder_monitor(payload: MonitorListReorder) -> dict:
    if not get_local_store().config.reorder_monitor(payload.symbols):
        raise HTTPException(400, detail={"error": "symbols_mismatch"})
    return {"status": "ok"}
```

- [ ] **Step 4: 跑全部 monitor_list 測試確認通過**

Run: `cd backend && python -m pytest tests/test_monitor_list_route.py -v`
Expected: ALL PASS

注意 `test_list_returns_newest_first` 可能會失敗，因為排序邏輯改了。如果新加的 item 有 position，它會照 position 排而非 added_at。檢查：
- `add_monitor` 新加 item position 是遞減的（第一個=0, 第二個=-1, ...），所以後加的 position 更小、排在前面，跟原本 added_at desc 效果一致。✓

- [ ] **Step 5: Commit**

```bash
git add backend/routes/monitor_list.py backend/tests/test_monitor_list_route.py
git commit -m "feat(api): PATCH /api/monitor_list/reorder + GET 改 position 排序"
```

---

### Task 3: 前端 — api.ts + useMonitorList 加 reorder

**Files:**
- Modify: `frontend/src/lib/api.ts:371-382`
- Modify: `frontend/src/hooks/useMonitorList.tsx`

- [ ] **Step 1: api.ts 加 monitorList.reorder**

在 `frontend/src/lib/api.ts` 的 `monitorList` object (line 378-381, `remove` 方法之後) 加：

```typescript
    reorder: (symbols: string[]) =>
      fetchJSON<{ status: string }>("/api/monitor_list/reorder", {
        method: "PATCH",
        body: JSON.stringify({ symbols }),
      }),
```

- [ ] **Step 2: useMonitorList.tsx 加 reorder callback**

修改 `frontend/src/hooks/useMonitorList.tsx`：

1) import 加 `useRef`（已有），加 `ApiError`：

```typescript
import { api, ApiError, type MonitorListItem } from "../lib/api";
```

2) interface 加 reorder：

```typescript
interface MonitorListContextValue {
  items: MonitorListItem[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  add: (symbol: string) => Promise<void>;
  remove: (symbol: string) => Promise<void>;
  reorder: (symbols: string[]) => Promise<void>;
}
```

3) Provider body 加 seqRef + reorder callback（在 `remove` callback 之後）：

```typescript
  const seqRef = useRef(0);
```

refresh callback 裡加 seqRef guard（改現有的 refresh）：

```typescript
  const refresh = useCallback(async () => {
    const mySeq = ++seqRef.current;
    try {
      setError(null);
      const r = await api.monitorList.list();
      if (mySeq === seqRef.current) setItems(r.items);
    } catch (e) {
      if (mySeq === seqRef.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
  }, []);
```

reorder callback（在 remove 之後）：

```typescript
  const reorder = useCallback(async (symbols: string[]) => {
    seqRef.current++;
    const prev = items;
    const bySymbol = new Map(items.map((it) => [it.symbol, it]));
    setItems(symbols.flatMap((s) => bySymbol.get(s) ?? []));
    try {
      await api.monitorList.reorder(symbols);
    } catch (e) {
      console.warn("reorder monitor failed:", e);
      setItems(prev);
      if (e instanceof ApiError) await refresh();
    }
  }, [items, refresh]);
```

4) useMemo value 加 reorder：

```typescript
  const value = useMemo(
    () => ({ items, loading, error, refresh, add, remove, reorder }),
    [items, loading, error, refresh, add, remove, reorder],
  );
```

- [ ] **Step 3: 確認 TypeScript 編譯沒問題**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/hooks/useMonitorList.tsx
git commit -m "feat(frontend): api + useMonitorList 加 reorder 支援"
```

---

### Task 4: 前端 — reorder.ts 刪 partitionByHits + 清理

**Files:**
- Modify: `frontend/src/lib/reorder.ts`
- Modify: `frontend/src/lib/reorder.test.ts`

- [ ] **Step 1: 刪 partitionByHits 及其測試**

`frontend/src/lib/reorder.ts` 改為：

```typescript
import { arrayMove } from "@dnd-kit/sortable";

/**
 * 書籤/監聽列表排序純函式 — 抽出來測(專案無 hook 測試環境)。
 */

/**
 * 把「拖 active 放到 over 的位置」套到完整順序。搬移本體用 dnd-kit 的
 * arrayMove — 與 SortableContext 拖拉預覽的位移計算同一份語意,不自製。
 */
export function applyDragToOrder(order: string[], active: string, over: string): string[] {
  const from = order.indexOf(active);
  const to = order.indexOf(over);
  if (from < 0 || to < 0 || from === to) return order;
  return arrayMove(order, from, to);
}
```

`frontend/src/lib/reorder.test.ts` 改為：

```typescript
import { describe, expect, it } from "vitest";
import { applyDragToOrder } from "./reorder";

describe("applyDragToOrder", () => {
  it("把 active 移到 over 的位置,其餘相對順序不變", () => {
    expect(applyDragToOrder(["A", "B", "C", "D"], "D", "B")).toEqual(["A", "D", "B", "C"]);
    expect(applyDragToOrder(["A", "B", "C", "D"], "A", "C")).toEqual(["B", "C", "A", "D"]);
  });
  it("active/over 不存在或相同時回傳原順序", () => {
    const order = ["A", "B"];
    expect(applyDragToOrder(order, "X", "B")).toBe(order);
    expect(applyDragToOrder(order, "A", "A")).toBe(order);
  });
});
```

- [ ] **Step 2: 跑前端測試確認通過**

Run: `cd frontend && npx vitest run`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/reorder.ts frontend/src/lib/reorder.test.ts
git commit -m "refactor: 刪 partitionByHits 置頂邏輯"
```

---

### Task 5: 前端 — BookmarkEditMode 加 dnd-kit 拖拉

**Files:**
- Modify: `frontend/src/components/BookmarkEditMode.tsx`

- [ ] **Step 1: 加 dnd-kit imports 和 Props**

`BookmarkEditMode.tsx` 頂部 import 加：

```typescript
import { useMemo } from "react";
import {
  DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { applyDragToOrder } from "../lib/reorder";
```

注意：`useMemo` 已在現有 import 裡（line 1 `useEffect, useMemo, useState`）。只需加 dnd-kit 和 reorder imports。

Props interface 加 `onReorder`：

```typescript
interface Props {
  group: BookmarkGroup;
  items: BookmarkItem[];
  groups: BookmarkGroup[];
  quotes: Record<string, WatchlistQuote>;
  onExit: () => void;
  onChanged: () => Promise<void>;
  onReorder: (symbols: string[]) => void;
}
```

- [ ] **Step 2: 元件內加 dnd 接線**

在 `BookmarkEditMode` function body 開頭（解構 props 後），加：

```typescript
  const itemIds = useMemo(() => items.map((it) => it.symbol), [items]);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    onReorder(applyDragToOrder(itemIds, String(active.id), String(over.id)));
  }
```

- [ ] **Step 3: `<ul>` 包 DndContext + SortableContext，`<li>` 改 SortableEditRow**

把現有 `<ul>` (line 174) 改成：

```tsx
      <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
        <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
          <ul>
            {items.map((it) => (
              <SortableEditRow key={it.symbol} id={it.symbol} item={it} isChecked={selected.has(it.symbol)} isJustAdded={recentlyAdded.has(it.symbol)} quote={quotes[it.symbol]} onToggle={toggle} />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
```

在 `BookmarkEditMode` 之外（file 底部），加 `SortableEditRow` 元件：

```tsx
function SortableEditRow({ id, item, isChecked, isJustAdded, quote, onToggle }: {
  id: string;
  item: BookmarkItem;
  isChecked: boolean;
  isJustAdded: boolean;
  quote: WatchlistQuote | undefined;
  onToggle: (symbol: string) => void;
}) {
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const pct = quote?.changePct ?? null;
  const priceCls = pct == null ? "text-ink-dim"
    : pct > 0 ? "text-bull"
    : pct < 0 ? "text-bear" : "text-ink-dim";

  return (
    <li
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : undefined,
      }}
      {...listeners}
      onClick={() => onToggle(item.symbol)}
      className={[
        "relative pl-10 pr-3.5 py-3 border-b border-line cursor-pointer transition-colors",
        isChecked ? "bg-bg-card" : "hover:bg-bg-card/40",
        isJustAdded ? "animate-bm-highlight" : "",
      ].join(" ")}
    >
      <span className={[
        "absolute left-3.5 top-4 w-3.5 h-3.5 border flex items-center justify-center text-bg text-2xs font-bold",
        isChecked ? "bg-accent border-accent" : "border-line-strong",
      ].join(" ")}>
        {isChecked && "✓"}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="text-[17px] font-medium text-ink shrink-0">{item.symbol}</span>
        <span className="text-sm text-ink-muted truncate flex-1">{item.name ?? "(無名稱)"}</span>
        <span className={`text-sm tabular-nums shrink-0 ${priceCls}`}>
          {quote?.price != null ? quote.price.toFixed(2) : "—"}
          {pct != null && (
            <span className="ml-1 text-xs">
              {pct > 0 ? "+" : ""}{pct.toFixed(2)}%
            </span>
          )}
        </span>
      </div>
    </li>
  );
}
```

- [ ] **Step 4: 確認 TypeScript 編譯**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors（此時 BookmarksPanel 會報錯因為 `onReorder` prop 尚未傳入，但 Task 7 會修）

若有 BookmarksPanel 的錯誤是預期的，先不管。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/BookmarkEditMode.tsx
git commit -m "feat(ui): BookmarkEditMode 加 dnd-kit 拖拉排序"
```

---

### Task 6: 前端 — SingleListView 移除隨時拖 + 移除置頂

**Files:**
- Modify: `frontend/src/components/BookmarksPanel.tsx` (SingleListView 部分)

- [ ] **Step 1: SingleListView 清理 — 移除 partitionByHits + frozen + DndContext**

修改 `BookmarksPanel.tsx` 的 `SingleListView` function (line 425-536)。

移除：
- Line 10: `partitionByHits` from import（只保留 `applyDragToOrder`）
- Line 444-448: `partitionByHits` 呼叫、`frozen/setFrozen` state、解構
- Line 450-451: `frozen` 相關
- Line 453: `restIds` memo
- Line 454: `sensors` (不再需要)
- Line 455-462: `handleDragEnd`（含 `setFrozen(null)`）
- Line 482-494: pinned items 渲染區塊
- Line 508-532: `DndContext`/`SortableContext` 包裹

改成純 `<ul>` 列表，所有 items 直接用 `ItemRow` 渲染（非 `SortableItemRow`）：

```tsx
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
            onRemove={!isSystem ? onRemove : undefined}
            showRemove={!isSystem}
          />
        ))}
      </ul>
    </div>
  );
}
```

注意：`onReorder` prop 從 SingleListView 移除（不再需要，拖拉只在 EditMode 裡）。

- [ ] **Step 2: SingleListView 呼叫端清理**

在 BookmarksPanel 的 return JSX 裡，`SingleListView` 的呼叫 (line 280-298) 移除 `onReorder` prop：

移除這行：
```tsx
              onReorder={reorderItemsEverywhere}
```

同時 `reorderItemsEverywhere` function (line 137-140) 不再被 SingleListView 使用，但會被 BookmarkEditMode 使用（Task 7），所以保留。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BookmarksPanel.tsx
git commit -m "refactor(ui): SingleListView 移除隨時拖拉 + 訊號置頂"
```

---

### Task 7: 前端 — BookmarksPanel 接線（EditMode onReorder + MonitorListView 編輯模式）

**Files:**
- Modify: `frontend/src/components/BookmarksPanel.tsx`

- [ ] **Step 1: BookmarkEditMode 傳入 onReorder**

在 BookmarksPanel return JSX 中，`BookmarkEditMode` 的呼叫 (原 line 270-278) 加 `onReorder` prop：

```tsx
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
```

- [ ] **Step 2: useMonitorList 解構加 reorder**

修改 line 71：

```tsx
  const { items: monitorItems, remove: removeFromMonitor, reorder: reorderMonitor } = useMonitorList();
```

- [ ] **Step 3: MonitorListView 加編輯模式 + 拖拉**

整個 `MonitorListView` function (原 line 640-672) 改寫：

```tsx
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
```

- [ ] **Step 4: MonitorListView 呼叫端傳入 onReorder**

在 BookmarksPanel return JSX 中 `MonitorListView` 呼叫處加 `onReorder` prop：

```tsx
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
```

- [ ] **Step 5: 需要的 import 確認**

確認 `BookmarksPanel.tsx` 頂部的 dnd-kit import 有 `DragEndEvent`（原本應該已有），且 `applyDragToOrder` import 已去掉 `partitionByHits`（Task 6 做過）。

MonitorListView 用到的 `useState`, `useMemo` 已在檔案頂部 import。
`DndContext`, `SortableContext`, `verticalListSortingStrategy`, `DragEndEvent` 已在檔案頂部 import（sidebar 群組拖拉在用）。

- [ ] **Step 6: 確認 TypeScript 編譯**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/BookmarksPanel.tsx
git commit -m "feat(ui): MonitorListView 編輯模式 + 拖拉, EditMode 接 onReorder"
```

---

### Task 8: 前端 — ItemRow 清理 hasHit marker

**Files:**
- Modify: `frontend/src/components/BookmarksPanel.tsx` (ItemRow 部分)

- [ ] **Step 1: ItemRow 移除 hasHit 相關 dead code**

在 `ItemRow` function 內 (原 line 542-622)：

移除：
- `const totalHits = totalHitsForSymbol(item.symbol, hitCounts);` (原 line 558)
- `const hasHit = totalHits > 0;` (原 line 559)
- `const markerBg = isDown ? "bg-bear" : "bg-accent";` (原 line 567)
- hasHit marker bar JSX (原 line 581-583)：
  ```tsx
      {hasHit && !isSel && (
        <span className={`absolute left-0 top-4 w-[3px] h-[22px] ${markerBg}`} aria-hidden />
      )}
  ```

保留：
- `hitCounts` prop（仍被 SignalChip 的 `hitCounts[item.symbol]?.[r.id]` 使用）
- `totalHitsForSymbol` function definition（line 62-65）— 雖然目前 ItemRow 不再呼叫它，但先保留；如果 tsc 報 unused，再刪

- [ ] **Step 2: 檢查 totalHitsForSymbol 是否還有呼叫者**

搜尋 `totalHitsForSymbol` 的所有用法。如果除了定義之外沒有任何呼叫者，一併刪除 function definition (line 62-65)。

- [ ] **Step 3: 確認 TypeScript 編譯**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/BookmarksPanel.tsx
git commit -m "refactor(ui): ItemRow 移除 hasHit marker dead code"
```

---

### Task 9: 全量驗證

**Files:** 全部

- [ ] **Step 1: 跑全部後端測試**

Run: `cd backend && python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: 跑全部前端測試**

Run: `cd frontend && npx vitest run`
Expected: ALL PASS

- [ ] **Step 3: TypeScript 無錯誤**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: 啟動 dev server 手動驗**

Run: `.\start.ps1`

手動驗證清單：
- [ ] 書籤瀏覽模式：點擊選股正常，**不能拖拉**
- [ ] 書籤「✎ 編輯」→ 進入編輯模式 → 可拖拉排序 → 拖完放開順序更新
- [ ] 書籤編輯模式：checkbox 勾選 + 移動/複製/刪除仍正常
- [ ] 書籤編輯模式：「✕ 結束編輯」退回瀏覽
- [ ] 監聽清單瀏覽模式：點擊選股正常，**不能拖拉**
- [ ] 監聽清單「✎ 編輯」→ 進入編輯模式 → 可拖拉排序
- [ ] 監聽清單編輯模式：× 移除按鈕仍正常
- [ ] 監聽清單重排後重新整理頁面，順序持久化
- [ ] Sidebar 書籤群組拖拉仍正常（不受影響）
- [ ] 「全部」view 正常顯示（無拖拉）
- [ ] 訊號命中的股票不再置頂，位置照 position 排
- [ ] 系統書籤列表不出現「✎ 編輯」按鈕

- [ ] **Step 5: 最終 Commit（如有修正）**

```bash
git add -A
git commit -m "fix: 拖拉排序重構全量驗證修正"
```
