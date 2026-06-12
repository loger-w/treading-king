# 監控列表自訂排序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 書籤群組(sidebar)與各書籤內股票都能用滑鼠拖拉自訂順序,順序持久化在 `backend/data/config.json`。

**Architecture:** 後端 `watchlist_items` 加 `position` 欄位 + 兩個批次 reorder 端點(群組沿用既有 `sort_order`);前端用 `@dnd-kit/core` + `@dnd-kit/sortable` 拖拉,樂觀更新、失敗回滾。「訊號命中置頂」保留:置頂項目不參與拖拉、拖拉結果套用到完整順序時置頂項目保留原 slot。

**Tech Stack:** FastAPI + Pydantic、本機 JSON(ConfigStore)、React 18 + dnd-kit、pytest、vitest。

**Spec:** `docs/superpowers/specs/2026-06-12-watchlist-custom-order-design.md`

**注意事項:**
- ⚠️ 改後端前先確認 user 的 `uvicorn --reload` dev server 已停(每次存檔會重啟+重登富邦,登入風暴會被券商拒絕)。
- spec 寫「失敗 toast 提示」,但前端沒有任何 toast 機制 — 簡化為回滾(列表彈回原順序即為視覺提示)+ `console.warn`,不為此建 toast 系統(YAGNI)。
- 後端測試在 `backend/` 下跑:`python -m pytest tests/test_config_store.py -v`
- 前端測試在 `frontend/` 下跑:`npm run test`

**檔案地圖:**
- Modify: `backend/services/local_store/config_store.py` — add_item 補 position、新增 reorder_items / reorder_groups
- Modify: `backend/routes/bookmarks.py` — GET items 排序、PATCH reorder ×2
- Modify: `backend/tests/test_config_store.py`、`backend/tests/test_bookmarks_route.py`
- Create: `frontend/src/lib/reorder.ts`、`frontend/src/lib/reorder.test.ts`
- Modify: `frontend/src/lib/api.ts`、`frontend/src/hooks/useBookmarkItems.ts`、`frontend/src/hooks/useBookmarks.ts`、`frontend/src/components/BookmarksPanel.tsx`

---

### Task 1: ConfigStore — add_item 寫入 position + reorder_items / reorder_groups

**Files:**
- Modify: `backend/services/local_store/config_store.py:161-180`
- Test: `backend/tests/test_config_store.py`

- [ ] **Step 1: 寫失敗測試**(append 到 `test_config_store.py`)

```python
def test_add_item_assigns_position_new_on_top(tmp_path):
    # 為何重要:自訂排序後,新加入的股票要排最上面(position 最小)且不打亂既有手動順序
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    a = cfg.add_item(g["id"], "2330")
    b = cfg.add_item(g["id"], "2317")
    assert a["position"] == 0
    assert b["position"] == -1  # 比現有最小值更小 → 排最上面


def test_reorder_items_rewrites_positions(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    g = cfg.create_group("X")
    for s in ("2330", "2317", "2454"):
        cfg.add_item(g["id"], s)
    assert cfg.reorder_items(g["id"], ["2454", "2330", "2317"]) is True
    pos = {it["symbol"]: it["position"] for it in cfg.list_items(g["id"])}
    assert pos == {"2454": 0, "2330": 1, "2317": 2}
    # 為何重要:順序必須持久化 — 重載後不可退回加入時間排序
    cfg2 = ConfigStore(path)
    cfg2.load()
    pos2 = {it["symbol"]: it["position"] for it in cfg2.list_items(g["id"])}
    assert pos2 == pos


def test_reorder_items_rejects_symbol_mismatch(tmp_path):
    # 為何重要:另一視窗剛刪/加股票時,過期的 reorder 不可默默吃掉或產生孤兒 position
    cfg = ConfigStore(tmp_path / "config.json")
    cfg.load()
    g = cfg.create_group("X")
    cfg.add_item(g["id"], "2330")
    assert cfg.reorder_items(g["id"], ["2330", "9999"]) is False
    assert cfg.reorder_items(g["id"], []) is False
    assert cfg.list_items(g["id"])[0]["position"] == 0  # 原值未動


def test_reorder_groups_rewrites_sort_order(tmp_path):
    path = tmp_path / "config.json"
    cfg = ConfigStore(path)
    cfg.load()
    g0 = cfg.list_groups()[0]          # 預設「自選」
    g1 = cfg.create_group("A", sort_order=1)
    g2 = cfg.create_group("B", sort_order=2)
    assert cfg.reorder_groups([g2["id"], g0["id"], g1["id"]]) is True
    order = {g["id"]: g["sort_order"] for g in cfg.list_groups()}
    assert order == {g2["id"]: 0, g0["id"]: 1, g1["id"]: 2}
    # mismatch(缺一個 id)→ 拒絕
    assert cfg.reorder_groups([g2["id"], g0["id"]]) is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`): `python -m pytest tests/test_config_store.py -v`
