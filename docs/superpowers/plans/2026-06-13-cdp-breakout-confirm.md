# CDP 突破確認(站穩)策略 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cdp_breakout_confirm` 策略 — 連續 N 根 1 分 K 收在 CDP 線之上/之下才推訊號，過濾假突破

**Architecture:** 在 `condition.py` 加 `BreakoutConfirmStrategy` model + StrategyConfig union 擴充；`signal_engine.py` 加 per-symbol 1 分鐘 candle 聚合 + breakout confirm evaluator + `_breakout_confirmed` set；`replay_engine.py` 加 `--preset breakout` 掃描矩陣。前端不動。

**Tech Stack:** Python 3.12, Pydantic v2, pytest + asyncio, 富邦 Neo SDK (replay 抓歷史 K 線)

**Spec:** `docs/superpowers/specs/2026-06-13-cdp-breakout-confirm-design.md`

---

### Task 1: Data Model — BreakoutConfirmStrategy + StrategyConfig 擴充

**Files:**
- Modify: `backend/models/condition.py:168-199`
- Test: `backend/tests/test_condition_breakout_confirm.py` (新建)

- [ ] **Step 1: 寫 model 驗證測試**

```python
# backend/tests/test_condition_breakout_confirm.py
"""驗 BreakoutConfirmStrategy schema 驗證 + ActiveFilter 整合。"""
import pytest
from models.condition import ActiveFilter, BreakoutConfirmStrategy


def test_valid_breakout_confirm_strategy():
    s = BreakoutConfirmStrategy(type="cdp_breakout_confirm")
    assert s.levels == ["ah", "nh", "nl", "al"]
    assert s.direction == "both"
    assert s.confirm_bars == 2
    assert s.margin_ticks == 0
    assert s.min_volume_ratio is None


def test_confirm_bars_range():
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=1)
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=10)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=0)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", confirm_bars=11)


def test_min_volume_ratio_range():
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=0.5)
    BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=20.0)
    with pytest.raises(Exception):
        BreakoutConfirmStrategy(type="cdp_breakout_confirm", min_volume_ratio=0.3)


def test_active_filter_with_breakout_confirm():
    f = ActiveFilter(strategy=BreakoutConfirmStrategy(type="cdp_breakout_confirm"))
    assert f.schema_version == 6
    assert f.strategy.type == "cdp_breakout_confirm"


def test_active_filter_discriminator_routes_correctly():
    """三種 strategy type 都能正確 parse。"""
    for stype in ("limit_up_open_touch", "breakout_retest", "cdp_breakout_confirm"):
        data = {"strategy": {"type": stype}}
        if stype == "breakout_retest":
            data["strategy"]["surge_pct"] = 3.0
        f = ActiveFilter(**data)
        assert f.strategy.type == stype
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_condition_breakout_confirm.py -v`
Expected: FAIL — `BreakoutConfirmStrategy` 尚未定義

- [ ] **Step 3: 實作 BreakoutConfirmStrategy + 擴充 StrategyConfig**

在 `backend/models/condition.py` 的 `BreakoutRetestStrategy` 之後、`StrategyConfig` 之前加：

```python
class BreakoutConfirmStrategy(BaseModel):
    """策略：連續 N 根 1 分 K 收在 CDP 線之上/之下 = 突破確認。"""

    type: Literal["cdp_breakout_confirm"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "nl", "al"], min_length=1,
    )
    direction: Literal["above", "below", "both"] = "both"
    confirm_bars: int = Field(default=2, ge=1, le=10)
    margin_ticks: int = Field(default=0, ge=0, le=5)
    min_volume_ratio: float | None = Field(default=None, ge=0.5, le=20.0)
```

修改 `StrategyConfig`：

```python
StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy | BreakoutConfirmStrategy,
    Field(discriminator="type"),
]
```

修改 `ActiveFilter.schema_version`：

```python
schema_version: int = 6  # 5→6,加 cdp_breakout_confirm strategy
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_condition_breakout_confirm.py -v`
Expected: 全 PASS

- [ ] **Step 5: 跑既有測試確認無回歸**

