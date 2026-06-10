# 群益委託清單:回報解碼 + 聚合顯示 — 設計(2026-06-10)

## 背景

首次正式實單(2026-06-10)修好 `SKReplyLib_ConnectByID` 後,OnNewData 回報通了,但同時照出顯示層問題:

1. **狀態欄顯示原始 "N"** — `capital_reply._STATUS` 用數字 {0,1,2,4,5} 對照 index 3,但官方 spec(`12.回報.docx`)說 index 3 是 `OrderErr`(Y失敗/T逾時/N正常),真正的「種類」是 index 2(N委託/C取消/D成交…)。對錯欄位 + 對照表整個不對。
2. **數量顯示「1000 張」** — 回報 Qty 證券是「股」,前端直接 `{qty} 張`。
3. 一張單多筆回報(委託 1 筆 + 部分成交 N 筆)在 store 同 seq_no 互相覆蓋,只剩最後一筆,無法回答「成交了沒/成交幾張」。
4. 只有代號沒名稱;買賣別/資券/時間都沒顯示;期貨回報混在證券清單裡。

## 目標(v1)

委託分頁 = **每張單一列、聚合狀態**,顯示:名稱+代號、買賣(紅綠)、現股/融資/融券、`成交/委託` 張數、價格、時間、`[預約]` 標記、失敗原因。**只顯示證券市場**;期貨/選擇權回報照存但不顯示(未來期貨下單面板重用)。

## OnNewData 欄位對照(官方 12.回報.docx + 2026-06-10 12 筆真實回報驗證)

逗號分隔,共 ~47 欄。本設計用到的:

| idx | 欄位 | 語意 | 真實樣本 |
|---|---|---|---|
| 0 | KeyNo | 13 碼委託序號(聚合 key;空值丟棄) | `2313091595225` |
| 1 | MarketType | TS證券 TA盤後 TL零股 TP興櫃 TC盤中零股 TF期貨 TO選擇權 OF海期 OO海選 OS複委託 | `TS` / `TF` |
| 2 | Type | N委託 C取消 U改量 P改價 D成交 B改價改量 S動態退單 | `C`(user 刪的預約單) |
| 3 | OrderErr | Y失敗 T逾時 N正常 | `N` |
| 6 | BuySell 複合 | 證券:`[0]`B買/S賣 `[1,2]`00現股 01代資 02代券 03融資 04融券 08無券 20零股 40拍賣現股 `[3]`TIF `[4]`價型。期/權:`[1]`Y當沖 N新倉 O平倉 | `B00R2`=買現股ROD限價、`S03R2`=賣融資 |
| 8 | ComId | 商品代碼 | `3357` |
| 11 | Price | N=委託價、D=成交價 | `293.0000` |
| 20 | Qty | TS/TC/OS=股、TF/TO/OF/OO=口;N=委託量 D=成交量 U=減量數 C=原委託剩量 | `1000` |
| 22 | AfterQty | (證券)改量後量 | |
| 23/24 | Date/Time | `20260610` / `14:59:48` | |
| 31 | PreOrder | A盤中單 B預約單 | `B`(收盤後掛的單) |
| 44 | ErrorMsg | OrderErr=Y 時的錯誤訊息 | |

註:價格欄寬鬆解析(失敗→None);其餘欄位拿不到一律 None/0,不炸。

## 架構(方案 A:後端解碼 + 聚合,前端笨渲染)

回報語意集中後端一處;聚合靠 ConnectByID 的當日 backlog 重播天然 stateless(重啟自動重建);未來 bot 成交通知重用同一份解碼。

### 1. `services/capital_reply.py` — 解碼(純函式)

`ReplyRecord` 補欄位:`market`、`buy_sell`("B"/"S")、`flag_label`(現股/代資/代券/融資/融券/無券/零股/拍賣現股;期權:當沖/新倉/平倉/代沖銷)、`time`("14:59:48")、`pre_order`(bool, idx31=="B")、`order_err`(idx3)、`error_msg`(idx44)、`after_qty`(idx22, int|None)。
`_STATUS` 廢除 → `_TYPE = {N:委託, C:刪單, U:改量, P:改價, D:成交, B:改價改量, S:退單}`;`status_raw`=idx2、`status_label`=對照值。

