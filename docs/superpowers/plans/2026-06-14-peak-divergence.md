# 雙峰量價背離造山(策略 5)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「雙峰量價背離造山」即時訊號(策略5):主峰創當日新高 → 收盤回落確認 → 次峰量縮且不過前高 → 滾頭觸發「做頭轉弱」。

**Architecture:** 吃引擎結算的 1 分 K candle(照策略1 `cdp_breakout_confirm` 模式),per `(active.id, symbol)` 狀態機存 `_peak_state` dict,觸發回 `cdp_touch` dict 走現有 `_fanout`。落地 = `condition.py`(schema)+ `signal_engine.py`(evaluator/dispatch/reset)+ `replay_engine.py`(回測 preset)+ `bot/src/signal.ts`(圖卡渲染)。設計依據:`docs/superpowers/specs/2026-06-14-peak-divergence-design.md`。

**Tech Stack:** Python 3 / Pydantic v2 / pytest(`asyncio_mode=auto`)/ FastAPI 後端;TypeScript Discord bot。

**分支:** 已在 `feat/peak-divergence`(spec commit `8faa101`)。所有 task 直接在此分支累積 commit。

**測試命令(在 `backend/` 下執行,用 backend 自己的 venv):** `.venv\Scripts\python -m pytest <檔> -v`。PowerShell 串接用分號 + `if($?)`,不要用 `&&`。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `backend/models/condition.py` | modify | 加 `PeakDivergenceStrategy` model、進 `StrategyConfig` union、`schema_version` 6→7 |
| `backend/tests/test_condition_strategy.py` | modify | peak schema defaults/discriminator 測;schema_version 斷言改 7 |
| `backend/services/signal_engine.py` | modify | `_peak_state` 狀態、`_eval_peak_divergence`、`_evaluate` 三處 stype 分支、reset |
| `backend/tests/test_signal_engine_peak_divergence.py` | create | 單元(狀態機)+ 整合(`_evaluate` 觸發)測 |
| `backend/scripts/replay_engine.py` | modify | `peak_rule` + `run_peak` + `--preset peak` |
| `bot/src/signal.ts` | modify | `ROLE_ZH` 增 `distribution`、`touchLine` 為 peak 加文案分支 |

---

## Task 1: PeakDivergenceStrategy schema

**Files:**
- Modify: `backend/models/condition.py`(union line 209-212、schema_version line 222)
- Test: `backend/tests/test_condition_strategy.py`

- [ ] **Step 1: 寫失敗測試** — 在 `test_condition_strategy.py` 末尾加,並把既有 `test_schema_version_bumped_to_6` 改成 7:

```python
# 把既有 test_schema_version_bumped_to_6 整個替換成:
def test_schema_version_bumped_to_7():
    assert ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")).schema_version == 7


def test_peak_divergence_strategy_defaults():
    from models.condition import PeakDivergenceStrategy
    f = ActiveFilter(strategy=PeakDivergenceStrategy(type="peak_divergence"))
    assert f.strategy.pullback_pct == 1.0
    assert f.strategy.not_exceed_tolerance_pct == 0.0
    assert f.strategy.volume_shrink_ratio == 0.8
    assert f.strategy.max_gap_minutes == 120
    assert f.strategy.min_main_peak_volume_ratio is None
    assert f.conditions == []                  # strategy-only 允許 conditions 空


def test_peak_divergence_discriminator_from_dict():
    from models.condition import PeakDivergenceStrategy
    f = ActiveFilter.model_validate(
        {"strategy": {"type": "peak_divergence", "pullback_pct": 1.5}}
    )
    assert isinstance(f.strategy, PeakDivergenceStrategy)
    assert f.strategy.pullback_pct == 1.5
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `.venv\Scripts\python -m pytest tests/test_condition_strategy.py -v`
Expected: FAIL — `ImportError: cannot import name 'PeakDivergenceStrategy'` + `test_schema_version_bumped_to_7` AssertionError(目前 6)。

- [ ] **Step 3: 實作** — 在 `condition.py` 的 `BreakoutConfirmStrategy`(line 196-206)之後、`StrategyConfig`(line 209)之前,加:

```python
class PeakDivergenceStrategy(BaseModel):
    """策略 5:雙峰量價背離造山 — 主峰創當日新高 → 收盤回落確認 → 次峰量縮且不過前高 → 滾頭。"""

    type: Literal["peak_divergence"]
    pullback_pct: float = Field(default=1.0, gt=0, le=20)
    not_exceed_tolerance_pct: float = Field(default=0.0, ge=0, le=5)
    volume_shrink_ratio: float = Field(default=0.8, gt=0, le=1.0)
    max_gap_minutes: int = Field(default=120, ge=1, le=240)
    min_main_peak_volume_ratio: float | None = Field(default=None, ge=0.5, le=20.0)
