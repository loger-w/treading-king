"""Per-symbol 時間窗 deque — 給 signal_engine 算 N 秒內的價量變化用。

callback path 永遠不建 entry/lock；subscribe 時預建。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

logger = logging.getLogger(__name__)

MAX_WINDOW_SECONDS = 1800   # 30 分鐘
DEQUE_MAXLEN = 5000          # OOM 防呆


@dataclass
class Tick:
    price: float
    size: int
    time: float  # epoch seconds
    bid: float | None = None  # 成交當下的最佳買價（用來判內外盤）
    ask: float | None = None  # 成交當下的最佳賣價


class RingBuffer:
    """thread-safe per-symbol tick buffer。"""

    def __init__(self) -> None:
        self._buffers: dict[str, Deque[Tick]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def ensure(self, symbol: str) -> None:
        """subscribe 時呼叫，預建 entry+lock。callback path 不可呼叫。"""
        with self._registry_lock:
            if symbol not in self._buffers:
                self._buffers[symbol] = deque(maxlen=DEQUE_MAXLEN)
                self._locks[symbol] = threading.Lock()

    def discard(self, symbol: str) -> None:
        """unsubscribe 且 refcount==0 才呼叫。"""
        with self._registry_lock:
            self._buffers.pop(symbol, None)
            self._locks.pop(symbol, None)

    def append(self, symbol: str, tick: Tick) -> None:
        """從 fubon WS callback 餵進來。symbol 必須先 ensure 過。"""
        lock = self._locks.get(symbol)
        if lock is None:
            logger.warning("append before ensure: %s — dropping tick", symbol)
            return
        buf = self._buffers[symbol]
        with lock:
            buf.append(tick)
            # tail trim：砍超過 max window 的舊 tick
            cutoff = tick.time - MAX_WINDOW_SECONDS
            while buf and buf[0].time < cutoff:
                buf.popleft()

    def window(self, symbol: str, seconds: int) -> list[Tick]:
        """拿過去 N 秒的 ticks（含 latest），ascending by time。"""
        lock = self._locks.get(symbol)
        if lock is None:
            return []
        buf = self._buffers[symbol]
        with lock:
            if not buf:
                return []
            cutoff = time.time() - seconds
            return [t for t in buf if t.time >= cutoff]

    def latest(self, symbol: str) -> Tick | None:
        lock = self._locks.get(symbol)
        if lock is None:
            return None
        buf = self._buffers[symbol]
        with lock:
            return buf[-1] if buf else None

    def has(self, symbol: str) -> bool:
        return symbol in self._buffers


_default: RingBuffer | None = None


def get_ring_buffer() -> RingBuffer:
    global _default
    if _default is None:
        _default = RingBuffer()
    return _default


