# Large Order Detection — 墊單與內外盤大單訊號系統(v2)

**Date**: 2026-05-20
**Status**: Brainstorming v3(Lens A + Lens B reviews merged,待專業交易人士 review 後進 writing-plans)
**Supersedes**: v1, v2, v2.1
**Related Reviews**:
- [`2026-05-20-large-order-detection-review.md`](./2026-05-20-large-order-detection-review.md)(Lens A — alpha 真偽 + 主力套路)
- [`2026-05-20-large-order-detection-lens-b-review.md`](./2026-05-20-large-order-detection-lens-b-review.md)(Lens B — 風控 / 成本 / 認知負擔)

---

## Summary

訊號家族包含 **11 個訊號 + 1 個 meta filter + 1 個動態 universe service**,**所有訊號分動作類型 + confidence + 受 Safe Mode / Throttle 管控**。

### 訊號分組(按 Tier + Action Type)

**Tier A — 對外 alert**(7 個):
- `primary_entry`:Trade-Through / B2(配 confirmation) / B3(配 confirmation)
- `confirmation`:A3
- `risk_warning`:A_pull layering
- `observation`:A1 / A_pull slow
- `intraday_observation`:B1′(現股慎用,當沖場景)

**Tier A — exit-only**(2 個):
- `exit_signal`:C2.5′ chain / C2.5′ independent(只給已 hold 原突破方向 position 的人「平倉訊號」,不該當反向進場)

**Tier C — Internal state**(5 個,只 log 不對外):
- C2′ / C3a′ / C3b′ / C3c′(完整 chain)
- A_pull fast(< 1s 撤,跟 algo cancel-replace 分不開)

**Tier B — Meta Filter**(1 個):WashTradeDetector(confidence 調節)

**Service**(1 個):DynamicUniverse(自動追蹤強勢股)

### 風控基礎建設(v1 必做,Lens B 強調)

1. **Action Type** 分類:每訊號明確標 primary_entry / confirmation / exit / observation / risk_warning,UI 區分對待
2. **Signal Priority / Suppression**:同事件 dedupe + cross-event suppression
3. **Liquidity Guard Metadata**:每訊號帶 `feasibility_score`(剩餘 depth / spread / 距漲跌停 / 預估滑價)
4. **Safe Mode**:三檔(NORMAL / DEGRADED / SUSPENDED),自動按市況切換
5. **Throttle**:全系統 + per-symbol + per-type 每日 quota

### 核心技術變更

1. 富邦 WebSocket 新增訂閱 `books` channel(目前只訂 `trades`)
2. 富邦 HTTP API 新增使用 `snapshot/movers` 端點(動態 universe)
3. 新模組 `backend/services/market_stats.py` — per-symbol-per-level rolling 統計(用 median)
4. 新模組 `backend/services/wash_detector.py` — wash trade 偵測
5. 新模組 `backend/services/dynamic_universe.py` — 強勢股 dynamic universe
6. 新模組 `backend/services/signal_ground_truth.py` — MFE/MAE 背景回填 job
7. **新模組 `backend/services/safe_mode.py`** — 市場模式判定(v3 新增)
8. **新模組 `backend/services/signal_throttle.py`** — 訊號頻率管控(v3 新增)
9. **新模組 `backend/services/liquidity_guard.py`** — 流動性可行性評分(v3 新增)
10. `signal_engine.py` 擴充新 state:`_active_walls`、`_pending_breakthroughs`,以及訊號 dispatch 前的 priority / suppression / throttle / safe_mode / liquidity_guard pipeline
11. `models/condition.py` 新增 7 個 Pydantic 訊號 schema
12. `signals_log` schema 擴充 + `signals_ground_truth` 新表
13. `active_signals.scope` 擴充:新 `dynamic_strong` 型別

---

## Goals

- 偵測墊單(委託簿異常大單)的出現 / 被吃 / 被撤(spoofing)三類事件
- 偵測成交異常(急動 / 掃單 / 量失衡 / 穿價)四類事件
- 偵測牆破 + 技術突破雙重確認的複合事件,含回測 / 失敗的完整 state machine
- 所有閾值採相對倍數設計,並依流動性 tier 分組
- **訊號設計第一天就支援量化後置**(MFE/MAE logging + ground truth 回填 job)
- **訊號池要自動追蹤強勢股**(漲幅 ≥6% + 成交量 ≥3000 張),不只 user 自選的 watchlist
- **Wash detector 當訊號 confidence filter**,降低主力做局訊號的影響
- **訊號可用性優先於訊號數量**:每訊號標 action_type、過 priority/suppression/throttle/safe_mode/liquidity_guard 五層 pipeline,寧可訊號少且可執行,不要訊號多但 user 兩週後關掉 notification

## Non-goals

- **A2 撤單偵測另一版本** — 已用 A_pull(fast + slow + layering)覆蓋
- **C1 墊單在關鍵價位** — 取消,已從設計中移除
- **多檔位 book congestion** — 雙邊都有大牆訊號,v1 不做
- **books channel 即時五檔前端顯示** — v1 books 只 backend 內部用
- **上一交易日 baseline bootstrap** — v1 純 in-memory rolling
- **下單功能** — 純偵測訊號,不觸發 `place_order`
- **Broker-level cross detection** — 撮合不暴露 broker info,技術上做不到
- **權證 / 期貨 universe** — 動態 universe 限上市+上櫃股票 + ETF(`type=ALLBUT099`)

### v2 候選(列下游 follow-up,本 spec 不實作)

- Quote-Trade Sequencing Anomaly(富邦 1 秒延遲,粒度不夠)
- Per-Minute-of-Day Volume Anomaly(要存 20 個交易日每分鐘 baseline,工程量大)
- Futures-Cash Basis Anomaly(要加期貨 scope,跨 product)
- Cancel Rate Spike(SDK 沒給 cancel,需 books 推送頻率 proxy)

---

## Architecture

```
backend/
├── services/
│   ├── market_stats.py              [新] per-symbol-per-level rolling stats(median)
│   ├── wash_detector.py             [新] WashTradeDetector
│   ├── dynamic_universe.py          [新] 強勢股動態追蹤
│   ├── signal_ground_truth.py       [新] MFE/MAE 背景回填 job
│   ├── safe_mode.py                 [新] 市場模式判定(NORMAL/DEGRADED/SUSPENDED)
│   ├── signal_throttle.py           [新] 訊號頻率管控(per-day quota)
│   ├── liquidity_guard.py           [新] 流動性可行性評分(feasibility_score)
│   ├── signal_priority.py           [新] 訊號優先級 / suppression / dedupe
│   ├── fubon_ws.py                  [改] 新增 books channel 訂閱 + 處理 callback
│   └── signal_engine.py             [改] 新 _active_walls、_pending_breakthroughs state
│                                          新 _eval_book_tick / 擴充 _evaluate
│                                          C 系列 state machine 推進邏輯
│                                          新 dispatch pipeline:
│                                              safe_mode → throttle → priority → liquidity_guard
│                                          每訊號帶 action_type / confidence_tier / feasibility_score
│
├── models/
│   └── condition.py                 [改] 新增 7 個訊號類型 Pydantic schema
│                                          ActiveFilter schema 3 → 4
│                                          ActiveScope 加 dynamic_strong type
│                                          訊號 metadata schema 加 action_type / feasibility_score
│
├── routes/
│   └── active_signals.py            [改] 接受新 filter type、verify schema
│
└── db/
    └── migrations/                  [新] signals_log 擴充欄位 + signals_ground_truth 新表 migration

frontend/
├── src/
│   ├── components/
│   │   ├── TriggerList.tsx          [改] 新訊號類型 row(action_type icon + confidence + feasibility 顏色)
│   │   ├── SafeModeBanner.tsx       [新] 顯示市場模式 banner(DEGRADED/SUSPENDED 時提示)
│   │   ├── ThrottleStatusBar.tsx    [新] 今日 alert quota 進度
│   │   ├── ActiveSignalEditor.tsx   [改] 編輯 UI(action_type 選擇 / dynamic_strong scope)
│   │   └── SignalChip.tsx           [改] action_type icon + confidence dot
│   └── lib/
│       └── api.ts                   [改] 新訊號 TS 型別含 action_type / feasibility_score

```

不動的部分:`ring_buffer.py` / `fubon_client.py` / `supabase_writer.py` / `cdp.py` / `ma_service.py` / `_field_cache` 機制全沿用。

---

## 訊號清單(11 個 + 1 個 meta filter)

每訊號明確標 `action_type`,UI 與通知行為依此區分。

| Tier | 訊號 | action_type | 等級 | 觸發來源 | 直覺 |
|---|---|---|---|---|---|
| A | **Trade-Through** | primary_entry | Strong | trades | 單筆 size 物理穿過整本五檔(wash 抗性最高) |
| A | A3 | confirmation | Strong | books + trades | A1 的牆被吃光(主力 conviction 確認) |
| A | B2 | primary_entry (need confirm) | Strong | trades | 連續單向主動成交(需 B3 同向或 wash inactive) |
| A | B3 | medium_entry | Medium | trades | 60s 內外盤量失衡(可與 B2 互為 confirmation) |
| A | A_pull layering | risk_warning | Strong (Risk) | books + trades | 5min 內 ≥3 次 spoof(整檔不可信) |
| A | A_pull slow | observation | Medium | books + trades | ≥10s 撤,典型 spoofing pattern(無明確進場方向) |
| A | A1 | observation | Informational | books | 五檔某檔 size 暴增(壓 / 撐方向歧義,參考用) |
| A | B1′ | intraday_observation | Informational | trades | 3 秒淨 3 tick 急動(現股負期望,只給當沖) |
| A | **C2.5′ chain** | exit_signal | Strong (Exit) | trades | 突破鏈失敗 — 給已 hold position 平倉 trigger |
| A | **C2.5′ independent** | exit_signal | Medium (Exit) | trades | 短時假突破 — 給已 hold position 平倉 trigger |
| C | C2′ | — | — | A3 後 trades | 牆破後突破 CDP/MA(internal log only) |
| C | C3a′ | — | — | C2′ 後 trades | 接近被突破的線 |
| C | C3b′ | — | — | C3a′ 後 trades | 真正觸到線 |
| C | C3c′ | — | — | C3b′ 後 trades | 線守住,反彈 ≥3 tick |
| C | A_pull fast | — | — | books + trades | < 1s 撤(跟 algo cancel-replace 分不開,internal log only) |
| B | WashTradeDetector | (meta filter) | — | trades | 偵測 wash pattern,套全 Tier A confidence label |

