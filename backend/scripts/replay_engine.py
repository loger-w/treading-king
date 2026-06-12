"""引擎級重播:近 N 日 1 分 K 餵真 SignalEngine,比較 re-arm 開/關的訊號量。

用法(backend/ 下,先停 dev server — 跑此腳本會登入富邦一次):
  .venv\\Scripts\\python scripts\\replay_engine.py            # 近 5 日
  .venv\\Scripts\\python scripts\\replay_engine.py --rearm 8  # 對照組改 8 ticks

已知近似:1 分 K 每根轉 4 筆 tick(紅 K 走 O→L→H→C、黑 K 走 O→H→L→C),
與真實逐筆有偏差 — 絕對數字僅供參考,看「rearm 開 vs 關」的相對差距。
每日重播的股票池 = 該日 signals_log 出現過的股票(當天確實被監聽且碰過線)。
之後的新策略上線前,比照此模式做引擎級回測(換掉餵進去的規則設定)。
"""
import argparse
import asyncio
import json
import sys
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

TAIPEI = timezone(timedelta(hours=8))


def day_symbols_from_log(n_days: int) -> dict[str, list[str]]:
    """signals_log 近 n_days 個(有訊號的)日期 → 該日出現過的股票。"""
    by_day: dict[str, set[str]] = defaultdict(set)
    log = BACKEND / "data" / "signals_log.jsonl"
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        day = row.get("triggered_at", "")[:10]
        if day and row.get("symbol"):
            by_day[day].add(row["symbol"])
    days = sorted(by_day)[-n_days:]
    return {d: sorted(by_day[d]) for d in days}


def fetch_fubon(symbols: list[str], day_from: str, day_to: str):
    """登入 → 抓 daily(算 CDP 用)+ 1 分 K → 登出。回 (daily, minute)。"""
    import os

    from fubon_neo.sdk import FubonSDK, Mode

    pid = os.getenv("FUBON_PERSONAL_ID", "").strip() or os.getenv("FUBON_ACCOUNT", "").strip()
    key = os.getenv("FUBON_API_KEY", "").strip()
    cert_path = os.getenv("FUBON_CERT_PATH", "").strip()

    sdk = FubonSDK()
    if cert_path:
        sdk.apikey_login(pid, key, cert_path, os.getenv("FUBON_CERT_PASS", "").strip() or None)
    else:
        sdk.apikey_dma_login(pid, key)
    sdk.init_realtime(Mode.Normal)

    daily: dict[str, dict[str, tuple]] = {}
    minute: dict[str, dict[str, list]] = {}
    try:
        rest = sdk.marketdata.rest_client.stock
        from_daily = (datetime.fromisoformat(day_from) - timedelta(days=30)).date().isoformat()
        for sym in symbols:
            _time.sleep(1.1)  # historical 官方 60 req/min
            r = rest.historical.candles(symbol=sym, from_=from_daily, to=day_to)
            daily[sym] = {
                row["date"][:10]: (float(row["high"]), float(row["low"]), float(row["close"]))
                for row in (r or {}).get("data", [])
            }
            _time.sleep(1.1)
            m = rest.historical.candles(symbol=sym, from_=day_from, to=day_to, timeframe="1")
            by_day = defaultdict(list)
            for row in (m or {}).get("data", []):
                d = row.get("date", "")
                by_day[d[:10]].append((d[11:16], float(row["open"]), float(row["high"]),
                                       float(row["low"]), float(row["close"])))
            minute[sym] = {d: sorted(v) for d, v in by_day.items()}
            print(f"fetched {sym}", file=sys.stderr)
    finally:
        try:
            sdk.logout()
        except Exception:
            pass
    return daily, minute


def candles_to_ticks(day: str, candles: list) -> list[tuple[float, float]]:
    """(epoch, price) 串流:每根 K 4 筆,紅 K O→L→H→C、黑 K O→H→L→C。"""
    out = []
    for hhmm, o, h, l, c in candles:
        base = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=TAIPEI).timestamp()
        path = (o, l, h, c) if c >= o else (o, h, l, c)
        out.extend((base + i * 15.0, p) for i, p in enumerate(path))
    return out


