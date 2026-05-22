# 大單偵測 + 內外盤訊號系統 — 給專業交易人士 Review

**Date**: 2026-05-20
**對象**: 台股 / 台指期 專業交易人士
**目的**: 第三輪 review,著重策略合理性 + 需要您決策的問題

---

## 系統定位(一頁版)

| 維度 | 描述 |
|---|---|
| **使用者** | 個人投資者兼職盯盤(非 HFT、非機構) |
| **用法** | 即時訊號 alert,使用者看完自己判斷下單 |
| **執行方式** | 純監控,系統不自動下單 |
| **監控範圍** | 使用者自選 watchlist + 自動納入強勢股(上市櫃,漲幅 ≥ 6% + 成交量 ≥ 3000 張,不含漲停股,最多 50 檔) |
| **資料來源** | 富邦證券 API(即時五檔 + 成交明細,~1 秒延遲) |
| **訊號 latency** | 從事件發生到使用者看到 alert ~ 1-2 秒 |
| **目標** | 給使用者比大眾早 / 跟大眾相反 的訊號 edge |

---

## 設計哲學

經過兩輪 review,設計圍繞 4 個信念:

### 1. 訊號不該跟散戶看盤軟體相同 → 被獵殺

XQ、群益、富邦 e01 教散戶的「五檔大量」、「連續紅單」、「外盤掃單」等訊號,主力知道散戶在看 → **故意製造假象釣魚**。我們刻意:

- **降權所有 commodity 訊號**(B 系列 / A1)
- **升權物理事件**(Trade-Through、A_pull layering)
- **升權 contrarian 訊號**(C2.5′ 假突破,主力不會自製)

### 2. 物理事件 > 統計訊號

統計訊號(連續 5 筆同向、size 5× 平均)易被 wash trade 污染 — 主力 A/B 帳戶配對成交就能滿足。物理事件(穿價成交、撤大單)需要實際資金動作,wash 抗性高。

### 3. 訊號可用性 > 訊號數量

70 檔監控 × 多訊號家族 = 每天可能 100-300 個訊號,使用者 2 週內必關 notification。所以加 5 層風控過濾(Safe Mode / Priority / Liquidity Guard / Throttle / Action Type)讓最後對外的 alert 數量降到使用者能處理的範圍。

### 4. 主力套路本身是訊號

Wash trade / Spoofing / Layering 在中小型股比例特別高,**不是干擾,是核心訊號**。偵測到主力做局 → 該檔所有訊號降可信度 + 反向操作機會浮現。

---

## 訊號清單

11 個訊號分三類:**對外即時 alert(9 個)** + **純內部記錄(5 個,只 log 供未來回測,不通知使用者)** + **1 個 meta filter(訊號濾波)**。

### 對外即時 Alert(9 個)

**第一組 — 進場類**

| 訊號 | 直覺 | 觸發條件(交易語言) | 動作分類 | 等級 |
|---|---|---|---|---|
| **Trade-Through 穿價成交** | 大戶 conviction 最強訊號 | 單筆成交張數 > 該方向五檔總量,且穿價後最佳價跳 ≥ 1 tick | 主進場 | Strong |
| **A3 墊單被吃** | 主力把擋在前面的單清掉 | 之前偵測到的五檔大牆,size 跌 ≥ 70% 且該方向成交量 ≥ 原牆 × 50% | 確認(獨立進場太晚) | Strong |
| **B2 連續單向掃單** | 主動方持續壓 / 拉 | 5 秒內連續 ≥ 5 筆同方向成交,總量 ≥ 過去 5 分鐘每秒平均 × 5 | 主進場(需配 B3 或 wash inactive) | Strong |
| **B3 內外盤量失衡** | 60 秒慢性傾斜 | 過去 60 秒外盤量 / 內盤量 ≥ 3.0,總量 ≥ 平常半分鐘量 | 中等進場 | Medium |

