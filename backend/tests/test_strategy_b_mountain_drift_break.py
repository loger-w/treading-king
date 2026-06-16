"""驗策略 B：造山後無力緩跌跌破支撐。"""
import math

from models.condition import ActiveFilter, ActiveSignalOut, MountainDriftBreakStrategy
from services.signal_engine import MinuteCandle, SignalEngine

NL = 95.0
AL = 90.0
CDP_MID = 97.0
NH = 100.0


def _active(drift_bars=5, drift_ratio=0.6, break_confirm_bars=2,
            levels=("nl",), tolerance_pct=0.0, require_below_vwap=False):
    return ActiveSignalOut(
        id="db", name="造山緩跌",
        filter_json=ActiveFilter(strategy=MountainDriftBreakStrategy(
            type="mountain_drift_break", levels=list(levels),
            drift_bars=drift_bars, drift_ratio=drift_ratio,
            break_confirm_bars=break_confirm_bars,
            tolerance_pct=tolerance_pct,
            require_below_vwap=require_below_vwap,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-17",
        notify_discord=False,
    )


def _engine():
    e = SignalEngine()
    e._field_cache["2330"] = {
        "cdp_nh": NH, "cdp_nl": NL, "cdp_al": AL, "cdp": CDP_MID,
    }
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


def test_no_signal_without_mountain():
    engine = _engine()
    active = _active()
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94, minute=10), 600)
    assert r is None


def test_mountain_not_confirmed_returns_none():
    engine = _engine()
    engine._mountain_state["2330"] = {
        "phase": "surge_tracking", "recent_closes": [],
        "peak_high": 110.0, "peak_vr": 2.0, "peak_minute": 5,
        "confirmed_minute": 0, "no_new_high_count": 0,
    }
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1)
    strat = engine._strategy_of(active)
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94, minute=10), 600)
    assert r is None


def test_drift_window_not_full_returns_none():
    """drift_bars=5 需要至少 6 根 candle（5 根比較 + 第一根設 prev_close）。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=5, drift_ratio=0.6, break_confirm_bars=1)
    strat = engine._strategy_of(active)
    closes = [99, 98, 97, 96]  # 只有 4 根,窗口不足
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
        assert r is None


def test_drift_confirmed_and_break():
    """drift_bars=5, drift_ratio=0.6 → 需 ceil(3)=3 根下跌。全 5 根遞減 + 跌破 NL。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=5, drift_ratio=0.6, break_confirm_bars=1)
    strat = engine._strategy_of(active)
    # 6 根 candle: 第一根設 prev_close,後 5 根都 close < prev_close,最後跌破 NL=95
    closes = [99, 98, 97, 96, 95.5, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is not None
    assert r["level"] == "nl"
    assert r["direction"] == "from_above"
    assert r["role"] == "mountain_drift_break"
    assert r["drift_down_count"] == 5
    assert r["break_confirm"] == 1
    assert r["peak_high"] == 110.0


def test_break_confirm_bars_2():
    """需要 2 根連續跌破確認。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=5, drift_ratio=0.6, break_confirm_bars=2)
    strat = engine._strategy_of(active)
    # 建立 drift: 6 根遞減到剛好在 NL 上方
    closes = [99, 98, 97, 96, 95.5, 95.2]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is None  # 都在 NL(95) 以上,沒跌破
    # 第一根跌破
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94.5, minute=6), 360)
    assert r is None  # break_confirm=1, 需要 2
    # 第二根跌破
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94.0, minute=7), 420)
    assert r is not None
    assert r["break_confirm"] == 2


def test_drift_not_met_resets_break_count():
    """drift 不成立時 break_confirm_count 歸零。
    drift_bars=3, drift_ratio=0.9 → ceil(2.7)=3, 需要 3 根全跌。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=3, drift_ratio=0.9, break_confirm_bars=2)
    strat = engine._strategy_of(active)
    # 4 根遞減 → drift window [T,T,T] = 3/3 OK
    for i, c in enumerate([99, 98, 97, 94.5]):
        engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    # break_confirm = 1 (94.5 < 95)
    # 反彈一根: close=95.5 > prev=94.5 → drift window [T,T,False] = 2/3 < 3 不及格
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(95.5, minute=4), 240)
    assert r is None
    # 再跌: close=94 < prev=95.5 → drift window [T,False,T] = 2/3 < 3 仍不及格
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94, minute=5), 300)
    assert r is None  # drift 不成立,即使跌破 NL