**Action Type 設計理由**(Lens B review 強調):

1. **不分 action_type = 使用者照訊號操作會反向用錯**。例如:把 C2.5′ 當主進場訊號(反向操作)而非平倉訊號 → r/r 比差
2. **訊號質量 ≠ 訊號可用性**:Trade-Through 是 primary_entry / A3 是 confirmation 即使兩個都 Strong,UI 跟使用者預期動作完全不同
3. **A1 / B1′ 沒砍 Tier C 的理由**:user 原始需求包含「偵測墊單」,完全砍掉等於否定核心動機 → 改 observation,UI 不發桌面 notification 但 TriggerList 顯示
4. **A_pull fast 降 Tier C 的理由**:1s 撤跟 algo cancel-replace 無法區分,雜訊高;事後做為 layering 計數的 input
5. **C 系列降 Tier C 的理由**(Lens A review):chain 越長越容易被主力刻意製造,但 C2.5′ 例外(contrarian 訊號主力不會自製),維持 Tier A

---

## Action Type + Confidence + UI 行為

每個 Tier A 訊號 fanout 時帶 **`action_type`** + **`confidence_tier`** + **`feasibility_score`** 三個維度,UI 組合呈現。

### `action_type` 定義

| action_type | 語意 | UI 行為 |
|---|---|---|
| `primary_entry` | 獨立可進場訊號 | 桌面 notification + 聲音 + TriggerList 顯眼(綠/紅 by direction) |
| `confirmation` | 確認訊號,配合其他訊號用,單獨不夠 | TriggerList 顯眼但**不發 notification** |
| `medium_entry` | 中等強度進場(配 confirmation 升級為 primary) | TriggerList 高亮 |
| `exit_signal` | **只給已 hold position 的人用作平倉 / 認賠 trigger** | TriggerList 紫色標「平倉」icon |
| `observation` | 純資訊,不應 actionable | TriggerList 灰色標「觀察」icon,**預設摺疊** |
| `intraday_observation` | 限當沖場景觀察(現股慎用) | TriggerList 標「當沖」icon |
| `risk_warning` | 風險警示(整檔不可信) | TriggerList 標「警示」icon + 強制該檔所有訊號降一級 confidence |

### `confidence_tier` 定義

| Tier | 條件 | UI 行為(預設) |
|---|---|---|
| HIGH | wash_active = false,且訊號自身物理 / 結構強(Trade-Through / A_pull layering) | 訊號明顯顯示 |
| MEDIUM | 既不是 HIGH 也不是 LOW | TriggerList 一般顯示 |
| LOW | wash_active = true,或 A_pull layering 偵測中該檔,或低流動性 tier | TriggerList 灰色 / 預設摺疊 |

### `feasibility_score` 定義(0-100)

由 LiquidityGuard 計算,進入 metadata:

| 分數 | UI 行為 |
|---|---|
| ≥ 70 | 綠 dot |
| 50-69 | 黃 dot |
| < 50 | 灰 dot,訊號預設隱藏 |
| < 30 | 完全不對外發送(只 log) |

### 三維組合 UI 規則

```
notification 發不發:
  - action_type=primary_entry AND confidence_tier=HIGH AND feasibility_score >= 70 → 發
  - action_type=exit_signal AND confidence_tier in (HIGH,MEDIUM) → 發
  - 其他 → 不發桌面 notification(仍寫 TriggerList)

TriggerList 顯不顯示:
  - feasibility_score < 30 → 不顯示
  - confidence_tier=LOW 且 dynamic_strong scope → 不顯示(避免強勢股疲勞)
  - 其他 → 顯示,視覺由 action_type / confidence_tier / feasibility_score 組合決定
```

### 強勢股場景特別處理

`dynamic_strong` scope 的訊號:
- `confidence_tier=LOW` 預設隱藏
- A1 / B1′(observation 類)預設摺疊
- 其他訊號各自的冷卻時間延長(見訊號 detail)

---

## Signal Dispatch Pipeline

訊號從觸發到 fanout,**強制經過 5 層 pipeline**(全部通過才對外發送):

```
[訊號偵測] (各訊號 _eval_* method)
    ↓
[1] SafeMode 過濾
    SUSPENDED → 全部 Tier A 暫停(只留 Trade-Through HIGH)
    DEGRADED → 全部閾值 ×1.5,cooldown ×2,confidence 上限 MEDIUM
    NORMAL → 照常
    ↓
[2] 訊號內部判定通過 (各訊號自身條件)
    ↓
[3] Priority / Suppression(SignalPriority)
    同事件 dedupe(5s 視窗內取最高 priority,其他併為 confirming chips)
    Cross-event suppression(Trade-Through 後 60s suppress 同向 B 系列)
    矛盾訊號降級(同 symbol 5min 內方向相反 → 強制 LOW)
    ↓
[4] LiquidityGuard 評分
    計算 feasibility_score(0-100)
    < 30 → 完全不發,只寫 internal log
    < 50 → 標記但發
    ≥ 50 → 一般發送
    ↓
[5] Throttle 過濾
    全系統 / per-symbol / per-type 每日 quota 檢查
    超過 → 不發
    ↓
[訊號 fanout] (broadcaster + supabase_writer + signals_ground_truth)
```

每個訊號從觸發到 fanout 經過五層檢查,**任一層拒絕該訊號就 silently dropped**(但仍可選擇性寫入 internal log 供量化分析)。

---

## Signal Priority / Suppression

### Layer 1 — 同事件 dedupe(必須)

同一個 `(symbol, direction, 5 秒視窗)` 內若多訊號同時觸發,只發 priority 最高的訊號,其他併為「**confirming signals**」進 metadata。

**Priority 順序**(高→低):
```
Trade-Through > A3 > B2 > B1′ > B3 > A1 > A_pull (任何 sub_type)
```

例:1 秒內 fire 了 Trade-Through + B2 + B1′ + A3 → 只發 Trade-Through,其他併入 metadata:
```json
{
  "type": "trade_through",
  ...原 metadata...,
  "confirming_signals": ["A3", "B2", "B1_prime"],
  "confirming_count": 3
}
```

### Layer 2 — Cross-event suppression

訊號 fire 後一段時間內,**suppress 相關後續訊號**(避免重複 alert):

| 主訊號 | suppress 對象 | 時間 |
|---|---|---|
| Trade-Through fire | 同向 B1′ / B2 / B3 | 60 秒 |
| A3 fire | 同向 B1′ / B2 | 60 秒 |
| A_pull layering 啟動 | 該 symbol 所有訊號 confidence 強制降一級 | 持續 5 分鐘 |
| B2 fire | 同向 B1′ | 30 秒 |

### Layer 3 — 矛盾訊號併現(警告)

同 symbol 5 分鐘內出現方向相反訊號(如 ask 牆被吃 + 後續 sell sweep):
- **不直接顯示矛盾**(過去 5min 內方向相反訊號計數 ≥ 2 → 觸發)
- 改顯示 `directional_uncertainty` meta alert
- 該 symbol 後續所有訊號 `confidence_tier` 強制 LOW(持續 10 分鐘)

### State 維護

```python
class SignalPriority:
    def __init__(self):
        # 5s 視窗內已 fire 的訊號(供 dedupe)
        self._recent_signals: dict[(symbol, direction), list[signal_event]] = ...
        # cross-event suppression 計時
        self._suppression_until: dict[(symbol, signal_type, direction), float] = ...
        # 矛盾偵測
        self._opposite_direction_history: dict[symbol, list[signal_event]] = ...
    
    def should_dispatch(self, signal_event) -> tuple[bool, dict]:
        """回 (是否發送, 補充 metadata)"""
        ...
```

Heartbeat 每秒清過期 state(5s / 60s 視窗的過期事件)。

---

## Liquidity Guard

### 為何需要

訊號 fire ≠ 真的能交易。最常見問題:

1. **Trade-Through 自身吃光 depth** — 訊號最強但 fire 時已沒人接
2. **接近漲跌停 spread 拉大** — 滑價 3-5 tick 把 alpha 吃光
3. **量縮股訊號** — 訊號 fire 但 1 分鐘只 5 筆,fill 等不到
4. **波動瞬間 spread 拉大** — 急動瞬間 bid-ask spread 1 tick → 5 tick

LiquidityGuard 在每個訊號 fanout 前**計算 `feasibility_score`(0-100)**,UI 用顏色梯度區分,< 30 直接不發。

### 計算邏輯

```python
class LiquidityGuard:
    def feasibility_score(self, symbol: str, signal: SignalEvent) -> dict:
        book = latest_book_snapshot(symbol)
        recent = trades_last_1min(symbol)
        cdp = field_cache[symbol]
        
        # 4 個子分數,各 0-25,合成總分
        depth_score = self._depth_score(book, signal.direction)  
            # 訊號方向剩餘五檔總 size / baseline_median × 25 (capped)
        spread_score = self._spread_score(book)  
            # 1 - (current_spread_ticks / typical_spread_ticks) × 25 (capped)
        volume_score = self._volume_score(recent)  
            # min(recent_1min_volume / baseline_per_min, 1.0) × 25
        limit_distance_score = self._limit_distance_score(cdp, signal.price)  
            # 距漲跌停 % 對應分數:>5% = 25 / 3-5% = 15 / 1-3% = 5 / <1% = 0
        
        feasibility = depth_score + spread_score + volume_score + limit_distance_score
        
        # Trade-Through warning(訊號本身吃光 depth)
        trade_through_warning = signal.signal_type == "trade_through"
        if trade_through_warning:
            feasibility = min(feasibility, 60)  # 物理穿透訊號 cap 在 60
        
        return {
            "feasibility_score": feasibility,
            "remaining_same_side_depth": depth_score * 4,  # 攤回原值
            "spread_ticks": current_spread_ticks,
            "recent_1min_volume": recent_volume,
            "distance_to_limit_pct": dist_pct,
            "expected_entry_slippage_ticks": estimate_slippage(book, signal),
            "trade_through_warning": trade_through_warning,
        }
```

