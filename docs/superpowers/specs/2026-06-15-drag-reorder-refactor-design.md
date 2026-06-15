# 拖拉排序重構：編輯模式 + 監聽清單支援

> Date: 2026-06-15
> Status: Draft
> Scope: 書籤列表 + 監聽清單的拖拉排序 UX 重構

---

## 動機

現有拖拉排序有三個問題：

1. **難用** — 瀏覽模式下隨時可拖（PointerSensor distance:6），容易誤觸、跟「點擊選股」混淆
2. **不是每筆都能拖** — 訊號命中的股票被 `partitionByHits` 強制置頂、不可拖拉
3. **監聽清單不能拖** — 排序寫死 `added_at` 降冪，無 position 欄位

## 設計決策

- **拖拉只在「編輯模式」內** — 正常瀏覽時點擊 = 選股看圖，完全不能拖
- **完全取消訊號命中置頂** — 所有股票照 position 排，命中只靠色條 / SignalChip 視覺提示
- **監聽清單加 position + 編輯模式** — 跟書籤同一套 pattern
- **Sidebar 書籤群組拖拉不動** — 那是不同層級、不受影響

---

## 改動範圍

### 1. 後端：監聽清單加 position

**config_store.py**

- `add_monitor`：新建 monitor item 加 `position` 欄位（`min(existing) - 1`，新加排最上，同 `add_item` pattern）
- 新增 `reorder_monitor(symbols: list[str]) -> bool`：驗證集合一致 → 寫入 `position` 0..n-1 → persist。Mirror `reorder_items`

**monitor_list.py**

- `GET /api/monitor_list`：排序改用 position（fallback `added_at` desc，相容舊資料無 position 的情況）
- 新增 `PATCH /api/monitor_list/reorder` 端點（接收 `{ symbols: [...] }`）
- **路由宣告順序**：PATCH reorder 必須在 DELETE `/{symbol}` 之前，否則 FastAPI 會把 "reorder" 當 symbol 參數吃掉

### 2. 前端：API 層 + Hook

**api.ts**

- `monitorList` 物件加 `reorder: (symbols) => fetchJSON('/api/monitor_list/reorder', { method: 'PATCH', body: ... })`

**useMonitorList.tsx**

- interface 加 `reorder: (symbols: string[]) => Promise<void>`
- 實作樂觀更新 + 失敗回滾（同 `useBookmarkItems.reorder` pattern）
- 加 `seqRef` 防止 stale refresh 蓋掉樂觀結果

### 3. 前端：BookmarkEditMode 加拖拉

**BookmarkEditMode.tsx**

- 加 dnd-kit imports（DndContext, SortableContext, useSortable, CSS, PointerSensor）
- Props 加 `onReorder: (symbols: string[]) => void`
- `<ul>` 包 `<DndContext>` + `<SortableContext>`
- 每個 `<li>` 用 `SortableEditRow` wrapper（co-locate useSortable，同 BookmarksPanel convention）
- PointerSensor distance:6 區分 click（checkbox toggle）vs drag
- `handleDragEnd` → `applyDragToOrder` → `onReorder`

### 4. 前端：MonitorListView 加編輯模式 + 拖拉

**BookmarksPanel.tsx 的 MonitorListView**

- 加「✎ 編輯」按鈕 + editMode state
- 編輯模式：wrap items in DndContext + SortableContext，用 `SortableItemRow`（已存在，直接 reuse）
- 瀏覽模式：純 `ItemRow`，不可拖
- 拖完 → `onReorder`（從 parent 傳入 `reorderMonitor`）

**BookmarksPanel.tsx（parent 接線）**

- `useMonitorList` 解構加 `reorder: reorderMonitor`
- MonitorListView 傳入 `onReorder={reorderMonitor}`
- BookmarkEditMode 傳入 `onReorder={reorderItemsEverywhere}`

### 5. 前端：SingleListView 移除隨時拖 + 移除置頂

**BookmarksPanel.tsx 的 SingleListView**

- 刪除 `partitionByHits` 呼叫、`frozen/setFrozen` state、`pinned/rest` 解構
- 刪除 pinned items 的獨立渲染區塊（lines 482-494）
- 刪除 `DndContext`/`SortableContext` — 瀏覽模式不再有拖拉
- 保留 `onReorder` prop（由 BookmarkEditMode 使用）
- items 全部用普通 `ItemRow` 渲染（照 position 排序，不分區）

**BookmarksPanel.tsx 的 ItemRow**

- 移除 `hasHit` 變數 + 左側色條 marker（lines 581-583）
- 移除 `markerBg`（只被 marker 使用）
- 保留 `totalHitsForSymbol`（SignalChip 的 hitCounts 仍需）

### 6. reorder.ts 清理

- 刪除 `partitionByHits` 函式
- 更新 module-level JSDoc，移除置頂相關描述
- `applyDragToOrder` 的註解移除置頂描述
- `applyDragToOrder` 函式本身不動

### 7. 測試

**reorder.test.ts**

- 移除 `partitionByHits` import
- 刪除 `describe('partitionByHits')` 整段
- `applyDragToOrder` 測試的註解移除置頂描述

**test_config_store.py**

- 加 `reorder_monitor` 測試（正常重排 + 集合不符拒絕）

**test_monitor_list_route.py**

- 加 PATCH reorder 端點測試

---

## 共用程式碼決策

| 符號 | 位置 | 用途 | 決策 |
|------|------|------|------|
| `useDragSensors` | BookmarksPanel:33 | sidebar 群組拖 + MonitorListView 拖 | **保留** |
| `useSortableContainer` | BookmarksPanel:40 | SortableSidebarItem + SortableItemRow + MonitorListView | **保留**；BookmarkEditMode 複製一份（co-locate convention） |
| `applyDragToOrder` | reorder.ts:27 | 所有四個 DndContext | **保留不動** |
| `partitionByHits` | reorder.ts:10 | 只被 SingleListView 用 | **刪除** |
| `SortableItemRow` | BookmarksPanel:624 | SingleListView（刪） + MonitorListView（新加） | **保留**（MonitorListView reuse） |
| `frozen/setFrozen` | BookmarksPanel:450 | 只在 SingleListView 用 | **刪除** |
| `totalHitsForSymbol` | BookmarksPanel:62 | ItemRow 的 hasHit marker + SignalChip | hasHit marker 刪後，檢查是否仍有呼叫者；若無則一併刪 |

---

## 風險與注意事項

1. **路由宣告順序**：`PATCH /api/monitor_list/reorder` 必須在 `DELETE /api/monitor_list/{symbol}` 之前
2. **舊資料相容**：既有 monitor items 無 position 欄位，排序要 fallback `added_at` desc
3. **click vs drag 共存**：BookmarkEditMode 的 `<li>` 同時有 `onClick`（checkbox）和 drag listener，靠 distance:6 區分。需手動測試
4. **recentlyAdded 動畫**：BookmarkEditMode 的 fade-out highlight 動畫可能跟 useSortable 的 transform/transition 衝突，需視覺驗
5. **系統書籤不可拖**：SingleListView 的 `isSystem` 分支仍用普通 ItemRow，確保不誤入 SortableContext
6. **盤中訊號觸發**：移除置頂後，items 陣列長度不變（不再 re-partition），dnd-kit 能正常處理

---

## 不在本次範圍

- Sidebar 書籤群組拖拉（保持現狀）
- 「全部」view 的排序（跨書籤聚合，不適合拖拉）
- drag handle 視覺圖示（≡）— 編輯模式的列本身就是可拖的，不額外加 handle icon（保持簡潔）
- 觸控裝置支援（本專案為桌面本機使用）
