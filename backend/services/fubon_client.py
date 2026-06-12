"""Fubon Neo SDK wrapper — 行情授權登入 + auto-retry（status 只有 OK/ERROR 兩態）.

所有 sync SDK call 透過 asyncio.to_thread 包裝。

登入：官方要求 init_realtime 前必須先登入才能取得行情權限。三種登入只有
apikey_dma_login(personal_id, api_key) 免 PFX 憑證,故預設走它。本系統純行情、
下單走群益,不需 DMA 交易權 —— 未申請時登入回「查無資料」屬預期,行情仍可用,
真正能否取得行情交由 init_realtime 判定（見 _do_login_sync）。
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


class FubonClient:
    """Lazy singleton — `await get_fubon()` 取實例。"""

    def __init__(self) -> None:
        self._sdk: Any = None
        self._status: FubonStatus = FubonStatus.ERROR
        self._last_error: str | None = None
        self._last_attempt_at: datetime | None = None
        self._retry_task: asyncio.Task[None] | None = None
        # 只在 ok→error 轉變時發 alert — 背景重試每 5 分鐘失敗一次,
        # 無條件發會把 Discord 灌爆、淹掉真正的新告警
        self._error_alerted = False
        self._lock = asyncio.Lock()

    @property
    def status(self) -> FubonStatus:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def sdk(self) -> Any:
        """Raw SDK handle. None when status=error."""
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
                    if self._error_alerted:
                        self._error_alerted = False
                        await alerts.notify_critical("Fubon SDK login recovered")
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
            if not self._error_alerted:
                self._error_alerted = True
                await alerts.notify_critical(
                    f"Fubon SDK init failed after {max_attempts} attempts",
                    error=self._last_error or "(no detail)",
                )
            else:
                logger.error("Fubon SDK still down: %s", self._last_error)

    def _do_login_sync(self) -> None:
        """Sync SDK login — must run in thread."""
        from fubon_neo.sdk import FubonSDK, Mode  # type: ignore[import-not-found]

        # relogin（overnight 8:25）前先收乾淨舊 SDK — 否則舊 WS 連線繼續推 tick
        # 造成雙倍行情,且每天洩漏一條連線額度（富邦每帳號上限 5 條）
        old_sdk = self._sdk
        if old_sdk is not None:
            self._sdk = None
            self._teardown_sdk_sync(old_sdk)

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

        # 官方:需登入後才能取得行情權限,故 init_realtime 前一定要先登入。
        # 三種登入只有 dma 免憑證 — 本系統純行情、下單走群益,預設走 dma。
        if cert_path:
            cert_pass = os.getenv("FUBON_CERT_PASS", "").strip() or None
            login_label = "apikey_login (with cert)"
            result = sdk.apikey_login(personal_id, api_key, cert_path, cert_pass)
        else:
            login_label = "apikey_dma_login (DMA, no cert)"
            result = sdk.apikey_dma_login(personal_id, api_key)

        ok = result is not None and getattr(result, "is_success", False)
        msg = getattr(result, "message", None) or "(no message)"
        if ok:
            logger.info("Fubon %s 登入成功", login_label)
        elif cert_path:
            # 憑證模式是操作者刻意設定、意圖取得交易層登入 — 失敗代表憑證/密碼真的
            # 有問題,屬非預期錯誤(不是 DMA 那種「查無資料」常態)。直接中止,讓
            # _login_with_retry 重試耗盡後 notify_critical 把它喊出來,不要靜默吞掉。
            raise RuntimeError(f"Fubon 憑證登入失敗: {msg}")
        else:
            # DMA 模式未申請交易權時回「查無資料」屬預期 — 金鑰仍有效,失敗的登入
            # 照樣發行情授權(實測:查無資料下 init_realtime 仍成功)。下單走群益,
            # 不需 DMA 交易權,故不在此中止;能否取得行情交由 init_realtime 判定。
            logger.info("Fubon %s 交易層未登入(%s);續以行情授權 init_realtime", login_label, msg)

        # init_realtime 的 token 交換才是行情可用與否的真正判準:金鑰真的壞掉時
        # 這裡會 raise(實測)。把登入訊息一併帶進例外,避免真因被晦澀的 token 例外遮蔽。
        # Normal 模式:Speed 預設不支援 candles / aggregates channel(期貨即時 K 需要),
        # Stock trades / 各 REST 端點兩模式都可用,切 Normal 不破壞既有股票功能。
        try:
            sdk.init_realtime(Mode.Normal)
        except Exception as e:
            # 登入成功但行情初始化失敗 → 此 sdk 已持有 server session,先收掉再 raise。
            # 否則 _login_with_retry 每次重試都新建並重新登入,會逐次吃掉富邦每帳號
            # 5 連線額度(同 _teardown_sdk_sync 防的洩漏)。
            try:
                sdk.logout()
            except Exception as logout_err:
                logger.debug("logout after init_realtime failure ignored: %s", logout_err)
            raise RuntimeError(
                f"Fubon 行情初始化失敗(登入: {'OK' if ok else msg}): {e}"
            ) from e
        self._sdk = sdk

    @staticmethod
    def _teardown_sdk_sync(sdk: Any) -> None:
        """收掉被汰換的 SDK:先斷 marketdata WS 再 logout,全部防禦式。

        舊 ws 的 on_disconnect 仍 wire 在 WSPool 上 — pool 端以 handle identity
        判 stale,不會被這裡的 disconnect 誤觸重連。
        """
        try:
            md = getattr(sdk, "marketdata", None)
            ws_client = getattr(md, "websocket_client", None) if md is not None else None
            if ws_client is not None:
                for name in ("stock", "futopt"):
                    try:
                        getattr(ws_client, name).disconnect()
                    except Exception as e:
                        logger.debug("old ws %s disconnect failed (ignored): %s", name, e)
        except Exception as e:
            logger.debug("old sdk ws teardown failed (ignored): %s", e)
        try:
            sdk.logout()
        except Exception as e:
            logger.debug("old sdk logout failed (ignored): %s", e)

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
            raise RuntimeError("Fubon SDK not available")
        # async 版 acquire — 阻塞版丟 to_thread 會在限流排隊時占住 default
        # thread pool worker,連坐其他 to_thread 操作（WS subscribe/relogin）
        await get_rate_limiter().acquire_async()
        return await asyncio.to_thread(
            self._sdk.marketdata.rest_client.stock.intraday.quote,
            symbol=symbol,
        )


# Module-level singleton
_client: FubonClient | None = None


def get_fubon() -> FubonClient:
    """Lazy singleton accessor."""
    global _client
    if _client is None:
        _client = FubonClient()
    return _client