**第二組 — 風險警示類**

| 訊號 | 直覺 | 觸發條件(交易語言) | 動作分類 | 等級 |
|---|---|---|---|---|
| **A_pull Layering(主力做局)** | 整檔該 session 不可信 | 同一檔 5 分鐘內出現 ≥ 3 次墊單→撤單(沒成交),代表主力擺多層假牆 | 風險警示(該檔所有訊號降一級可信度) | Strong (Risk) |

**第三組 — 觀察類(預設摺疊不發 notification)**

| 訊號 | 直覺 | 觸發條件 | 動作分類 | 等級 |
|---|---|---|---|---|
| **A_pull Slow(慢撤)** | 典型 spoofing 但 price 方向不定 | 大牆掛 ≥ 10 秒後撤掉,該方向幾乎沒成交(< 原牆 × 30%) | 觀察(無明確進場方向) | Medium |
| **A1 墊單出現** | 主力擺牆(壓單 / 撐單方向歧義) | 五檔某檔 size 突然變 5× 平均深度,持續 ≥ 5 秒 | 觀察 | Informational |
| **B1′ 短時急動** | 3 秒跳 3 tick(現股負期望) | 過去 3 秒 price 淨移動 ≥ 3 tick | 當沖觀察(現股慎用) | Informational |

**第四組 — 平倉訊號(只給已 hold position 的人用)**

| 訊號 | 直覺 | 觸發條件 | 動作分類 | 等級 |
|---|---|---|---|---|
| **C2.5′ Chain(完整鏈失敗)** | 完整突破鏈失敗 → 確認平倉 | A3 → 突破 CDP/MA ≥ 3 tick → 反向跌穿 ≥ 3 tick | 平倉(認賠 / 鎖利) | Strong (Exit) |
| **C2.5′ Independent(短假突破)** | 快速假突破 → 暫時平倉觀望 | 30 秒內穿越 CDP/MA ≥ 2 tick 再反向跌穿 ≥ 2 tick | 平倉 | Medium (Exit) |

### 純內部記錄(5 個,不對外發送)

只寫入 log 供未來量化回測,使用者看不到、不能 enable / disable。

| 訊號 | 為何只 log 不對外 |
|---|---|
| **A_pull Fast(< 1s 撤)** | 撤太快跟程式單 cancel-replace 分不開,雜訊太高 |
| **C2′ 牆破突破 CDP/MA** | CDP / MA 是 retail 公開資訊,主力專門做假突破騙進場 → 對外發 = 等於送目標給主力 |
| **C3a′ 接近回測線** | 同上,完整 chain 越長越容易被刻意製造 |
| **C3b′ 真正觸線** | 同上 |
| **C3c′ 線守住反彈** | 同上 |

備註:C2.5′(假突破)逆向是 contrarian — **主力不會自製 C2.5′**(這違反他的意圖),所以 C2.5′ 升 Tier A 對外發。

### Meta Filter(訊號污染指標)

| 名稱 | 邏輯 | 用途 |
|---|---|---|
| **Wash Trade Detector** | 1 秒內出現相近 size + 相近 price + 反向主動方向的成交配對;5 分鐘累計 ≥ 3 次 → wash pattern active | 套用所有對外訊號 — wash active 時對應訊號標 LOW confidence,UI 灰色顯示 |

---

## 動作分類(5 種 action_type)

訊號 metadata 強制標記動作類型,UI 區分對待 — 避免使用者照訊號操作反向用錯。

