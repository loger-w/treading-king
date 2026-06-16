"""策略 A 回測對比 — 兩種觸發方法:

方法 A: 造山確認 + 在 VWAP 下 + 碰 CDP 線
方法 B: 造山確認 + 碰 CDP 線 + 後續 2 根 close 在線下

用法: .venv\\Scripts\\python scripts\\_backtest_strategy_a.py --day 2026-06-16
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

CACHE_DIR = BACKEND / "scripts"

SURGE_PCT, SURGE_WINDOW, SURGE_VR = 3.0, 10, 1.5
CONFIRM_VR, RE_SURGE_MARGIN, NOISE_PCT = 0.5, 0.3, 0.5
MIN_BARS = 3


def cmin(hhmm):
    return (int(hhmm[:2]) - 9) * 60 + int(hhmm[3:5])


def run_backtest(day):
    p = CACHE_DIR / f"_diag_auto_monitor_cache_{day}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    DAY, pool, minute, cdp_data = d["DAY"], d["pool"], d["minute"], d["cdp"]
    cands = d["cands"]

    noise = 1 + NOISE_PCT / 100
    sw1 = SURGE_WINDOW + 1

    results_a = []
    results_b = []

    for sym in cands:
        raw = minute.get(sym, {}).get(DAY, [])
        if len(raw) < 10:
            continue
        candles = [tuple(r) for r in raw]
        cdp_info = cdp_data.get(sym)
        if not cdp_info:
            continue

        cdp_levels = {"AH": cdp_info["ah"], "NH": cdp_info["nh"], "CDP": cdp_info["cdp"]}

        vols = [c[5] for c in candles]
        cum = []
        s = 0
        for v in vols:
            s += v
            cum.append(s)

        day_close = pool[sym].get("closePrice", candles[-1][4])

        # Mountain state
        recent_closes = []
        phase = 0
        peak_high = 0.0
        nhh = 0
        mountain_confirmed = False
        confirmed_bar = -1

        # VWAP state
        cum_tp_vol = 0.0
        cum_vol_vwap = 0
        vwap = 0.0

        # Approach B state
        armed_b = {}
        # Track fired signals to avoid duplicates per level
        fired_a = set()
        fired_b = set()

        for i in range(len(candles) - 1):
            t, o, h, l, c, vol = candles[i]
            day_vol = cum[i] + vols[i + 1] // 4
            emin = cmin(candles[i + 1][0])

            tp = (h + l + c) / 3
            cum_tp_vol += tp * vol
            cum_vol_vwap += vol
            vwap = cum_tp_vol / cum_vol_vwap if cum_vol_vwap > 0 else c

            recent_closes.append(c)
            if len(recent_closes) > sw1:
                del recent_closes[:-sw1]

            vr = (vol * emin / day_vol) if (emin >= 1 and day_vol > 0) else 0.0

            # Detect surge
            is_surge = False
            n_rc = len(recent_closes)
            if n_rc >= MIN_BARS + 1:
                nb = n_rc - 1
                if nb <= 1:
                    base = recent_closes[0] if nb else 0.0
                else:
                    rm = recent_closes[nb - 1]
                    for j in range(nb - 2, -1, -1):
                        if recent_closes[j] <= rm:
                            rm = recent_closes[j]
                        elif recent_closes[j] > rm * noise:
                            break
                    base = rm
                if base > 0 and (h / base - 1) * 100 >= SURGE_PCT - 1e-9 and vr >= SURGE_VR:
                    is_surge = True

            # Mountain state machine
            if phase == 0:
                if is_surge:
                    phase = 1
                    peak_high = h
                    nhh = 0
            elif phase == 1:
                if h > peak_high:
                    peak_high = h
                    nhh = 0
                else:
                    confirmed = False
                    if c < o and vr >= CONFIRM_VR:
                        confirmed = True
                    else:
                        nhh += 1
                        if nhh >= 2:
                            confirmed = True
                    if confirmed:
                        phase = 2
                        mountain_confirmed = True
                        confirmed_bar = i
            elif phase == 2:
                margin = 1 + RE_SURGE_MARGIN / 100
                if h > peak_high * margin:
                    phase = 1
                    peak_high = h
                    nhh = 0

            if not mountain_confirmed:
                continue

            remaining = candles[i + 1:]
            if not remaining:
                continue

            for lname, lval in cdp_levels.items():
                if lval <= 0:
                    continue
                touched = h >= lval

                # === Approach A: below VWAP + touch CDP ===
                if touched and c < vwap and c < lval and lname not in fired_a:
                    subsequent_low = min(rc[3] for rc in remaining)
                    drop_close = (day_close / c - 1) * 100
                    drop_low = (subsequent_low / c - 1) * 100
                    results_a.append({
                        "sym": sym, "bar": i, "time": t, "level": lname,
                        "cdp_val": lval, "price": c, "vwap": round(vwap, 2),
                        "drop_close": round(drop_close, 2),
                        "drop_low": round(drop_low, 2),
                    })
                    fired_a.add(lname)

                # === Approach B: touch CDP + 2 candles close below ===
                bkey = lname
                if touched and bkey not in armed_b and bkey not in fired_b:
                    armed_b[bkey] = {"bar": i, "count": 0, "cdp_val": lval}
                if bkey in armed_b:
                    ab = armed_b[bkey]
                    if c < ab["cdp_val"]:
                        ab["count"] += 1
                    else:
                        del armed_b[bkey]
                        continue
                    if ab["count"] >= 2:
                        subsequent_low = min(rc[3] for rc in remaining)
                        drop_close = (day_close / c - 1) * 100
                        drop_low = (subsequent_low / c - 1) * 100
                        results_b.append({
                            "sym": sym, "bar": i, "time": t, "level": lname,
                            "cdp_val": ab["cdp_val"], "price": c,
                            "vwap": round(vwap, 2),
                            "drop_close": round(drop_close, 2),
                            "drop_low": round(drop_low, 2),
                        })
                        del armed_b[bkey]
                        fired_b.add(bkey)

    return results_a, results_b, DAY, len(cands)


def print_results(label, results):
    print(f"\n--- {label} ---")
    print(f"  訊號數: {len(results)}")
    if not results:
        return
    drops_c = [r["drop_close"] for r in results]
    drops_l = [r["drop_low"] for r in results]
    avg_dc = sum(drops_c) / len(drops_c)
    avg_dl = sum(drops_l) / len(drops_l)
    profitable = [r for r in results if r["drop_close"] < -0.5]
    big_win = [r for r in results if r["drop_close"] < -1.5]
    losing = [r for r in results if r["drop_close"] > 0.5]

    print(f"  平均跌到收盤: {avg_dc:+.2f}%")
    print(f"  平均最大回撤: {avg_dl:+.2f}%")
    print(f"  獲利(跌>0.5%): {len(profitable)}/{len(results)} ({len(profitable)/len(results)*100:.0f}%)")
    print(f"  大賺(跌>1.5%): {len(big_win)}/{len(results)} ({len(big_win)/len(results)*100:.0f}%)")
    print(f"  虧損(漲>0.5%): {len(losing)}/{len(results)} ({len(losing)/len(results)*100:.0f}%)")

    by_level = {}
    for r in results:
        by_level.setdefault(r["level"], []).append(r)
    print(f"  CDP 線分布:")
    for lv in ["AH", "NH", "CDP"]:
        rs = by_level.get(lv, [])
        if rs:
            avg = sum(r["drop_close"] for r in rs) / len(rs)
            win = sum(1 for r in rs if r["drop_close"] < -0.5)
            print(f"    {lv}: {len(rs)} 訊號, 平均 {avg:+.2f}%, 獲利率 {win}/{len(rs)}")

    print(f"  最佳/最差 10 筆:")
    header = f"    {'sym':<6} {'time':<6} {'lv':<4} {'CDP':>7} {'price':>7} {'VWAP':>7} {'→收盤':>7} {'→最低':>7}"
    print(header)
    for r in sorted(results, key=lambda x: x["drop_close"])[:5]:
        print(f"    {r['sym']:<6} {r['time']:<6} {r['level']:<4} {r['cdp_val']:>7} {r['price']:>7} "
              f"{r['vwap']:>7} {r['drop_close']:>+6.1f}% {r['drop_low']:>+6.1f}%")
    print(f"    ...")
    for r in sorted(results, key=lambda x: x["drop_close"])[-5:]:
        print(f"    {r['sym']:<6} {r['time']:<6} {r['level']:<4} {r['cdp_val']:>7} {r['price']:>7} "
              f"{r['vwap']:>7} {r['drop_close']:>+6.1f}% {r['drop_low']:>+6.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-06-16")
    args = ap.parse_args()

    ra, rb, day, n_stocks = run_backtest(args.day)

    print(f"{'=' * 70}")
    print(f"  策略 A 回測對比  {day}  {n_stocks} 檔")
    print(f"  造山: v4 defaults | CDP: AH/NH/CDP中軸")
    print(f"{'=' * 70}")

    print_results("方法A: 造山確認 + VWAP下 + 碰CDP", ra)
    print_results("方法B: 造山確認 + 碰CDP + 2根close在線下", rb)

    # Summary comparison
    print(f"\n{'=' * 70}")
    print(f"  對比摘要")
    print(f"{'=' * 70}")
    for label, r in [("A (VWAP下+碰CDP)", ra), ("B (碰+2根確認)", rb)]:
        if r:
            avg = sum(x["drop_close"] for x in r) / len(r)
            win = sum(1 for x in r if x["drop_close"] < -0.5)
            print(f"  {label}: {len(r)} 訊號, 平均 {avg:+.2f}%, 獲利率 {win}/{len(r)} ({win/len(r)*100:.0f}%)")
        else:
            print(f"  {label}: 0 訊號")


if __name__ == "__main__":
    main()