### 訊號 metadata 加入

每個 Tier A 訊號 fanout metadata 加:
```json
"liquidity_guard": {
    "feasibility_score": 65,
    "remaining_same_side_depth": 320,
    "spread_ticks": 1,
    "recent_1min_volume": 850,
    "distance_to_limit_pct": 4.2,
    "expected_entry_slippage_ticks": 2,
    "trade_through_warning": false
}
```

### Per-訊號適用

- **Trade-Through**:必檢查(`trade_through_warning = true` 預設 feasibility cap 60)
- **A3 / B2 / B3 / B1′**:必檢查
- **A1 / A_pull observation**:可選(訊號本身就是觀察類,流動性陷阱影響小)
- **C2.5′(exit)**:必檢查(平倉時若市場已抽光,出不掉)

---

## Safe Mode(市場模式)

### 三檔 Mode

| Mode | 行為 |
|---|---|
| **NORMAL** | 預設,全訊號照常 |
| **DEGRADED** | 全部閾值 ×1.5;cooldown ×2;confidence_tier 上限 MEDIUM;A_pull layering 仍 HIGH |
| **SUSPENDED** | 全部 Tier A 暫停。**只留 Trade-Through 物理事件**,且 confidence_tier 強制 MEDIUM |

### 自動切換規則(精簡版,v1 範圍)

```python
class SafeModeService:
    def current_mode(self) -> Literal["NORMAL", "DEGRADED", "SUSPENDED"]:
        now = datetime.now(tz=TPE_TZ)
        hhmm = now.strftime("%H:%M")
        
        # SUSPENDED
        if "09:00" <= hhmm < "09:05":
            return "SUSPENDED"  # 集合競價穩定期
        if self._taiex_change_pct_abs() >= 5.0:
            return "SUSPENDED"  # 大盤崩盤日 ±5%
        
        # DEGRADED
        if "13:20" <= hhmm <= "13:30":
            return "DEGRADED"  # 末段拉尾盤
        if self._total_volume_ratio_vs_20d() < 0.6:
            return "DEGRADED"  # 量縮淡盤
        
        return "NORMAL"
    
    def _taiex_change_pct_abs(self) -> float:
        """加權指數漲跌幅絕對值(從富邦 intraday 取)。"""
        ...
    
    def _total_volume_ratio_vs_20d(self) -> float:
        """當日累計成交額 / 過去 20 個交易日平均(此時的進度比較,從 supabase OHLC 算)。"""
        ...
```

### v2 候選(本 spec 不做)

- 美股盤後跌 -3% / 台指期跌停 自動 SUSPENDED
- 月底 / 季底 / 央行 FOMC 日 DEGRADED
- 個股新聞 / 重大公告 該檔 SUSPENDED
- ex-date 除權息日該成分股 SUSPENDED

理由:這些情境對 retail 工具實務影響沒那麼大,實作工程量大,先精簡 4 條規則上線實測再決定。

### UI 呈現

`SafeModeBanner.tsx` 在 TriggerList 頂端顯示:

```
[DEGRADED] 市場進入降權模式 — 量縮淡盤,訊號閾值加倍中    [×]
[SUSPENDED] 市場進入暫停模式 — 大盤崩盤,僅 Trade-Through 仍發送 [×]
```

NORMAL 時不顯示 banner。

---

## Throttle(訊號每日上限)

### Quota 設定

全系統 + per-symbol + per-action_type 三個維度:

```python
class SignalThrottle:
    MAX_STRONG_ALERTS_PER_DAY = 40           # primary_entry + exit_signal (Strong)
    MAX_MEDIUM_ALERTS_PER_DAY = 80           # medium_entry + confirmation + Medium exit
    MAX_INFORMATIONAL_PER_DAY = 200          # observation / intraday_observation / risk_warning
    MAX_ALERTS_PER_SYMBOL_PER_DAY = 8        # 單一檔股票每日上限
    
    # State (每日重置,跨午夜 heartbeat 清)
    self._daily_count: dict[str, int] = ...      # by action_type
    self._symbol_daily_count: dict[str, int] = ...  # by symbol
```

### 行為

```
到 quota 後:
  該 action_type / 該 symbol 停發新訊號(其他類型 / 其他 symbol 仍可發)
  UI 顯示 ThrottleStatusBar 進度條 / 「today's quota reached for {category}」
  超量訊號仍寫 signals_log + signals_ground_truth(供量化)
```

### UI 呈現

`ThrottleStatusBar.tsx` 顯示:

```
今日訊號 Strong: 23 / 40    Medium: 31 / 80   Observation: 67 / 200
```

進度條漸進變色:綠(<50%) → 黃(50-80%) → 紅(>80%)。

### 跨午夜重置

Heartbeat 跨午夜(date.today() 改變)時清空 `_daily_count` / `_symbol_daily_count`,跟既有 cdp / touch_count 一樣處理。

---

## A 系列 — 委託面

### A1 — 墊單出現 (wall appearance)

**Tier A,action_type = `observation`,等級 Informational。**

**直覺**:有人在五檔某檔放異常大量委託,撐 / 壓股價的當下動作。**沒有 actionable 方向**(壓單可能是真的賣壓也可能是 spoofing 假象),所以是 observation 類訊號 — UI 不發桌面 notification,但 TriggerList 顯示(預設摺疊,user 點開看)。

**觸發邏輯**:每次 books 推送,比對每個檔位的 size 跟 rolling baseline:

```
size_now >= baseline_median × multiplier
且持續 N 秒以上
→ 觸發
```

**主要改動(vs v1)**:
- baseline 從 `算術平均` 改成 **median**(抗 fat tail 雜訊)
- baseline 樣本維護方式不變(heartbeat 每秒抽樣,300 樣本 deque,每檔位獨立)
- multiplier 依流動性 tier 不同:見下方 per-liquidity-tier 參數表

**參數**:

| 參數 | 設定 |
|---|---|
| 監控檔位 | 全五檔:bid1-bid5 + ask1-ask5 |
| size 閾值 | `size >= median(baseline_300s) × multiplier`(multiplier 見 per-liquidity-tier 表) |
| 持續時間 | `min_persist_seconds = 5` |
| 方向 | both(bid = 撐單 / ask = 壓單,分開 metadata) |
| 冷卻 | (symbol, 檔位, 方向)5 分鐘(強勢股 universe 拉到 10 分鐘) |
| Warm-up | 無 |

**Metadata fanout**:
```json
{
  "type": "wall_appearance",
  "side": "ask",
  "level": "ask2",
  "wall_price": 985.0,
  "wall_size": 350,
  "baseline_median_size": 28,
  "size_x_median": 12.5,
  "persisted_seconds": 5.2,
  "confidence_tier": "HIGH",
  "wash_active_at_trigger": false,
  "liquidity_tier": "mid"
}
```

**State 寫入**:觸發後寫一筆到 `_active_walls`:
```python
_active_walls[(symbol, level, side)] = {
    "wall_size": 350,
    "wall_price": 985.0,
    "appeared_at": 1747820000.0,
    "matched_trade_volume": 0,   # 後續累計
    "size_x_median_at_trigger": 12.5,
}
```

### A3 — 墊單被吃 (wall eaten)

**直覺**:A1 偵測到的牆,被主動單真的吃光,壓力 / 支撐被穿透。

**觸發邏輯**:`_active_walls` 內某筆 wall,後續書本推送中其 size 下降 ≥ 70%,**且**對應方向累計成交量 ≥ wall_size × 50%。

**方向判定**:
- ask 牆 → 累計 `tick.price >= tick.ask` 的成交量(外盤主動買)
- bid 牆 → 累計 `tick.price <= tick.bid` 的成交量(內盤主動賣)
- 累計視窗:牆消失前 30s 內

**A3 / A_pull 分岔規則**(同一筆 wall 消失時,改 Lens A 建議):

| 對應方向累計成交 / wall_size | 結果 |
|---|---|
| ≥ 50% | 走 A3 |
| **30% ~ 50%** | 灰色地帶,兩個都不發(v2 改 30,v1 是 20) |
| < 30% | 走 A_pull |

**參數**:

| 參數 | 設定 |
|---|---|
| size 下降閾值 | ≥ 70% |
| 成交對證 | 對應方向累計 ≥ wall_size × 50% |
| 牆有效期 | 出現後 5 分鐘內 |
| 冷卻 | (symbol, side) 3 分鐘 |

**Metadata fanout**:
```json
{
  "type": "wall_eaten",
  "side": "ask",
  "level": "ask2",
  "wall_price": 985.0,
  "original_wall_size": 350,
  "remaining_size": 80,
  "eaten_size": 270,
  "matched_trade_volume": 280,
  "elapsed_seconds": 47,
  "wall_appeared_at": "2026-05-20T13:21:05+08:00",
  "confidence_tier": "MEDIUM",
  "wash_active_at_trigger": true
}
```

**牆生命週期結束**:觸發後 `_active_walls` 該筆刪除,啟動 C2′ pending(見 C 系列)。

### A_pull — 抽單 (spoofing detection)

**三種 sub_type 不同 Tier / action_type:**

| sub_type | Tier | action_type | 等級 | 理由 |
|---|---|---|---|---|
| `fast_spoof`(< 1s 撤) | **Tier C(internal log only)** | — | — | 跟 algo cancel-replace 分不開,雜訊高。事後當 layering 計數 input |
| `slow_spoof`(≥ 10s 撤) | Tier A | `observation` | Medium | spoofing 確認,但 price 後續方向不確定 — 不該主進場 |
| `wall_layering`(5min 內 ≥3 次) | Tier A | `risk_warning` | Strong (Risk) | 整檔該 session 不可信 — 降該檔所有訊號 confidence 一級 |

**直覺**:A1 偵測到的牆消失,但沒有對應成交 → 主力撤單,可能是 spoofing。