| action_type | 語意 | UI 行為 |
|---|---|---|
| `primary_entry` | 獨立可進場 | 桌面通知 + 聲音 + TriggerList 醒目色(綠 / 紅 by direction) |
| `confirmation` | 確認類,配合其他訊號用 | TriggerList 醒目但**不發桌面通知** |
| `medium_entry` | 中等強度進場(配 confirmation 升級為 primary) | TriggerList 高亮 |
| `exit_signal` | **只給已 hold position 平倉用,不該反向進場** | TriggerList 紫色標「平倉」icon |
| `observation` | 純資訊,不應 actionable | TriggerList 灰色標「觀察」icon,**預設摺疊** |
| `intraday_observation` | 限當沖場景觀察 | TriggerList 標「當沖」icon |
| `risk_warning` | 風險警示(整檔不可信) | TriggerList 標「警示」icon + 該檔所有訊號降一級 confidence |

**重要設計決定**:`exit_signal` 訊號(C2.5′ 系列)不該當反向進場用 — 假突破後反向 price 能走多遠不確定,r/r 比差;但用作平倉很有意義(認賠 / 鎖利的明確 trigger)。

---

## 5 層風控過濾

每個訊號從觸發到對外發送,經過 5 層過濾:

```
[訊號觸發]
    ↓
[1] Safe Mode 過濾    — 市況不該交易時整體暫停
    ↓
[2] 訊號內部判定      — 訊號自身條件
    ↓
[3] Priority / 抑制    — 同事件多訊號 dedupe,跨事件 suppression
    ↓
[4] Liquidity Guard   — 流動性可行性評分
    ↓
[5] Throttle          — 每日訊號上限
    ↓
[對外發送]
```

### 第 1 層:Safe Mode(市場模式自動切換)

三檔模式:

| Mode | 行為 |
|---|---|
| **NORMAL** | 預設,全訊號照常 |
| **DEGRADED** | 全部閾值 × 1.5、冷卻 × 2、confidence 上限 Medium |
| **SUSPENDED** | 全部 Tier A 暫停,只留 Trade-Through(物理事件)且強制 Medium |

切換規則(精簡 4 條):

| 條件 | Mode |
|---|---|
| 9:00 ~ 9:05(集合競價穩定期) | SUSPENDED |
| 大盤指數漲跌幅絕對值 ≥ 5%(崩盤日 / 噴出日) | SUSPENDED |
| 13:20 ~ 13:30(末段拉尾盤) | DEGRADED |
| 當日累計成交額 < 過去 20 個交易日平均 × 0.6(量縮淡盤) | DEGRADED |
| 其他 | NORMAL |

留 v2 候選(本期不做):月底季底 / 央行 FOMC 日 / 美股盤後跳空 / 個股新聞 / ex-date。

### 第 2 層:訊號內部判定

各訊號自身的觸發條件(上面表格)。

### 第 3 層:Signal Priority / 抑制

**同事件 dedupe**(5 秒視窗):多訊號同時 fire,只發最強的,其他併入「確認訊號」chip。

優先級(高 → 低):
```
Trade-Through > A3 > B2 > B1′ > B3 > A1 > A_pull
```

**跨事件 suppression**:

| 主訊號 | suppress 對象 | 時間 |
|---|---|---|
| Trade-Through fire | 同向 B1′ / B2 / B3 | 60 秒 |
| A3 fire | 同向 B1′ / B2 | 60 秒 |
| A_pull layering 啟動 | 該檔所有訊號降一級 confidence | 5 分鐘 |
| B2 fire | 同向 B1′ | 30 秒 |

**矛盾訊號警告**:同 symbol 5 分鐘內方向相反訊號 ≥ 2 個 → 觸發「方向不確定」meta alert,該 symbol 後續訊號強制 LOW(持續 10 分鐘)。

### 第 4 層:Liquidity Guard(流動性可行性)

訊號 fire ≠ 真能交易。為每個訊號計算 `feasibility_score`(0-100),4 個子分數:

| 子分數 | 計算 | 滿分 |
|---|---|---|
| **深度** | 訊號方向剩餘五檔總量 ÷ 平常深度 × 25(上限) | 25 |
| **價差** | 1 - 當下 spread / 平常 spread × 25(上限) | 25 |
| **量能** | min(近 1 分鐘成交量 / 平均, 1.0) × 25 | 25 |
| **距漲跌停** | > 5% = 25 / 3-5% = 15 / 1-3% = 5 / < 1% = 0 | 25 |
| **總分** | 上面加總 | 100 |

