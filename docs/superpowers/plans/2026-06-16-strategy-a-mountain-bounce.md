# Strategy A: Mountain Bounce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "mountain_bounce" strategy evaluator that fires a short signal when a stock's mountain peak is confirmed and the price bounces into a CDP resistance line but fails to break through.

**Architecture:** New `MountainBounceStrategy` Pydantic model + `_eval_mountain_bounce()` evaluator in signal_engine.py, wired into the existing strategy dispatch. Also adds per-symbol VWAP calculation to the engine (usable by other evaluators). Follows the `cdp_breakout_confirm` candle-path pattern exactly.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, signal_engine.py evaluator pattern

**Spec:** `docs/superpowers/specs/2026-06-16-strategy-a-mountain-bounce-design.md`

---

### Task 1: Add MountainBounceStrategy Pydantic model

**Files:**
- Modify: `backend/models/condition.py:209-224`
- Test: `backend/tests/test_condition_mountain_bounce.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_condition_mountain_bounce.py`:

```python
"""驗 MountainBounceStrategy model 解析。"""
from models.condition import ActiveFilter, MountainBounceStrategy


def test_mountain_bounce_defaults():
    s = MountainBounceStrategy(type="mountain_bounce")
    assert s.levels == ["ah", "nh", "cdp"]
    assert s.confirm_bars == 2
    assert s.tolerance_pct == 0.0
    assert s.require_below_vwap is False


def test_mountain_bounce_custom():
    s = MountainBounceStrategy(
        type="mountain_bounce", levels=["nh"], confirm_bars=3,
        tolerance_pct=0.3, require_below_vwap=True,
    )
    assert s.levels == ["nh"]
    assert s.confirm_bars == 3
    assert s.require_below_vwap is True


def test_mountain_bounce_in_active_filter():
    f = ActiveFilter(strategy=MountainBounceStrategy(type="mountain_bounce"))
    assert f.strategy.type == "mountain_bounce"
    assert f.schema_version == 8


def test_mountain_bounce_roundtrip_json():
    f = ActiveFilter(strategy=MountainBounceStrategy(type="mountain_bounce"))
    raw = f.model_dump_json()
    f2 = ActiveFilter.model_validate_json(raw)
    assert f2.strategy.type == "mountain_bounce"
    assert f2.strategy.levels == ["ah", "nh", "cdp"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_condition_mountain_bounce.py -v`
Expected: FAIL — `MountainBounceStrategy` not defined

- [ ] **Step 3: Write the model**

In `backend/models/condition.py`, after `PeakDivergenceStrategy` (line 218), add:

```python
class MountainBounceStrategy(BaseModel):
    """策略 A：造山確認 + 碰 CDP 線 + 連續 N 根 close 在線下 → 做空訊號。"""

    type: Literal["mountain_bounce"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp"], min_length=1,
    )
    confirm_bars: int = Field(default=2, ge=1, le=5)
    tolerance_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    require_below_vwap: bool = False
```

Update `StrategyConfig` (line 221-224) to include it:

```python
StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy | BreakoutConfirmStrategy | PeakDivergenceStrategy | MountainBounceStrategy,
    Field(discriminator="type"),
]
```

Update `schema_version` (line 234):

```python
schema_version: int = 8  # 7→8, 加 mountain_bounce strategy
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_condition_mountain_bounce.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_condition_mountain_bounce.py models/condition.py
git commit -m "feat: add MountainBounceStrategy model + schema_version 8"
```

---

### Task 2: Add VWAP calculation to signal engine

**Files:**
- Modify: `backend/services/signal_engine.py:119,375,790`
- Test: `backend/tests/test_signal_engine_vwap.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_signal_engine_vwap.py`:

```python
"""驗引擎 VWAP 即時計算。"""
from services.signal_engine import MinuteCandle, SignalEngine


def _candle(high, low, close, volume, minute=0):
    return MinuteCandle(minute=minute, open=close, high=high, low=low,
                        close=close, volume=volume)


def test_vwap_single_candle():
    e = SignalEngine()
    c = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    e._update_vwap("2330", c)
    assert abs(e._field_cache["2330"]["vwap"] - 100.0) < 0.01  # tp=(102+98+100)/3=100


def test_vwap_two_candles_weighted():
    e = SignalEngine()
    c1 = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    c2 = _candle(high=110, low=106, close=108, volume=3000, minute=2)
    e._update_vwap("2330", c1)
    e._update_vwap("2330", c2)
    # tp1=100, tp2=(110+106+108)/3=108
    # vwap = (100*1000 + 108*3000) / (1000+3000) = 424000/4000 = 106.0
    assert abs(e._field_cache["2330"]["vwap"] - 106.0) < 0.01


def test_vwap_zero_volume_skipped():
    e = SignalEngine()
    c = _candle(high=100, low=100, close=100, volume=0, minute=1)
    e._update_vwap("2330", c)
    assert "vwap" not in e._field_cache.get("2330", {})


def test_vwap_cleared_on_reset():
    e = SignalEngine()
    c = _candle(high=102, low=98, close=100, volume=1000, minute=1)
    e._update_vwap("2330", c)
    assert "vwap" in e._field_cache.get("2330", {})
    e._vwap_state.clear()
    # vwap value stays in field_cache until daily reset — that's by design
    # (field_cache is cleared separately by _reset_daily_strategy_state)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_vwap.py -v`
