# 移除 Supabase 依賴:改本機 JSON/JSONL 儲存 + 匯出/匯入

**日期**:2026-06-01
**狀態**:設計待 review(v2)
**範圍**:後端資料持久化層 + 前端匯出/匯入 UI + 一次性遷移腳本

---

## 1. 背景與目標

trading-king 目前的架構是:每個人**在自己電腦本機**跑後端(FastAPI + 富邦 Neo Python SDK)+ 前端(React),但**共用同一個雲端 Supabase**。

要解決的痛點:**不想再依賴那個共用的雲端 Supabase**。

目標架構:

```
前端 ──/api/──> 後端(富邦 SDK)──> 本機 JSON/JSONL 檔
                     │
                     └──────────> 富邦伺服器(即時行情)
   前端、後端、資料檔 全部在同一台電腦,零雲端 DB 依賴
```

關鍵認知(已對齊):**「後端」不是「Supabase」**。後端是富邦引擎,因為富邦只提供 Python SDK(瀏覽器無法載入、金鑰不能放前端、WS 長連線與訊號狀態機需常駐),所以它**必須**以一個本機 Python 程式存在。要拿掉的只有雲端 Supabase,後端留著。

---

## 2. 關鍵決策(brainstorming 定案)

| 決策 | 結論 |
|------|------|
| 後端去留 | **留**(富邦引擎,本機跑) |
| Supabase | **徹底移除**,零依賴 |
| 本機儲存形式 | **JSON + JSONL 檔案**(檔案即資料庫) |
| 匯出範圍 | **只含個人設定**(書籤、訊號規則、監聽清單);訊號歷史不匯出 |
| 匯入語意 | **整包取代**(匯入前先備份舊檔) |
| 舊 Supabase 資料 | **一次性遷移**腳本拉到本機 |
| 匯入後生效方式 | **熱套用**(不重啟;沿用現有 CRUD 的訂閱/refresh 機制) |
| `USER_LABEL` | **完全移除**(本機單人,無隔離需求) |

---

## 3. 設計鐵則:資料 / API / 功能 行為保持不變

> 回應需求「**確保所有資料、API 以及功能不受此次重構影響**」。

這次是**純儲存層抽換**:把後端與 Supabase 之間那一段換成本機檔案,**對外契約完全不變**。

**鐵則**:
1. **所有 `/api/*` 端點的 request / response 形狀不變**。前端打的每一條路由,回傳格式逐欄位一致。
2. **所有 WebSocket 事件不變**(`signal` / `tick` / `mxf_candle` / `mxf_signal` / `mxf_strategy_state`)。
3. **前端資料流不改**(前端目前無任何 Supabase 程式碼);只**新增**匯出/匯入 UI,不動既有畫面邏輯。
4. **零資料遺失**:遷移腳本把現有 Supabase 個人資料 + 訊號歷史完整拉到本機。
5. **富邦相關全不動**:登入、WS 訂閱、訊號引擎、MXF 策略、排程、rate limiter 一律不碰。

**端點分類(確認影響面)**:

| 類別 | 端點 | 這次是否更動 |
|------|------|:---:|
| 儲存型(Supabase → 本機) | `/api/symbols`(搜尋)、`/api/watchlist*`、`/api/bookmarks*`、`/api/monitor_list*`、`/api/active_signals*`、`/api/signals/history`、`/api/signals/today_counts` | 換底層,**契約不變** |
| 指標型(讀 daily_ohlc 快取) | `/api/cdp/*`、`/api/camarilla/*` | 換快取來源,**契約不變** |
| 純富邦代理(本來就不碰 Supabase) | `/api/quote/*`、`/api/quotes/snapshot`、`/api/candles/*`、`/api/ma/*`、`/api/mxf/*`、`/api/preview` | **完全不動** |
| 即時推送 | `WS /ws/realtime` | **完全不動** |
| 新增 | `/api/config/export`、`/api/config/import` | 新功能 |

**驗證方式**(實作完成的成功判準):
- 既有後端測試套件(`backend/tests/`)全綠 —— 把 mock 對象從 supabase client 換成 local_store 後,**斷言不變**。
- 逐端點 parity:對照重構前後同一請求的回傳(欄位、型別、排序、錯誤碼)。
- 遷移後資料筆數核對:各表 row 數 = config.json / jsonl 對應筆數。

**唯一需要你拍板的行為細節**:加股票時對「symbol 是否存在」的檢查。原本靠 Supabase 的外鍵(symbol 不在 `symbols` 表就擋)。本機化後改為「對照本機 symbols 快取」,但若快取尚未就緒(例如新機器剛開、爬蟲還沒跑完),為了不把使用者鎖死,**降級為放行**(best-effort,名稱事後補)。互動式加股票仍維持驗證、僅在快取不可用時放行;**匯入**路徑一律寬容(不因單一未知 symbol 擋掉整包)。詳見 §6。

