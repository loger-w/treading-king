# Large Order Detection — Lens B Review(風控 / 成本 / 認知負擔視角)

**Date**: 2026-05-20
**Status**: Lens B review 完成,待併入修訂版 spec
**Related**:
- [`2026-05-20-large-order-detection-design.md`](./2026-05-20-large-order-detection-design.md)(v2.1 設計)
- [`2026-05-20-large-order-detection-review.md`](./2026-05-20-large-order-detection-review.md)(Lens A — alpha 真偽 + 主力套路視角)

**Reviewer 視角**: 8+ 年台股自營商風控主管 / cost-aware trader
- 設計過 day trading 策略執行成本模型(slippage / spread / commission / 證交稅)
- 評估過內部訊號上線,殺掉過很多「paper 上有 alpha 但實際 net of cost 是負的」訊號
- 對 retail trader 過度交易 / 訊號疲勞造成 KPI 衰退非常熟悉
- 對台股流動性差異(2330 / 0050 vs 中小型 / 興櫃)的實際 fill behavior 有經驗

**Review 前提**(本 lens 不討論,假設 Lens A 已處理):
- 假設每個訊號 raw alpha 都存在
- 只評估「實際拿來交易,扣掉成本與認知負擔後,還剩多少 edge」

---

## TL;DR

Spec 在 alpha 設計面 sophisticated,但 **cost / 認知 / 風控三大面向 underbuilt**:

1. **訊號頻率太高**(估每天 100-300 alerts),沒有全系統 throttle
2. **B1′ 在現股根本是負期望**(net of cost 必虧)
3. **A1 / A_pull fast 沒有 actionable 方向**(訊號 ≠ 進場指令)
4. **訊號沒分動作類型**(主進場 / 確認 / 出場 / 觀察),使用者照 A3 / C2.5′ 反向進場會被宰
5. **同事件多訊號齊發無 suppression**(1 秒內 5 個 alerts 不知該看哪個)
6. **流動性陷阱完全沒設計**(訊號 fire ≠ 真的能交易)
7. **Safe mode 沒有**(開盤 / 末段 / 央行日 / 量縮日全照常 fire)
8. **PR 1 應該砍訊號數量,加風控基礎建設**

按目前 spec 直接 ship,**2 週後 user 會關掉所有 notification** — 訊號疲勞 + 看到的訊號 net of cost 是負的 + 看到訊號但市場流動性不允許執行。

---

## 0. 估算用基準

| 項目 | 數值 |
|---|---|
| watchlist 規模 | user 自選 10-20 檔 + DynamicUniverse 上限 50 檔 ≈ **總監控 ~70 檔** |
| 一筆進出總成本(現股,一般戶) | 手續費 0.1425% × 2 + 證交稅 0.3% = **0.585%** |
| 一筆進出總成本(現股,折扣戶 0.04%) | 0.08% + 0.3% = **0.38%** |
| 一筆進出總成本(當沖,折扣戶) | 0.08% + 0.15% = **0.23%** |
| 人類反應 + 下單延遲 | 富邦 books 1s + 推送 + 看 + 反應 + 下單 ≈ **4-6 秒** |
| Tick size 假設 | 100-500 元股 = 0.5 元 / 500-1000 元 = 1 元 / 1000+ 元 = 5 元 |

---

## 1. 訊號頻率 / 雜訊比預估

**結論**:不加 suppression 下,每天會 fire **100-300 個 alerts**,單人完全處理不完。

### 分訊號估算(70 檔監控,8 小時盤中)