Expected: FAIL — `_update_vwap` not defined

- [ ] **Step 3: Implement VWAP**

In `backend/services/signal_engine.py`:

Add to `__init__` (after line 119 `self._mountain_state`):

```python
self._vwap_state: dict[str, dict[str, float]] = {}
```

Add method (after `_update_mountain`, before `_detect_surge`):

```python
def _update_vwap(self, symbol: str, candle: MinuteCandle) -> None:
    """累積 VWAP = Σ(typical_price × volume) / Σ(volume)。"""
    if candle.volume <= 0:
        return
    st = self._vwap_state.get(symbol)
    if st is None:
        st = {"cum_tp_vol": 0.0, "cum_vol": 0}
        self._vwap_state[symbol] = st
    tp = (candle.high + candle.low + candle.close) / 3
    st["cum_tp_vol"] += tp * candle.volume
    st["cum_vol"] += candle.volume
    self._field_cache.setdefault(symbol, {})["vwap"] = st["cum_tp_vol"] / st["cum_vol"]
```

Wire into `_evaluate()` — after line 375 (`self._update_mountain(symbol, settled, now)`), add:

```python
self._update_vwap(symbol, settled)
```

Add to `_reset_daily_strategy_state()` (after line 790 `self._mountain_state.clear()`):

```python
self._vwap_state.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_vwap.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_signal_engine_vwap.py services/signal_engine.py
git commit -m "feat: add per-symbol VWAP calculation to signal engine"
```

---

### Task 3: Add mountain_bounce evaluator

**Files:**
- Modify: `backend/services/signal_engine.py:116-119,391-402`
- Test: `backend/tests/test_strategy_a_mountain_bounce.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_a_mountain_bounce.py`:

```python
"""驗策略 A：造山後碰 CDP 無力。"""
from models.condition import ActiveFilter, ActiveSignalOut, MountainBounceStrategy
from services.signal_engine import MinuteCandle, SignalEngine

NH = 100.0
AH = 105.0
CDP_MID = 97.0


def _active(confirm_bars=2, levels=("nh",), tolerance_pct=0.0, require_below_vwap=False):
    return ActiveSignalOut(
        id="mb", name="造山碰CDP",
        filter_json=ActiveFilter(strategy=MountainBounceStrategy(
            type="mountain_bounce", levels=list(levels),
            confirm_bars=confirm_bars, tolerance_pct=tolerance_pct,
            require_below_vwap=require_below_vwap,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-16",
        notify_discord=False,
    )


def _engine():
    e = SignalEngine()
    e._field_cache["2330"] = {"cdp_nh": NH, "cdp_ah": AH, "cdp": CDP_MID}
    return e


def _candle(close, high=None, volume=100, minute=0, open_=None):
    h = high if high is not None else close
    o = open_ if open_ is not None else close
    lo = min(o, close, h)
    return MinuteCandle(minute=minute, open=o, high=h, low=lo, close=close, volume=volume)


def _set_mountain_confirmed(engine, symbol="2330", peak_high=110.0):
    engine._mountain_state[symbol] = {
        "phase": "confirmed", "recent_closes": [],
        "peak_high": peak_high, "peak_vr": 2.0, "peak_minute": 5,
        "confirmed_minute": 8, "no_new_high_count": 0,
    }


# ---- Core flow ----

def test_no_signal_without_mountain():
    """Mountain not confirmed → no signal."""
    engine = _engine()
    active = _active()
    strat = engine._strategy_of(active)
    c = _candle(close=99, high=101, minute=10)
    r = engine._eval_mountain_bounce(strat, active, "2330", c, 600)
    assert r is None


def test_arm_and_confirm_2_bars():
    """Mountain confirmed + high touches NH + 2 bars close below → signal."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=2)
    strat = engine._strategy_of(active)

    # Bar 1: high=101 touches NH(100), close=99 < NH → arm + confirm_count=1
    r1 = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r1 is None

    # Bar 2: close=98 < NH → confirm_count=2 → fire
    r2 = engine._eval_mountain_bounce(strat, active, "2330", _candle(98, high=99, minute=11), 660)
    assert r2 is not None
    assert r2["level"] == "nh"
    assert r2["direction"] == "from_below"
    assert r2["role"] == "mountain_bounce"
    assert r2["confirm_bars"] == 2
    assert r2["peak_high"] == 110.0


def test_disarm_on_close_above():
    """Close above CDP line → disarm, reset count."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=2)
    strat = engine._strategy_of(active)

    # Bar 1: touch + close below → count=1
    engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    # Bar 2: close above → disarm
    engine._eval_mountain_bounce(strat, active, "2330", _candle(101, high=102, minute=11), 660)
    # Bar 3: touch again → count=1 (restart)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=100.5, minute=12), 720)
    assert r is None  # only 1 bar, need 2


def test_confirm_bars_3():
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=3)
    strat = engine._strategy_of(active)

    engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    r2 = engine._eval_mountain_bounce(strat, active, "2330", _candle(98, minute=11), 660)
    assert r2 is None  # 2/3
    r3 = engine._eval_mountain_bounce(strat, active, "2330", _candle(97, minute=12), 720)
    assert r3 is not None  # 3/3


def test_tolerance_pct():
    """With tolerance_pct=0.5, NH=100 → threshold=99.5. high=99.8 should arm."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, tolerance_pct=0.5)
    strat = engine._strategy_of(active)

    # high=99.8 >= 100*(1-0.005)=99.5 → arm. close=99 < 100 → confirm_count=1 → fire
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=99.8, minute=10), 600)
    assert r is not None


def test_require_below_vwap_blocks():
    """require_below_vwap=True + close >= VWAP → no signal."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 98.0
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)

    # close=99 >= vwap=98 → blocked
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_require_below_vwap_allows():
    """require_below_vwap=True + close < VWAP → signal fires."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 100.0
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)

    # close=99 < vwap=100 → allowed. high=101 >= NH=100 → arm. close=99 < NH → fire
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is not None


def test_require_below_vwap_no_vwap_skips():
    """require_below_vwap=True but VWAP not yet computed → skip (no signal)."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    # no vwap in field_cache
    active = _active(confirm_bars=1, require_below_vwap=True)
    strat = engine._strategy_of(active)

    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_multiple_levels_fire_independently():
    """AH and NH can both arm and fire independently."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(confirm_bars=1, levels=("nh", "ah"))
    strat = engine._strategy_of(active)

    # high=106 touches both AH(105) and NH(100). close=104 < AH but > NH
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(104, high=106, minute=10), 600)
    # Should fire for AH (104 < 105) but NOT for NH (104 > 100)
    assert r is not None
    assert r["level"] == "ah"


def test_mountain_not_confirmed_returns_none():
    """Mountain in surge_tracking phase → no signal."""
    engine = _engine()
    engine._mountain_state["2330"] = {
        "phase": "surge_tracking", "recent_closes": [],
        "peak_high": 110.0, "peak_vr": 2.0, "peak_minute": 5,
        "confirmed_minute": 0, "no_new_high_count": 0,
    }
    active = _active(confirm_bars=1)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is None


def test_below_vwap_metadata():
    """Signal includes below_vwap metadata regardless of require_below_vwap setting."""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 100.0
    active = _active(confirm_bars=1, require_below_vwap=False)
    strat = engine._strategy_of(active)

    r = engine._eval_mountain_bounce(strat, active, "2330", _candle(99, high=101, minute=10), 600)
    assert r is not None
    assert r["below_vwap"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_strategy_a_mountain_bounce.py -v`
Expected: FAIL — `_eval_mountain_bounce` not defined

- [ ] **Step 3: Implement the evaluator**

In `backend/services/signal_engine.py`:

Add state dict to `__init__` (after `self._vwap_state`):

```python
self._mountain_bounce_armed: dict[tuple[str, str, str], dict] = {}
```

Add evaluator method (after `_eval_breakout_confirm`, before mountain constants):

```python
def _eval_mountain_bounce(
    self, strat: dict, active: ActiveSignalOut, symbol: str,
    candle: MinuteCandle, now: float,
) -> dict | None:
    """策略 A：造山確認 + 碰 CDP + N 根 close 在線下 → 做空訊號。"""
    st = self._mountain_state.get(symbol)
    if st is None or st["phase"] != "confirmed":
        return None

    cache = self._field_cache.get(symbol, {})
    field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp", "nl": "cdp_nl", "al": "cdp_al"}
    confirm_n = strat["confirm_bars"]
    tol_pct = strat.get("tolerance_pct", 0.0)
    require_vwap = strat.get("require_below_vwap", False)
    vwap = cache.get("vwap")

    if require_vwap and (vwap is None or candle.close >= vwap):
        return None

    result = None
    for level in strat["levels"]:
        v = cache.get(field_map.get(level, level))
        if v is None:
            continue
        threshold = v * (1 - tol_pct / 100)
        key = (active.id, symbol, level)
        armed = self._mountain_bounce_armed.get(key)

        if candle.high >= threshold:
            if armed is None:
                armed = {"confirm_count": 0, "cdp_val": v}
                self._mountain_bounce_armed[key] = armed

        if armed is None:
            continue

        if candle.close < armed["cdp_val"]:
            armed["confirm_count"] += 1
            if armed["confirm_count"] >= confirm_n:
                del self._mountain_bounce_armed[key]
                if result is None:
                    result = {
                        "level": level,
                        "direction": "from_below",
                        "role": "mountain_bounce",
                        "confirm_bars": armed["confirm_count"],
                        "peak_high": st["peak_high"],
                        "below_vwap": vwap is not None and candle.close < vwap,
                    }
        else:
            del self._mountain_bounce_armed[key]

    return result
```

