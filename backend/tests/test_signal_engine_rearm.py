"""re-arm 狀態機:觸發→抑制→離線→再武裝;horizontal 不推;標記時點在規則整體成立後。

field_cache cdp_nh=100.0 → tick_size(100.0)=0.5,預設 rearm 5 ticks = 離線 2.5 元。
標記走 _evaluate(規則成立才消耗 armed),解除/跳過走 _eval_with_touch_meta。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition, Condition,
    MAProximityCondition,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 6, 12, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()

_T = [0.0]  # 每筆 tick 唯一 time,避免 identity 去重干擾


def _tick(price: float) -> Tick:
    _T[0] += 1.0
    return Tick(price=price, size=1, time=_T[0])


def _active(filter_json: ActiveFilter, cooldown: int = 60) -> ActiveSignalOut:
    return ActiveSignalOut(
        id="a1", name="t", filter_json=filter_json,
        scope={"type": "symbols", "symbols": ["2330"]},
        cooldown_seconds=cooldown, enabled=True, created_at="2026-06-12",
    )


def _cdp_active(cooldown: int = 60, **prox_kwargs) -> ActiveSignalOut:
    kwargs = {"levels": ["nh"], "tolerance_ticks": 0, **prox_kwargs}
    return _active(ActiveFilter(cdp_proximity=CdpProximityCondition(**kwargs)), cooldown)


def _engine(active: ActiveSignalOut, cache: dict | None = None) -> SignalEngine:
    engine = SignalEngine()
    engine._active = [active]
    engine._field_cache["2330"] = cache if cache is not None else {"cdp_nh": 100.0}
    return engine


async def _evaluate(engine: SignalEngine, price: float, prev_price: float | None) -> list:
    """走 _evaluate 完整路徑,回傳本次 fanout 的 touch dicts。"""
    fired: list = []

    async def fake_broadcast(payload):
        fired.append(payload["data"].get("cdp_touch") or payload["data"].get("ma_touch"))

    with patch("services.signal_engine.time.time", return_value=POST_OPEN), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        if prev_price is not None:
            engine._prev_tick["2330"] = _tick(prev_price)
        else:
            engine._prev_tick.pop("2330", None)
        await engine._evaluate("2330", _tick(price))
    return fired


# ---- 標記:規則成立才消耗 armed ----

@pytest.mark.asyncio
async def test_touch_fires_then_suppresses_repeat():
    a = _cdp_active()
    a.cooldown_seconds = 0
    engine = _engine(a)
    f1 = await _evaluate(engine, 100.0, 99.5)
    assert len(f1) == 1 and f1[0]["direction"] == "from_below"
    assert ("a1", "2330", "nh") in engine._prox_suppressed
    # 黏線後再跨一次(99.5→100)— 仍抑制,不推
    f2 = await _evaluate(engine, 100.0, 99.5)
    assert f2 == []


@pytest.mark.asyncio
async def test_and_rule_failed_combine_does_not_consume_armed():
    """AND 組合:碰線但其他條件沒過 → 不發訊號、也不標記抑制,
    否則之後條件補上時第一筆合法訊號會被先前的失敗嘗試吃掉。"""
    f = ActiveFilter(
        logic="AND",
        conditions=[Condition(field="close", operator="gt", value=10000)],  # 必不過
        cdp_proximity=CdpProximityCondition(levels=["nh"], tolerance_ticks=0),
    )
    engine = _engine(_active(f))
    fired = await _evaluate(engine, 100.0, 99.5)
    assert fired == []
    assert engine._prox_suppressed == set()


@pytest.mark.asyncio
async def test_cooldown_blocked_touch_still_consumes_armed():
    """cooldown 擋下的觸發仍要消耗 armed — 黏線時不能等 cooldown 到期又推。"""
    engine = _engine(_cdp_active(cooldown=600))
    f1 = await _evaluate(engine, 100.0, 99.5)
    assert len(f1) == 1
    # 離線 2.5(re-arm)再回頭碰:cooldown 內 → 不推,但重新進入抑制
    await _evaluate(engine, 102.5, 100.0)
    assert ("a1", "2330", "nh") not in engine._prox_suppressed
    f2 = await _evaluate(engine, 100.0, 102.5)
    assert f2 == []
    assert ("a1", "2330", "nh") in engine._prox_suppressed


# ---- 解除 / 跳過(_eval_with_touch_meta 層)----

def test_leaving_less_than_rearm_distance_stays_suppressed():
    a = _cdp_active()
    engine = _engine(a)
    engine._prox_suppressed.add(("a1", "2330", "nh"))
    engine._eval_with_touch_meta(a, "2330", _tick(102.0), _tick(100.0))  # 離線 2.0 < 2.5
    assert ("a1", "2330", "nh") in engine._prox_suppressed


def test_rearm_after_leaving_far_enough_then_retouch_returns_touch():
    a = _cdp_active()
    engine = _engine(a)
    engine._prox_suppressed.add(("a1", "2330", "nh"))
    engine._eval_with_touch_meta(a, "2330", _tick(102.5), _tick(102.0))  # 離線 2.5 = 5 ticks
    assert ("a1", "2330", "nh") not in engine._prox_suppressed
    touch, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), _tick(102.5))
    assert touch == {"level": "nh", "direction": "from_above", "role": "support"}


def test_horizontal_touch_dropped_and_not_suppressed():
    a = _cdp_active()
    engine = _engine(a)
    touch, _ = engine._eval_with_touch_meta(a, "2330", _tick(100.0), None)
    assert touch is None
    assert engine._prox_suppressed == set()


@pytest.mark.asyncio
async def test_rearm_zero_disables_suppression():
    a = _cdp_active(rearm_ticks=0)
    a.cooldown_seconds = 0
    engine = _engine(a)
    f1 = await _evaluate(engine, 100.0, 99.5)
    f2 = await _evaluate(engine, 100.0, 99.5)
    assert len(f1) == 1 and len(f2) == 1
    assert engine._prox_suppressed == set()


@pytest.mark.asyncio
async def test_ma_touch_uses_same_rearm_mechanism():
    a = _active(ActiveFilter(
        ma_proximity=MAProximityCondition(levels=["sma_5"], tolerance_ticks=1),
    ))
    a.cooldown_seconds = 0
    engine = _engine(a, cache={"sma_5": 100.0})
    f1 = await _evaluate(engine, 100.0, 99.0)
    assert len(f1) == 1
    assert ("a1", "2330", "sma_5") in engine._prox_suppressed
    f2 = await _evaluate(engine, 100.0, 99.0)
    assert f2 == []


def test_daily_reset_clears_suppressed():
    engine = _engine(_cdp_active())
    engine._prox_suppressed.add(("a1", "2330", "nh"))
    engine._reset_daily_strategy_state()
    assert engine._prox_suppressed == set()


# ---- effective rearm 解析(None → max(預設, tolerance+1))----

def test_effective_rearm_resolution():
    eff = SignalEngine._effective_rearm_ticks
    assert eff({"tolerance_ticks": 0}) == 5          # 預設
    assert eff({"tolerance_ticks": 7}) == 8          # tolerance ≥ 預設 → 墊高,保證可 re-arm
    assert eff({"tolerance_ticks": 7, "rearm_ticks": 10}) == 10  # 顯式值優先
    assert eff({"rearm_ticks": 0}) == 0              # 顯式關閉
    assert eff(CdpProximityCondition(tolerance_ticks=9)) == 10   # pydantic 模型同邏輯


# ---- cooldown 粒度 ----

@pytest.mark.asyncio
async def test_cooldown_is_per_level_not_per_symbol():
    """proximity:碰 NH 觸發後,cooldown 內碰 CDP 線要照樣觸發。"""
    a = _cdp_active(cooldown=600, levels=["nh", "cdp"], rearm_ticks=0)
    engine = _engine(a, cache={"cdp_nh": 100.0, "cdp": 95.0})
    f1 = await _evaluate(engine, 100.0, 99.5)   # 碰 NH
    f2 = await _evaluate(engine, 95.0, 95.5)    # 碰 CDP(cooldown 內)
    f3 = await _evaluate(engine, 100.0, 99.5)   # 再碰 NH → 被擋
    assert [t["level"] for t in f1 + f2 + f3] == ["nh", "cdp"]


@pytest.mark.asyncio
async def test_strategy_cooldown_stays_per_symbol():
    """strategy 類(漲停打開等)維持舊行為:一個 cooldown 窗只推一次,
    不因回落連穿多條線而每線各推一發;且不進 re-arm 抑制。"""
    from models.condition import LimitUpOpenTouchStrategy
    a = _active(ActiveFilter(
        strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch"),
    ), cooldown=600)
    engine = _engine(a)
    touches = [
        {"level": "nh", "direction": "from_above", "role": "support"},
        {"level": "cdp", "direction": "from_above", "role": "support"},
    ]
    with patch.object(SignalEngine, "_eval_strategy", side_effect=touches):
        f1 = await _evaluate(engine, 100.0, 100.5)
        f2 = await _evaluate(engine, 95.0, 95.5)
    assert len(f1) == 1 and f1[0]["level"] == "nh"
    assert f2 == []                                # cooldown per 股票,第二發被擋
    assert engine._prox_suppressed == set()        # strategy 不套 re-arm