**Trade-Through 訊號特殊**:本身就是吃光 depth 的事件,feasibility 強制 cap 在 60。

UI 顯示:

| 分數 | 顯示 |
|---|---|
| ≥ 70 | 綠 dot |
| 50-69 | 黃 dot |
| 30-49 | 灰 dot(訊號預設摺疊) |
| < 30 | 不對外發送,只 log |

### 第 5 層:Throttle(訊號每日上限)

避免訊號疲勞 — 兼職盯盤者實測**每天最多消化 20-30 個有效訊號**,超過就會選擇性忽略。

| 維度 | 上限 |
|---|---|
| Strong 訊號(primary_entry + exit_signal Strong) | 40 / 天 |
| Medium 訊號(medium_entry + confirmation + exit_signal Medium) | 80 / 天 |
| Informational 訊號(observation + intraday + risk) | 200 / 天 |
| 單一檔股票每日總訊號 | 8 |

到 quota 後該類停發,UI 顯示「today's quota reached for {category}」。**超量訊號仍寫 log + ground truth 表(供量化)。**

---

## 訊號頻率預估(粗估)

70 檔監控(自選 + 強勢股)、8 小時盤中:

| 訊號 | 單檔/天 | 70 檔/天 | 備註 |
|---|---|---|---|
| **A1** | 3-15 | 200-1000 | observation 預設摺疊,實際使用者感知少 |
| **A3** | 0.5-2 | 30-150 | 稀少 |
| **A_pull(slow + fast)** | 強勢股 3-10 | 100-400 | layering 升級稀少 |
| **A_pull layering** | 強勢股 0.2-1 | 5-30 | 觸發後降該檔 confidence |
| **B1′** | 強勢股 5-15 | 300-800 | 但 intraday_observation 預設摺疊 |
| **B2** | 強勢股 3-8 | 100-400 | |
| **B3** | 1-3 | 70-200 | |
| **Trade-Through** | 強勢股 1-3 | 30-100 | 稀少且 informative |
| **C2.5′ chain + independent** | 2-5 | 100-300 | exit-only |
| **Raw total**(去 50% 重疊)| | **500-1500** | — |

**經 5 層風控過濾後對外實際發送(預估)**:

| 等級 | 預估每日 |
|---|---|
| Strong | 15-25 |
| Medium | 30-50 |
| Informational(預設摺疊) | 50-100 |
| **使用者實際看到 + 處理** | **~20-30** |

對齊 reviewer 估算「兼職盯盤每天最多消化 20-30 個有效訊號」。

---

## 量化前置設計

第一天就 log 訊號 + 完整市場狀態 snapshot:

| 寫入時點 | 內容 |
|---|---|
| 訊號觸發瞬間 | 訊號 metadata、五檔 snapshot、近 5 分鐘成交摘要、CDP/MA 值、wash 狀態、流動性 guard 分數、liquidity_tier、time_of_day_bucket |
| 訊號後 5min / 30min / 60min / 收盤 | 該時點 price + 成交量 |
| 訊號後 60min | MFE(朝預期方向走最遠 tick 數)+ MAE(反方向走最遠 tick 數) |

設計用兩張表分開:
- **訊號 log 表**(metadata + 市場 snapshot)
- **訊號 ground truth 表**(平面化欄位 + ground truth + MFE/MAE,給量化 SQL 直接 query)

每個訊號每分鐘有背景 job 自動回填價格,60min 後算 MFE / MAE。

未來量化 backtest 可以直接 SQL query:
```
找:wash inactive + HIGH confidence + 訊號類型 = "Trade-Through" 的所有訊號,
   60 分鐘後 MFE 分布如何?MAE 風險如何?
```