Expected: 4 個新測試 FAIL(`KeyError: 'position'` / `AttributeError: reorder_items`)

- [ ] **Step 3: 實作**

`config_store.py` — `add_item`(line 161)改為:

```python
    def add_item(self, group_id: str, symbol: str, note: str | None = None) -> dict:
        for it in self._data["watchlist_items"]:
            if it["group_id"] == group_id and it["symbol"] == symbol:
                return it  # 同 (group, symbol) 不重複
        # position 越小越上面;新加入排最上 → 取現有最小值 - 1。
        # 舊資料可能沒有 position(讀取端 fallback added_at),這裡只看有 position 的。
        positions = [it["position"] for it in self._data["watchlist_items"]
                     if it["group_id"] == group_id and it.get("position") is not None]
        it = {"id": _new_id(), "group_id": group_id, "symbol": symbol,
              "added_at": _now_iso(), "note": note,
              "position": (min(positions) - 1) if positions else 0}
        self._data["watchlist_items"].append(it)
        self._persist()
        return it
```

在 `remove_item` 之後(line 180 後)新增:

```python
    def reorder_items(self, group_id: str, symbols: list[str]) -> bool:
        """以 symbols 的順序重寫該群組所有 position(0..n-1)。

        symbols 集合必須與群組現況完全一致 — 否則拒絕(回 False),
        避免過期的 reorder(另一視窗剛增刪)默默蓋掉資料。
        """
        items = [it for it in self._data["watchlist_items"] if it["group_id"] == group_id]
        if sorted(symbols) != sorted(it["symbol"] for it in items):
            return False
        pos = {s: i for i, s in enumerate(symbols)}
        for it in items:
            it["position"] = pos[it["symbol"]]
        self._persist()
        return True
```

在 `delete_group` 之後(line 149 後)新增:

```python
    def reorder_groups(self, ids: list[str]) -> bool:
        """以 ids 的順序重寫所有 user 群組的 sort_order(0..n-1)。集合不符拒絕。"""
        user_groups = [g for g in self._data["bookmark_groups"] if not g.get("is_system")]
        if sorted(ids) != sorted(g["id"] for g in user_groups):
            return False
        order = {gid: i for i, gid in enumerate(ids)}
        for g in user_groups:
            g["sort_order"] = order[g["id"]]
        self._persist()
        return True
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_config_store.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/local_store/config_store.py backend/tests/test_config_store.py
git commit -m "feat(store): watchlist_items 加 position + 群組/股票批次 reorder"
```

---

### Task 2: Route — GET items 改 position 排序 + 兩個 reorder 端點

**Files:**
- Modify: `backend/routes/bookmarks.py`
- Test: `backend/tests/test_bookmarks_route.py`

- [ ] **Step 1: 寫失敗測試**(append 到 `test_bookmarks_route.py`;沿用檔內既有 `local_store_tmp` + monkeypatch 模式)

