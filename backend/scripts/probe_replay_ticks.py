"""端到端 replay — 拉今日 1 分 K 跑 user 的 active_signals + watchlist。

盤後可跑。讀 supabase + Fubon `intraday.candles` API。
不寫 signals_log / 不 WS broadcast(monkey-patch fanout)。
不啟動 ws_pool / writer / engine background tasks。

關鍵設計:FakeClock module-level patch ring_buffer/signal_engine 的 time,
讓歷史時間 candle 也能正確跑 ring_buffer.window() 跟 cooldown 邏輯。
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

backend = Path(__file__).resolve().parent.parent
load_dotenv(backend / ".env")
sys.path.insert(0, str(backend))

import services.ring_buffer as rb_mod
import services.signal_engine as se_mod
from services.fubon_client import get_fubon
from services.ring_buffer import Tick, get_ring_buffer
from services.signal_engine import get_signal_engine
from services.supabase_client import get_supabase
from services.user_context import get_user_label


class FakeClock:
    """Module-level monkey-patch target — 取代 ring_buffer/signal_engine 的 time 模組。"""

    def __init__(self) -> None:
        self.now: float = 0.0

    def time(self) -> float:
        return self.now


@dataclass
class Trigger:
    triggered_at: float       # epoch seconds (tick.time)
    symbol: str
    active_signal_id: str
    active_signal_name: str
    trigger_price: float
    trigger_volume: int
    summary: str              # window/cache context (human-readable)


def _get(obj, key, default=None):
    """從 Pydantic model 或 dict 取屬性。filter_json 從 DB 讀回是 dict,直接 mock 是 model。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _summary(active, symbol, tick) -> str:
    """產 human-readable 觸發摘要 — 給 report 用。"""
    parts: list[str] = []
    f = active.filter_json
    rb = get_ring_buffer()
    engine = get_signal_engine()
    cache = engine._field_cache.get(symbol, {})

    wcs = _get(f, "window_conditions", []) or []
    for wc in wcs:
        wc_type = _get(wc, "type")
        wc_secs = _get(wc, "window_seconds")
        ticks = rb.window(symbol, wc_secs) if wc_secs else []
        if wc_type == "price_change_pct" and ticks:
            start = ticks[0].price
            pct = (tick.price - start) / start * 100 if start else 0.0
            parts.append(f"start={start:.2f}({pct:+.2f}%)")
        elif wc_type == "volume_burst":
            vol = sum(t.size for t in ticks)
            parts.append(f"vol={vol}")
        elif wc_type == "trade_count":
            parts.append(f"ticks={len(ticks)}")

    cs = _get(f, "conditions", []) or []
    for c in cs:
        field = _get(c, "field")
        if field and field != "close":
            val = cache.get(field)
            if val is not None:
                parts.append(f"{field}={val:.2f}" if isinstance(val, (int, float)) else f"{field}={val}")

    return " ".join(parts) if parts else "-"


def _render_rule_summary(active) -> str:
    """單行 render 一條 active_signal 的條件,給 report header 用。"""
    f = active.filter_json
    logic = _get(f, "logic", "AND")
    parts: list[str] = []
    for wc in (_get(f, "window_conditions", []) or []):
        parts.append(
            f"{_get(wc, 'type')} {_get(wc, 'operator')} {_get(wc, 'value')} "
            f"({_get(wc, 'window_seconds')}s)"
        )
    for c in (_get(f, "conditions", []) or []):
        parts.append(f"{_get(c, 'field')} {_get(c, 'operator')} {_get(c, 'value')}")
    return (" " + logic + " ").join(parts)


def _print_report(
    *,
    label: str,
    watchlist: list[str],
    actives: list,
    replay_symbols: list[str],
    candle_total: int,
    candle_failed: list[str],
    triggers: list[Trigger],
) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n=== probe_replay_ticks 報告 ({today}, USER_LABEL={label}) ===")

    wl_show = ", ".join(watchlist[:10]) + ("..." if len(watchlist) > 10 else "")
    print(f"Watchlist: {len(watchlist)} symbols ({wl_show})")

    print(f"Active signals: {len(actives)} enabled")
    for a in actives:
        rule = _render_rule_summary(a)
        print(f'  - "{a.name}" — {rule}, cooldown={a.cooldown_seconds}s')

    fail_show = f", failed: {', '.join(candle_failed)}" if candle_failed else ""
    print(f"\nReplayed: {len(replay_symbols)} symbols | "
          f"candles fetched: {candle_total} | failed: {len(candle_failed)}{fail_show}")

    if not triggers:
        print("\n觸發明細: (無 — 沒有規則命中)")
    else:
        print(f"\n觸發明細(按時間排序):")
        print(f"  {'時間':<8}  {'symbol':<6}  {'規則':<28}  {'價':>10}  window/cache 摘要")
        print(f"  {'-'*8}  {'-'*6}  {'-'*28}  {'-'*10}  {'-'*30}")
        for t in sorted(triggers, key=lambda x: x.triggered_at):
            ts = datetime.fromtimestamp(t.triggered_at).strftime("%H:%M:%S")
            name = (t.active_signal_name[:26] + "..") if len(t.active_signal_name) > 28 else t.active_signal_name
            print(f"  {ts:<8}  {t.symbol:<6}  {name:<28}  {t.trigger_price:>10.2f}  {t.summary}")

    print("\n每規則統計:")
    counts = Counter(t.active_signal_id for t in triggers)
    for a in actives:
        symbols_hit = {t.symbol for t in triggers if t.active_signal_id == a.id}
        n = counts.get(a.id, 0)
        suffix = f" ({len(symbols_hit)} symbols)" if n > 0 else "  ← 今日完全沒觸發"
        print(f"  {a.name:<30} × {n} 次{suffix}")