Run: `cd backend && .venv\Scripts\python -m pytest tests/ -v --timeout=30`
Expected: 全 PASS（schema_version 改不影響既有 filter_json 載入）

- [ ] **Step 6: Commit**

```
git add backend/models/condition.py backend/tests/test_condition_breakout_confirm.py
git commit -m "feat(model): add BreakoutConfirmStrategy + schema v6"
```

---

### Task 2: Candle 聚合 — `_update_candle` 方法

**Files:**
- Modify: `backend/services/signal_engine.py:1-15` (imports + dataclass)
- Modify: `backend/services/signal_engine.py:43-81` (`__init__`)
- Modify: `backend/services/signal_engine.py:471-486` (`_reset_daily_strategy_state`)
- Test: `backend/tests/test_candle_aggregation.py` (新建)

- [ ] **Step 1: 寫 candle 聚合測試**

```python
# backend/tests/test_candle_aggregation.py
"""驗 SignalEngine 的 per-symbol 1 分鐘 candle 聚合。"""
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

# 基準時間 09:01:00 UTC+8 的 epoch（任意工作日）
BASE = 1716166860.0  # 用跟其他測試一致的整數基準即可
MIN0 = BASE          # 分鐘 0
MIN1 = BASE + 60     # 分鐘 1
MIN2 = BASE + 120    # 分鐘 2


def test_first_tick_creates_candle_no_settlement():
    engine = SignalEngine()
    tick = Tick(price=100.0, size=10, time=MIN0)
    settled = engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    assert settled is None
    candle = engine._minute_candle["2330"]
    assert candle.open == 100.0
    assert candle.close == 100.0
    assert candle.volume == 10


def test_same_minute_updates_ohlcv():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    engine._update_candle("2330", Tick(102.0, 5, MIN0 + 10), MIN0 + 10, True)
    engine._update_candle("2330", Tick(98.0, 8, MIN0 + 20), MIN0 + 20, True)
    engine._update_candle("2330", Tick(101.0, 3, MIN0 + 30), MIN0 + 30, True)
    c = engine._minute_candle["2330"]
    assert c.open == 100.0
    assert c.high == 102.0
    assert c.low == 98.0
    assert c.close == 101.0
    assert c.volume == 26


def test_tick_driven_settlement_on_minute_cross():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    engine._update_candle("2330", Tick(105.0, 5, MIN0 + 30), MIN0 + 30, True)
    settled = engine._update_candle("2330", Tick(106.0, 7, MIN1), MIN1, True)
    assert settled is not None
    assert settled.close == 105.0
    assert settled.volume == 15
    new = engine._minute_candle["2330"]
    assert new.open == 106.0
    assert new.volume == 7


def test_heartbeat_refeed_no_volume_update():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    engine._update_candle("2330", tick, MIN0 + 1, is_new_tick=False)
    assert engine._minute_candle["2330"].volume == 10  # 不重複加


def test_heartbeat_settles_on_wall_clock_advance():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    engine._update_candle("2330", tick, MIN0, is_new_tick=True)
    settled = engine._update_candle("2330", tick, MIN1, is_new_tick=False)
    assert settled is not None
    assert settled.close == 100.0
    assert "2330" not in engine._minute_candle  # 刪除,等真 tick 建新 candle


def test_heartbeat_before_any_tick_no_candle():
    engine = SignalEngine()
    tick = Tick(100.0, 10, MIN0)
    settled = engine._update_candle("2330", tick, MIN0, is_new_tick=False)
    assert settled is None
    assert "2330" not in engine._minute_candle


def test_daily_reset_clears_candle():
    engine = SignalEngine()
    engine._update_candle("2330", Tick(100.0, 10, MIN0), MIN0, True)
    assert "2330" in engine._minute_candle
    engine._reset_daily_strategy_state()
    assert "2330" not in engine._minute_candle
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_candle_aggregation.py -v`
Expected: FAIL — `_update_candle` / `_minute_candle` / `MinuteCandle` 尚未定義

- [ ] **Step 3: 實作 MinuteCandle + `_update_candle` + `__init__` 擴充 + daily reset**

