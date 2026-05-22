# Large Order Detection — Critical Review & 修訂設計

**Date**: 2026-05-20
**Status**: Review 完成,待 writing-plans 接手實作修訂版
**Related**: [`2026-05-20-large-order-detection-design.md`](./2026-05-20-large-order-detection-design.md)
**Reviewer 視角**: 10+ 年台股 / 台指期專業交易員(自營商量化研究 + discretionary day trading + 主力套路熟悉)

---

## Summary

對原 spec 11 個訊號做專業交易員視角的 critical review,結論:

1. **10 / 11 個訊號是散戶 TA 書 / 看盤軟體已經在教的 commodity 訊號** — 主力知道散戶在看,**反向利用**機率高
2. **整體 risk > reward** — 把這套訊號給散戶當進場依據,等於提供「主力獵殺目標範圍」清單
3. **方向建議**:從「找對」(momentum / breakout)轉向「避錯」(contrarian / 防 trap)

針對 user 目標(**中小型股 + 避錯 + 通往量化**)重新設計,從 11 個訊號縮到 **3 個對外 alert + 2 個 meta-filter + 6 個 internal state**,並建議第一天 ship 就強制 log ground truth 給未來量化使用。

---

## Part 1 — 原 Spec 11 訊號的 Commodity 風險清點

| 訊號 | Commodity 程度 | 散戶在哪裡看過 | 主力反向利用套路 |
|---|---|---|---|
| **A1 墊單出現** | ★★★★★ | XQ / 群益 / 富邦 e01「五檔大量」alert,散戶課本必教 | **假掛單釣魚 (spoofing)** — 掛 500 張假壓單讓散戶不敢追多,等散戶停手後撤單反向急拉 |
| **A3 墊單被吃** | ★★★★★ | 「壓力 / 支撐被吃 = 真突破」是技術派啟蒙教學 | **自吃自牆 (wash trade)** — A 帳戶掛牆、B 帳戶吃光,完美滿足 size_drop 70% + 對證 50%,純煙幕 |
| **A_pull 抽單** | ★★★☆☆ | 散戶較少明確看,但部分軟體有「掛單異常變化」alert | **Layering** — 主力一次掛 bid1-bid3 三層假牆逐層撤,A_pull 連續發 noise,真實意圖反向 |
| **B1′ 急動** | ★★★★★ | 「秒急動」「漲停預警」是看盤軟體標配 | **Head fake 拉一檔** — 故意拉 3-4 tick 引散戶 FOMO 進場後立刻倒貨 |
| **B2 連續掃單** | ★★★★★ | 「連續紅單」「連續外盤」99% 看盤軟體都有 | **拆單造勢** — 1 筆 1000 張拆 10 筆 100 張連續打,B2 偵測為 6+ 個不同人,實際是同一筆 |
| **B3 內外盤失衡** | ★★★★★ | XQ 直接在報價檔上面顯示內外盤比 | **假外盤** — wash sale 製造外盤量,B3 ratio 被汙染 |
| **C2′ 牆破突破** | ★★★★☆ | CDP / MA 是散戶 standard indicator | **假突破出貨** — CDP_AH 上方放假壓力牆,等散戶 FOMO 突破後倒貨 |
| **C3a′ 接近回測** | ★★★★☆ | 「突破後回測」是 Wyckoff / VSA 必教 | 主力知道散戶在等回測,故意製造 |
| **C3b′ 真正回測** | ★★★★☆ | 同上 | 同上,price 故意拉到 line 引散戶進場 |
| **C3c′ 守住反彈** | ★★★☆☆ | 較嚴格的散戶會等,但比例不高 | 雙重 fake — 守住 + 反彈 3 tick 後再倒 |
| **C2.5′ 假突破** | ★★★☆☆ | 「假突破 = bull trap」概念散戶懂,即時抓難 | **唯一 contrarian 訊號**,主力不會「製造 C2.5′」 |

**結論**:A1 / B1′ / B2 / B3 / C2′ 五個訊號,等於把主力反向獵殺目標精準告訴 retail。

---

