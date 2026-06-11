"""期貨 WS 連線管理(MXF 近月)。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from services.fubon_futures import determine_current_session, resolve_active_symbol

logger = logging.getLogger(__name__)

TPE = ZoneInfo("Asia/Taipei")
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30, 60]
MAX_RECONNECT_ATTEMPTS = 8  # 用完後放棄,直到下一次 session boundary 重置


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
        # SDK 對主動 close 也會 fire disconnect 事件;記下 pending 的主動斷線數,
        # 讓 _handle_disconnect 不把 session 切換的 teardown 當故障去清狀態+重連
        self._expected_disconnects = 0
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
        """供 scheduler 在 session 邊界呼叫,確認訂閱對齊現況。

        每次 reconcile 都重置 reconnect 計數 — 讓「之前放棄的 transient 故障」
        每分鐘獲得一次完整重試的機會;若是不可恢復的 config 錯誤,也只會持續
        每分鐘看到一輪錯誤、不會 hot loop。
        """
        async with self._lock:
            self._reconnect_attempt = 0
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
        self._expected_disconnects += 1
        try:
            await asyncio.to_thread(self._ws.disconnect)
        except Exception as e:
            # close 失敗時 disconnect 事件多半不會來,把計數補回去免得吞掉下次真斷線
            self._expected_disconnects -= 1
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
        # SDK 的 message event 給的是原始 JSON 字串(client emit 前不 parse),
        # 必須先 json.loads;dict 也照收以保相容。payload 結構(預期):
        # {"event": "data", "channel": "candles",
        #  "data": {"symbol": "MXFF6", "date": "...", "open": ..., ...}}
        from ws_broadcaster import get_broadcaster  # noqa: PLC0415
        try:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                # 丟棄時留 log,避免格式變動造成推送靜默失效
                logger.warning("futures_ws dropping non-JSON message: %.200s", raw)
                return
            if not isinstance(payload, dict):
                logger.warning(
                    "futures_ws dropping unexpected message type: %s", type(raw).__name__
                )
                return
            data = payload.get("data")
            if not data or not isinstance(data, dict):
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
        async with self._lock:
            if self._expected_disconnects > 0:
                # 主動 teardown 引發的事件 — 不是故障,別清掉剛建好的訂閱也別重連
                self._expected_disconnects -= 1
                return
            self._ws = None
            self._current_after_hours = None
        logger.info("futures_ws disconnected, scheduling reconnect")
        await self._schedule_reconnect()

    async def _schedule_reconnect(self) -> None:
        if self._symbol is None:
            return
        if self._reconnecting:
            return  # 已有進行中的 reconnect task,別累積
        if self._reconnect_attempt >= MAX_RECONNECT_ATTEMPTS:
            # 達上限 — 不再 hot retry,等下次 reconcile_session(60s 排程)重置計數
            logger.error(
                "futures_ws giving up after %d failed attempts; will retry on next session reconcile",
                self._reconnect_attempt,
            )
            return
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


async def reconcile_pool(pool: FuturesWSPool) -> None:
    """單次 reconcile:近月 symbol 變了就切換訂閱,否則只對齊 session。

    沒有這步,合約換月(或 startup 解析失敗)後 WS 會永遠停在舊 symbol 直到重啟。
    resolve_active_symbol 有 1h cache,每分鐘呼叫的額度成本可忽略;
    回 None(SDK 掛/查不到)時不動 symbol,維持既有訂閱。
    """
    symbol = await resolve_active_symbol()
    if symbol and symbol != pool._symbol:
        await pool.start(symbol)
    else:
        await pool.reconcile_session()


async def session_reconcile_loop() -> None:
    """每分鐘檢查 session 與近月 symbol,跨邊界/換月時切訂閱。Startup 後 fire-and-forget 跑。"""
    pool = get_futures_ws_pool()
    while True:
        try:
            await reconcile_pool(pool)
        except Exception as e:
            logger.warning("session_reconcile_loop error: %s", e)
        await asyncio.sleep(60)