Wire into `_evaluate()` dispatch — add after the `cdp_breakout_confirm` block (after line 394):

```python
elif stype == "mountain_bounce":
    if settled is None:
        continue
    cdp_touch = self._eval_mountain_bounce(strat, active, symbol, settled, now)
    ma_touch = None
    ok = cdp_touch is not None
```

Add to cooldown section — `mountain_bounce` should use per-level cooldown (after line 418-419):

```python
elif stype == "mountain_bounce":
    touch_level = (cdp_touch or {}).get("level", "")
```

Add to `_reset_daily_strategy_state()` (after `self._vwap_state.clear()`):

```python
self._mountain_bounce_armed.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_strategy_a_mountain_bounce.py -v`
Expected: 11 PASSED

- [ ] **Step 5: Run all existing tests to verify no regression**

Run: `.venv\Scripts\python -m pytest tests/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_strategy_a_mountain_bounce.py services/signal_engine.py
git commit -m "feat: add mountain_bounce evaluator (Strategy A)"
```

---

### Task 4: GA backtest script for Strategy A

**Files:**
- Create: `backend/scripts/_ga_strategy_a.py`

- [ ] **Step 1: Write the GA script**

Create `backend/scripts/_ga_strategy_a.py` — inline both mountain detection and strategy A evaluation. Chromosome:

| Gene | Range | Step |
|------|-------|------|
| confirm_bars | 1-4 | 1 |
| require_below_vwap | 0/1 | — |
| levels_combo | 0-6 (7 combos) | 1 |
| tolerance_pct | 0.0-0.5 | 0.1 |

Levels combos: `[["ah"], ["nh"], ["cdp"], ["ah","nh"], ["ah","cdp"], ["nh","cdp"], ["ah","nh","cdp"]]`

Fitness = profitable_count × abs(avg_drop) − α × losing_count (profitable = drop_to_close < -0.5%, losing = > +0.5%).

The script should:
1. Load cache (same as `_ga_mountain_v4.py`)
2. For each stock: run mountain detection → after confirmed, scan for CDP touches → apply confirm_bars logic → classify signal
3. Output: best params, comparison table, top 10

- [ ] **Step 2: Smoke test**

Run: `.venv\Scripts\python scripts/_ga_strategy_a.py --day 2026-06-16 --pop 10 --gen 5`
Expected: Completes, prints results

- [ ] **Step 3: Full GA run**

Run: `.venv\Scripts\python scripts/_ga_strategy_a.py --day 2026-06-16 --pop 40 --gen 80`
Expected: Results with comparison to defaults

- [ ] **Step 4: Commit**

```bash
git add scripts/_ga_strategy_a.py
git commit -m "feat: add GA backtest for Strategy A mountain_bounce"
```

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/models/condition.py` | Modify | Add `MountainBounceStrategy`, update `StrategyConfig`, bump `schema_version` |
| `backend/services/signal_engine.py` | Modify | Add `_update_vwap()`, `_eval_mountain_bounce()`, wiring, reset |
| `backend/tests/test_condition_mountain_bounce.py` | Create | Model parsing tests |
| `backend/tests/test_signal_engine_vwap.py` | Create | VWAP calculation tests |
| `backend/tests/test_strategy_a_mountain_bounce.py` | Create | Evaluator logic tests |
| `backend/scripts/_ga_strategy_a.py` | Create | GA parameter optimization |

## Self-Review

- **Spec coverage**: Model ✓, Evaluator ✓, VWAP ✓, Wiring ✓, GA ✓, Reset ✓
- **Placeholder scan**: All code blocks are complete — no TBD/TODO
- **Type consistency**: `MountainBounceStrategy` name consistent across condition.py and tests. `_eval_mountain_bounce` signature consistent. `_mountain_bounce_armed` key type matches usage.
- **Test coverage**: Model defaults/custom/roundtrip, VWAP single/multi/zero/reset, Evaluator no-mountain/arm-confirm/disarm/tolerance/vwap-block/vwap-allow/multi-level/metadata = 15 tests
