"""Thread-safe token bucket — throttles Fubon REST calls from worker threads.

Plan §Phase 2a：indicator_cache_job 用 ThreadPoolExecutor(max_workers=8) 並行打
富邦 technical API。沒有 limiter = 8 worker × 100ms = 80 req/s 直接被 IP ban。

Token bucket：每秒補 N 個 token，每次 acquire() 消耗 1 個；不夠就 sleep 到夠。
從 sync ThreadPoolExecutor 內呼叫 → 用 threading 原語（不是 asyncio）。

調率：環境變數 FUBON_RATE_LIMIT_PER_SEC，預設 5。Phase 2a 跑完 ~2363 檔 × 5
indicator 後再依實測調。
"""
from __future__ import annotations

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
            capacity: max bucket size; defaults to `rate` (allows ~1s burst).
        """
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        cap = float(capacity) if capacity is not None else float(rate)
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


_default_bucket: TokenBucket | None = None


def get_rate_limiter() -> TokenBucket:
    """Lazy singleton — uses FUBON_RATE_LIMIT_PER_SEC env (default 5.0)."""
    global _default_bucket
    if _default_bucket is None:
        rate = float(os.getenv("FUBON_RATE_LIMIT_PER_SEC", "5"))
        _default_bucket = TokenBucket(rate=rate)
        logger.info("Rate limiter initialized: %.1f req/s", rate)
    return _default_bucket


# ---------------------------------------------------------------------------
# Inline smoke test — `python services/rate_limiter.py`
# 不引 pytest（與 scripts/sdk_smoke.py 同風格）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from concurrent.futures import ThreadPoolExecutor

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

    def step(n: int, title: str) -> None:
        print(f"\n{YELLOW}[Test {n}] {title}{RESET}")

    def ok(msg: str) -> None:
        print(f"{GREEN}  ✓ {msg}{RESET}")

    def fail(msg: str) -> None:
        print(f"{RED}  ✗ {msg}{RESET}")
        sys.exit(1)

    # Test 1：單線程 — 10 acquires at rate=5/s, capacity=5
    # 預期：前 5 個 instant（吃掉初始 burst），後 5 個每 0.2s 一個 → ~1.0s 總
    step(1, "single-thread steady-state (rate=5/s, cap=5, n=10)")
    bucket = TokenBucket(rate=5.0, capacity=5)
    start = time.monotonic()
    for _ in range(10):
        bucket.acquire()
    elapsed = time.monotonic() - start
    print(f"  elapsed={elapsed:.2f}s (expect ~1.0s)")
    if 0.8 < elapsed < 1.4:
        ok("rate honored")
    else:
        fail(f"rate violation: expected 0.8–1.4s, got {elapsed:.2f}s")

    # Test 2：8 worker thread 平行打 40 個 acquire，rate=5/s
    # 預期：前 5 instant，後 35 每秒 5 個 → ~7.0s 總
    step(2, "8-thread contention (rate=5/s, cap=5, n=40)")
    bucket2 = TokenBucket(rate=5.0, capacity=5)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: bucket2.acquire(), range(40)))
    elapsed = time.monotonic() - start
    print(f"  elapsed={elapsed:.2f}s (expect ~7.0s)")
    if 6.5 < elapsed < 8.0:
        ok("thread-safe rate honored")
    else:
        fail(f"rate violation: expected 6.5–8.0s, got {elapsed:.2f}s")

    # Test 3：timeout
    step(3, "timeout returns False without blocking forever")
    bucket3 = TokenBucket(rate=1.0, capacity=1)
    bucket3.acquire()  # 用掉初始 token
    start = time.monotonic()
    got = bucket3.acquire(timeout=0.3)
    elapsed = time.monotonic() - start
    print(f"  acquire(timeout=0.3) → {got}, elapsed={elapsed:.2f}s")
    if not got and 0.25 < elapsed < 0.5:
        ok("timeout honored")
    else:
        fail(f"expected (False, ~0.3s), got ({got}, {elapsed:.2f}s)")

    # Test 4：tokens > capacity raises
    step(4, "acquire(tokens > capacity) raises")
    try:
        TokenBucket(rate=5, capacity=5).acquire(tokens=10)
        fail("expected ValueError")
    except ValueError as e:
        ok(f"raised: {e}")

    print(f"\n{GREEN}All rate limiter smoke tests passed ✓{RESET}")