在 `signal_engine.py` 頂部 imports 下方（class SignalEngine 之前）加 dataclass：

```python
from dataclasses import dataclass

@dataclass
class MinuteCandle:
    minute: int
    open: float
    high: float
    low: float
    close: float
    volume: int
```

在 `SignalEngine.__init__` 末尾（`self._degraded = False` 之後）加：

```python
        self._minute_candle: dict[str, MinuteCandle] = {}
        self._breakout_confirm_count: dict[tuple[str, str, str], int] = {}
        self._breakout_confirmed: set[tuple[str, str, str]] = set()
```

新增 `_update_candle` 方法（放在 `_reset_daily_strategy_state` 之前）：

```python
    def _update_candle(
        self, symbol: str, tick: Tick, now: float, is_new_tick: bool,
    ) -> MinuteCandle | None:
        """維護 per-symbol 1 分鐘 candle;跨分鐘時回傳結算後的前一根。"""
        tick_minute = int(tick.time // 60)
        wall_minute = int(now // 60)
        candle = self._minute_candle.get(symbol)

        if candle is None:
            if not is_new_tick:
                return None
            self._minute_candle[symbol] = MinuteCandle(
                minute=tick_minute, open=tick.price, high=tick.price,
                low=tick.price, close=tick.price, volume=tick.size,
            )
            return None

        if is_new_tick and tick_minute > candle.minute:
            settled = candle
            self._minute_candle[symbol] = MinuteCandle(
                minute=tick_minute, open=tick.price, high=tick.price,
                low=tick.price, close=tick.price, volume=tick.size,
            )
            return settled

        if (not is_new_tick) and wall_minute > candle.minute:
            settled = candle
            del self._minute_candle[symbol]
            return settled

        if is_new_tick:
            candle.high = max(candle.high, tick.price)
            candle.low = min(candle.low, tick.price)
            candle.close = tick.price
            candle.volume += tick.size
        return None
```

在 `_reset_daily_strategy_state` 加三行清除：

```python
        self._minute_candle.clear()
        self._breakout_confirm_count.clear()
        self._breakout_confirmed.clear()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_candle_aggregation.py -v`
Expected: 全 PASS

- [ ] **Step 5: 跑既有測試確認無回歸**

Run: `cd backend && .venv\Scripts\python -m pytest tests/ -v --timeout=30`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```
git add backend/services/signal_engine.py backend/tests/test_candle_aggregation.py
git commit -m "feat(engine): add per-symbol 1-min candle aggregation"
```

---

### Task 3: Breakout Confirm Evaluator — `_eval_breakout_confirm` + `_candle_volume_ratio`

**Files:**
- Modify: `backend/services/signal_engine.py`
- Test: `backend/tests/test_signal_engine_breakout_confirm.py` (新建)

- [ ] **Step 1: 寫 evaluator 核心測試（狀態機 + 方向）**

