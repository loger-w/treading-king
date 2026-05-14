"""富邦 WS 連線池 — refcount registry（兩 owner 同 symbol 取消其一不互踩）。

重要：富邦 WS callback 是 sync (在 fubon SDK thread)，要 bridge 到 asyncio。
我們在 startup 時 cache main loop reference，sync callback 用 loop.call_soon_threadsafe
把 tick 寫進 ring_buffer + signal_engine queue。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from enum import Enum
from typing import Any, Awaitable, Callable

from services import alerts
from services.fubon_client import FubonStatus, get_fubon
from services.ring_buffer import Tick, get_ring_buffer
from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)

# 富邦官方規格 (https://www.fbs.com.tw/TradeAPI/en/docs/market-data/rate-limit/):
#   - WebSocket 每連線最多 200 subscriptions
#   - WebSocket 每帳號最多 5 connections
# 但 SDK 的 websocket_client.stock 是 process-level singleton。
# 同 process 內取多次回同一個 ws instance。
# 要拿到富邦允許的 5 connections 必須 multi-process 架構 (5 個獨立 SDK login)。
# 目前單 process 真實容量 = 1 × 200 = 200。
# MAX_CONNS=1 反映 SDK 真實限制 — 避免 _pick_conn_with_capacity 誤判把 sub 分到
# 不同 idx 但實際同一條 ws，造成 server silent reject (error 1001) SDK 又 drop。
WS_PER_CONN_CAP = 200
MAX_CONNS = 1
RECONNECT_DELAYS = (1, 2, 4, 8, 16, 30, 60)  # exponential backoff，cap 60
CIRCUIT_OPEN_THRESHOLD = 5  # 連續失敗次數


class WSPoolStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"


class WSPool:
    """單例。Owner 是字串（"watchlist" / active_signal_id 等），refcount 解 R1。"""

    def __init__(self) -> None:
        # symbol → set of owner_id
        self._refcount: dict[str, set[str]] = defaultdict(set)
        # symbol → 屬於哪條 WS connection (0..MAX_CONNS-1)
        self._symbol_to_conn: dict[str, int] = {}
        # connection idx → list of subscribed symbols
        self._conn_subs: dict[int, set[str]] = defaultdict(set)
        # ws handles (lazy create)
        self._ws_handles: dict[int, Any] = {}
        self._lock = asyncio.Lock()
        self._status = WSPoolStatus.OK
        self._reconnect_failures = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_tick: Callable[[str, Tick], Awaitable[None]] | None = None

    @property
    def status(self) -> WSPoolStatus:
        return self._status

    def total_subscribed(self) -> int:
        return sum(len(s) for s in self._conn_subs.values())

    def conn_count(self) -> int:
        return len(self._ws_handles)

    def set_tick_callback(self, fn: Callable[[str, Tick], Awaitable[None]]) -> None:
        """signal_engine.start() 時呼叫，註冊 tick 處理 callback。"""
        self._on_tick = fn

    async def start(self) -> None:
        """lifespan startup 時呼叫。"""
        self._loop = asyncio.get_running_loop()
        logger.info("WSPool started, capacity=%d", MAX_CONNS * WS_PER_CONN_CAP)

    async def subscribe(self, symbol: str, owner_id: str) -> None:
        """加 owner；refcount 0→1 才真打富邦訂閱。"""
        async with self._lock:
            if symbol in self._refcount and owner_id in self._refcount[symbol]:
                return  # already
            need_real_sub = symbol not in self._symbol_to_conn
            self._refcount[symbol].add(owner_id)
            if need_real_sub:
                conn_idx = self._pick_conn_with_capacity()
                if conn_idx is None:
                    self._refcount[symbol].discard(owner_id)
                    raise RuntimeError(
                        f"WS pool capacity full ({MAX_CONNS * WS_PER_CONN_CAP})"
                    )
                self._symbol_to_conn[symbol] = conn_idx
                self._conn_subs[conn_idx].add(symbol)
                # ring_buffer 一定要 ensure 在 sub 之前，才不會 callback 拿不到 lock
                get_ring_buffer().ensure(symbol)
                await self._real_subscribe(conn_idx, [symbol])

    async def unsubscribe(self, symbol: str, owner_id: str) -> None:
        async with self._lock:
            owners = self._refcount.get(symbol)
            if not owners or owner_id not in owners:
                return
            owners.discard(owner_id)
            if owners:
                return  # still has other owners
            # last owner — really unsubscribe
            del self._refcount[symbol]
            conn_idx = self._symbol_to_conn.pop(symbol, None)
            if conn_idx is not None:
                self._conn_subs[conn_idx].discard(symbol)
                await self._real_unsubscribe(conn_idx, [symbol])
            get_ring_buffer().discard(symbol)

    def _pick_conn_with_capacity(self) -> int | None:
        for idx in range(MAX_CONNS):
            if len(self._conn_subs.get(idx, set())) < WS_PER_CONN_CAP:
                return idx
        return None

    # --------- 真打富邦的 sync 動作（asyncio.to_thread wrap） ---------

    async def _real_subscribe(self, conn_idx: int, symbols: list[str]) -> None:
        ws = await self._ensure_handle(conn_idx)
        if ws is None:
            return
        try:
            await asyncio.to_thread(
                ws.subscribe, {"channel": "trades", "symbols": symbols}
            )
            logger.info("conn[%d] subscribed: %s (total=%d)", conn_idx, symbols, len(self._conn_subs[conn_idx]))
        except Exception as e:
            logger.error("subscribe failed conn[%d] symbols=%s: %s", conn_idx, symbols, e)

    async def _real_unsubscribe(self, conn_idx: int, symbols: list[str]) -> None:
        ws = self._ws_handles.get(conn_idx)
        if ws is None:
            return
        try:
            await asyncio.to_thread(
                ws.unsubscribe, {"channel": "trades", "symbols": symbols}
            )
            logger.info("conn[%d] unsubscribed: %s", conn_idx, symbols)
        except Exception as e:
            logger.warning("unsubscribe failed (ignored) conn[%d] %s: %s", conn_idx, symbols, e)

    async def _ensure_handle(self, conn_idx: int) -> Any:
        """lazy create + connect。"""
        if conn_idx in self._ws_handles:
            return self._ws_handles[conn_idx]
        fubon = get_fubon()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            logger.error("cannot create ws conn[%d]: fubon SDK not OK", conn_idx)
            return None
        try:
            ws = fubon.sdk.marketdata.websocket_client.stock
            self._ws_handles[conn_idx] = ws
            self._wire_callbacks(conn_idx, ws)
            await asyncio.to_thread(ws.connect)
            logger.info("ws conn[%d] connected", conn_idx)
            self._reconnect_failures = 0
            return ws
        except Exception as e:
            logger.error("ws conn[%d] connect failed: %s", conn_idx, e)
            return None

    def _wire_callbacks(self, conn_idx: int, ws: Any) -> None:
        def on_message(raw: object) -> None:
            self._handle_raw_message(raw)

        def on_disconnect(*args: object) -> None:
            logger.warning("ws conn[%d] disconnected: %s", conn_idx, args)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    asyncio.create_task, self._reconnect(conn_idx)
                )

        def on_error(*args: object) -> None:
            logger.error("ws conn[%d] error: %s", conn_idx, args)

        ws.on("message", on_message)
        ws.on("disconnect", on_disconnect)
        ws.on("error", on_error)

    def _handle_raw_message(self, raw: object) -> None:
        """sync callback (在 fubon SDK thread) → bridge to asyncio。"""
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("event") != "data":
            return
        data = payload.get("data") or {}
        symbol = data.get("symbol")
        price = data.get("price")
        size = data.get("size", 0)
        if not symbol or price is None:
            return
        tick = Tick(price=float(price), size=int(size), time=time.time())

        # 1. ring_buffer (sync, thread-safe)
        get_ring_buffer().append(symbol, tick)

        # 2. signal_engine queue (async, 用 loop bridge)
        if self._on_tick is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                asyncio.create_task, self._on_tick(symbol, tick)
            )

        # 3. broadcast tick 給前端 — 分時走勢圖最後一根 K 棒即時更新
        if self._loop is not None:
            tick_payload = {"event": "tick", "data": {"symbol": symbol, "price": float(price)}}
            self._loop.call_soon_threadsafe(
                asyncio.create_task, get_broadcaster().broadcast(tick_payload)
            )

    async def _reconnect(self, conn_idx: int) -> None:
        for delay in RECONNECT_DELAYS:
            await asyncio.sleep(delay)
            try:
                # snapshot subscribed symbols + discard old handle under lock
                async with self._lock:
                    self._ws_handles.pop(conn_idx, None)
                    syms = list(self._conn_subs.get(conn_idx, set()))

                ws = await self._ensure_handle(conn_idx)
                if ws is None:
                    raise RuntimeError("ensure_handle returned None")
                # 重訂閱 conn_subs[conn_idx]
                if syms:
                    await asyncio.to_thread(
                        ws.subscribe, {"channel": "trades", "symbols": syms}
                    )
                logger.info("ws conn[%d] reconnected, %d symbols restored", conn_idx, len(syms))
                self._status = WSPoolStatus.OK
                self._reconnect_failures = 0
                return
            except Exception as e:
                logger.warning("ws conn[%d] reconnect attempt failed: %s", conn_idx, e)
                self._reconnect_failures += 1
                if self._reconnect_failures >= CIRCUIT_OPEN_THRESHOLD:
                    self._status = WSPoolStatus.CIRCUIT_OPEN
                    await alerts.notify_critical(
                        f"WS pool circuit breaker open after {CIRCUIT_OPEN_THRESHOLD} reconnect failures",
                        conn_idx=conn_idx,
                    )
                    return
        self._status = WSPoolStatus.DEGRADED

    async def shutdown(self) -> None:
        for idx, ws in list(self._ws_handles.items()):
            try:
                await asyncio.to_thread(ws.disconnect)
            except Exception:
                pass
        self._ws_handles.clear()
        self._conn_subs.clear()
        self._symbol_to_conn.clear()
        self._refcount.clear()


_pool: WSPool | None = None


def get_ws_pool() -> WSPool:
    global _pool
    if _pool is None:
        _pool = WSPool()
    return _pool