## Part 2 — 每個訊號的具體失敗模式(主力如何設計)

### A1 墊單出現
- **Iceberg order**:真大主力用 hidden order,委託簿只露一小部分。A1 偵不到(因為露出來的就是小單)
- **2330 / 2317 等權值股完全測不到**:平常 bid1 200-500 張,5× = 1000-2500 張,真實大戶單 500-1000 張,**永遠不觸發**
- **小型股 baseline 污染**:平常 bid1 size 2-5 張,某筆 80 張瞬間進來後 baseline 拉到 8 張左右,後續 40 張過不了 5×
- **算術平均對 fat tail 不抗噪**:應改 median 或 trimmed mean

### A3 墊單被吃
- **Wash trade / cross**:主力 A 帳戶掛牆、B 帳戶吃,完美滿足條件,撮合不暴露 broker info,**spec 完全無法偵測**
- **做市商 inventory unwind**:被吃光純粹 mechanical,但 A3 會發
- **ETF 套利 mechanical 吃單**:0050 vs 成分股套利 routine,A3 誤判為 directional
- **VIX 高、大盤急跌時**:所有股票 ask 同時被吃,A3 全市場觸發,selectivity = 0

### A_pull 抽單
- **Algo cancel 是日常**:VWAP / TWAP / Iceberg 都 cancel-and-replace,false positive 多
- **min_lived_seconds = 10 太短**:真實 spoofer 撤單後 0.5-1 秒內就反向,**反而漏掉最快的 spoofer**
- **Layering 稀釋訊號**:多層假牆連續撤,A_pull 連發 alert 但其實是同一策略
- **20%-50% 灰色地帶過大**:真實 spoofing 過程中常有少量被動成交落在 30-40%,真 spoofing 也漏報

### B1′ 急動
- **撮合機制噪音**:09:00:00 開盤集合競價結算第一筆容易跳幾個 tick,B1′ 必發
- **跨 tick_size 級距邊界**:498 → 502 從 0.5 → 1.0 tick,雖 spec 用 start_price 但仍有邊緣 case
- **主力 head fake 專門針對這個訊號**:retail 看 B1′ 進場,主力立刻倒貨,**訊號越準確散戶被獵越快**
- **3 秒視窗對流動性差異無感**:2330 跳 3 tick = 0.3%、小型股跳 3 tick = 0.75%,訊號意義不同卻同閾值

### B2 連續掃單
- **拆單(最致命)**:1 筆大單拆 10 筆連續打,B2 看起來 6+ 個不同人,實際是 1 個人。**這在台股實務上幾乎沒 edge,大戶都會拆**
- **VWAP / TWAP 演算法執行**:外資調節 / 自營部位 unwind 必然連續單向,屬被動 mechanic
- **期現套利連帶**:期貨拉動現貨,套利者連續掃成分股
- **流動性差的股票 5 筆 / 5 秒太容易達標**

### B3 內外盤失衡
- **假外盤 (wash sale)**:主力配對交易,A 掛 ask 100 張、B 市價買,變成統計外盤,B3 完全被汙染
- **做市商不對稱 inventory**:自然偏一邊但只是 market making 不是壓力
- **60 秒視窗對台股節奏不對**:主力一次拉抬 30 秒結束,等 60 秒視窗累計到 ratio ≥ 3 時主力早跑了
- **min_total_volume 沒考慮股票規模**

### C2′ 牆破突破
- **CDP / MA 是 retail 最公開資訊**:主力知道散戶在線上掛單,**專門製造假突破**。突破 3 tick 是 retail FOMO sweet spot
- **wall ↔ line ≤ 5 tick proximity 對流動性差異無感**
- **CDP 失效**:跳空 / 大新聞日 CDP 沒參考價值,但 C2′ 還是會發
- **真主力動作不會剛好對齊 CDP/MA**:真實主力進場價是事前評估的關鍵價,**剛好對齊 CDP/MA 的 A3 → C2′ 反而更可能是 trap**