---

## 強勢股 universe(自動納入)

| 條件 | 設定 |
|---|---|
| 市場 | 上市 + 上櫃(不含期貨 / 權證 / 興櫃) |
| 漲幅 | ≥ 6% |
| 排除 | 漲幅 ≥ 9.5%(漲停 + 漲停預兆 — 訊號失效) |
| 成交量 | ≥ 3000 張(以股單位 ≥ 3,000,000) |
| 清單上限 | 50 檔(超過取漲幅最高 50) |
| 重新整理頻率 | 每 60 秒 |
| 移出 grace period | 5 分鐘(避免進行中的訊號被打斷) |

實作上來源富邦官方「Snapshot Movers」API(直接 server-side filter 漲幅,client 端再過濾量)。

---

## 上線順序(MVP-first)

預計切 5 個 PR,第一個是極簡 MVP:

### PR 1(MVP)— 風控基礎 + 2 個訊號

**目標**:**先驗證風控基礎建設能正常運作,訊號只挑 2 個 wash 抗性最強當測試**。

範圍:
- 5 層風控過濾完整實作(Safe Mode / Throttle / Liquidity Guard / Priority / Action Type)
- 完整 logging(訊號 metadata + 市場 snapshot + MFE/MAE 回填 job)
- Dynamic universe 自動納入強勢股
- Wash Trade Detector
- **訊號只 2 個**:
  - **Trade-Through**(primary_entry,wash 抗性最高)
  - **A_pull layering**(risk_warning,主力做局警示)
- 前端 UI 完整準備好(只是訊號類型少)

**Critical Gate**(PR 1 跑 1-2 週後檢視):

| 指標 | 期望 |
|---|---|
| Trade-Through fire 後 5 min 內延續 ≥ 3 tick 的比例 | > 55% |
| 使用者「看完 Tier A alert 處理時間」 | < 個人認知預算 |
| `feasibility_score < 50` 的訊號比例 | < 20% |
| Safe Mode 切換在正常市況 | 不會過度觸發 |
| Throttle 在正常市況 | 不會觸發 quota |
| Wash Trade Detector 每日命中 | 0-10 次合理範圍 |
| A_pull layering 觸發後該檔 confidence 降級 | 確認生效 |

**Gate 過了才開 PR 2。** Gate 過不去代表設計理念有誤,後續 PR 砍掉重新評估。

### PR 2 — A 系列展開

加入 A1(observation)、A3(confirmation)、A_pull slow(observation)、A_pull fast(internal log only)。

### PR 3 — B 系列

加入 B1′(intraday_observation)、B2(primary_entry need confirm)、B3(medium_entry)。

### PR 4 — C 系列 + C2.5′

C2′ / C3a′ / C3b′ / C3c′ 完整 chain 偵測(internal log only),C2.5′ chain + independent 對外(exit_signal)。

### PR 5(可選)— UI polish + 矛盾警告 + position tracking

矛盾訊號 UI、整合部位追蹤、各 action_type icon 設計優化。

---

# 需要您決策的問題

請依您專業經驗判斷,我列了 13 個問題分 5 組。每題下面有我們目前的設定 + 想聽您的看法 / 替代方案。

---

## A 組 — 訊號參數實證(對台股實際分布合不合理)

### A1. A1「墊單出現」size 倍數合理嗎?

我們目前用 **相對倍數**(per-liquidity-tier),不是絕對張數:

| 流動性 tier | A1 size 閾值 |
|---|---|
| 高(過去 5 min 成交 ≥ 5000 張)| ≥ 5 × 五檔該檔位過去 5 分鐘 median |
| 中(1000-5000 張) | ≥ 8 × median |
| 低(< 1000 張) | ≥ 12 × median |

Baseline 用 **median** 而非算術平均(抗 fat tail)。

