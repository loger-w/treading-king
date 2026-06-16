"""策略 CDP 去重:strategy 規則觸發的 level 抑制同 tick 同 symbol 的 non-strategy 碰線。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import (
    ActiveFilter, ActiveSignalOut, CdpProximityCondition, LimitUpOpenTouchStrategy,
)
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine

POST_OPEN = datetime(2026, 5, 20, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()


def _cdp_rule() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="cdp-rule", name="碰 CDP",
        filter_json=ActiveFilter(
            cdp_proximity=CdpProximityCondition(levels=["ah", "nh"], tolerance_ticks=1),
        ),
        scope={"type": "watchlist"},
        cooldown_seconds=60, enabled=True, created_at="2026-06-16",
    )


def _limit_up_rule() -> ActiveSignalOut:
    return ActiveSignalOut(
        id="limit-up-rule", name="漲停被開碰 CDP",
        filter_json=ActiveFilter(
            strategy=LimitUpOpenTouchStrategy(
                type="limit_up_open_touch", lock_seconds=5,
                levels=["ah", "nh"], tolerance_ticks=1,
            ),
        ),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-16",
    )


async def _evaluate_tick(engine, price, prev_price=None, ts=POST_OPEN):
    """送一筆 tick 進引擎,回傳所有 fanout 的 (rule_name, cdp_level) tuple list。"""
    if prev_price is not None:
        engine._prev_tick["2330"] = Tick(price=prev_price, size=1, time=0.0)

    fired: list[tuple[str, str | None]] = []

    async def capture(payload):
        data = payload.get("data", {})
        fired.append((data.get("active_signal_name"), (data.get("cdp_touch") or {}).get("level")))

    with patch("services.signal_engine.time.time", return_value=ts), \
         patch("services.signal_engine.get_broadcaster") as bc, \
         patch("services.signal_engine.get_signal_writer") as sw:
        bc.return_value.broadcast = capture
        sw.return_value = MagicMock()
        await engine._evaluate("2330", Tick(price=price, size=1, time=ts))

    return fired


def _make_engine(rules):
    engine = SignalEngine()
    engine._active = sorted(rules, key=lambda a: 0 if engine._strategy_of(a) else 1)
    engine._field_cache["2330"] = {
        "prev_close": 500.0,
        "cdp_ah": 540.0,
        "cdp_nh": 530.0,
        "cdp": 520.0,
        "cdp_nl": 510.0,
        "cdp_al": 500.0,
    }
    return engine


@pytest.mark.asyncio
async def test_strategy_dedup_same_level():
    """漲停碰CDP 觸發 AH 後,同 tick 一般碰CDP 不再重複觸發 AH。"""
    engine = _make_engine([_cdp_rule(), _limit_up_rule()])
    # 模擬曾鎖漲停 ≥1s
    engine._limit_lock_best["2330"] = 10.0

    fired = await _evaluate_tick(engine, price=540.0, prev_price=545.0)

    rule_names = [name for name, _ in fired]
    assert "漲停被開碰 CDP" in rule_names
    assert "碰 CDP" not in rule_names


@pytest.mark.asyncio
async def test_strategy_dedup_allows_different_level():
    """漲停碰CDP 觸發 AH 後,一般碰CDP 碰 NH 仍可觸發(不同 level 不去重)。"""
    engine = _make_engine([_cdp_rule(), _limit_up_rule()])
    engine._limit_lock_best["2330"] = 10.0

    # 第一筆: 漲停碰 AH
    await _evaluate_tick(engine, price=540.0, prev_price=545.0, ts=POST_OPEN)

    # 第二筆: 價格跌到 NH(不同 level)
    fired = await _evaluate_tick(engine, price=530.0, prev_price=535.0, ts=POST_OPEN + 300)

    rule_names = [name for name, _ in fired]
    assert "碰 CDP" in rule_names


@pytest.mark.asyncio
async def test_no_dedup_when_strategy_not_firing():
    """漲停規則存在但沒觸發(鎖死不夠久),一般碰CDP 照常觸發。"""
    engine = _make_engine([_cdp_rule(), _limit_up_rule()])
    engine._limit_lock_best["2330"] = 0.0  # 沒鎖過

    fired = await _evaluate_tick(engine, price=540.0, prev_price=545.0)

    rule_names = [name for name, _ in fired]
    assert "碰 CDP" in rule_names
    assert "漲停被開碰 CDP" not in rule_names


@pytest.mark.asyncio
async def test_strategy_rules_sorted_first():
    """refresh_active_signals sort: 策略規則排在前面。"""
    engine = SignalEngine()
    cdp = _cdp_rule()
    lim = _limit_up_rule()
    engine._active = [cdp, lim]
    # 模擬 refresh sort
    engine._active.sort(key=lambda a: 0 if engine._strategy_of(a) else 1)
    assert engine._active[0].id == "limit-up-rule"
    assert engine._active[1].id == "cdp-rule"
