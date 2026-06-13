# CDP 突破確認(站穩)策略設計 — 2026-06-13

策略候選清單(`docs/notes/2026-06-12-strategy-candidates.md`)第 1 條。
現有碰線訊號只有「碰」— 價格觸碰 CDP 線就推。但「站上 NH」遠比「碰 NH 8 次」有用。
6/12 力成(6239)突破 NH 一路上 AH 就是典型案例。

## 結論先講

- **新 strategy type `cdp_breakout_confirm`**,獨立於碰線 evaluator
- **核心概念是「站穩」而非瞬間突破**:連續 N 根 1 分 K 收在 CDP 線之上/之下才推訊號,
  過濾假突破(碰線附近 1 根 K 棒收在上方立刻跌回的噪音)
- **需要 1 分鐘 K 棒聚合**:引擎目前逐 tick 評估,沒有 K 棒收盤概念。
  本案在 SignalEngine 內加 per-symbol 1 分鐘 candle 追蹤,分鐘邊界時結算
- **量能確認內建為選用參數**(`min_volume_ratio`),直接複用 `_eval_window` 的
  volume_ratio 邏輯,不改 strategy/window_conditions 組合架構
- **雙向**:`direction: "above" | "below" | "both"` — 站上(多頭突破)/ 跌破(空頭轉弱)
- **回測天然精確**:策略判定的是 1 分 K 收盤,replay_engine 的 1 分 K 轉 tick 後
  重建的 candle close = 原始收盤(非近似)

## 與現有/未來策略的關係

| 策略 | 關係 |
|------|------|
| 碰線(cdp_proximity) | 互補。碰 = 警示,站穩 = 確認。cooldown key 自然分開(獨立 strategy) |
| 突爆拉回踩(breakout_retest) | 語義不同。retest = 衝過去 → 回來;站穩 = 上去 → 不回來。兩者互斥(同一筆突破只會走其一) |
| 午後轉弱(策略 5,未做) | **正向依賴**。策略 5 前提「早盤曾站上 NH」可直接讀本策略的 confirmed 狀態,不用自己判。本案預留 `_breakout_confirmed` set 供讀取 |
| 其他(突爆殺、外盤比、分價量) | 無交集 |

## 策略邏輯

### 1 分鐘 K 棒聚合

在 `_evaluate` 內(per-rule 迴圈之前)維護 per-symbol 的 1 分鐘 candle。
candle 歸屬由 `tick.time` 決定(tick 屬於哪個分鐘);結算由 `tick.time` 或
wall-clock 任一跨越分鐘邊界時觸發。兩種結算情境行為不同:

```
_update_candle(symbol, tick, now, is_new_tick) -> MinuteCandle | None:

    tick_minute = int(tick.time // 60)
    wall_minute = int(now // 60)
    candle = _minute_candle.get(symbol)

    # ── 首筆:無 candle 記錄 ──
    if candle is None:
        if not is_new_tick:
            return None                    # heartbeat 在真 tick 之前,不建 candle
        _minute_candle[symbol] = MinuteCandle(
            minute=tick_minute, O=H=L=C=tick.price, vol=tick.size)
        return None                        # 無結算

    # ── 情境 A:tick-driven 結算(新 tick 屬於新的一分鐘)──
    if is_new_tick and tick_minute > candle.minute:
        settled = candle                   # 結算前一根
        _minute_candle[symbol] = MinuteCandle(
            minute=tick_minute, O=H=L=C=tick.price, vol=tick.size)
        return settled

    # ── 情境 B:heartbeat 結算(wall-clock 已過分鐘,但無新 tick)──
    if (not is_new_tick) and wall_minute > candle.minute:
        settled = candle                   # 結算前一根
        del _minute_candle[symbol]         # 刪除 — 等下一筆真 tick 建新 candle
        return settled                     # (不能用 stale tick 建新 candle)

    # ── 同分鐘 — 更新 OHLC(僅限真 tick)──
    if is_new_tick:
        candle.high = max(candle.high, tick.price)
        candle.low  = min(candle.low,  tick.price)
        candle.close = tick.price
        candle.volume += tick.size
    return None
```

`is_new_tick` 沿用 `_day_volume` 的 `tick is not self._prev_tick.get(symbol)` 守衛,
heartbeat re-feed 同一個 Tick 物件不重複計量、也不會建假 candle。
heartbeat 唯一的作用是在無新 tick 時觸發分鐘邊界結算(wall-clock 推進)。

### 狀態機

Per `(rule_id, symbol, level)` 維護一個計數器:

```
收到結算後的 candle:
  margin = margin_ticks × tick_size(level_value)   # 0 ticks = 嚴格 >/<

  direction = "above":  candle.close > level_value + margin
  direction = "below":  candle.close < level_value - margin
  direction = "both":   上述任一(above 和 below 分開追蹤,可同時觸發不同方向)

  if 方向正確:
      if count == 0 AND min_volume_ratio 有設:
          確認該 candle 的 volume_ratio ≥ 門檻
          不符 → count 維持 0(這根不是有效突破)
      count += 1
  else:
      count = 0                          # 跌回 → 重置

  if count >= confirm_bars:
      fire signal
      將 (symbol, level, actual_direction) 加入 _breakout_confirmed set
      # actual_direction = "above" 或 "below"(即使策略設 "both",set 存的是實際方向)
      count 歸零(靠 cooldown 擋重複觸發,非靠 count 維持)
```

```
  IDLE ──(candle close 在正確側 + 量能 OK)──→ PENDING(count=1)
    ↑                                              │
    │                                     (下根 candle close 仍正確)
    │                                              ↓
    └──(candle close 跌回)── PENDING(count=N) ──(count≥confirm_bars)──→ FIRE
```

### 量能確認

首根確認 K 棒(count 從 0 → 1)的量能必須達標(若有設 `min_volume_ratio`)。
後續確認 K 棒只需收盤在正確側,不要求量 — 符合「放量突破、縮量站穩」的交易邏輯。

volume_ratio 算法沿用 `_eval_window` 的 volume_ratio:
`candle.volume / (day_volume / elapsed_minutes)`。不呼叫 `_eval_window`(它讀
ring_buffer 窗口,粒度不是 candle),而是用同一公式直接算。

### 訊號輸出

fire 時產出 `cdp_touch` dict(複用現有 fanout):

```python
{
    "level": "nh",           # 被突破的 CDP 線
    "direction": "from_below",  # 站上(above) → from_below;跌破(below) → from_above
    "role": "breakout",      # 區別於碰線的 "support"/"resistance"
    "confirm_bars": 3,       # 實際連續幾根確認
}
```

Discord 推送沿用碰線圖卡格式,rule name 區分(如「CDP 突破確認」vs「碰 CDP」)。

## 參數

```python
class BreakoutConfirmStrategy(BaseModel):
    type: Literal["cdp_breakout_confirm"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "nl", "al"],
        min_length=1,
    )
    direction: Literal["above", "below", "both"] = "both"
    confirm_bars: int = Field(default=2, ge=1, le=10)
    margin_ticks: int = Field(default=0, ge=0, le=5)
    min_volume_ratio: float | None = Field(default=None, ge=0.5, le=20.0)
```

| 參數 | 預設 | 說明 |
|------|------|------|
| `levels` | ah, nh, nl, al | 要監看的 CDP 線。不含 cdp 中軸(突破中軸意義不大,可由 user 自選加回) |
| `direction` | both | above = 站上(多);below = 跌破(空);both = 雙向 |
| `confirm_bars` | 2 | 連續幾根 1 分 K 收在線的正確側才算確認。最終值由回測定 |
| `margin_ticks` | 0 | 收盤須超過線值幾個 tick 才算「在正確側」。0 = 嚴格 >/<。最終值由回測定 |
| `min_volume_ratio` | None(關閉) | 首根確認 K 棒的成交量 / 當日每分鐘均量 ≥ 此倍數。None = 不檢查量 |

## 實作範圍

```
backend/
├── models/
│   └── condition.py          [改] +BreakoutConfirmStrategy, StrategyConfig union 加入, schema_version 5→6
├── services/
│   └── signal_engine.py      [改] +candle 聚合 + breakout confirm evaluator + _breakout_confirmed set
└── scripts/
    └── replay_engine.py      [改] +breakout preset(門檻掃描)

frontend/  — 本案不動(先跑回測定參數;UI 策略卡後續再加)
```

### Data model(`models/condition.py`)

```python
class BreakoutConfirmStrategy(BaseModel):
    """策略:連續 N 根 1 分 K 收在 CDP 線之上/之下 = 突破確認。"""
    type: Literal["cdp_breakout_confirm"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "nl", "al"], min_length=1,
    )
    direction: Literal["above", "below", "both"] = "both"
    confirm_bars: int = Field(default=2, ge=1, le=10)
    margin_ticks: int = Field(default=0, ge=0, le=5)
    min_volume_ratio: float | None = Field(default=None, ge=0.5, le=20.0)

# StrategyConfig union 加入
StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy | BreakoutConfirmStrategy,
    Field(discriminator="type"),
]
```

`schema_version` 升至 6(新 strategy type,舊版 filter_json 無 `cdp_breakout_confirm`
不受影響 — discriminator 看 type 欄位,沒有就不 match)。

### SignalEngine(`backend/services/signal_engine.py`)

新增狀態:

