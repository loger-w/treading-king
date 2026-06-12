"""replay_engine 的 window 條件回放:餵 ring_buffer 後 price_change_pct 規則要會觸發。

合成資料用 2026-06-12(週五)— _evaluate 的正盤 gate 看(被 patch 的)wall-clock,
時間戳必須落在平日 09:00–13:30。前日 H102/L98/C100 → CDP=100、NH=102、NL=98。
"""
import pytest

from scripts.replay_engine import replay_day, touch_rule, window_rule

DAY = "2026-06-12"
PREV = "2026-06-11"


def _data(candles):
    daily = {"6207": {PREV: (102.0, 98.0, 100.0)}}
    minute = {"6207": {DAY: candles}}
    return daily, minute


@pytest.mark.asyncio
async def test_crash_rule_fires_on_5min_drop():
    # 5 根 K 從 100 跌到 97.8(−2.2%),300s 窗 lt −2.0 要觸發
    candles = [
        ("09:01", 100.0, 100.0, 100.0, 100.0),
        ("09:02", 99.6, 99.6, 99.4, 99.4),
        ("09:03", 99.0, 99.0, 98.8, 98.8),
        ("09:04", 98.4, 98.4, 98.2, 98.2),
        ("09:05", 98.0, 98.0, 97.8, 97.8),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert fired["6207"] >= 1


@pytest.mark.asyncio
async def test_crash_rule_silent_on_flat_prices():
    candles = [(f"09:0{i}", 100.0, 100.0, 100.0, 100.0) for i in range(1, 6)]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert sum(fired.values()) == 0


@pytest.mark.asyncio
async def test_touch_rule_still_fires_after_refactor():
    # 99.8 → 100.0 由下碰 CDP(=100)— 回歸:重構不能弄壞碰線回放
    candles = [
        ("09:01", 99.5, 99.5, 99.5, 99.5),
        ("09:02", 99.8, 100.0, 99.8, 100.0),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute, touch_rule(5, DAY))
    assert fired["6207"] >= 1
