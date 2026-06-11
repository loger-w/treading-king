"""引擎存活性 — 壞 tick / 壞規則 / stale tick 都不能殺死或污染引擎。

24/7 長駐設計下,consumer / heartbeat task 一死訊號就靜默停擺到重啟,
所以這裡的測試都在驗「炸一筆之後還能處理下一筆」。
"""
import asyncio
import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import signal_engine as se
from services.ring_buffer import Tick
from services.signal_engine import SignalEngine


# ---------- consume / heartbeat loop 例外保護 ----------

@pytest.mark.asyncio
async def test_consume_loop_survives_evaluate_exception():
    """第一筆 tick 評估炸掉,consumer 要活著處理第二筆。"""
    engine = SignalEngine()
    engine._evaluate = AsyncMock(side_effect=[RuntimeError("boom"), None])
    now = time.time()
    task = asyncio.create_task(engine._consume_loop())
    await engine.enqueue("2330", Tick(price=1.0, size=1, time=now))
    await engine.enqueue("2330", Tick(price=2.0, size=1, time=now))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert engine._evaluate.await_count == 2


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_evaluate_exception(monkeypatch):
    """heartbeat 評估炸掉,下一輪 heartbeat 仍要繼續評估。"""
    engine = SignalEngine()
    engine._last_field_refill_date = date.today()  # 跳過 daily refill 分支
    engine._field_cache = {"2330": {}}
    engine._active = [MagicMock()]
    engine._evaluate = AsyncMock(side_effect=RuntimeError("boom"))
    fresh = Tick(price=1.0, size=1, time=time.time())
    monkeypatch.setattr(se, "get_ring_buffer",
                        lambda: MagicMock(latest=lambda s: fresh))
    monkeypatch.setattr(se, "HEARTBEAT_INTERVAL_S", 0.01)
    task = asyncio.create_task(engine._heartbeat_loop())
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert engine._evaluate.await_count >= 2


# ---------- heartbeat stale tick 新鮮度 ----------

@pytest.mark.asyncio
async def test_heartbeat_skips_stale_tick(monkeypatch):
    """平日休市日 wall-clock gate 全開,latest 停在前一交易日收盤 tick —
    過舊 tick 不得重評估,否則每 cooldown 重複觸發假訊號整個上午。"""
    engine = SignalEngine()
    engine._last_field_refill_date = date.today()
    engine._field_cache = {"2330": {}}
    engine._active = [MagicMock()]
    engine._evaluate = AsyncMock()
    stale = Tick(price=1.0, size=1, time=time.time() - 7200)  # 2 小時前
    monkeypatch.setattr(se, "get_ring_buffer",
                        lambda: MagicMock(latest=lambda s: stale))
    monkeypatch.setattr(se, "HEARTBEAT_INTERVAL_S", 0.01)
    task = asyncio.create_task(engine._heartbeat_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    engine._evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_evaluates_fresh_tick(monkeypatch):
    """新鮮 tick(正常盤中)不受新鮮度檢查影響。"""
    engine = SignalEngine()
    engine._last_field_refill_date = date.today()
    engine._field_cache = {"2330": {}}
    engine._active = [MagicMock()]
    engine._evaluate = AsyncMock()
    fresh = Tick(price=1.0, size=1, time=time.time())
    monkeypatch.setattr(se, "get_ring_buffer",
                        lambda: MagicMock(latest=lambda s: fresh))
    monkeypatch.setattr(se, "HEARTBEAT_INTERVAL_S", 0.01)
    task = asyncio.create_task(engine._heartbeat_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert engine._evaluate.await_count >= 1


# ---------- 規則載入逐筆容錯 ----------

@pytest.mark.asyncio
async def test_refresh_skips_invalid_rule_keeps_valid(local_store_tmp):
    """一筆壞規則(舊 schema / 匯入檔)只跳過該筆,其餘規則照常生效,
    且不可阻斷啟動路徑(refresh 不能 raise)。"""
    cfg = local_store_tmp.config
    cfg.create_active_signal({
        "name": "good",
        "filter_json": {"conditions": [{"field": "close", "operator": "gt", "value": 100}]},
        "scope": {"type": "watchlist"},
    })
    cfg.create_active_signal({"name": "bad", "filter_json": {}, "scope": {}})

    engine = SignalEngine()
    engine._refill_field_cache = AsyncMock()
    await engine.refresh_active_signals()   # 不該 raise

    assert [a.name for a in engine._active] == ["good"]
    assert engine.health()["invalid_rules"] == 1


@pytest.mark.asyncio
async def test_refresh_invalid_count_resets_when_rows_fixed(local_store_tmp):
    cfg = local_store_tmp.config
    cfg.create_active_signal({
        "name": "good",
        "filter_json": {"conditions": [{"field": "close", "operator": "gt", "value": 100}]},
        "scope": {"type": "watchlist"},
    })
    engine = SignalEngine()
    engine._refill_field_cache = AsyncMock()
    engine._invalid_rules = 3   # 模擬上次載入有壞列
    await engine.refresh_active_signals()
    assert engine.health()["invalid_rules"] == 0


# ---------- degraded 重置 ----------

@pytest.mark.asyncio
async def test_refresh_resets_degraded_flag(local_store_tmp):
    """重載規則 = 操作者已介入 — degraded 要清掉,auto-disable 保護才可再次觸發,
    health 也不會在恢復後永遠顯示 degraded。"""
    engine = SignalEngine()
    engine._refill_field_cache = AsyncMock()
    engine._degraded = True
    engine._lag_violation_started = 123.0
    await engine.refresh_active_signals()
    assert engine._degraded is False
    assert engine._lag_violation_started is None


# ---------- daily reset 補清 ----------

def test_daily_reset_clears_prev_tick_and_prunes_cooldown():
    """_prev_tick 全清(昨日收盤 tick 不該當隔日方向參考);cooldown 只淘汰
    超過上限 86400s 的 key — 跨午夜仍在效期內的 cooldown 要繼續擋。"""
    engine = SignalEngine()
    now = time.time()
    engine._prev_tick["2330"] = Tick(price=1.0, size=1, time=now)
    engine._cooldown[("r1", "2330")] = now - 90000   # 已超過 cooldown 上限
    engine._cooldown[("r2", "2330")] = now - 100     # 仍可能在效期內
    engine._dropped_today = 7

    engine._reset_daily_strategy_state()

    assert engine._prev_tick == {}
    assert ("r1", "2330") not in engine._cooldown
    assert ("r2", "2330") in engine._cooldown
    assert engine._dropped_today == 0