### C3a′ / C3b′ / C3c′ 回測 chain
- **Selection bias**:真正最強的突破不回測(衝上去就走),C3 系列只發在「中等強度 + 會回頭」的突破,**反向選出較弱訊號**
- **C3b′ price = broken_line 機率低**:跨 tick_size 邊界時 line value 落在 tick 之間,難精確命中
- **C3c′ bounce ≥ 3 tick 太低**:短期波動大的股票 random walk 就能 bounce
- **跨 trading session 髒狀態**:CDP 收盤後失效,但 pending state machine 不會自動清
- **主力雙重 fake**:回測 + 守住 + 反彈 3 tick 後再倒,**專門獵殺「等 C3c′ 確認進場」這群**

### C2.5′ 假突破
- **事後訊號**:訊號發出來時 price 已跌穿 3 tick,retail 看到 alert 已來不及反應
- **正常震盪 false positive**:三角整理時連續觸發 C2′ + C2.5′ noise pair

---

## Part 3 — 遺漏的 Pro 角度(5 個建議新增訊號)

### 1. Trade-Through Detection(穿價成交)
**邏輯**:單筆成交 `tick.size > sum(該方向五檔 size)`,把整本書都吃光,後續必須等新報價有人接。

**為什麼有 edge**:
- 跟 A3 不同,A3 用統計閾值容易被 wash 汙染;trade-through 是**物理穿透**,wash 成本極高(需事先安排對手方提供整本書 depth)
- 是「真實大戶 conviction」的最硬指標
- Retail 完全看不到(他們報價是 1 秒級延遲)

**Microstructure 角度**:✓

### 2. Quote-Trade Sequencing Anomaly(報價成交時序異常)
**邏輯**:某 tick 成交價 > 顯示 ask1 + 1 tick(或 < bid1 - 1 tick),代表用「五檔以外的 hidden order」打出來,或撮合間隙的 fast move。

**為什麼有 edge**:
- 撮合系統內 broker → 撮合中心 → 報價更新有 microsecond 級時序,retail (1s 延遲) 看不到
- 顯示「真實有大戶在做動作但不想曝光」(hidden liquidity)
- 跟 A_pull 互補:A_pull 看「撤」,這個看「藏」

### 3. Per-Minute-of-Day Volume Anomaly(分時量能異常)
**邏輯**:每檔股票分別維護「過去 N 個交易日同一分鐘」baseline。某股 11:35 成交量是過去 20 個交易日 11:35 平均的 5× → 觸發。

**為什麼有 edge**:
- 盤中不同時段 volume profile 不一樣(09:00-09:30 量大、11:30-12:30 量小、13:00-13:30 量大),retail B3「過去 60s」算不出
- **冷時段量能突增**幾乎只能是 informed flow
- 散戶看盤軟體幾乎沒這個角度

### 4. Futures-Cash Basis Anomaly(期現價差跳動)
**邏輯**:台指期(MXF / TXF)與加權指數(或 0050)的 basis,正常 ±10 點區間。突然跳 > 30 點 → 期貨主力在動作,現貨會被套利者拉。

**為什麼有 edge**:
- 台股很多時候**期貨 leading current → cash**,retail 只看現貨完全 miss
- Basis 跳動極難 wash(需同時控制期現兩個 market)
- 既有 watchlist + 富邦行情訂閱可直接擴 MXF / TXF

### 5. Order Cancellation Rate(撤單比率突增)
**邏輯**:每秒統計 `cancel_count / (cancel_count + new_order_count)`,短時間飆升超過 baseline 3 sigma → 市場進入「假掛單戰場」,警示後續訊號**全部低可信度**。

**為什麼有 edge**:
- 不是發訊號,是發「訊號信心降低」meta-signal
- 富邦 books channel 推送頻率變化可粗略推導(每秒推送次數 vs 成交次數)即使沒直接拿到 cancel 數也可 proxy
- Pro 角度:用「市場 noise 級別」當 filter,retail 完全沒這概念

---

## Part 4 — 參數現實性(對台股實際成交分布)

### A1 的 5× book depth — 嚴重不適用

