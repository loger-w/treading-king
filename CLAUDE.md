# CLAUDE.md — trading-king

台股即時監控 + 訊號引擎。FastAPI(後端,富邦 Neo Python SDK 2.2.8)+ React + Supabase。
所有人本機跑、共用同一個 Supabase,靠 `USER_LABEL` 隔離各自資料。專案 onboarding 見 `README.md`。

---

## 富邦 Neo API 工作流程(觸發即執行)

當任務涉及富邦 API,你 **必須先做這三步再動手**:

1. **Grep** `docs/api/fubon-neo-llms.txt`,從中找跟任務相關的條目
2. **WebFetch** 該條目的 `.txt` URL(本地只是索引、實際內容在遠端)
3. 才開始實作 / review / 解釋

不要憑印象寫富邦 API 的呼叫 — SDK 版本差異會咬人(`FubonSDK()` vs `FubonSDK(30, 2)`、`intraday.quote` vs `query_symbol_quote`、callback envelope 結構等)。

### 觸發關鍵字

- 程式碼識別字:`FubonSDK`、`fubon_*`、`sdk.stock.*`、`sdk.marketdata.*`、`init_realtime`、`place_order`、`Order(...)`、`BSAction`/`PriceType`/`MarketType`/`TimeInForce`、`set_on_filled`/`set_on_order`/`set_on_event`
- 主題:登入、行情訂閱(WebSocket)、下單/改單/查單、條件單、停損停利、移動鎖利、rate limit、斷線重連、主動回報、歷史 K 線、技術指標、當沖、期貨/選擇權
- 檔案路徑:`backend/services/fubon_*.py`

### 跳過的情況

純前端、純 DB schema、跟富邦無關的 refactor 或 bug fix — 不需查富邦文件。

### 補充

- 索引外的官方入口:<https://www.fbs.com.tw/TradeAPI/docs/welcome/build-with-llm>
- 任何頁面後綴 `.txt` 都能拿到純文字版(`.../foo` → `.../foo.txt`),`.md` 不再支援
- 索引更新:`.\scripts\update-fbs-docs.ps1`
- 詳細用法:`docs/api/README.md`
- 全域 skill 已存在:`~/.claude/skills/neoapi-python/`(含 `llms-full.txt`)

---

## 重要約束

- **僅訂閱行情、不下單**:後端 `fubon_client.py` 是 DMA login,目前不呼叫 `place_order`。加下單功能前必須先跟 user 確認。
- **期貨歷史資料**:富邦無提供期貨 historical、FinMind 不適合 1m;期貨回測走券商歷史 bootstrap + 富邦即時累積(設計見 `docs/superpowers/specs/2026-05-15-mxf-backtest-design.md`)。
- **MA 線**:`/api/ma/{symbol}` 是 on-demand 即時打富邦 `tech.sma`(period=5, 20),不走 DB cache。當日 daily SMA 不變(實測),前端不必加 cache。
- **Supabase 隔離**:`watchlist` / `active_signals` / `signals_log` 用 `USER_LABEL` 隔離;市場資料(`symbols` / OHLC)共用。

---

## 常用啟動

```powershell
.\start.ps1          # 同時開 backend + frontend
.\install.ps1        # 第一次安裝(pip + npm)
```

Backend dev:`uvicorn main:app --reload`(在 `backend/`)
Frontend dev:`npm run dev`(在 `frontend/`)