| 訊號 | 單檔/天 | 70 檔/天 | 計算依據 |
|---|---|---|---|
| **A1** | 中型股 3-8 / 小型股 8-15 | **300-700** | 五檔×雙向=10 個 (檔位,方向) 組合,5× median 在中型股一天會過多次。冷卻 5min 也擋不住,因為冷卻是 (symbol, 檔位, 方向) — 同檔不同檔位算分開的 |
| **A3** | 0.5-2 | **30-100** | 牆被吃是稀少事件,合理 |
| **A_pull(fast+slow)** | 強勢股 3-10 / 普通 1-3 | **100-400** | 強勢股 spoofing 多到誇張 |
| **A_pull layering** | 強勢股 0.2-1 | **5-30** | OK |
| **B1′** | 強勢股 5-15 / 普通 2-5 | **300-800** | **3 秒淨 3 tick 在強勢股是 noise**,2 min 冷卻擋不住 |
| **B2** | 強勢股 3-8 | **100-400** | 強勢股拉抬常見 sweep,真大戶 + 散戶追高都會 fire |
| **B3** | 1-3 | **70-200** | 60s + 30× baseline,還算嚴 |
| **Trade-Through** | 強勢股 1-3 / 普通 0.5-1 | **30-100** | 物理事件,稀少且 informative |
| **C2.5′ independent** | 2-5 | **100-300** | 2 tick 門檻,失敗回測在強勢股很多 |

**Raw total: 1000-3000 alerts/day。** 假設訊號重疊去除 50%,仍是 **500-1500/day**,平均 **盤中每 1-3 秒一個 alert**。

### 認知負擔判斷

Retail 兼職盯盤,經驗值:**一個人一天最多消化 20-30 個有效訊號**,超過就會:
1. **訊號疲勞**:看到 alert 自動忽略
2. **選擇性偏誤**:只看符合自己 bias 的訊號(就跟沒訊號一樣)
3. **賭博化**:看到太多就亂跟

**合理範圍 → 每天 15-25 個 Tier A alerts**(平均每 20-30 分鐘一個,有時間 evaluate)。

**Spec 沒有任何「全系統總量 throttle」機制是嚴重問題。**

### 太少有沒有價值?
A3 / Layering / Trade-Through 即使一天總和 5-10 個都有價值,因為事件本身具強烈訊號意義。但前提是訊號家族的 hit rate 高到讓人不會 FOMO。

---

## 2. 執行成本吃 alpha — 哪些訊號 net of cost 是負的

### 通用前提
- 訊號觸發 → user 看到 → 人類反應 → 下單 ≈ **4-6 秒延遲**
- 在強勢股 4-6 秒 price 平均跑 **1-3 tick**
- 進場成本(folded into 一筆進出) ≈ 0.38% ~ 0.59%

### 個別訊號 cost-vs-alpha 試算

**舉例:300 元中型股,tick = 0.5 元**
- 1 tick = 0.5 / 300 = **0.167%**
- 一筆進出成本(折扣戶現股) = 0.38% = **~2.3 ticks**
- 一筆進出成本(當沖折扣戶) = 0.23% = **~1.4 ticks**

| 訊號 | Raw 預期 move | 滑價(4-6s) | 進場後可吃 | 扣成本(當沖折扣戶) | 結論 |
|---|---|---|---|---|---|
| **B1′(3 tick rapid)** | 已發生的 3 tick | -2 tick(已跑掉) | +1 tick 延續(樂觀) | 1 - 1.4 = **-0.4 tick** | **負期望** |
| **B2 sweep** | 已成 5 筆連續 | -3 tick | +2 tick 延續 | 2 - 1.4 = **+0.6 tick** | break-even,看流動性 |
| **B3 imbalance** | 60s 失衡 | -1 tick(較慢) | +2-4 tick | **+1-2 tick** | **微正**,但 60s 視窗已過半條趨勢 |
| **A1 wall appearance** | 沒有預期方向 | N/A | 牆方向是「壓」還是「撐」? | **未定義 alpha 方向** | **不應 actionable** |
| **A3 wall eaten** | 穿牆已發生 | -2 tick | +3-5 tick(主力 conviction) | **+1.6-3.6 tick** | **正期望** |
| **A_pull(slow)** | 反向操作? | 含時間判斷不確定 | 看後續走向 | **方向歧義,難量化** | **不應主進場** |
| **A_pull(layering)** | 同上 | 同上 | 同上 | 同上 | **觀察訊號** |
| **Trade-Through** | 已 ≥1 tick + 五檔吃光 | -2-3 tick | +3-8 tick(real conviction) | **+1.6-6.6 tick** | **正期望最高** |
| **C2.5′ independent** | 反向操作 | 反向時 spread 易拉大 | +2-3 tick 回測 | 2-3 - 1.4 = **+0.6-1.6 tick** | **微正**,但用來進場 risk/reward 比差 |

