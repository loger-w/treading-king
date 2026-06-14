"""驗策略 5:雙峰量價背離造山(吃結算 1 分 K candle)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, PeakDivergenceStrategy
from services.ring_buffer import Tick
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