```

把 `StrategyConfig`(line 209-212)改成:

```python
StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy | BreakoutConfirmStrategy | PeakDivergenceStrategy,
    Field(discriminator="type"),
]
```

把 `ActiveFilter.schema_version`(line 222)改成:

```python
    schema_version: int = 7  # 6→7,加 peak_divergence strategy
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `.venv\Scripts\python -m pytest tests/test_condition_strategy.py -v`
Expected: PASS(全部)。

- [ ] **Step 5: Commit**

```bash
git add backend/models/condition.py backend/tests/test_condition_strategy.py
git commit -m "feat(condition): 加 PeakDivergenceStrategy schema(策略5)"
```

---

## Task 2: `_eval_peak_divergence` 單元測試(先寫,全紅)

雙峰是分支互相依賴的狀態機,採「一組測試先定義行為 → Task 3 一次實作」(逐 test 漸進會反覆重寫整個 method)。

**Files:**
- Create: `backend/tests/test_signal_engine_peak_divergence.py`

- [ ] **Step 1: 寫測試檔(含 helper)**

```python
"""驗策略 5:雙峰量價背離造山(吃結算 1 分 K candle)。"""
from datetime import datetime, timedelta, timezone

from models.condition import ActiveFilter, ActiveSignalOut, PeakDivergenceStrategy
from services.signal_engine import MinuteCandle, SignalEngine

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 15, 9, 30, tzinfo=TZ).timestamp()   # 週一開盤後 30 分


def _active(pullback_pct=1.0, volume_shrink_ratio=0.8, not_exceed_tolerance_pct=0.0,
            max_gap_minutes=120, min_main_peak_volume_ratio=None):
    return ActiveSignalOut(
        id="pk", name="造山",
        filter_json=ActiveFilter(strategy=PeakDivergenceStrategy(
            type="peak_divergence", pullback_pct=pullback_pct,
            volume_shrink_ratio=volume_shrink_ratio,
            not_exceed_tolerance_pct=not_exceed_tolerance_pct,
            max_gap_minutes=max_gap_minutes,
            min_main_peak_volume_ratio=min_main_peak_volume_ratio,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-14",
        notify_discord=False,
    )


def _candle(high, close, volume=100, minute=0, low=None, open_=None):
    """造一根 MinuteCandle;high 與 close 可不同以表達峰形(既有 breakout 的 _candle 只平單值)。"""
    o = open_ if open_ is not None else close
    lo = low if low is not None else min(o, close, high)
    return MinuteCandle(minute=minute, open=o, high=high, low=lo, close=close, volume=volume)


def _feed(engine, active, candles):
    """逐根餵 candle,回最後一根結果(其餘忽略)。now = MORNING + minute*60。"""
    strat = engine._strategy_of(active)
    last = None
    for c in candles:
        last = engine._eval_peak_divergence(strat, active, "2330", c, MORNING + c.minute * 60)
    return last


def test_double_peak_with_volume_shrink_fires():
    # 主峰 high110/vol1000(m0) → 收回落 close108(m1) → 次峰 high108/vol500 不過前高(m2) → 滾頭 close106(m3)
    engine = SignalEngine()
    r = _feed(engine, _active(pullback_pct=1.0, volume_shrink_ratio=0.8), [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=108, volume=300, minute=1),   # 108 < 110*0.99=108.9 → 主峰確認、進 pullback
        _candle(high=108, close=108, volume=500, minute=2),   # 次峰 high108<110、vol500<1000*0.8=800
        _candle(high=108, close=106, volume=200, minute=3),   # 106 < 108*0.99=106.92 → 滾頭 + 量縮 → 觸發
    ])
    assert r is not None
    assert r["level"] == "peak"
    assert r["direction"] == "from_above"
    assert r["role"] == "distribution"
    assert r["main_peak_price"] == 110
    assert r["second_peak_price"] == 108
    assert r["volume_shrink"] == 0.5          # 500/1000


def test_second_peak_exceeds_prior_high_no_fire_and_becomes_new_main():
    # 次峰過前高 → 不是做頭、變新主峰;沒有第二次回落 → 不觸發
    engine = SignalEngine()
    active = _active(pullback_pct=1.0)
    r = _feed(engine, active, [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=108, volume=300, minute=1),   # 主峰確認 → pullback
        _candle(high=112, close=112, volume=900, minute=2),   # high112 ≥ 110 → 過前高,當新主峰、回 watch
    ])
    assert r is None
    st = engine._peak_state[(active.id, "2330")]
    assert st["phase"] == "watch"
    assert st["peak1_high"] == 112


def test_volume_not_shrunk_no_fire():
    # 次峰不過前高但量沒縮 → 不觸發(量 900 ≥ 主峰 1000*0.8=800)
    engine = SignalEngine()
    r = _feed(engine, _active(pullback_pct=1.0, volume_shrink_ratio=0.8), [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=108, volume=300, minute=1),   # 主峰確認
        _candle(high=108, close=108, volume=900, minute=2),   # 次峰 vol900 ≥ 800 不算縮
        _candle(high=108, close=106, volume=200, minute=3),   # 滾頭但量沒縮 → 不觸發、重置次峰
    ])
    assert r is None


def test_main_peak_not_confirmed_until_close_pullback():
    # 主峰創高後 close 沒回落足夠 → 仍在 watch,不進 pullback
    engine = SignalEngine()
    active = _active(pullback_pct=1.0)
    _feed(engine, active, [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=109.5, volume=300, minute=1),  # 109.5 > 108.9 → 未確認回落
    ])
    assert engine._peak_state[(active.id, "2330")]["phase"] == "watch"


def test_main_peak_tracks_higher_high_in_watch():
    # watch 階段連續創高 → 主峰跟著漲到最高那根
    engine = SignalEngine()
    active = _active(pullback_pct=1.0)
    _feed(engine, active, [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=115, close=115, volume=1200, minute=1),   # 創更高 → 主峰更新
    ])
    st = engine._peak_state[(active.id, "2330")]
    assert st["peak1_high"] == 115
    assert st["peak1_vol"] == 1200


def test_max_gap_minutes_abandons_stale_main_peak():
    # 主峰後超過 max_gap_minutes 才出現次峰 → 放棄、回 watch
    engine = SignalEngine()
    active = _active(pullback_pct=1.0, max_gap_minutes=5)
    _feed(engine, active, [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=108, volume=300, minute=1),    # pullback,peak1_minute=0
        _candle(high=108, close=108, volume=500, minute=10),   # minute 10-0=10 > 5 → 放棄回 watch
    ])
    assert engine._peak_state[(active.id, "2330")]["phase"] == "watch"


def test_min_main_peak_volume_ratio_gates_main_peak():
    # 主峰那根量不足 → 不鎖主峰(min_main_peak_volume_ratio 門檻)
    engine = SignalEngine()
    engine._day_volume["2330"] = 10000                    # 開盤後 30 分 → avg=10000/30≈333/min
    active = _active(min_main_peak_volume_ratio=3.0)
    _feed(engine, active, [
        _candle(high=110, close=110, volume=500, minute=0),   # vr=500/333≈1.5 < 3.0 → 不鎖主峰
    ])
    assert engine._peak_state[(active.id, "2330")]["peak1_high"] == 0.0


def test_confirmed_latches_no_repeat():
    # 觸發後 phase=confirmed,後續 candle 不再觸發
    engine = SignalEngine()
    active = _active(pullback_pct=1.0, volume_shrink_ratio=0.8)
    _feed(engine, active, [
        _candle(high=110, close=110, volume=1000, minute=0),
        _candle(high=110, close=108, volume=300, minute=1),
        _candle(high=108, close=108, volume=500, minute=2),
        _candle(high=108, close=106, volume=200, minute=3),    # 觸發 → confirmed
    ])
    r2 = _feed(engine, active, [_candle(high=108, close=104, volume=100, minute=4)])
    assert r2 is None
    assert engine._peak_state[(active.id, "2330")]["phase"] == "confirmed"


def test_daily_reset_clears_peak_state():
    engine = SignalEngine()
    engine._peak_state[("x", "2330")] = {"phase": "pullback"}
    engine._reset_daily_strategy_state()
    assert engine._peak_state == {}
```

