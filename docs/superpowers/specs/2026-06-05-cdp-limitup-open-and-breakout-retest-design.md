# CDP 雙策略 — 漲停打開碰 CDP + 順勢爆拉突破回踩

**Date**: 2026-06-05
**Status**: Draft（brainstorm 定案 2026-06-05,待 writing-plans）
**Branch**: 建議 `feat/cdp-strategies`（從 main 開）

## Summary

在即時訊號引擎加兩個**預設策略**(preset,非自由組合的 DSL 積木),都建立在現有 CDP 五線
+ tick 流之上,只新增一個共用的「每檔當日狀態層」:

1. **漲停打開碰 CDP** — 個股盤中**鎖死漲停 ≥N 秒** → 打開往下 → 跌回**由上而下碰**所選 CDP 線 → 告警(測支撐找低接)。
2. **順勢爆拉突破回踩** — 個股**早盤**(開盤 ~W 分內)**突然爆拉**、向上突破某條 CDP 線 → **回踩同一條** → 告警(突破回踩確認,順勢做多)。

兩者最後都收斂成「**per-symbol 當日狀態 → 由上而下碰 CDP 線**」,差別只在前置狀態。碰線偵測、
方向(支撐/壓力)、當日觸碰計次、cooldown、fan-out(前端 WS + 歷史 + Discord)**全部沿用現有
`SignalEngine`**。

## Goals

- 共用一層 per-symbol 當日狀態(漲停 latch、突破 arming),放進 `SignalEngine`,跟既有
  `_day_volume` / 觸碰計次並列,**跨午夜沿用現有 daily refill reset**
- `compute` 出**漲停價** = 昨收 × 1.1 套台股 tick 進位(`prev_close` 已在 field_cache,零新資料源)
- `ActiveFilter` 加一個 discriminated-union 的 `strategy` 欄位;引擎在 `strategy` 存在時走專用
  evaluator,其餘條件忽略
- UI 出兩張預設策略卡,可開關 + 調參數 + 選 scope
- 告警走現有 `cdp_touch` metadata(哪條線、第幾次、role=support)

## Non-goals

- **不做**自由組合(approach B 的 DSL primitives)— 序列型條件塞進無狀態 per-tick DSL 是 YAGNI
- **不接**跨市場(大盤/台指期)資料 — 「強盤」一律解為個股自身動能
- **不下單** — 只發訊號(CLAUDE.md 約束),買賣由 user 決定
- **不改** CDP 五線計算、`daily_ohlc` 流程
- **不做**台指期版本 — 漲停是個股概念、CDP 期貨資料未實作(見
  `project_mxf_intraday_cdpcam_deferred`)
- **v1 不做策略 2「開高」分支** — 只做盤中極拉穿越;開盤跳空高開(首 tick 已在線上方)留 v2,
  屆時再加 session-open 追蹤

## Architecture

```
backend/
├── models/
│   └── condition.py          [改] +StrategyConfig union + ActiveFilter.strategy,schema_version 4→5
├── services/
│   ├── cdp.py                [改] +limit_up_price() 純函式(複用 round_to_tick_tw)
│   └── signal_engine.py      [改] +狀態層 + 兩個 strategy evaluator + _evaluate 路由
│   └── discord_notifier.py   [不動 / 微調] 沿用 cdp_touch 訊息;可加策略 badge(nice-to-have)
└── routes/
    └── active_signals.py     [不動] filter_json 走 ActiveFilter,新欄位自動 validate

frontend/src/
├── lib/
│   ├── api.ts                [改] +strategy filter type
│   └── signal-format.ts      [改] 格式化兩策略觸發訊息(複用 cdp_touch)
└── components/
    └── ActiveSignalEditor.tsx [改] +兩張預設策略卡 + 參數輸入
```

### Data model（`models/condition.py`）