### Owner 應該知道但 spec 沒講的事

1. **B1′ 在非當沖情境下幾乎一定虧錢**:現股 0.585% = 3.5 ticks,raw move 3 tick + 滑價就被吃光
2. **小型股(<100元)tick 0.05 反而比中型股不利**:tick 占股價 0.05/80 = 0.0625%,要 6 個 tick 才打平當沖成本,但小型股波動每筆都跑 1-2 tick → 滑價吃更兇
3. **1000 元以上股(2330 等級)反而最適合**:tick = 5 元 / 1000 元 = 0.5%,1 tick 就過當沖成本一大半,移動性也好
4. **spec 完全沒有 cost-aware 設計**:per-liquidity-tier 只調 size threshold,沒調 tick 門檻;沒有 per-price-band 區分

### Recommendation

per-liquidity-tier 表加 **per-price-band tick threshold** 維度,或至少在 metadata 帶「預期淨 move」估算,用 confidence_tier 降級。

---

## 3. 位置不對稱 — 訊號該對應什麼動作

**這是 spec 最大的隱性問題:8 個 Tier A 訊號全 fanout 成「alert」,沒有區分動作類型,使用者照訊號操作會反向用錯。**

### 動作分類(8 個 Tier A)

| 訊號 | spec 預設語意 | **實戰應該是** | 理由 |
|---|---|---|---|
| **A1** wall appearance | 「主力出現,可以跟」 | **觀察訊號(informational)** | 沒有可執行方向(壓單=走弱?撐單=主力護盤?),且大概率是 spoofing 的早期狀態 |
| **A3** wall eaten | 「主力 conviction,跟單」 | **確認訊號(confirmation)** | 觸發時 price 已穿過,單獨進場太晚;適合用來「**確認已 hold position 加碼**」或「**從觀望轉進場的最後扣板機**」 |
| **A_pull (fast)** | spec 沒明說 | **觀察訊號** | 1 秒撤單可能是 algo cancel-replace,不是真 spoofing,alpha 不明 |
| **A_pull (slow)** | spec 沒明說 | **觀察訊號 + risk warning** | spoofing 確認,但 price 後續可能 spoof 方向反向也可能繼續,**沒有明確進場時機** |
| **A_pull (layering)** | spec 沒明說 | **風控訊號** — 標記該檔「主力做局中」,降該檔所有訊號 confidence | layering 是「整個 session 該檔不可信」的訊號 |
| **B1′** rapid tick move | 「急動跟進」 | **觀察訊號 / 當沖訊號** | 不該當 swing 進場訊號(成本吃光);當沖且能在 1 秒內反應才有 edge |
| **B2** sweep | 「掃單跟進」 | **主進場訊號**(但需配 confirmation) | 真大戶掃單 conviction 強,但需 wash filter clean + 流動性 OK |
| **B3** imbalance | 「失衡跟進」 | **主進場訊號** | 60s 持續失衡是相對乾淨的訊號 |
| **Trade-Through** | 「物理事件,strong conviction」 | **主進場訊號** | 最乾淨的訊號,spec 默認 HIGH confidence 正確 |
| **C2.5′ chain / independent** | 「失敗反向操作」 | **出場 / 認賠訊號** — 給已 hold 原突破方向 position 的人「平倉訊號」 | C2.5′ 反向進場 risk/reward 差(假突破後反向走多遠不確定),但若你已照 C2′ 進場,C2.5′ 是「**強制認賠 trigger**」 |

### 後果:沒分類會發生什麼

- 使用者看到 A1(壓單牆出現)以為要做空,結果是主力 spoofing 假象,price 反向衝高被軋
- 使用者看到 C2.5′ independent 反向進場,結果 price 在 line value 附近震盪,scratch out
- 使用者看到 A_pull slow 以為 spoofing 反向操作,實際 price 繼續原方向衝