| 股票類型 | 平常 bid1/ask1 size | 5× 閾值 | 真實大戶單規模 | 結論 |
|---|---|---|---|---|
| 2330 / 2317(權值股) | 200-500 張 | 1000-2500 張 | 500-1000 張 | **永遠不觸發** |
| 2603 / 2454(中型股) | 30-80 張 | 150-400 張 | 100-300 張 | 邊緣 |
| 興櫃 / 小型股 | 2-10 張 | 10-50 張 | 30-80 張 | 容易觸發但意義低 |

**建議**:閾值改 `max(5× baseline, 流動性 tier 對應的絕對 floor)`,或直接用「該檔位 size 占歷史五檔總 depth 的 percentile」。

### B1′ 的 3 tick / 3 秒 — 對流動性差異無感

實質波動對不同股票差異:
- 2330 (1000 元) 跳 3 tick = 0.3%
- 中型股 (50 元) 跳 3 tick = 0.6%
- 小型股 (20 元) 跳 3 tick = 0.75%
- 興櫃 (15 元) 跳 3 tick = 1.0%

**建議**:用「3 秒內變動超過該股當日 5 分鐘 ATR 的 X%」或「σ(過去 N 分鐘) 的 2 sigma」做動態閾值。3 秒視窗太短應拉到 5-10 秒。

### C 系列 30 秒 / 3 分鐘 — 30 秒太短

- **A3 → C2′ 的 30 秒**:台股盤中「探底 → 整理 → 攻擊」常見 1-5 分鐘,30 秒大量漏掉。**建議 60-90 秒**
- **C2′ → C3 的 3 分鐘 retest 視窗**:大致對(多數突破回測在 1-5 分鐘),但極端 trending 日突破直接走 10+ 分鐘
- **時段問題**:09:00-10:00 節奏快、12:00-13:00 慢、13:00-13:30 集中度高 — 應加 `time_of_day_multiplier`

---

## Part 5 — State Machine 被遊戲化的風險

主力知道你抓 5 tick / 3 秒 / 70% size drop / 50% 對證之後,**會主動製造**符合條件的事件。**Goodhart's Law 在 microstructure signals 上特別明顯**:訊號越乾淨 = 越像被人為製造。

### 主力遊戲方式

#### 套路 1 — 故意製造完整 chain 騙散戶(最致命)
```
1. 主力 A 帳戶在 cdp_ah 上方掛假壓力牆(ask2 985)
2. 拉幾筆 → 牆「自然」變大 → A1 觸發
3. 主力 B 帳戶用市價單吃光自己 A 的牆 → A3 觸發
4. 連續單向掃幾筆 → B1′ + B2 同時觸發
5. price 穿過 cdp_ah 3 tick → C2′ 觸發
6. retail 看到「A1 + A3 + B1′ + B2 + C2′ 五訊號齊發」FOMO 進場
7. 主力反手倒貨,price 跌回 cdp_ah 下方 → C2.5′ 觸發
8. 散戶套牢,主力獲利
```
整個 chain 是主力刻意製造,訊號偵測完美,但這就是 trap。

#### 套路 2 — Fake retest 出貨
真突破後,主力故意把 price 拉回 broken_line 引散戶「等回測進場」(C3a′ + C3b′ 觸發),retail 進場後主力反手倒貨,跌穿 (C2.5′ 觸發)。

#### 套路 3 — Layering 製造 A_pull noise
主力同時在 bid1, bid2, bid3 掛 3 層假牆,一層一層撤,A_pull 連續觸發,**retail 解讀為「主力撤單看空」**,但主力煙幕掩護下反向操作。

#### 套路 4 — 自吃自牆 製造 A3
A 帳戶掛 ask 大牆,B 帳戶市價吃,A3 觸發「壓力被攻破」,**散戶誤以為要漲**進場,主力倒貨。

### Spec 抓不到的「主力刻意製造的訊號」

1. Wash trade 製造的 A3 / B2 — 撮合不暴露 broker
2. Cross / OT 製造的 A3 — 跨帳戶配對單
3. Layering spoofing 的 A_pull noise — 多層撤單合法且常見
4. Fake breakthrough + fake retest 的完整 C 系列 chain
5. 針對 retail FOMO 行為的 head fake B1′