```
牆消失時,根據存活時間分類:
  
  lived_seconds < 1.0     → fast_spoof  (撤太快 — Tier C internal log only)
  lived_seconds in [1.0, 10.0)
                          → 不發(過渡帶,可能是 algo cancel-and-replace routine)
  lived_seconds >= 10.0   → slow_spoof  (Tier A observation)

且兩個都需要:
  size_drop_pct >= 70%
  matched_trade_volume < wall_size × 30% (灰色地帶 30%-50% 不發)

Layering 升級(5 分鐘內偵測):
  同 symbol 5 分鐘內 fast + slow 加起來 >= 3 次
  → 升級成 "wall_layering" 訊號(Tier A risk_warning)
  → 觸發後 5 分鐘內,該 symbol 所有訊號 confidence 強制降一級
  → 標記 LAYERING_DETECTED flag
```

**參數**:

| 參數 | 設定 |
|---|---|
| size 下降閾值 | ≥ 70% |
| 成交對證上限 | < wall_size × 30% |
| fast_spoof 存活時間 | < 1.0s |
| slow_spoof 存活時間 | >= 10.0s |
| 過渡帶 | [1.0, 10.0) — 不發訊號 |
| layering 升級閾值 | 5 分鐘內 ≥ 3 次 spoof(fast + slow 加總) |
| 牆有效期 | 出現後 5 分鐘內 |
| 冷卻 | (symbol, side) 5 分鐘 |

**Metadata fanout**(fast / slow / layering 共用 type,sub_type 區分):
```json
{
  "type": "wall_pulled",
  "sub_type": "slow_spoof",        // 或 "fast_spoof" / "wall_layering"
  "side": "bid",
  "level": "bid1",
  "wall_price": 984.0,
  "original_wall_size": 500,
  "pulled_size": 450,
  "matched_trade_volume": 30,
  "lived_seconds": 47,
  "recent_spoof_count_5min": 2,
  "wall_appeared_at": "2026-05-20T13:21:05+08:00",
  "confidence_tier": "HIGH"
}
```

**牆生命週期結束**:觸發後 `_active_walls` 該筆刪除。A_pull **不啟動** C2′ pending(沒「被吃」事件可衍生突破)。

---

## B 系列 — 成交面

B 系列訊號(B1′ / B2 / B3)邏輯沿用 v1 設計,**主要改動是加 wash filter + confidence label**:

**所有 B 系列訊號 fanout 前**,先 check `wash_detector.is_active(symbol)`:
- 若 active → `confidence_tier = "LOW"`(強勢股場景下隱藏不顯示)
- 若 inactive → `confidence_tier = "HIGH"` 或 `"MEDIUM"`(看 metadata 偏離倍數)

### B1′ — 短時間多 tick 急動 (rapid tick move)

**Tier A,action_type = `intraday_observation`,等級 Informational。**

**直覺與限制**:現股 round-trip 成本 ≈ 0.38-0.59%(2.3-3.5 tick @ 300 元股),raw 3 tick + 4-6 秒反應滑價就吃光 → **現股 swing 用是負期望**。只在當沖場景且 user 能 1 秒內反應時有 edge。

UI 標「當沖」icon,**不發桌面 notification**。

沿用 v1 邏輯:過去 3 秒視窗內,price 淨移動 ≥ 3 tick。

**參數**:

| 參數 | 設定 |
|---|---|
| 視窗 | 3 秒 |
| tick 數 | 淨移動 ≥ 3 tick |
| 視窗起點 | 視窗內第一筆 tick.price |
| tick_size 算法 | 用 `start_price` 算 |
| 方向 | both(metadata 標 direction) |
| 冷卻 | (symbol, direction) 2 分鐘(**強勢股拉到 5 分鐘**) |
| Warm-up | 無 |

**Metadata fanout**(加 wash + confidence):
```json
{
  "type": "rapid_tick_move",
  "direction": "up",
  "ticks_moved": 4,
  "from_price": 985.0,
  "to_price": 989.0,
  "elapsed_seconds": 2.3,
  "tick_size": 1.0,
  "confidence_tier": "HIGH",
  "wash_active_at_trigger": false
}
```

### B2 — 連續單向掃單 (sweep)

沿用 v1 邏輯:過去 5 秒視窗,連續 ≥ 5 筆同方向(neutral 忽略),總量 ≥ 5× baseline。

**參數**:

| 參數 | 設定 |
|---|---|
| 視窗 | 5 秒 |
| 連續筆數 | ≥ 5 筆嚴格同向 |
| 連續性 | 反向插入 → 重置;neutral 插入 → 忽略 |
| 總量門檻 | ≥ 5× 過去 5 分鐘每秒平均成交量 |
| 方向 | both(buy_sweep / sell_sweep) |
| 冷卻 | (symbol, direction) 2 分鐘(**強勢股拉到 5 分鐘**) |
| Warm-up | 無 |

### B3 — 內外盤量失衡 (aggression imbalance)

沿用 v1 邏輯:過去 60 秒視窗,buy_vol / sell_vol ≥ 3,total_vol ≥ baseline × 30。

**參數**:

| 參數 | 設定 |
|---|---|
| 視窗 | 60 秒 |
| 比值門檻 | dominant / 另一側 ≥ 3.0 |
| 最小總量 | total_vol ≥ baseline_per_second × 30 |
| 方向 | both(metadata 標 dominant_side) |
| 冷卻 | (symbol, direction) 5 分鐘(**強勢股拉到 10 分鐘**) |
| Warm-up | 無 |

---

## Trade-Through — 穿價成交(新增 v2)

**直覺**:單筆成交直接吃穿整本五檔 size,等於物理穿透 → 真實大戶 conviction。wash 抗性極高(要事先安排對手方提供整本書 depth)。

**觸發邏輯**:

```python
# 每筆 trades tick 進來時:
book = latest_book_snapshot(symbol)

# 判方向:price 在 ask 側 = 主動買;在 bid 側 = 主動賣
if tick.price >= book.ask1.price:
    # 主動買,看 ask 側深度
    total_ask_depth = sum(level.size for level in book.asks[:5])
    if tick.size > total_ask_depth and book.ask1_after_trade.price > book.ask1.price:
        觸發("up_through")

elif tick.price <= book.bid1.price:
    # 主動賣,看 bid 側深度
    total_bid_depth = sum(level.size for level in book.bids[:5])
    if tick.size > total_bid_depth and book.bid1_after_trade.price < book.bid1.price:
        觸發("down_through")
```

**參數**:

| 參數 | 設定 |
|---|---|
| 觸發條件 | tick.size > sum(該方向五檔 size) 且該方向最佳價往該方向跳 ≥1 tick |
| 方向 | both(up_through / down_through) |
| 冷卻 | (symbol, direction) 3 分鐘 |
| Warm-up | 無 |

**Metadata fanout**:
```json
{
  "type": "trade_through",
  "direction": "up_through",
  "trade_price": 990.0,
  "trade_size": 1500,
  "consumed_ask_depth": 1200,    // 五檔總深度
  "new_ask1_price_after": 991.0,
  "ticks_jumped": 1,
  "confidence_tier": "HIGH",      // 物理事件,wash 抗性高
  "wash_active_at_trigger": false
}
```

**為何 Trade-Through 預設 HIGH confidence**:物理穿越需要實際把整本書吃光,主力 wash 偽造成本極高(要安排對手方提供 5 層 depth),所以即使 wash_active = true,Trade-Through 仍給 HIGH(不像 B 系列會被 wash filter 降級)。

---

## C 系列 — 牆破 + 突破 CDP/MA(state machine)

### State machine 全景

```
┌────────────────┐
│  A3 觸發        │ 牆被吃 → 啟動 pending
└───────┬────────┘
        ↓
  ┌─────────────────────┐
  │ pending 寫入         │ phase = "waiting_breakthrough"
  │ expires = +60s       │ ← v2 改:30s → 60s
  └─────────┬───────────┘
            │
   ┌────────┴────────┐
   │ price 穿 +3 tick │ → C2′ 觸發(Tier C, 只 log,不對外 alert)
   │ phase = "wait_  │
   │  strong_move"   │
   └────────┬────────┘
            │ ← 從這裡開始,任何階段都可走 C2.5′ 終止
            ↓
   ┌──────────────────────┐
   │ peak 達到 ≥5 tick     │ phase = "retest_eligible"
   └──────────┬───────────┘
              ↓
   ┌──────────────────────┐
   │ price 進入 +1~+3 tick │ → C3a′ 觸發(Tier C,只 log)
   │ phase = "approaching" │
   └──────────┬───────────┘
              ↓
   ┌──────────────────────┐
   │ price 達到 line value │ → C3b′ 觸發(Tier C,只 log)
   │ phase = "retested"    │
   └──────────┬───────────┘
              ↓
       ┌──────┴──────┐
       ↓             ↓
  ┌─────────┐   ┌──────────────────────────┐
  │ 彈離 +3  │   │ 反向 -3                   │
  │ → C3c′  │   │ → C2.5′(從 Tier A 發 alert)│
  │ (Tier C)│   │ (chain version, sub_type) │
  └─────────┘   └──────────────────────────┘
```

**核心改動(vs v1)**:
1. **C2′ / C3a′ / C3b′ / C3c′ 全降 Tier C**,只 log 不對外 alert(Lens A review 建議,因為 chain 越長越容易被主力刻意製造)
2. **C2.5′ 升 Tier A,但增加獨立判定路徑(不一定要走完 A3 → C2′ 鏈)**
3. **breakthrough_window 30 → 60 秒**(台股節奏)
4. **加 CDP confidence boost**:wall_price 對齊 CDP/MA 線時 confidence_tier = HIGH

### C2′ — 牆破突破(Tier C,只 log)

沿用 v1 邏輯,**主要改動**:`breakthrough_window_seconds = 60`(v1 是 30)。

**為何降 Tier C**:Lens A 指出 CDP / MA 是 retail public information,主力專門製造假突破騙進場。降 Tier C 表示「**記錄但不主動催使用者進場**」,避免被獵殺。後續量化 backtest 可用 C2′ + 各種 filter(wash / CDP alignment / liquidity)組合找 edge。

