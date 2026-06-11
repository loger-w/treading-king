# 庫存均價+未實現損益 — 接 GetProfitLossGWReport(2026-06-11)

延續同日庫存欄位校準批次(PR #23):OnRealBalanceReport 沒有均價欄,
`Position.avg_price` 目前恆 None、前端損益顯示「—」。本批次接群益新損益試算
API 補上均價,**前端零改動**(avg_price 有值後,既有顯示鏈自動亮起)。

## 官方介面(策略王COM元件使用說明_V2.13.58.docx,未實測欄位首跑校準)

- **查詢** 4-2-73:`GetProfitLossGWReport(bstrLogInID, TSPROFITLOSSGWQUERY)`,
  條件物件:`bstrFullAccount`、`nTPQueryType=0`(未實現)、`nFunc=0`(彙總)、
  `bstrStockNo=""`(全部)、其餘字串欄帶空字串。rc 同步回、資料走事件。
- **事件** 4-2-p `OnProfitLossGWReport(bstrData)`(未實現-彙總,25 欄):
  第 1 筆=查詢結果(`000,訊息`=成功);資料列 `[0]`股票名稱 `[1]`股票代號
  `[3]`交易種類 `[4]`庫存股數 `[5]`市價 `[9]`損益 **`[10]`平均買進(券賣)成本**
  `[22]`未知成本股數;結尾 `##` 列。
- 舊 `GetRequestProfitReport`/`OnRequestProfitReport` 官方標注即將下線,不用。

## 資料流

```
成交(D)2s debounce / 60s 定時 → GetRealBalanceReport(現有)
  → OnRealBalanceReport×N → ## → flush:set_positions(qty/kind 權威)
  → 隨即(同 COM 執行緒)發 GetProfitLossGWReport     ← 串行避開 1019 查詢中
  → OnProfitLossGWReport×N → ## → flush:store.apply_avg_prices({股號: 均價})
  → 推 WS capital_position → 前端 refetch,均價/損益亮起
```

## 元件改動(全後端)

1. **`capital_com.py`**:
   - Protocol + Skcom 加 `get_profit_loss_gw(user_id, full_account) -> int`
     (建 `TSPROFITLOSSGWQUERY`,nTPQueryType=0/nFunc=0,字串欄空字串)
   - `_OrderEvents` 加 `OnProfitLossGWReport` → `on_profit` 回呼;
     `setup()` 加可選參數 `on_profit`(疊回呼,不重寫既有 — M1 防 regress 慣例)
2. **`capital_balance.py`**:
   - `BalanceCollector` 泛化:建構子加 `parse` 參數(預設 `parse_balance_line`,
     現有行為不變)
   - 新增 `parse_profit_line(raw) -> tuple[stock_no, avg] | None`:
     跳過 `#` 開頭/`000` 狀態列/欄位不足/數字壞/avg≤0
3. **`capital_store.py`**:
   - `apply_avg_prices(dict[str, float])`:回填現有部位均價(查無股號忽略)
   - `set_positions` 保留語意:新列 avg=None 且舊列同股號**同種類**有均價 → 沿用
     (損益查詢回來前不閃缺;種類變了不沿用 — 換了資/券成本基礎不同)
4. **`capital_client.py`**:
   - 第二個 collector(`parse=parse_profit_line`)+ `_handle_profit`/`_on_profit_complete`
   - `_on_balance_complete` 末端直接呼叫 `com.get_profit_loss_gw`(已在 COM 執行緒;
     balance 查詢剛完結,串行天然避開 1019),rc≠0 只 log
   - 幫浦圈加 `self._profit.poll()`(timeout flush 保險)
   - `_on_profit_complete`:`store.apply_avg_prices` + 推 WS `capital_position`
5. **`capital_smoke.py --balance`**:tap 也掛 `_handle_profit` 印原始字串,
   等待時間涵蓋兩段查詢,最後印含均價的持倉 — 首跑校準用

## 校準假設(首跑驗證,錯了只會缺均價、不出垃圾)

- `[10]` 為每股單價(非總成本);非數字/≤0 → 該檔均價維持 None
- 未實現查詢條件全留空(股號/種類/日期)= 回全部 — 失敗 rc 或空資料看 log 再調
- 同檔多列(現股+融資)→ 後到覆蓋;與 balance dedupe「保留大者」的不一致
  屬已知過渡取捨(部位分種類建模時一併處理)

## 不做

- 報告 `[9]`損益/`[21]`報酬率(前端用 tick 即時算)、已實現損益、當沖損益、
  前端任何改動、`OnRequestProfitReport`(將下線)

## 測試

- pytest:`parse_profit_line`(官方欄位構造樣本;首跑後換真實去敏樣本)、
  collector 泛化不破既有、store 回填/沿用/種類變更不沿用、
  client 鏈(balance flush → 觸發損益查詢 → profit flush → store 均價)
- 前端 vitest 全套維持綠(零改動驗證)

## 驗收

1. 後端啟動後 ≤90s,`/api/capital/positions` 各檔 `avg_price` 非 null(有成本資料者)
2. 庫存分頁均價與群益 App 一致;損益/%/總損益自動顯示
3. 成交後 ≤數秒,張數與均價一起刷新
4. 後端 pytest 全綠、前端 vitest 全綠(未改動)
