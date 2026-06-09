# 群益下單面板 — 設計規格

- 日期:2026-06-05
- 狀態:設計定案(待 user review → 進 writing-plans)
- 作者:brainstorming 對話產出
- 相關 mockup:`.superpowers/brainstorm/391-1780585522/content/`(tray-directions / monitor-full-page / monitor-v2-no-tape / flash-order / trading-panel-tabs)

---

## 1. 目標

在現有系統(富邦 Neo 行情 + 訊號引擎)右側新增一個**群益下單面板**,讓 user 用群益帳號**手動**下台股單,並就地看到委託回報、持倉與帳務。富邦那條(行情 / 訊號 / 自選 / 分時 / 五檔)**完全不動**。

### Non-goals
- 不做自動下單 / 訊號觸發自動送單(本期只有「使用者主動按鈕」)。
- 不做多帳號(單一群益帳號)。
- 不用群益行情(行情仍走富邦)。
- 期貨下單列入 v3,本期以台股為主。

> CLAUDE.md 原有約束「僅訂閱行情、不下單,加下單功能前必須先跟 user 確認」—— 本 spec 即為該確認結果:user 同意新增**群益**下單(與富邦無關,富邦仍不下單)。

---

## 2. 已鎖定的範圍決策

| # | 決策 | 理由 |
|---|---|---|
| 1 | 群益只做**台股手動下單** + 回報 + 部位/帳務查詢 | user 真正下單用群益;富邦只行情 |
| 2 | **測試環境優先**(`SetAuthority(2)`,`CAPITAL_ENV=test` 預設) | 不碰真錢先把整條鏈路驗通 |
| 3 | 後端架構 **A:同進程專屬 COM 執行緒 + 訊息幫浦** | 與既有富邦「同步 SDK 包執行緒」一致、部署最簡 |
| 4 | 下單面板 = Monitor **最右側欄**,取代被移除的「明細」欄 | 明細幾乎沒用;空間讓給下單 |
| 5 | 面板用**分頁**:額度列長駐 + `下單 / 委託 / 庫存` | 5 類資訊塞不進單欄 |
| 6 | 期貨(TF)、閃電下單、智慧單列 **v3** | 風險/複雜度高,先穩核心 |

---

## 3. 現況前提(讀過 code 的事實)

- 後端 FastAPI 全 async;富邦 `services/fubon_client.py` 用 `asyncio.to_thread` 包同步 SDK;目前**不呼叫 `place_order`**。
- 本機優先儲存(JSON/JSONL),已移除 Supabase。
- 前端 React + Tailwind,Monitor 主頁為 4 欄 grid `300px | 460px | 1fr | 300px`(觸發歷史 / 書籤+自選 / 分時+五檔 / 明細)。
- 主題(實際 token):底 `#14110c`、面板 `#1d1812`、輸入 `#0d0a07`、邊框 `#2e2a22`/`#4a4234`、文字 `#ede4d3`;**買/漲=紅 `#e85a4f`、賣/跌=綠 `#7fc99a`**(台股紅漲綠跌,與美股相反)。字型 Source Serif 4 + Inter Tight。
- 逐筆成交 `tick` 廣播被 `useTradeTape`(明細)、`useWatchlistQuotes`(自選最新價)、`useIntradayCandles`(分時圖)、後端 `signal_engine`(ring_buffer)共用 → **移除明細只能刪前端 UI,後端 trades 管線必須保留**(已驗證,見 §8)。

---

## 4. 群益 API 參考(以官方 Python 範例 V2.13.58 釘樁,非憑印象)

COM 元件 `SKCOM.dll`,透過 `comtypes` 使用,事件驅動。