def touch_rule(rearm_ticks: int, day: str):
    """碰 CDP 規則(沿用 2026-06-12 re-arm 回測的設定)。"""
    from models.condition import ActiveFilter, ActiveSignalOut, CdpProximityCondition
    return ActiveSignalOut(
        id="replay", name="碰CDP",
        filter_json=ActiveFilter(cdp_proximity=CdpProximityCondition(
            levels=["ah", "nh", "cdp", "nl", "al"],
            tolerance_ticks=0, rearm_ticks=rearm_ticks,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=600, enabled=True, created_at=day,
    )


def window_rule(name: str, operator: str, value: float, day: str):
    """price_change_pct 時窗規則 — 突爆殺(lt 負值)/ 突爆拉(gt 正值)共用。"""
    from models.condition import ActiveFilter, ActiveSignalOut, WindowCondition
    return ActiveSignalOut(
        id="replay", name=name,
        filter_json=ActiveFilter(window_conditions=[WindowCondition(
            type="price_change_pct", window_seconds=300,
            operator=operator, value=value,
        )]),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
    )


async def replay_day(day: str, symbols: list[str], daily, minute, active):
    import services.ring_buffer as ring_buffer_module
    from services.cdp import compute_cdp
    from services.ring_buffer import RingBuffer, Tick
    from services.signal_engine import SignalEngine

    engine = SignalEngine()
    engine._active = [active]

    # window 條件讀 ring_buffer 單例 — 每日換全新實例,避免跨日殘留 tick
    ring_buffer_module._default = RingBuffer()
    rb = ring_buffer_module.get_ring_buffer()

    streams = []  # (ts, symbol, price) 全股票合併、按時間序
    for sym in symbols:
        prevs = sorted(d for d in daily.get(sym, {}) if d < day)
        candles = minute.get(sym, {}).get(day, [])
        if not prevs or not candles:
            continue
        h, l, c = daily[sym][prevs[-1]]
        lv = compute_cdp(h, l, c)
        engine._field_cache[sym] = {
            "cdp_ah": lv["ah"], "cdp_nh": lv["nh"], "cdp": lv["cdp"],
            "cdp_nl": lv["nl"], "cdp_al": lv["al"],
        }
        rb.ensure(sym)
        streams.extend((ts, sym, p) for ts, p in candles_to_ticks(day, candles))
    streams.sort()

    fired: dict[str, int] = defaultdict(int)

    async def fake_broadcast(payload):
        fired[payload["data"]["symbol"]] += 1

    clock = [0.0]
    # 此 patch 改的是全域 time 模組的 time 屬性 — signal_engine 與 ring_buffer
    # import 同一個 time 模組物件,ring_buffer.window() 的 cutoff 一併用假時鐘
    with patch("services.signal_engine.time.time", side_effect=lambda: clock[0]), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        for ts, sym, price in streams:
            clock[0] = ts
            tick = Tick(price=price, size=1, time=ts)
            rb.append(sym, tick)
            await engine._evaluate(sym, tick)
    return fired


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--rearm", type=int, default=5)
    args = ap.parse_args()

    day_syms = day_symbols_from_log(args.days)
    if not day_syms:
        print("signals_log 無資料")
        return
    all_syms = sorted({s for v in day_syms.values() for s in v})
    days = sorted(day_syms)
    daily, minute = fetch_fubon(all_syms, days[0], days[-1])

    print(f"\n{'day':<12}{'rearm=0':>9}{'rearm=' + str(args.rearm):>9}")
    tot0 = totN = 0
    last_detail = {}
    for day in days:
        f0 = await replay_day(day, day_syms[day], daily, minute, touch_rule(0, day))
        fN = await replay_day(day, day_syms[day], daily, minute, touch_rule(args.rearm, day))
        print(f"{day:<12}{sum(f0.values()):>9}{sum(fN.values()):>9}")
        tot0 += sum(f0.values())
        totN += sum(fN.values())
        last_detail = {s: (f0.get(s, 0), fN.get(s, 0)) for s in day_syms[day]}
    print(f"{'total':<12}{tot0:>9}{totN:>9}")
    print(f"\n-- {days[-1]} per-symbol (rearm=0 → rearm={args.rearm}) --")
    for s, (a, b) in sorted(last_detail.items()):
        print(f"{s:<6}{a:>4} → {b}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
