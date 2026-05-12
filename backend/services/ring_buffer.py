"""Per-symbol 時間窗 deque — 給 signal_engine 算 N 秒內的價量變化用。

Plan §Phase 3 §4.2。callback path 永遠不建 entry/lock；subscribe 時預建。
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


# ----------------------- inline smoke -----------------------

if __name__ == "__main__":
    import sys
    from concurrent.futures import ThreadPoolExecutor

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

    def step(n, t): print(f"\n{YELLOW}[Test {n}] {t}{RESET}")
    def ok(m): print(f"{GREEN}  ✓ {m}{RESET}")
    def fail(m): print(f"{RED}  ✗ {m}{RESET}"); sys.exit(1)

    buf = RingBuffer()

    step(1, "ensure 後 append 跟 latest 對齊")
    buf.ensure("2330")
    now = time.time()
    buf.append("2330", Tick(price=580, size=10, time=now))
    last = buf.latest("2330")
    if last and last.price == 580: ok("latest 拿得到")
    else: fail(f"latest 不對: {last}")

    step(2, "append 沒 ensure → drop + warn")
    buf.append("9999", Tick(price=1, size=1, time=now))
    if buf.latest("9999") is None: ok("沒 leak entry")
    else: fail("9999 entry 被建出來了")

    step(3, "window 截取過去 N 秒")
    buf.ensure("2317")
    base = time.time()
    for i in range(10):
        buf.append("2317", Tick(price=100 + i, size=1, time=base - (9 - i) * 1.0))
    w = buf.window("2317", seconds=5)
    if 4 <= len(w) <= 6: ok(f"window(5s) → {len(w)} 筆 (預期 5±1)")
    else: fail(f"window 不對: 拿到 {len(w)} 筆")

    step(4, "tail trim — 超過 1800s 的會被砍")
    buf.ensure("2454")
    old_time = time.time() - 2000  # 33 分鐘前
    buf.append("2454", Tick(price=1, size=1, time=old_time))
    buf.append("2454", Tick(price=2, size=1, time=time.time()))
    w = buf.window("2454", seconds=2000)
    if len(w) == 1: ok("舊 tick 已被 trim")
    else: fail(f"trim 失敗: {len(w)} 筆")

    step(5, "多執行緒同時 append + window 不爆")
    buf.ensure("3008")
    base = time.time()
    def writer():
        for i in range(200):
            buf.append("3008", Tick(price=1000 + i % 10, size=1, time=base + i * 0.001))
    def reader():
        for _ in range(200):
            buf.window("3008", seconds=1)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for _ in range(4):
            ex.submit(writer)
            ex.submit(reader)
    last = buf.latest("3008")
    if last is not None: ok(f"無爆，latest price={last.price}")
    else: fail("multithread race 跑完沒資料")

    step(6, "discard")
    buf.discard("2330")
    if buf.latest("2330") is None and not buf.has("2330"): ok("entry 移除")
    else: fail("discard 沒清乾淨")

    print(f"\n{GREEN}All ring_buffer smoke tests passed ✓{RESET}")