**Reviewer Lens A 質疑**:對 2330 / 2317 等權值股(平常 bid1 200-500 張),5× = 1000-2500 張,真實大戶單通常 500-1000 張,**永遠不觸發**。

問:
- 5×/8×/12× 對中小型股 vs 中型股 vs 大型股實際合理嗎?
- 我們在中小型股(強勢股場景)5× 預估觸發頻率每天 3-15 次,合理嗎?
- 是否該改成「該檔位 size 占歷史五檔總 depth 的 percentile」?

### A2. B1′「短時急動」3 tick / 3 秒對流動性差異適用嗎?

| 股票類型 | 3 tick 占股價 % |
|---|---|
| 2330 (1000 元) | 0.5% |
| 中型股 (300 元) | 0.5% |
| 中型股 (50 元) | 0.6% |
| 小型股 (20 元) | 0.75% |
| 興櫃 (15 元) | 1.0% |

**Reviewer Lens B 質疑**:現股一筆進出成本 0.38-0.59%,raw 3 tick + 4-6 秒人類反應滑價 ≈ 2 tick,實際可吃 < 1 tick → **負期望**。所以我們改成 `intraday_observation` 動作分類(預設摺疊,標明「現股慎用」)。

問:
- 把 B1′ 標 `intraday_observation` 而非主進場訊號合理嗎?
- 還是該完全砍掉(降到內部 log)?
- 3 秒視窗 / 3 tick 對台股節奏是否該調整?

### A3. C 系列突破視窗 60 秒、回測視窗 3 分鐘,對台股節奏合適嗎?

我們把 C 系列(牆破 → 突破 CDP/MA → 回測)做完整 state machine 但**全降到內部 log**(只 C2.5′ 失敗訊號對外作為 exit_signal)。

- 突破視窗:A3 觸發後 60 秒內 price 穿過附近 CDP/MA 線 ≥ 3 tick
- 回測視窗:突破後 3 分鐘內 price 回測線值
- Peak unlock:price 必須先離開線 ≥ 5 tick 才能開始追回測

**Reviewer Lens A 建議**:台股「探底 → 整理 → 攻擊」常見 1-5 分鐘,30 秒視窗會大量漏掉,建議 60-90 秒。

問:
- 60 秒突破視窗對台股節奏對嗎?
- 回測 3 分鐘合理嗎?(時段差異:09:00-10:00 vs 12:00-13:00 是否該不同?)

---

## B 組 — 訊號分類(動作對不對)

### B1. A1 / B1′ 留 `observation` 對嗎?還是該完全砍掉?

兩個訊號的問題:

- **A1 墊單出現**:沒有可執行方向(壓單 vs 撐單方向歧義)。Reviewer 認為**該砍**(70% 以上是 spoofing 早期狀態,沒 actionable 方向)
- **B1′ 短時急動**:現股負期望(上面分析)

我們決定**保留但標 observation / intraday_observation**(預設摺疊,不發 notification,但 TriggerList 顯示)— 理由是尊重使用者原始需求(「想偵測墊單」就是他直覺的核心要求)。

問:
- 「保留但摺疊」vs「完全砍掉」哪個對?
- 標 observation 是否能避免「使用者照訊號操作會反向用錯」的風險?

### B2. A_pull fast(< 1s 撤)降內部 log 對嗎?

A_pull 分三種:fast(< 1s)、slow(≥ 10s)、layering(5 min ≥ 3 次)。

**Reviewer Lens A 質疑**:VWAP / TWAP / Iceberg 演算法的 cancel-and-replace routine 跟 fast spoofing 在資料層無法區分,雜訊高。

我們決定:**fast 降內部 log only**(不對外發但作為 layering 計數的 input)。

問:
- 這樣處理對嗎?還是 fast 也有實戰價值該對外發?
- 真實 spoofer 撤單後反向打過去常在多少秒內?如果常是 0.5-1 秒,我們漏掉了重要訊號嗎?