### 防禦建議
- 加 **wash trade 警示**:同 size、相近時間(5 秒內)反向同 price 成交視為 wash 跡象
- C2.5′ **boost priority**,C2′ / C3a′ / C3b′ 降級為 backend state,只到 C3c′ 才對外發 alert
- 加 **meta-signal「市場 noise level」**:cancel rate / wash 跡象密度超過閾值時所有 A / B 訊號標 LOW_CONFIDENCE

---

# Part 6 — 修訂版設計(中小型股 + 避錯 + 通往量化)

## 設計前提

User 目標重新定義:

1. **目標標的**:中小型股(排除 2330 / 2317 等高流動性權值股)
2. **方向**:避錯(contrarian / 防 trap),而非找對(momentum / breakout)
3. **終局**:從人工盯盤工具進化為量化策略

這三個條件改變了訊號選擇邏輯,因為:

- **中小型股**:流動性差,大戶 footprint 顯眼,但 baseline 統計脆弱、wash / spoofing 比例特別高
- **避錯導向**:不告訴使用者「該進場」,告訴「現在進場很可能被殺」
- **量化導向**:訊號必須有明確邊界 + 可記錄 + 可回測 + 訊號間相關性低

## 三層架構

```
┌─────────────────────────────────────────────────────┐
│ Tier A: 對外 alert(3 個,高 conviction)             │
│   - A_pull(spoofing detector,反向訊號)             │
│   - C2.5′ 獨立化(假突破 trap,反向 entry signal)   │
│   - Trade-Through(物理穿越,directional)            │
└─────────────────────────────────────────────────────┘
                          ↑ 用 Tier B 的 confidence 修飾
┌─────────────────────────────────────────────────────┐
│ Tier B: 濾波 / Meta(內部 state,不發 alert)         │
│   - Wash Trade Detector(訊號污染指標)              │
│   - B3 改造版(時段正規化失衡,filter)              │
│   - Cancel Rate Spike(可選,市場 noise 指標)       │
└─────────────────────────────────────────────────────┘
                          ↑ 內部 dependency
┌─────────────────────────────────────────────────────┐
│ Tier C: 純 internal state(不發 alert,只供其他訊號)│
│   - A1 牆出現(僅作為 A_pull 的 prerequisite)       │
│   - A3 牆被吃(僅供 C 系列 internal)                │
│   - C2′ / C3a′ / C3b′ / C3c′(完整 chain log)       │
└─────────────────────────────────────────────────────┘
```

對外 **3 個高 conviction alert** = 人工盯盤不會 alert fatigue;Tier B 提供 confidence 調節;Tier C 為未來訊號優化保留全量資料。

---

## Tier A 詳細設計

### A.1 A_pull 抽單(改造版)

**為何留**:中小型股 spoofing 跡象比大型股顯眼 — book depth 小,假牆相對 baseline 倍率更高(可能 8-15×),抽單瞬間 book 結構變化也更明顯。

**避錯用法**:
- 看到 ask A_pull → 「剛才那個壓力是假的,不代表 supply」
- 看到 bid A_pull → 「剛才那個支撐是假的,不要 buy the dip」

**對原 spec 的改造**:
- `min_lived_seconds`: 10 → **30**(濾掉 algo cancel routine)
- 加 **Layering 升級**:同 symbol 5 分鐘內 ≥ 3 次 A_pull → `LAYERING_DETECTED` event(對中小型股特別容易出現)
- **反向訊號**用法(量化):bid 牆被 pulled 後,30 / 60 / 180 / 300 秒後 price 往**下**的機率統計,build 量化策略

**Metadata 新增欄位(量化用)**:
```json
{
  ...原 spec 欄位,
  "wash_active_at_trigger": true,
  "recent_apull_count_5min": 2,
  "layering_detected": false,
  "size_x_avg_at_trigger": 12.5,
  "tick_size_at_trigger": 0.05
}
```

---