```python
def _setup_group_with_items(monkeypatch, symbols):
    """共用前置:mock WS/CDP、註冊 symbols、批次加入預設書籤,回傳 group id。"""
    fake_pool = AsyncMock()
    monkeypatch.setattr("routes.bookmarks.get_ws_pool", lambda: fake_pool)
    monkeypatch.setattr("routes.bookmarks.get_cdp_service", lambda: AsyncMock())
    get_local_store().market.replace_symbols(
        [{"symbol": s, "name": s, "market": "TWSE", "is_etf": False, "is_active": True}
         for s in symbols])
    gid = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
               if not g["is_system"])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": symbols})
    return gid


def test_reorder_items_changes_get_order(local_store_tmp, monkeypatch):
    # 為何重要:這就是「自訂排序」的核心 contract — reorder 後 GET 要照新順序回
    gid = _setup_group_with_items(monkeypatch, ["2330", "2317", "2454"])
    r = client.patch(f"/api/bookmarks/{gid}/items/reorder",
                     json={"symbols": ["2317", "2454", "2330"]})
    assert r.status_code == 200
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got == ["2317", "2454", "2330"]


def test_reorder_items_mismatch_400(local_store_tmp, monkeypatch):
    gid = _setup_group_with_items(monkeypatch, ["2330"])
    r = client.patch(f"/api/bookmarks/{gid}/items/reorder",
                     json={"symbols": ["2330", "9999"]})
    assert r.status_code == 400


def test_new_item_appears_on_top(local_store_tmp, monkeypatch):
    # 為何重要:user 已確認「新加入排最上面」— 批次加入後再加一檔,新檔要在第一位
    gid = _setup_group_with_items(monkeypatch, ["2330", "2317"])
    get_local_store().market.replace_symbols(
        [{"symbol": s, "name": s, "market": "TWSE", "is_etf": False, "is_active": True}
         for s in ["2330", "2317", "2454"]])
    client.post(f"/api/bookmarks/{gid}/items", json={"symbols": ["2454"]})
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got[0] == "2454"


def test_items_without_position_fallback_added_at_desc(local_store_tmp, monkeypatch):
    # 為何重要:既有 config.json 沒有 position(spec 明定不遷移)— 舊資料要維持原本
    # 「加入時間新→舊」的順序、且排在有 position 的項目後面
    gid = _setup_group_with_items(monkeypatch, ["2330"])
    cfg = get_local_store().config
    cfg._data["watchlist_items"].extend([
        {"id": "old1", "group_id": gid, "symbol": "1101",
         "added_at": "2026-01-01T00:00:00+00:00", "note": None},
        {"id": "old2", "group_id": gid, "symbol": "1102",
         "added_at": "2026-02-01T00:00:00+00:00", "note": None},
    ])
    got = [it["symbol"] for it in client.get(f"/api/bookmarks/{gid}/items").json()["items"]]
    assert got == ["2330", "1102", "1101"]  # 有 position 在前;舊資料 added_at 新→舊


def test_reorder_groups_changes_list_order(local_store_tmp):
    g1 = client.post("/api/bookmarks", json={"name": "甲"}).json()["id"]
    g2 = client.post("/api/bookmarks", json={"name": "乙"}).json()["id"]
    g0 = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
              if g["name"] == "自選")
    r = client.patch("/api/bookmarks/reorder", json={"ids": [g2, g1, g0]})
    assert r.status_code == 200
    user_ids = [g["id"] for g in client.get("/api/bookmarks").json()["groups"]
                if not g["is_system"]]
    assert user_ids == [g2, g1, g0]
    # 系統書籤(大漲股)永遠在最後、不受影響
    assert client.get("/api/bookmarks").json()["groups"][-1]["is_system"] is True


def test_reorder_groups_mismatch_400(local_store_tmp):
    g0 = next(g["id"] for g in client.get("/api/bookmarks").json()["groups"]
              if not g["is_system"])
    r = client.patch("/api/bookmarks/reorder", json={"ids": [g0, "no-such-id"]})
    assert r.status_code == 400
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_bookmarks_route.py -v`
Expected: 新測試 FAIL(reorder 端點 404 / 順序不符);既有測試仍 PASS

