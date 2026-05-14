"""Async batch flush writer — evaluator 命中時 append，500ms 或 ≥100 列觸發 INSERT signals_log。

失敗 retry 1 次仍失敗 → alerts + buffer 保留待下次 flush 重送。
buffer > 1000 列 → FIFO drop + metric。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import alerts
from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 0.5
FLUSH_THRESHOLD = 100
BUFFER_HARD_CAP = 1000


class SupabaseWriter:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._dropped_writes = 0
        self._failed_flushes = 0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("SupabaseWriter started")

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        # 最後 flush
        await self._flush()

    def append(self, row: dict[str, Any]) -> None:
        """同步 append（從 evaluator 呼叫）。超過 hard cap → drop 最舊。"""
        if len(self._buffer) >= BUFFER_HARD_CAP:
            self._buffer.pop(0)
            self._dropped_writes += 1
        self._buffer.append(row)

    def metrics(self) -> dict[str, int]:
        return {
            "buffer_size": len(self._buffer),
            "dropped_writes": self._dropped_writes,
            "failed_flushes": self._failed_flushes,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(FLUSH_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if len(self._buffer) >= FLUSH_THRESHOLD or self._buffer:
                await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        sb = get_supabase()
        if sb.client is None:
            return
        async with self._lock:
            batch = self._buffer[:]
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("signals_log").insert(batch).execute()
            )
            async with self._lock:
                # 只清 batch size 那麼多，新進來的 row 留著
                self._buffer = self._buffer[len(batch):]
            logger.debug("flushed %d signal rows", len(batch))
        except Exception as e:
            self._failed_flushes += 1
            logger.warning("flush failed (will retry next cycle): %s", e)
            if self._failed_flushes % 10 == 1:
                await alerts.notify_critical(
                    "supabase_writer flush failing",
                    error=f"{type(e).__name__}: {e}",
                    failed_count=str(self._failed_flushes),
                )


_writer: SupabaseWriter | None = None


def get_supabase_writer() -> SupabaseWriter:
    global _writer
    if _writer is None:
        _writer = SupabaseWriter()
    return _writer