def test_tolerance_pct():
    """tolerance_pct 收緊跌破判定（threshold 更低,close 要更低才算跌破）。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    # NL=95, tolerance=0.5% → threshold = 95 * (1 - 0.005) = 94.525
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1, tolerance_pct=0.5)
    strat = engine._strategy_of(active)
    # 94.6 > threshold(94.525) → 不算跌破
    closes_no_break = [99, 98, 97, 94.6]
    for i, c in enumerate(closes_no_break):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is None
    # 94.4 < threshold(94.525) → 算跌破
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94.4, minute=4), 240)
    assert r is not None


def test_require_below_vwap_blocks():
    """require_below_vwap=True + close >= vwap → 不觸發。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 93.0  # 很低的 VWAP
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1,
                     require_below_vwap=True)
    strat = engine._strategy_of(active)
    # 遞減到 94.5 但 > vwap(93) → 不觸發
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is None


def test_require_below_vwap_allows():
    """require_below_vwap=True + close < vwap → 觸發。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 96.0
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1,
                     require_below_vwap=True)
    strat = engine._strategy_of(active)
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is not None


def test_require_below_vwap_no_vwap_blocks():
    """require_below_vwap=True + VWAP 不存在 → 不觸發。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1,
                     require_below_vwap=True)
    strat = engine._strategy_of(active)
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is None


def test_multiple_levels():
    """多條支撐線獨立判斷。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1,
                     levels=("nl", "al"))
    strat = engine._strategy_of(active)
    # 遞減到 94.5 < NL(95) 但 > AL(90)
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is not None
    assert r["level"] == "nl"  # NL 觸發,AL 不觸發


def test_break_count_reset_on_close_above():
    """跌破後站回線上 → break_confirm_count 歸零。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=5, drift_ratio=0.6, break_confirm_bars=2)
    strat = engine._strategy_of(active)
    # 建立 drift
    closes = [99, 98, 97, 96, 95.5, 94.5]
    for i, c in enumerate(closes):
        engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    # 一根跌破 NL (break_confirm=1)
    # 站回 NL 上方
    engine._eval_mountain_drift_break(strat, active, "2330", _candle(95.5, minute=6), 360)
    # 再跌破 — 重新計數
    r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(94.5, minute=7), 420)
    # drift 窗口 [T,T,T,T,F,T] 最近5 = [T,T,F,T,T] = 3/5=60% 剛好及格
    # break_confirm = 1 (剛重置後的第一根)
    assert r is None  # 需要 2 根


def test_below_vwap_metadata():
    """signal output 含 below_vwap metadata。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    engine._field_cache["2330"]["vwap"] = 96.0
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1)
    strat = engine._strategy_of(active)
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is not None
    assert r["below_vwap"] is True


def test_drift_ratio_ceil():
    """drift_ratio 用 ceil 計算門檻。drift_bars=5, ratio=0.6 → ceil(3.0) = 3。"""
    threshold = math.ceil(5 * 0.6)
    assert threshold == 3
    # drift_bars=7, ratio=0.6 → ceil(4.2) = 5
    threshold2 = math.ceil(7 * 0.6)
    assert threshold2 == 5


def test_can_retrigger_same_level():
    """同一 level 可重複觸發（跟策略 A 不同，不加 fired set）。"""
    engine = _engine()
    _set_mountain_confirmed(engine)
    active = _active(drift_bars=3, drift_ratio=0.5, break_confirm_bars=1)
    strat = engine._strategy_of(active)
    # 第一次觸發
    closes = [99, 98, 97, 94.5]
    for i, c in enumerate(closes):
        r = engine._eval_mountain_drift_break(strat, active, "2330", _candle(c, minute=i), i * 60)
    assert r is not None
    # 繼續跌 → 第二次觸發
    r2 = engine._eval_mountain_drift_break(strat, active, "2330", _candle(93.5, minute=4), 240)
    assert r2 is not None
