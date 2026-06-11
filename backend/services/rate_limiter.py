"""Thread-safe token bucket — throttles Fubon REST calls.

Token bucket：每秒補 N 個 token，每次 acquire() 消耗 1 個；不夠就 sleep 到夠。
Sync 版可從 thread 內呼叫 → 用 threading 原語(不是 asyncio)。
Event loop 端用 acquire_async() — sync 版丟 to_thread 會在排隊時占住 worker。

調率：環境變數 FUBON_RATE_LIMIT_PER_SEC，預設 5。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


class TokenBucket:
    """Thread-safe token bucket. Blocks `acquire()` callers until tokens available."""

    def __init__(self, rate: float = 5.0, capacity: float | None = None) -> None:
        """
        Args:
            rate: tokens per second (steady-state allowance).
            capacity: max bucket size; defaults to `max(rate, 1)` (allows ~1s burst).
                下限 1 — rate < 1（調慢限流）時 acquire(1) 的語意是「等更久」,
                不能因 capacity < 1 直接 ValueError 讓所有 REST 呼叫失效。
        """
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        cap = float(capacity) if capacity is not None else max(float(rate), 1.0)
        if cap <= 0:
            raise ValueError(f"capacity must be > 0, got {cap}")

        self._rate = float(rate)
        self._capacity = cap
        self._tokens = cap
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def capacity(self) -> float:
        return self._capacity

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    def acquire(self, tokens: int = 1, timeout: float | None = None) -> bool:
        """Block until `tokens` tokens are available.

        Returns True on success, False if `timeout` elapsed first.
        Raises ValueError if `tokens` > capacity (would block forever).
        """
        if tokens > self._capacity:
            raise ValueError(
                f"requested {tokens} tokens > capacity {self._capacity}"
            )

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self._rate

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait = min(wait, remaining)

            time.sleep(wait)

    async def acquire_async(self, tokens: int = 1, timeout: float | None = None) -> bool:
        """acquire() 的 asyncio 版 — 等待用 asyncio.sleep,不占 thread pool worker。

        與 sync 版共用同一個 bucket/lock（lock 內只做算術,不會卡 event loop）。
        """
        if tokens > self._capacity:
            raise ValueError(
                f"requested {tokens} tokens > capacity {self._capacity}"
            )

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self._rate

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait = min(wait, remaining)

            await asyncio.sleep(wait)


_default_bucket: TokenBucket | None = None
_historical_bucket: TokenBucket | None = None


def get_rate_limiter() -> TokenBucket:
    """Intraday/Snapshot/Technical 用 — 富邦官方 300/min = 5 req/s。
    Env FUBON_RATE_LIMIT_PER_SEC 可覆寫 (default 5.0)。
    """
    global _default_bucket
    if _default_bucket is None:
        rate = float(os.getenv("FUBON_RATE_LIMIT_PER_SEC", "5"))
        _default_bucket = TokenBucket(rate=rate)
        logger.info("Rate limiter initialized: %.1f req/s (Intraday/Snapshot/Technical)", rate)
    return _default_bucket


def get_historical_rate_limiter() -> TokenBucket:
    """Historical API 專用 — 富邦官方 60/min = 1 req/s。
    比 default limiter 嚴 5 倍，避免 cdp.backfill 在尖峰超限被 429。
    Env FUBON_HISTORICAL_RATE_LIMIT_PER_SEC 可覆寫 (default 1.0)。
    """
    global _historical_bucket
    if _historical_bucket is None:
        rate = float(os.getenv("FUBON_HISTORICAL_RATE_LIMIT_PER_SEC", "1"))
        _historical_bucket = TokenBucket(rate=rate)
        logger.info("Historical rate limiter initialized: %.1f req/s (60/min)", rate)
    return _historical_bucket