```python
# backend/tests/test_signal_engine_breakout_confirm.py
"""驗策略：CDP 突破確認（站穩）。"""
from models.condition import ActiveFilter, ActiveSignalOut, BreakoutConfirmStrategy
from services.signal_engine import MinuteCandle, SignalEngine

NH = 100.0  # cdp_nh 測試值


def _active(confirm_bars=2, direction="above", margin_ticks=0, min_volume_ratio=None,
            levels=("nh",)):
    return ActiveSignalOut(
        id="bc", name="突破確認",
        filter_json=ActiveFilter(strategy=BreakoutConfirmStrategy(
            type="cdp_breakout_confirm", levels=list(levels),
            direction=direction, confirm_bars=confirm_bars,
            margin_ticks=margin_ticks, min_volume_ratio=min_volume_ratio,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-13",
        notify_discord=False,
    )


def _engine():
    e = SignalEngine()
    e._field_cache["2330"] = {"cdp_nh": NH, "cdp_nl": 95.0, "cdp_ah": 105.0}
    return e


def _candle(close, volume=100, minute=0):
    return MinuteCandle(minute=minute, open=close, high=close, low=close,
                        close=close, volume=volume)


def test_confirm_bars_2_fires_after_2_consecutive():
    engine = _engine()
    active = _active(confirm_bars=2, direction="above")
    strat = engine._strategy_of(active)
    r1 = engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    assert r1 is None  # 第 1 根,count=1,未達 2
    r2 = engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=1), 60)
    assert r2 is not None
    assert r2["level"] == "nh"
    assert r2["direction"] == "from_below"
    assert r2["role"] == "breakout"
    assert r2["confirm_bars"] == 2


def test_reset_count_on_close_below():
    engine = _engine()
    active = _active(confirm_bars=3, direction="above")
    strat = engine._strategy_of(active)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(99.0, minute=1), 60)  # 跌回
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=2), 120)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=3), 180)
    assert r is None  # 重新累計,count=2,未達 3


def test_direction_below():
    engine = _engine()
    active = _active(confirm_bars=2, direction="below", levels=("nl",))
    strat = engine._strategy_of(active)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(94.0, minute=0), 0)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(93.0, minute=1), 60)
    assert r is not None
    assert r["direction"] == "from_above"
    assert r["level"] == "nl"


def test_direction_both_tracks_separately():
    engine = _engine()
    active = _active(confirm_bars=2, direction="both", levels=("nh",))
    strat = engine._strategy_of(active)
    # above 方向:2 根 close > NH
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=1), 60)
    assert r is not None
    assert r["direction"] == "from_below"


def test_margin_ticks():
    engine = _engine()
    active = _active(confirm_bars=1, direction="above", margin_ticks=1)
    strat = engine._strategy_of(active)
    # NH=100, tick_size(100)=0.5, margin=0.5 → 需 close > 100.5
    r_exact = engine._eval_breakout_confirm(strat, active, "2330", _candle(100.5, minute=0), 0)
    assert r_exact is None  # 100.5 不 > 100.5
    # 重置 count(因為 close 不在正確側,count 歸零)
    r_above = engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=1), 60)
    assert r_above is not None


def test_confirmed_set_populated():
    engine = _engine()
    active = _active(confirm_bars=1, direction="above")
    strat = engine._strategy_of(active)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    assert ("2330", "nh", "above") in engine._breakout_confirmed


def test_count_resets_after_fire():
    engine = _engine()
    active = _active(confirm_bars=2, direction="above")
    strat = engine._strategy_of(active)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=1), 60)
    # count 歸零,下一根又要重新計
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(103.0, minute=2), 120)
    assert r is None  # count=1,未達 2


def test_multiple_levels_fire_independently():
    engine = _engine()
    active = _active(confirm_bars=1, direction="above", levels=("nh", "ah"))
    strat = engine._strategy_of(active)
    # close=106 同時 > NH(100) 和 AH(105)
    results = engine._eval_breakout_confirm(strat, active, "2330", _candle(106.0, minute=0), 0)
    # _eval_breakout_confirm 回第一個命中的 level（遍歷順序）
    assert results is not None


def test_daily_reset_clears_breakout_state():
    engine = _engine()
    active = _active(confirm_bars=2, direction="above")
    strat = engine._strategy_of(active)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    assert len(engine._breakout_confirm_count) > 0
    engine._breakout_confirmed.add(("2330", "nh", "above"))
    engine._reset_daily_strategy_state()
    assert len(engine._breakout_confirm_count) == 0
    assert len(engine._breakout_confirmed) == 0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py -v`
Expected: FAIL — `_eval_breakout_confirm` 尚未定義

- [ ] **Step 3: 實作 `_eval_breakout_confirm` + `_candle_volume_ratio`**

在 `signal_engine.py` 的 `_eval_breakout_retest` 方法之後加：

