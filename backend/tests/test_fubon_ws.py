"""WSPool 單元測試 — 用 fake ws/fubon,不碰真 SDK。

覆蓋行情唯一入口的核心語意:refcount、容量回滾、訂閱失敗回滾、
殭屍 handle、重連不重綁 handler、isTrial 過濾、訊息解析防護、
circuit/degraded 與 8:25 復位路徑。
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

import services.fubon_ws as fws
import ws_broadcaster as wsb
from services.fubon_client import FubonStatus
from services.fubon_ws import WSPool, WSPoolStatus
from services.ring_buffer import RingBuffer, Tick


class FakeWS:
    """模擬 fugle WebSocketStockClient:on() 累加 handler(與 pyee 同語意)。"""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = defaultdict(list)
        self.subscribe_calls: list[dict] = []
        self.unsubscribe_calls: list[dict] = []
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fail_subscribe = False
        self.fail_connect = False

    def on(self, event, listener):
        self.handlers[event].append(listener)

    def connect(self):
        self.connect_calls += 1
        if self.fail_connect:
            raise RuntimeError("connect boom")

    def disconnect(self):
        self.disconnect_calls += 1

    def subscribe(self, params):
        if self.fail_subscribe:
            raise RuntimeError("subscribe boom")
        self.subscribe_calls.append(params)

    def unsubscribe(self, params):
        self.unsubscribe_calls.append(params)


class FakeFubon:
    def __init__(self, ws: FakeWS) -> None:
        self.status = FubonStatus.OK
        self.set_ws(ws)

    def set_ws(self, ws: FakeWS) -> None:
        """模擬 relogin — 新 SDK 的 ws 是全新實例。"""
        self.sdk = SimpleNamespace(
            marketdata=SimpleNamespace(
                websocket_client=SimpleNamespace(stock=ws)
            )
        )


@pytest.fixture()
def ring(monkeypatch) -> RingBuffer:
    rb = RingBuffer()
    monkeypatch.setattr("services.ring_buffer._default", rb)
    return rb


@pytest.fixture()
def env(monkeypatch, ring):
    """fresh pool + fake ws/fubon。"""
    ws = FakeWS()
    fubon = FakeFubon(ws)
    monkeypatch.setattr(fws, "get_fubon", lambda: fubon)
    pool = WSPool()
    return SimpleNamespace(pool=pool, ws=ws, fubon=fubon, ring=ring)


def _tick_msg(symbol="2330", price=100.0, size=3, **extra) -> str:
    data = {"symbol": symbol, "price": price, "size": size, **extra}
    return json.dumps({"event": "data", "data": data})


# ---------------- refcount ----------------

async def test_refcount_second_owner_no_extra_real_subscribe(env):
    await env.pool.subscribe("2330", owner_id="A")
    await env.pool.subscribe("2330", owner_id="B")
    assert len(env.ws.subscribe_calls) == 1
    assert env.ws.subscribe_calls[0] == {"channel": "trades", "symbols": ["2330"]}


async def test_refcount_unsub_one_owner_keeps_subscription(env):
    await env.pool.subscribe("2330", owner_id="A")
    await env.pool.subscribe("2330", owner_id="B")
    await env.pool.unsubscribe("2330", owner_id="B")
    assert env.ws.unsubscribe_calls == []
    assert env.ring.has("2330")
    # 最後一個 owner 退訂才真退訂 + 清 ring buffer
    await env.pool.unsubscribe("2330", owner_id="A")
    assert len(env.ws.unsubscribe_calls) == 1
    assert not env.ring.has("2330")


# ---------------- 容量 / 失敗回滾 ----------------

async def test_capacity_full_raises_and_rolls_back(env, monkeypatch):
    monkeypatch.setattr(fws, "WS_PER_CONN_CAP", 1)
    await env.pool.subscribe("2330", owner_id="A")
    with pytest.raises(RuntimeError):
        await env.pool.subscribe("2317", owner_id="A")
    assert not env.pool._refcount.get("2317")
    assert "2317" not in env.pool._symbol_to_conn
    assert env.pool.total_subscribed() == 1


async def test_real_subscribe_failure_rolls_back_then_retry_works(env):
    env.ws.fail_subscribe = True
    with pytest.raises(RuntimeError):
        await env.pool.subscribe("2330", owner_id="A")
    # bookkeeping 全回滾 — 不能留下「已訂閱但永遠沒 tick」的殭屍狀態
    assert "2330" not in env.pool._refcount
    assert "2330" not in env.pool._symbol_to_conn
    assert env.pool.total_subscribed() == 0
    assert not env.ring.has("2330")
    # 回滾後重試要能再走真訂閱(不是 need_real_sub=False 假成功)
    env.ws.fail_subscribe = False
    await env.pool.subscribe("2330", owner_id="A")
    assert len(env.ws.subscribe_calls) == 1
    assert env.ring.has("2330")


async def test_connect_failure_leaves_no_zombie_handle(env):
    env.ws.fail_connect = True
    with pytest.raises(RuntimeError):
        await env.pool.subscribe("2330", owner_id="A")
    # connect 失敗的 handle 不能留在 dict — 否則之後 subscribe 全拿死 handle
    assert env.pool._ws_handles == {}
    env.ws.fail_connect = False
    await env.pool.subscribe("2330", owner_id="A")
    assert env.pool.conn_count() == 1


# ---------------- handler 不重綁 ----------------

async def test_reconnect_same_instance_does_not_rewire_handlers(env):
    await env.pool.subscribe("2330", owner_id="A")
    assert len(env.ws.handlers["message"]) == 1
    # 模擬斷線重連:pop handle 後再 ensure(SDK singleton 回同一實例)
    env.pool._ws_handles.pop(0)
    await env.pool._ensure_handle(0)
    assert env.ws.connect_calls == 2
    # 同一實例不重綁 — pyee on() 是累加,重綁會讓每筆 tick 處理 N+1 次
    assert len(env.ws.handlers["message"]) == 1
    assert len(env.ws.handlers["disconnect"]) == 1


async def test_relogin_new_instance_gets_wired_once(env):
    await env.pool.subscribe("2330", owner_id="A")
    new_ws = FakeWS()
    env.fubon.set_ws(new_ws)
    env.pool._ws_handles.pop(0)
    await env.pool._ensure_handle(0)
    assert len(new_ws.handlers["message"]) == 1
    assert new_ws.connect_calls == 1


# ---------------- 訊息解析 ----------------

async def test_message_appends_tick_to_ring_buffer(env):
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._handle_raw_message(_tick_msg(price=101.5, size=7, bid=101.0, ask=102.0))
    t = env.ring.latest("2330")
    assert t is not None
    assert (t.price, t.size, t.bid, t.ask) == (101.5, 7, 101.0, 102.0)


async def test_is_trial_tick_filtered_at_source(env):
    """試撮(集合競價模擬撮合)不是真實成交,不能進 ring buffer/訊號。"""
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._handle_raw_message(_tick_msg(isTrial=True))
    assert env.ring.latest("2330") is None
    # isTrial=False / 缺欄位視為真實成交
    env.pool._handle_raw_message(_tick_msg(isTrial=False))
    assert env.ring.latest("2330") is not None


async def test_malformed_messages_do_not_raise(env):
    """callback 例外外洩進 SDK thread 行為未定義 — 任何爛封包都要吞掉。"""
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._handle_raw_message("not json{{{")
    env.pool._handle_raw_message(json.dumps(["a", "list"]))
    env.pool._handle_raw_message(json.dumps({"event": "heartbeat"}))
    env.pool._handle_raw_message(_tick_msg(symbol=None))
    env.pool._handle_raw_message(_tick_msg(price=None))
    env.pool._handle_raw_message(_tick_msg(price="abc"))     # 規格外型別
    env.pool._handle_raw_message(_tick_msg(size=None))       # null size
    assert env.ring.latest("2330") is None


async def test_tick_dispatched_to_on_tick_callback(env):
    received: list[tuple[str, Tick]] = []

    async def on_tick(symbol: str, tick: Tick) -> None:
        received.append((symbol, tick))

    env.pool.set_tick_callback(on_tick)
    env.pool._loop = asyncio.get_running_loop()
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._handle_raw_message(_tick_msg(price=99.0))
    # call_soon_threadsafe + create_task 要讓 loop 跑兩輪
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert received and received[0][0] == "2330"
    assert received[0][1].price == 99.0


# ---------------- 重連狀態機 ----------------

async def test_schedule_reconnect_dedup_and_stale_guard(env, monkeypatch):
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._loop = asyncio.get_running_loop()
    gate = asyncio.Event()
    calls = []

    async def fake_reconnect(conn_idx: int) -> None:
        calls.append(conn_idx)
        await gate.wait()

    monkeypatch.setattr(env.pool, "_reconnect", fake_reconnect)
    env.pool._schedule_reconnect(0, env.ws)
    env.pool._schedule_reconnect(0, env.ws)   # in-flight 去重
    stale = FakeWS()
    env.pool._schedule_reconnect(0, stale)    # 舊 handle 的斷線事件不觸發重連
    await asyncio.sleep(0)
    assert calls == [0]
    gate.set()
    await asyncio.sleep(0)


async def test_reconnect_restores_subscriptions_and_status(env, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(fws.asyncio, "sleep", no_sleep)
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._ws_handles.pop(0)  # 模擬斷線後 handle 失效
    env.pool._status = WSPoolStatus.DEGRADED
    await env.pool._reconnect(0)
    assert env.ws.connect_calls == 2
    assert {"channel": "trades", "symbols": ["2330"]} in env.ws.subscribe_calls
    assert env.pool.status == WSPoolStatus.OK
    assert env.pool._reconnect_failures == 0


async def test_reconnect_failures_open_circuit_with_alert(env, monkeypatch):
    alerts_sent = []

    async def no_sleep(_):
        return None

    async def fake_alert(msg, **fields):
        alerts_sent.append(msg)

    monkeypatch.setattr(fws.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(fws.alerts, "notify_critical", fake_alert)
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._ws_handles.pop(0)
    env.ws.fail_connect = True
    await env.pool._reconnect(0)
    assert env.pool.status == WSPoolStatus.CIRCUIT_OPEN
    assert env.pool._reconnect_failures == fws.CIRCUIT_OPEN_THRESHOLD
    assert any("circuit breaker" in m for m in alerts_sent)


async def test_degraded_path_sends_alert(env, monkeypatch):
    """connect 成功(計數歸零)但重訂閱一直失敗 → DEGRADED 也要有告警,不能無聲死亡。"""
    alerts_sent = []

    async def no_sleep(_):
        return None

    async def fake_alert(msg, **fields):
        alerts_sent.append(msg)

    monkeypatch.setattr(fws.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(fws.alerts, "notify_critical", fake_alert)
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._ws_handles.pop(0)
    env.ws.fail_subscribe = True  # connect OK、resubscribe 失敗 → 失敗計數每輪被歸零
    await env.pool._reconnect(0)
    assert env.pool.status == WSPoolStatus.DEGRADED
    assert any("degraded" in m for m in alerts_sent)


# ---------------- relogin 復位路徑(overnight 8:25) ----------------

async def test_disconnect_all_then_resubscribe_all_resets_pool(env):
    await env.pool.subscribe("2330", owner_id="A")
    await env.pool.subscribe("2317", owner_id="A")
    env.pool._status = WSPoolStatus.CIRCUIT_OPEN

    await env.pool.disconnect_all()
    assert env.ws.disconnect_calls == 1
    assert env.pool.conn_count() == 0

    # relogin → 新 SDK 新 ws 實例
    new_ws = FakeWS()
    env.fubon.set_ws(new_ws)
    await env.pool.resubscribe_all()
    assert new_ws.connect_calls == 1
    assert len(new_ws.subscribe_calls) == 1
    assert sorted(new_ws.subscribe_calls[0]["symbols"]) == ["2317", "2330"]
    assert len(new_ws.handlers["message"]) == 1
    # 復位 — 這是 CIRCUIT_OPEN/DEGRADED 唯一的自動恢復路徑
    assert env.pool.status == WSPoolStatus.OK


async def test_stale_ws_disconnect_event_after_relogin_no_reconnect(env):
    """舊 ws 被汰換後才觸發 disconnect,不能把新 handle 踢掉重連(churn)。"""
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._loop = asyncio.get_running_loop()
    old_ws = env.ws
    new_ws = FakeWS()
    env.fubon.set_ws(new_ws)
    await env.pool.disconnect_all()
    await env.pool.resubscribe_all()
    # 舊連線此刻才被 server 收掉 → 觸發舊 handler
    old_ws.handlers["disconnect"][0](1006, "going away")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert env.pool._reconnect_tasks == {}
    assert env.pool._ws_handles[0] is new_ws


async def test_shutdown_suppresses_reconnect_spawn(env):
    await env.pool.subscribe("2330", owner_id="A")
    env.pool._loop = asyncio.get_running_loop()
    ws = env.ws
    await env.pool.shutdown()
    assert ws.disconnect_calls == 1
    # shutdown 的 disconnect 會觸發 on_disconnect — closing flag 要擋掉重連
    ws.handlers["disconnect"][0](1000, "bye")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert env.pool._reconnect_tasks == {}


# ---------------- ring buffer race guard ----------------

def test_ring_buffer_append_after_discard_drops_silently(ring):
    ring.ensure("2330")
    ring.discard("2330")
    # unsubscribe race:in-flight tick 在 discard 後到達 → 丟棄,不能 KeyError
    ring.append("2330", Tick(price=1.0, size=1, time=0.0))
    assert ring.latest("2330") is None
    assert ring.window("2330", 60) == []


# ---------------- broadcaster 佇列 ----------------

class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def test_broadcaster_publish_delivers_in_order():
    b = wsb.Broadcaster()
    client = FakeClient()
    await b.add(client)
    b.publish({"event": "tick", "n": 1})
    b.publish({"event": "tick", "n": 2})
    await asyncio.sleep(0.05)
    assert [p["n"] for p in client.sent] == [1, 2]


async def test_broadcaster_queue_full_drops_oldest(monkeypatch):
    monkeypatch.setattr(wsb, "QUEUE_MAXSIZE", 2)
    b = wsb.Broadcaster()
    client = FakeClient()
    await b.add(client)
    # 擋住 consumer 讓佇列塞滿(publish 先排 consumer task 但尚未執行)
    b.publish({"n": 1})
    b.publish({"n": 2})
    b.publish({"n": 3})  # 滿 → 丟最舊的 1
    await asyncio.sleep(0.05)
    assert [p["n"] for p in client.sent] == [2, 3]
    assert b._dropped == 1