### Recommendation

Metadata 加 `action_type` 欄位,值 `{primary_entry, confirmation, exit_signal, observation, risk_warning}`,UI 用不同顏色 / icon 區分。**Tier A 不等於「應進場」**。

---

## 4. 訊號相互關係 / Sequencing

### 重疊問題實例

**Case 1: 強勢股拉抬瞬間(常見)**
```
t=0.0s: Trade-Through fire(物理穿透)
t=0.5s: B2 sweep fire(5 筆連續同向)
t=0.8s: B1′ fire(3 tick 急動)
t=1.0s: A3 fire(壓力牆被吃)
t=1.2s: B3 fire(60s 內外盤失衡)
t=60s : C2′ internal log(突破 CDP_NH)
t=120s: C3a′ approaching log
```
**1 秒內 5 個 Tier A alerts。** 使用者點 A1 看完,B 系列已經過去了。

**Case 2: 假突破(也常見)**
```
t=0s  : A3(撐單被吃,下跌)
t=3s  : B1′(急跌 3 tick)
t=5s  : B2(連續主動賣)
t=60s : C2′ log(跌穿 CDP_NL)
t=120s: 開始反彈
t=180s: C2.5′ independent fire(失敗反向)
t=181s: A_pull slow fire(剛剛的撐單原來是真撤?)
```
使用者要怎麼解讀「同檔股票 3 分鐘內前後矛盾的 7 個訊號」?

### Priority / Suppression 建議

**Layer 1 — 同事件 dedupe(必須)**

```
Within (symbol, direction, 5 秒窗口):
  Priority: Trade-Through > B2 sweep > B1′ rapid > A3 > B3
  → 只顯示最高 priority,其他併為「confirming signals」帶 metadata
```

**Layer 2 — Cross-event suppression**

```
Trade-Through 觸發後 60 秒內 → suppress 同方向 B1′ / B2 / B3
A3 觸發後 60 秒內 → suppress 同方向 B1′ / B2
A_pull layering 啟動後 → 該 symbol 所有訊號 confidence_tier 強制降一級
```

**Layer 3 — 矛盾訊號併現(警告)**

```
同 symbol 5 分鐘內出現方向相反訊號(例: A3 ask 牆被吃 + 後續 B2 sell sweep)
→ 不直接顯示矛盾,改顯示 "directional_uncertainty" meta alert
→ confidence_tier 強制 LOW
```

### 具體場景處理

| 同時 fire | 應該顯示 | 隱藏為 confirming |
|---|---|---|
| Trade-Through + B2 + B1′(同向) | Trade-Through(主) | 顯示 chip「+B2 +B1′」 |
| A3 + B1′ + B2 + B3(同向) | A3(主訊號) | 顯示 chip「+B1′ +B2 +B3」 |
| C2.5′ chain + C2.5′ independent(同 symbol) | C2.5′ chain(較強) | 隱藏 independent |
| B1′ + B1′(連續同 symbol) | 後者 suppress,2 min 內只一個 | — |

**spec 第 1207 行「未決待後續 review」有列訊號 priority,但這是 v1 ship 前的核心問題,不是 v2 follow-up。**

---

## 5. 流動性陷阱 — 訊號 fire ≠ 真的能交易

### 經典場景:訊號告訴你進,但你進不去

**Case A:Trade-Through 後五檔抽光**
- 你看到 Trade-Through(up_through),想跟進
- 物理穿透剛把五檔吃光,你下市價:price = ask6 / ask7(可能 +3-5 tick)
- 下限價 = 沒人賣,撈不到
- **實際 fill 比訊號 price 差 3-5 tick,alpha 被吃光**

**Case B:強勢股漲停預兆**
- 訊號 fire,你想跟
- 距漲停 < 1%,bid 排隊大量 / ask 抽光
- 下市價 = 漲停價成交(可能 +0.5%)
- spec 雖然 C 系列接近漲跌停 skip,但 **B 系列、A 系列、Trade-Through 沒 skip**