```python
@dataclass
class MinuteCandle:
    minute: int      # epoch minute (tick.time // 60)
    open: float
    high: float
    low: float
    close: float
    volume: int

# per-symbol 當前正在聚合的 1 分 K
self._minute_candle: dict[str, MinuteCandle] = {}

# per (rule_id, symbol, level) 連續確認計數
self._breakout_confirm_count: dict[tuple[str, str, str], int] = {}

# 今日已確認的突破(供策略 5 讀取)
self._breakout_confirmed: set[tuple[str, str, str]] = set()  # (symbol, level, direction_str)
```

新增方法:

- `_update_candle(symbol, tick, now, is_new_tick) -> MinuteCandle | None`:
  更新 candle;跨分鐘時回傳結算後的前一根 candle
- `_eval_breakout_confirm(strat, active, symbol, settled_candle, now) -> dict | None`:
  對結算後的 candle 跑狀態機;回 cdp_touch dict 或 None
- `_candle_volume_ratio(symbol, candle, now) -> float`:
  算該 candle 的 volume_ratio(沿用 day_volume / elapsed_minutes 公式)

`_evaluate` 內流程(candle 聚合在 trading session gate **之前**,
跟 `_day_volume` 累積同層級 — 資料更新不受盤中/盤後閘門影響):

```python
# --- candle 聚合(trading session gate 之前)---
is_new_tick = tick is not self._prev_tick.get(symbol)
settled = self._update_candle(symbol, tick, now, is_new_tick)

# --- trading session gate(擋訊號觸發,不擋資料更新)---
if not self._in_trading_session(now):
    return

# --- per-rule 迴圈 ---
for active in self._active:
    strat = self._strategy_of(active)
    if strat and strat.get("type") == "cdp_breakout_confirm":
        if settled is None:
            continue    # 沒有結算的 candle → 這個 tick 不觸發 breakout confirm
        cdp_touch = self._eval_breakout_confirm(strat, active, symbol, settled, now)
        # ... cooldown → fanout(同既有 strategy 路徑)
    elif strat is not None:
        # 既有 strategy(limit_up_open_touch / breakout_retest)
        ...
    else:
        # 既有 proximity / window 路徑
        ...
```

`_eval_strategy` 路由加入 `cdp_breakout_confirm` 分支。

### 與既有機制的隔離

| 機制 | 碰線 | breakout_confirm |
|------|------|-----------------|
| re-arm | 有(離線 ≥ N ticks 再武裝) | **無**(不適用 — 連續確認本身即降噪) |
| cooldown | per (rule, symbol, level), 600s | **per (rule, symbol, level)**, 預設 1800s |
| touch_count | 有(今日第 N 次碰) | 有(沿用,但語義改為「第 N 次確認突破」) |
| direction | 每 tick 判 from_above/from_below | 由 candle close 位置判(不用 `_direction_of_touch`) |

**cooldown 粒度偏離 strategy 慣例**:re-arm spec 將 strategy 類 cooldown 還原為
per (rule, symbol)(level=""),漲停打開 / 爆拉回踩沿用此慣例。但 breakout_confirm
刻意改用 per-level:確認站上 NH 不該吞掉接著確認站上 AH 的訊號(兩條線代表不同
強度的突破)。實作時 `touch_level` 取 `cdp_touch["level"]`(有值,非空字串),
自然走 per-level 路徑,不需特殊分支。

## 回測設計(replay_engine.py)

新增 `--preset breakout` 掃描矩陣:

| 參數 | 掃描值 |
|------|--------|
| `confirm_bars` | 1, 2, 3, 5 |
| `margin_ticks` | 0, 1, 2 |
| `direction` | both |
| `levels` | ah, nh, nl, al |

量能門檻先不掃(純價格先看量,再疊量條件)。每組參數跑 5 日,輸出:
- per-day × 各參數組合訊號量表
- 最後一日 per-symbol 明細(含哪條線、第幾根確認)
- 對照:同日碰線(re-arm=5)的訊號量(已知 baseline)

### 回測精確度

replay 的 1 分 K → 4 tick 轉換:
- **candle close**:第 4 筆 tick = 原始 K 棒收盤 → **精確**
- **candle volume**:4 筆 tick 量加總 = 原始 K 棒量 → **精確**
- **volume_ratio**:day_volume(逐 tick 累積)= Σ 各 K 棒量 = 全日量 → **精確**

本策略是少數「回測結果 = 實盤結果」的設計,因為判定依據(K 棒收盤)就是回測資料本身,
不存在 tick 模擬偏差。唯一差異:live 中 candle 結算時機可能差數百 ms(下一筆 tick
才觸發結算),回測中精確在分鐘邊界。

## 測試

### 狀態機(`test_signal_engine_breakout_confirm.py`)

