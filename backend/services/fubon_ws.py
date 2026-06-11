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
        # ws handles (lazy create)，只放「connect 成功」的 handle
        self._ws_handles: dict[int, Any] = {}
        # conn idx → 已 wire callback 的 ws 實例（identity 比對）。
        # SDK ws 是 singleton 且 pyee .on() 對新 closure 是累加不是覆蓋 —
        # 同一實例重複 wire 會讓每筆 tick 被處理 N+1 次，所以同實例只 wire 一次。
        self._wired_ws: dict[int, Any] = {}
        # conn idx → in-flight reconnect task（去重 + 持強參考防 GC）
        self._reconnect_tasks: dict[int, asyncio.Task[None]] = {}
        # tick callback 的 fire-and-forget task 持強參考（event loop 只持弱參考）
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._status = WSPoolStatus.OK
        self._reconnect_failures = 0
        self._closing = False
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
        """加 owner；refcount 0→1 才真打富邦訂閱。

        真訂閱失敗會回滾 bookkeeping 並 raise RuntimeError —
        否則 symbol 卡在 _symbol_to_conn 裡，之後重試都走 need_real_sub=False
        直接回成功，永遠收不到 tick。
        """
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
                try:
                    await self._real_subscribe(conn_idx, [symbol])
                except Exception as e:
                    self._refcount[symbol].discard(owner_id)
                    if not self._refcount[symbol]:
                        del self._refcount[symbol]
                    self._symbol_to_conn.pop(symbol, None)
                    self._conn_subs[conn_idx].discard(symbol)
                    get_ring_buffer().discard(symbol)
                    logger.error("subscribe failed conn[%d] %s: %s", conn_idx, symbol, e)
                    raise RuntimeError(f"WS subscribe {symbol} failed: {e}") from e

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

    async def _subscribe_trades(self, ws: Any, symbols: list[str]) -> None:
        """trades 訂閱唯一出口 — _real_subscribe/_reconnect/resubscribe_all 共用。"""
        await asyncio.to_thread(
            ws.subscribe, {"channel": "trades", "symbols": symbols}
        )

    async def _real_subscribe(self, conn_idx: int, symbols: list[str]) -> None:
        """失敗會 raise — 由 subscribe() 回滾 bookkeeping。"""
        ws = await self._ensure_handle(conn_idx)
        await self._subscribe_trades(ws, symbols)
        logger.info("conn[%d] subscribed: %s (total=%d)", conn_idx, symbols, len(self._conn_subs[conn_idx]))

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
        """lazy create + connect。失敗 raise（不留殭屍 handle 在 dict 裡）。"""
        if conn_idx in self._ws_handles:
            return self._ws_handles[conn_idx]
        fubon = get_fubon()
        if fubon.status != FubonStatus.OK or fubon.sdk is None:
            raise RuntimeError(f"cannot create ws conn[{conn_idx}]: fubon SDK not OK")
        ws = fubon.sdk.marketdata.websocket_client.stock
        # 同一實例只 wire 一次（relogin 後是新實例才需要重 wire）
        if self._wired_ws.get(conn_idx) is not ws:
            self._wire_callbacks(conn_idx, ws)
            self._wired_ws[conn_idx] = ws
        try:
            await asyncio.to_thread(ws.connect)
        except Exception as e:
            logger.error("ws conn[%d] connect failed: %s", conn_idx, e)
            raise
        # connect 成功才入 dict — 失敗的 handle 留著會讓後續 subscribe 全部靜默壞掉
        self._ws_handles[conn_idx] = ws
        logger.info("ws conn[%d] connected", conn_idx)
        self._reconnect_failures = 0
        return ws

    def _wire_callbacks(self, conn_idx: int, ws: Any) -> None:
        def on_message(raw: object) -> None:
            self._handle_raw_message(raw)

        def on_disconnect(*args: object) -> None:
            logger.warning("ws conn[%d] disconnected: %s", conn_idx, args)
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    self._schedule_reconnect, conn_idx, ws
                )

        def on_error(*args: object) -> None:
            logger.error("ws conn[%d] error: %s", conn_idx, args)

        ws.on("message", on_message)
        ws.on("disconnect", on_disconnect)
        ws.on("error", on_error)

    def _schedule_reconnect(self, conn_idx: int, ws: Any) -> None:
        """event loop 內執行 — 去重 + stale 判斷後才 spawn _reconnect。"""
        if self._closing:
            return
        # 被汰換的舊 handle（relogin/shutdown 已 pop）斷線不該把新 handle 踢掉重連
        if self._ws_handles.get(conn_idx) is not ws:
            return
        existing = self._reconnect_tasks.get(conn_idx)
        if existing is not None and not existing.done():
            return  # 已有 in-flight reconnect，重複 disconnect 事件不疊加
        task = asyncio.create_task(self._reconnect(conn_idx))
        self._reconnect_tasks[conn_idx] = task

        def _done(t: asyncio.Task[None]) -> None:
            if self._reconnect_tasks.get(conn_idx) is t:
                self._reconnect_tasks.pop(conn_idx, None)
            if not t.cancelled() and t.exception() is not None:
                logger.error("reconnect task conn[%d] crashed: %s", conn_idx, t.exception())

        task.add_done_callback(_done)

    def _spawn_on_tick(self, symbol: str, tick: Tick) -> None:
        """event loop 內執行 — create_task 持強參考（loop 只持弱參考，可能被 GC）。"""
        if self._on_tick is None:
            return
        task = asyncio.create_task(self._on_tick(symbol, tick))
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_bg_task_done)

    def _on_bg_task_done(self, task: asyncio.Task[Any]) -> None:
        self._bg_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("tick callback task failed: %s", task.exception())

    def _handle_raw_message(self, raw: object) -> None:
        """sync callback (在 fubon SDK thread) → bridge to asyncio。

        整段包 try/except — callback 例外外洩進 SDK dispatch 的行為未定義，
        最壞會中斷整條連線的訊息迴圈。
        """
        try:
            self._process_raw_message(raw)
        except Exception:
            logger.exception("unexpected error handling ws message")

    def _process_raw_message(self, raw: object) -> None:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("event") != "data":
            return
        data = payload.get("data") or {}
        # 試撮（開盤前 08:30–09:00、收盤前 13:25–13:30 集合競價模擬撮合）不是真實成交，
        # 從源頭擋掉，避免汙染 ring_buffer 量能視窗與訊號評估
        if data.get("isTrial"):
            return
        symbol = data.get("symbol")
        price = data.get("price")
        size = data.get("size", 0)
        if not symbol or price is None:
            return

        bid = data.get("bid")
        ask = data.get("ask")
        tick = Tick(
            price=float(price),
            size=int(size),
            time=time.time(),
            bid=float(bid) if bid is not None else None,
            ask=float(ask) if ask is not None else None,
        )

        # 1. ring_buffer (sync, thread-safe)
        get_ring_buffer().append(symbol, tick)

        # 2. signal_engine queue (async, 用 loop bridge)
        if self._on_tick is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._spawn_on_tick, symbol, tick)

        # 3. broadcast tick 給前端 — 分時走勢圖最後一根 K 棒即時更新 + 明細張數 + 內外盤
        if self._loop is not None:
            tick_data: dict[str, Any] = {
                "symbol": symbol,
                "price": float(price),
                "size": int(size),
            }
            if bid is not None:
                tick_data["bid"] = float(bid)
            if ask is not None:
                tick_data["ask"] = float(ask)
            tick_payload = {"event": "tick", "data": tick_data}
            # publish 走有界佇列 + 常駐 consumer，不會每 tick 疊一個 send task
            self._loop.call_soon_threadsafe(get_broadcaster().publish, tick_payload)

    async def _reconnect(self, conn_idx: int) -> None:
        for delay in RECONNECT_DELAYS:
            await asyncio.sleep(delay)
            if self._closing:
                return
            try:
                # 整段持鎖 — 避免與 subscribe()/resubscribe_all() 並發對同一條 ws connect
                async with self._lock:
                    self._ws_handles.pop(conn_idx, None)
                    syms = list(self._conn_subs.get(conn_idx, set()))
                    ws = await self._ensure_handle(conn_idx)
                    # 重訂閱 conn_subs[conn_idx]
                    if syms:
                        await self._subscribe_trades(ws, syms)
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
                        f"WS pool circuit breaker open after {CIRCUIT_OPEN_THRESHOLD} reconnect failures"
                        " — 行情中斷，等 8:25 overnight reconnect 或重啟復位",
                        conn_idx=conn_idx,
                    )
                    return
        # connect 成功過（計數被重置）但重訂閱持續失敗才會走到這 — 同樣要可見
        self._status = WSPoolStatus.DEGRADED
        await alerts.notify_critical(
            "WS pool degraded: reconnect attempts exhausted without restoring subscriptions"
            " — 行情中斷，等 8:25 overnight reconnect 或重啟復位",
            conn_idx=conn_idx,
        )

    async def disconnect_all(self) -> None:
        """斷開並丟棄所有 handle；訂閱 bookkeeping 保留給 resubscribe_all。

        先 pop 再 disconnect — 舊 handle 的 disconnect 事件會被 _schedule_reconnect
        的 stale 判斷擋掉，不會誤觸重連。
        """
        async with self._lock:
            handles = list(self._ws_handles.items())
            self._ws_handles.clear()
            for idx, ws in handles:
                try:
                    await asyncio.to_thread(ws.disconnect)
                except Exception as e:
                    logger.warning("disconnect conn[%d] failed (ignored): %s", idx, e)

    async def resubscribe_all(self) -> None:
        """relogin 後重建連線 + 重訂閱（overnight 8:25 呼叫）。

        迭代 _conn_subs（有訂閱需求的 conn）而非 _ws_handles —
        handle 建立失敗時 dict 是空的，迭代 handle 會整批漏掉。
        成功後復位 status，是 CIRCUIT_OPEN/DEGRADED 的唯一自動恢復路徑。
        """
        async with self._lock:
            for conn_idx in sorted(self._conn_subs.keys()):
                syms = list(self._conn_subs[conn_idx])
                if not syms:
                    continue
                self._ws_handles.pop(conn_idx, None)
                ws = await self._ensure_handle(conn_idx)
                await self._subscribe_trades(ws, syms)
                logger.info("conn[%d] re-subscribed %d symbols", conn_idx, len(syms))
            self._status = WSPoolStatus.OK
            self._reconnect_failures = 0

    async def shutdown(self) -> None:
        # closing flag 要先設 — disconnect 會觸發 on_disconnect，
        # 沒有 flag 會在關機收尾 spawn _reconnect
        self._closing = True
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()
        await self.disconnect_all()
        self._conn_subs.clear()
        self._symbol_to_conn.clear()
        self._refcount.clear()
        self._wired_ws.clear()


_pool: WSPool | None = None


def get_ws_pool() -> WSPool:
    global _pool
    if _pool is None:
        _pool = WSPool()
    return _pool