**參數**:

| 參數 | 設定 |
|---|---|
| 確認視窗 | 60 秒(從 A3 觸發起) |
| 穿越門檻 | ≥ 3 tick |
| wall ↔ line 距離 | ≤ 5 tick |
| 候選線 | cdp_ah, cdp_nh, cdp, cdp_nl, cdp_al, sma_5, sma_20(7 條) |
| 方向限定 | 跟 wall 方向一致 |
| 冷卻 | (symbol, broken_line, direction) 5 分鐘(只影響 log 重複寫入頻率) |

### C3a′ / C3b′ / C3c′ — Retest chain(Tier C,只 log)

沿用 v1 邏輯。Retest 視窗從 v1 的 3 min 拉到 v2 的 **3-5 分鐘**(具體看實證調整),peak unlock 仍是 5 tick。

### C2.5′ — 假突破(Tier A,獨立化 + chain 雙路徑)

**action_type = `exit_signal`,等級 Strong (chain) / Medium (independent)。**

**重要設計決定(Lens B review)**:**C2.5′ 不是反向進場訊號,是平倉訊號**。理由:
- 反向進場 r/r 比差(price 在 line 附近震盪,scratch out 機率高)
- 「主力故意製造假突破」場景 → C2.5′ 真實發生 → 但反向能走多遠不確定
- 真正有用的場景:**user 之前照 C2′ chain 進場已 hold position,C2.5′ 觸發時強制平倉認賠 / 鎖利**

UI 標「平倉」icon,**對已 hold 該方向 position 的 user 發送 notification**(若有部位追蹤),其他人只看 TriggerList。

**主要改動(vs v1)**:**獨立判定路徑** + **confidence_tier** 標記 + **action_type=exit_signal**。

**獨立判定路徑**(新):
```
不依賴 A3 + C2′ 完整 chain,直接觀察:

對每個 symbol 維護輕量 state:
  recent_short_breakthrough: dict[(symbol, line_name, direction), 
                                  {breakthrough_price, breakthrough_at, peak_ticks}]
  
當 tick 觸發以下事件:
  1. price 短時(過去 30s 內)穿越某條 CDP/MA 線 ≥ 2 tick  → 寫入 recent_short_breakthrough
  2. 後續 price 反向跌穿線 ≥ 2 tick  → 觸發 C2.5′ 獨立版

confidence_tier 計算:
  HIGH = 突破前 5 min 內偵測到 A_pull / WASH_PATTERN_ACTIVE(高機率刻意 trap)
  MEDIUM = 純技術假突破(可能 organic noise)
```

**Chain 路徑**(保留,當 sub_type):同 v1 設計,完整 A3 → C2′ → ... → C2.5′ chain 觸發時,sub_type = "chain"。

**參數**:

| 參數 | 設定 |
|---|---|
| 獨立版穿越門檻 | ≥ 2 tick(v1 是 3 tick,中小型股 2 tick 已顯著) |
| 獨立版反向跌穿 | ≥ 2 tick |
| 獨立版時間視窗 | 30 秒(短時穿越 + 反向) |
| Chain 版穿越 / 反向 | ≥ 3 tick(沿用 v1 chain 版) |
| 方向限定 | 必須是原突破方向的反向 |
| 冷卻 | (symbol, broken_line, direction) 5 分鐘 |

**Metadata fanout**:
```json
{
  "type": "line_breakthrough_failed",
  "sub_type": "independent",       // 或 "chain"
  "trap_type": "bull_trap",
  "broken_line": "cdp_ah",
  "broken_line_value": 988.0,
  "breakthrough_price": 990.0,
  "failure_price": 985.5,
  "failure_ticks_below_line": 2,
  "elapsed_seconds_after_breakthrough": 78,
  "confidence_tier": "HIGH",         // HIGH 表示突破前有 wash / A_pull
  "wash_active_at_trigger": true,
  "recent_apull_count_5min": 2
}
```

---

## Tier B — Meta Filter

### WashTradeDetector(新模組)

**直覺**:中小型股 wash trade 機率特別高,沒 wash detector 所有 B 系列 / A3 / C 系列訊號都不可信。

**位置**:`backend/services/wash_detector.py`

**偵測邏輯**:

```python
條件 1(單事件):過去 1 秒視窗內出現
  - 兩筆成交 size 相近(±10%)
  - 反向主動方向(一筆外盤 / 一筆內盤)
  - 價格相近(±1 tick)
  → 疑似 cross

條件 2(模式):過去 5 分鐘視窗內 ≥ 3 次條件 1
  → WASH_PATTERN_ACTIVE = True

WASH_PATTERN_ACTIVE 維持條件:
  - 條件 1 累計 ≥ 3 次 → True
  - 5 分鐘無新條件 1 → False(自然衰減)
```

**API**:
```python
class WashTradeDetector:
    def consume_trade(self, symbol: str, tick: Tick) -> None:
        """每筆 trade 餵入。"""
    
    def is_active(self, symbol: str) -> bool:
        """該 symbol 過去 5 min 是否 WASH_PATTERN_ACTIVE。"""
    
    def get_recent_count(self, symbol: str, window_seconds: int = 300) -> int:
        """供 metadata 帶 wash_event_count_5min。"""
```

**整合點**:
- `signal_engine` 收 trade tick 時,先 `wash_detector.consume_trade()`,再評估訊號
- 每個訊號 fanout 前查 `wash_detector.is_active(symbol)`,決定 confidence_tier
- 訊號 metadata 帶 `wash_active_at_trigger` + `wash_event_count_5min`

**設計考量 — 為何 ETF 套利不會被誤判**:

| | Wash trade | ETF 套利 |
|---|---|---|
| 目的 | 製造成交量 / 洗刷成本 | 跨商品價差套利 |
| 資金流 | 自我循環(A→B→A) | 真實資金流入流出 |
| 同一檔股票上行為 | 配對成交,反向 + 同 size | 單向買 OR 單向賣 |
| 跨商品依賴 | 無 | 跨 ETF + 成分股 |
| 偵測器反應 | 命中 | 不命中 |

ETF 套利者操作:在「成分股」買 N 檔 + 在「ETF」賣出 → 用成分股向投信申購 → 結算。對單一檔成分股(例如 2330)而言,套利者只是「一筆主動買」,**不會有對應的「同 size 同 price 主動賣」在 2330 上發生** → WashTradeDetector 條件 1 不會命中。

**已知限制**:
- 撮合不暴露 broker info,broker 內部 cross 偵測不到(技術限制)
- 主動造市商(MM)做市過程的自身對沖 — 短時間在同檔股票買賣可能被偵測;但 MM 性質本身接近 wash(目的是 spread profit 不是 directional),偵測到也算合理
- HFT micro arbitrage 在台股比例極低,即便發生會被偵測也合理
- 純 size + price + direction 模式比對,真實正常交易若巧合滿足條件可能誤判 → 用「過去 5 min ≥ 3 次」減少 false positive
- 兩個獨立投資者剛好對打的 case 會被誤判,但 size+price+1s+反向 4 個條件同時滿足機率極小

### MarketStats(沿用 v1 設計 + median)

**位置**:`backend/services/market_stats.py`

**主要改動(vs v1)**:baseline 算法從**算術平均改成 median**(抗 fat tail)。

**API**:
```python
class MarketStats:
    def sample_book_depth(self, symbol: str, level: str, size: int) -> None:
        """每秒 heartbeat 抽樣(在 fubon_ws 內接 books 推送時呼叫)。"""
    
    def sample_trade_size(self, symbol: str, size: int) -> None:
        """trades tick 進來時呼叫。"""
    
    def median_book_depth(self, symbol: str, level: str, window_seconds: int = 300) -> float:
        """A1 用 — 過去 N 秒的 median size。"""
    
    def median_trade_size_per_second(self, symbol: str, window_seconds: int = 300) -> float:
        """B2 / B3 用 — 過去 N 秒每秒平均成交量(median)。"""
    
    def liquidity_tier(self, symbol: str) -> Literal["high", "mid", "low"]:
        """根據過去 5 min total trade volume 動態分類 — 用於 per-liquidity-tier 參數查表。"""
```

**Tier 分類規則**:
- **high**: 過去 5 min 累計成交量 ≥ 5000 張(權值股 / 0050 / 強勢中型股拉抬中)
- **mid**: 1000 ~ 5000 張
- **low**: < 1000 張(冷門股,訊號可信度降)

### Per-Liquidity-Tier 參數表(新)

| 參數 | high tier | mid tier | low tier |
|---|---|---|---|
| **A1 size 倍數** | 5× median | 8× median | 12× median |
| **B1′ tick 數** | 3 tick | 3 tick | 4 tick |
| **B2 連續筆數** | 5 筆 | 5 筆 | 7 筆 |
| **B2 總量倍數** | 5× | 5× | 8× |
| **B3 ratio 門檻** | 3.0 | 3.0 | 4.0 |
| **B3 最小總量倍數** | 30× | 30× | 50× |

`low` tier 訊號預設 confidence_tier = LOW(無論 wash status),因為 baseline 統計樣本不足,雜訊高。

---

## Tier C — Internal State + Logging

### Logging Schema(兩表設計)

訊號 metadata 寫 `signals_log` JSONB(沿用),**ground truth 跟量化關鍵欄位寫新表 `signals_ground_truth`**(平面化,backtest 用)。

**為何分兩表**:JSONB 對量化 backtest 不友善 — `->>` 路徑慢、不能 index、code 醜。Ground truth 欄位是量化團隊主要 query 對象,平面化後 SQL 標準寫法 + 可 index。

