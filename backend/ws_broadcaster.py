"""管理 /ws/realtime 的所有前端連線 + broadcast helper。

tick 熱路徑不能每筆 spawn 一個 send task — 慢 client 會讓 pending task 與記憶體
無上限堆積，且多 task 並發對同一個 WebSocket send_json 沒有順序/安全保證。
改為單一有界佇列 + 常駐 consumer：所有 send 序列化、佇列滿丟最舊（行情 tick 可丟）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 1000
DROP_LOG_EVERY = 1000  # 丟訊息是預期內的降載行為，只低頻記 log


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._consumer: asyncio.Task[None] | None = None
        self._dropped = 0

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
        logger.info("ws client connected (total=%d)", len(self._clients))

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("ws client disconnected (total=%d)", len(self._clients))

    def publish(self, payload: dict[str, Any]) -> None:
        """非阻塞投遞 — 必須在 event loop 執行緒呼叫（SDK thread 用 call_soon_threadsafe 包）。"""
        if self._consumer is None or self._consumer.done():
            # lazy start（lifespan 不歸這裡管）；consumer 意外死掉也能在下一筆復活
            self._consumer = asyncio.create_task(self._consume())
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._dropped += 1
                if self._dropped % DROP_LOG_EVERY == 1:
                    logger.warning(
                        "broadcast queue full — dropped %d messages so far", self._dropped
                    )
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(payload)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """同樣走佇列 — 所有路徑的 send 都由 consumer 序列化，
        不會多個 coroutine 並發對同一個 WebSocket send_json。"""
        self.publish(payload)

    async def _consume(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._send_all(payload)
            except Exception:
                logger.exception("broadcast send failed")

    async def _send_all(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


_b: Broadcaster | None = None


def get_broadcaster() -> Broadcaster:
    global _b
    if _b is None:
        _b = Broadcaster()
    return _b
