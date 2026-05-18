"""訊號 evaluator — 消費 tick → 跑 WindowCondition + Filter.conditions → 達成 fan-out。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Any

from models.condition import (
    ActiveSignalOut, Condition, Filter, WindowCondition,
)
from services import alerts
from services.cdp import get_cdp_service
from services.ring_buffer import Tick, get_ring_buffer
from services.supabase_client import get_supabase
from services.user_context import get_user_label
from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)

QUEUE_MAXSIZE = 5000
BACKPRESSURE_LAG_MS = 5000
BACKPRESSURE_DURATION_S = 30
HEARTBEAT_INTERVAL_S = 1.0


class SignalEngine:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, Tick]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._consumer: asyncio.Task | None = None
        self._monitor: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._active: list[ActiveSignalOut] = []
        # cooldown: (active_signal_id, symbol) → last_triggered_at (epoch s)
        self._cooldown: dict[tuple[str, str], float] = {}
        # in-memory cache: symbol → field → value (indicator + cdp 共用)
        self._field_cache: dict[str, dict[str, float]] = {}
        # 上次 refill field_cache 的本地日期 — heartbeat 跨午夜時自動重 refill
        # 解 24/7 backend 不重啟時 cdp_* 永遠停在前一天的 stale 問題
        self._last_field_refill_date: date | None = None
        # metrics
        self._dropped_today = 0
        self._last_lag_ms = 0.0
        self._lag_violation_started: float | None = None
        self._degraded = False

    # ---------- 公開 API ----------

    async def start(self) -> None:
        from services.fubon_ws import get_ws_pool
        get_ws_pool().set_tick_callback(self.enqueue)
        self._consumer = asyncio.create_task(self._consume_loop())
        self._monitor = asyncio.create_task(self._monitor_loop())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        await self.refresh_active_signals()
        logger.info("SignalEngine started")

    async def shutdown(self) -> None:
        for t in (self._consumer, self._monitor, self._heartbeat):
            if t and not t.done():
                t.cancel()

    async def enqueue(self, symbol: str, tick: Tick) -> None:
        try:
            self._queue.put_nowait((symbol, tick))
        except asyncio.QueueFull:
            self._dropped_today += 1

    async def refresh_active_signals(self) -> None:
        """從 supabase 讀 enabled active_signals，刷新 in-memory list 跟 field cache。"""
        sb = get_supabase()
        if sb.client is None:
            self._active = []
            return
        res = await asyncio.to_thread(
            lambda: sb.client.table("active_signals")
            .select("id, name, filter_json, scope, cooldown_seconds, enabled, created_at")
            .eq("user_label", get_user_label())
            .eq("enabled", True)
            .execute()
        )
        rows = res.data or []
        self._active = [self._row_to_active(r) for r in rows]
        await self._refill_field_cache()
        logger.info("active_signals reloaded: %d enabled", len(self._active))

    def health(self) -> dict[str, Any]:
        return {
            "queue_depth": self._queue.qsize(),
            "lag_ms": int(self._last_lag_ms),
            "dropped_today": self._dropped_today,
            "degraded": self._degraded,
            "active_count": len(self._active),
        }

    # ---------- internal ----------

    def _row_to_active(self, r: dict) -> ActiveSignalOut:
        return ActiveSignalOut(
            id=r["id"], name=r["name"],
            filter_json=r["filter_json"], scope=r["scope"],
            cooldown_seconds=r.get("cooldown_seconds", 1800),
            enabled=r.get("enabled", True),
            created_at=str(r.get("created_at", "")),
        )

    async def _refill_field_cache(self) -> None:
        """為每個 active 涉及的 symbol 載入 cdp_* 值進 cache。

        close 走即時 tick.price(由 _eval_filter_cond 處理),不進 field_cache。
        """
        sb = get_supabase()
        if sb.client is None:
            return

        # 蒐集所有 active 涉及的 symbol
        symbols_needed: set[str] = set()
        watchlist_fetched = False  # 只查一次 watchlist
        for a in self._active:
            scope = a.scope
            # scope 可以是 dict（舊路徑）或 Pydantic model（_row_to_active 驗證後）
            if isinstance(scope, dict):
                scope_type = scope.get("type")
                scope_symbols = scope.get("symbols", [])
            else:
                scope_type = getattr(scope, "type", None)
                scope_symbols = getattr(scope, "symbols", [])
            if scope_type == "symbols":
                symbols_needed.update(scope_symbols)
            elif scope_type == "watchlist" and not watchlist_fetched:
                # watchlist 全部（限定本 instance 的 user_label）
                res = await asyncio.to_thread(
                    lambda: sb.client.table("watchlist")
                    .select("symbol")
                    .eq("user_label", get_user_label())
                    .execute()
                )
                for row in (res.data or []):
                    symbols_needed.add(row["symbol"])
                watchlist_fetched = True

        # cdp 5 值
        cdp = get_cdp_service()
        for sym in symbols_needed:
            levels = await cdp.get(sym)
            if levels:
                d = self._field_cache.setdefault(sym, {})
                d["cdp_ah"] = levels["ah"]
                d["cdp_nh"] = levels["nh"]
                d["cdp"] = levels["cdp"]
                d["cdp_nl"] = levels["nl"]
                d["cdp_al"] = levels["al"]

        self._last_field_refill_date = date.today()

    async def _consume_loop(self) -> None:
        """主消費迴圈 — 從 queue 拉 tick → evaluate → fan-out。"""
        while True:
            try:
                symbol, tick = await self._queue.get()
            except asyncio.CancelledError:
                return
            # lag 只在 tick-driven path 計（heartbeat 用的是舊 tick，會誤判 backpressure）
            self._last_lag_ms = (time.time() - tick.time) * 1000.0
            await self._evaluate(symbol, tick)

    async def _heartbeat_loop(self) -> None:
        """每秒對所有 active scope 內的 symbol 用 ring_buffer 最新 tick 重評估一次。

        補 tick-driven 在「視窗滑動」場景的盲點：視窗剛滑出某筆 tick / 視窗起點漂走，
        但沒有新成交時，原本要等下一筆 tick 才被偵測。cooldown 沿用既有機制，不重複觸發。

        同時負責每日 field_cache refill：跨午夜後第一個 heartbeat 觸發
        _refill_field_cache，確保 cdp_* 用最新一日的值。
        """
        rb = get_ring_buffer()
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            except asyncio.CancelledError:
                return

            # Daily field_cache refresh — 跨午夜後第一個 heartbeat 觸發
            today = date.today()
            if self._last_field_refill_date != today:
                try:
                    await self._refill_field_cache()
                    logger.info("signal_engine: daily field_cache refilled for %s", today)
                except Exception as e:
                    # refill 失敗不影響 heartbeat — 下個 heartbeat 會再試
                    logger.warning("signal_engine: daily field_cache refill failed: %s", e)

            symbols: set[str] = set()
            for a in self._active:
                symbols.update(self._scope_symbols(a))
            for symbol in symbols:
                tick = rb.latest(symbol)
                if tick is None:
                    continue
                await self._evaluate(symbol, tick)

    def _scope_symbols(self, active: ActiveSignalOut) -> list[str]:
        s = active.scope
        if isinstance(s, dict):
            t = s.get("type")
            if t == "watchlist":
                return list(self._field_cache.keys())
            if t == "symbols":
                return list(s.get("symbols", []))
        else:
            t = getattr(s, "type", None)
            if t == "watchlist":
                return list(self._field_cache.keys())
            if t == "symbols":
                return list(getattr(s, "symbols", []))
        return []

    async def _evaluate(self, symbol: str, tick: Tick) -> None:
        """對每個涉及這 symbol 的 active_signal 跑條件。"""
        for active in self._active:
            if not self._scope_includes(active, symbol):
                continue
            if not self._eval_conditions(active, symbol, tick):
                continue
            # cooldown 檢查
            key = (active.id, symbol)
            now = time.time()
            last_ts = self._cooldown.get(key, 0)
            if now - last_ts < active.cooldown_seconds:
                continue
            self._cooldown[key] = now
            await self._fanout(active, symbol, tick)

    def _scope_includes(self, active: ActiveSignalOut, symbol: str) -> bool:
        s = active.scope
        # scope 可以是 dict（從 DB JSON 讀）或 Pydantic model（直接構建）
        if isinstance(s, dict):
            t = s.get("type")
            syms = s.get("symbols", [])
        else:
            t = getattr(s, "type", None)
            syms = getattr(s, "symbols", [])
        if t == "watchlist":
            return symbol in self._field_cache  # watchlist refill 過就在
        if t == "symbols":
            return symbol in syms
        return False

    def _eval_conditions(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
        # WindowCondition + Filter.conditions + CdpProximity
        f = active.filter_json
        results: list[bool] = []
        for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
            results.append(self._eval_window(symbol, tick, wc))
        for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
            results.append(self._eval_filter_cond(symbol, tick, c))
        cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                    else getattr(f, "cdp_proximity", None))
        if cdp_prox is not None:
            results.append(self._eval_cdp_proximity(symbol, tick, cdp_prox))
        if not results:
            return False
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
        return all(results) if logic == "AND" else any(results)

    def _eval_cdp_proximity(self, symbol: str, tick: Tick, prox) -> bool:
        """tick.price 落在所選 CDP 線的 ±N tick 範圍內就 true。

        prox 可以是 dict(從 filter_json JSON 讀)或 Pydantic CdpProximityCondition。
        """
        from services.cdp import tick_size

        cache = self._field_cache.get(symbol, {})
        levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
        tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                     else prox.tolerance_ticks)

        field_map = {
            "ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
            "nl": "cdp_nl", "al": "cdp_al",
        }
        for level in levels:
            v = cache.get(field_map[level])
            if v is None:
                continue
            tol = tol_ticks * tick_size(v)
            if abs(tick.price - v) <= tol:
                return True
        return False

    def _eval_window(self, symbol: str, tick: Tick, wc) -> bool:
        wc_type = wc.get("type") if isinstance(wc, dict) else wc.type
        wc_secs = wc.get("window_seconds") if isinstance(wc, dict) else wc.window_seconds
        op = wc.get("operator") if isinstance(wc, dict) else wc.operator
        val = wc.get("value") if isinstance(wc, dict) else wc.value

        ticks = get_ring_buffer().window(symbol, seconds=wc_secs)
        if not ticks:
            return False

        if wc_type == "price_change_pct":
            start = ticks[0].price
            if start == 0:
                return False
            actual = (tick.price - start) / start * 100
            return _cmp(actual, op, val)
        if wc_type == "volume_burst":
            current_vol = sum(t.size for t in ticks)
            return _cmp(current_vol, op, val)  # 簡化：跟絕對 value 比，未來可加歷史平均
        if wc_type == "trade_count":
            return _cmp(len(ticks), op, val)
        return False

    def _eval_filter_cond(self, symbol: str, tick: Tick, c) -> bool:
        field = c.get("field") if isinstance(c, dict) else c.field
        op = c.get("operator") if isinstance(c, dict) else c.operator
        value = c.get("value") if isinstance(c, dict) else c.value

        # field 'close' 用即時 tick.price，其他從 cache
        if field == "close":
            lhs = tick.price
        else:
            lhs = self._field_cache.get(symbol, {}).get(field)
        if lhs is None:
            return False

        if isinstance(value, str):
            # 跨欄位（含 cdp_*）
            if value == "close":
                rhs = tick.price
            else:
                rhs = self._field_cache.get(symbol, {}).get(value)
            if rhs is None:
                return False
        else:
            rhs = float(value)

        return _cmp(lhs, op, rhs)

    async def _fanout(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> None:
        from services.supabase_writer import get_supabase_writer
        payload = {
            "event": "signal",
            "data": {
                "active_signal_id": active.id,
                "active_signal_name": active.name,
                "symbol": symbol,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "trigger_price": tick.price,
                "trigger_volume": tick.size,
            },
        }
        # 1. 前端 WS broadcast
        await get_broadcaster().broadcast(payload)
        # 2. supabase writer
        get_supabase_writer().append({
            "active_signal_id": active.id,
            "symbol": symbol,
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
            "context_json": {"latest_tick_time": tick.time},
            "user_label": get_user_label(),
        })

    async def _monitor_loop(self) -> None:
        """監控 lag — 超過 5s 連續 30s → 自動 disable + alerts。"""
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return
            if self._last_lag_ms > BACKPRESSURE_LAG_MS:
                if self._lag_violation_started is None:
                    self._lag_violation_started = time.time()
                elif time.time() - self._lag_violation_started > BACKPRESSURE_DURATION_S:
                    if not self._degraded:
                        await self._auto_disable_all()
            else:
                self._lag_violation_started = None

    async def _auto_disable_all(self) -> None:
        sb = get_supabase()
        if sb.client is None:
            return
        try:
            await asyncio.to_thread(
                lambda: sb.client.table("active_signals")
                .update({"enabled": False})
                .eq("user_label", get_user_label())
                .eq("enabled", True)
                .execute()
            )
        except Exception as e:
            logger.error("auto disable failed: %s", e)
        self._active = []
        self._degraded = True
        await alerts.notify_critical(
            "evaluator overload — all active_signals auto-disabled",
            lag_ms=str(self._last_lag_ms),
        )


def _cmp(lhs: float, op: str, rhs: float) -> bool:
    if op == "gt": return lhs > rhs
    if op == "gte": return lhs >= rhs
    if op == "lt": return lhs < rhs
    if op == "lte": return lhs <= rhs
    if op == "eq": return lhs == rhs
    return False


_engine: SignalEngine | None = None


def get_signal_engine() -> SignalEngine:
    global _engine
    if _engine is None:
        _engine = SignalEngine()
    return _engine
