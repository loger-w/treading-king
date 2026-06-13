# Hand-off：MXF 分時圖 Batch B — Session 順序 + 更新頻率

**日期**：2026-05-27
**狀態**：待新 session 開始
**前情**：`feat/mxf-chart-interactions` branch 已完成 chart UX 三輪 iteration（zoom / 時間軸 / header / 拖曳防選 / TF 切換不閃 / 中文名）共 19 commits。剩兩個 issue 跨 backend + 富邦 API，需 fresh context 處理。

---

## Issue 4 — Session 順序：改為 night → day

### 現況

`backend/services/fubon_futures.py:177-215` 的 `fetch_candles`：
- 並行抓「今日日盤」(`session=None`) + 「今日 afterhours」(`session="afterhours"`)
- 用 `merge_candles` 排序後回傳

當 **user 在夜盤時段**（15:00–05:00）開啟頁面：
- day = 今日 08:45–13:45（已完成）
- afterhours = 今日 15:00 起（進行中）
- 視覺呈現：**day → night**（user 不喜歡）

### 期望

依「交易日 D = D-1 15:00 → D 13:45」定義（已寫在 `determine_current_session` 註解），永遠呈現 **night → day**：
- 抓「昨日 afterhours」+「今日日盤」
- 不管現在是哪個時段都這樣

### 待釐清

1. **富邦 candles API 支不支援指定日期**？目前 `_fetch` 只傳 `session` 參數沒傳 date。需查 `docs/api/fubon-neo-llms.txt` 找對應 endpoint 文件。
2. **「昨日」要怎麼算**？週一查的話「昨日」是上週五（週五無夜盤）→ 應 fallback 抓週五日盤 + 週一日盤？還是只顯示週一日盤一段？
3. **WS 訂閱要不要也跟著改**？目前 `fubon_futures_ws.py:74-89` 在 session 邊界用 `target_after_hours_flag(now)` 決定訂閱 day or night。若 chart 顯示「昨夜 + 今日盤」，WS 仍訂閱「現在」的 session 就好（昨夜已完成不用訂）。

### 影響範圍

- `backend/services/fubon_futures.py` — `fetch_candles` 改抓不同時段
- `backend/tests/test_fubon_futures.py` — test fixture 可能要更新
- 前端 `useMXFCandles` 不用動（candle list 結構不變）

---

## Issue 5 — 更新頻率太慢

### 現況

`backend/services/fubon_futures_ws.py:91-115` 訂閱富邦 WS：
```py
ws.subscribe({"channel": "candles", "symbol": self._symbol, "afterHours": after_hours})
```

`candles` channel 收到 push → broadcast 給前端 → `useMXFCandles` 更新最後一根 candle。

User 反映：「**更新太慢**」— 大字現價刷新感覺不是即時。

### 待釐清

1. **富邦 candles channel 推送頻率**到底是 per-tick 還是 per-bar-close？
   - 若 per-tick：應該是即時的，問題在別處（網路、WS 沒連上、broadcast 漏掉等）
   - 若 per-bar-close：1m TF 每分鐘才推一次、5m 五分鐘才推 → 確實慢
2. 若 candles channel 太慢，**有沒有 ticker / trades channel** 可以拿 per-tick 即時價？
3. 即時 ticker 要怎麼**整合進現有 candle 流**？
   - 方案 A：另開一個 `mxf_tick` event 只更新 header 大字現價、不動 candle list
   - 方案 B：把 ticker 投影到「最後一根 candle 的 close」並 push 模擬 `mxf_candle`（跟現有邏輯相容）

### 量測方法

進 dev server、開瀏覽器 console、在 `useMXFCandles.ts:58-77` 的 push handler 加 `console.log('mxf_candle push', new Date().toISOString(), candle.close)`，盤中觀察推送頻率。

### 影響範圍

- `backend/services/fubon_futures_ws.py` — 可能改 channel 或加新 subscription
- `backend/ws_broadcaster.py` — 可能新 event type
- `frontend/src/hooks/useSignalsStream.ts` — 新 event subscriber
- `frontend/src/hooks/useMXFCandles.ts` 或新 hook — 新 event handler
- `frontend/src/components/MXFIntradayChart.tsx` — header 大字價可能改成讀新的「即時價」而非 candle.close

---

## 富邦 API workflow（必做）

CLAUDE.md 規定：動到 `fubon_*.py` 或主題涉及行情訂閱前，**必須**：

1. **Grep** `docs/api/fubon-neo-llms.txt`，從中找跟任務相關的條目
   - Issue 4 找：歷史 K 線 / candles / 期貨 K / `marketdata.rest_client.futopt.intraday.candles` / session 參數
   - Issue 5 找：WebSocket / candles channel / tickers / trades / 即時行情
2. **WebFetch** 該條目的 `.txt` URL（本地只是索引、實際內容在遠端）
3. 才開始實作

不要憑印象寫富邦 API 呼叫 — SDK 版本差異會咬人。

---

## 建議執行順序

1. **先做 Issue 4**（contained backend change，~30-45 min）：
   - 查富邦 candles endpoint date 參數
   - 改 `fetch_candles` 抓「昨日 afterhours + 今日日盤」
   - 寫 test for 週一 fallback edge case
   - 跑前端確認 session 順序正確
   - Commit
2. **再做 Issue 5**（research-heavy，1-2h）：
   - 先量測現況推送頻率（console.log + 等市場時段）
   - 若 candles channel 已是 per-tick，問題不在這 → 看 ws_broadcaster 有沒有 drop
   - 若是 per-bar-close → 評估 tickers channel + 設計新 event flow
   - Brainstorm + spec + plan
   - 實作

---

## 啟動新 session 的 prompt

複製下面這段貼到新 session 起頭（會自動 trigger 富邦 workflow + brainstorming skill）：

```
繼續做 MXF 分時圖 Batch B，完整脈絡讀 docs/superpowers/handoffs/2026-05-27-mxf-realtime-update-handoff.md。先做 Issue 4 (session 順序)，做完再做 Issue 5 (更新頻率)。記得依 CLAUDE.md 富邦 API workflow 先 grep + WebFetch docs 再動手。Issue 4 比較 contained 直接走 writing-plans；Issue 5 從 brainstorming 開始。
```