### B3. C2.5′ 改成 `exit_signal`(只給平倉)對嗎?

C2.5′ 假突破 — 之前 spec 設計是可以反向進場。Reviewer Lens B 建議改成只給已 hold position 的人作為平倉 trigger,不該反向進場。

理由:
- 反向進場 r/r 比差(price 在 line 附近震盪,scratch out 機率高)
- 假突破後反向能走多遠不確定
- 但「強制認賠 / 鎖利 trigger」用途很明確

問:
- 改 exit_signal only 對嗎?
- 還是該保留進場版本(雖然 r/r 差,但對短線交易者仍有用)?
- 如果 exit-only,是否需要整合部位追蹤(知道使用者已 hold 才發 notification)?

---

## C 組 — 風控設定

### C1. Safe Mode 4 條切換規則夠嗎?

目前自動切換:

| 條件 | Mode |
|---|---|
| 9:00 ~ 9:05 集合競價穩定期 | SUSPENDED |
| 大盤 ±5% 崩盤 / 噴出日 | SUSPENDED |
| 13:20 ~ 13:30 末段拉尾盤 | DEGRADED |
| 量縮 < 0.6 × 平常量 | DEGRADED |

留 v2 候選:月底季底 / 央行 FOMC 日 / 美股盤後跳空 / 個股新聞 / ex-date / 台指期跌停。

問:
- 4 條規則夠嗎?還少了什麼關鍵盤勢?
- 哪些 v2 候選實際上是 v1 必做(影響大)?
- 大盤 ±5% 門檻合適嗎?(2008 / 2020 等異常日 ±5% 才觸發,但「-3% 連續跌」是否該也算)

### C2. Throttle quota 對 retail 兼職盯盤合理嗎?

| 維度 | 上限 |
|---|---|
| Strong 訊號 | 40 / 天 |
| Medium 訊號 | 80 / 天 |
| Informational | 200 / 天 |
| 單一檔股票 | 8 / 天 |

**Reviewer Lens B 建議**:25 / 50 / per-symbol 5。我們略放寬到 40 / 80 / 8(原因:兼職盯盤者實際 evaluator 速度比自營商風控標準稍快)。

問:
- 40 / 80 對兼職盯盤合理嗎?還是太鬆(該保守一點)?
- 單一檔 8 個是否該分:Strong 2 + Medium 3 + Informational 3?
- 到 quota 後該停發還是降級?(現在設計是停發)

### C3. Liquidity Guard 4 個子分數權重合理嗎?

`feasibility_score`(0-100)= 深度 + 價差 + 量能 + 距漲跌停,各 25 分。

問:
- 4 個子分數平均權重對嗎?
- 中小型股場景下「量能」是否該權重更高(因為流動性陷阱主要來自量縮)?
- 「距漲跌停」評分是否需要為「距前波高 / 低點」(技術面阻力 / 支撐附近 fill quality 差)?

---

## D 組 — 遺漏的視角

### D1. 我們漏了什麼 pro 會看的訊號?

Reviewer Lens A 建議的 5 個新增訊號,我們採用 2 個(Trade-Through、Wash Detector),其他 3 個列 v2 候選:

| 訊號 | v1 不做的理由 |
|---|---|
| Quote-Trade Sequencing Anomaly(報價成交時序異常 — 報價跳空成交,暗示 hidden order) | 富邦 1 秒延遲,粒度不夠細抓 microsecond 時序 |
| Per-Minute-of-Day Volume Anomaly(每分鐘量能異常 — 對比過去 20 個交易日同分鐘 baseline) | 工程量大(要存歷史每分鐘 baseline) |
| Futures-Cash Basis Anomaly(期現價差異常 — 期貨領先現貨) | 要把期貨納入訂閱 scope,跨 product 太大 |
| Cancel Rate Spike(撤單比率突增 — 整檔進入假掛單戰場) | 富邦 SDK 沒給 cancel count,只能 proxy 不準 |