- [ ] **Step 3: 實作 — `routes/bookmarks.py` 三處修改**

(a) **群組 reorder 端點 — 必須宣告在 `@router.patch("/api/bookmarks/{bid}")`(line 147)之前**,否則 FastAPI 依宣告順序把 `reorder` 當成 `{bid}` 吃掉回 404。插在 `create_bookmark`(line 144)之後:

```python
class GroupsReorder(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


# 注意:必須宣告在 PATCH /api/bookmarks/{bid} 之前 — 否則 "reorder" 被當成 {bid}
@router.patch("/api/bookmarks/reorder")
async def reorder_bookmarks(payload: GroupsReorder) -> dict:
    """批次重排 user 書籤群組。ids 必須與現有 user 群組集合完全一致。"""
    if not get_local_store().config.reorder_groups(payload.ids):
        raise HTTPException(400, detail={"error": "groups_mismatch"})
    return {"status": "ok"}
```

(b) **GET items 排序**(line 221-223)— 把:

```python
    # User 書籤 — 從 watchlist_items 補 symbols metadata,added_at desc
    items = store.config.list_items(bid)
    items = sorted(items, key=lambda it: it.get("added_at") or "", reverse=True)
```

改為:

```python
    # User 書籤 — 有 position 的照 position 升冪在前(自訂順序);
    # 舊資料沒有 position(不遷移),fallback added_at 新→舊接在後面
    items = store.config.list_items(bid)
    with_pos = sorted((it for it in items if it.get("position") is not None),
                      key=lambda it: it["position"])
    no_pos = sorted((it for it in items if it.get("position") is None),
                    key=lambda it: it.get("added_at") or "", reverse=True)
    items = with_pos + no_pos
```

(c) **items reorder 端點** — 插在 `remove_item`(line 295)之後:

```python
class ItemsReorder(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)


@router.patch("/api/bookmarks/{bid}/items/reorder")
async def reorder_bookmark_items(bid: str, payload: ItemsReorder) -> dict:
    """以 symbols 的順序重排書籤內股票。集合與現況不符回 400(過期請求不蓋資料)。"""
    _require_user_group(bid)  # 系統書籤擋 + 存在性
    if not get_local_store().config.reorder_items(bid, payload.symbols):
        raise HTTPException(400, detail={"error": "items_mismatch"})
    return {"status": "ok"}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_bookmarks_route.py tests/test_config_store.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routes/bookmarks.py backend/tests/test_bookmarks_route.py
git commit -m "feat(routes): 書籤群組/股票 reorder 端點 + items 改 position 排序"
```

---

### Task 3: 前端 — 安裝 dnd-kit、api.ts、純函式 + 測試

**Files:**
- Modify: `frontend/package.json`(npm install)
- Modify: `frontend/src/lib/api.ts:384-422`
- Create: `frontend/src/lib/reorder.ts`
- Test: `frontend/src/lib/reorder.test.ts`

- [ ] **Step 1: 安裝依賴**(在 `frontend/`)

```bash
npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities
```

- [ ] **Step 2: api.ts 加兩個呼叫** — 在 `bookmarks` 物件內 `move:` 之後加:

```typescript
    reorderItems: (id: string, symbols: string[]) =>
      fetchJSON<{ status: string }>(
        `/api/bookmarks/${encodeURIComponent(id)}/items/reorder`,
        { method: "PATCH", body: JSON.stringify({ symbols }) },
      ),
    reorderGroups: (ids: string[]) =>
      fetchJSON<{ status: string }>("/api/bookmarks/reorder", {
        method: "PATCH", body: JSON.stringify({ ids }),
      }),
```