- [ ] **Step 2: 跑測試驗證失敗**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_peak_divergence.py -v`
Expected: FAIL — `AttributeError: 'SignalEngine' object has no attribute '_eval_peak_divergence'`(以及 `_peak_state` 不存在)。

- [ ] **Step 3: Commit(紅燈測試先入庫)**

```bash
git add backend/tests/test_signal_engine_peak_divergence.py
git commit -m "test(signal): 策略5 雙峰造山單元測試(待實作)"
```

---

## Task 3: `_eval_peak_divergence` 實作 + `_peak_state`

**Files:**
- Modify: `backend/services/signal_engine.py`(`__init__` 92-94 旁、`_reset_daily_strategy_state` 606-624、新增 method)

- [ ] **Step 1: `__init__` 加狀態** — 在 `self._breakout_confirmed`(line 94)那行之後加:

```python
        # 策略5 雙峰造山:per (active.id, symbol) 當日狀態機(daily reset 清)
        self._peak_state: dict[tuple[str, str], dict] = {}
```

- [ ] **Step 2: 新增 `_eval_peak_divergence`** — 加在 `_eval_breakout_confirm`(line 499-548)之後:

```python
    def _eval_peak_divergence(
        self, strat: dict, active: ActiveSignalOut, symbol: str,
        candle: MinuteCandle, now: float,
    ) -> dict | None:
        """策略 5:雙峰量價背離造山。每根結算 candle 餵一次,推進 per-symbol 狀態機。

        主峰 = candle.high 創當日新高(可選量門檻);close 回落 pullback_pct 確認主峰封頂。
        次峰 = 主峰後反彈高點;不過前高 + 量縮 + close 滾頭 → 觸發「做頭轉弱」、當日 latch。
        量縮直接比 raw candle.volume(不經 _candle_volume_ratio,避開 day_volume 重啟偏誤)。
        """
        key = (active.id, symbol)
        st = self._peak_state.get(key)
        if st is None:
            st = {"phase": "watch", "day_high": 0.0,
                  "peak1_high": 0.0, "peak1_vol": 0, "peak1_minute": candle.minute,
                  "trough_low": candle.low, "peak2_high": 0.0, "peak2_vol": 0}
            self._peak_state[key] = st

        pullback = strat["pullback_pct"] / 100.0
        tol = strat["not_exceed_tolerance_pct"] / 100.0
        shrink = strat["volume_shrink_ratio"]
        max_gap = strat["max_gap_minutes"]
        min_vr = strat.get("min_main_peak_volume_ratio")

        is_new_high = candle.high > st["day_high"]
        st["day_high"] = max(st["day_high"], candle.high)

        if st["phase"] == "watch":
            if is_new_high and (
                min_vr is None or self._candle_volume_ratio(symbol, candle, now) >= min_vr
            ):
                st["peak1_high"] = candle.high
                st["peak1_vol"] = candle.volume
                st["peak1_minute"] = candle.minute
            if st["peak1_high"] > 0 and candle.close < st["peak1_high"] * (1 - pullback):
                st["phase"] = "pullback"
                st["trough_low"] = candle.low
                st["peak2_high"] = 0.0
                st["peak2_vol"] = 0
            return None

        if st["phase"] == "pullback":
            st["trough_low"] = min(st["trough_low"], candle.low)
            if candle.minute - st["peak1_minute"] > max_gap:
                st["phase"] = "watch"
                return None
            if candle.high > st["peak2_high"]:
                st["peak2_high"] = candle.high
                st["peak2_vol"] = candle.volume
            if st["peak2_high"] >= st["peak1_high"] * (1 + tol):
                # 過前高 → 不是做頭,次峰當新主峰、回 watch 續找
                st["peak1_high"] = st["peak2_high"]
                st["peak1_vol"] = st["peak2_vol"]
                st["peak1_minute"] = candle.minute
                st["phase"] = "watch"
                return None
            if st["peak2_high"] > 0 and candle.close < st["peak2_high"] * (1 - pullback):
                if st["peak2_vol"] < st["peak1_vol"] * shrink:
                    st["phase"] = "confirmed"
                    return {
                        "level": "peak", "direction": "from_above", "role": "distribution",
                        "main_peak_price": st["peak1_high"],
                        "second_peak_price": st["peak2_high"],
                        "volume_shrink": (round(st["peak2_vol"] / st["peak1_vol"], 2)
                                          if st["peak1_vol"] else 0.0),
                    }
                # 量沒縮:這波反彈不算背離,重置次峰候選續找
                st["peak2_high"] = 0.0
                st["peak2_vol"] = 0
            return None

        return None  # confirmed:當日 latch