---

## 4. 範圍與非目標

**範圍內**:後端所有 Supabase 讀寫改本機檔案;移除 `supabase` Python 依賴與 `SUPABASE_*` / `USER_LABEL` 環境變數;新增匯出/匯入 API + 前端 UI;一次性遷移腳本。

**非目標**:不改前端資料流;不改富邦 SDK 邏輯;不做跨裝置自動同步(改機器靠手動匯出/匯入);不下單。

---

## 5. 資料分層

| 類型 | 原 Supabase 表 | 本機去處 | 進匯出檔? |
|------|----------------|----------|:---:|
| 書籤群組 | `bookmark_groups` | `config.json` | ✅ |
| 書籤股票 | `watchlist_items` | `config.json` | ✅ |
| 訊號規則 | `active_signals` | `config.json` | ✅ |
| 監聽清單 | `monitor_list` | `config.json` | ✅ |
| (legacy)自選 | `watchlist` | 併入書籤「自選」群組 | ✅ |
| 訊號觸發歷史 | `signals_log` | `signals_log.jsonl`(append-only) | ❌ 只留本機 |
| 全市場代碼名稱 | `symbols` | `cache/symbols.json` | ❌ 見 §6 |
| 昨日日線 OHLC | `daily_ohlc` | `cache/daily_ohlc.json` | ❌ 從富邦 lazy backfill |
| 大漲股 | `top_gainers_snapshot` | **記憶體**,每分鐘重算 | ❌ 即時重生 |

> **修正**:`daily_ohlc` 不是遺物。`cdp.py` / `camarilla.py` 用它存昨日 OHLC(lazy 從富邦 `historical.candles` backfill)算 CDP 5 線與 Camarilla 8 線。是 per-symbol 小快取,改本機 JSON 即可,**不可移除**。

---

## 6. 全市場代碼名稱(symbols)怎麼獲取

> 回應需求「**全市場代碼名稱之後如何獲取**」。

**重點:symbols 跟富邦無關**,來自公開的 TWSE/OTC 網站(現成的 `routes/symbols.py` 已經這樣做,只是終點是 Supabase):

- **主來源 — ISIN 表**(全部上市/上櫃股票,big5 HTML):
  - 上市:`https://isin.twse.com.tw/isin/C_public.jsp?strMode=2`
  - 上櫃:`https://isin.twse.com.tw/isin/C_public.jsp?strMode=4`
- **補來源 — OpenAPI**(補 ISIN 漏掉的當日交易股):
  - TWSE:`https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
  - TPEx:`https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes`
- 用 `httpx`(已是依賴)抓,`verify=False`(公開站憑證問題,無 secret)。

**改動**:把抓回來的 `{symbol, name, market, is_etf, is_active}` 清單,從「upsert 到 Supabase」改成「寫入 `cache/symbols.json`」;`GET /api/symbols?search=` 從「查 Supabase」改成「讀 symbols.json 進記憶體後過濾(symbol 前綴 OR name 模糊)」。**回傳格式不變**。

**何時更新**:
1. **自動 bootstrap**:後端啟動時若 `symbols.json` 不存在(全新機器),在背景爬一次(不阻塞 startup)。
2. **手動**:保留 `POST /api/symbols/refresh`,隨時可重爬(新上市、改名)。
3. symbols 不進匯出檔 —— 換機器在新機自然重爬即可。

---

## 7. 檔案布局

```
backend/data/                 # 加進 .gitignore,每台機器各一份
  config.json                 # 個人設定(匯出/匯入的可攜檔基礎)
  config.backup-<n>.json      # 匯入時自動備份的舊檔
  signals_log.jsonl           # 訊號觸發歷史(append-only,本機限定)
  cache/
    symbols.json              # 代碼→中文名 快取(TWSE/OTC 公開來源)
    daily_ohlc.json           # symbol → 最近一筆 {date,high,low,close}
backend/logs/
  mxf_signals.jsonl           # (已存在,維持不變)