**Table 1:`signals_log`(沿用,擴充 context_json)**:
```json
{
  "signal_type": "wall_pulled",
  "tier": "A",
  "sub_type": "slow_spoof",
  "triggered_at_ms": 1747820000000,

  "symbol": "2603",
  "metadata": { ... },                        // 該訊號專屬 fanout

  // 訊號觸發當下市場 snapshot(完整保留,給未來分析)
  "book_snapshot": {
      "bid1": {"price": 984.5, "size": 200},
      "bid2": ..., "bid5": ...,
      "ask1": ..., "ask5": ...
  },
  "trades_5min_summary": {
      "total_volume": 1500,
      "buy_volume": 900,
      "sell_volume": 600,
      "trade_count": 47,
      "vwap": 985.2
  },
  "cdp_ma_values": {
      "cdp_ah": 988.0, "cdp_nh": 986.0, ...,
      "sma_5": 985.6, "sma_20": 983.4
  },
  "wash_event_count_5min": 0,
  "recent_spoof_count_5min": 1,
  "layering_detected": false
}
```

**Table 2:`signals_ground_truth`(新)**:
```sql
CREATE TABLE signals_ground_truth (
    signal_id uuid PRIMARY KEY REFERENCES signals_log(id),
    symbol text NOT NULL,
    triggered_at_ms bigint NOT NULL,
    
    -- 訊號方向(MFE/MAE 算哪邊用)
    expected_direction text,           -- 'up' / 'down' / 'neutral'
    
    -- 時點 price(回填 job 寫入)
    price_at_5m numeric,
    price_at_30m numeric,
    price_at_60m numeric,
    price_at_eod numeric,
    
    -- 時點累計成交量
    volume_at_5m numeric,
    volume_at_30m numeric,
    volume_at_60m numeric,
    volume_at_eod numeric,
    
    -- 60min 區間 MFE / MAE
    max_favorable_excursion_ticks numeric,
    max_adverse_excursion_ticks numeric,
    
    -- 平面化的 critical metadata(量化 query 用)
    signal_type text,
    confidence_tier text,              -- HIGH / MEDIUM / LOW
    wash_active_at_trigger bool,
    liquidity_tier text,               -- high / mid / low
    
    -- 回填時間追蹤
    filled_at_5m timestamp,
    filled_at_30m timestamp,
    filled_at_60m timestamp,
    filled_at_eod timestamp,
    filled_at_mfe_mae timestamp
);

CREATE INDEX idx_sgt_symbol_time ON signals_ground_truth(symbol, triggered_at_ms);
CREATE INDEX idx_sgt_type_conf ON signals_ground_truth(signal_type, confidence_tier);
CREATE INDEX idx_sgt_pending ON signals_ground_truth(triggered_at_ms)
    WHERE filled_at_60m IS NULL;
```

**訊號 fanout 路徑改動**:
```
signal_engine 觸發訊號:
  1. 寫 signals_log row(既有路徑)
  2. 同步寫 signals_ground_truth row(新)— 時點 price 都 null
       expected_direction 從訊號 metadata 帶
       confidence_tier / wash_active_at_trigger / liquidity_tier 平面化過來
```

**量化 backtest query 範例**:
```sql
SELECT 
    symbol, expected_direction,
    max_favorable_excursion_ticks, max_adverse_excursion_ticks,
    price_at_60m, confidence_tier
FROM signals_ground_truth
WHERE signal_type = 'wall_pulled'
  AND confidence_tier = 'HIGH'
  AND wash_active_at_trigger = false
  AND triggered_at_ms > 1747820000000
  AND filled_at_60m IS NOT NULL
ORDER BY triggered_at_ms DESC;
```

**為何選 5m / 30m / 60m / eod 四個時點**:reviewer 建議 5m / 15m / 30m / 60m / eod,我簡化掉 15m(跟 30m 高度相關,可插值估算):
- 5m:超短期反應
- 30m:short-term continuation
- 60m:medium-term continuation
- eod:long-term

### Ground Truth 回填 Job(新模組)

**位置**:`backend/services/signal_ground_truth.py`

**邏輯**:
```python
class GroundTruthFillJob:
    """背景 cron 每 60 秒掃描需要回填的訊號,從 ring_buffer / OHLC 補 price_at_t_plus。"""
    
    async def run(self):
        while True:
            try:
                await self._fill_pending()
            except Exception as e:
                logger.error(f"GroundTruthFillJob: {e}")
            await asyncio.sleep(60)
    
    async def _fill_pending(self):
        sb = get_supabase()
        # 找出需要回填的 signals_log row
        # 條件:context_json.ground_truth_filled_at IS NULL
        #       AND context_json.triggered_at_ms 距現在已經超過該時點
        rows = await sb.client.table("signals_log").select("*").execute()
        
        for row in rows.data:
            ctx = row["context_json"]
            triggered_at = ctx["triggered_at_ms"]
            now = time.time() * 1000
            elapsed_min = (now - triggered_at) / 60_000
            
            updates = {}
            for time_point in [5, 30, 60]:
                key = f"{time_point}m"
                if ctx["price_at_t_plus"][key] is None and elapsed_min >= time_point:
                    price = await self._fetch_price_at(row["symbol"], triggered_at + time_point * 60_000)
                    updates[f"price_at_t_plus.{key}"] = price
            
            # eod 在收盤後 (13:30 後) 才寫
            if ctx["price_at_t_plus"]["eod"] is None and self._is_after_close():
                ...
            
            # 計算 MFE / MAE(只在 60m 都填完後,從 ring_buffer 算)
            if updates and ctx["max_favorable_excursion_ticks"] is None:
                mfe, mae = await self._compute_mfe_mae(row["symbol"], triggered_at, lookback_min=60)
                updates["max_favorable_excursion_ticks"] = mfe
                updates["max_adverse_excursion_ticks"] = mae
            
            if updates:
                # update supabase
                ...
```

**價格資料源**:
- 短期(5m / 30m / 60m):從 ring_buffer 取對應時點 tick
- eod:從 supabase OHLC 表查 close 價
- ring_buffer 過期後(預設 30 min),改從富邦 intraday/candles API 拉

**已知 caveat**:
- ring_buffer 預設保留 30 min,60m 時點若還沒寫入 ring_buffer 還在,但晚一點可能拿不到 → 用富邦 API fallback
- backend 重啟後 ring_buffer 重置,該段時間訊號可能無法回填 — 接受

---

## DynamicUniverse 服務(新)

### 直覺

訊號 universe 不只 user 自選 watchlist,還要**自動納入「強勢股」**(上市+上櫃,漲幅 ≥ 6% + 成交量 ≥ 3000 張)。強勢股是訊號最豐富、最有 edge 的場景。

### 資料源

富邦 HTTP API:`sdk.marketdata.rest_client.stock.snapshot.movers`

```python
res = await asyncio.to_thread(
    sdk.marketdata.rest_client.stock.snapshot.movers,
    market="TSE",         # 或 "OTC"
    direction="up",
    change="percent",
    gte=6.0,              # server-side filter 漲幅 >= 6%
    type="ALLBUT099",     # 排除權證 / 牛熊證,留股票 / ETF
)

# Response 結構:
# {
#   "date": "2026-05-20", "time": "13:21:00",
#   "data": [
#     {"symbol": "2603", "name": "長榮", ...,
#      "changePercent": 7.2, "tradeVolume": 4500000, ...},
#     ...
#   ]
# }

# Client filter: tradeVolume > 3_000_000(3000 張 × 1000 股/張)
```

### 位置

`backend/services/dynamic_universe.py`

### 核心邏輯

```python
class DynamicUniverse:
    REFRESH_INTERVAL_S = 60         # 每分鐘 refresh
    MIN_CHANGE_PCT = 6.0
    MAX_CHANGE_PCT = 9.5            # 排除漲停股(漲停時訊號失效)
    MIN_VOLUME_LOTS = 3000          # 張(等於 tradeVolume_股 >= 3_000_000)
    MAX_DYNAMIC_SYMBOLS = 50        # 強勢股清單上限(超過取 changePercent top N)
    REMOVAL_GRACE_S = 300           # 移出 grace period 5 分鐘
    
    def __init__(self):
        self._symbols: set[str] = set()
        self._pending_removal: dict[str, float] = {}
        self._task: asyncio.Task | None = None
    
    async def start(self):
        self._task = asyncio.create_task(self._refresh_loop())
        logger.info("DynamicUniverse started")
    
    async def shutdown(self):
        if self._task:
            self._task.cancel()
    
    def symbols(self) -> set[str]:
        return self._symbols.copy()
    
    def includes(self, symbol: str) -> bool:
        return symbol in self._symbols
    
    async def _refresh_loop(self):
        while True:
            try:
                await self._refresh()
            except Exception as e:
                logger.error(f"DynamicUniverse refresh failed: {e}")
            await asyncio.sleep(self.REFRESH_INTERVAL_S)
    
    async def _refresh(self):
        fubon = get_fubon()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            return
        
        new_symbols = set()
        for market in ["TSE", "OTC"]:
            try:
                res = await asyncio.to_thread(
                    fubon.sdk.marketdata.rest_client.stock.snapshot.movers,
                    market=market, direction="up", change="percent",
                    gte=self.MIN_CHANGE_PCT, type="ALLBUT099",
                )
                for row in res.get("data", []):
                    # 過濾條件:
                    # 1. tradeVolume(以股為單位)> 3000 張 * 1000 股
                    # 2. changePercent < 9.5%(排除漲停 + 漲停預兆 — 漲停股訊號失效)
                    if row.get("tradeVolume", 0) <= self.MIN_VOLUME_LOTS * 1000:
                        continue
                    if row.get("changePercent", 0) >= self.MAX_CHANGE_PCT:
                        continue
                    new_symbols.add(row["symbol"])
            except Exception as e:
                logger.warning(f"DynamicUniverse: movers {market} failed: {e}")
                # 部分失敗不重新整理 - 沿用上次結果
        
        added = new_symbols - self._symbols
        removed = self._symbols - new_symbols
        
        # Diff:加入立即 subscribe
        ws_pool = get_ws_pool()
        for sym in added:
            try:
                await ws_pool.subscribe(sym, owner_id="dynamic_universe")
                self._pending_removal.pop(sym, None)  # 取消 grace period 計時
            except Exception as e:
                logger.warning(f"DynamicUniverse subscribe {sym} failed: {e}")
        
        # 移出進 grace period
        now = time.time()
        for sym in removed:
            self._pending_removal[sym] = now + self.REMOVAL_GRACE_S
        
        # 過期 grace 真 unsubscribe
        for sym, expires_at in list(self._pending_removal.items()):
            if now >= expires_at:
                try:
                    await ws_pool.unsubscribe(sym, owner_id="dynamic_universe")
                except Exception as e:
                    logger.warning(f"DynamicUniverse unsubscribe {sym} failed: {e}")
                del self._pending_removal[sym]
        
        self._symbols = new_symbols
        if added or removed:
            logger.info(
                f"DynamicUniverse: +{len(added)} -{len(removed)} "
                f"(total={len(self._symbols)}, pending_removal={len(self._pending_removal)})"
            )
```