```

- [ ] **Step 3: `_reset_daily_strategy_state` 加清除** — 在 `self._breakout_confirmed.clear()`(line 622)之後加:

```python
        self._peak_state.clear()
```

- [ ] **Step 4: 跑測試驗證通過**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_peak_divergence.py -v`
Expected: PASS(全部 9 條)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/signal_engine.py
git commit -m "feat(signal): 策略5 雙峰造山 evaluator + _peak_state"
```

---

## Task 4: `_evaluate` dispatch 三處 + 整合測試

策略5 吃 settled candle,必須在 `_evaluate` 加平行於 `cdp_breakout_confirm` 的分支,並在 cooldown / touch_count 兩段同步加 stype 判斷(recon:三處散落,漏一處行為錯)。

**Files:**
- Modify: `backend/services/signal_engine.py`(`_evaluate` line 320-357)
- Test: `backend/tests/test_signal_engine_peak_divergence.py`(加整合測)

- [ ] **Step 1: 寫整合測試** — 在 `test_signal_engine_peak_divergence.py` 末尾加:

```python
from unittest.mock import MagicMock, patch

import pytest

from services.ring_buffer import Tick


@pytest.mark.asyncio
async def test_evaluate_fires_double_peak_through_fanout():
    """整合:逐 tick 跨分鐘結算 candle → _evaluate → fanout payload 帶 distribution。"""
    engine = SignalEngine()
    active = _active(pullback_pct=1.0, volume_shrink_ratio=0.8)
    engine._active = [active]
    engine._field_cache["2330"] = {}            # scope 閘門:monitor symbol 一律建 entry
    fired = []

    async def fake_broadcast(payload):
        fired.append(payload)

    # 四根 K 的代表 tick(high=close 同根則用兩筆模擬 high 後收低);這裡用每分鐘一筆收盤 tick
    # 跨分鐘結算前一根,故需多餵一筆「下一分鐘」tick 把最後一根結算出來。
    ticks = [
        (110.0, 1000, 0),   # m0 建 candle
        (108.0, 300, 1),    # m1 → 結算 m0(high=close=110/vol1000 主峰),建 m1
        (108.0, 500, 2),    # m2 → 結算 m1(close108 主峰確認 pullback),建 m2
        (106.0, 200, 3),    # m3 → 結算 m2(次峰 high108/vol500),建 m3
        (104.0, 50, 4),     # m4 → 結算 m3(close106 滾頭+量縮 → 觸發)
    ]
    with patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        for price, size, minute in ticks:
            ts = MORNING + minute * 60
            with patch("services.signal_engine.time.time", return_value=ts):
                await engine._evaluate("2330", Tick(price=price, size=size, time=ts))

    assert len(fired) == 1
    assert fired[0]["data"]["cdp_touch"]["role"] == "distribution"
    assert fired[0]["data"]["cdp_touch"]["main_peak_price"] == 110.0