### 2. `services/capital_store.py` — 聚合

`_orders` 改存聚合紀錄 `OrderAgg`(per seq_no):

- `N 委託`:`order_qty=qty`、`price=委託價`、`status=預約中(pre_order)|委託成功`
- `D 成交`:`filled_qty += qty`;`status = 部分成交`(filled<order)/`全部成交`(filled≥order);order_qty==0(異常無 N)時以 filled 顯示
- `C 刪單`:`status=已刪單`(order_qty/filled_qty 不動;C 的 qty 是剩量,不用)
- `U 改量`:`order_qty = after_qty`(無 after_qty 則 `order_qty - qty`)
- `P/B 改價(量)`:更新 price(B 連動 after_qty)
- `S 退單`:`status=退單`
- `OrderErr=Y`:`status=失敗`,存 error_msg(T 逾時同失敗處理、label「逾時」)
- 每筆事件更新 `last_time`;列表照 `last_time` 倒序
- 狀態演進只進不退:晚到的 N(replay 順序)不得把已是 成交/刪單 的狀態打回 委託成功(以 Type 優先級防 replay 亂序:失敗/退單/刪單/全部成交 > 部分成交 > 委託/改價量)

單位換算在 store 出口(`orders()`)做:`unit`=「張」(TS/TA/TP,qty÷1000)/「股」(TL/TC)/「口」(TF/TO/OF/OO)。

### 3. `routes/capital.py` — 過濾 + 名稱 enrich

`/api/capital/orders`:
- **只回證券市場**(TS/TA/TL/TP/TC);期權(TF/TO/OF/OO)存而不回(未來 `?market=futures` 或獨立面板再開)
- 每列補 `name`:查 `local_store.market`(精確 symbol 比對;查無→空字串,前端只顯代號)

### 4. 前端 `OrdersList`(`TradingPanel.tsx`)

照定案 preview 渲染:

```
3357 臺慶科   買·融資         全部成交
317.50 · 1/1 張 · 12:09:48

3357 臺慶科   買·現股 [預約]   已刪單
293.00 · 0/1 張 · 14:59:48
```

- 買=紅(`text-bull`)、賣=綠(`text-bear`),沿用站內紅漲綠跌
- 失敗/退單:狀態紅字 + 下行小字 error_msg
- `成交/委託` 張數一律顯示(0/1 張);單位用後端給的 `unit`
- WS `capital_order` 事件僅作 reload 觸發(現行為不變)

### 5. 測試

- fixture 用 2026-06-10 真實 raw(去個資可保留):現股預約買+刪單、融資買全部成交、融資賣部分成交(1+2+1/4)、期貨 BNR20、TM2606 無 seq_no 丟棄
- `capital_reply`:逐欄解碼斷言(B00R2/S03R2/BNR20 拆解、pre_order、time)
- `capital_store`:聚合演進(委託→部分→全部成交;委託→刪單;OrderErr=Y→失敗;U 改量;replay 亂序不倒退)、張/股/口換算
- `routes`:期貨被過濾、name enrich(monkeypatch market store)
- 前端 vitest:OrdersList 渲染(名稱、紅綠、預約標記、失敗紅字)

## 不做(v1 範圍外)

- 期貨/選擇權清單與期貨帳號下單(未來另開;資料已存)
- 成交均價計算(限價單成交價≈委託價,先顯示委託價)
- 面板刪單/改單功能
- 期貨代號→名稱對照(顯示代號)
- 歷史日委託(ConnectByID 只重播當日;清單=今日)

## 風險

- 官方文件說 KeyNo「國內期選成交單無此欄」— 已被過濾,不影響證券清單;期貨聚合留待期貨面板設計
- U/P/B/S、OrderErr=Y 無真實樣本(今日 12 筆沒有),依文件實作 + 單元測試覆蓋,首次遇到時看 log 驗證