```python
CdpLevel = Literal["ah", "nh", "cdp", "nl", "al"]

class LimitUpOpenTouchStrategy(BaseModel):
    type: Literal["limit_up_open_touch"]
    lock_seconds: int = Field(default=60, ge=5, le=600)        # 鎖死 ≥N 秒才算「曾漲停」
    levels: list[CdpLevel] = Field(default_factory=lambda: ["ah","nh","cdp","nl","al"], min_length=1)
    tolerance_ticks: int = Field(default=1, ge=0, le=10)

class BreakoutRetestStrategy(BaseModel):
    type: Literal["breakout_retest"]
    early_window_minutes: int = Field(default=10, ge=1, le=60)  # 開盤後 W 分內的突破才算
    surge_pct: float = Field(default=3.0, gt=0, le=20)          # 爆拉門檻 Y%
    surge_window_seconds: WindowSeconds = 60                    # 爆拉時窗 T(沿用既有 enum)
    retest_within_minutes: int = Field(default=10, ge=1, le=120)# 回踩時限 M（「馬上」）
    levels: list[CdpLevel] = Field(default_factory=lambda: ["ah","nh","cdp","nl","al"], min_length=1)
    tolerance_ticks: int = Field(default=1, ge=0, le=10)

StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy,
    Field(discriminator="type"),
]

class ActiveFilter(Filter):
    schema_version: int = 5   # 4→5,加 strategy
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None
    ma_proximity:  MAProximityCondition  | None = None
    strategy: StrategyConfig | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # strategy 存在 = 整條 filter 由 strategy 定義,其餘可空
        if self.strategy is not None:
            return self
        if (not self.conditions and not self.window_conditions
                and self.cdp_proximity is None and self.ma_proximity is None):
            raise ValueError("至少要有一個 condition / window / cdp_proximity / ma_proximity / strategy")
        return self
```

### 漲停價純函式（`services/cdp.py`）

```python
def limit_up_price(prev_close: float) -> float:
    """台股漲停價 = 昨收 × 1.1,尾數不足一個 tick 捨去(不超過 +10%)。

    tick 以漲停價當下價位的級距為準(round_to_tick_tw 內部用 tick_size(price))。
    對無漲跌停限制的標的(部分 ETF / 新股首 5 日),價格不會剛好盯在此值,
    策略 1 latch 自然不會誤觸 — 無需顯式排除。
    """
    return round_to_tick_tw(prev_close * 1.1, "down")
```

### 狀態層（`SignalEngine.__init__`，daily refill 時 reset）

```python
# 漲停:per-symbol(客觀事實,跨 rule 共用)
self._limit_at_since: dict[str, float] = {}     # 目前連續盯漲停價的起點 epoch(離開即清)
self._limit_lock_best: dict[str, float] = {}    # 今天達到過的最長連續鎖死秒數
# 突破 arming:per (rule_id, symbol)(門檻/線依 rule 參數而異,不可跨 rule 共用)
self._breakout_armed: dict[tuple[str, str], dict[str, float]] = {}  # → {level: armed_at}
```

`_refill_field_cache()` 末端(現有 `self._day_volume.clear()` 旁)一併 `clear()` 上述三個。

### 引擎路由（`_evaluate` 內,per active 迴圈）

```python
strat = active.filter_json.get("strategy") if isinstance(...) else active.filter_json.strategy
if strat is not None:
    cdp_touch = self._eval_strategy(active, strat, symbol, tick, prev, now)
    if cdp_touch is None:
        continue
    # 既有 cooldown → touch_count → _fanout 流程不變(cdp_touch 帶入)
    ...
    continue
# 否則走既有 _combine_results 路徑
```

## 策略 1：漲停打開碰 CDP

**狀態更新**(每 tick + 每秒 heartbeat;heartbeat 用 `ring_buffer.latest` 推進「鎖死計時」,
**不依賴鎖死期間是否有新成交**):

```
lp = limit_up_price(prev_close)            # prev_close 缺 → 跳過
if tick.price >= lp:                        # 盯在漲停價(price 不可能 > lp)
    _limit_at_since.setdefault(sym, now)
    _limit_lock_best[sym] = max(_limit_lock_best.get(sym,0), now - _limit_at_since[sym])
else:
    _limit_at_since.pop(sym, None)          # 離開漲停 → 清連續計時(best 保留 = 曾鎖過)
```