### 4.1 初始化 / 登入(下單必經)
```python
comtypes.client.GetModule('SKCOM.dll')
import comtypes.gen.SKCOMLib as sk
skC = CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
skO = CreateObject(sk.SKOrderLib,  interface=sk.ISKOrderLib)
skR = CreateObject(sk.SKReplyLib,  interface=sk.ISKReplyLib)

skC.SKCenterLib_SetAuthority(2)          # 0=正式, 2=測試環境
skC.SKCenterLib_Login(user_id, password) # 回 nCode
skO.SKOrderLib_Initialize()
skO.ReadCertByID(user_id)                 # 讀憑證,無憑證不能下單
# 事件:comtypes.client.GetEvents(skR, ReplyHandler) / GetEvents(skO, OrderHandler)
```
`SKCenterLib_GetReturnCodeMessage(nCode)` 把回傳碼轉中文訊息。

### 4.2 送單(台股)
```python
pOrder = sk.STOCKORDER()
pOrder.bstrFullAccount = full_account   # 分公司IB(4)+帳號(7)
pOrder.bstrStockNo = "2330"
pOrder.sBuySell = 0/1                    # 買=0 賣=1
pOrder.bstrPrice = "590.00"
pOrder.nQty = 1                          # 張(整股)
pOrder.nTradeType = 0/1/2               # ROD/IOC/FOK
pOrder.nSpecialTradeType = 1/2          # 市價=1 限價=2
pOrder.sFlag = 0/1/2/3                  # 現股/融資/融券/無券
pOrder.sPeriod = 0/1/2/4               # 盤中/盤後/零股/盤中零股
pOrder.sPrime = 0/1                    # 上市櫃/興櫃
bstrMessage, nCode = skO.SendStockOrder(user_id, bAsync, pOrder)
```
> 群益原生價格類型只有市價/限價;漲停/跌停/平盤是台股慣例,前端做快捷鈕、送單時換算成限價+對應價。

### 4.3 改 / 刪單(同步回 `(message, nCode)`,亦支援非同步)
| 動作 | 函式 | 鍵 |
|---|---|---|
| 刪單 | `CancelOrderBySeqNo / CancelOrderByBookNo / CancelOrderByStockNo` | SeqNo / BookNo / StockNo |
| 改價 | `CorrectPriceBySeqNo(…, SeqNo, Price, nTradeType)` / `CorrectPriceByBookNo(…, MarketSymbol="TS", BookNo, …)` | SeqNo / BookNo |
| 改量(減量) | `DecreaseOrderBySeqNo(…, SeqNo, DecreaseQty)` | SeqNo |

### 4.4 查詢(**皆非同步,結果走事件**)
| 用途 | 函式 | 結果事件 |
|---|---|---|
| 即時庫存(張數/均價) | `GetRealBalanceReport(UserID, FullAccount)` | `OnRealBalanceReport(bstrData)` |
| 未實現損益 | `GetProfitLossGWReport(UserID, TSPROFITLOSSGWQUERY)`(`nTPQueryType=0` 未實現) | `OnProfitLossGWReport(bstrData)` |
| 融資額度 | `GetMarginPurchaseAmountLimit(UserID, FullAccount, StockNo)` | `OnMarginPurchaseAmountLimit(bstrData)` |
| 集保券量 | `GetBalanceQuery(UserID, FullAccount, StockNo)` | `OnBalanceQuery(bstrData)` |

### 4.5 回報(主動,最重要)
`OnNewData(UserID, bstrData)` —— `bstrData` **逗號分隔**字串。已知欄位索引:
`[0]委託序號 SeqNo`、`[2]委託種類`、`[3]委託狀態`、`[8]商品代碼`、`[10]委託書號 BookNo`、`[11]價格`、`[20]數量`、`[23][24]日期/時間`、`[-4][-3]錯誤訊息`。
> 委託狀態的完整 enum 值、欄位全表 → **開放項**(見 §12),實作時對 `12.回報.docx` / 官方文件釘死。

### 4.6 已知群益慣例(實作時依範例確認)
- `SKReplyLib` 需實作 `OnReplyMessage` 並回傳 `-1` 以抑制彈窗。
- 連線/憑證:群益 session 通常**每日需重登**;憑證有到期。

---

## 5. 後端架構

### 5.1 `services/capital_client.py`(新增,單例)
一條**專屬長命 COM 執行緒** `_com_thread`,負責所有群益互動(COM 物件有執行緒親和性,送單與收事件必須同一條):

