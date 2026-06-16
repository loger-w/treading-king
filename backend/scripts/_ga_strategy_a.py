"""策略 A（mountain_bounce）GA 參數優化。

染色體 = [confirm_bars, require_below_vwap, levels_combo, tolerance_pct]
造山參數固定用 v4 defaults。
適應度 = 獲利訊號數 × abs(平均跌幅) − α × 虧損訊號數

用法:
  .venv\\Scripts\\python scripts\\_ga_strategy_a.py --day 2026-06-16
  .venv\\Scripts\\python scripts\\_ga_strategy_a.py --day 2026-06-16 --pop 40 --gen 80
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = Path(__file__).resolve().parents[1]
CACHE_DIR = BACKEND / "scripts"

# ── Mountain v4 defaults (fixed, not searched) ──
M_SURGE_PCT, M_SURGE_WINDOW, M_SURGE_VR = 3.0, 10, 1.5
M_CONFIRM_VR, M_RE_SURGE_MARGIN, M_NOISE_PCT = 0.5, 0.3, 0.5
M_MIN_BARS = 3

# ── Strategy A gene definitions ──
LEVELS_COMBOS = [
    ["ah"], ["nh"], ["cdp"],
    ["ah", "nh"], ["ah", "cdp"], ["nh", "cdp"],
    ["ah", "nh", "cdp"],
]
GENES = [
    ("confirm_bars",       1, 4, 1),
    ("require_below_vwap", 0, 1, 1),
    ("levels_combo",       0, 6, 1),
    ("tolerance_pct",      0.0, 0.5, 0.1),
]
PROFIT_THRESHOLD = -0.5
LOSS_THRESHOLD = 0.5

DEFAULTS = (2, 0, 6, 0.0)  # confirm=2, no vwap, all 3 levels, no tolerance


def _gene_values(idx):
    _, lo, hi, step = GENES[idx]
    vals = []
    v = lo
    while v <= hi + step * 0.01:
        vals.append(round(v, 4) if isinstance(lo, float) else int(round(v)))
        v += step
    return vals


ALL_GENE_VALUES = [_gene_values(i) for i in range(len(GENES))]


def random_chromosome():
    return tuple(random.choice(vs) for vs in ALL_GENE_VALUES)


def cmin(hhmm):
    return (int(hhmm[:2]) - 9) * 60 + int(hhmm[3:5])


def load_cache(day):
    p = CACHE_DIR / f"_diag_auto_monitor_cache_{day}.json"
    if not p.exists():
        print(f"✗ cache 不存在: {p}", file=sys.stderr)
        sys.exit(1)
    c = json.loads(p.read_text(encoding="utf-8"))
    return c["cands"], c["pool"], c["minute"], c.get("cdp", {}), c["DAY"]


def preprocess(cands, pool, minute_data, cdp_data, day):
    stocks = []
    for sym in cands:
        raw = minute_data.get(sym, {}).get(day, [])
        if len(raw) < 10:
            continue
        candles = [tuple(r) for r in raw]
        ci = cdp_data.get(sym)
        if not ci:
            continue
        cdp_levels = {"ah": ci["ah"], "nh": ci["nh"], "cdp": ci["cdp"]}
        day_close = pool[sym].get("closePrice", candles[-1][4])
        vols = [c[5] for c in candles]
        cum = []
        s = 0
        for v in vols:
            s += v
            cum.append(s)
        stocks.append((sym, candles, cdp_levels, day_close, cum, vols))
    return stocks


def eval_chromosome(params, stocks_list, alpha):
    confirm_bars, require_vwap, levels_idx, tolerance_pct = params
    levels = LEVELS_COMBOS[int(levels_idx)]
    noise = 1 + M_NOISE_PCT / 100
    sw1 = M_SURGE_WINDOW + 1

    signals = []
    for day_label, stocks in stocks_list:
        for _sym, candles, cdp_levels, day_close, cum, vols in stocks:
            # Mountain detection state
            recent_closes = []
            phase = 0
            peak_high = 0.0
            nhh = 0
            mountain_confirmed = False

            # VWAP state
            cum_tp_vol = 0.0
            cum_vol_vwap = 0
            vwap = 0.0

            # Strategy A armed state: per-level
            armed = {}
            fired = set()

            n = len(candles) - 1
            for i in range(n):
                t, o, h, l, c, vol = candles[i]
                day_vol = cum[i] + vols[i + 1] // 4
                emin = cmin(candles[i + 1][0])

                # VWAP
                tp = (h + l + c) / 3
                cum_tp_vol += tp * vol
                cum_vol_vwap += vol
                vwap = cum_tp_vol / cum_vol_vwap if cum_vol_vwap > 0 else c

                # Mountain detection (inline, v4 defaults)
                recent_closes.append(c)
                if len(recent_closes) > sw1:
                    del recent_closes[:-sw1]

                vr = (vol * emin / day_vol) if (emin >= 1 and day_vol > 0) else 0.0

                is_surge = False
                n_rc = len(recent_closes)
                if n_rc >= M_MIN_BARS + 1:
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
                    if base > 0 and (h / base - 1) * 100 >= M_SURGE_PCT - 1e-9 and vr >= M_SURGE_VR:
                        is_surge = True

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
                        if c < o and vr >= M_CONFIRM_VR:
                            confirmed = True
                        else:
                            nhh += 1
                            if nhh >= 2:
                                confirmed = True
                        if confirmed:
                            phase = 2
                            mountain_confirmed = True
                elif phase == 2:
                    margin = 1 + M_RE_SURGE_MARGIN / 100
                    if h > peak_high * margin:
                        phase = 1
                        peak_high = h
                        nhh = 0

                if not mountain_confirmed:
                    continue

                # VWAP filter
                if require_vwap and c >= vwap:
                    continue

                remaining = candles[i + 1:]
                if not remaining:
                    continue

                # Strategy A: check each level
                for lname in levels:
                    if lname in fired:
                        continue
                    lval = cdp_levels.get(lname, 0)
                    if lval <= 0:
                        continue
                    threshold = lval * (1 - tolerance_pct / 100)

                    if h >= threshold:
                        if lname not in armed:
                            armed[lname] = {"count": 0, "cdp_val": lval}

                    if lname not in armed:
                        continue

                    ab = armed[lname]
                    if c < ab["cdp_val"]:
                        ab["count"] += 1
                        if ab["count"] >= confirm_bars:
                            drop_close = (day_close / c - 1) * 100
                            subsequent_low = min(rc[3] for rc in remaining)
                            drop_low = (subsequent_low / c - 1) * 100
                            signals.append({
                                "drop_close": drop_close,
                                "drop_low": drop_low,
                                "level": lname,
                                "day": day_label,
                            })
                            del armed[lname]
                            fired.add(lname)
                    else:
                        del armed[lname]

    profitable = [s for s in signals if s["drop_close"] < PROFIT_THRESHOLD]
    losing = [s for s in signals if s["drop_close"] > LOSS_THRESHOLD]
    avg_drop = sum(s["drop_close"] for s in profitable) / len(profitable) if profitable else 0.0
    fitness = len(profitable) * abs(avg_drop) - alpha * len(losing)
    return fitness, len(signals), len(profitable), len(losing), avg_drop


def tournament_select(pop, fits, k=3):
    idxs = random.sample(range(len(pop)), k)
    return pop[max(idxs, key=lambda i: fits[i])]


def crossover(p1, p2):
    return tuple(p1[i] if random.random() < 0.5 else p2[i] for i in range(len(GENES)))


def mutate(chrom, rate=0.25):
    genes = list(chrom)
    for i in range(len(genes)):
        if random.random() < rate:
            genes[i] = random.choice(ALL_GENE_VALUES[i])
    return tuple(genes)


def ga_run(stocks_list, alpha, pop_size=40, n_gen=80, elite_n=4, seed=42):
    random.seed(seed)
    pop = [random_chromosome() for _ in range(pop_size)]
    pop[0] = DEFAULTS

    best_ever = None
    best_fitness = -float("inf")
    eval_cache = {}

    for gen in range(n_gen):
        t0 = time.perf_counter()
        results = []
        for c in pop:
            if c in eval_cache:
                results.append(eval_cache[c])
            else:
                r = eval_chromosome(c, stocks_list, alpha)
                eval_cache[c] = r
                results.append(r)

        fits = [r[0] for r in results]
        bi = max(range(len(pop)), key=lambda i: fits[i])
        if fits[bi] > best_fitness:
            best_fitness = fits[bi]
            best_ever = (pop[bi], results[bi])

        if gen % 10 == 0 or gen == n_gen - 1:
            _, ns, np, nl, ad = results[bi]
            elapsed = time.perf_counter() - t0
            print(f"  Gen {gen:>3} | best={fits[bi]:>7.1f} | "
                  f"訊號={ns} 獲利={np} 虧損={nl} 跌={ad:+.1f}% | {elapsed:.1f}s")

        if gen == n_gen - 1:
            break

        ranked = sorted(range(len(pop)), key=lambda i: fits[i], reverse=True)
        new_pop = [pop[ranked[i]] for i in range(elite_n)]
        while len(new_pop) < pop_size:
            child = mutate(crossover(
                tournament_select(pop, fits),
                tournament_select(pop, fits),
            ))
            new_pop.append(child)
        pop = new_pop

    all_chroms = sorted(eval_cache.items(), key=lambda x: x[1][0], reverse=True)
    return best_ever, all_chroms[:10], eval_cache


def fmt_params(p):
    cb, vwap, li, tol = p
    lvs = "+".join(l.upper() for l in LEVELS_COMBOS[int(li)])
    return f"confirm={int(cb)} vwap={'Y' if vwap else 'N'} levels={lvs} tol={tol:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None)
    ap.add_argument("--days", default=None)
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--gen", type=int, default=80)
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.day and not args.days:
        print("需要 --day 或 --days", file=sys.stderr)
        sys.exit(1)

    days = args.days.split(",") if args.days else [args.day]
    stocks_list = []
    total = 0
    for d in days:
        cands, pool, minute, cdp, actual_day = load_cache(d.strip())
        stocks = preprocess(cands, pool, minute, cdp, actual_day)
        stocks_list.append((actual_day, stocks))
        total += len(stocks)
        print(f"  載入 {actual_day}: {len(stocks)} 檔", file=sys.stderr)

    search_space = 1
    for vs in ALL_GENE_VALUES:
        search_space *= len(vs)

    print(f"\n{'=' * 60}")
    print(f"  策略 A (mountain_bounce) GA 優化")
    print(f"  日期: {', '.join(days)} | 股票: {total} 檔")
    print(f"  族群: {args.pop} | 世代: {args.gen} | α={args.alpha}")
    print(f"  搜索空間: {search_space} 組合")
    print(f"{'=' * 60}")

    base = eval_chromosome(DEFAULTS, stocks_list, args.alpha)
    print(f"\n📊 基準: {fmt_params(DEFAULTS)}")
    print(f"   fitness={base[0]:.1f} | 訊號={base[1]} 獲利={base[2]} 虧損={base[3]} 跌={base[4]:+.1f}%")

    print(f"\n🧬 開始演化...\n")
    t_start = time.perf_counter()
    best, top10, cache = ga_run(stocks_list, args.alpha,
                                pop_size=args.pop, n_gen=args.gen, seed=args.seed)
    t_total = time.perf_counter() - t_start

    print(f"\n{'=' * 60}")
    print(f"  完成 — {t_total:.0f}s | 評估 {len(cache)} 個獨立染色體")
    print(f"{'=' * 60}")

    best_chrom, best_r = best
    print(f"\n🏆 最佳: {fmt_params(best_chrom)}")
    print(f"   fitness={best_r[0]:.1f} | 訊號={best_r[1]} 獲利={best_r[2]} 虧損={best_r[3]} 跌={best_r[4]:+.1f}%")

    imp = best_r[0] - base[0]
    print(f"   vs 基準: fitness {'+' if imp >= 0 else ''}{imp:.1f}")

    print(f"\n📋 Top 10:")
    print(f"  {'#':>2} {'fitness':>8} {'訊號':>4} {'獲利':>4} {'虧損':>4} {'跌%':>6}  參數")
    for rank, (chrom, r) in enumerate(top10, 1):
        marker = " ◄ default" if chrom == DEFAULTS else ""
        print(f"  {rank:>2} {r[0]:>8.1f} {r[1]:>4} {r[2]:>4} {r[3]:>4} {r[4]:>+5.1f}%  {fmt_params(chrom)}{marker}")

    out = {
        "days": days, "alpha": args.alpha, "pop": args.pop, "gen": args.gen, "seed": args.seed,
        "baseline": {"params": list(DEFAULTS), "fitness": base[0],
                     "signals": base[1], "profitable": base[2], "losing": base[3], "avg_drop": base[4]},
        "best": {"params": list(best_chrom), "fitness": best_r[0],
                 "signals": best_r[1], "profitable": best_r[2], "losing": best_r[3], "avg_drop": best_r[4]},
        "top10": [{"params": list(c), "fitness": r[0], "signals": r[1],
                   "profitable": r[2], "losing": r[3], "avg_drop": r[4]} for c, r in top10],
        "total_evals": len(cache), "elapsed_sec": round(t_total, 1),
    }
    out_path = CACHE_DIR / f"_ga_strategy_a_result_{'_'.join(days)}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n💾 結果已存: {out_path.name}")


if __name__ == "__main__":
    main()