async def main() -> None:
    fubon = get_fubon()
    await fubon.init()
    sb = get_supabase()
    sb.init()
    if fubon.status.value != "ok" or sb.status.value != "ok":
        print(f"FAIL fubon={fubon.status.value} sb={sb.status.value}")
        sys.exit(1)

    label = get_user_label()

    wl_res = await asyncio.to_thread(
        lambda: sb.client.table("watchlist").select("symbol").eq("user_label", label).execute()
    )
    watchlist = sorted(row["symbol"] for row in (wl_res.data or []))
    if not watchlist:
        print(f"USER_LABEL={label} 沒 watchlist — nothing to replay")
        sys.exit(0)

    engine = get_signal_engine()
    await engine.refresh_active_signals()
    if not engine._active:
        print(f"USER_LABEL={label} 沒 enabled active_signals — nothing to replay")
        sys.exit(0)

    print(f"[init OK] USER_LABEL={label}, watchlist={len(watchlist)} symbols, "
          f"active_signals={len(engine._active)} enabled")

    # 要 replay 的 symbols = 所有 active_signal scope 涉及的 symbols(由 _refill_field_cache 算)
    replay_symbols = sorted(engine._field_cache.keys())
    if not replay_symbols:
        print(f"[warn] field_cache 空 — 沒任何 active_signal 的 scope 包到 symbol")
        sys.exit(0)

    # ---------- Monkey-patch engine._fanout 改成 record-only ----------
    triggers: list[Trigger] = []

    async def mock_fanout(active, symbol, tick):
        triggers.append(Trigger(
            triggered_at=tick.time,
            symbol=symbol,
            active_signal_id=active.id,
            active_signal_name=active.name,
            trigger_price=tick.price,
            trigger_volume=tick.size,
            summary=_summary(active, symbol, tick),
        ))

    engine._fanout = mock_fanout

    # ---------- 安裝 FakeClock(module-level patch)----------
    fake = FakeClock()
    orig_rb_time = rb_mod.time
    orig_se_time = se_mod.time
    rb_mod.time = fake
    se_mod.time = fake

    candle_total = 0
    candle_failed: list[str] = []
    rb = get_ring_buffer()

    try:
        for symbol in replay_symbols:
            try:
                resp = fubon.sdk.marketdata.rest_client.stock.intraday.candles(
                    symbol=symbol, timeframe="1"
                )
            except Exception as e:
                print(f"  [warn] {symbol}: API failed — {type(e).__name__}: {e}")
                candle_failed.append(symbol)
                continue

            data = resp.get("data") if isinstance(resp, dict) else None
            if not data:
                print(f"  [warn] {symbol}: no candles data (盤前 / 停牌?)")
                candle_failed.append(symbol)
                continue

            data_sorted = sorted(data, key=lambda x: x.get("date", ""))
            candle_total += len(data_sorted)

            # clear ring_buffer for this symbol 避免互污染
            rb.discard(symbol)
            rb.ensure(symbol)

            for c in data_sorted:
                date_str = c.get("date", "")
                if not date_str:
                    continue
                # Fubon `date` 可能是 ISO8601 帶 TZ(`2026-05-13T09:00:00+08:00`)
                # 也可能是純 date-time 字串。fromisoformat 兩種都能吃,Z 要先換成 +00:00
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    epoch = dt.timestamp()
                except (ValueError, TypeError):
                    continue
                tick = Tick(
                    price=float(c.get("close", 0)),
                    size=int(c.get("volume", 0)),
                    time=epoch,
                )
                fake.now = tick.time
                rb.append(symbol, tick)
                await engine._evaluate(symbol, tick)
    finally:
        rb_mod.time = orig_rb_time
        se_mod.time = orig_se_time

    _print_report(
        label=label,
        watchlist=watchlist,
        actives=engine._active,
        replay_symbols=replay_symbols,
        candle_total=candle_total,
        candle_failed=candle_failed,
        triggers=triggers,
    )


if __name__ == "__main__":
    asyncio.run(main())