```

> 註:此測用「每分鐘一筆收盤 tick」近似,主峰 candle 的 high=close=110。設計的 `_candle` 單元測已覆蓋 high≠close 的峰形;整合測只驗 dispatch→結算→fanout 串接。

- [ ] **Step 2: 跑測試驗證失敗**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_peak_divergence.py::test_evaluate_fires_double_peak_through_fanout -v`
Expected: FAIL — `len(fired) == 0`(尚無 dispatch 分支,strategy 落到通用 `_eval_strategy` 拿不到 candle)。

- [ ] **Step 3: 加 dispatch 分支** — 在 `_evaluate` 的 `if stype == "cdp_breakout_confirm":`(line 320)區塊之後、`elif strat is not None:`(line 326)之前,插入:

```python
                elif stype == "peak_divergence":
                    if settled is None:
                        continue
                    cdp_touch = self._eval_peak_divergence(strat, active, symbol, settled, now)
                    ma_touch = None
                    ok = cdp_touch is not None
```

- [ ] **Step 4: cooldown touch_level 加分支** — `_evaluate` 的 cooldown 段(line 343-348)目前是:

```python
                if stype is None:
                    touch_level = (cdp_touch or ma_touch or {}).get("level", "")
                elif stype == "cdp_breakout_confirm":
                    touch_level = (cdp_touch or {}).get("level", "")
                else:
                    touch_level = ""
```