### A.2 C2.5′ 假突破(獨立化)

**為何留**:唯一一個 contrarian 訊號,直接抓 trap 形成的瞬間。中小型股因為流動性差,假突破設置成本低、發生頻率高 — **訊號量充足、可統計**。

**對原 spec 的改造(核心:獨立化)**:
- **不要綁 A3 + C2′ 完整 chain 才能發** — 改成獨立判定:「price 短時穿越某條 CDP / MA ≥ 2 tick → 反向跌穿 ≥ 2 tick」就發
- 反向門檻 3 tick → **2 tick**(中小型股 tick 對價值比例大,2 tick 已顯著,3 tick 太晚)
- 加 `confidence_tier`:
  - **HIGH** = 突破前 5 min 有 A_pull / wash 跡象(高機率刻意 trap)
  - **MEDIUM** = 純技術假突破(可能是 organic noise)

**為何要獨立化**:chain 越長 → 樣本越少 → 量化沒法用。獨立 C2.5′ 觸發頻率高得多,backtesting 才有意義。

**反向 entry signal 用法(量化)**:HIGH tier 假突破後反向操作,backtesting 應該是 winning edge。

---

### A.3 Trade-Through 穿價成交(新增)

**為何加**:中小型股五檔總 depth 經常只有 50-150 張,單筆 100+ 張**物理穿過五檔**是顯著事件 — **wash 偽造成本極高**(要安排對手方提供整本書的 depth)。

**邏輯**:
```
觸發條件:
  tick.size > sum(該方向五檔 size)
  AND
  該 tick 後最佳買 / 賣價往該方向跳 ≥ 1 tick
```

**避錯用法**:
- Trade-Through 是「真實 conviction」硬訊號 — 出現後跟隨方向
- **反過來看**:最近 5 min 內有 Trade-Through 卻沒有後續延續 → 反向意圖強(主力一次推完就回頭)

**量化用法**:
- 直接訊號:Trade-Through 後 5-15 min 延續率統計
- 配 wash 濾波:乾淨 Trade-Through(無 wash active)= HIGH_CONFIDENCE_DIRECTIONAL

---

## Tier B 詳細設計

### B.1 Wash Trade Detector(新增 — 中小型股必備)

**為何加**:原 spec 完全沒有。中小型股 wash 比例比大型股高很多 — **沒有 wash detector,所有 B 系列訊號都不可信**。

**偵測邏輯**:
```
條件 1(單事件):1 秒視窗內出現
  - 相近 size (±10%)
  - 相反主動方向(一筆外盤 / 一筆內盤)
  - 相近 price (±1 tick)
  → 疑似 cross

條件 2(模式):5 分鐘視窗內 ≥ 3 次條件 1
  → WASH_PATTERN_ACTIVE
```

**用法**:
- WASH_PATTERN_ACTIVE 期間,所有 B 系列訊號自動標 LOW_CONFIDENCE
- A3、C2′ 同樣標 LOW
- C2.5′ 升級:WASH active + 短突破 → 直接判 HIGH(很高機率是出貨 trap)

**量化必備**:這是訊號 ensemble 裡的「市場 honesty score」,長期資料價值極高。

---

### B.2 B3 改造版 — 時段正規化失衡

**保留邏輯**:當下 buy / sell imbalance ratio。
**全部改的部分**:

| 參數 | 原 spec | 改造後 |
|---|---|---|
| 視窗 | 60s | **5-10 分鐘** |
| Baseline | last 60s | **過去 20 個交易日同一分鐘平均** |
| Ratio 門檻 | 3.0 | **5.0** |
| Wash 過濾 | 無 | **WASH_ACTIVE 期間靜音** |

**避錯用法**:**冷時段 + 高 ratio + 沒 wash 跡象 = 真機構動作**(很罕見,但訊號值高)

**從 Tier A 降到 Tier B 的原因**:即使改造後,B3 還是 trailing signal。當 filter 用比當 alert 用價值高。

---

### B.3 Cancel Rate Spike(可選,Phase 2)

