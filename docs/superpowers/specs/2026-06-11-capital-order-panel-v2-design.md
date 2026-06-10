# 群益下單面板 v2 — 交易種類/TIF/快捷/閃電/庫存平倉 設計規格

- 日期:2026-06-11
- 狀態:設計定案(待 user review → 進 writing-plans)
- 前作:`2026-06-05-capital-order-panel-design.md`(v1 核心交易迴圈)、`2026-06-10-capital-orders-reply-display-design.md`(委託聚合清單)
- 相關 mockup:`.superpowers/brainstorm/363-1781095628/content/`(flash-placement / flash-layout)

---

## 1. 目標

在既有群益下單面板(PR #21 委託聚合鏈已併入)之上,補齊手動交易的完整選項與速度:

1. 交易種類:現股 / 融資 / 融券 / 無券賣出
2. 委託條件:ROD / IOC / FOK + 市價切換
3. 快速張數快捷(填入+累加混合)
4. 價格快捷鈕(跌停 / 平盤 / 漲停)
5. 閃電下單(右欄第二分頁,點價直送)
6. 庫存總覽 + 一鍵平倉/反向

### Non-goals

- **智慧單**(停損停利 / OCO / 移動鎖利,群益 `StockSmartTrade` 券商端代管):user 已選要做,但**不在本 spec**——介面細節須先查群益官方文件,查完獨立開一輪設計(不憑印象寫 API)。
- 期貨、零股、興櫃(`sPeriod=0`/`sPrime=0` 沿用)。
- 考慮過未採(本輪 user 未選):鍵盤快捷鍵下單流、下單模板、預估成本試算、當日風控條、成交音效、訊號→預填下單匣、Discord 成交通知。

---

## 2. 已鎖定的範圍決策(brainstorm 問答結果)

| # | 決策 | 理由 |
|---|---|---|
| 1 | user 交易型態=混合(當沖/波段/信用都用) | 四種交易種類常駐 segmented,不藏下拉 |
| 2 | 帳戶權限全開(信用戶+現股當沖先賣) | 四種類都可實測;無券賣可直接驗 |
| 3 | 張數快捷=填入+累加混合 | 快捷鈕單點填入、再點同顆累加,stepper 微調 |
| 4 | 閃電原型=群益 App | 中央價格軸、左買右賣、點價直送 |
| 5 | 閃電擺放=右欄分頁,順序「下單\|⚡閃電\|委託」 | user 指定閃電排第二;(瀏覽器事件顯示曾考慮浮動視窗,未採,留未來參考) |
| 6 | 閃電內容布局=mockup v1 核可 | 武裝開關/階梯/我N徽章/回中鈕/底部快捷,見 §5 |
| 7 | fat-finger=±5% 外灰掉 | 武裝直送的唯一防手滑;掛遠單走下單匣完整確認流程 |
| 8 | 優先序=下單匣強化 → 閃電 → 庫存平倉 → 智慧單 | 盤中速度最重要 |
| 9 | 本 spec 範圍=Phase 1-3,智慧單獨立下輪 | StockSmartTrade 細節未釘死,寫了也要重開 |

---

## 3. 現況前提(讀過 code 的事實)

- `StockOrderRequest` **已有** `price_type`(limit/market)、`time_in_force`(ROD/IOC/FOK)、`trade_kind`(cash/margin/short)欄位與預設值;`capital_mapping.to_stockorder_fields` 已映射 `nSpecialTradeType`/`nTradeType`/`sFlag`——**前端從未露出,一直用預設(限價+ROD+現股)送**。
- `TradeKind` 缺「無券」;群益官方範例已釘 `sFlag = 0/1/2/3 = 現股/融資/融券/無券`(前作 spec §4.2)。
- `correct_price` 末參數 `nTradeType` 硬編 0(ROD)——IOC/FOK 不留簿,改價語意只對 ROD 成立,**維持不動**。
- 富邦 `intraday.quote`(已 WebFetch 文件確認):漲跌停只有**布林**(`isLimitUpBid` 等,已接),**無漲跌停價數值**;有 `referencePrice`(今日參考價)但 route 未往前傳。
- 五檔=`useQuoteBook` REST 1 秒輪詢(背景 tab 暫停);現價=WS tick(`subscribeTicks`);委託清單=`useCapitalOrders` store,`OrderRecord.actionable` 由後端下發。
- 前端測試慣例:無 hook 測試環境,抽 lib 純函式測。
- 安全鏈現況:總開關 `CAPITAL_ORDER_ENABLED`、市場閘(僅證券)、張數/金額上限、NaN/負價擋、送出 debounce、二次確認彈窗、稽核 `capital_orders.jsonl`。

---

## 4. Phase 1 — 下單匣強化

### 4.1 後端

1. `TradeKind` 加 `DAYTRADE_SELL = "daytrade_sell"`(無券賣出);`capital_mapping._FLAG` 加映射 `→ 3`。
2. 安全閘新規:**`trade_kind=daytrade_sell` 且 `buy_sell=buy` 直接拒**(寫稽核)。無券只有賣出;當沖回補=現股買進,交易所自動沖銷。
3. `GET /api/quote/{symbol}` 回傳加 `reference_price`(富邦 `referencePrice` 透傳;缺值回 `null`)。
4. `StockOrderRequest` 加選用欄位 `source: "panel" | "flash" = "panel"`,寫進稽核 jsonl(出事分得清單從哪個介面來)。

### 4.2 前端下單匣(`TradingPanel` 下單 tab)

- **交易種類** segmented 四鈕常駐:`現股|融資|融券|無券`。選「無券」→ 自動切到賣出、買進鈕 disabled。
- **TIF** segmented:`ROD|IOC|FOK`,預設 ROD。
- **市價 toggle**:勾選後價格欄 disabled,送 `price_type=market`。
- **價格快捷鈕**:`跌停|平盤|漲停` 三小鈕(平盤=reference_price 原值;漲跌停=±10% 後按 tick 取整:漲停向下取、跌停向上取)。reference_price 為 null 時三鈕 disabled。
- **張數快捷**:`1|3|5|10` 四鈕+既有輸入框改 stepper。單點=填入該值;再點同一顆=累加(點 5→5、再點 5→10);點不同顆=重新填入。
- 確認彈窗(`OrderConfirmDialog`)顯示:交易種類/TIF/市價(或限價+價格)。
- 委託清單 `flag_label` 既有,無券單會自然顯示。

### 4.3 新 lib 純函式(Phase 2 共用引擎,完整測試)

- `lib/tick.ts`:`tickSize(price)`(台股 6 級距:<10→0.01、10-50→0.05、50-100→0.1、100-500→0.5、500-1000→1、≥1000→5)、`roundToTick(price, dir)`、`limitUp(ref)`、`limitDown(ref)`。
- `lib/qty-quick.ts`:張數快捷的填入/累加/切顆狀態機。

---

## 5. Phase 2 — 閃電下單

### 5.1 前端 `FlashPanel.tsx`(右欄第二分頁)

布局照核可 mockup(`flash-layout.html`):①標的+現價列 → ②武裝開關 → ③階梯(委買量|價|委賣量)→ ④回中鈕 → ⑤張數快捷+stepper → ⑥交易種類四鈕 → ⑦狀態列(掛單數/部位)+全部刪單。

- **階梯生成**:以現價為中心、tick 引擎上下各生成 ~30 檔,範圍夾在 `[limitDown, limitUp]`;現價列金色高亮;自動置中跟隨,手動滾動暫停跟隨、「回到現價」恢復。
- **資料**:深度=`useQuoteBook`(1 秒);現價=WS tick;「我N」徽章=委託 store 該價位 `actionable` 單張數聚合。
- **點價送單**:左欄=該價掛買、右欄=該價掛賣;固定**限價 ROD**(IOC/FOK 留在下單匣);張數/種類=面板當前值;無券時買側整排鎖住;同價位 500ms 防抖。
- **fat-finger**:離現價 ±5% 外的價位列 disabled(視覺反灰);掛遠單走下單匣。
- **點「我N」=刪該價位全部活單**(逐筆走既有 cancel);**全部刪單**保留確認彈窗。
- **武裝開關**:
  - 未武裝(預設):點價只閃提示不送單。
  - 武裝:點價直送、無確認彈窗——**唯一**繞過二次確認的路徑。
  - 自動解除:切分頁 / 換標的 / 群益連線斷 / 閒置 5 分鐘 / 連續 3 次送單失敗。
  - `CAPITAL_ENV=prod` 時武裝列紅底全程警示。

### 5.2 後端

無新 endpoint。沿用 `POST /order/stock`(`source="flash"`)與 `/order/cancel`——總開關、上限、市場閘、稽核**一條都不少**,閃電只省「前端」確認彈窗。

---

## 6. Phase 3 — 庫存總覽 + 一鍵平倉

### 6.1 前端

- 分頁變四個:`下單|⚡閃電|委託|庫存`。
- 庫存列:代號/名稱/張數(空單負值)/均價/現價/未實現損益(額+%);頂部總未實現損益;點列帶標的回下單匣。
- 現價:已訂 WS tick 的標的即時;其餘開分頁時打既有 `/api/quotes/snapshot` 批次補、每 30 秒刷新。
- 「平」鈕 → 確認彈窗預覽反向單(方向/種類/張數/價;預設全部張數+市價,可切限價帶現價)→ 送出。

### 6.2 後端 `POST /api/capital/position/close`

- body:`{stock_no, qty?(預設全部), price_type?(預設 market), price?}`。
- 反向單組裝規則(固定映射):現股多→現股賣;融資多→融資賣(資賣);融券空→融券買(券補);無券空→現股買(交易所自動沖銷)。
- `qty` 超過持有量直接拒(以後端最新部位快取驗)。
- 走完整安全閘(總開關/上限/稽核),`source` 記 `"panel"`。

---

## 7. 錯誤處理

- 沿用既有:nCode≠0 → 群益訊息顯示+寫稽核;`OnNewData` 回報更新委託 store;送單後 refetch 去抖。
- 閃電:單次失敗 toast;連續 3 次失敗自動解除武裝。
- 平倉:前端按鈕點擊即鎖到回應;後端驗量防快取過時超賣。
- reference_price 缺值:價格快捷鈕 disabled、閃電階梯漲跌停夾界退化為「現價 ±10% 估算」並在面板標示「估」。

---

## 8. 測試策略(測意圖,改壞商業邏輯必 fail)

**後端:**
- `sFlag` 映射含無券=3;「無券+買進」必拒(且留稽核)。
- `source` 欄位落進稽核 jsonl。
- 平倉反向組裝四規則各一測;qty 超持有必拒。
- `reference_price` 透傳與缺值 null。

**前端 lib:**
- `tick.ts`:六級距邊界值(10/50/100/500/1000 跨界)、漲停向下取/跌停向上取、千元股 tick=5。
- `qty-quick.ts`:填入/同顆累加/切顆重填。
- 階梯生成:中心錨定、漲跌停夾界、±5% 灰區邊界。
- 「我N」聚合(僅 actionable 單)、平倉預覽組裝。

**盤中實測門檻(測試環境先行,後 prod 小額):**
- 四種交易種類各一筆(無券需可先賣標的)。
- IOC/FOK 實際行為(部分成交/全取消)。
- 閃電:武裝→點價→秒到回報;點「我N」刪單;自動解除五條件。
- 平倉全鏈(現股多/融券空各一)。

---

## 9. 開放項(實作前釘死,不擋設計)

1. **信用部位資料來源**:`GetRealBalanceReport` 是否含融資/融券部位、或另有查詢——Phase 3 動工前對群益官方文件釘死;拿不到就先顯示現股、信用列為已知缺口。
2. **市價單 `bstrPrice` 填值慣例**(空字串或 "0")——實作時對官方範例釘。
3. **無券賣標的資格**(可先賣清單):v1 不做前端驗證,靠券商退單+錯誤訊息呈現。
4. 智慧單 `StockSmartTrade` 全部細節 → 下一輪 spec。

---

## 10. 風險

- 武裝直送=真錢高速路徑:多層緩解(預設未武裝、五條件自動解除、±5% 灰區、後端閘全保留、prod 紅底)。首次實測必走測試環境。
- IOC/FOK 與市價的群益端行為未實測:盤中實測門檻覆蓋。
- 五檔 1 秒輪詢的深度時滯:量橫條僅供參考,點價送的是「價位」非「量」,可接受;未來可換 WS 行情再優化。
- 無券賣標的不可先賣時的退單體驗:錯誤訊息已有呈現鏈(`last_error`/委託列 error_msg)。