```python
    def _eval_breakout_confirm(
        self, strat: dict, active: ActiveSignalOut, symbol: str,
        candle: MinuteCandle, now: float,
    ) -> dict | None:
        """連續 N 根 1 分 K 收在 CDP 線正確側 → 回 cdp_touch dict。

        direction="both" 時 above/below 分開追蹤:per (rule, symbol, level, dir) 各一計數。
        """
        from services.cdp import tick_size

        cache = self._field_cache.get(symbol, {})
        field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp", "nl": "cdp_nl", "al": "cdp_al"}
        confirm_bars = strat["confirm_bars"]
        margin_ticks = strat.get("margin_ticks", 0)
        min_vr = strat.get("min_volume_ratio")
        directions = (["above", "below"] if strat["direction"] == "both"
                      else [strat["direction"]])

        result = None
        for level in strat["levels"]:
            v = cache.get(field_map.get(level, level))
            if v is None:
                continue
            margin = margin_ticks * tick_size(v)
            for d in directions:
                key = (active.id, symbol, f"{level}:{d}")
                on_correct_side = (candle.close > v + margin if d == "above"
                                   else candle.close < v - margin)
                if on_correct_side:
                    count = self._breakout_confirm_count.get(key, 0)
                    if count == 0 and min_vr is not None:
                        vr = self._candle_volume_ratio(symbol, candle, now)
                        if vr < min_vr:
                            continue
                    count += 1
                    self._breakout_confirm_count[key] = count
                    if count >= confirm_bars:
                        self._breakout_confirm_count[key] = 0
                        touch_dir = "from_below" if d == "above" else "from_above"
                        self._breakout_confirmed.add((symbol, level, d))
                        if result is None:
                            result = {
                                "level": level,
                                "direction": touch_dir,
                                "role": "breakout",
                                "confirm_bars": count,
                            }
                else:
                    self._breakout_confirm_count[(active.id, symbol, f"{level}:{d}")] = 0
        return result

    def _candle_volume_ratio(self, symbol: str, candle: MinuteCandle, now: float) -> float:
        """該 candle 的 volume / 當日每分鐘平均成交量。"""
        day_vol = self._day_volume.get(symbol, 0)
        if day_vol <= 0:
            return 0.0
        elapsed = self._minutes_since_open(now)
        if elapsed < 1:
            return 0.0
        avg_per_min = day_vol / elapsed
        return candle.volume / avg_per_min if avg_per_min > 0 else 0.0
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```
git add backend/services/signal_engine.py backend/tests/test_signal_engine_breakout_confirm.py
git commit -m "feat(engine): add breakout confirm evaluator + state machine"
```

---

### Task 4: 量能確認測試

**Files:**
- Modify: `backend/tests/test_signal_engine_breakout_confirm.py`

- [ ] **Step 1: 在 test_signal_engine_breakout_confirm.py 末尾加量能測試**

```python
def test_volume_ratio_blocks_first_bar():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    # 20 分後,avg=500/min,candle vol=800 → ratio=1.6 < 2.0
    now = 20 * 60.0
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(101.0, volume=800, minute=0), now)
    assert r is None


def test_volume_ratio_allows_first_bar():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    # 20 分後,avg=500/min,candle vol=1200 → ratio=2.4 ≥ 2.0
    now = 20 * 60.0
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(101.0, volume=1200, minute=0), now)
    assert r is not None


def test_volume_not_checked_on_subsequent_bars():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=2, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    now = 20 * 60.0
    # 首根:ratio OK
    engine._eval_breakout_confirm(strat, active, "2330",
                                   _candle(101.0, volume=1200, minute=0), now)
    # 第 2 根:volume 低(ratio=0.2),但不檢查 → 仍觸發
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(102.0, volume=100, minute=1), now + 60)
    assert r is not None


def test_volume_ratio_none_skips_check():
    engine = _engine()
    engine._day_volume["2330"] = 0  # 沒量也 OK — 不檢查
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=None)
    strat = engine._strategy_of(active)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    assert r is not None
```

- [ ] **Step 2: 跑測試確認通過**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py -v`
Expected: 全 PASS

- [ ] **Step 3: Commit**

```
git add backend/tests/test_signal_engine_breakout_confirm.py
git commit -m "test(engine): add volume ratio tests for breakout confirm"
```

---

### Task 5: 引擎整合 — `_evaluate` 接入 candle 聚合 + breakout confirm

**Files:**
- Modify: `backend/services/signal_engine.py:277-343` (`_evaluate`)
- Modify: `backend/services/signal_engine.py:353-363` (`_eval_strategy`)

- [ ] **Step 1: 寫整合測試（candle → 結算 → evaluator → fanout）**

