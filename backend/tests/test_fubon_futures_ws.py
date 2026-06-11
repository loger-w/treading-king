from __future__ import annotations
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import services.fubon_futures_ws as fws
from services.fubon_futures_ws import (
    MAX_RECONNECT_ATTEMPTS,
    FuturesWSPool,
    reconcile_pool,
    target_after_hours_flag,
)

TPE = ZoneInfo("Asia/Taipei")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("2026-05-25T10:00:00", False),  # day → afterHours=False
        ("2026-05-25T22:00:00", True),   # night → afterHours=True
        ("2026-05-26T03:00:00", True),   # 跨日夜盤
        ("2026-05-29T22:00:00", True),   # 週五夜盤照開(期交所盤後交易)
        ("2026-05-30T03:00:00", True),   # 週六凌晨仍是週五夜盤
        ("2026-05-25T14:00:00", None),   # 休市 → 不訂閱
        ("2026-05-30T10:00:00", None),   # 週六 05:00 後 → 不訂閱
        ("2026-05-25T13:45:00", None),   # 收盤瞬間 → 不訂閱
        ("2026-05-25T08:44:00", None),   # 開盤前休市 → 不訂閱
    ],
)
def test_target_after_hours_flag(iso: str, expected):
    assert target_after_hours_flag(dt(iso)) == expected


# ---- FuturesWSPool._handle_message ----
# SDK 的 message event 給的是原始 JSON 字串(emit 前不 parse),
# pool 必須 json.loads 才不會把所有推送靜默丟掉;dict 餵法保持相容。


class _FakeBroadcaster:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def broadcast(self, payload: dict) -> None:
        self.payloads.append(payload)


@pytest.fixture
def broadcaster(monkeypatch) -> _FakeBroadcaster:
    b = _FakeBroadcaster()
    monkeypatch.setattr("ws_broadcaster.get_broadcaster", lambda: b)
    return b


def _candle_message(symbol: str = "MXFF6") -> dict:
    return {
        "event": "data",
        "channel": "candles",
        "data": {
            "symbol": symbol,
            "date": "2026-05-25T10:00:00+08:00",
            "open": 17000.0,
            "high": 17010.0,
            "low": 16990.0,
            "close": 17005.0,
            "volume": 12,
            "average": 17001.0,
        },
    }


def _pool(symbol: str = "MXFF6") -> FuturesWSPool:
    pool = FuturesWSPool()
    pool._symbol = symbol
    return pool


async def test_handle_message_parses_json_string(broadcaster):
    pool = _pool()
    await pool._handle_message(json.dumps(_candle_message()))
    assert len(broadcaster.payloads) == 1
    out = broadcaster.payloads[0]
    assert out["event"] == "mxf_candle"
    assert out["data"]["symbol"] == "MXFF6"
    assert out["data"]["candle"]["close"] == 17005.0
    assert out["data"]["candle"]["volume"] == 12


async def test_handle_message_accepts_dict(broadcaster):
    pool = _pool()
    await pool._handle_message(_candle_message())
    assert len(broadcaster.payloads) == 1
    assert broadcaster.payloads[0]["data"]["candle"]["date"] == "2026-05-25T10:00:00+08:00"


async def test_handle_message_drops_other_symbol(broadcaster):
    pool = _pool("MXFF6")
    await pool._handle_message(json.dumps(_candle_message(symbol="MXFG6")))
    assert broadcaster.payloads == []


async def test_handle_message_drops_garbage_without_raising(broadcaster):
    pool = _pool()
    await pool._handle_message("not json {{{")          # 壞 JSON
    await pool._handle_message(json.dumps([1, 2, 3]))   # 合法 JSON 但非 dict
    await pool._handle_message({"event": "heartbeat"})  # 無 data 欄位
    assert broadcaster.payloads == []


# ---- FuturesWSPool 主動 teardown 不應觸發 reconnect ----
# SDK 對主動 close 也會 fire disconnect 事件,沒擋掉的話每次 session 切換
# 都會把剛建好的訂閱狀態清掉再繞一圈斷線重連。