**邏輯**:每秒統計 `cancel_count / (cancel_count + new_order_count)`,短時間飆升超 baseline 3 sigma → 市場進入「假掛單戰場」。

**實作 caveat**:富邦 SDK 不直接給 cancel count,可用 books 推送頻率變化 proxy(每秒推送次數 vs 成交次數)。Phase 2 評估富邦 books channel 資料粒度後再決定要不要做。

---

## Tier C — Internal State(不發 alert,純供 logging 與其他訊號)

| 訊號 | 用途 |
|---|---|
| A1 牆出現 | A_pull / A3 的 prerequisite state |
| A3 牆被吃 | C 系列 internal,wash 高發地帶 |
| C2′ 牆破突破 | Pure noise signal,只 log 供 backtest 用 |
| C3a′ / C3b′ / C3c′ | 完整 chain log,看完整事件序列 |

**為何全降 Tier C**:這 6 個訊號都有「主力刻意製造」的風險,直接 alert 給人會被獵殺。Log 起來等量化階段 backtest,可能會發現某些 combo 在特定條件下有 edge — 但需先有資料才能驗證。

---

# Part 7 — 量化前置:Logging Schema

避錯 + 量化 = 訊號**現在發不發**不重要,**事後能不能驗證**才重要。建議第一天 ship 就強制 log。

## 必含欄位

```python
{
  # === 觸發點 context ===
  "signal_type": "a_pull" | "c25_independent" | "trade_through" | ...,
  "tier": "A" | "B" | "C",
  "triggered_at_ms": 1747820000000,
  "symbol": "2603",
  "metadata": {...},                    # 各訊號 spec 既有 fanout

  # === 訊號發生當下市場狀態(critical for ML / backtest) ===
  "book_snapshot": {
      "bid1": {"price": ..., "size": ...},
      "bid2": ..., ..., "bid5": ...,
      "ask1": ..., ..., "ask5": ...
  },
  "trades_5min_summary": {
      "total_volume": ...,
      "buy_volume": ...,
      "sell_volume": ...,
      "trade_count": ...,
      "vwap": ...
  },
  "cdp_ma_values": {
      "cdp_ah": ..., "cdp_nh": ..., "cdp": ...,
      "cdp_nl": ..., "cdp_al": ...,
      "sma_5": ..., "sma_20": ...
  },

  # === 訊號污染指標(meta-signal,wash / cancel)===
  "wash_active": false,
  "wash_event_count_5min": 0,
  "recent_apull_count_5min": 1,
  "cancel_rate_z": 0.8,                  # 撤單率 z-score(若 Phase 2 實作)
  "layering_detected": false,
  "time_of_day_bucket": "open_30m" | "mid" | "lunch" | "close_30m",

  # === Ground truth(觸發後追蹤,async job / cron 寫回) ===
  "price_at_t_plus": {
      "5m": ..., "15m": ..., "30m": ..., "60m": ..., "eod": ...
  },
  "volume_at_t_plus": {"5m": ..., ..., "eod": ...},
  "max_favorable_excursion_ticks": float,   # 朝預期方向走最遠 tick 數
  "max_adverse_excursion_ticks": float,     # 反方向走最遠 tick 數
  "ground_truth_filled_at": "2026-05-20T15:00:00+08:00"
}
```

**MFE / MAE 是後續 backtesting 最關鍵的兩個欄位** — 同一個訊號可能短期看對長期錯,或反之。沒這兩個值無法做 stop loss / take profit 參數優化。

## Ground truth 回填機制

- 訊號觸發後寫一筆 `signals_log` row,`price_at_t_plus` 全 null
- 背景 job 每分鐘掃近 60 分鐘觸發的訊號,讀 ring_buffer / OHLC 回填對應時點 price
- 收盤後最後一次 job 回填 `eod` 欄位

---

# Part 8 — 取捨原則總結

針對「中小型股 + 避錯 + 量化」訊號設計原則:

1. **Spoofing / Wash 不是 noise,是核心訊號** — 中小型股這兩個比例特別高,把它們從「干擾」翻轉為「meta-signal」是關鍵差異
2. **訊號 latency 換 conviction** — 寧可訊號慢 30 秒但 wash 過濾乾淨,也不要快但 false positive 高
3. **獨立化 contrarian 訊號 vs chain-based 訊號** — C2.5′ 不要綁 chain prerequisite,因為 chain 越長樣本越少、量化越難用
4. **物理事件 > 統計門檻**(Trade-Through > A3) — 物理事件無法 wash,統計訊號可以
5. **第一天 ship 就 log MFE / MAE** — 量化的 ground truth,缺了補不回來
6. **對外 alert 數量壓到 ≤ 3** — 人工盯盤不會疲勞,量化也清晰
7. **Tier C 訊號照樣 log,只是不對外發** — 未來量化發現某些 combo 有 edge 時,要有歷史資料才能 backtest

---

# Part 9 — 修訂版 Implementation Order

建議切 4 個 PR,**順序倒過來**(先濾波後訊號):

## PR 1 — Tier B 濾波層 + Logging schema 基礎
- `MarketStats` 模組(per-symbol-per-level rolling stats,沿用原 spec 設計)
- `WashTradeDetector` 模組(新增)
- `signals_log` schema 擴充:加 logging 必含欄位
- Ground truth 回填 job(背景 cron)
- B3 改造版(per-minute-of-day baseline + 5 min 視窗 + wash 過濾)
- **本 PR 不對外發任何 alert,純後端基礎建設**

**為何先 ship 這個**:wash / cancel rate / B3 baseline 是其他訊號的 confidence 基礎,**也是未來量化最有價值的歷史資料**。等 PR 2-4 上線時這些資料已累積數週。

## PR 2 — Tier A.1 + A.2:A_pull + C2.5′ 獨立版
- A1 偵測(internal,不發 alert)
- A_pull 偵測 + 改造(min_lived 30s + layering 升級)
- C2.5′ 獨立判定邏輯(不綁 chain)
- C2.5′ confidence_tier 計算(用 PR 1 的 wash / A_pull 資料)
- 前端 TriggerList 新訊號 row
- ActiveSignalEditor 編輯 UI

## PR 3 — Tier A.3:Trade-Through
- books channel 訂閱(沿用原 spec)
- Trade-Through 偵測邏輯
- 前端 row + 編輯 UI

## PR 4(可選)— Tier C 完整 chain logging
- A3 偵測(internal)
- C2′ / C3a′ / C3b′ / C3c′ 完整 state machine(internal)
- 全部結果寫 `signals_log` 但 tier="C"、不 broadcast
- 供未來量化 backtest 用

---

# Part 10 — Open Questions(待 user 決定)

1. **目標股票範圍**:中小型股的精確定義?(成交額 / 市值 / 流動性 tier?)— 影響 baseline 參數調整
2. **興櫃 / 創新板要不要納入**?— 流動性極差,訊號特性可能完全不同
3. **量化目標時程**:多久後要開始 backtest?— 影響 Ground truth 回填粒度(分 / 秒)
4. **是否需要期貨 + 現貨整合**?— 第 3 個建議遺漏訊號(Futures-Cash Basis)要不要納入 v2
5. **WASH detector 沒辦法處理同 broker 內部 cross**(broker 內撮合不上交易所),這個限制 user 能接受嗎?
6. **C2.5′ 獨立化是否會跟現有 C 系列 chain 設計衝突**?— 需要決定:Tier C 的 C2.5′ chain 版本要不要保留,或者統一只用獨立版

---

# 結語

**如果現在這個版本 ship 出去做量化,半年後最有價值的不是訊號本身,而是 wash / cancel rate / A_pull / Tier C chain 這四類歷史資料**。

建議方向:
- 短期(人工盯盤):3 個 Tier A alert 提供避錯邊際效益
- 中期(量化研究):用累積的 Tier B + Tier C 資料探索訊號 ensemble combo
- 長期(量化執行):從 backtest 出 winning combo,作為自動化策略基礎

**做這套不是 retail edge,是把整套系統當成「資料採集 + 避錯工具 + 量化前置」三合一基礎建設**。