在 `backend/tests/test_signal_engine_breakout_confirm.py` 末尾加：

```python
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 13, 9, 10, tzinfo=TZ).timestamp()  # 盤中


def test_evaluate_integration_fires_on_candle_settlement():
    """整合：tick 跨分鐘 → candle 結算 → evaluator → fanout。"""
    engine = _engine()
    active = _active(confirm_bars=1, direction="above")
    engine._active = [active]
    fired = []

    async def fake_broadcast(payload):
        fired.append(payload)

    min0 = MORNING
    min1 = MORNING + 60

    with patch("services.signal_engine.time.time", return_value=min0), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        # 分鐘 0:close > NH(101)
        asyncio.get_event_loop().run_until_complete(
            engine._evaluate("2330", Tick(101.0, 100, min0)))
        assert len(fired) == 0  # 還沒結算

        # 分鐘 1 的第一個 tick → 結算分鐘 0 → evaluator 觸發
        with patch("services.signal_engine.time.time", return_value=min1):
            asyncio.get_event_loop().run_until_complete(
                engine._evaluate("2330", Tick(102.0, 50, min1)))
        assert len(fired) == 1
        assert fired[0]["data"]["cdp_touch"]["role"] == "breakout"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py::test_evaluate_integration_fires_on_candle_settlement -v`
Expected: FAIL — `_evaluate` 還沒接入 candle 聚合

- [ ] **Step 3: 修改 `_evaluate` — candle 聚合移到 trading session gate 之前**

修改 `signal_engine.py` 的 `_evaluate` 方法。現有結構：

```python
async def _evaluate(self, symbol: str, tick: Tick) -> None:
    if not self._in_trading_session(time.time()):
        return
    now = time.time()
    ...
```

改為：

```python
async def _evaluate(self, symbol: str, tick: Tick) -> None:
    now = time.time()
    is_new_tick = tick is not self._prev_tick.get(symbol)
    settled = self._update_candle(symbol, tick, now, is_new_tick)

    if not self._in_trading_session(now):
        return

    if self._limit_up_active:
        self._update_limit_up_state(symbol, tick, now)
    if is_new_tick:
        self._day_volume[symbol] = self._day_volume.get(symbol, 0) + max(0, tick.size)

    prev = self._prev_tick.get(symbol)
    try:
        for active in self._active:
            if not self._scope_includes(active, symbol):
                continue

            strat = self._strategy_of(active)
            stype = strat.get("type") if strat else None

            if stype == "cdp_breakout_confirm":
                if settled is None:
                    continue
                cdp_touch = self._eval_breakout_confirm(strat, active, symbol, settled, now)
                ma_touch = None
                ok = cdp_touch is not None
            elif strat is not None:
                cdp_touch = self._eval_strategy(strat, active, symbol, tick, prev, now)
                ma_touch = None
                ok = cdp_touch is not None
            else:
                cdp_touch, ma_touch = self._eval_with_touch_meta(active, symbol, tick, prev)
                non_prox_ok = self._eval_non_proximity(active, symbol, tick)
                ok = self._combine_results(active, cdp_touch, ma_touch, non_prox_ok)
            if not ok:
                continue

            if strat is None:
                self._mark_touch_suppressed(active, symbol, cdp_touch, ma_touch)

            # breakout_confirm 用 per-level cooldown;其他 strategy 維持 per-symbol
            touch_level = (cdp_touch or {}).get("level", "") if (stype is None or stype == "cdp_breakout_confirm") else ""
            key = (active.id, symbol, touch_level)
            last_ts = self._cooldown.get(key, 0)
            if now - last_ts < active.cooldown_seconds:
                continue
            self._cooldown[key] = now

            today = date.today()
            if cdp_touch is not None:
                count_key = (symbol, cdp_touch["level"], today)
                self._cdp_touch_count[count_key] = self._cdp_touch_count.get(count_key, 0) + 1
                cdp_touch["touch_index"] = self._cdp_touch_count[count_key]
            if ma_touch is not None:
                count_key = (symbol, ma_touch["level"], today)
                self._ma_touch_count[count_key] = self._ma_touch_count.get(count_key, 0) + 1
                ma_touch["touch_index"] = self._ma_touch_count[count_key]

            await self._fanout(active, symbol, tick, cdp_touch, ma_touch)
    finally:
        self._prev_tick[symbol] = tick
```

