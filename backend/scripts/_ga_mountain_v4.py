"""造山積木 v4 參數 GA 優化 — 用每日 cache 池跑遺傳演算法找最佳參數。

染色體 = [surge_pct, surge_window, surge_vr, confirm_vr, re_surge_margin, noise_pct]
固定不搜索: min_bars=3, 黑K=1根, 非黑K=2根
適應度 = 真做頭數 × abs(平均山後跌幅) − α × 假山數

用法:
  .venv\\Scripts\\python scripts\\_ga_mountain_v4.py --day 2026-06-16
  .venv\\Scripts\\python scripts\\_ga_mountain_v4.py --day 2026-06-16 --pop 80 --gen 200 --alpha 1.5
  .venv\\Scripts\\python scripts\\_ga_mountain_v4.py --days 2026-06-16,2026-06-17  # 多日 walk-forward
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

# ── Gene definitions: (name, min, max, step) ──
GENES = [
    ("surge_pct",       2.0, 5.0, 0.5),
    ("surge_window",    5,   20,  1),
    ("surge_vr",        1.0, 3.0, 0.25),
    ("confirm_vr",      0.0, 1.0, 0.25),
    ("re_surge_margin", 0.0, 0.5, 0.1),
    ("noise_pct",       0.2, 1.0, 0.1),
]
FIXED_MIN_BARS = 3
V4_DEFAULTS = (3.0, 10, 1.5, 0.5, 0.3, 0.5)


def _gene_values(idx):
    _, lo, hi, step = GENES[idx]
    vals = []
    v = lo
    while v <= hi + step * 0.01:
        vals.append(round(v, 4))
        v += step
    return vals


ALL_GENE_VALUES = [_gene_values(i) for i in range(len(GENES))]


def random_chromosome():
    return tuple(random.choice(vs) for vs in ALL_GENE_VALUES)


# ── Data loading ──

def load_cache(day):
    p = CACHE_DIR / f"_diag_auto_monitor_cache_{day}.json"
    if not p.exists():
        print(f"✗ cache 不存在: {p}", file=sys.stderr)
        sys.exit(1)
    c = json.loads(p.read_text(encoding="utf-8"))
    return c["cands"], c["pool"], c["minute"], c["DAY"]


def preprocess(cands, pool, minute_data, day):
    stocks = []
    for sym in cands:
        raw = minute_data.get(sym, {}).get(day, [])
        if len(raw) < 2:
            continue
        candles = [tuple(r) for r in raw]
        day_high = max(c[2] for c in candles)
        close_price = pool[sym].get("closePrice", candles[-1][4])
        vols = [c[5] for c in candles]
        cum = []
        s = 0
        for v in vols:
            s += v
            cum.append(s)
        stocks.append((sym, candles, day_high, close_price, cum, vols))
    return stocks


# ── Fitness evaluation (inline mountain detection for speed) ──

def eval_chromosome(params, stocks_list, alpha):
    """跑全部 stocks,回傳 (fitness, true_tops, false_tops, avg_drop, detail_per_day)。

    stocks_list: list of (day_label, stocks) — 支援多日。
    """
    surge_pct, surge_window, surge_vr, confirm_vr, re_surge_margin, noise_pct = params
    noise = 1 + noise_pct / 100
    sw_plus_1 = int(surge_window) + 1
    total_true = 0
    total_false = 0
    all_drops = []
    per_day = []

    for day_label, stocks in stocks_list:
        day_true = 0
        day_false = 0
        day_drops = []

        for _sym, candles, day_high, close_price, cum, vols in stocks:
            recent_closes = []
            phase = 0  # 0=idle 1=surge_tracking 2=confirmed
            peak_high = 0.0
            peak_vr = 0.0
            nhh = 0  # no_new_high_count

            n = len(candles) - 1
            for i in range(n):
                _t, o, h, _l, c, vol = candles[i]
                day_vol = cum[i] + vols[i + 1] // 4
                t_next = candles[i + 1][0]
                emin = (int(t_next[:2]) - 9) * 60 + int(t_next[3:5])

                recent_closes.append(c)
                if len(recent_closes) > sw_plus_1:
                    del recent_closes[:-sw_plus_1]

                vr = (vol * emin / day_vol) if (emin >= 1 and day_vol > 0) else 0.0

                # detect surge
                is_surge = False
                n_rc = len(recent_closes)
                if n_rc >= FIXED_MIN_BARS + 1:
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
                    if base > 0 and (h / base - 1) * 100 >= surge_pct - 1e-9 and vr >= surge_vr:
                        is_surge = True

                # state machine
                if phase == 0:
                    if is_surge:
                        phase = 1
                        peak_high = h
                        peak_vr = vr
                        nhh = 0
                elif phase == 1:
                    if h > peak_high:
                        peak_high = h
                        if vr > peak_vr:
                            peak_vr = vr
                        nhh = 0
                    else:
                        if vr > peak_vr:
                            peak_vr = vr
                        confirmed = False
                        if c < o and vr >= confirm_vr:
                            confirmed = True
                        else:
                            nhh += 1
                            if nhh >= 2:
                                confirmed = True
                        if confirmed:
                            phase = 2
                            if abs(peak_high - day_high) < 0.01:
                                day_true += 1
                                day_drops.append((close_price / peak_high - 1) * 100)
                            else:
                                day_false += 1
                elif phase == 2:
                    if h > peak_high * (1 + re_surge_margin / 100):
                        phase = 1
                        peak_high = h
                        peak_vr = vr
                        nhh = 0

        total_true += day_true
        total_false += day_false
        all_drops.extend(day_drops)
        per_day.append((day_label, day_true, day_false,
                        sum(day_drops) / len(day_drops) if day_drops else 0.0))

    avg_drop = sum(all_drops) / len(all_drops) if all_drops else 0.0
    fitness = total_true * abs(avg_drop) - alpha * total_false
    return fitness, total_true, total_false, avg_drop, per_day


# ── GA operators ──

def tournament_select(pop, fits, k=3):
    idxs = random.sample(range(len(pop)), k)
    return pop[max(idxs, key=lambda i: fits[i])]


def crossover(p1, p2):
    return tuple(p1[i] if random.random() < 0.5 else p2[i] for i in range(len(GENES)))


def mutate(chrom, rate=0.2):
    genes = list(chrom)
    for i in range(len(genes)):
        if random.random() < rate:
            genes[i] = random.choice(ALL_GENE_VALUES[i])
    return tuple(genes)


# ── GA main loop ──

def ga_run(stocks_list, alpha, pop_size=50, n_gen=100, elite_n=5, seed=42):
    random.seed(seed)
    pop = [random_chromosome() for _ in range(pop_size)]
    pop[0] = V4_DEFAULTS  # seed current defaults into population

    best_ever_chrom = None
    best_ever_result = None
    best_fitness = -float("inf")
    history = []
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
        gen_best = fits[bi]
        avg_fit = sum(fits) / len(fits)

        if gen_best > best_fitness:
            best_fitness = gen_best
            best_ever_chrom = pop[bi]
            best_ever_result = results[bi]

        elapsed = time.perf_counter() - t0
        history.append((gen, gen_best, avg_fit))

        if gen % 10 == 0 or gen == n_gen - 1:
            _, tt, ft, ad, _ = results[bi]
            print(f"  Gen {gen:>3} | best={gen_best:>7.1f} avg={avg_fit:>7.1f} | "
                  f"真={tt} 假={ft} 跌={ad:+.1f}% | {elapsed:.1f}s | cache={len(eval_cache)}")

        if gen == n_gen - 1:
            break

        ranked = sorted(range(len(pop)), key=lambda i: fits[i], reverse=True)
        new_pop = [pop[ranked[i]] for i in range(elite_n)]
        while len(new_pop) < pop_size:
            p1 = tournament_select(pop, fits)
            p2 = tournament_select(pop, fits)
            child = mutate(crossover(p1, p2))
            new_pop.append(child)
        pop = new_pop

    # Final top-10
    all_chroms = list(eval_cache.items())
    all_chroms.sort(key=lambda x: x[1][0], reverse=True)
    top10 = all_chroms[:10]

    return best_ever_chrom, best_ever_result, history, top10, eval_cache


# ── Output helpers ──

def fmt_params(p):
    return (f"surge={p[0]:.1f}% win={int(p[1])} svr={p[2]:.2f} "
            f"cvr={p[3]:.2f} margin={p[4]:.1f}% noise={p[5]:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="單日 cache (e.g. 2026-06-16)")
    ap.add_argument("--days", default=None, help="多日逗號分隔 (e.g. 2026-06-16,2026-06-17)")
    ap.add_argument("--pop", type=int, default=50)
    ap.add_argument("--gen", type=int, default=100)
    ap.add_argument("--alpha", type=float, default=1.0, help="假山懲罰係數")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.day and not args.days:
        print("需要 --day 或 --days 參數", file=sys.stderr)
        sys.exit(1)

    days = args.days.split(",") if args.days else [args.day]
    stocks_list = []
    total_stocks = 0
    for d in days:
        cands, pool, minute, actual_day = load_cache(d.strip())
        stocks = preprocess(cands, pool, minute, actual_day)
        stocks_list.append((actual_day, stocks))
        total_stocks += len(stocks)
        print(f"  載入 {actual_day}: {len(stocks)} 檔", file=sys.stderr)

    print(f"\n{'═' * 65}")
    print(f"  造山 v4 GA 優化")
    print(f"  日期: {', '.join(days)} | 股票: {total_stocks} 檔")
    print(f"  族群: {args.pop} | 世代: {args.gen} | α={args.alpha} | seed={args.seed}")
    print(f"  搜索空間: {' × '.join(str(len(v)) for v in ALL_GENE_VALUES)}"
          f" = {eval('*'.join(str(len(v)) for v in ALL_GENE_VALUES)):,} 組合")
    print(f"{'═' * 65}")

    # Baseline: current v4 defaults
    base_result = eval_chromosome(V4_DEFAULTS, stocks_list, args.alpha)
    print(f"\n📊 基準 (v4 defaults): {fmt_params(V4_DEFAULTS)}")
    print(f"   fitness={base_result[0]:.1f} | 真={base_result[1]} 假={base_result[2]} "
          f"跌={base_result[3]:+.1f}%")
    for dl, dt, df, dd in base_result[4]:
        print(f"   {dl}: 真={dt} 假={df} 跌={dd:+.1f}%")

    print(f"\n🧬 開始演化...\n")
    t_start = time.perf_counter()
    best_chrom, best_result, history, top10, cache = ga_run(
        stocks_list, args.alpha,
        pop_size=args.pop, n_gen=args.gen, seed=args.seed,
    )
    t_total = time.perf_counter() - t_start

    print(f"\n{'═' * 65}")
    print(f"  演化完成 — {t_total:.0f}s | 評估 {len(cache)} 個獨立染色體")
    print(f"{'═' * 65}")

    print(f"\n🏆 最佳參數:")
    print(f"   {fmt_params(best_chrom)}")
    f, tt, ft, ad, per_day = best_result
    print(f"   fitness={f:.1f} | 真={tt} 假={ft} 跌={ad:+.1f}%")
    for dl, dt, df, dd in per_day:
        print(f"   {dl}: 真={dt} 假={df} 跌={dd:+.1f}%")

    # Improvement over baseline
    imp = best_result[0] - base_result[0]
    print(f"\n   vs 基準: fitness {'+' if imp >= 0 else ''}{imp:.1f}"
          f" (真 {best_result[1] - base_result[1]:+d},"
          f" 假 {best_result[2] - base_result[2]:+d},"
          f" 跌幅 {best_result[3] - base_result[3]:+.1f}pp)")

    print(f"\n📋 Top 10:")
    print(f"  {'#':>2} {'fitness':>8} {'真':>3} {'假':>3} {'跌%':>6}  參數")
    print(f"  {'─' * 2} {'─' * 8} {'─' * 3} {'─' * 3} {'─' * 6}  {'─' * 40}")
    for rank, (chrom, res) in enumerate(top10, 1):
        marker = " ◄ v4 default" if chrom == V4_DEFAULTS else ""
        print(f"  {rank:>2} {res[0]:>8.1f} {res[1]:>3} {res[2]:>3} {res[3]:>+5.1f}%  {fmt_params(chrom)}{marker}")

    # α sensitivity on best
    print(f"\n📈 α 敏感度 (最佳參數):")
    for a in [0.5, 1.0, 1.5, 2.0, 3.0]:
        f_a = tt * abs(ad) - a * ft
        print(f"   α={a:.1f}: fitness={f_a:.1f}")

    # Gene convergence
    print(f"\n🔬 Top-10 基因分布:")
    for gi, (name, lo, hi, step) in enumerate(GENES):
        vals = [chrom[gi] for chrom, _ in top10]
        lo_v, hi_v = min(vals), max(vals)
        avg_v = sum(vals) / len(vals)
        if lo_v == hi_v:
            print(f"   {name:<16} 收斂 → {lo_v}")
        else:
            print(f"   {name:<16} [{lo_v} ~ {hi_v}] avg={avg_v:.2f}")

    # Save results
    out = {
        "days": days,
        "alpha": args.alpha,
        "pop": args.pop,
        "gen": args.gen,
        "seed": args.seed,
        "baseline": {"params": list(V4_DEFAULTS), "fitness": base_result[0],
                      "true_tops": base_result[1], "false_tops": base_result[2],
                      "avg_drop": base_result[3]},
        "best": {"params": list(best_chrom), "fitness": best_result[0],
                 "true_tops": best_result[1], "false_tops": best_result[2],
                 "avg_drop": best_result[3], "per_day": best_result[4]},
        "top10": [{"params": list(c), "fitness": r[0], "true_tops": r[1],
                   "false_tops": r[2], "avg_drop": r[3]}
                  for c, r in top10],
        "history": history,
        "total_evals": len(cache),
        "elapsed_sec": round(t_total, 1),
    }
    out_path = CACHE_DIR / f"_ga_result_{'_'.join(days)}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n💾 結果已存: {out_path.name}")


if __name__ == "__main__":
    main()
