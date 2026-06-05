# CDP 雙策略(漲停打開碰 CDP + 順勢爆拉突破回踩)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在即時訊號引擎加兩個預設策略(preset),都靠現有 CDP 五線 + tick 流,新增一層 per-symbol 當日狀態。

**Architecture:** `ActiveFilter` 加 discriminated-union `strategy` 欄位;`SignalEngine._evaluate` 在 `strategy` 存在時路由到專用 evaluator,回 `cdp_touch` 後沿用既有 cooldown / touch_count / fanout。漲停 latch + 突破 arming 存在 `SignalEngine`,跨午夜在 heartbeat daily 分支 reset(不放 `_refill_field_cache`,避免規則編輯誤清)。

**Tech Stack:** Python / FastAPI / Pydantic v2(backend),React + TypeScript + Vite(frontend),pytest / vitest。

**Spec:** `docs/superpowers/specs/2026-06-05-cdp-limitup-open-and-breakout-retest-design.md`

**建議分支:** `feat/cdp-strategies`(或 worktree)。

**指令慣例:** backend 指令在 `backend/` 下執行;frontend 指令在 `frontend/` 下執行。

---

## Task 1: 漲停價純函式 `limit_up_price`

**Files:**
- Modify: `backend/services/cdp.py`(在 `round_to_tick_tw` 後、`CdpLevels` 前加函式)
- Test: `backend/tests/test_limit_up_price.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_limit_up_price.py`:

```python
"""驗台股漲停價 = 昨收 × 1.1,尾數捨去(不超過 +10%),tick 以漲停價級距為準。"""
from services.cdp import limit_up_price


def test_round_number_aligned():
    # 100 × 1.1 = 110.0,110 在 100–500 級距(tick 0.5),已對齊
    assert limit_up_price(100.0) == 110.0


def test_truncates_down_not_exceeding_10pct():
    # 10.05 × 1.1 = 11.055;11 在 10–50 級距(tick 0.05)→ 捨去到 11.05(= +9.95%)
    # 若用四捨五入會變 11.06(> +10%)— 漲停價絕不可超過 +10%
    assert limit_up_price(10.05) == 11.05


def test_uses_limit_price_tick_band():
    # 49 × 1.1 = 53.9;53.9 在 50–100 級距(tick 0.1)→ 53.9
    assert limit_up_price(49.0) == 53.9


def test_high_price_5_dollar_tick():
    # 1000 × 1.1 = 1100;>= 1000 級距 tick 5.0 → 1100.0
    assert limit_up_price(1000.0) == 1100.0


def test_sub_10_one_cent_tick():
    # 5.0 × 1.1 = 5.5;< 10 級距 tick 0.01 → 5.5
    assert limit_up_price(5.0) == 5.5
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`python -m pytest tests/test_limit_up_price.py -v`
Expected: FAIL — `ImportError: cannot import name 'limit_up_price'`

- [ ] **Step 3: 實作**

在 `backend/services/cdp.py` 的 `round_to_tick_tw` 函式之後(`class CdpLevels` 之前)加:

```python
def limit_up_price(prev_close: float) -> float:
    """台股漲停價 = 昨收 × 1.1,尾數不足一個 tick 捨去(不超過 +10%)。

    tick 以漲停價當下價位的級距為準。用整數「分」運算避免 float floor 在 tick
    邊界出錯(round_to_tick_tw 的 "down" 對 53.9/0.1=538.999… 會誤捨成 53.8)。
    無漲跌停限制的標的(部分 ETF / 新股首 5 日)價格不會剛好盯在此值,
    策略 1 的鎖死 latch 自然不會誤觸。
    """
    raw = prev_close * 1.1
    tick = tick_size(raw)
    raw_cents = round(raw * 100)          # 先 round 殺浮點雜訊
    tick_cents = round(tick * 100)
    floored_cents = (raw_cents // tick_cents) * tick_cents
    return round(floored_cents / 100.0, 2)
```

- [ ] **Step 4: 跑測試確認通過**

Run(在 `backend/`):`python -m pytest tests/test_limit_up_price.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/cdp.py backend/tests/test_limit_up_price.py
git commit -m "feat(cdp-strategies): limit_up_price 純函式(昨收×1.1 捨去到 tick)"
```

---

## Task 2: Data model — `strategy` 欄位

**Files:**
- Modify: `backend/models/condition.py`
- Test: `backend/tests/test_condition_strategy.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_condition_strategy.py`:

```python
"""驗 ActiveFilter.strategy(discriminated union)+ strategy-only filter 合法。"""
import pytest

from models.condition import (
    ActiveFilter, BreakoutRetestStrategy, LimitUpOpenTouchStrategy,
)


def test_limit_up_strategy_only_filter_valid():
    f = ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch"))
    assert f.strategy.lock_seconds == 60          # 預設
    assert f.strategy.levels == ["ah", "nh", "cdp", "nl", "al"]
    assert f.conditions == []                      # strategy-only 允許 conditions 空


def test_breakout_strategy_defaults():
    f = ActiveFilter(strategy=BreakoutRetestStrategy(type="breakout_retest"))
    assert f.strategy.surge_pct == 3.0
    assert f.strategy.early_window_minutes == 10
    assert f.strategy.retest_within_minutes == 10


def test_discriminator_picks_right_model_from_dict():
    f = ActiveFilter.model_validate(
        {"strategy": {"type": "breakout_retest", "surge_pct": 5}}
    )
    assert isinstance(f.strategy, BreakoutRetestStrategy)
    assert f.strategy.surge_pct == 5.0


def test_schema_version_bumped_to_5():
    assert ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")).schema_version == 5


def test_empty_filter_without_strategy_rejected():
    with pytest.raises(ValueError):
        ActiveFilter()   # 無 conditions / window / proximity / strategy
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`python -m pytest tests/test_condition_strategy.py -v`
Expected: FAIL — `ImportError: cannot import name 'LimitUpOpenTouchStrategy'`

- [ ] **Step 3: 實作 — import 加 Annotated**

`backend/models/condition.py` 第 12 行:

```python
from typing import Annotated, Literal
```

- [ ] **Step 4: 實作 — 加策略模型**

在 `class MAProximityCondition` 之後、`class ActiveFilter` 之前加:

```python
CdpLevel = Literal["ah", "nh", "cdp", "nl", "al"]


class LimitUpOpenTouchStrategy(BaseModel):
    """策略 1:曾鎖死漲停 ≥ lock_seconds → 打開後由上而下碰所選 CDP 線。"""

    type: Literal["limit_up_open_touch"]
    lock_seconds: int = Field(default=60, ge=5, le=600)
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"], min_length=1,
    )
    tolerance_ticks: int = Field(default=1, ge=0, le=10)


class BreakoutRetestStrategy(BaseModel):
    """策略 2:早盤爆拉由下突破某 CDP 線 → arm → retest_within_minutes 分內回踩同線。"""

    type: Literal["breakout_retest"]
    early_window_minutes: int = Field(default=10, ge=1, le=60)
    surge_pct: float = Field(default=3.0, gt=0, le=20)
    surge_window_seconds: WindowSeconds = 60
    retest_within_minutes: int = Field(default=10, ge=1, le=120)
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"], min_length=1,
    )
    tolerance_ticks: int = Field(default=1, ge=0, le=10)


StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy,
    Field(discriminator="type"),
]
```

- [ ] **Step 5: 實作 — ActiveFilter 加欄位 + 改 validator**

把 `class ActiveFilter(Filter):` 整段換成:

```python
class ActiveFilter(Filter):
    """即時訊號專用 Filter — 在 Filter 之上加時窗條件 + CDP/MA 觸發 + preset 策略。

    跟 Filter 的差異:允許 conditions=[] 當 window_conditions / cdp_proximity /
    ma_proximity / strategy 任一非空。
    """

    schema_version: int = 5  # 4→5,加 strategy(preset 策略)
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None
    ma_proximity:  MAProximityCondition  | None = None
    strategy: StrategyConfig | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # 覆蓋 Filter.conditions_non_empty;strategy 存在時由 strategy 定義整條 filter
        if self.strategy is not None:
            return self
        if (not self.conditions
                and not self.window_conditions
                and self.cdp_proximity is None
                and self.ma_proximity is None):
            raise ValueError("至少要有一個 condition / window_condition / cdp_proximity / ma_proximity / strategy")
        return self
```

- [ ] **Step 6: 跑測試確認通過 + 既有 model 測試不破**

Run(在 `backend/`):`python -m pytest tests/test_condition_strategy.py tests/test_condition_model.py -v`
Expected: PASS(新檔 5 passed,既有 test_condition_model 全 pass)

- [ ] **Step 7: Commit**

```bash
git add backend/models/condition.py backend/tests/test_condition_strategy.py
git commit -m "feat(cdp-strategies): ActiveFilter 加 strategy discriminated union (schema v5)"
```

---

## Task 3: 引擎 strategy 路由骨架

**Files:**
- Modify: `backend/services/signal_engine.py`
- Test: `backend/tests/test_signal_engine_strategy_routing.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_signal_engine_strategy_routing.py`:

```python
"""驗 strategy 路由:既有規則照常觸發;strategy 規則走 _eval_strategy(目前 stub 不發)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition, LimitUpOpenTouchStrategy,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()


def _cdp_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="g", name="generic",
        filter_json=ActiveFilter(cdp_proximity=CdpProximityCondition(levels=["ah"], tolerance_ticks=0)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


def _strategy_active() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="s", name="strat",
        filter_json=ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


@pytest.mark.asyncio
async def test_generic_rule_still_fires_with_routing():
    engine = SignalEngine()
    engine._active = [_cdp_active()]
    engine._field_cache["2330"] = {"cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured.get("event") == "signal"


@pytest.mark.asyncio
async def test_strategy_rule_no_fire_with_stub():
    engine = SignalEngine()
    engine._active = [_strategy_active()]
    engine._field_cache["2330"] = {"prev_close": 100.0, "cdp_ah": 100.0}
    engine._prev_tick["2330"] = Tick(price=99.0, size=1, time=0.0)

    fired = False
    async def fake_broadcast(payload):
        nonlocal fired; fired = True

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert fired is False   # stub _eval_strategy 回 None
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`python -m pytest tests/test_signal_engine_strategy_routing.py -v`
Expected: FAIL — `AttributeError: 'SignalEngine' object has no attribute '_strategy_of'`

- [ ] **Step 3: 實作 — 加 `_strategy_of` + `_eval_strategy` stub**

在 `SignalEngine` class 內(`_eval_with_touch_meta` 之前)加兩個方法:

```python
    def _strategy_of(self, active: ActiveSignalOut) -> dict | None:
        """取 filter 的 strategy,一律回 dict(evaluator 不必處理 model/dict 二態)。"""
        f = active.filter_json
        s = f.get("strategy") if isinstance(f, dict) else getattr(f, "strategy", None)
        if s is None:
            return None
        return s if isinstance(s, dict) else s.model_dump()

    def _eval_strategy(
        self, strat: dict, active: ActiveSignalOut, symbol: str, tick: Tick,
        prev: Tick | None, now: float,
    ) -> dict | None:
        """依 strategy.type 跑專用 evaluator,回 cdp_touch dict 或 None。"""
        return None  # Task 4 / 5 填內容
```

- [ ] **Step 4: 實作 — `_evaluate` 加路由**

把 `async def _evaluate` 整段換成(改動:hoist `now`、加 strategy if/else):

```python
    async def _evaluate(self, symbol: str, tick: Tick) -> None:
        """對每個涉及這 symbol 的 active_signal 跑條件,觸發時帶 touch metadata fanout。"""
        if not self._in_trading_session(time.time()):
            return

        now = time.time()
        # 正盤內才累積今日總量,避免試撮 / 盤後 stale tick 污染
        self._day_volume[symbol] = self._day_volume.get(symbol, 0) + max(0, tick.size)

        prev = self._prev_tick.get(symbol)
        try:
            for active in self._active:
                if not self._scope_includes(active, symbol):
                    continue

                strat = self._strategy_of(active)
                if strat is not None:
                    cdp_touch = self._eval_strategy(strat, active, symbol, tick, prev, now)
                    ma_touch = None
                    ok = cdp_touch is not None
                else:
                    cdp_touch, ma_touch = self._eval_with_touch_meta(active, symbol, tick, prev)
                    non_prox_ok = self._eval_non_proximity(active, symbol, tick)
                    ok = self._combine_results(active, cdp_touch, ma_touch, non_prox_ok)
                if not ok:
                    continue

                # cooldown 檢查
                key = (active.id, symbol)
                last_ts = self._cooldown.get(key, 0)
                if now - last_ts < active.cooldown_seconds:
                    continue
                self._cooldown[key] = now

                # touch_count(proximity / strategy 觸發才計次)
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
            # 用 finally 保證每次 evaluate 都更新 prev,避免下次方向算錯
            self._prev_tick[symbol] = tick
```

- [ ] **Step 5: 跑測試確認通過 + 既有引擎測試不破**

Run(在 `backend/`):`python -m pytest tests/test_signal_engine_strategy_routing.py tests/test_signal_engine_touch_metadata.py tests/test_signal_engine_proximity.py -v`
Expected: PASS(全綠)

- [ ] **Step 6: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_strategy_routing.py
git commit -m "feat(cdp-strategies): 引擎 strategy 路由骨架(_eval_strategy stub)"
```

---

## Task 4: 策略 1 — 漲停打開碰 CDP

**Files:**
- Modify: `backend/services/signal_engine.py`
- Test: `backend/tests/test_signal_engine_limit_up_open.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_signal_engine_limit_up_open.py`:

```python
"""驗策略 1:鎖死漲停 ≥N 秒 → 打開後由上而下碰 CDP 線 → 發(測支撐)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, LimitUpOpenTouchStrategy
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()

STRAT = {
    "type": "limit_up_open_touch", "lock_seconds": 60,
    "levels": ["cdp"], "tolerance_ticks": 1,
}


def _cache(engine):
    # 昨收 100 → 漲停價 110;CDP 中線設 100(供回踩碰線)
    engine._field_cache["2330"] = {"prev_close": 100.0, "cdp": 100.0}


def test_lock_accumulates_over_pinned_ticks():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    assert engine._limit_lock_best["2330"] >= 60.0


def test_fires_after_lock_then_open_then_touch_from_above():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 71.0), prev=Tick(101.0, 1, 70.5),
    )
    assert touch == {"level": "cdp", "direction": "from_above", "role": "support"}


def test_no_fire_if_lock_too_short():
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=30.0)   # 只 30s
    engine._update_limit_up_state("2330", Tick(109.0, 1, 0.0), now=31.0)   # 打開
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 40.0), prev=Tick(101.0, 1, 39.0),
    )
    assert touch is None


def test_no_fire_on_upward_touch():
    # 鎖死夠久但反彈由下往上碰線(回升穿線)→ 不發(只認由上而下測支撐)
    engine = SignalEngine()
    _cache(engine)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=0.0)
    engine._update_limit_up_state("2330", Tick(110.0, 1, 0.0), now=70.0)
    touch = engine._eval_limit_up_open_touch(
        STRAT, "2330", Tick(100.0, 1, 80.0), prev=Tick(99.0, 1, 79.0),
    )
    assert touch is None


def test_reset_daily_clears_lock_state():
    engine = SignalEngine()
    engine._limit_at_since["2330"] = 5.0
    engine._limit_lock_best["2330"] = 120.0
    engine._reset_daily_strategy_state()
    assert "2330" not in engine._limit_lock_best
    assert "2330" not in engine._limit_at_since


@pytest.mark.asyncio
async def test_fires_through_evaluate():
    engine = SignalEngine()
    engine._active = [ActiveSignalOut(
        id="s", name="漲停打開碰CDP",
        filter_json=ActiveFilter(strategy=LimitUpOpenTouchStrategy(
            type="limit_up_open_touch", lock_seconds=60, levels=["cdp"], tolerance_ticks=1)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )]
    engine._limit_up_active = True
    _cache(engine)
    engine._limit_lock_best["2330"] = 65.0        # 已鎖死夠久
    engine._prev_tick["2330"] = Tick(101.0, 1, 0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["data"]["cdp_touch"]["level"] == "cdp"
    assert captured["data"]["cdp_touch"]["role"] == "support"
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`python -m pytest tests/test_signal_engine_limit_up_open.py -v`
Expected: FAIL — `AttributeError: ... '_update_limit_up_state'`

- [ ] **Step 3: 實作 — import limit_up_price**

`backend/services/signal_engine.py` 把 `from services.cdp import get_cdp_service` 換成:

```python
from services.cdp import get_cdp_service, limit_up_price
```

- [ ] **Step 4: 實作 — `__init__` 加狀態**

在 `__init__` 的 `self._ma_touch_count: ... = {}` 之後加:

```python
        # 漲停 latch(per-symbol,當日;daily reset)
        self._limit_at_since: dict[str, float] = {}    # 目前連續盯漲停價起點(離開即清)
        self._limit_lock_best: dict[str, float] = {}   # 今日達到過的最長連續鎖死秒數
        self._limit_up_active = False                  # 有無啟用 limit_up_open_touch 規則
```

- [ ] **Step 5: 實作 — refresh_active_signals 設 flag**

在 `refresh_active_signals` 內 `self._active = [...]` 之後、`await self._refill_field_cache()` 之前加:

```python
        self._limit_up_active = any(
            self._strategy_type(a) == "limit_up_open_touch" for a in self._active
        )
```

- [ ] **Step 6: 實作 — 加狀態方法 + 評估方法**

在 `SignalEngine` class 內(`_eval_strategy` 之後)加:

```python
    def _strategy_type(self, active: ActiveSignalOut) -> str | None:
        s = self._strategy_of(active)
        return s.get("type") if s else None

    def _update_limit_up_state(self, symbol: str, tick: Tick, now: float) -> None:
        """維護 per-symbol 漲停鎖死狀態(每 tick + heartbeat 呼叫)。

        用「最新成交價 == 漲停價且持續 ≥N 秒」近似鎖死;heartbeat 用 latest tick
        推進 now,連續盯住就累積秒數,不依賴鎖死期間有無新成交。
        """
        prev_close = self._field_cache.get(symbol, {}).get("prev_close")
        if prev_close is None:
            return
        lp = limit_up_price(prev_close)
        if tick.price >= lp:
            since = self._limit_at_since.get(symbol)
            if since is None:
                self._limit_at_since[symbol] = now
                since = now
            dur = now - since
            if dur > self._limit_lock_best.get(symbol, 0.0):
                self._limit_lock_best[symbol] = dur
        else:
            self._limit_at_since.pop(symbol, None)

    def _eval_limit_up_open_touch(
        self, strat: dict, symbol: str, tick: Tick, prev: Tick | None,
    ) -> dict | None:
        """曾鎖死漲停 ≥N 秒 → 打開後由上而下碰所選 CDP 線 → 回 cdp_touch。"""
        cache = self._field_cache.get(symbol, {})
        prev_close = cache.get("prev_close")
        if prev_close is None:
            return None
        lp = limit_up_price(prev_close)
        if self._limit_lock_best.get(symbol, 0.0) < strat["lock_seconds"]:
            return None                      # 今天沒鎖死夠久
        if tick.price >= lp:
            return None                      # 還沒打開(仍在漲停價或之上)
        ok, level = self._eval_cdp_proximity(symbol, tick, {
            "levels": strat["levels"], "tolerance_ticks": strat["tolerance_ticks"],
        })
        if not ok or level is None:
            return None
        field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp", "nl": "cdp_nl", "al": "cdp_al"}
        v = cache.get(field_map[level])
        direction = self._direction_of_touch(prev, tick, v) if v is not None else "horizontal"
        if direction != "from_above":
            return None                      # 只在「由上而下回落碰」時告警(測支撐)
        return {"level": level, "direction": "from_above", "role": "support"}

    def _reset_daily_strategy_state(self) -> None:
        """跨午夜清 strategy 當日狀態。放 heartbeat daily 分支,不放 _refill_field_cache —
        後者也在規則編輯時被呼叫,會誤清盤中累積的鎖死狀態。"""
        self._limit_at_since.clear()
        self._limit_lock_best.clear()
```

- [ ] **Step 7: 實作 — `_eval_strategy` dispatch + `_evaluate` 呼叫狀態更新**

(a) 把 `_eval_strategy` 的 body 換成:

```python
        stype = strat.get("type")
        if stype == "limit_up_open_touch":
            return self._eval_limit_up_open_touch(strat, symbol, tick, prev)
        return None
```

(b) 在 `_evaluate` 的 `now = time.time()` 之後、`self._day_volume[symbol] = ...` 之前加:

```python
        if self._limit_up_active:
            self._update_limit_up_state(symbol, tick, now)
```

- [ ] **Step 8: 實作 — heartbeat daily 分支呼叫 reset**

在 `_heartbeat_loop` 的 daily 分支,`self._gc_touch_counts()` 之後加一行:

```python
                    self._reset_daily_strategy_state()
```

- [ ] **Step 9: 跑測試確認通過**

Run(在 `backend/`):`python -m pytest tests/test_signal_engine_limit_up_open.py -v`
Expected: PASS(6 passed)

- [ ] **Step 10: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_limit_up_open.py
git commit -m "feat(cdp-strategies): 策略1 漲停打開碰CDP(鎖死 latch + 由上而下碰線)"
```

---

## Task 5: 策略 2 — 順勢爆拉突破回踩

**Files:**
- Modify: `backend/services/signal_engine.py`
- Test: `backend/tests/test_signal_engine_breakout_retest.py`

- [ ] **Step 1: 寫失敗測試**

Create `backend/tests/test_signal_engine_breakout_retest.py`:

```python
"""驗策略 2:早盤爆拉由下突破 CDP 線 → arm → M 分內由上而下回踩同線 → 發。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, BreakoutRetestStrategy
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

TZ = timezone(timedelta(hours=8))
EARLY = datetime(2026, 5, 20, 9, 5, tzinfo=TZ).timestamp()    # 開盤後 5 分(早盤內)
LATE = datetime(2026, 5, 20, 9, 20, tzinfo=TZ).timestamp()    # 開盤後 20 分(早盤外)
POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=TZ).timestamp()


def _active(levels=("nh",)):
    return ActiveSignalOut(
        id="b", name="爆拉突破回踩",
        filter_json=ActiveFilter(strategy=BreakoutRetestStrategy(
            type="breakout_retest", early_window_minutes=10, surge_pct=3.0,
            surge_window_seconds=60, retest_within_minutes=10,
            levels=list(levels), tolerance_ticks=1)),
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=60, enabled=True, created_at="2026-06-05",
    )


def test_arm_on_early_surge_cross_then_retest_fires():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: True       # 模擬爆拉成立
    active = _active()
    strat = engine._strategy_of(active)

    # 早盤由下穿越 nh=100 → arm(穿越當下不發)
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert r is None
    assert "nh" in engine._breakout_armed[(active.id, "2330")]

    # 稍後由上而下回踩 nh → 發
    later = EARLY + 60
    r2 = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, later), prev=Tick(101.0, 1, later - 1), now=later)
    assert r2 == {"level": "nh", "direction": "from_above", "role": "support"}
    assert "nh" not in engine._breakout_armed[(active.id, "2330")]   # 觸發後 disarm


def test_no_arm_when_surge_below_threshold():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: False      # 爆拉不足
    active = _active()
    strat = engine._strategy_of(active)
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert engine._breakout_armed.get((active.id, "2330"), {}) == {}


def test_no_arm_outside_early_window():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: True
    active = _active()
    strat = engine._strategy_of(active)
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, LATE), prev=Tick(99.0, 1, LATE - 1), now=LATE)
    assert engine._breakout_armed.get((active.id, "2330"), {}) == {}


def test_retest_after_window_expires_no_fire():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._eval_window = lambda symbol, tick, wc: False
    active = _active()
    strat = engine._strategy_of(active)
    engine._breakout_armed[(active.id, "2330")] = {"nh": EARLY}    # 5 分時 arm
    late = EARLY + 11 * 60                                          # 11 分後才回踩(> 10 分)
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, late), prev=Tick(101.0, 1, late - 1), now=late)
    assert r is None
    assert "nh" not in engine._breakout_armed[(active.id, "2330")]  # 逾時 disarm


def test_multi_line_arm_retest_any():
    engine = SignalEngine()
    engine._field_cache["2330"] = {"cdp_nh": 100.0, "cdp_ah": 102.0}
    engine._eval_window = lambda symbol, tick, wc: True
    active = _active(levels=("nh", "ah"))
    strat = engine._strategy_of(active)
    # 一次爆拉穿越 nh(100) 與 ah(102):prev 99 → curr 103,兩條都 from_below
    engine._eval_breakout_retest(
        strat, active, "2330", Tick(103.0, 1, EARLY), prev=Tick(99.0, 1, EARLY - 1), now=EARLY)
    assert set(engine._breakout_armed[(active.id, "2330")]) == {"nh", "ah"}
    # 回踩 nh → 發
    later = EARLY + 60
    r = engine._eval_breakout_retest(
        strat, active, "2330", Tick(100.0, 1, later), prev=Tick(101.0, 1, later - 1), now=later)
    assert r["level"] == "nh"


def test_reset_daily_clears_breakout_armed():
    engine = SignalEngine()
    engine._breakout_armed[("x", "2330")] = {"nh": 1.0}
    engine._reset_daily_strategy_state()
    assert engine._breakout_armed == {}


@pytest.mark.asyncio
async def test_retest_fires_through_evaluate():
    engine = SignalEngine()
    active = _active()
    engine._active = [active]
    engine._field_cache["2330"] = {"cdp_nh": 100.0}
    engine._breakout_armed[(active.id, "2330")] = {"nh": POST_OPEN - 60}  # 1 分前突破
    engine._prev_tick["2330"] = Tick(101.0, 1, 0.0)

    captured: dict = {}
    async def fake_broadcast(payload): captured.update(payload)

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = fake_broadcast
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=100.0, size=1, time=1.0))

    assert captured["data"]["cdp_touch"] == {
        "level": "nh", "direction": "from_above", "role": "support", "touch_index": 1,
    }
```

- [ ] **Step 2: 跑測試確認失敗**

Run(在 `backend/`):`python -m pytest tests/test_signal_engine_breakout_retest.py -v`
Expected: FAIL — `AttributeError: ... '_breakout_armed'` / `'_eval_breakout_retest'`

- [ ] **Step 3: 實作 — `__init__` 加 arming 狀態**

在 `__init__` 的 `self._limit_up_active = False` 之後加:

```python
        # 突破 arming(per (rule_id, symbol),門檻/線依 rule 參數而異;daily reset)
        self._breakout_armed: dict[tuple[str, str], dict[str, float]] = {}
```

- [ ] **Step 4: 實作 — `_reset_daily_strategy_state` 加清 arming**

把 `_reset_daily_strategy_state` 改成(加最後一行):

```python
    def _reset_daily_strategy_state(self) -> None:
        """跨午夜清 strategy 當日狀態。放 heartbeat daily 分支,不放 _refill_field_cache —
        後者也在規則編輯時被呼叫,會誤清盤中累積的鎖死 / arming 狀態。"""
        self._limit_at_since.clear()
        self._limit_lock_best.clear()
        self._breakout_armed.clear()
```

- [ ] **Step 5: 實作 — `_minutes_since_open` + `_eval_breakout_retest`**

在 `SignalEngine` class 內(`_eval_limit_up_open_touch` 之後)加:

```python
    @staticmethod
    def _minutes_since_open(now_ts: float) -> float:
        """距開盤(09:00)幾分鐘(僅正盤內有意義)。"""
        dt = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
        return (dt.hour - MARKET_OPEN[0]) * 60 + (dt.minute - MARKET_OPEN[1]) + dt.second / 60.0

    def _eval_breakout_retest(
        self, strat: dict, active: ActiveSignalOut, symbol: str, tick: Tick,
        prev: Tick | None, now: float,
    ) -> dict | None:
        """早盤爆拉由下突破某 CDP 線 → arm;之後 M 分內由上而下回踩同線 → 回 cdp_touch。"""
        from services.cdp import tick_size

        cache = self._field_cache.get(symbol, {})
        field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp", "nl": "cdp_nl", "al": "cdp_al"}
        armed = self._breakout_armed.setdefault((active.id, symbol), {})

        # 1. arming — 僅早盤內,偵測「由下而上穿越 + 突然爆拉」
        if self._minutes_since_open(now) < strat["early_window_minutes"]:
            surge_ok = self._eval_window(symbol, tick, {
                "type": "price_change_pct",
                "window_seconds": strat["surge_window_seconds"],
                "operator": "gte",
                "value": strat["surge_pct"],
            })
            if surge_ok:
                for level in strat["levels"]:
                    v = cache.get(field_map[level])
                    if v is None:
                        continue
                    if self._direction_of_touch(prev, tick, v) == "from_below":
                        armed[level] = now

        # 2. retest — 已 arm 的線,M 分內由上而下回踩
        window_s = strat["retest_within_minutes"] * 60
        for level in list(armed.keys()):
            if now - armed[level] > window_s:
                del armed[level]                 # 逾時 disarm
                continue
            v = cache.get(field_map[level])
            if v is None:
                continue
            tol = strat["tolerance_ticks"] * tick_size(v)
            if abs(tick.price - v) <= tol and self._direction_of_touch(prev, tick, v) == "from_above":
                del armed[level]                 # 觸發一次即 disarm
                return {"level": level, "direction": "from_above", "role": "support"}
        return None
```

- [ ] **Step 6: 實作 — `_eval_strategy` 加 breakout 分支**

把 `_eval_strategy` 的 body 換成:

```python
        stype = strat.get("type")
        if stype == "limit_up_open_touch":
            return self._eval_limit_up_open_touch(strat, symbol, tick, prev)
        if stype == "breakout_retest":
            return self._eval_breakout_retest(strat, active, symbol, tick, prev, now)
        return None
```

- [ ] **Step 7: 跑測試確認通過 + 整支引擎測試回歸**

Run(在 `backend/`):`python -m pytest tests/ -v -k "signal_engine or condition or limit_up"`
Expected: PASS(全綠)

- [ ] **Step 8: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_breakout_retest.py
git commit -m "feat(cdp-strategies): 策略2 順勢爆拉突破回踩(早盤 arm + 回踩同線)"
```

---

## Task 6: 前端 api.ts type

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加 strategy type**

在 `export interface MAProximity { ... }`(約 line 111)之後、`export interface ActiveFilter`(約 line 113)之前加:

```typescript
export type CdpLevel = "ah" | "nh" | "cdp" | "nl" | "al";

export interface LimitUpOpenTouchStrategy {
  type: "limit_up_open_touch";
  lock_seconds: number;
  levels: CdpLevel[];
  tolerance_ticks: number;
}

export interface BreakoutRetestStrategy {
  type: "breakout_retest";
  early_window_minutes: number;
  surge_pct: number;
  surge_window_seconds: WindowSeconds;
  retest_within_minutes: number;
  levels: CdpLevel[];
  tolerance_ticks: number;
}

export type StrategyConfig = LimitUpOpenTouchStrategy | BreakoutRetestStrategy;
```

- [ ] **Step 2: ActiveFilter 加 strategy 欄位**

把 `export interface ActiveFilter extends Filter { ... }` 換成:

```typescript
export interface ActiveFilter extends Filter {
  // schema_version 已從 Filter inherit,不重複宣告
  window_conditions?: WindowCondition[];
  cdp_proximity?: CdpProximity | null;
  ma_proximity?: MAProximity | null;
  strategy?: StrategyConfig | null;
}
```

- [ ] **Step 3: typecheck 通過**

Run(在 `frontend/`):`npm run build`
Expected: build 成功(`tsc -b` 無型別錯誤)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(cdp-strategies): 前端 ActiveFilter 加 strategy type"
```

---

## Task 7: 前端策略卡 UI

**Files:**
- Create: `frontend/src/components/PresetStrategyFields.tsx`
- Modify: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: 新建 PresetStrategyFields 元件**

Create `frontend/src/components/PresetStrategyFields.tsx`:

```tsx
import { type StrategyConfig } from "../lib/api";

const ALL_CDP_LEVELS = ["ah", "nh", "cdp", "nl", "al"] as const;
const CDP_LEVEL_LABEL: Record<typeof ALL_CDP_LEVELS[number], string> = {
  ah: "AH (最高值)", nh: "NH (近高)", cdp: "CDP 中線", nl: "NL (近低)", al: "AL (最低值)",
};

interface Props {
  value: StrategyConfig;
  onChange: (next: StrategyConfig) => void;
}

/** 兩個 preset 策略共用的參數編輯(線多選 + 各自的數字參數)。 */
export function PresetStrategyFields({ value, onChange }: Props) {
  function toggleLevel(level: typeof ALL_CDP_LEVELS[number]) {
    const has = value.levels.includes(level);
    if (has && value.levels.length <= 1) return;   // 至少留 1 條
    const levels = has ? value.levels.filter((l) => l !== level) : [...value.levels, level];
    onChange({ ...value, levels });
  }
  function setNum(key: string, n: number) {
    onChange({ ...value, [key]: n } as StrategyConfig);
  }

  return (
    <div className="border border-line p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {ALL_CDP_LEVELS.map((lv) => {
          const checked = value.levels.includes(lv);
          const lock = checked && value.levels.length === 1;
          return (
            <label key={lv} className={`text-sm flex items-center gap-1 ${lock ? "opacity-60" : "cursor-pointer"}`}>
              <input type="checkbox" checked={checked} disabled={lock}
                onChange={() => toggleLevel(lv)} className="accent-accent" />
              {CDP_LEVEL_LABEL[lv]}
            </label>
          );
        })}
      </div>

      {value.type === "limit_up_open_touch" ? (
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">鎖死秒數
            <input type="number" min={5} max={600} value={value.lock_seconds}
              onChange={(e) => setNum("lock_seconds", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-20 tabular-nums" /></label>
          <label className="flex items-center gap-2">Tolerance
            <input type="number" min={0} max={10} value={value.tolerance_ticks}
              onChange={(e) => setNum("tolerance_ticks", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />tick</label>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">早盤視窗
            <input type="number" min={1} max={60} value={value.early_window_minutes}
              onChange={(e) => setNum("early_window_minutes", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />分</label>
          <label className="flex items-center gap-2">爆拉門檻
            <input type="number" step="any" value={value.surge_pct}
              onChange={(e) => setNum("surge_pct", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-20 tabular-nums" />%</label>
          <label className="flex items-center gap-2">回踩時限
            <input type="number" min={1} max={120} value={value.retest_within_minutes}
              onChange={(e) => setNum("retest_within_minutes", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />分</label>
          <label className="flex items-center gap-2">Tolerance
            <input type="number" min={0} max={10} value={value.tolerance_ticks}
              onChange={(e) => setNum("tolerance_ticks", Number(e.target.value))}
              className="bg-bg-deep border border-line px-2 py-1 w-16 tabular-nums" />tick</label>
        </div>
      )}
    </div>
  );
}
```

註:`surge_window_seconds`(爆拉時窗)v1 不在 UI 開放,沿用預設 60 秒。

- [ ] **Step 2: ActiveSignalEditor — import + 預設值**

把 `ActiveSignalEditor.tsx` 第 1–7 行的 import 區換成(加 strategy types + 元件):

```tsx
import { useState } from "react";
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal,
  type BreakoutRetestStrategy, type CdpProximity, type Condition,
  type ConditionField, type ConditionOperator, type LimitUpOpenTouchStrategy,
  type MAProximity, type StrategyConfig,
  type WindowCondition, type WindowConditionType, type WindowSeconds,
} from "../lib/api";
import { PresetStrategyFields } from "./PresetStrategyFields";

const ALL_CDP_LEVELS_DEFAULT = ["ah", "nh", "cdp", "nl", "al"] as const;

const DEFAULT_LIMIT_UP: LimitUpOpenTouchStrategy = {
  type: "limit_up_open_touch", lock_seconds: 60,
  levels: [...ALL_CDP_LEVELS_DEFAULT], tolerance_ticks: 1,
};
const DEFAULT_BREAKOUT: BreakoutRetestStrategy = {
  type: "breakout_retest", early_window_minutes: 10, surge_pct: 3,
  surge_window_seconds: 60, retest_within_minutes: 10,
  levels: [...ALL_CDP_LEVELS_DEFAULT], tolerance_ticks: 1,
};
```

- [ ] **Step 3: ActiveSignalEditor — strategy state**

在 `const [filter, setFilter] = useState<ActiveFilter>(...)` 之後加:

```tsx
  const [strategy, setStrategy] = useState<StrategyConfig | null>(initial?.filter_json?.strategy ?? null);
```

- [ ] **Step 4: ActiveSignalEditor — 改 save()**

把 `async function save()` 整段換成(strategy 模式組 strategy-only filter):

```tsx
  async function save() {
    if (!name.trim()) { setError("請輸入名稱"); return; }
    let filterToSave: ActiveFilter;
    if (strategy) {
      filterToSave = { schema_version: 5, conditions: [], window_conditions: [], logic: "AND", strategy };
    } else {
      if (filter.conditions.length === 0
          && (filter.window_conditions ?? []).length === 0
          && !filter.cdp_proximity
          && !filter.ma_proximity) {
        setError("至少要有一條條件"); return;
      }
      filterToSave = filter;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        filter_json: filterToSave,
        scope: { type: "watchlist" as const },  // legacy; backend ignores
        cooldown_seconds: cooldown,
        enabled,
        notify_discord: notifyDiscord,
      };
      if (initial) await api.activeSignals.update(initial.id, payload);
      else await api.activeSignals.create(payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  }
```

- [ ] **Step 5: ActiveSignalEditor — strategy 選擇器 + 條件區條件渲染**

在名稱 input 區塊(`<input value={name} ... />` 的 `</...>` 之後、`{/* WindowCondition 區塊 */}` 之前)插入策略選擇器:

```tsx
        {/* 預設策略 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">預設策略</div>
          <select
            value={strategy?.type ?? "none"}
            onChange={(e) => {
              const t = e.target.value;
              if (t === "limit_up_open_touch") setStrategy({ ...DEFAULT_LIMIT_UP });
              else if (t === "breakout_retest") setStrategy({ ...DEFAULT_BREAKOUT });
              else setStrategy(null);
            }}
            className="bg-bg-deep border border-line text-sm px-2 py-1 mb-3"
          >
            <option value="none">無(自訂條件)</option>
            <option value="limit_up_open_touch">漲停打開碰 CDP</option>
            <option value="breakout_retest">順勢爆拉突破回踩</option>
          </select>
          {strategy && <PresetStrategyFields value={strategy} onChange={setStrategy} />}
        </div>
```

接著把原本四個自訂條件區塊(WindowCondition / Filter.conditions / CDP 觸發 / MA 觸發,以及它們下方的「邏輯」radio)包在 `{!strategy && ( ... )}` 內 — strategy 模式時隱藏。具體做法:在 `{/* WindowCondition 區塊 */}` 前一行加 `{!strategy && (<>`,在 `{/* Logic / Discord / Cooldown */}` 區塊的 `邏輯` `<div>...</div>`(含 AND/OR radio)結尾之後加 `</>)}`。Discord / Cooldown 兩格保留在 strategy 模式也要顯示。

> 落地提示:`邏輯` radio 跟 Discord / Cooldown 同在一個 `grid grid-cols-2` 容器內。最簡單的拆法 = 把該 grid 容器拆成兩段:「邏輯」格放進 `{!strategy && (...)}`;Discord、Cooldown 兩格獨立保留。實作時依當下 JSX 結構調整,確保:strategy 模式只顯示「預設策略 + Discord + Cooldown + 按鈕」,非 strategy 模式維持原樣。

- [ ] **Step 6: typecheck 通過**

Run(在 `frontend/`):`npm run build`
Expected: build 成功

- [ ] **Step 7: 手動驗收(UI)**

啟動 `.\start.ps1`,開訊號規則編輯器:
1. 「預設策略」選「漲停打開碰 CDP」→ 出現線多選 + 鎖死秒數 + Tolerance;自訂條件區塊消失
2. 取消選擇(選「無」)→ 自訂條件區塊回來
3. 選「順勢爆拉突破回踩」→ 出現早盤視窗 / 爆拉門檻 / 回踩時限 / Tolerance
4. 命名後儲存 → `GET /api/active_signals` 回傳的該 rule `filter_json.strategy.type` 正確

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/PresetStrategyFields.tsx frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(cdp-strategies): 訊號編輯器加兩個預設策略卡 + 參數"
```

---

## Task 8: 全量驗證

**Files:** 無(僅跑測試 + 手動煙霧)

- [ ] **Step 1: 後端全量測試**

Run(在 `backend/`):`python -m pytest tests/ -v`
Expected: 全綠(新增 5 個測試檔 + 既有測試無回歸)

- [ ] **Step 2: 前端 build + 測試**

Run(在 `frontend/`):`npm run build && npm test`
Expected: build 成功、vitest 全綠

- [ ] **Step 3: 端到端煙霧(可選,需富邦連線)**

啟動 `.\start.ps1`,各建一條策略掛在一檔監聽中的個股上:
- 策略 1:挑一檔今天曾鎖漲停又打開的股(或盤後用既有歷史回放概念人工確認 log)
- 策略 2:盤中早盤觀察爆拉突破 → 回踩的告警是否在前端 / Discord 出現

確認 `signals_log` 有寫入、`cdp_touch` 帶 `role:"support"` 與正確 `level`。

> 註:富邦 `trades` 鎖漲停期間推送行為(spec 開放問題 1)在這步實測;若鎖死期間完全無 tick,heartbeat 用 latest tick 推進鎖死計時應仍成立,實測確認。

- [ ] **Step 4: 更新 memory 指標(實作完成後)**

把 `~/.claude/.../memory/project_cdp_two_strategies.md` 的「待 writing-plans → 實作」改為「已實作(commit 範圍)」,或視整合方式更新。

---

## Self-Review

**Spec coverage（spec 章節 → task）:**

| Spec 章節 | Task |
|---|---|
| 漲停價純函式 `limit_up_price` | Task 1 |
| Data model（StrategyConfig + ActiveFilter.strategy + validator） | Task 2 |
| 狀態層（`__init__` 狀態 + daily reset） | Task 4(限漲停)/ Task 5(arming） |
| 引擎路由（`_evaluate` 分流） | Task 3 |
| 策略 1 evaluator | Task 4 |
| 策略 2 evaluator（早盤閘門 + 爆拉 + arming + 回踩） | Task 5 |
| Frontend UI（兩張策略卡 + 參數） | Task 6（type）+ Task 7（UI） |
| 測試（純函式 / 狀態機 / 為什麼） | 各 Task 內 |
| 富邦鎖漲停推送行為驗證 | Task 8 Step 3 |

**型別 / 命名一致性檢查(已核對):**
- `_eval_strategy(strat, active, symbol, tick, prev, now)` 簽名在 Task 3 定義、Task 4/5 沿用一致
- `_eval_limit_up_open_touch(strat, symbol, tick, prev)`、`_eval_breakout_retest(strat, active, symbol, tick, prev, now)` 在 `_eval_strategy` 呼叫處參數對齊
- `_reset_daily_strategy_state` Task 4 建(清 limit)、Task 5 擴(加 `_breakout_armed.clear()`);`_breakout_armed` 在 Task 5 `__init__` 才初始化 — Task 4 的 reset 不引用它,順序安全
- strategy dict key(`lock_seconds` / `levels` / `tolerance_ticks` / `early_window_minutes` / `surge_pct` / `surge_window_seconds` / `retest_within_minutes`)在 model(Task 2)、evaluator(Task 4/5)、前端 type(Task 6)、UI(Task 7)四處一致
- `cdp_touch` 形狀 `{level, direction:"from_above", role:"support"}` + `touch_index`(由 `_evaluate` 補)對齊既有 `TouchMeta`

**已知 v1 行為(非 bug,spec 已載):**
- 策略 1 只在 `from_above`(由上而下到達線值或以下)觸發;停在線上方 tolerance 內不發(實務上回落必經過線值)
- 策略 2 不含「開高」分支(spec Non-goals);只認盤中極拉穿越
- backend 盤中重啟 → 當日狀態歸零(同 `_day_volume` 性質)