注意改動重點：
1. `now = time.time()` 和 `is_new_tick` 移到 trading session gate 之前
2. `settled = self._update_candle(...)` 在 gate 之前（資料更新不受盤中閘門影響）
3. `_day_volume` 累積用 `is_new_tick` 而非重複寫 identity check
4. `stype` 提前定義（`strat.get("type") if strat else None`），三路分支清晰：`cdp_breakout_confirm` / 其他 strategy / proximity
5. `touch_level` 邏輯：`stype is None`（proximity）或 `cdp_breakout_confirm` → per-level；其他 strategy → per-symbol（`""`）

- [ ] **Step 4: 跑整合測試確認通過**

Run: `cd backend && .venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py -v`
Expected: 全 PASS

- [ ] **Step 5: 跑全部測試確認無回歸**

Run: `cd backend && .venv\Scripts\python -m pytest tests/ -v --timeout=30`
Expected: 全 PASS — 特別注意 `test_signal_engine_pre_open.py`（trading session gate 行為）

- [ ] **Step 6: Commit**

```
git add backend/services/signal_engine.py backend/tests/test_signal_engine_breakout_confirm.py
git commit -m "feat(engine): integrate candle aggregation + breakout confirm into _evaluate"
```

---

### Task 6: 回測 — replay_engine.py 加 `--preset breakout`

**Files:**
- Modify: `backend/scripts/replay_engine.py`

- [ ] **Step 1: 加 `breakout_rule` 工廠函式**

在 `replay_engine.py` 的 `touch_with_volume_rule` 函式之後加：

```python
def breakout_rule(confirm_bars: int, margin_ticks: int, day: str):
    """突破確認(站穩)規則 — breakout preset 用。"""
    from models.condition import ActiveFilter, ActiveSignalOut, BreakoutConfirmStrategy
    return ActiveSignalOut(
        id="replay", name=f"突破cb={confirm_bars}m={margin_ticks}",
        filter_json=ActiveFilter(strategy=BreakoutConfirmStrategy(
            type="cdp_breakout_confirm",
            levels=["ah", "nh", "nl", "al"],
            direction="both", confirm_bars=confirm_bars,
            margin_ticks=margin_ticks,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
        notify_discord=False,
    )
```

- [ ] **Step 2: 加 `run_breakout` 掃描函式**

在 `run_volume` 函式之後加：

```python
BREAKOUT_CONFIRM_BARS = [1, 2, 3, 5]
BREAKOUT_MARGINS = [0, 1, 2]
BREAKOUT_DETAIL_CB = 2
BREAKOUT_DETAIL_MG = 0


async def run_breakout(days, day_syms, daily, minute, rearm: int):
    """突破確認門檻掃描：confirm_bars × margin_ticks 矩陣 + 碰 CDP baseline。"""
    baseline = {}
    for day in days:
        baseline[day] = await replay_day(
            day, day_syms[day], daily, minute, touch_rule(rearm, day))

    detail_fired = {}

    for mg in BREAKOUT_MARGINS:
        cols = [f"cb={cb}" for cb in BREAKOUT_CONFIRM_BARS]
        print(f"\n== margin_ticks={mg} ==")
        print(f"{'day':<12}{'touch':>9}" + "".join(f"{c:>9}" for c in cols))
        totals = [0] * (1 + len(BREAKOUT_CONFIRM_BARS))
        for day in days:
            base_count = sum(baseline[day].values())
            row = [base_count]
            for cb in BREAKOUT_CONFIRM_BARS:
                f = await replay_day(day, day_syms[day], daily, minute,
                                     breakout_rule(cb, mg, day))
                row.append(sum(f.values()))
                if mg == BREAKOUT_DETAIL_MG and cb == BREAKOUT_DETAIL_CB and day == days[-1]:
                    detail_fired = f
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{day:<12}" + "".join(f"{c:>9}" for c in row))
        print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))

    last = days[-1]
    print(f"\n-- {last} per-symbol (cb={BREAKOUT_DETAIL_CB} margin={BREAKOUT_DETAIL_MG}) --")
    base = baseline[last]
    print(f"{'sym':<8}{'touch':>6}{'break':>6}")
    for s in sorted(day_syms[last]):
        print(f"{s:<8}{base.get(s, 0):>6}{detail_fired.get(s, 0):>6}")
```

