# 監控列表自訂排序 — 設計

日期:2026-06-12
狀態:已與 user 確認需求,採做法 A(dnd-kit + 後端 position 欄位)

## 需求

1. **書籤群組**(左側 sidebar)與**各書籤內的股票**兩個層級都能由使用者自訂排列順序
2. 操作方式:滑鼠拖拉
3. 「有訊號命中自動置頂」規則保留 — 命中的股票浮到最上面,其餘照手動順序
4. 新加入的股票排在最上面(維持現狀的視覺習慣)
5. 順序持久化在 `backend/data/config.json`,跟著匯出/匯入走

## 現況

- 儲存:`backend/services/local_store/config_store.py`,單一 `config.json` 寫穿
- `watchlist_items` 欄位:`id` / `group_id` / `symbol` / `added_at` / `note`,**無排序欄位**
- `GET /api/bookmarks/{bid}/items` 固定以 `added_at` 新→舊回傳(`routes/bookmarks.py:221`)
- `bookmark_groups` 已有 `sort_order` 欄位 + PATCH 可改,缺的只是拖拉 UI 與批次 reorder 端點
- 前端 `BookmarksPanel.tsx` 的 SingleListView 另有一層「訊號命中數降冪置頂」的 client sort
- 前端無任何 drag-and-drop 依賴

## 設計

### 後端

1. **`watchlist_items` 加 `position`(整數)**
   - 新增股票時設為該群組現有最小 position − 1(沒有任何 position 時為 0)→ 自然排最上面
   - 既有資料不做一次性遷移;無 `position` 的項目由讀取端 fallback 處理
2. **`GET /api/bookmarks/{bid}/items` 排序改為**:有 `position` 者按 position 升冪在前;無 `position` 者按 `added_at` 新→舊接在後面
3. **新端點 `PATCH /api/bookmarks/{bid}/items/reorder`**
   - body:該群組完整的 symbol 順序陣列
   - 後端驗證 symbol 集合與群組現有項目一致(不一致回 400),然後重寫 position 為 0..n-1
4. **新端點 `PATCH /api/bookmarks/reorder`**
   - body:使用者群組 id 的完整順序陣列,批次重寫 `sort_order`
   - 一次呼叫完成,不讓前端連打 N 次既有的單筆 PATCH
5. 匯入驗證(`_IMPORT_REQUIRED_FIELDS`)不把 `position` 列為必填 — 舊匯出檔要能直接匯入

### 前端

6. 新依賴:`@dnd-kit/core` + `@dnd-kit/sortable`(輕量、支援滑鼠/觸控/鍵盤)
7. **SingleListView(單一書籤檢視)**:項目可拖拉排序
   - 有訊號命中的項目仍自動浮到最上面,且不參與拖拉
   - 其餘項目照手動順序,可互拖
8. **左側書籤群組**:可拖拉;系統內建的「監聽」「全部」固定在頂端不可拖
9. 拖放完成 → 樂觀更新 UI → 背景打 reorder API;API 失敗則回滾並顯示錯誤
10. 「監聽」與「全部」檢視不做拖拉(「全部」的分組順序自然跟著群組 sort_order 走)

## 錯誤處理

- reorder API 收到的 symbol/id 集合與現況不符(例如另一視窗剛刪了一檔)→ 400,前端回滾重抓
- 拖拉中網路失敗 → 回滾到拖之前的順序,toast 提示

## 測試

- 後端:`test_bookmarks_route.py` 加 reorder 端點 contract 測試 — 正常重排、集合不符 400、舊資料(無 position)fallback 排序、新增項目排最上
- 前端:排序邏輯(置頂 + position 合併)抽純函式進 `lib/`,單元測試;拖拉互動不做自動化測試(專案無 hook 測試環境),靠手動驗收

## 不做的事(YAGNI)

- 不做跨書籤拖拉移動(既有「編輯模式」的移動/複製已涵蓋)
- 不做「監聽」「全部」檢視內的拖拉
- 不做既有資料的一次性 position 回填