```

### `config.json` 結構(已移除 `user_label`)

```json
{
  "schema_version": 1,
  "exported_at": null,
  "bookmark_groups": [
    {"id": "uuid", "name": "自選", "sort_order": 0,
     "is_system": false, "source_type": null, "created_at": "ISO8601"}
  ],
  "watchlist_items": [
    {"id": "uuid", "group_id": "uuid", "symbol": "2330",
     "added_at": "ISO8601", "note": null}
  ],
  "active_signals": [
    {"id": "uuid", "name": "...", "filter_json": {}, "scope": {},
     "cooldown_seconds": 1800, "enabled": true,
     "notify_discord": true, "created_at": "ISO8601"}
  ],
  "monitor_list": [
    {"symbol": "2330", "added_at": "ISO8601"}
  ]
}
```

- `schema_version`:向前相容;匯入時驗證。
- 系統書籤「大漲股」(`source_type=top_gainers`)是後端產生的,**不寫入 config.json**;啟動時自動 seed。

### `signals_log.jsonl`

每行一個 JSON 物件,append-only:`{id, active_signal_id, symbol, triggered_at, trigger_price, trigger_volume, context_json}`。
- 歷史查詢(`/api/signals/history`)= 讀檔 + 記憶體過濾(symbol / active_signal_id / since / limit)。
- 今日次數(`/api/signals/today_counts`)= 記憶體 counter(Asia/Taipei)。
- **檔案輪替**:本版單檔 + 記憶體過濾;若需要,後續改按交易日切檔(`signals_log/YYYY-MM-DD.jsonl`)。

---

## 8. 元件設計

### 8.1 `backend/services/local_store.py`(取代 `supabase_client.py`)

一個 `LocalStore` 單例,**記憶體為權威 + 寫穿到檔案**:

- `init()`:確保 `data/` 存在;載入 `config.json`(不存在則建預設:seed「自選」書籤 + 「大漲股」系統書籤);載入 `signals_log.jsonl` 建今日 counter;載入 market 快取(symbols.json 不存在 → 觸發背景 bootstrap 爬蟲)。
- **個人設定 repository**(明確函式,取代鏈式 `supabase.table().select().eq()`):書籤(含 cascade 刪 items)、訊號規則、監聽清單。**不再有任何 `user_label` 過濾。**
- **訊號歷史**:`append_signal_log(row)`、`query_history(...)`、`today_counts()`。
- **market 快取**:`search_symbols(prefix)` / `replace_symbols(rows)`;`get_daily_ohlc(symbol)` / `upsert_daily_ohlc(...)`;`get_top_gainers()` / `replace_top_gainers(rows)`(記憶體)。
- **匯出/匯入**:`export_config()` → dict;`import_config(data)` → 驗證 + 備份 + 取代 + 觸發 re-sync。

**寫入安全**:`config.json` 變更走「寫暫存檔 + `os.replace` 原子替換」+ 一把 `asyncio.Lock`;`signals_log.jsonl` append 同樣經鎖。

**為何用明確 repository 而非「假裝是 supabase 的 shim」**:查詢就那幾種,明確函式可讀、可測;做查詢建構器 shim 是過度抽象(違反 Rule 2)。

### 8.2 symbol 驗證(行為保持)

加股票端點(bookmarks / monitor_list)對 symbol 的檢查:**對照本機 symbols 快取**(保留原行為);快取尚未就緒時**降級放行**(不把使用者鎖死)。**匯入**路徑一律寬容。名稱解析查不到先顯示代碼(多數前端元件已是 best-effort)。

### 8.3 匯出 / 匯入 API

- `GET /api/config/export`:回 `export_config()`(蓋上 `exported_at`、`schema_version`),前端觸發下載 `trading-king-config-<date>.json`。
- `POST /api/config/import`:收上傳 JSON → 驗 `schema_version` → **備份**現有 `config.json` 為 `config.backup-<n>.json` → 記憶體 + 磁碟整包取代 → **熱套用 re-sync**(見 8.4)。

### 8.4 匯入熱套用(不重啟)

取代 config 後,沿用既有機制讓熱狀態跟上:① 退訂舊 WS owner(`bookmark:*`、`monitor_list`、legacy `watchlist`);② 重跑啟動時「依 config 訂閱所有書籤股票 + 監聽清單」的初始訂閱;③ 呼叫 `signal_engine.refresh_active_signals()`。抽共用 `resync_from_config()`(啟動與匯入共用,DRY)。**最需小心、要有測試覆蓋。**

### 8.5 一次性遷移腳本 `backend/scripts/migrate_supabase_to_local.py`

- 用法:`python -m scripts.migrate_supabase_to_local --user-label loger`
  - `SUPABASE_URL` / `SUPABASE_KEY` 仍從 `.env` 讀(一次性需要);`--user-label` 是 **CLI 參數**(只為決定拉哪個 user 的舊資料,**不寫入本機、不成為執行期概念**)。
- 拉該 label 的 `bookmark_groups` / `watchlist_items` / `active_signals` / `monitor_list`(+ legacy `watchlist` 併入「自選」)→ 寫 `config.json`(**去掉 user_label 欄**)。
- 拉該 label 的 `signals_log` → 寫 `signals_log.jsonl`。
- `symbols` / `daily_ohlc` 不遷(本機重建)。
- 印出摘要(各類筆數),供 §3 的筆數核對。
- 跑完驗證 OK → 從 `.env` 移除 `SUPABASE_*`、解除安裝 supabase 依賴。

### 8.6 移除 `USER_LABEL`

- 刪 `services/user_context.py` 與 `get_user_label()`。
- 各 route / service 拿掉 `.eq("user_label", ...)` 過濾與 user_label 寫入。
- `main.py` 拿掉啟動時的 user_label fail-fast 驗證。
- `.env` / `.env.example` 移除 `USER_LABEL`。
- 測試:`conftest.py` 與相關測試移除 user_label fixture/斷言。

### 8.7 `supabase_writer.py` → JSONL appender

原本「批次 buffer + 500ms flush + retry」是為遠端寫;本機 append 不需批次 → 簡化成直接(經鎖)append 到 `signals_log.jsonl`。

---

## 9. 受影響檔案

### 後端 — 改資料存取(Supabase → local_store)
- `routes/`:`bookmarks.py`、`watchlist.py`、`monitor_list.py`、`active_signals.py`、`signals_history.py`、`symbols.py`
- `services/`:`cdp.py`、`camarilla.py`(daily_ohlc 快取)、`signal_engine.py`(monitor_list / active_signals 來源)、`supabase_writer.py`(改 JSONL appender)
- `jobs/top_gainers_scheduler.py`(寫記憶體而非 Supabase 表)
- `main.py`(lifespan 拿掉 Supabase init + user_label 驗證;加 local_store init;抽出 `resync_from_config()`)

### 後端 — 新增
- `services/local_store.py`、`routes/config_io.py`、`scripts/migrate_supabase_to_local.py`

### 後端 — 移除
- `services/supabase_client.py`、`services/user_context.py`
- `pyproject.toml` 的 `"supabase>=2.4"`
- `.env` / `.env.example` 的 `SUPABASE_URL` / `SUPABASE_KEY` / `USER_LABEL`

### 前端 — 新增(僅此)
- 匯出/匯入 UI(下載按鈕 + 上傳檔案 + 取代前確認對話框),放側欄或設定區
- `lib/api.ts` 加 `exportConfig()` / `importConfig(file)`

> 前端目前**無任何 Supabase 程式碼**,資料流不變。

---

## 10. 風險與權衡

1. **失去跨裝置自動同步** — 刻意取捨(已接受)。
2. **匯入熱套用最複雜** — 漏退訂/漏 refresh 會殘留 → 抽共用 `resync_from_config()` + 測試。
3. **`signals_log.jsonl` 成長** — 預留按日切檔後路。
4. **symbol 驗證降級** — 快取未就緒時放行(§3 已標為待你拍板的行為細節)。
5. **`daily_ohlc` 快取** — 維持原 lazy backfill 語意,只換儲存。
6. **多分頁併發寫** — `asyncio.Lock` 序列化。
7. **遷移一次性** — 驗證無誤再清 `.env` / 依賴(留退路)。

---

## 11. 測試策略(Rule 9 — 測意圖,不只測行為)

- **行為不變(§3 鐵則)**:既有 `backend/tests/` 全綠(mock 由 supabase client 換 local_store,**斷言不變**);關鍵端點 parity。
- **local_store CRUD**:round-trip;原子寫入;刪書籤群組**必須**連帶清 items(cascade — 為何:孤兒 item = UI 幽靈股票);預設「自選」+「大漲股」seeding。
- **匯出/匯入**:round-trip 一致;匯入是「**取代**」(舊資料消失);匯入前**有備份**。
- **匯入熱套用**:`signal_engine.refresh_active_signals()` 被呼叫(為何:不重啟也要讓規則生效)、舊 owner 退訂、新 owner 訂閱(mock WSPool 斷言)。
- **signals_log JSONL**:append→history 過濾正確;today_counts 跨午夜歸零。
- **symbols**:`refresh` 寫 symbols.json;`search` 過濾正確;新機 bootstrap 觸發。
- **遷移腳本**:對 mock supabase client 餵 fixture(含 user_label)→ 產出**去 user_label** 的預期 config.json 與 jsonl。
- **daily_ohlc 快取**:沿用 `test_camarilla.py` mock 模式,改 mock local_store。

---

## 12. 開放問題 / 待 review 確認

1. 資料檔放 `backend/data/`(進 `.gitignore`)OK?
2. 匯出檔名 `trading-king-config-<date>.json` OK?
3. symbol 驗證在「快取未就緒時放行」(§3 / §8.2)可接受嗎?(這是唯一一處與舊硬 FK 行為的差異,且只會更寬鬆、不減功能)
4. 訊號歷史本版「單檔 + 記憶體過濾」夠用?