- [ ] **Step 3: 在 `main` 加 breakout preset 路由**

修改 `argparse` 的 `choices`：

```python
ap.add_argument("--preset", choices=["touch", "crash", "volume", "breakout"], default="touch")
```

在 `main` 的 `if args.preset == "volume":` 區塊之後加：

```python
    if args.preset == "breakout":
        await run_breakout(days, day_syms, daily, minute, args.rearm)
        return
```

- [ ] **Step 4: 確認腳本可執行（dry run）**

Run: `cd backend && .venv\Scripts\python scripts/replay_engine.py --preset breakout --help`
（只驗 argparse 不報錯,不真正跑回測 — 那要登入富邦）

- [ ] **Step 5: Commit**

```
git add backend/scripts/replay_engine.py
git commit -m "feat(replay): add --preset breakout for breakout confirm threshold scan"
```

---

### Task 7: 回測 candle 聚合相容性 — replay_day 補 prev_close

**Files:**
- Modify: `backend/scripts/replay_engine.py:143-190` (`replay_day`)

- [ ] **Step 1: 確認回測 field_cache 有 prev_close**

`replay_day` 的 `engine._field_cache[sym]` 目前只設 `cdp_ah/nh/cdp/nl/al`。
`_candle_volume_ratio` 不需要 `prev_close`（用 `_day_volume` 和 `_minutes_since_open`），
但 breakout_confirm evaluator 的 `_candle_volume_ratio` 需要 `_minutes_since_open`，
而 `_minutes_since_open` 用 `now`（已 patch 的假時鐘），所以不需額外改動。

確認 `replay_day` 的時鐘 patch 涵蓋 `_update_candle` 中的 `now` 參數 — `_evaluate` 內
`now = time.time()` 已被 patch，所以 `_update_candle` 收到的 `now` 就是假時鐘值。OK。

唯一需要確認的是 `replay_day` 的 `ring_buffer` patch 也涵蓋 candle 路徑。
candle 不讀 ring_buffer（直接在 `_evaluate` 內處理），所以不需改動。

- [ ] **Step 2: 跑碰 CDP 回測驗證既有行為不變**

Run: `cd backend && .venv\Scripts\python scripts/replay_engine.py --preset touch`
（需盤後執行,確認輸出與既有 baseline 一致）

注意：此步驟需要登入富邦 API，只能在盤後執行。如果當下不方便，標記為 pending 跳過。

- [ ] **Step 3: Commit**（如有改動）

---

### Task 8: 跑回測 + 報告結果

**Files:** 無程式碼變更（純數據收集）

- [ ] **Step 1: 停 dev server**

確認 `uvicorn` 沒在跑（跟回測共用富邦 SDK 登入會衝突）。

- [ ] **Step 2: 跑 breakout 回測**

Run: `cd backend && .venv\Scripts\python scripts/replay_engine.py --preset breakout`

- [ ] **Step 3: 把回測結果貼給 user 分析**

輸出包含：
- per-day × (confirm_bars × margin_ticks) 訊號量表
- 碰 CDP baseline 對照
- 最後一日 per-symbol 明細

user 根據數據決定 `confirm_bars` / `margin_ticks` 最終值。

---

## 檔案總覽

| 動作 | 路徑 |
|------|------|
| 改 | `backend/models/condition.py` |
| 改 | `backend/services/signal_engine.py` |
| 改 | `backend/scripts/replay_engine.py` |
| 新 | `backend/tests/test_condition_breakout_confirm.py` |
| 新 | `backend/tests/test_candle_aggregation.py` |
| 新 | `backend/tests/test_signal_engine_breakout_confirm.py` |