**Case C:小型股訊號**
- 訊號 fire,但近 1 分鐘成交 5 張 / 8 筆
- spread = bid 98 / ask 99 / 1.0 元 = 1 tick(看似 OK)
- 下限價要等 10 分鐘才成交,下市價直接吃 ask3-4
- **訊號 fire 時市場根本不存在你期望的流動性**

**Case D:盤中波動瞬間 spread 拉大**
- 大跌瞬間 / 急動瞬間
- 平常 spread 1 tick → 訊號當下 spread 3-5 tick
- 下市價直接被宰 3 tick

### 哪些訊號最易遇

| 訊號 | 流動性陷阱風險 | 原因 |
|---|---|---|
| **Trade-Through** | **極高** | 訊號本身就是「吃光五檔」,fire 時 depth 已空 |
| **A3 wall eaten** | **高** | 牆被吃完 next 五檔可能薄 |
| **B2 sweep** | **高** | sweep 把 5 筆 ask 吃完,depth 減 |
| **B1′ rapid** | **中** | 急動瞬間 spread 易拉大 |
| **C2.5′ independent** | **中** | 反向時可能進場方向 depth 不好 |
| **A1 wall appearance** | **低** | 訊號本身就帶五檔 size(本來就深) |
| **A_pull** | **中** | 撤單後該方向 depth 變薄 |
| **B3 imbalance** | **低** | 60s 視窗,瞬間 spread 影響小 |

### Recommendation: liquidity guard metadata

每個 Tier A 訊號 fanout 時 metadata 必帶:

```json
"liquidity_guard": {
    "feasibility_score": 65,                    // 0-100,綜合分
    "remaining_same_side_depth": 320,           // 訊號方向的剩餘五檔 size
    "spread_ticks": 1,                          // bid1-ask1 tick 差
    "recent_1min_volume": 850,                  // 近 1 分鐘成交量
    "distance_to_limit_pct": 4.2,               // 距漲跌停 %
    "expected_entry_slippage_ticks": 2,         // 預估滑價
    "trade_through_warning": false              // 訊號本身吃光 depth
}
```

UI 上 feasibility_score < 50 的訊號標灰,< 30 的隱藏。

**這是 spec 完全沒設計,但對 retail 工具最關鍵的一塊**:看到訊號 vs 能交易訊號是兩件事。

---

## 6. 系統性失效情境 — Safe Mode

### 整族訊號集體失效的場景

| 場景 | 為何整族失效 | 哪些訊號最先壞 |
|---|---|---|
| **集合競價開盤 9:00-9:05** | baseline 還在 warm-up;一筆超大開盤量秒衝 baseline 上去;tick jumps 多 | A1 / B1′ / B2 / Trade-Through 全 false positive 爆炸 |
| **9:05-9:15 開盤穩定期** | 上一日跳空後的方向確認,正常急動但訊號會誤判 | B1′ / B2 / A3 |
| **12:30-13:30 末段拉尾盤** | 主力 last-minute push / 收盤前掃單(MOC)結算 | B 系列爆炸、A_pull spoof 假象多 |
| **美股盤後 -3% 隔日開盤** | 全市場跳空,個股聯動,所有 baseline 失效 | B 系列全失效;C2.5′ 假突破 baseline 偏 |
| **台指期跌停 / 接近跌停** | 股市跟跌,個股 panic selling | B3 imbalance / B2 sweep 假象多 |
| **月底 / 季底 / ex-date** | 法人櫥窗;ex-date 除權息日股價斷層 | C 系列(CDP 跨除權失效);A1 baseline(跨日 close 斷層) |
| **央行 / FOMC 決議日** | 全市場 sit-out 直到事件後 60 秒爆炸 | 全部訊號 |
| **量縮淡盤(<0.6× 平均)** | baseline 統計樣本不夠,5×median = 真實值的 30% | A1 / B2 / B3 大量 false positive |
| **個股新聞 / 重大公告** | 該檔 baseline 全失效 | 該檔全訊號失效 |
| **漲跌停鎖死** | 撮合無實質交易 | A_pull / A1 / Trade-Through |