- [ ] **Step 3: 寫失敗測試** — Create `frontend/src/lib/reorder.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { applyDragToOrder, partitionByHits } from "./reorder";

describe("applyDragToOrder", () => {
  // 為何重要:置頂(訊號命中)項目不進拖拉區,但它在「完整順序」裡佔有 slot;
  // 拖拉結果必須套回完整順序而不破壞置頂項目的手動位置
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

describe("partitionByHits", () => {
  const items = [{ symbol: "A" }, { symbol: "B" }, { symbol: "C" }];
  it("命中的置頂(命中數降冪),其餘維持原順序", () => {
    const hits: Record<string, number> = { B: 2, C: 5 };
    const { pinned, rest } = partitionByHits(items, (s) => hits[s] ?? 0);
    expect(pinned.map((i) => i.symbol)).toEqual(["C", "B"]);
    expect(rest.map((i) => i.symbol)).toEqual(["A"]);
  });
  it("無命中時全部都在 rest、順序不變", () => {
    const { pinned, rest } = partitionByHits(items, () => 0);
    expect(pinned).toEqual([]);
    expect(rest.map((i) => i.symbol)).toEqual(["A", "B", "C"]);
  });
});
```

- [ ] **Step 4: 跑測試確認失敗**

Run(在 `frontend/`): `npm run test`
Expected: FAIL — `Cannot find module './reorder'`

- [ ] **Step 5: 實作** — Create `frontend/src/lib/reorder.ts`:

```typescript
/**
 * 書籤列表排序純函式 — 抽出來測(專案無 hook 測試環境)。
 *
 * 概念:後端 position 給出「完整順序」;顯示時訊號命中的置頂(不可拖拉),
 * 其餘照完整順序排。拖拉只發生在非置頂區,但結果要套回完整順序送後端。
 */

/** 訊號命中的置頂(命中數降冪、同數維持原順序),其餘照原順序。 */
export function partitionByHits<T extends { symbol: string }>(
  items: T[],
  totalHits: (symbol: string) => number,
): { pinned: T[]; rest: T[] } {
  const pinned = items
    .filter((it) => totalHits(it.symbol) > 0)
    .sort((a, b) => totalHits(b.symbol) - totalHits(a.symbol));
  const rest = items.filter((it) => totalHits(it.symbol) === 0);
  return { pinned, rest };
}

/**
 * 把「拖 active 放到 over 的位置」套到完整順序(同 dnd-kit arrayMove 語意)。
 * 置頂項目不會是 over(不在拖拉區),它們的 slot 自然保留。
 */
export function applyDragToOrder(order: string[], active: string, over: string): string[] {
  const from = order.indexOf(active);
  const to = order.indexOf(over);
  if (from < 0 || to < 0 || from === to) return order;
  const next = [...order];
  next.splice(from, 1);
  next.splice(to, 0, active);
  return next;
}
```

- [ ] **Step 6: 跑測試確認通過**

Run: `npm run test`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/api.ts frontend/src/lib/reorder.ts frontend/src/lib/reorder.test.ts
git commit -m "feat(frontend): dnd-kit 依賴 + reorder API/純函式"
```

---

### Task 4: 前端 hooks — 樂觀 reorder + 回滾

**Files:**
- Modify: `frontend/src/hooks/useBookmarkItems.ts`
- Modify: `frontend/src/hooks/useBookmarks.ts`

- [ ] **Step 1: `useBookmarkItems.ts`** — 在 `removeItem`(line 34-38)之後加,並把 `reorder` 加進回傳值:

```typescript
  // 樂觀重排:先照新順序重組本地 items、再打 API;失敗回滾(列表彈回即為提示)。
  const reorder = useCallback(async (symbols: string[]) => {
    if (!groupId) return;
    const prev = items;
    const bySym = new Map(items.map((it) => [it.symbol, it]));
    setItems(symbols.flatMap((s) => bySym.get(s) ?? []));
    try {
      await api.bookmarks.reorderItems(groupId, symbols);
    } catch (e) {
      console.warn("reorder items failed:", e);
      setItems(prev);
      await refresh();  // 400 = 集合過期(他處剛增刪)— 重抓伺服器現況
    }
  }, [groupId, items, refresh]);

  return { items, loading, refresh, removeItem, reorder };
