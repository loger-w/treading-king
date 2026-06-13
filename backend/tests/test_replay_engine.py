"""replay_engine 的 window 條件回放:餵 ring_buffer 後 price_change_pct 規則要會觸發。

合成資料用 2026-06-12(週五)— _evaluate 的正盤 gate 看(被 patch 的)wall-clock,
時間戳必須落在平日 09:00–13:30。前日 H102/L98/C100 → CDP=100、NH=102、NL=98。
"""
import pytest

import services.ring_buffer as ring_buffer_module
from scripts.replay_engine import (
    replay_day, touch_rule, touch_with_volume_rule, window_rule,
)

DAY = "2026-06-12"
PREV = "2026-06-11"


@pytest.fixture(autouse=True)
def _reset_ring_buffer_singleton():
    """replay_day 會改寫模組單例 _default — 測後歸零,
    不留含歷史 tick 的 buffer 給同 session 的其他測試。"""
    yield
    ring_buffer_module._default = None


def _data(candles):
    daily = {"6207": {PREV: (102.0, 98.0, 100.0)}}
    minute = {"6207": {DAY: candles}}
    return daily, minute


@pytest.mark.asyncio
async def test_crash_rule_fires_on_5min_drop():
    # 5 根 K 從 100 跌到 97.8(−2.2%),300s 窗 lt −2.0 要觸發
    candles = [
        ("09:01", 100.0, 100.0, 100.0, 100.0, 500),
        ("09:02", 99.6, 99.6, 99.4, 99.4, 500),
        ("09:03", 99.0, 99.0, 98.8, 98.8, 500),
        ("09:04", 98.4, 98.4, 98.2, 98.2, 500),
        ("09:05", 98.0, 98.0, 97.8, 97.8, 500),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert fired["6207"] >= 1


@pytest.mark.asyncio
async def test_crash_rule_silent_on_flat_prices():
    candles = [(f"09:0{i}", 100.0, 100.0, 100.0, 100.0, 500) for i in range(1, 6)]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert sum(fired.values()) == 0


def test_replay_rules_never_push_discord():
    # ActiveSignalOut 預設 notify_discord=True;回測規則必須關掉,
    # 否則 bot 在跑時一次回放會推出數百張圖卡
    assert touch_rule(5, DAY).notify_discord is False
    assert window_rule("突爆殺", "lt", -2.0, DAY).notify_discord is False


@pytest.mark.asyncio
async def test_touch_rule_still_fires_after_refactor():
    # 99.8 → 100.0 由下碰 CDP(=100)— 回歸:重構不能弄壞碰線回放
    candles = [
        ("09:01", 99.5, 99.5, 99.5, 99.5, 300),
        ("09:02", 99.8, 100.0, 99.8, 100.0, 300),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute, touch_rule(5, DAY))
    assert fired["6207"] >= 1


# ---------------------------------------------------------------------------
# volume_ratio 引擎邏輯測試
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_volume_ratio_fires_on_high_volume_touch():
    """碰 CDP + 當分鐘量 ≥ 2x 均量 → 觸發。
    10 根 K 各 100 張 → 均量 100/min。第 11 根碰 CDP 且量 300(3x) → 應觸發。"""
    candles = [(f"09:{i:02d}", 99.0, 99.0, 99.0, 99.0, 100) for i in range(1, 11)]
    candles.append(("09:11", 99.8, 100.0, 99.8, 100.0, 300))
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             touch_with_volume_rule(5, 2.0, 0, DAY))
    assert fired["6207"] >= 1


@pytest.mark.asyncio
async def test_volume_ratio_silent_on_low_volume_touch():
    """碰 CDP 但量太低(1x 均量)→ 不觸發(門檻 2x)。"""
    candles = [(f"09:{i:02d}", 99.0, 99.0, 99.0, 99.0, 100) for i in range(1, 11)]
    candles.append(("09:11", 99.8, 100.0, 99.8, 100.0, 100))
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             touch_with_volume_rule(5, 2.0, 0, DAY))
    assert sum(fired.values()) == 0


@pytest.mark.asyncio
async def test_volume_ratio_blocked_by_min_elapsed():
    """開盤 3 分鐘內碰 CDP 且大量，但 min_elapsed=5 → 不觸發。"""
    candles = [
        ("09:01", 99.0, 99.0, 99.0, 99.0, 100),
        ("09:02", 99.0, 99.0, 99.0, 99.0, 100),
        ("09:03", 99.8, 100.0, 99.8, 100.0, 500),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             touch_with_volume_rule(5, 1.5, 5, DAY))
    assert sum(fired.values()) == 0


@pytest.mark.asyncio
async def test_volume_ratio_passes_after_min_elapsed():
    """同樣碰 CDP + 大量，min_elapsed=5 但發生在 09:10 → 觸發。"""
    candles = [(f"09:{i:02d}", 99.0, 99.0, 99.0, 99.0, 100) for i in range(1, 10)]
    candles.append(("09:10", 99.8, 100.0, 99.8, 100.0, 500))
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             touch_with_volume_rule(5, 1.5, 5, DAY))
    assert fired["6207"] >= 1


def test_volume_rule_never_pushes_discord():
    assert touch_with_volume_rule(5, 2.0, 0, DAY).notify_discord is False