**觸發**(回 `cdp_touch` 才算 fire):

```
fire 條件:
  _limit_lock_best[sym] >= lock_seconds     # 今天曾鎖死 ≥N 秒
  且 tick.price < lp                        # 已打開
  且 tick 由上而下碰所選 levels 之一(±tolerance_ticks × tick_size)
→ cdp_touch = {level, direction:"from_above", role:"support", touch_index:…}
```

碰線/方向/計次複用現有 `_eval_cdp_proximity` + `_direction_of_touch` + `_cdp_touch_count`。

## 策略 2：順勢爆拉突破回踩

**早盤閘門**:`09:00 ≤ wall-clock < 09:00 + early_window_minutes`。

**arming**(僅早盤內):對所選 levels,偵測**由下而上穿越** line X 且當下「突然爆拉」→ arm X
(`_breakout_armed[(rule_id,sym)][X] = now`):

- **極拉(v1 唯一觸發)**:`_direction_of_touch(prev,tick,X)=="from_below"` 且
  `window price_change_pct(surge_window_seconds) ≥ surge_pct`

> v1 **不做開高**(開盤跳空已在 X 上方視為已突破)— 見 Non-goals / 後續。砍掉省去 session-open
> 追蹤,實作更乾淨。

**回踩觸發**:已 arm 的 line X,`now - armed_at ≤ retest_within_minutes`,且 tick **由上而下**
碰回 X(±tolerance）→ fire、disarm X。逾時(超過 M 分)→ disarm 不發。

```
→ cdp_touch = {level:X, direction:"from_above", role:"support", touch_index:…}
```

回踩**不限**早盤(突破在早盤、回踩「馬上」可略晚於 W);只受 `retest_within_minutes` 約束。

## Data 流

```
WS trades tick ─┬─→ ring_buffer
                └─→ SignalEngine.queue ─→ _evaluate
                       │  prev_close(來自 CdpService → field_cache)
                       ▼
              狀態層更新(漲停 latch / 突破 arming)
                       ▼
              strategy evaluator ─→ cdp_touch? ─→ cooldown ─→ _fanout
                                                              ├─ 前端 WS broadcast
                                                              ├─ signals_log 寫入
                                                              └─ Discord(per-rule 開關)
heartbeat(1s)─→ 同 _evaluate(用 latest tick)→ 推進鎖死計時 / 補回踩偵測
daily refill ─→ 清狀態層 + 觸碰計次 + day_volume
```

## Frontend UI

`ActiveSignalEditor` 加「預設策略」入口,兩張卡:

- **漲停打開碰 CDP**:鎖死秒數 N、tolerance、看哪些線(多選,預設全)、scope、cooldown、Discord 開關
- **順勢爆拉突破回踩**:早盤視窗 W、爆拉門檻 %、爆拉時窗(60/180/300…)、回踩時限 M、tolerance、
  看哪些線、scope、cooldown、Discord 開關

送出時組 `filter_json = {schema_version:5, strategy:{type:…, …}}`。其餘走現有 active_signals
建立 / 列表 / enable 流程。觸發訊息 `signal-format.ts` 複用現有 cdp_touch 格式(線名 + 方向 +
第幾次),rule name 區分兩策略。

## 參數預設

| 策略 1 | 預設 | 策略 2 | 預設 |
|---|---|---|---|
| `lock_seconds` | 60 | `early_window_minutes` | 10 |
| `tolerance_ticks` | 1 | `surge_pct` | 3.0 |
| `levels` | 全 5 | `surge_window_seconds` | 60 |
| | | `retest_within_minutes` | 10 |
| | | `tolerance_ticks` | 1 |
| | | `levels` | 全 5 |

## 錯誤處理 / 邊界

- **prev_close 缺**(停牌 / 新上市)→ 策略 1 跳過該 symbol(無漲停價可算)
- **無漲跌停限制標的**(部分 ETF / 新股首 5 日)→ 價格不會盯在 `prev_close×1.1`,latch 不誤觸
- **非正盤時段** → 沿用 `_in_trading_session` gate,試撮 / 盤後 stale tick 不評估、不更新狀態
- **backend 重啟**(盤中)→ 狀態層歸零;策略 1 的「曾鎖死」會漏判(重啟前的鎖死沒記到)、
  策略 2 的 arming 同樣丟失。與既有 `_day_volume` restart 重算同性質,接受(log 一行提示)
- **同 symbol 多條同類 rule** → 漲停 latch 客觀共用無妨;突破 arming 以 `(rule_id, symbol)` 隔離

## 測試（TDD，沿用 `test_signal_engine_*` 合成 tick 風格）

**純函式** `test_limit_up_price.py`:
- known-value:昨收 100 → 110.0(0.5 tick);昨收 10.05 → 11.05(0.05 tick,捨去不超 +10%)
- tick-level 跨界:昨收落在級距邊界時漲停價用**漲停價自身**級距

**狀態機** `test_signal_engine_limit_up_open.py`(測**為什麼**,非只 happy path):
- 鎖死**滿** N 秒 → 打開 → 碰線 **會**發
- 鎖死**不足** N 秒就打開(摸一下就掉)→ 之後碰線 **不**發(驗證「鎖死門檻」存在的理由)
- 打開後碰線方向必須 **from_above**;由下穿越(還在漲停上方波動)**不**發
- 跨午夜 reset:昨天鎖過今天不算
- cooldown:同 rule 同 symbol N 秒內只發一次

**狀態機** `test_signal_engine_breakout_retest.py`:
- 早盤極拉穿越 X + 回踩 X → 發;且回踩 metadata level==被突破的 X
- 爆拉幅度**不足** surge_pct(緩漲穿越)→ **不** arm → 回踩不發(驗證「突然」的理由)
- 突破在早盤**外**(W 分後才穿越)→ **不** arm
- 回踩**超過** M 分 → disarm,不發
- 多線爆拉穿越 → 全 arm → 回踩任一被突破線即發(設計選擇,見開放問題)

**前端** — 沿用既有手動驗收;`signal-format` 若加格式化加對應單元測試。

## 開放問題 / 實作時驗證

1. **富邦 `trades` 鎖漲停期間推送行為** — 設計用 heartbeat+latest 規避「鎖死無成交」;
   實作時實測一次(找一檔鎖漲停股或 mock latest 停在 lp)。
2. **漲停價 TWSE 進位邊界** — `round_to_tick_tw(prev_close*1.1,"down")` 是否完全符合官方
   「尾數捨去」與跨級距規則;查 TWSE 漲跌幅計算規則精確化(必要時補 edge case 測試)。
3. **開高分支** — ✅ **已定:v1 砍掉,只留極拉穿越**(user 2026-06-05 拍板)。v2 若要補開高,
   需顯式記 per-symbol session-open 價/時間(不能用 `prev is None` 判當日第一筆 —`_prev_tick`
   非盤中不更新、重啟殘留昨日 tick)。
4. **多線 arming** — 一次爆拉穿多條 → 全 arm、回踩任一即發(已與 user 確認方向,符合
   「回踩原來那條」)。
5. **早盤爆拉時窗吃到試撮** — `ring_buffer` 會累積 08:30–09:00 試撮 indicative tick;開盤頭
   `surge_window_seconds` 秒內的 `price_change_pct` 視窗會回看到試撮價,扭曲爆拉判定。此為
   既有 `window_conditions` 共有行為,非本策略新增,但策略 2 專打早盤、較敏感 — 實作時評估
   是否以 session-open 為視窗左界。

## References

- 既有 CDP 設計:`services/cdp.py`、`docs/superpowers/plans/2026-05-15-cdp-proximity.md`
- CDP 觸發方向 + 觸碰計次:`docs/superpowers/specs/2026-05-19-chart-and-signal-tweaks-design.md`
- 漲停旗標 forward(REST,本策略未用,改走價格法):同上 spec Item 3
- 引擎結構:`services/signal_engine.py`、`models/condition.py`