```
_com_thread:
  1. comtypes GetModule / CreateObject(SKCenter/SKOrder/SKReply)
  2. GetEvents 註冊事件 handler(OnNewData / OnRealBalanceReport /
     OnProfitLossGWReport / OnMarginPurchaseAmountLimit / OnAsyncOrder / OnReplyMessage→-1)
  3. SetAuthority(env) → Login → SKOrderLib_Initialize → ReadCertByID
  4. loop:
       pythoncom.PumpWaitingMessages()   # 收事件
       drain command queue(送單/改刪/查詢,在本執行緒上呼叫群益)
       sleep(short)
```

- **下單請求**:API → `queue.Queue` → COM 執行緒取出執行 → 結果以 `loop.call_soon_threadsafe` 回填 asyncio future。
- **事件 → 快取**:`OnNewData` 更新「委託/成交」快取;`On*Report` 更新「庫存/損益/額度」快取。因查詢是非同步事件,**採「快取 + 推播」模型**(查詢=觸發刷新,事件=快取更新),避免逐筆 request/response 對應。
- **健康狀態**:`status ∈ {ok, degraded, error}`,比照富邦 `_background_retry`;登入/憑證失敗 → degraded,**不影響富邦**。
- **隔離**:本檔不 import 富邦任何東西;富邦不 import 本檔。

### 5.2 `routes/capital.py`(新增)
- `GET  /api/capital/status` — 群益連線/登入/憑證健康
- `POST /api/capital/order/stock` — 送單(過安全閘)
- `POST /api/capital/order/cancel` — 刪單
- `POST /api/capital/order/modify` — 改價/改量
- `POST /api/capital/position/close` — 一鍵平倉/反向(= 反向市價/限價單,過安全閘)
- `GET  /api/capital/orders` — 今日委託/成交(讀快取)
- `GET  /api/capital/positions` — 庫存(讀快取,含融券/做空)
- `GET  /api/capital/account` — 額度/可用資金(讀快取)
- WS:群益事件(回報/部位更新)推前端(獨立 channel,不混富邦 tick)

### 5.3 設定(`.env`)
```
CAPITAL_USER_ID=          # 群益登入帳號
CAPITAL_PASSWORD=
CAPITAL_FULL_ACCOUNT=     # 分公司IB(4)+帳號(7)
CAPITAL_ENV=test          # test|prod,預設 test
CAPITAL_ORDER_ENABLED=false  # 下單總開關,預設關
CAPITAL_MAX_QTY=          # 單筆張數上限
CAPITAL_MAX_AMOUNT=       # 單筆金額上限
CAPITAL_FEE_RATE=0.001425 # 手續費率(可含券商折數)
CAPITAL_FEE_DISCOUNT=     # 手續費折數
CAPITAL_TAX_RATE=0.003    # 證交稅(賣出;當沖另計)
```

---

## 6. 前端設計