### Spec 處理現況

- ✅ 強勢股近漲跌停 1.5% skip C 系列
- ✅ DynamicUniverse 排除漲停股(MAX_CHANGE_PCT = 9.5%)
- ❌ 開盤 30 秒 warm-up:**沒做**(spec 第 1024 行寫「接受開盤前 30s 雜訊」)
- ❌ 跨商品事件:**沒做**
- ❌ 法人偏好日 / 央行日:**沒做**
- ❌ 量縮淡盤:**沒做**
- ❌ Cross-market panic mode:**沒做**

### Safe Mode 建議設計

**三檔 Mode**:
```
NORMAL    : 預設,全訊號照常
DEGRADED  : 部分訊號降權 / 提升閾值
SUSPENDED : 大部分訊號暫停,只留 Trade-Through 等物理事件
```

**自動切換規則**:
```python
def market_mode():
    # SUSPENDED
    if 9:00 <= now <= 9:05:                          return SUSPENDED  # 集合競價穩定
    if now <= calendar.ex_date and is_member(symbol): return SUSPENDED  # 除權息日該檔
    if abs(taiex_change_pct) >= 5:                   return SUSPENDED  # 大盤崩盤日
    if futures_limit_down():                         return SUSPENDED  # 台指期跌停

    # DEGRADED
    if 9:05 <= now <= 9:15:                          return DEGRADED   # 開盤穩定期
    if 13:20 <= now <= 13:30:                        return DEGRADED   # 末段拉尾盤
    if total_market_volume < 0.6 * 20d_avg:          return DEGRADED   # 量縮淡盤
    if abs(taiex_change_pct) >= 2:                   return DEGRADED   # 大盤波動高
    if today in [month_end, quarter_end]:            return DEGRADED   # 法人偏好日
    if today in cb_calendar.events:                  return DEGRADED   # 央行日

    return NORMAL
```

**個別 Mode 行為**:

| Mode | 訊號處理 |
|---|---|
| SUSPENDED | 全部 Tier A 暫停。**只留 Trade-Through 物理事件**,且 confidence_tier 強制 MEDIUM |
| DEGRADED | 全部閾值 ×1.5;cooldown ×2;confidence_tier 上限 MEDIUM;A_pull layering 仍 HIGH |
| NORMAL | spec 預設 |

**UI 上 banner 顯示「市場進入 DEGRADED mode — 訊號降權中」**,user 看到 alert 數量降低時知道為什麼。

**這是風控層強制要做的事**:不是 nice-to-have。沒有 safe mode = 在最不該交易的時段給最多訊號(因為波動大訊號才會 fire) = 反向的設計。

---

## 7. 最終建議 — 「真實可執行版本」

### 7.1 訊號組合調整

**砍掉 / 大幅降權**

| 訊號 | 處置 | 理由 |
|---|---|---|
| **A1 wall appearance** | **砍掉 Tier A,降 Tier C internal log** | 沒有 actionable 方向、70% 以上是 spoofing 早期狀態(A_pull 會接著 fire),fire 太多認知負擔最大 |
| **B1′ rapid tick move** | **砍掉 Tier A,降 Tier C** 或合併進「Trade-Through 副訊號」 | net of cost 負期望,雜訊最多 |
| **A_pull fast (lived < 1s)** | **改 Tier C internal log** | 1 秒撤單分不出 algo cancel-replace vs spoofing,雜訊高 |
| **C2.5′ independent (2 tick)** | **降為 exit-only,UI 標「平倉訊號」** | 反向進場 risk/reward 差,只應該觸發已 hold position 的平倉 |

**保留 + Confirmation Layer**