### `active_signals.scope` 擴充

```python
class ActiveScope(BaseModel):
    type: Literal["watchlist", "symbols", "dynamic_strong", "watchlist_or_dynamic"]
    symbols: list[str] = Field(default_factory=list)  # 只用於 type=symbols
```

`signal_engine._scope_includes()` 對新 type 的處理:
- `dynamic_strong` → `symbol in dynamic_universe.symbols()`
- `watchlist_or_dynamic` → 上面兩個聯集

預設新訊號的 scope 用 `watchlist_or_dynamic`(讓 user watchlist + 強勢股都涵蓋)。

### 強勢股 universe 跟 watchlist 並存

WS subscribe 用 refcount 機制(既有設計),同一檔股票同時在 watchlist 跟 dynamic_universe 時:
- `_refcount["2603"] = {"watchlist", "dynamic_universe"}`(2 個 owner)
- 任一 owner unsubscribe 不會真 unsubscribe(refcount > 0)
- 兩個 owner 都離開時才真正 unsubscribe

---

## 強勢股場景特別處理

`active_signals.scope.type = "dynamic_strong"`(或 `watchlist_or_dynamic` 中觸發的是 dynamic_strong symbol)的訊號,自動套用以下調整:

| 訊號 | 調整 |
|---|---|
| A 系列(A1 / A3 / A_pull) | 冷卻 5min → 10min |
| B 系列(B1′ / B2 / B3) | 冷卻 2-5min → 5-10min |
| C 系列 chain | 接近漲跌停(price 距 close_limit < 1.5%)自動 skip — CDP/MA 在漲跌停附近失效 |
| C2.5′ 獨立版 | 接近漲跌停 skip,因為這時 price 已被限制不可能再跌穿線 |
| 所有訊號 | confidence_tier = LOW 預設不顯示(避免疲勞) |

實作上由 `signal_engine` 內每筆訊號發送前 check:
```python
if active_signal.scope.includes_dynamic_strong():
    apply_strong_stock_adjustments(signal_meta)
```

---

## 邊界場景處理

### 開盤 baseline 不穩

全系列無 warm-up。Baseline median 樣本數不足時(開盤前幾秒)會出現:
- A1 的 5×~12× median 對小樣本容易過,但因為 size 絕對值也小,訊號意義本來就低
- Median 比 mean 對 fat tail 抗噪好,所以即使有 1-2 筆異常 spike 也不會嚴重污染

接受開盤前 ~30 秒可能有雜訊訊號,**靠 cooldown 限頻 + wash filter 過濾**。

### Tick 跨 tick_size 級距

`tick_size()` 一律用 `start_price` 算,避免跨級距誤判。

### Neutral 成交

- A3 / A_pull 對應方向累計成交:neutral 不計入
- B2 連續性計數:neutral 忽略不重置
- B3 比值計算:neutral 不算進任何邊
- WashTradeDetector 條件 1 中 neutral 不算反向

### books 推送頻率

baseline 用 heartbeat 每秒抽樣一次該檔位 size,維護 300 樣本 deque。不直接對 books 推送本身平均(高頻推送會污染)。

### 多條 CDP/MA 線同時被穿越

C2′ 觸發時取最近 wall_price 的線當主突破線。C2.5′ 獨立版同樣。

### Pending 過期 vs 失敗

`pending` 過期(60s 內沒突破 / 5 min 內沒回測)= **不發訊號**(突破成功但沒測試,不算成功也不算失敗)。  
`pending` 走到 C2.5′ chain 版 = 明確失敗,有 metadata。

### DynamicUniverse refresh 失敗

`refresh` 失敗時(富邦 API 短暫故障)沿用上次 symbols 不變,記 warning log。連續 5 次失敗(5 分鐘)→ alert。

### Ring_buffer 過期影響 ground truth

回填 60m 時若 ring_buffer 已過期(預設 30 min),改從富邦 `intraday/candles` API 拉。Backend 重啟時無法回填的訊號跳過,記 warning。

---

## v2 候選(列下游 follow-up)

| 訊號 / 機制 | 為什麼留 v2 |
|---|---|
| Quote-Trade Sequencing Anomaly | 富邦 books channel 1 秒延遲,粒度不夠細抓 microsecond 時序 |
| Per-Minute-of-Day Volume Anomaly | 需要存歷史 20 個交易日每分鐘 baseline,supabase schema 大量擴充 |
| Futures-Cash Basis Anomaly | 要把期貨(MXF/TXF)納入 watchlist scope,scope creep 太大 |
| Cancel Rate Spike | 富邦 SDK 沒給 cancel count,只能用 books 推送頻率 proxy(不準) |
| 上一交易日 baseline bootstrap | 用上日同檔位 baseline 解決開盤前 30s 雜訊問題 |
| A_pull → 反向急動 chain confirmation | spoofing 完整 pattern(撤單後 30s 內反向跌穿) |
| 多檔位 book congestion | 雙邊都有大牆訊號 |
| books 即時五檔前端顯示 | 加 quote book UI |
| 倍數 / 絕對閾值混合模式 | 看 v1 跑出來訊號分布再決定 |
| 訊號強度評分 | 同類訊號根據 size_x_median / ratio / peak_distance 打分 |

---

## 已決設計參數一覽

| 訊號 | Tier | action_type | 視窗 | 主閾值 | 副閾值 | 冷卻(普通 / 強勢股) |
|---|---|---|---|---|---|---|
| **Trade-Through** | A | primary_entry | 即時 | tick.size > 整本五檔 + 最佳價跳 ≥ 1 tick | — | 3min / 5min |
| **A3** | A | confirmation | 牆有效期 5 min | size_drop ≥ 70% | 對證 ≥ 50% | 3min / 5min |
| **B2** | A | primary_entry (need confirm) | 5 s | 連續 ≥ tier_count 筆 | 總量 ≥ tier_x × baseline | 2min / 5min |
| **B3** | A | medium_entry | 60 s | ratio ≥ tier_ratio | total ≥ baseline × tier_min_x | 5min / 10min |
| **A_pull layering** | A | risk_warning | 5 min | 5min 內 spoof 累計 ≥ 3 次 | — | 10min |
| A_pull slow | A | observation | 牆有效期 5 min | size_drop ≥ 70% | lived ≥ 10s, 對證 < 30% | 5min / 10min |
| A1 | A | observation | baseline 5 min | size ≥ tier_multiplier × median | 持續 5s | 5min / 10min |
| B1′ | A | intraday_observation | 3 s | 淨 ≥ tier_ticks tick | — | 2min / 5min |
| **C2.5′ chain** | A | exit_signal | 同 C2′ pending | 反向 ≥ 3 tick | C2′ 已發 | pending 內一次 |
| **C2.5′ independent** | A | exit_signal | 30 s | 短突破 ≥ 2 tick + 反向 ≥ 2 tick | 過去 5min wash/A_pull → HIGH | 5min |
| A_pull fast(internal) | C | — | 牆有效期 5 min | size_drop ≥ 70% | lived < 1s, 對證 < 30% | (log only) |
| C2′(internal) | C | — | 60s 從 A3 | 穿越 ≥ 3 tick | wall↔line ≤ 5 tick | 5min / per-line |
| C3a′ / C3b′ / C3c′(internal) | C | — | 3 min 從 C2′ | 同 v1 | peak ≥ 5 tick | pending 內一次 |

`tier_*` 參考 per-liquidity-tier 表(上方)。**所有 Tier A 訊號** fanout 前都過 5 層 dispatch pipeline(SafeMode / 訊號條件 / Priority / LiquidityGuard / Throttle)。

---

## Implementation Order(MVP-first + 風控優先,供 writing-plans 接手)

**設計原則(v3 更新)**:訊號架構 spec 內完整,**ship 順序風控優先 + 訊號極簡**。Lens B 強調訊號質量 ≠ 訊號可用性,沒有風控基礎建設,訊號再準也會被認知負擔 + 流動性陷阱 + 成本吃光。

### PR 1(MVP)— 基礎設施 + 2 個高 conviction 訊號 + 風控五層 pipeline

**目標**:**最少訊號驗證風控基礎建設**。先確認 Safe Mode / Throttle / Liquidity Guard / Priority / Action Type 五層 pipeline 能正常運作,訊號只挑 2 個 wash 抗性最強的當測試。

**範圍**:

**(A)基礎建設**:
- `MarketStats` 模組(median baseline)
- `WashTradeDetector` 模組
- `DynamicUniverse` 模組(含排除漲停 + MAX 50 上限)
- `ActiveScope.dynamic_strong` / `watchlist_or_dynamic`
- 富邦 books channel 訂閱(`fubon_ws.py` 改造)
- 兩表 logging schema:`signals_log` 擴充 + `signals_ground_truth` 新表
- `GroundTruthFillJob` 背景 cron

**(B)風控五層 pipeline(全新,v3 新增)**:
- `SafeMode` 模組(精簡 4 切換規則:9:00-9:05 / 13:20-13:30 / 大盤 ±5% / 量縮 <0.6×)
- `SignalThrottle` 模組(per-day quotas + per-symbol quotas)
- `LiquidityGuard` 模組(feasibility_score 計算)
- `SignalPriority` 模組(dedupe + cross-event suppression + 矛盾警告)
- `signal_engine.dispatch_pipeline()` 整合上面 4 個 + Action Type 標記

**(C)2 個訊號(極簡核心)**:
- **Trade-Through**(action_type = primary_entry,Strong,wash 抗性最高)
- **A_pull layering**(action_type = risk_warning,Strong (Risk),整檔風控訊號)