### 6.1 `TradingPanel.tsx`(取代明細欄)
> 以下為**完整面板(end-state)**;各部分落在哪個 phase 見 §9。
- 頂部:**群益連線健康燈 + 環境徽章**(測試/正式)長駐;未就緒時送單鈕 disabled。**額度列**(可用資金/已用額度/使用率)於 **v2** 接上(待 §12 #1 釘死現股可用資金函式)。
- 分頁 **`下單 / 委託 / 庫存`**(委託/庫存帶未成交數/部位數 badge)。

**下單 tab**
- 下單匣:股票代號、買賣(大按鈕,買紅賣綠)、委託價、數量(stepper)、價格類型(限/市/漲停/跌停/平盤)、委託類別(ROD/IOC/FOK)、交易種類(現股/融資/融券)。
- **目前標的部位卡**:有部位才顯示張數/均價/現價/未實現損益(即時);無則「無部位」。

**委託 tab**:今日委託/成交列(買賣別、代號、價、量、狀態:委託中/部分成交/全部成交/已刪),未成交列可**改價/刪單**。

**庫存 tab**:全部部位(含**融券/做空**,負張數),每列 張數/均價/現價/未實現損益(額+%);頂部總未實現損益;每列可**一鍵平倉/反向**;點列帶回下單匣。

### 6.2 新 hooks / 連動
- `useCapitalStatus / useCapitalOrders / useCapitalPositions / useCapitalAccount`(REST + WS 推播)。
- **五檔連動**:點 `QuoteBook`(五檔)任一價 → 帶入下單匣委託價。
- **點列帶代號**:自選股 / 觸發歷史 / 庫存 / 委託 任一列 → 代號(必要時連價)帶入下單匣。
- 實作:一個輕量 shared store(React context 或既有 bus 模式)存「選定 symbol/price」,QuoteBook / 各清單 emit,下單匣 subscribe。

### 6.3 即時未實現損益(設計亮點)
`未實現損益(毛) = 張數 × 1000 × (富邦現價 − 群益均價)`。
- 均價/張數來自群益庫存(`GetRealBalanceReport`),現價來自**富邦**(重用 `subscribeTicks`)→ 每跳一個富邦 tick 即時重算,不必一直跟群益要。
- **淨損益** = 毛 − 估算手續費(買+賣)− 證交稅(賣);費率取 `.env`。庫存/部位卡同時可顯示群益官方損益(刷新時)以對帳。

---

## 7. 安全設計(真錢核心)

| 機制 | 內容 |
|---|---|
| 主開關 | `CAPITAL_ORDER_ENABLED` 預設 `false` → 擋掉**所有**寫入(下單/改/刪/平倉),API 回 423 |
| 環境 | `CAPITAL_ENV=test` 預設 → `SetAuthority(2)`;`prod` 時 UI 全程**紅底警示** |
| 二次確認 | 下單/改/刪/平倉 送出前彈窗(環境+標的+買賣+量+預估金額);閃電模式改用武裝開關(v3) |
| 上限 | 超過 `CAPITAL_MAX_QTY` / `CAPITAL_MAX_AMOUNT` 直接擋 |
| 下單前檢查 | 可用資金不足、限價離現價過遠(fat-finger)警告 |
| 防呆 | 送出 debounce;「短時間內已下過同樣的單」提醒,避免誤觸/重送 |
| 稽核 | 每筆寫入(下單/改/刪)寫 `data/capital_orders.jsonl`(時間/環境/參數/nCode/訊息) |
| 對帳 | 斷線重連後重查委託(避免漏接 `OnNewData` 造成狀態不一致) |
| 隔離 | 群益崩潰 → degraded,不影響富邦行情/訊號 |

---

## 8. 明細(TradeTape)移除 —— 純前端(已驗證)

**只刪前端:**
- `frontend/src/components/TradeTape.tsx`(整檔)
- `frontend/src/hooks/useTradeTape.ts`(整檔)
- `frontend/src/pages/Monitor.tsx`:移除 TradeTape import + 第 4 欄 JSX;grid 由 4 欄改為「3 欄 + 下單面板」。

**後端完全不動**:`trades` 訂閱 / `ring_buffer` / `tick` 廣播都保留 —— `signal_engine`(訊號)、`useWatchlistQuotes`(自選最新價)、`useIntradayCandles`(分時圖)仍依賴。
> 驗證:`subscribeTicks` 消費者含 `useWatchlistQuotes`(grep 確認);刪 `tick` 廣播會打爆自選報價與分時圖,故不可動後端。

---

## 9. 分期

> 實作計畫(writing-plans)從 **M0 環境前置 → M1 測試環境登入除風險 → v1** 起,逐 phase 展開。

### v1 — 核心交易迴圈(測試環境)
- 移除明細 → `TradingPanel` 進場(grid 改版)。
- 下單匣 + 五檔連動 + 點列帶代號。
- `capital_client` COM 執行緒 + 登入/憑證 + `SendStockOrder`。
- 回報 `OnNewData` → 委託 tab 狀態。
- 該標的部位卡 + 即時未實現損益(毛+淨)。
- 連線健康燈、錯誤呈現、下單前檢查、防呆、稽核、本地持久化 + 對帳。
- 全套安全閥(主開關/二次確認/上限)。

### v2 — 帳務全貌 + 委託管理
- 庫存 tab(全部部位,含融券/做空,淨損益)+ 總損益。
- 額度列(可用資金/已用額度)—— 待 §12 開放項釘死。
- 改價/刪單。
- 一鍵平倉/反向。

### v3 — 進階
- 閃電下單(價格階梯 DOM + 武裝/解除開關 + 測試環境 + 口數上限,取代二次確認)。
- 期貨(TF):`SendFutureOrder`、未平倉/口數(`GetOpenInterest`)。
- 智慧單:停損停利 / 條件單 / OCO / MIT(群益券商端代管)。

### Future(目前不做)
- 成交/委託 推 Discord(可重用 `discord_notifier`)。

---

## 10. 資料流(摘要)

- **送單**:UI → 二次確認 → `POST /order/stock` → 安全閘 → queue → COM 執行緒 `SendStockOrder` → `(msg,nCode)` 回 UI;稽核寫檔。
- **回報**:群益 `OnNewData` → COM 執行緒 → 更新委託快取 + WS 推 → 委託 tab 即時更新。
- **部位/損益**:定時 + 成交後觸發 `GetRealBalanceReport`/`GetProfitLossGWReport` → `On*Report` 更新快取;前端 `富邦 tick` 即時重算毛/淨損益。
- **額度**:查詢 → 事件 → 快取 → 額度列。

---

## 11. 測試策略

- **單元測試**(mock 掉 COM 層,把群益物件換 fake):
  - 主開關 `false` 時必擋下單/改/刪/平倉(回 423)。
  - 數量/金額超限必擋。
  - `CAPITAL_ENV` → `SetAuthority` 參數對應正確(test→2)。
  - `SendStockOrder` 結果 `(msg,nCode)` 解析、錯誤碼呈現。
  - `OnNewData` 逗號字串解析 → 委託狀態映射正確。
  - 未實現損益毛/淨計算正確(含費率)。
  - 這些測「商業意圖」:改錯安全邏輯或損益口徑必 fail。
- **整合驗證**:測試環境實際登入 + 憑證 + 送一筆測試單 + 收回報,跑通整條鏈路(M1 先除此風險)。

---

## 12. 開放項(實作前需釘死,均不擋 v1)

1. **現股可用資金/已用額度的確切函式** —— 範例只有融資 `GetMarginPurchaseAmountLimit`;現股可用資金/銀行餘額需查群益官方文件(`4.下單準備介紹.docx` / `5.下單-國內證券.docx`)。在 **v2** 前確認。
2. **`OnNewData` 委託狀態 enum 完整值** 與欄位全表 —— 對 `12.回報.docx` / 官方文件。
3. **`OnReplyMessage` 回傳 -1** 與各 `On*Report` 字串欄位格式 —— 依範例 Reply.py 釘死解析。
4. **群益測試環境**是否需獨立測試帳號、開放時段 —— M1 登入時實測確認。
5. 智慧單(v3)的 `StockSmartTrade` 介面細節 —— 到 v3 再展開。

---

## 13. 環境前置(M0,實作前)
- `元件\x64\install.bat` 以系統管理員 `regsvr32` 註冊 `SKCOM.dll` 等。
- `pip install comtypes`(後端 venv,64-bit Python 3.13,與 x64 元件對齊)。
- 群益帳號已**開通 API 權限**(線上申請憑證 + 簽 API 同意書),憑證安裝到本機。

---

## 14. 風險
- COM ↔ asyncio 橋接(執行緒親和性 + 訊息幫浦):用單一專屬執行緒 + queue/future 收斂。
- 真錢誤觸:多層安全閥 + 測試環境優先;閃電下單延到 v3 並用武裝開關。
- 群益每日重登 / 憑證到期:健康燈 + degraded + 背景重試;盤中清楚提示。
- 狀態不一致(漏接回報):重連對帳重查委託。
- comtypes + Python 3.13 + 群益舊 COM 的相容性:M1 先實測。