peak 無 price level → 走 per 股票(空字串),已落在 `else` 分支,**不需改**。確認 `else: touch_level = ""` 涵蓋 `peak_divergence`。

- [ ] **Step 5: touch_count 排除 peak** — 把 line 357 的:

```python
                if stype != "cdp_breakout_confirm":
```

改成:

```python
                if stype not in ("cdp_breakout_confirm", "peak_divergence"):
```

- [ ] **Step 6: 跑測試驗證通過(含全檔回歸)**

Run: `.venv\Scripts\python -m pytest tests/test_signal_engine_peak_divergence.py -v`
Then: `.venv\Scripts\python -m pytest tests/test_signal_engine_breakout_confirm.py tests/test_signal_engine_breakout_retest.py -v`
Expected: 兩批都 PASS(策略5 觸發 + 既有策略 1/2 不回歸)。

- [ ] **Step 7: Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_peak_divergence.py
git commit -m "feat(signal): 策略5 接入 _evaluate candle dispatch + 整合測"
```

---

## Task 5: 回測 `--preset peak`

照 `breakout` preset 三處改;此 task 無 pytest(replay 跑要登入富邦),驗證靠語法檢查 + 既有 `test_replay_engine.py` 不回歸。

**Files:**
- Modify: `backend/scripts/replay_engine.py`(rule helper 區、preset 掃描函式區、`main`)

- [ ] **Step 1: 加 `peak_rule`** — 在 `breakout_rule`(line 217-231)之後加:

```python
def peak_rule(pullback_pct: float, volume_shrink_ratio: float, day: str):
    """雙峰量價背離造山規則 — peak preset 用。"""
    from models.condition import ActiveFilter, ActiveSignalOut, PeakDivergenceStrategy
    return ActiveSignalOut(
        id="replay", name=f"造山pb={pullback_pct}vs={volume_shrink_ratio}",
        filter_json=ActiveFilter(strategy=PeakDivergenceStrategy(
            type="peak_divergence", pullback_pct=pullback_pct,
            volume_shrink_ratio=volume_shrink_ratio,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
        notify_discord=False,
    )
```

- [ ] **Step 2: 加常數 + `run_peak`** — 在 `run_breakout`(line 319-351)之後加:

```python
PEAK_PULLBACKS = [0.5, 1.0, 1.5]
PEAK_SHRINKS = [0.6, 0.8, 1.0]
PEAK_DETAIL_PB = 1.0
PEAK_DETAIL_VS = 0.8
assert PEAK_DETAIL_PB in PEAK_PULLBACKS
assert PEAK_DETAIL_VS in PEAK_SHRINKS


async def run_peak(days, day_syms, daily, minute, rearm: int):
    """雙峰造山門檻掃描:pullback_pct × volume_shrink_ratio 矩陣 + 碰 CDP baseline。"""
    baseline = {}
    for day in days:
        baseline[day] = await replay_day(
            day, day_syms[day], daily, minute, touch_rule(rearm, day))

    detail_fired = {}

    for vs in PEAK_SHRINKS:
        cols = [f"pb={pb}" for pb in PEAK_PULLBACKS]
        print(f"\n== volume_shrink_ratio={vs} ==")
        print(f"{'day':<12}{'touch':>9}" + "".join(f"{c:>9}" for c in cols))
        totals = [0] * (1 + len(PEAK_PULLBACKS))
        for day in days:
            base_count = sum(baseline[day].values())
            row = [base_count]
            for pb in PEAK_PULLBACKS:
                f = await replay_day(day, day_syms[day], daily, minute,
                                     peak_rule(pb, vs, day))
                row.append(sum(f.values()))
                if vs == PEAK_DETAIL_VS and pb == PEAK_DETAIL_PB and day == days[-1]:
                    detail_fired = f
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{day:<12}" + "".join(f"{c:>9}" for c in row))
        print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))

    last = days[-1]
    print(f"\n-- {last} per-symbol (pb={PEAK_DETAIL_PB} vs={PEAK_DETAIL_VS}) --")
    base = baseline[last]
    print(f"{'sym':<8}{'touch':>6}{'peak':>6}")
    for s in sorted(day_syms[last]):
        print(f"{s:<8}{base.get(s, 0):>6}{detail_fired.get(s, 0):>6}")