問:
- 這 4 個 v2 候選哪個該升 v1 必做?
- 還有什麼 pro 會看但我們完全沒涵蓋的訊號?
- 個別主力套路(例如「拉抬 → 出貨」標準模式 / 「下殺洗盤 → 拉抬」標準模式)是否該做整套 chain detect?

### D2. 強勢股 universe 條件對嗎?

「上市櫃 + 漲幅 ≥ 6% + 量 ≥ 3000 張,排除漲停 ≥ 9.5%」

問:
- 漲幅 ≥ 6% 是否該也加「跌幅 ≥ 6%」(下殺類強勢)?
- 量 ≥ 3000 張對小型股是否太高?(平常每天 < 3000 張的潛力股可能被漏)
- 排除漲停應該用「changePercent < 9.5%」夠了嗎?還是要直接查富邦的 priceLimitStatus?
- 是否該分時段不同條件?(09:30 前漲 3% 已是 promising,11:30 後漲 6% 才有意義)
- 50 檔上限是否合適?(太多會稀釋訊號,太少會漏)

### D3. 哪些訊號其實是 commodity(主力知道 → 反向獵殺),要砍?

Reviewer Lens A 用「commodity 程度 ★」評分:

| 訊號 | Commodity ★ |
|---|---|
| A1 / A3 / B1′ / B2 / B3 / C2′ | ★★★★★ |
| A_pull / C3 系列 | ★★★☆☆ ~ ★★★★☆ |
| Trade-Through / A_pull layering / C2.5′ | ★★☆☆☆ |

我們的 ranking 是:**保留所有但 commodity 高的標 observation / 降權**。

問:
- 這個分級對嗎?(您看哪些訊號其實在台股實戰中根本沒人在用 → 反而 niche?)
- 哪些是真的「散戶都在看」 → 已被反向利用?

---

## E 組 — 動作分類完整性

### E1. action_type 6 種完整嗎?

| action_type | 訊號用例 |
|---|---|
| primary_entry | Trade-Through / B2 |
| medium_entry | B3 |
| confirmation | A3 |
| exit_signal | C2.5′ chain / independent |
| observation | A1 / A_pull slow |
| intraday_observation | B1′ |
| risk_warning | A_pull layering |

問:
- 還缺哪種類型?
- 例如「**stop_loss(止損 trigger)**」是否該獨立?(目前歸 exit_signal)
- 例如「**scale_in(加碼 trigger)**」?(C3c′ 線守住反彈是不是該分這個?但我們降到 internal log)
- 例如「**market_context(總體市況訊號,非個股)**」?

---

# 給 reviewer 的 review 重點

如果您只有時間看一部分,請優先看:

1. **訊號清單**(11 個)— 每個的觸發條件 + 動作分類是否合理(B 組問題)
2. **C2.5′ 改 exit-only**(B3 問題)— 反向進場 vs 平倉 哪個對?
3. **動作分類**(E 組)— 6 種夠嗎?
4. **強勢股 universe 條件**(D2)— 漲 ≥ 6% + 量 ≥ 3000 張合理?

次優先:
- 參數實證(A 組)
- 風控設定(C 組)— 尤其 Throttle 數字
- 遺漏訊號(D1)

---

# 額外資料(若您想了解更多)

如果想看完整技術設計、訊號 metadata schema、實作架構,有兩份補充文件:

1. `2026-05-20-large-order-detection-design.md` — 完整技術 spec(含資料結構、模組架構、實作細節)
2. `2026-05-20-large-order-detection-review.md` — Lens A review(alpha 真偽 + 主力套路視角)
3. `2026-05-20-large-order-detection-lens-b-review.md` — Lens B review(風控 + 成本 + 認知負擔視角)

但**這份 brief 已包含所有需要您決策的內容**。技術細節不需您處理。