async def test_deliberate_teardown_disconnect_is_ignored():
    pool = _pool()
    pool._ws = SimpleNamespace(disconnect=lambda: None)
    pool._current_after_hours = False
    reconnects: list[int] = []

    async def fake_reconnect() -> None:
        reconnects.append(1)

    pool._schedule_reconnect = fake_reconnect  # type: ignore[method-assign]

    await pool._teardown_ws()
    # 主動 close 引發的 disconnect 事件 → 必須被吃掉,不排 reconnect
    await pool._handle_disconnect()
    assert reconnects == []

    # 之後的真斷線(沒有 pending 的主動 teardown)仍要重連
    await pool._handle_disconnect()
    assert reconnects == [1]


async def test_teardown_failure_restores_expected_disconnect_count():
    def boom() -> None:
        raise RuntimeError("socket already dead")

    pool = _pool()
    pool._ws = SimpleNamespace(disconnect=boom)
    await pool._teardown_ws()
    # close 失敗 → 事件多半不會來,計數須補回,免得吞掉下次真斷線
    assert pool._expected_disconnects == 0
    assert pool._ws is None


# ---- reconnect 上限與 reconcile 重置 ----


async def test_schedule_reconnect_gives_up_at_max_and_reconcile_resets(monkeypatch):
    pool = _pool()
    ensured: list[int] = []

    async def fake_ensure() -> None:
        ensured.append(1)

    pool._ensure_subscribed_for_now = fake_ensure  # type: ignore[method-assign]

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(fws.asyncio, "sleep", no_sleep)

    pool._reconnect_attempt = MAX_RECONNECT_ATTEMPTS
    await pool._schedule_reconnect()
    assert ensured == []  # 達上限 → 放棄,不再 hot retry

    await pool.reconcile_session()  # reconcile 重置計數(本身會 ensure 一次)
    assert pool._reconnect_attempt == 0
    await pool._schedule_reconnect()
    assert len(ensured) == 2


# ---- reconcile_pool:近月 symbol 對齊(換月/startup 解析失敗補救)----


def _record_calls(pool: FuturesWSPool, monkeypatch):
    calls: list[tuple] = []

    async def fake_start(symbol: str) -> None:
        calls.append(("start", symbol))

    async def fake_reconcile() -> None:
        calls.append(("reconcile",))

    monkeypatch.setattr(pool, "start", fake_start)
    monkeypatch.setattr(pool, "reconcile_session", fake_reconcile)
    return calls


async def test_reconcile_pool_switches_to_new_symbol(monkeypatch):
    pool = _pool("MXFF6")
    calls = _record_calls(pool, monkeypatch)

    async def fake_resolve():
        return "MXFG6"  # 換月後的新近月

    monkeypatch.setattr(fws, "resolve_active_symbol", fake_resolve)
    await reconcile_pool(pool)
    assert calls == [("start", "MXFG6")]


async def test_reconcile_pool_same_symbol_only_reconciles_session(monkeypatch):
    pool = _pool("MXFF6")
    calls = _record_calls(pool, monkeypatch)

    async def fake_resolve():
        return "MXFF6"

    monkeypatch.setattr(fws, "resolve_active_symbol", fake_resolve)
    await reconcile_pool(pool)
    assert calls == [("reconcile",)]


async def test_reconcile_pool_keeps_symbol_when_resolve_fails(monkeypatch):
    pool = _pool("MXFF6")
    calls = _record_calls(pool, monkeypatch)

    async def fake_resolve():
        return None  # SDK 掛/查不到 → 不動既有訂閱

    monkeypatch.setattr(fws, "resolve_active_symbol", fake_resolve)
    await reconcile_pool(pool)
    assert calls == [("reconcile",)]
    assert pool._symbol == "MXFF6"


async def test_reconcile_pool_recovers_from_startup_resolve_failure(monkeypatch):
    # startup 時 resolve 失敗 → _symbol=None;下一輪 reconcile 必須補上
    pool = FuturesWSPool()
    calls = _record_calls(pool, monkeypatch)

    async def fake_resolve():
        return "MXFF6"

    monkeypatch.setattr(fws, "resolve_active_symbol", fake_resolve)
    await reconcile_pool(pool)
    assert calls == [("start", "MXFF6")]