```

- [ ] **Step 3: `main` 加 preset** — 把 `--preset` 的 `choices`(line 358)改成含 `"peak"`:

```python
    ap.add_argument("--preset", choices=["touch", "crash", "volume", "breakout", "peak"], default="touch")
```

在 `if args.preset == "breakout":`(line 375-377)區塊之後加:

```python
    if args.preset == "peak":
        await run_peak(days, day_syms, daily, minute, args.rearm)
        return
```

- [ ] **Step 4: 語法 + 回歸驗證**

Run: `.venv\Scripts\python -c "import ast; ast.parse(open('scripts/replay_engine.py', encoding='utf-8').read()); print('OK')"`
Then: `.venv\Scripts\python -m pytest tests/test_replay_engine.py -v`
Expected: `OK` + 既有 replay 測 PASS。

> 真正跑回測(需停 dev server、登入富邦):`.venv\Scripts\python scripts\replay_engine.py --preset peak`。預期目標案例 6207/8064/8150(在 6/12 池)在 per-symbol 表出現非零 peak 觸發。此為手動驗收步驟,不在自動測內。

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/replay_engine.py
git commit -m "feat(replay): 加 --preset peak 雙峰造山門檻掃描"
```

---

## Task 6: bot 圖卡渲染(跨 repo,依開放問題 #1 = bot 增譯)

做頭轉弱不是碰線事件;`role=distribution` / `level=peak` 不在 bot 翻譯表,預設會顯「碰 CDP PEAK」。改 `signal.ts` 讓它顯「📉 做頭轉弱」。WS 前端 + signals_log 不受影響,此 task 為圖卡文案。

