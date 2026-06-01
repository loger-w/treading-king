# Local-First(移除 Supabase)驗收 Runbook

**日期**:2026-06-02
**對象**:已合併到 `main` 的 local-first 重構(後端持久化從共用 Supabase → 本機 JSON/JSONL + 匯出/匯入)
**相關**:spec `docs/superpowers/specs/2026-06-01-remove-supabase-local-first-design.md`、plan `docs/superpowers/plans/2026-06-01-local-first-no-supabase.md`
**自動化現況**:後端 170 tests 綠、前端 build 綠、production code 零 supabase 依賴。以下是**只能手動跑**的端到端驗收。

---

## 前置:已知的「刻意行為差異」(看到別當 bug)

| 端點 / 行為 | 舊(Supabase) | 新(本機) | 為何可接受 |
|---|---|---|---|
| `PUT/DELETE /api/active_signals/{id}` 未知 id | 靜默(PUT 回 `{}`、DELETE 204) | **404** | 前端只操作已知 id;舊的靜默假成功更危險 |
| item 的 `is_etf`(symbol 不在快取) | `null` | `null`(已修正 parity) | 與舊一致 |
| `/api/signals/history`、`/today_counts` Supabase 不可用時 | 503 | 永遠 **200** | 本機無「不可用」狀態 |
| `POST/PATCH /api/bookmarks` 回傳 | 含 `user_label` | 不含 | 本機無此欄,前端只用 id/name |

---

## Part A — 一次性遷移(把舊雲端資料拉到本機)

> ⚠️ **時序關鍵**:必須在 venv 仍裝著 `supabase`、且 `.env` 仍有 `SUPABASE_URL/KEY` 時跑(腳本 lazy import supabase 連舊雲端)。

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.migrate_supabase_to_local --user-label loger
```

- [ ] 指令印出摘要(各類筆數):`bookmark_groups / watchlist_items / active_signals / monitor_list / signals_log`。
- [ ] 摘要筆數 ≈ 你在 Supabase 既有的資料量(粗略核對,確認沒漏)。
- [ ] 確認本機檔生成:`backend/data/config.json`、`backend/data/signals_log.jsonl`。
- [ ] **重跑守衛**:再跑一次同指令 → 應 `RuntimeError` 中止(避免訊號歷史重複);確定要重跑才加 `--force`。
- [ ] **驗證無誤後**:從個人 `.env` 移除 `SUPABASE_URL` / `SUPABASE_KEY`(`USER_LABEL` 也可一併移除,已無用)。

---

## Part B — 冷啟動 bootstrap(驗證全新機器能自建)

- [ ] 停掉 backend,把 `backend\data\` 改名搬走(例:`data` → `data.bak`)。
- [ ] `.\start.ps1`。
- [ ] backend log 出現 symbols 背景 bootstrap(從 TWSE/OTC ISIN 抓全市場 → 寫新的 `data\cache\symbols.json`)。
- [ ] `GET /api/symbols?search=台積` 能回結果(代表 symbols 快取已建立)。
- [ ] 從 UI 加一檔股票 → log 有 WS 訂閱、CDP backfill、訊號引擎開始評估。
- [ ] 驗完:若要還原個人資料,把 `data.bak` 改回 `data`,或用 Part D 的匯入。

---

## Part C — 端點 parity(逐一打,確認形狀與舊版一致)

> 帶 `X-API-Key` header(若有設 `BFF_API_KEY`)。可用瀏覽器、curl、或前端 UI 觸發。

- [ ] **symbols 搜尋** `GET /api/symbols?search=2330&limit=20` → `{results:[{symbol,name,market,is_etf}]}`。
- [ ] **書籤列表** `GET /api/bookmarks` → `{groups:[...],count}`,含使用者群組 + 系統「大漲股」群組(排最後)。
- [ ] **書籤 items** `GET /api/bookmarks/{gid}/items` → items 有補上 `name/market/is_etf`;系統「大漲股」群組的 items 來自即時大漲股。
- [ ] **書籤增/刪** `POST`/`DELETE /api/bookmarks/{gid}/items` → 列表即時反映 + WS 訂閱/退訂(log)+ 加入時 CDP backfill。
- [ ] **監聽清單** `GET /api/monitor_list`(最新加入在前)、`POST`(訂閱 + 引擎刷新)、`DELETE`(退訂)。
- [ ] **訊號規則 CRUD** `GET/POST/PUT/DELETE /api/active_signals` → 寫操作後 signal_engine 重載規則。
- [ ] **訊號歷史** `GET /api/signals/history?symbol=...`、`GET /api/signals/today_counts` → 形狀對照 `test_signals_history_route.py`。
- [ ] **CDP / Camarilla** `GET /api/cdp/{symbol}`、`/api/camarilla/{symbol}` → 線值與改前一致(現走本機 daily_ohlc 快取)。
- [ ] **純富邦代理(不該受影響)** `/api/quote`、`/api/candles`、`/api/ma`、`/api/mxf/*`、`/api/preview`、`WS /ws/realtime` → 行為不變。

---

## Part D — 匯出 / 匯入 roundtrip(UI)

前端(預設 `http://localhost:5173`)→ 書籤管理 modal → 「⤓ 匯出 / 匯入」:

- [ ] **匯出**:下載 `trading-king-config-<date>.json`,內容含 `schema_version`、`exported_at`、書籤/規則/監聽清單。
- [ ] **匯入(整包取代)**:先改動一些書籤/規則 → 匯入剛才的檔 → 跳 `confirm` 警告 → 確認後「匯入完成」,設定整包還原。
- [ ] **熱套用**:匯入後**不需重啟**,WS 重新訂閱、規則即時生效(對照 `routes/config_io.py` 的 resync)。
- [ ] **壞檔**:匯入非 JSON / schema 不符的檔 → 顯示「匯入失敗:…」,不崩潰(後端回 400)。
- [ ] **備份**:匯入後 `backend/data/` 應出現 `config.backup-<n>.json`(誤匯入可救回)。
- [ ] (已知小 UX)匯入後 modal 背後的書籤清單不會自動刷新,重開 modal / reload 即更新 — 非 bug。

---

## Part E — MCP(chrome-devtools)能涵蓋的範圍

用 chrome-devtools MCP 驅動瀏覽器,可端到端驗 **Part D 全部 + Part C 中經 UI 觸發的操作**(書籤、監聽、訊號規則、搜尋),這些**不需富邦登入**即可測 —— 正好涵蓋這次本機化的重點。

即時行情/訊號推送(tick、signal、mxf_*)需要富邦 DMA 登入才有資料,屬另一層,非本次重構範圍。

新 session 開場可直接說:「用 chrome-devtools MCP 測 localhost:5173 的匯出/匯入 + 書籤 UI」。

---

## 一鍵自動化回歸(每次改動後)

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 期望:170 passed
.\.venv\Scripts\python.exe -c "import main"      # 期望:乾淨
cd ..\frontend
npm run build                                     # 期望:exit 0
```