| 訊號 | Confirmation 要求 | 等級 |
|---|---|---|
| **Trade-Through** | 無需(自身即為強訊號) | **Strong** |
| **A_pull layering** | 無需(整族風控訊號) | **Strong (Risk)** |
| **A_pull slow** | + WashTradeDetector inactive + 該檔 1 hr 內 ≥ 2 次 slow | **Medium** |
| **A3 wall eaten** | + 觸發前該方向五檔仍有深度(可繼續吃)+ wash inactive | **Strong** |
| **B2 sweep** | + B3 同向 30s 內也 fire + wash inactive | **Strong** |
| **B3 imbalance** | + 30s 內 B2 同向(自動配對) | **Medium** |
| **C2.5′ chain** | 已有 chain confirmation,留 | **Strong (Exit)** |

### 7.2 訊號等級設計

```
Strong              : 桌面 notification + 聲音
Medium              : TriggerList 高亮,no notification
Informational/Exit  : TriggerList 灰色,給 hold position 看
```

### 7.3 每日訊號上限(throttle)

```python
# 全系統(所有 watchlist + DynamicUniverse 合計)
MAX_STRONG_ALERTS_PER_DAY     = 25
MAX_MEDIUM_ALERTS_PER_DAY     = 50
MAX_ALERTS_PER_SYMBOL_PER_DAY = 5

# 觸發上限後:該訊號類型 / 該 symbol 停發
# UI 顯示「today's quota reached for {category}」
```

### 7.4 靜音模式(Safe Mode)— 必做

如 §6 描述,**三檔 mode + 自動切換 + UI banner**,這是核心風控,不可省略。

### 7.5 流動性 Guard — 必做

每個訊號 metadata 帶 `feasibility_score`(如 §5 描述),UI 用顏色梯度區分。

### 7.6 動作分類 metadata — 必做

每個訊號帶 `action_type`(primary_entry / confirmation / exit / observation / risk_warning),UI 區分。

### 7.7 Implementation Order 修正建議

Spec PR 1 選擇 A_pull + Trade-Through + C2.5′ independent,**部分同意**,但要改:

**PR 1 修正版**:
- ✅ MarketStats / WashTradeDetector / DynamicUniverse / 兩表 logging
- ✅ Trade-Through(獨立、最乾淨)
- ✅ A_pull layering(風控訊號)
- ❌ A_pull slow → 留 PR 2(單獨太雜)
- ❌ A_pull fast → 砍
- ❌ C2.5′ independent → 改 exit-only,留 PR 4
- ➕ **新增:Safe Mode 基礎架構 + 流動性 guard metadata** — 這比訊號本身更關鍵
- ➕ **新增:訊號 throttle + 全系統 alert 上限**

**Critical Gate(PR 1 驗證)**:
- Trade-Through 一週實測 hit rate(預期 fire 後 5 min 內延續 ≥ +3 tick 的比例 > 55%)
- 訊號疲勞測試:user 自評「看完 Tier A alert 處理花費時間」< 認知預算
- 流動性 guard feasibility_score 分布,< 50 的訊號比例 < 20%

---

## 對 Spec 的明確修改清單

照 review 結果,修訂版 spec 應該加入以下章節:

1. **「Action Type 分類」章節** — 每個訊號明確標 `action_type`
2. **「Signal Priority / Suppression」章節** — 移出 Open Questions 第 1207 行,變成 v1 必做
3. **「Liquidity Guard Metadata」章節** — 每訊號 fanout 帶 feasibility_score
4. **「Safe Mode」章節** — 三檔 mode + 自動切換規則 + UI banner
5. **「Throttle 機制」章節** — 全系統 / per-symbol / per-signal-type 上限
6. **修訂 Per-Liquidity-Tier 表** — 加 per-price-band tick threshold 維度
7. **修訂 Implementation Order PR 1** — 砍訊號數量,加風控基礎建設,新增 critical gate

修訂後 PR 1 範圍:
- 基礎建設(MarketStats / WashTradeDetector / DynamicUniverse / logging schema)
- 2 個高 conviction 訊號:**Trade-Through + A_pull layering**
- **Safe Mode 三檔 + 自動切換**
- **Liquidity Guard metadata**
- **訊號 throttle**
- 前端 TriggerList(含 action_type / feasibility_score / safe mode banner)

PR 2 以後再展開其他訊號。
