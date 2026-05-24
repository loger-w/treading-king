"""期貨 WS 連線管理(MXF 近月)。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from services.fubon_futures import determine_current_session

logger = logging.getLogger(__name__)

TPE = ZoneInfo("Asia/Taipei")
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30, 60]


def target_after_hours_flag(now: datetime) -> Optional[bool]:
    """根據 now 回傳訂閱所需的 afterHours 旗標,或 None 表示「目前休市、不訂閱」。"""
    sess = determine_current_session(now)
    if sess == "day":
        return False
    if sess == "night":
        return True
    return None


class FuturesWSPool:
    """單例:管理 MXF 近月的 WS 連線。

    - 訂閱 candles channel(timeframe 由富邦推、不參與)
    - Session 邊界(15:00 / 05:00 / 08:45 / 13:45)自動 re-subscribe
    - 收到推送 → broadcast 給前端
    - 斷線指數重試
    """

    def __init__(self) -> None:
        self._ws = None  # type: ignore[assignment]
        self._symbol: Optional[str] = None
        self._current_after_hours: Optional[bool] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reconnect_attempt = 0
        self._reconnecting: bool = False
        self._lock = asyncio.Lock()

    async def start(self, symbol: str) -> None:
        """啟動 / 切換到指定 symbol。"""
        async with self._lock:
            self._loop = asyncio.get_running_loop()
            if self._symbol != symbol:
                await self._teardown_ws()
                self._symbol = symbol
            await self._ensure_subscribed_for_now()

    async def stop(self) -> None:
        async with self._lock:
            await self._teardown_ws()
            self._symbol = None

    async def reconcile_session(self) -> None:
        """供 scheduler 在 session 邊界呼叫,確認訂閱對齊現況。"""
        async with self._lock:
            await self._ensure_subscribed_for_now()

    # ---------------- internals ----------------

    async def _ensure_subscribed_for_now(self) -> None:
        if self._symbol is None:
            return
        now = datetime.now(TPE)
        want = target_after_hours_flag(now)

        if want is None:
            # 休市 → 取消訂閱(若有)
            await self._teardown_ws()
            return

        if self._ws is not None and self._current_after_hours == want:
            return  # 已是正確訂閱,不動

        await self._teardown_ws()
        await self._subscribe(want)

    async def _subscribe(self, after_hours: bool) -> None:
        # Lazy imports 避免 test env httpx 缺失
        from services.fubon_client import get_fubon  # noqa: PLC0415

        fubon = get_fubon()
        if fubon.sdk is None:
            logger.warning("futures_ws: fubon sdk not initialized")
            return
        try:
            ws = fubon.sdk.marketdata.websocket_client.futopt
            ws.on("message", self._on_message_raw)
            ws.on("disconnect", self._on_disconnect_raw)
            await asyncio.to_thread(ws.connect)
            await asyncio.to_thread(
                ws.subscribe,
                {"channel": "candles", "symbol": self._symbol, "afterHours": after_hours},
            )
            self._ws = ws
            self._current_after_hours = after_hours
            self._reconnect_attempt = 0
            logger.info("futures_ws subscribed symbol=%s afterHours=%s", self._symbol, after_hours)
        except Exception as e:
            logger.warning("futures_ws subscribe failed: %s", e)
            # 不要直接 await,因為當前 path 在 _lock 內,_schedule_reconnect 也會 acquire lock → deadlock
            asyncio.create_task(self._schedule_reconnect())

    async def _teardown_ws(self) -> None:
        if self._ws is None:
            return
        try:
            await asyncio.to_thread(self._ws.disconnect)
        except Exception as e:
            logger.debug("futures_ws disconnect raised: %s", e)
        finally:
            self._ws = None
            self._current_after_hours = None

    # 富邦 SDK 是 sync callback。bridge 回 asyncio。
    def _on_message_raw(self, message) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(asyncio.create_task, self._handle_message(message))
        except RuntimeError:
            pass

    def _on_disconnect_raw(self, *_args) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(asyncio.create_task, self._handle_disconnect())
        except RuntimeError:
            pass

    async def _handle_message(self, raw) -> None:
        # raw 結構(預期):{"event": "data", "channel": "candles",
        #                  "data": {"symbol": "MXFF6", "date": "...", "open": ..., ...}}
        from ws_broadcaster import get_broadcaster  # noqa: PLC0415
        try:
            data = raw.get("data") if isinstance(raw, dict) else None
            if not data:
                return
            symbol = data.get("symbol")
            if symbol != self._symbol:
                return
            await get_broadcaster().broadcast({
                "event": "mxf_candle",
                "data": {
                    "symbol": symbol,
                    "candle": {
                        "date": data.get("date"),
                        "open": data.get("open"),
                        "high": data.get("high"),
                        "low": data.get("low"),
                        "close": data.get("close"),
                        "volume": data.get("volume", 0),
                        "average": data.get("average", 0),
                    },
                },
            })
        except Exception as e:
            logger.warning("futures_ws handle_message error: %s", e)

    async def _handle_disconnect(self) -> None:
        logger.info("futures_ws disconnected, scheduling reconnect")
        async with self._lock:
            self._ws = None
            self._current_after_hours = None
        await self._schedule_reconnect()

    async def _schedule_reconnect(self) -> None:
        if self._symbol is None:
            return
        if self._reconnecting:
            return  # 已有進行中的 reconnect task,別累積
        self._reconnecting = True
        try:
            delay = RECONNECT_DELAYS[min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
            self._reconnect_attempt += 1
            await asyncio.sleep(delay)
            async with self._lock:
                await self._ensure_subscribed_for_now()
        finally:
            self._reconnecting = False


_pool: Optional[FuturesWSPool] = None


def get_futures_ws_pool() -> FuturesWSPool:
    global _pool
    if _pool is None:
        _pool = FuturesWSPool()
    return _pool


async def session_reconcile_loop() -> None:
    """每分鐘檢查 session,跨邊界時切訂閱。Startup 後 fire-and-forget 跑。"""
    pool = get_futures_ws_pool()
    while True:
        try:
            await pool.reconcile_session()
        except Exception as e:
            logger.warning("session_reconcile_loop error: %s", e)
        await asyncio.sleep(60)
