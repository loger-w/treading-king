"""引擎級重播:近 N 日 1 分 K 餵真 SignalEngine,比較 re-arm 開/關的訊號量。

用法(backend/ 下,先停 dev server — 跑此腳本會登入富邦一次):
  .venv\\Scripts\\python scripts\\replay_engine.py                 # 近 5 日
  .venv\\Scripts\\python scripts\\replay_engine.py --rearm 8       # 對照組改 8 ticks
  .venv\\Scripts\\python scripts\\replay_engine.py --preset crash  # 突爆殺門檻掃描

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
                                       float(row["low"]), float(row["close"]),
                                       int(row.get("volume") or 0)))
            minute[sym] = {d: sorted(v) for d, v in by_day.items()}
            print(f"fetched {sym}", file=sys.stderr)
    finally:
        try:
            sdk.logout()
        except Exception:
            pass
    return daily, minute


def candles_to_ticks(day: str, candles: list) -> list[tuple[float, float, int]]:
    """(epoch, price, volume) 串流:每根 K 4 筆,紅 K O→L→H→C、黑 K O→H→L→C。
    candle volume 平分給 4 筆 tick(餘數給最後一筆)。"""
    out = []
    for hhmm, o, h, l, c, vol in candles:
        base = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=TAIPEI).timestamp()
        path = (o, l, h, c) if c >= o else (o, h, l, c)
        q, r = divmod(max(vol, 0), 4)
        vols = [q, q, q, q + r]
        out.extend((base + i * 15.0, p, vols[i]) for i, p in enumerate(path))
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
        # _fanout 會對 notify_discord=True 的規則 POST SIGNALS_BOT_PUSH_URL —
        # 重播幾百筆觸發不能灌進真 Discord
        notify_discord=False,
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
        # _fanout 會對 notify_discord=True 的規則 POST SIGNALS_BOT_PUSH_URL —
        # 重播幾百筆觸發不能灌進真 Discord
        notify_discord=False,
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

    streams = []  # (ts, symbol, price, volume) 全股票合併、按時間序
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
        streams.extend((ts, sym, p, vol) for ts, p, vol in candles_to_ticks(day, candles))
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
        for ts, sym, price, vol in streams:
            clock[0] = ts
            tick = Tick(price=price, size=vol, time=ts)
            rb.append(sym, tick)
            await engine._evaluate(sym, tick)
    return fired


def touch_with_volume_rule(rearm_ticks: int, ratio: float, min_elapsed: int, day: str):
    """碰 CDP + volume_ratio 複合規則。"""
    from models.condition import (
        ActiveFilter, ActiveSignalOut, CdpProximityCondition, WindowCondition,
    )
    return ActiveSignalOut(
        id="replay", name=f"碰CDP+vol≥{ratio}x",
        filter_json=ActiveFilter(
            cdp_proximity=CdpProximityCondition(
                levels=["ah", "nh", "cdp", "nl", "al"],
                tolerance_ticks=0, rearm_ticks=rearm_ticks,
            ),
            window_conditions=[WindowCondition(
                type="volume_ratio", window_seconds=60,
                operator="gte", value=ratio,
                min_elapsed_minutes=min_elapsed,
            )],
        ),
        scope={"type": "watchlist"},
        cooldown_seconds=600, enabled=True, created_at=day,
        notify_discord=False,
    )


def breakout_rule(confirm_bars: int, margin_ticks: int, day: str):
    """突破確認(站穩)規則 — breakout preset 用。"""
    from models.condition import ActiveFilter, ActiveSignalOut, BreakoutConfirmStrategy
    return ActiveSignalOut(
        id="replay", name=f"突破cb={confirm_bars}m={margin_ticks}",
        filter_json=ActiveFilter(strategy=BreakoutConfirmStrategy(
            type="cdp_breakout_confirm",
            levels=["ah", "nh", "nl", "al"],
            direction="both", confirm_bars=confirm_bars,
            margin_ticks=margin_ticks,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
        notify_discord=False,
    )


CRASH_THRESHOLDS = [-1.5, -2.0, -2.5, -3.0]
# per-symbol 明細的基線門檻 — 用 index() 取結果,改 CRASH_THRESHOLDS 時
# 不在列表會直接 ValueError,而非默默配錯資料與標籤
CRASH_BASE_THR = -2.0


async def run_crash(days, day_syms, daily, minute):
    """突爆殺門檻掃描 + 突爆拉(gt 2.0)同池對照。"""
    cols = [f"lt{t}" for t in CRASH_THRESHOLDS] + ["gt2.0"]
    print(f"\n{'day':<12}" + "".join(f"{c:>9}" for c in cols))
    totals = [0] * len(cols)
    last_detail = {}
    for day in days:
        runs = []
        for thr in CRASH_THRESHOLDS:
            runs.append(await replay_day(day, day_syms[day], daily, minute,
                                         window_rule("突爆殺", "lt", thr, day)))
        runs.append(await replay_day(day, day_syms[day], daily, minute,
                                     window_rule("突爆拉", "gt", 2.0, day)))
        counts = [sum(f.values()) for f in runs]
        totals = [a + b for a, b in zip(totals, counts)]
        print(f"{day:<12}" + "".join(f"{c:>9}" for c in counts))
        base, ref = runs[CRASH_THRESHOLDS.index(CRASH_BASE_THR)], runs[-1]
        last_detail = {s: (base.get(s, 0), ref.get(s, 0)) for s in day_syms[day]}
    print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))
    print(f"\n-- {days[-1]} per-symbol (突爆殺 lt{CRASH_BASE_THR} → 突爆拉 gt2.0) --")
    for s, (a, b) in sorted(last_detail.items()):
        print(f"{s:<6}{a:>4} → {b}")


VOLUME_RATIOS = [1.5, 2.0, 3.0, 5.0]
VOLUME_MIN_ELAPSED = [0, 5, 10]
VOLUME_DETAIL_ELAPSED = 5
assert VOLUME_DETAIL_ELAPSED in VOLUME_MIN_ELAPSED


async def run_volume(days, day_syms, daily, minute, rearm: int):
    """量能過濾碰線門檻掃描：baseline(純碰 CDP) vs volume_ratio 矩陣。"""
    baseline = {}
    for day in days:
        baseline[day] = await replay_day(
            day, day_syms[day], daily, minute, touch_rule(rearm, day))

    detail_fired = {}

    for min_el in VOLUME_MIN_ELAPSED:
        cols = [f"x{r}" for r in VOLUME_RATIOS]
        print(f"\n== min_elapsed={min_el} ==")
        print(f"{'day':<12}{'baseline':>9}" + "".join(f"{c:>9}" for c in cols))
        totals = [0] * (1 + len(VOLUME_RATIOS))
        for day in days:
            base_count = sum(baseline[day].values())
            row = [base_count]
            for ratio in VOLUME_RATIOS:
                f = await replay_day(day, day_syms[day], daily, minute,
                                     touch_with_volume_rule(rearm, ratio, min_el, day))
                row.append(sum(f.values()))
                if min_el == VOLUME_DETAIL_ELAPSED and day == days[-1]:
                    detail_fired[ratio] = f
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{day:<12}" + "".join(f"{c:>9}" for c in row))
        print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))
        if totals[0] > 0:
            pcts = ["—"] + [f"-{(1 - c / totals[0]) * 100:.0f}%" for c in totals[1:]]
            print(f"{'cut':<12}" + "".join(f"{p:>9}" for p in pcts))

    last = days[-1]
    print(f"\n-- {last} per-symbol (min_elapsed={VOLUME_DETAIL_ELAPSED}) --")
    hdr = f"{'sym':<8}{'base':>6}" + "".join(f"{'x' + str(r):>6}" for r in VOLUME_RATIOS)
    print(hdr)
    for s in sorted(day_syms[last]):
        vals = [baseline[last].get(s, 0)]
        for r in VOLUME_RATIOS:
            vals.append(detail_fired.get(r, {}).get(s, 0))
        print(f"{s:<8}" + "".join(f"{v:>6}" for v in vals))


BREAKOUT_CONFIRM_BARS = [1, 2, 3, 5]
BREAKOUT_MARGINS = [0, 1, 2]
BREAKOUT_DETAIL_CB = 2
BREAKOUT_DETAIL_MG = 0


async def run_breakout(days, day_syms, daily, minute, rearm: int):
    """突破確認門檻掃描：confirm_bars × margin_ticks 矩陣 + 碰 CDP baseline。"""
    baseline = {}
    for day in days:
        baseline[day] = await replay_day(
            day, day_syms[day], daily, minute, touch_rule(rearm, day))

    detail_fired = {}

    for mg in BREAKOUT_MARGINS:
        cols = [f"cb={cb}" for cb in BREAKOUT_CONFIRM_BARS]
        print(f"\n== margin_ticks={mg} ==")
        print(f"{'day':<12}{'touch':>9}" + "".join(f"{c:>9}" for c in cols))
        totals = [0] * (1 + len(BREAKOUT_CONFIRM_BARS))
        for day in days:
            base_count = sum(baseline[day].values())
            row = [base_count]
            for cb in BREAKOUT_CONFIRM_BARS:
                f = await replay_day(day, day_syms[day], daily, minute,
                                     breakout_rule(cb, mg, day))
                row.append(sum(f.values()))
                if mg == BREAKOUT_DETAIL_MG and cb == BREAKOUT_DETAIL_CB and day == days[-1]:
                    detail_fired = f
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{day:<12}" + "".join(f"{c:>9}" for c in row))
        print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))

    last = days[-1]
    print(f"\n-- {last} per-symbol (cb={BREAKOUT_DETAIL_CB} margin={BREAKOUT_DETAIL_MG}) --")
    base = baseline[last]
    print(f"{'sym':<8}{'touch':>6}{'break':>6}")
    for s in sorted(day_syms[last]):
        print(f"{s:<8}{base.get(s, 0):>6}{detail_fired.get(s, 0):>6}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--rearm", type=int, default=5)
    ap.add_argument("--preset", choices=["touch", "crash", "volume", "breakout"], default="touch")
    args = ap.parse_args()

    day_syms = day_symbols_from_log(args.days)
    if not day_syms:
        print("signals_log 無資料")
        return
    all_syms = sorted({s for v in day_syms.values() for s in v})
    days = sorted(day_syms)
    daily, minute = fetch_fubon(all_syms, days[0], days[-1])

    if args.preset == "crash":
        await run_crash(days, day_syms, daily, minute)
        return
    if args.preset == "volume":
        await run_volume(days, day_syms, daily, minute, args.rearm)
        return
    if args.preset == "breakout":
        await run_breakout(days, day_syms, daily, minute, args.rearm)
        return

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