**(D)前端**:
- `TriggerList` 改造:action_type icon + confidence dot + feasibility 顏色
- `SafeModeBanner` 新元件
- `ThrottleStatusBar` 新元件
- `SignalChip` 改造
- `ActiveSignalEditor` 改造(雖然 v1 只 2 個訊號,但 UI 結構準備好)

**Critical Gate(PR 1 跑 1-2 週後驗收)**:
- Trade-Through fire 後 5 min 內延續 ≥ +3 tick 的比例 > 55%(reviewer 建議的 hit rate 門檻)
- 訊號疲勞測試:user 自評「看完 Tier A alert 處理花費時間」< 個人認知預算
- 流動性 guard `feasibility_score < 50` 的訊號比例 < 20%(代表訊號可執行性合理)
- SafeMode 切換正確(9:00-9:05 暫停 / 量縮日 degraded)
- Throttle 沒在正常市況觸發 quota(代表上限合理)
- WashTradeDetector 命中率(每天看到 wash 事件數量在合理範圍 0-10 次)
- A_pull layering 觸發後,對應該檔 confidence 強制降級確認生效

**PR 1 gate 過了才開 PR 2**。如果 Gate 過不去:
- Trade-Through hit rate 低 → 設計理念有誤,需要重新評估整套
- 訊號疲勞 → quota 還要更嚴
- feasibility_score 分布偏低 → LiquidityGuard 邏輯要調

### PR 2 — A 系列展開(A1 + A3 + A_pull slow + A_pull fast log)

**範圍**:
- A1 偵測邏輯 + `_active_walls` state machine(action_type = observation)
- A3 偵測邏輯(成交對證 + 灰色地帶分岔,action_type = confirmation)
- A_pull slow(action_type = observation)
- A_pull fast(Tier C internal log only)
- Per-liquidity-tier 參數查表 + confidence_tier 計算
- 訊號跑過完整 dispatch pipeline(SafeMode / Throttle / LiquidityGuard / Priority)
- 前端 row + 編輯 UI(A1 / A3 / A_pull slow)

**驗收**:
- A1 觸發頻率合理(per-liquidity-tier 各自每檔每天 0~3 次 with observation 摺疊)
- A3 / A_pull 灰色地帶分岔正確
- A_pull layering 觸發後 5 min 內,A1 / A3 / A_pull slow 對該檔自動降級 confidence
- Wash filter on 時 A 系列訊號正確標 LOW

### PR 3 — B 系列訊號(B1′ + B2 + B3)

**範圍**:
- B1′ 偵測邏輯(action_type = intraday_observation)
- B2 偵測邏輯(action_type = primary_entry need confirm)
- B3 偵測邏輯(action_type = medium_entry,可與 B2 互為 confirmation)
- B2 / B3 cross-confirmation 機制(同向 30s 內升級)
- Wash filter + confidence_tier 整合
- 強勢股場景冷卻調整
- 前端 row + 編輯 UI(B1′ / B2 / B3)

**驗收**:
- 強勢股實測,B1′ 觸發頻率不爆炸(observation 預設摺疊,user 不被淹)
- B2 + B3 同向 30s 內自動 promote 為 Strong
- WashTradeDetector active 時 B 系列訊號正確標 LOW
- Priority Layer 2 cross-event suppression 生效(Trade-Through 後 60s suppress B 系列)

### PR 4 — C 系列(Tier C internal + C2.5′ chain + C2.5′ independent exit)

**範圍**:
- C2′ / C3a′ / C3b′ / C3c′ 完整 state machine(internal,只寫 signals_log + signals_ground_truth tier=C)
- C2.5′ chain 路徑(action_type = exit_signal,Strong)
- C2.5′ independent 路徑(action_type = exit_signal,Medium)
- `_pending_breakthroughs` state
- 接近漲跌停 skip 邏輯
- 前端不暴露 C2′ / C3 系列編輯(只能 internal log)
- 前端 row(只有 C2.5′ chain + independent,標「平倉」icon)

**驗收**:
- 完整 chain 在強勢股實測能跑通(A3 → C2′ → C3a′ → C3b′ → 分岔)
- chain 完整資料寫進 signals_log + signals_ground_truth(tier=C)
- C2.5′ chain 路徑跟獨立版 sub_type 分得開
- 接近漲跌停的 case skip 處理正確
- C2.5′ 通過 dispatch pipeline(尤其 LiquidityGuard 因為平倉時要能出得掉)

### PR 5(可選)— UI polish + 矛盾警告 + edge case

- 矛盾訊號併現(`directional_uncertainty` meta alert)
- Position tracking 整合(C2.5′ 對已 hold position 發 notification)
- TriggerList action_type icon 設計優化
- 強勢股 vs watchlist 訊號的視覺區分
- 各 action_type 訊號 filter UI

---

## Open Questions(已 close;追蹤紀錄)

### Lens A → v2 close

| # | 問題 | 決定 |
|---|---|---|
| 1 | Tier C signals 也要在 frontend UI 編輯嗎? | **不要** — Tier C 純 internal log,使用者看不到也不能 enable/disable |
| 2 | 強勢股場景 Trade-Through / A_pull 冷卻拉長? | **要** — Trade-Through 3→5 min,A_pull 5→10 min |
| 3 | DynamicUniverse 強勢股清單上限 / 排除漲停? | **MAX_DYNAMIC_SYMBOLS=50**;**排除漲停**(`changePercent < 9.5`)|
| 4 | WashTradeDetector 對 ETF 套利怎麼處理? | **不需特別處理** — ETF 套利跨商品,單一檔不會自我配對成交,WashTradeDetector 不會誤判 |
| 5 | MFE / MAE 計算方向 — metadata 加 `expected_direction`? | **加** — 平面化進 `signals_ground_truth.expected_direction`,'up' / 'down' / 'neutral' |
| 6 | DynamicUniverse refresh 60s 夠? | **OK** — 60s 漏掉的訊號影響小 |

### Lens B → v3 close

| # | 問題 | 決定 |
|---|---|---|
| 7 | 訊號分動作類型嗎? | **必須** — 每訊號標 action_type,UI 區分對待 |
| 8 | 同事件多訊號齊發 dedupe / suppression? | **必須**(從 v2 follow-up 升級到 v1 必做)— SignalPriority 三層機制 |
| 9 | 流動性陷阱怎麼處理? | **必須**(LiquidityGuard 計算 `feasibility_score`,UI 顏色區分)|
| 10 | Safe Mode 怎麼設計? | **精簡 4 條規則**(集合競價 / 末段拉尾盤 / 大盤崩盤 / 量縮),其他留 v2 |
| 11 | 訊號每日 throttle? | **必須**(Strong 40 / Medium 80 / Informational 200 / per-symbol 8)|
| 12 | A1 / B1′ 砍掉 Tier C 嗎? | **不砍** — 保留 Tier A 但 action_type=observation(尊重 user 原始需求)|
| 13 | A_pull fast 砍嗎? | **降 Tier C internal log only**(1s 撤跟 algo cancel-replace 分不開)|
| 14 | C2.5′ independent 反向進場 vs 平倉? | **改 exit_signal**(只給已 hold 原突破方向 position 用,不該當反向進場)|
| 15 | PR 1 範圍? | **2 個訊號 + 5 個風控基礎建設**(風控優先於訊號數量)|

---

## 未決待後續 review(等專業交易人士意見)

- 各訊號實證閾值(目前 5×/median / 3 tick / 60s window 是設計值,實際跑出來可能要調)
- Safe Mode 在台股 normal market 切換頻率是否合適(4 條規則會不會太常觸發 / 太少觸發)
- Throttle quota 數字(40/80/200/8)是否符合 user 實際盤中查看頻率
- LiquidityGuard 4 個子分數(depth/spread/volume/limit_distance)權重是否需要調
- C 系列降 Tier C 後是否有真實的 edge(等資料累積)
- Position tracking 是否要整合(讓 C2.5′ 能對已 hold position 發 notification)
- 矛盾訊號 `directional_uncertainty` meta alert 的 UI 呈現方式

---

## 結語

**v3 修訂版設計**從原本 v1「11 個對外訊號」改成「**7 個進場/觀察類 Tier A + 2 個 exit-only Tier A + 5 個 internal + 1 個 meta filter + 1 個 dynamic universe**」結構,**並加上完整的 5 層 dispatch pipeline 風控基礎建設**。

### 核心設計理念

1. **訊號可用性 > 訊號數量**(Lens B):沒有風控基礎建設,訊號再準也會被認知負擔 + 流動性陷阱 + 成本吃光
2. **訊號分動作類型**(Lens B):primary_entry / confirmation / exit / observation / risk_warning,使用者看到訊號就知道該做什麼
3. **同事件 dedupe + cross-event suppression**(Lens B):避免一秒內 5 個 alert
4. **物理事件 > 統計訊號**(Lens A):Trade-Through 比 B 系列統計可信
5. **Wash 抗性 + 反主流視角**(Lens A):A_pull / Trade-Through / C2.5′ 是主力不會故意製造的訊號
6. **量化前置**:第一天就 log MFE/MAE,缺了補不回來

### v1 → v3 關鍵變化

1. **C 系列降 Tier C** — chain 越長 → 主力刻意製造的風險越大
2. **C2.5′ 升 Tier A + 改 exit_signal** — contrarian 訊號保留,但只給平倉用
3. **A1 / B1′ 改 observation** — 保留但不發 notification
4. **A_pull 拆 fast(Tier C) / slow(observation) / layering(risk_warning)** 三種
5. **Trade-Through 新增** — 物理事件
6. **WashTradeDetector 新增** — confidence filter
7. **DynamicUniverse 新增** — 強勢股自動納入,排除漲停
8. **5 層 dispatch pipeline**:SafeMode → 訊號內部 → Priority → LiquidityGuard → Throttle
9. **兩表 logging** — signals_log + signals_ground_truth + MFE/MAE 回填 job
10. **PR 1 = 2 訊號 + 5 風控模組** — 風控優先,訊號極簡

### 下一步

User 過 v3 spec → 給專業交易人士看 → 拿回意見繼續迭代,通過後進 writing-plans 寫實作計畫。
