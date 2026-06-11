"""TokenBucket 邊界 — rate<1 的合法調慢操作不能讓 acquire(1) 直接 ValueError。"""
from __future__ import annotations

import pytest

from services.rate_limiter import TokenBucket


def test_rate_below_one_still_allows_single_acquire():
    # capacity 下限 1:rate=0.5 的語意是「等更久」,不是每次 acquire 都炸
    bucket = TokenBucket(rate=0.5)
    assert bucket.capacity == 1.0
    assert bucket.acquire() is True


def test_explicit_capacity_still_respected():
    bucket = TokenBucket(rate=5.0, capacity=2.0)
    assert bucket.capacity == 2.0
    with pytest.raises(ValueError):
        bucket.acquire(tokens=3)


async def test_acquire_async_consumes_and_waits():
    bucket = TokenBucket(rate=100.0)  # 快速補充,測試不用等久
    for _ in range(int(bucket.capacity)):
        assert await bucket.acquire_async() is True
    # token 用完 → 要等補充(asyncio.sleep 路徑),不是 raise
    assert await bucket.acquire_async() is True


async def test_acquire_async_timeout_returns_false():
    bucket = TokenBucket(rate=1.0)
    assert await bucket.acquire_async() is True  # 清空 bucket
    assert await bucket.acquire_async(timeout=0.01) is False