- 連續 N 根 candle close > NH → 觸發;N-1 根不觸發
- 中間插一根 close ≤ NH → 計數歸零,重新累計
- direction="above":close < NH 不觸發;direction="below":close > NL 不觸發
- margin_ticks=1:close 剛好在 NH 上 0 ticks(== NH)不觸發,≥ 1 tick 才觸發
- cooldown:同 rule 同 symbol 同 level 在 cooldown 內不重複觸發
- `_breakout_confirmed` set:觸發後 (symbol, level, direction) 加入;跨日清空

### 量能確認

- min_volume_ratio=2.0:首根確認 candle 的 volume_ratio < 2.0 → 不起算
- 首根 OK、後續 candle volume 低但 close 仍在正確側 → 仍計入確認(不檢查後續量)
- min_volume_ratio=None:不檢查量,純價格確認

### K 棒聚合(`test_candle_aggregation.py`)

- 同分鐘多筆 tick → O/H/L/C/V 正確
- 分鐘跨越 → 結算前一根,新 candle 用新 tick 起始
- heartbeat re-feed(同 tick 物件)→ 不重複加量、但可觸發 wall-clock 分鐘結算
- heartbeat 結算後 → 刪除 candle entry(不用 stale tick 建新 candle);下一筆真 tick 建新 candle
- 無 tick 的分鐘 → 無 candle → 不影響確認計數(不算確認、也不算打斷)

### 跨日

- `_reset_daily_strategy_state` 清 `_minute_candle` / `_breakout_confirm_count` / `_breakout_confirmed`

## 邊界與風險

- **冷門股延遲**:candle 結算靠下一筆 tick(或 heartbeat wall-clock)觸發。
  冷門股分鐘間隔大,突破訊號可能延遲數分鐘。但冷門股本身突破意義有限,接受。
  heartbeat 每秒跑一次,wall-clock 推進時可結算前一分鐘的 candle,延遲 ≤ 1 秒。
- **盤中重啟**:candle state 歸零,當根 K 棒資訊丟失。
  confirm_count 歸零,需要重新累計 N 根才觸發。與既有 `_day_volume` 重啟行為同性質。
- **收盤最後一根 K 棒**:13:25~13:30 的 candle 在 13:30 後無法結算 — `_evaluate`
  在 `_in_trading_session` 為 False 時整個 return,candle 結算在其後方。
  **決策:把 candle 結算移到 trading session gate 之前。** candle 聚合是資料層操作
  (跟 `_day_volume` 累積同性質),不產生訊號;產生訊號的是 breakout evaluator,
  仍在 gate 之後。gate 擋觸發不擋資料更新,13:30 後 heartbeat 結算最後一根
  candle,但不觸發任何訊號(per-rule 迴圈被 gate 擋下)。
- **prev_close 缺**:跟碰線一樣,field_cache 無 cdp 值 → 跳過該 symbol。
- **confirm_bars=1**:退化為單根 K 棒突破(允許,但回測預期噪音較高;使用者自行承擔)。

## 開放問題

1. **margin_ticks 預設值**:0(嚴格)vs 1(至少超過 1 tick)。回測後決定。
2. **confirm_bars 預設值**:2(快回應)vs 3(更穩)。回測後決定。
3. **與策略 5 的接口**:本案只預留 `_breakout_confirmed` set;策略 5 實作時再定
   是否需要更多 metadata(如 confirmed_at 時間、哪根 candle)。
4. **levels 是否含 cdp 中軸**:預設不含(突破中軸意義不大),但參數允許 user 加回。
5. **direction="both" 時多 level 同時觸發**:若 candle close 同時在 NH 和 AH 上方,
   兩個 level 各自獨立觸發(per-level cooldown 不互擋)。預期行為正確(站上 AH 比
   站上 NH 更強),但訊號量可能加倍。回測時觀察。

## 不做的事(YAGNI)

- 不動碰線 evaluator(cdp_proximity)— 兩者完全獨立
- 不動 breakout_retest — 語義不同,各自的狀態機互不影響
- 不抽 candle 聚合為獨立 service — 目前只有本策略用;策略 5/7 需要時再評估
- 不做前端 UI 策略卡 — 先跑回測定參數,確認有價值再做 UI
- 不做 ring_buffer 層的 candle — candle 是策略層概念,不污染低階 tick store
- 不做多時間框架(5 分 K、15 分 K)— 1 分 K 是最小粒度,夠用;更長時間框架
  可以用 confirm_bars 數量等效(3 根 1 分 K ≈ 觀察 3 分鐘站穩)

## 流程與驗收

1. 實作 candle 聚合 + strategy evaluator + model 擴充
2. 擴充 replay_engine.py → 跑 5 日回測(先停 dev server)
3. 回測數字出來 → user 定 confirm_bars / margin_ticks / min_volume_ratio 最終值
4. 測試補齊
5. PR 進 main
6. UI 策略卡(下一輪)
7. 盤中實測驗證
