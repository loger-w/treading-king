"""Fubon Neo SDK wrapper — DMA login + degraded mode + auto-retry.

所有 sync SDK call 透過 asyncio.to_thread 包裝。

DMA login 路線：apikey_dma_login(personal_id, api_key) — 無需 PFX 憑證。
用途：行情查詢 + WebSocket trades/books/ticks/snapshot/tickers 訂閱。
不支援：下單、aggregates/candles channel（Speed mode 限制）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from services import alerts
from services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


class FubonStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    DEGRADED = "degraded"


class FubonClient:
    """Lazy singleton — `await get_fubon()` 取實例。"""

    def __init__(self) -> None:
        self._sdk: Any = None
        self._status: FubonStatus = FubonStatus.ERROR
        self._last_error: str | None = None
        self._last_attempt_at: datetime | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def status(self) -> FubonStatus:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def sdk(self) -> Any:
        """Raw SDK handle. None when degraded/error."""
        return self._sdk

    async def init(self) -> None:
        """One-shot startup init with retry."""
        await self._login_with_retry(max_attempts=3)
        if self._status == FubonStatus.ERROR:
            # Schedule background retry every 5 min
            if self._retry_task is None or self._retry_task.done():
                self._retry_task = asyncio.create_task(self._background_retry())

    async def _login_with_retry(self, max_attempts: int = 3) -> None:
        delays = [1, 2, 4]
        async with self._lock:
            for attempt in range(max_attempts):
                self._last_attempt_at = datetime.now(timezone.utc)
                try:
                    await asyncio.to_thread(self._do_login_sync)
                    self._status = FubonStatus.OK
                    self._last_error = None
                    logger.info("Fubon SDK login + init_realtime OK")
                    return
                except Exception as e:
                    self._last_error = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "Fubon login attempt %d/%d failed: %s",
                        attempt + 1,
                        max_attempts,
                        self._last_error,
                    )
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(delays[min(attempt, len(delays) - 1)])

            # All retries exhausted
            self._status = FubonStatus.ERROR
            self._sdk = None
            await alerts.notify_critical(
                f"Fubon SDK init failed after {max_attempts} attempts",
                error=self._last_error or "(no detail)",
            )

    def _do_login_sync(self) -> None:
        """Sync SDK login — must run in thread."""
        from fubon_neo.sdk import FubonSDK, Mode  # type: ignore[import-not-found]

        personal_id = (
            os.getenv("FUBON_PERSONAL_ID", "").strip()
            or os.getenv("FUBON_ACCOUNT", "").strip()
        )
        api_key = os.getenv("FUBON_API_KEY", "").strip()

        if not personal_id or not api_key:
            raise RuntimeError(
                "FUBON_API_KEY and FUBON_PERSONAL_ID (or FUBON_ACCOUNT) required"
            )

        sdk = FubonSDK()
        cert_path = os.getenv("FUBON_CERT_PATH", "").strip()

        if cert_path:
            cert_pass = os.getenv("FUBON_CERT_PASS", "").strip() or None
            logger.info("Using apikey_login (with cert)")
            sdk.apikey_login(personal_id, api_key, cert_path, cert_pass)
        else:
            logger.info("Using apikey_dma_login (DMA mode, no cert)")
            sdk.apikey_dma_login(personal_id, api_key)

        # Normal 模式:Speed 預設不支援 candles / aggregates channel (期貨即時 K 需要)。
        # Stock trades / 各 REST 端點兩模式都可用,切 Normal 不破壞既有股票功能。
        sdk.init_realtime(Mode.Normal)
        self._sdk = sdk

    async def _background_retry(self) -> None:
        """Retry login every 5 min while status=error."""
        while self._status == FubonStatus.ERROR:
            await asyncio.sleep(300)
            logger.info("Background retry: attempting Fubon login again")
            try:
                await self._login_with_retry(max_attempts=1)
            except Exception as e:
                logger.warning("Background retry failed: %s", e)

    async def shutdown(self) -> None:
        """Cleanup on app shutdown."""
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        if self._sdk is not None:
            try:
                await asyncio.to_thread(self._sdk.logout)
            except Exception as e:
                logger.debug("Logout failed (ignored): %s", e)

    # ---- High-level API wrappers (async, all use to_thread) ----

    async def intraday_quote(self, symbol: str) -> dict[str, Any]:
        if self._status != FubonStatus.OK or self._sdk is None:
            raise RuntimeError("Fubon SDK not available (degraded mode)")
        await asyncio.to_thread(get_rate_limiter().acquire)
        return await asyncio.to_thread(
            self._sdk.marketdata.rest_client.stock.intraday.quote,
            symbol=symbol,
        )

    async def intraday_ticker(self, symbol: str) -> dict[str, Any]:
        if self._status != FubonStatus.OK or self._sdk is None:
            raise RuntimeError("Fubon SDK not available")
        await asyncio.to_thread(get_rate_limiter().acquire)
        return await asyncio.to_thread(
            self._sdk.marketdata.rest_client.stock.intraday.ticker,
            symbol=symbol,
        )

    async def technical_rsi(self, symbol: str, period: int = 14) -> dict[str, Any]:
        if self._status != FubonStatus.OK or self._sdk is None:
            raise RuntimeError("Fubon SDK not available")
        await asyncio.to_thread(get_rate_limiter().acquire)
        return await asyncio.to_thread(
            self._sdk.marketdata.rest_client.stock.technical.rsi,
            symbol=symbol,
            period=period,
        )


# Module-level singleton
_client: FubonClient | None = None


def get_fubon() -> FubonClient:
    """Lazy singleton accessor."""
    global _client
    if _client is None:
        _client = FubonClient()
    return _client