```

- [ ] **Step 2: `useBookmarks.ts`** — 在 `remove`(line 41-44)之後加,並把 `reorderGroups` 加進回傳值:

```typescript
  // 樂觀重排 user 群組(系統書籤固定殿後,不在 ids 內);失敗回滾。
  const reorderGroups = useCallback(async (ids: string[]) => {
    const prev = groups;
    const byId = new Map(groups.map((g) => [g.id, g]));
    const system = groups.filter((g) => g.is_system);
    setGroups([...ids.flatMap((i) => byId.get(i) ?? []), ...system]);
    try {
      await api.bookmarks.reorderGroups(ids);
    } catch (e) {
      console.warn("reorder groups failed:", e);
      setGroups(prev);
      await refresh();
    }
  }, [groups, refresh]);

  return { groups, loading, error, refresh, create, rename, remove, reorderGroups };
```

- [ ] **Step 3: 型別檢查**

Run(在 `frontend/`): `npx tsc -b`
Expected: 無錯誤

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useBookmarkItems.ts frontend/src/hooks/useBookmarks.ts
git commit -m "feat(frontend): 書籤群組/股票樂觀 reorder hooks"
```

---

### Task 5: 前端 UI — SingleListView 與 sidebar 拖拉

**Files:**
- Modify: `frontend/src/components/BookmarksPanel.tsx`

- [ ] **Step 1: imports + ItemRow 接拖拉 props**

檔頭加:

```typescript
import {
  DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext, useSortable, verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { applyDragToOrder, partitionByHits } from "../lib/reorder";
```

`ItemRow` 的 props 加三個可選欄位(既有呼叫端不用改):

```typescript
  containerRef?: (node: HTMLElement | null) => void;
  containerStyle?: React.CSSProperties;
  containerProps?: React.HTMLAttributes<HTMLLIElement>;
```

`<li>` 開頭改為:

```tsx
    <li
      ref={containerRef}
      style={containerStyle}
      {...containerProps}
      className={[ /* 原 className 不變 */ ].join(" ")}
      onClick={() => onSelect(item.symbol)}
    >
```

(注意:`{...containerProps}` 要放在 `className`/`onClick` **之前**,避免 dnd-kit 的 attributes 蓋掉它們。)

- [ ] **Step 2: SortableItemRow wrapper** — 加在 `ItemRow` 定義後:

```tsx
// 拖拉包裝:把 dnd-kit 的 ref/transform/listeners 餵給 ItemRow 的 li。
// PointerSensor 有 distance 啟動門檻,點擊選股不受影響。
function SortableItemRow(props: Parameters<typeof ItemRow>[0]) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: props.item.symbol });
  return (
    <ItemRow
      {...props}
      containerRef={setNodeRef}
      containerStyle={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : undefined,
      }}
      containerProps={{ ...attributes, ...listeners }}
    />
  );
}
```

- [ ] **Step 3: SingleListView 拖拉**

props 加 `items` 之外的 `onReorder: (symbols: string[]) => void;`。
body 改:置頂用 `partitionByHits`(取代原本的 `sorted` useMemo)、rest 進 `SortableContext`:

```tsx
  const { pinned, rest } = useMemo(
    () => partitionByHits(items, (s) => totalHitsForSymbol(s, hitCounts)),
    [items, hitCounts],
  );
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    onReorder(applyDragToOrder(items.map((it) => it.symbol), String(active.id), String(over.id)));
  }
```

`<ul>` 渲染改為:置頂項目用一般 `ItemRow`,rest 在系統書籤時也用一般 `ItemRow`(大漲股不可拖),user 書籤才包 DnD:

```tsx
      <ul>
        {pinned.map((it) => (
          <ItemRow key={it.symbol} item={it} quote={quotes[it.symbol]} rules={rules}
            hitCounts={hitCounts} selectedSymbol={selectedSymbol} onSelect={onSelect}
            onRemove={!isSystem ? onRemove : undefined} showRemove={!isSystem} />
        ))}
        {isSystem ? (
          rest.map((it) => (
            <ItemRow key={it.symbol} item={it} quote={quotes[it.symbol]} rules={rules}
              hitCounts={hitCounts} selectedSymbol={selectedSymbol} onSelect={onSelect}
              showRemove={false} />
          ))
        ) : (
          <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
            <SortableContext items={rest.map((it) => it.symbol)}
              strategy={verticalListSortingStrategy}>
              {rest.map((it) => (
                <SortableItemRow key={it.symbol} item={it} quote={quotes[it.symbol]}
                  rules={rules} hitCounts={hitCounts} selectedSymbol={selectedSymbol}
                  onSelect={onSelect} onRemove={onRemove} showRemove />
              ))}
            </SortableContext>
          </DndContext>
        )}
      </ul>
```

`BookmarksPanel` 呼叫端:從 `useBookmarkItems` 解構出 `reorder`,傳 `onReorder={reorder}` 給 `SingleListView`。

- [ ] **Step 4: Sidebar 群組拖拉**

`SidebarItem` 同 ItemRow 模式加三個可選 props(`containerRef`/`containerStyle`/`containerProps`,spread 在 `<button>` 上、放在既有 props 之前),並加 wrapper:

```tsx
function SortableSidebarItem(props: Parameters<typeof SidebarItem>[0] & { id: string }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: props.id });
  return (
    <SidebarItem
      {...props}
      containerRef={setNodeRef}
      containerStyle={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : undefined,
      }}
      containerProps={{ ...attributes, ...listeners }}
    />
  );
}
```

`BookmarksPanel` 內(從 `useBookmarks` 解構出 `reorderGroups`):

```tsx
  const userGroups = useMemo(() => groups.filter((g) => !g.is_system), [groups]);
  const sidebarSensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  function handleGroupDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    reorderGroups(applyDragToOrder(userGroups.map((g) => g.id), String(active.id), String(over.id)));
  }
```

Sidebar 的 `{groups.map(...)}`(line 157-166)改為:user 群組包 DnD、系統書籤照舊(固定殿後):

```tsx
          <DndContext sensors={sidebarSensors} onDragEnd={handleGroupDragEnd}>
            <SortableContext items={userGroups.map((g) => g.id)}
              strategy={verticalListSortingStrategy}>
              {userGroups.map((g) => (
                <SortableSidebarItem key={g.id} id={g.id} label={g.name} count={g.count}
                  selected={selectedGroupId === g.id} onClick={() => pickGroup(g.id)} />
              ))}
            </SortableContext>
          </DndContext>
          {groups.filter((g) => g.is_system).map((g) => (
            <SidebarItem key={g.id} label={g.name} count={g.count}
              selected={selectedGroupId === g.id} system onClick={() => pickGroup(g.id)} />
          ))}
```

(「監聽」「全部」兩個固定入口在 DndContext 之外,不受影響。)

- [ ] **Step 5: 型別檢查 + 全部測試**

Run(在 `frontend/`): `npx tsc -b && npm run test`
Run(在 `backend/`): `python -m pytest tests/ -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BookmarksPanel.tsx
git commit -m "feat(frontend): 書籤群組與股票列表拖拉排序"
```

---

### Task 6: 手動驗收

- [ ] **Step 1: 啟動**(確認 user 沒有自己的 dev server 在跑,或請 user 自己操作)

```powershell
.\start.ps1
```

- [ ] **Step 2: 驗收清單**

1. 單一書籤內拖一檔股票到別的位置 → 順序改變;重新整理頁面 → 順序保留
2. 點擊股票(不拖)→ 仍正常選取、右側圖表切換
3. × 移除、編輯模式 → 行為不變
4. sidebar 拖書籤群組 → 順序改變且重整保留;「監聽」「全部」「大漲股」位置固定不可拖
5. 加入新股票 → 出現在該書籤最上方
6. 大漲股(系統書籤)→ 不可拖
7. 後端關掉再拖 → 列表彈回原順序(回滾)
8. `backend/data/config.json` 裡 `watchlist_items` 有 `position`、群組 `sort_order` 被改寫

- [ ] **Step 3: 完成後** 用 superpowers:finishing-a-development-branch 收尾(PR 或 merge)
