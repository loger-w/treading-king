"""驗策略：CDP 突破確認（站穩）。"""
from datetime import datetime, timedelta, timezone

from models.condition import ActiveFilter, ActiveSignalOut, BreakoutConfirmStrategy
from services.signal_engine import MinuteCandle, SignalEngine

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 15, 9, 20, tzinfo=TZ).timestamp()  # 週一

NH = 100.0


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
    assert r1 is None
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
    engine._eval_breakout_confirm(strat, active, "2330", _candle(99.0, minute=1), 60)
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=2), 120)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=3), 180)
    assert r is None


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
    engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(102.0, minute=1), 60)
    assert r is not None
    assert r["direction"] == "from_below"


def test_margin_ticks():
    engine = _engine()
    active = _active(confirm_bars=1, direction="above", margin_ticks=1)
    strat = engine._strategy_of(active)
    r_exact = engine._eval_breakout_confirm(strat, active, "2330", _candle(100.5, minute=0), 0)
    assert r_exact is None
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
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(103.0, minute=2), 120)
    assert r is None


def test_multiple_levels_fire_independently():
    engine = _engine()
    active = _active(confirm_bars=1, direction="above", levels=("nh", "ah"))
    strat = engine._strategy_of(active)
    results = engine._eval_breakout_confirm(strat, active, "2330", _candle(106.0, minute=0), 0)
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


# ---------- 量能確認 ----------

def test_volume_ratio_blocks_first_bar():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    # 09:20 = 開盤後 20 分, avg=500/min, candle vol=800 → ratio=1.6 < 2.0
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(101.0, volume=800, minute=0), MORNING)
    assert r is None


def test_volume_ratio_allows_first_bar():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    # 09:20 = 開盤後 20 分, avg=500/min, candle vol=1200 → ratio=2.4 ≥ 2.0
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(101.0, volume=1200, minute=0), MORNING)
    assert r is not None


def test_volume_not_checked_on_subsequent_bars():
    engine = _engine()
    engine._day_volume["2330"] = 10000
    active = _active(confirm_bars=2, direction="above", min_volume_ratio=2.0)
    strat = engine._strategy_of(active)
    # 首根: ratio OK
    engine._eval_breakout_confirm(strat, active, "2330",
                                   _candle(101.0, volume=1200, minute=0), MORNING)
    # 第 2 根: volume 低(ratio=0.2),但不檢查 → 仍觸發
    r = engine._eval_breakout_confirm(strat, active, "2330",
                                       _candle(102.0, volume=100, minute=1), MORNING + 60)
    assert r is not None


def test_volume_ratio_none_skips_check():
    engine = _engine()
    engine._day_volume["2330"] = 0
    active = _active(confirm_bars=1, direction="above", min_volume_ratio=None)
    strat = engine._strategy_of(active)
    r = engine._eval_breakout_confirm(strat, active, "2330", _candle(101.0, minute=0), 0)
    assert r is not None


# ---------- 整合測試 ----------

from unittest.mock import MagicMock, patch

import pytest
from services.ring_buffer import Tick


@pytest.mark.asyncio
async def test_evaluate_integration_fires_on_candle_settlement():
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
        await engine._evaluate("2330", Tick(101.0, 100, min0))
        assert len(fired) == 0

        with patch("services.signal_engine.time.time", return_value=min1):
            await engine._evaluate("2330", Tick(102.0, 50, min1))
        assert len(fired) == 1
        assert fired[0]["data"]["cdp_touch"]["role"] == "breakout"