**Files:**
- Modify: `bot/src/signal.ts`(`ROLE_ZH` line 55、`touchLine` line 65-72)

- [ ] **Step 1: `ROLE_ZH` 加 distribution** — 把 line 55 改成:

```typescript
const ROLE_ZH: Record<string, string> = { support: "支撐", resistance: "壓力", touch: "觸碰", distribution: "做頭轉弱" };
```

- [ ] **Step 2: `touchLine` 為 peak 加分支** — 在 `touchLine`(line 65)函式開頭(`const parts` 之前)加:

```typescript
  // 雙峰造山「做頭轉弱」不是碰線事件,獨立文案(不走「碰 CDP <LEVEL>」)
  if (t.level === "peak") {
    return `📉 ${t.role ? (ROLE_ZH[t.role] ?? t.role) : "做頭轉弱"}`;
  }
```

- [ ] **Step 3: build / lint 驗證** — 讀 `bot/package.json` 的 scripts 確認命令,在 `bot/` 下執行型別檢查(通常 `npm run build` 或 `npx tsc --noEmit`)。

Run(在 `bot/` 下,實際命令以 package.json 為準): `npx tsc --noEmit`
Expected: 無型別錯誤。

> 安裝依賴若需要:`npm install --no-package-lock`(memory:bot lockfile 漂移,勿用 `npm ci`)。

- [ ] **Step 4: Commit**

```bash
git add bot/src/signal.ts
git commit -m "feat(bot): 圖卡支援策略5 做頭轉弱(distribution role)"
```

---

## Task 7: 全套回歸 + 收尾

- [ ] **Step 1: 後端全測回歸**

Run(在 `backend/` 下): `.venv\Scripts\python -m pytest -v`
Expected: 全綠(策略5 新測 + 既有全部不回歸)。

- [ ] **Step 2: 確認分支 commit 串**

Run: `git log --oneline eb3e404..HEAD`
Expected: 看到 spec(2 筆)+ Task 1-6 各 1 筆,共 ~8 筆,語意清楚。

- [ ] **Step 3: 上線動作(手動,非本 plan 程式碼)**

UI 建「造山」規則(strategy=peak_divergence,預設參數)寫進 `config.json` active_signals,或等回測定參後再建。盤中實機驗:雷科型做頭要推 Discord「做頭轉弱」圖卡。

---

## Self-Review(寫 plan 後對照 spec)

- **Spec coverage:** schema(T1)、狀態機 evaluator(T2/T3)、dispatch 三處(T4)、reset(T3 step3)、回測 preset(T5)、bot 渲染(T6)、目標案例驗收(T5 step4 手動 + T7 step3)— 全覆蓋。spec「不做的事/未來工作」不需 task。
- **量縮判定:** 用 raw `peak1_vol` vs `candle.volume`(T3 code),對齊 spec 收緊後的決定,不經 `_candle_volume_ratio`。
- **三處 stype:** dispatch(T4S3)、cooldown(T4S4 確認 else 已涵蓋)、touch_count(T4S5)— 三處皆處理,符合 recon gotcha。
- **schema_version:** condition.py 6→7(T1S3)+ 測試斷言改 7(T1S1)— 一致。
- **型別一致:** `_eval_peak_divergence` 簽名 `(strat, active, symbol, candle, now)` 與 dispatch 呼叫(T4S3)、單元測呼叫(T2)一致;回傳 dict 的 key(level/direction/role/main_peak_price/second_peak_price/volume_shrink)在 T2 斷言、T4 整合測、spec metadata 節三處一致。
- **無 placeholder:** 各 step 均附完整 code 與確切命令。

---

## Execution Handoff

Plan 已存 `docs/superpowers/plans/2026-06-14-peak-divergence.md`。兩種執行方式:

1. **Subagent-Driven(建議)** — 每個 task 派一個全新 subagent、task 間我做兩段式 review,迭代快。
2. **Inline Execution** — 在本 session 直接逐 task 執行(executing-plans),批次 + checkpoint 審查。

要哪一種?
