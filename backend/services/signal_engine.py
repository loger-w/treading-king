"""訊號 evaluator — 消費 tick → 跑 WindowCondition + Filter.conditions → 達成 fan-out。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from models.condition import (
    ActiveSignalOut, Condition, Filter, WindowCondition,
)
from services import alerts, ma_service
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

# 正盤定義為週一~五 09:00 ≤ t < 13:30(台北時間),半開區間跟 PRE_OPEN/MARKET_OPEN
# 對稱。試撮 / 盤後 / 隔夜 / 週末皆不評估訊號:
#   - 試撮(08:30-09:00):Fubon WS 推 indicative tick,實際沒成交
#   - 盤後(>= 13:30):heartbeat 用 ring_buffer.latest 重評估,但 latest 永遠停
#     在收盤那筆 stale tick — 收盤價落在 proximity tolerance 內時每 cooldown
#     會重複觸發訊號(直到隔日 8:30 試撮 gate 才再擋,中間有 19 小時假訊號窗)
TAIPEI_TZ = timezone(timedelta(hours=8))
MARKET_OPEN  = (9, 0)
MARKET_CLOSE = (13, 30)


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
        # 上一筆 tick per-symbol,供 _direction_of_touch 判方向(壓力 / 支撐)
        self._prev_tick: dict[str, Tick] = {}
        # 當天觸碰次數計數 (symbol, level, date) → count,跨日 GC
        self._cdp_touch_count: dict[tuple[str, str, date], int] = {}
        self._ma_touch_count:  dict[tuple[str, str, date], int] = {}
        # 今日累積成交量 — backend 啟動後累積,daily refill 重置
        self._day_volume: dict[str, int] = {}
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

    async def _load_monitor_symbols(self) -> set[str]:
        """從 monitor_list 拉本 user 的所有監聽 symbol。"""
        sb = get_supabase()
        if sb.client is None:
            return set()
        res = await asyncio.to_thread(
            lambda: sb.client.table("monitor_list")
            .select("symbol")
            .eq("user_label", get_user_label())
            .execute()
        )
        return {r["symbol"] for r in (res.data or [])}

    async def _refill_field_cache(self) -> None:
        """為 monitor_list 內的 symbol 載入 cdp_* + sma 進 cache。

        close 走即時 tick.price(由 _eval_filter_cond 處理),不進 field_cache。
        """
        symbols_needed: set[str] = await self._load_monitor_symbols()

        # cdp 5 值 + 昨日收盤(供 day_change_pct 算式分母)
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
                d["prev_close"] = levels["prev_close"]

        # sma 5 / 20(失敗欄位回 None,不寫 cache)
        for sym in symbols_needed:
            sma_5, sma_20 = await ma_service.fetch_sma_5_20(sym)
            if sma_5 is not None or sma_20 is not None:
                d = self._field_cache.setdefault(sym, {})
                if sma_5  is not None: d["sma_5"]  = sma_5
                if sma_20 is not None: d["sma_20"] = sma_20

        # 跨午夜後重新累積今日成交量(跟 _gc_touch_counts 一樣的 daily reset 邏輯)
        self._day_volume.clear()
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
                    self._gc_touch_counts()  # 順便清舊 date 的 touch_count
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
        """所有 rule 共用 monitor_list;heartbeat 用此列舉 candidate symbols。"""
        return list(self._field_cache.keys())

    async def _evaluate(self, symbol: str, tick: Tick) -> None:
        """對每個涉及這 symbol 的 active_signal 跑條件,觸發時帶 touch metadata fanout。"""
        if not self._in_trading_session(time.time()):
            # 非正盤時段(試撮 / 盤後 / 隔夜 / 週末)直接 return — 不評估 / 不累積量 /
            # 不更新 prev_tick。用 wall-clock(time.time)而非 tick.time,
            # heartbeat path 拿到收盤 stale tick 時也能正確擋下。
            return

        # 正盤內才累積今日總量,避免試撮 / 盤後 stale tick 污染
        self._day_volume[symbol] = self._day_volume.get(symbol, 0) + max(0, tick.size)

        prev = self._prev_tick.get(symbol)
        try:
            for active in self._active:
                if not self._scope_includes(active, symbol):
                    continue

                cdp_touch, ma_touch = self._eval_with_touch_meta(active, symbol, tick, prev)
                non_prox_ok = self._eval_non_proximity(active, symbol, tick)

                # 邏輯結合(AND/OR)— 任一觸發機制成立才往下走
                ok = self._combine_results(active, cdp_touch, ma_touch, non_prox_ok)
                if not ok:
                    continue

                # cooldown 檢查
                key = (active.id, symbol)
                now = time.time()
                last_ts = self._cooldown.get(key, 0)
                if now - last_ts < active.cooldown_seconds:
                    continue
                self._cooldown[key] = now

                # touch_count(僅 proximity 觸發才計次)
                today = date.today()
                if cdp_touch is not None:
                    count_key = (symbol, cdp_touch["level"], today)
                    self._cdp_touch_count[count_key] = self._cdp_touch_count.get(count_key, 0) + 1
                    cdp_touch["touch_index"] = self._cdp_touch_count[count_key]
                if ma_touch is not None:
                    count_key = (symbol, ma_touch["level"], today)
                    self._ma_touch_count[count_key] = self._ma_touch_count.get(count_key, 0) + 1
                    ma_touch["touch_index"] = self._ma_touch_count[count_key]

                await self._fanout(active, symbol, tick, cdp_touch, ma_touch)
        finally:
            # 用 finally 保證每次 evaluate 都更新 prev,避免下次方向算錯
            self._prev_tick[symbol] = tick

    def _eval_with_touch_meta(
        self, active: ActiveSignalOut, symbol: str, tick: Tick, prev: Tick | None,
    ) -> tuple[dict | None, dict | None]:
        """跑 cdp/ma proximity,回 (cdp_touch_dict, ma_touch_dict) 含方向 + role。

        None 表示該 proximity 沒設或沒命中。
        """
        f = active.filter_json

        cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                    else getattr(f, "cdp_proximity", None))
        cdp_touch: dict | None = None
        if cdp_prox is not None:
            ok, level = self._eval_cdp_proximity(symbol, tick, cdp_prox)
            if ok and level is not None:
                field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
                             "nl": "cdp_nl", "al": "cdp_al"}
                v = self._field_cache.get(symbol, {}).get(field_map[level])
                direction = self._direction_of_touch(prev, tick, v) if v is not None else "horizontal"
                role = {"from_below": "resistance", "from_above": "support"}.get(direction, "touch")
                cdp_touch = {"level": level, "direction": direction, "role": role}

        ma_prox = (f.get("ma_proximity") if isinstance(f, dict)
                   else getattr(f, "ma_proximity", None))
        ma_touch: dict | None = None
        if ma_prox is not None:
            ok, level = self._eval_ma_proximity(symbol, tick, ma_prox)
            if ok and level is not None:
                v = self._field_cache.get(symbol, {}).get(level)
                direction = self._direction_of_touch(prev, tick, v) if v is not None else "horizontal"
                role = {"from_below": "resistance", "from_above": "support"}.get(direction, "touch")
                ma_touch = {"level": level, "direction": direction, "role": role}

        return cdp_touch, ma_touch

    def _eval_non_proximity(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool | None:
        """跑非 proximity 的條件(window + cross-field)。

        回 None 表示沒設這類條件;True/False 表示有設且整體 logic 通不通過。
        """
        f = active.filter_json
        results: list[bool] = []
        for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
            results.append(self._eval_window(symbol, tick, wc))
        for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
            results.append(self._eval_filter_cond(symbol, tick, c))
        if not results:
            return None
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
        return all(results) if logic == "AND" else any(results)

    def _combine_results(
        self, active: ActiveSignalOut,
        cdp_touch: dict | None, ma_touch: dict | None, non_prox_ok: bool | None,
    ) -> bool:
        """把 non-proximity / cdp_proximity / ma_proximity 結果合在一起。

        - 完全沒設任何條件 → False(filter 不可能空,但保險)
        - 用 AND:全部「有設且 True」才 True
        - 用 OR :任一「有設且 True」就 True
        """
        f = active.filter_json
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))

        # 子條件結果(None = 沒設、True/False = 有設且結果)
        sub_results: list[bool] = []
        if non_prox_ok is not None:
            sub_results.append(non_prox_ok)
        # proximity:有設(prox 物件 not None)就一定有 None/dict 結果
        cdp_prox_set = ((f.get("cdp_proximity") if isinstance(f, dict)
                         else getattr(f, "cdp_proximity", None)) is not None)
        if cdp_prox_set:
            sub_results.append(cdp_touch is not None)
        ma_prox_set = ((f.get("ma_proximity") if isinstance(f, dict)
                        else getattr(f, "ma_proximity", None)) is not None)
        if ma_prox_set:
            sub_results.append(ma_touch is not None)

        if not sub_results:
            return False
        return all(sub_results) if logic == "AND" else any(sub_results)

    def _scope_includes(self, active: ActiveSignalOut, symbol: str) -> bool:
        """所有 rule 共用 monitor_list;field_cache key = monitor_list union。"""
        return symbol in self._field_cache

    def _eval_conditions(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
        # WindowCondition + Filter.conditions + CdpProximity + MAProximity
        f = active.filter_json
        results: list[bool] = []
        for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
            results.append(self._eval_window(symbol, tick, wc))
        for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
            results.append(self._eval_filter_cond(symbol, tick, c))
        cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                    else getattr(f, "cdp_proximity", None))
        if cdp_prox is not None:
            ok, _ = self._eval_cdp_proximity(symbol, tick, cdp_prox)
            results.append(ok)
        ma_prox = (f.get("ma_proximity") if isinstance(f, dict)
                   else getattr(f, "ma_proximity", None))
        if ma_prox is not None:
            ok, _ = self._eval_ma_proximity(symbol, tick, ma_prox)
            results.append(ok)
        if not results:
            return False
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
        return all(results) if logic == "AND" else any(results)

    def _gc_touch_counts(self) -> None:
        """清掉非當天的 touch_count key — 跨午夜 heartbeat 呼叫。"""
        today = date.today()
        self._cdp_touch_count = {
            k: v for k, v in self._cdp_touch_count.items() if k[2] == today
        }
        self._ma_touch_count = {
            k: v for k, v in self._ma_touch_count.items() if k[2] == today
        }

    @staticmethod
    def _in_trading_session(now_ts: float) -> bool:
        """現在時間是否在正盤(週一~五 09:00 ≤ t < 13:30,台北時間)。

        caller 應傳 wall-clock(time.time())而非 tick.time — heartbeat path 的
        latest tick 可能是收盤 / 昨日 stale tick,用 tick.time 會誤把盤後算成盤中。
        """
        dt = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
        if dt.weekday() >= 5:  # 週末不開盤
            return False
        return MARKET_OPEN <= (dt.hour, dt.minute) < MARKET_CLOSE

    @staticmethod
    def _direction_of_touch(prev: Tick | None, curr: Tick, threshold: float) -> str:
        """判斷 curr.price 相對 threshold 從哪個方向跨越過來。

        回傳 "from_below" / "from_above" / "horizontal"。
        """
        if prev is None:
            return "horizontal"
        if prev.price < threshold and curr.price >= threshold:
            return "from_below"
        if prev.price > threshold and curr.price <= threshold:
            return "from_above"
        return "horizontal"

    def _eval_cdp_proximity(self, symbol: str, tick: Tick, prox) -> tuple[bool, str | None]:
        """tick.price 落在所選 CDP 線的 ±N tick 範圍內 → (True, 哪條觸發)。

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
                return True, level
        return False, None

    def _eval_ma_proximity(self, symbol: str, tick: Tick, prox) -> tuple[bool, str | None]:
        """tick.price 落在所選 MA 線的 ±N tick 範圍內 → (True, 哪條觸發)。

        cache 內 sma 是 raw 算術平均,常落在非合法 tick;tolerance=0 實務上很難命中。
        """
        from services.cdp import tick_size

        cache = self._field_cache.get(symbol, {})
        levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
        tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                     else prox.tolerance_ticks)

        for level in levels:  # "sma_5" or "sma_20"
            v = cache.get(level)
            if v is None:
                continue
            tol = tol_ticks * tick_size(v)
            if abs(tick.price - v) <= tol:
                return True, level
        return False, None

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

        lhs = self._resolve_field(symbol, tick, field)
        if lhs is None:
            return False

        if isinstance(value, str):
            # 跨欄位比較
            rhs = self._resolve_field(symbol, tick, value)
            if rhs is None:
                return False
        else:
            rhs = float(value)

        return _cmp(lhs, op, rhs)

    def _resolve_field(self, symbol: str, tick: Tick, field: str) -> float | None:
        """欄位 → 數值。close / day_* 動態算,其他走 _field_cache。"""
        if field == "close":
            return tick.price
        if field == "day_change_pct":
            prev = self._field_cache.get(symbol, {}).get("prev_close")
            if prev is None or prev == 0:
                return None
            return (tick.price - prev) / prev * 100.0
        if field == "day_volume":
            return float(self._day_volume.get(symbol, 0))
        return self._field_cache.get(symbol, {}).get(field)

    async def _fanout(
        self, active: ActiveSignalOut, symbol: str, tick: Tick,
        cdp_touch: dict | None = None, ma_touch: dict | None = None,
    ) -> None:
        from services.supabase_writer import get_supabase_writer
        data: dict = {
            "active_signal_id": active.id,
            "active_signal_name": active.name,
            "symbol": symbol,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
        }
        if cdp_touch: data["cdp_touch"] = cdp_touch
        if ma_touch:  data["ma_touch"]  = ma_touch
        payload = {"event": "signal", "data": data}
        # 1. 前端 WS broadcast
        await get_broadcaster().broadcast(payload)
        # 2. supabase writer
        context: dict = {"latest_tick_time": tick.time}
        if cdp_touch: context["cdp_touch"] = cdp_touch
        if ma_touch:  context["ma_touch"]  = ma_touch
        get_supabase_writer().append({
            "active_signal_id": active.id,
            "symbol": symbol,
            "trigger_price": tick.price,
            "trigger_volume": tick.size,
            "context_json": context,
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
