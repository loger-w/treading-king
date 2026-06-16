"""每日盤後自動攢資料 — 建當日獨立池 + 抓 1 分 K + CDP,存成 GA cache 格式。

收盤後執行:
  .venv\\Scripts\\python scripts\\_daily_pool_fetcher.py

功能:
  1. 打 snapshot.movers(up/down) + actives(volume/value) 建當日候選池
  2. 篩選: 振幅>3% + 量>3000張 + 4位純數字 + 排除處置股
  3. 抓 1 分 K
  4. 存成 _diag_auto_monitor_cache_{date}.json (與 _diag_auto_monitor_backtest.py 同格式)

處置股清單:
  每次跑時會嘗試從 TWSE/TPEx 拉最新清單;失敗時 fallback 到硬編碼清單。
"""
import json
import os
import re
import sys
import time as _time
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from services.cdp import compute_cdp

CACHE_DIR = BACKEND / "scripts"
SYMBOL_RE = re.compile(r"^\d{4}$")

MIN_AMP_PCT = 3.0
MIN_VOL_LOTS = 3000

DISPOSITION_FALLBACK = {
    "1409", "1563", "1568", "1714", "1718",
    "2337", "2491", "2492", "3021", "3149",
    "3346", "3481", "3535", "4807", "6116",
    "6442", "6830", "6890", "7610", "8021", "8454",
}


def amp(r):
    hp, lp = r.get("highPrice"), r.get("lowPrice")
    return (hp - lp) / lp * 100 if (hp and lp and lp > 0) else 0.0


def passes_screen(r, disposition):
    sym = (r.get("symbol") or "").strip()
    vol = r.get("tradeVolume")
    if not sym or vol is None:
        return False
    if not SYMBOL_RE.match(sym):
        return False
    if sym in disposition:
        return False
    if amp(r) <= MIN_AMP_PCT:
        return False
    if vol <= MIN_VOL_LOTS:
        return False
    return True


def rows_of(r):
    if isinstance(r, dict):
        d = r.get("data", r)
        return d if isinstance(d, list) else (d.get("data", []) if isinstance(d, dict) else [])
    return r if isinstance(r, list) else []


def fetch_disposition():
    """嘗試從網路拉處置股清單,失敗回 fallback。"""
    try:
        import urllib.request
        url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        syms = set()
        for row in data.get("data", []):
            if row and len(row) > 0:
                m = re.match(r"(\d{4})", str(row[0]))
                if m:
                    syms.add(m.group(1))
        if syms:
            print(f"  處置股線上清單: {len(syms)} 檔", file=sys.stderr)
            return syms
    except Exception as e:
        print(f"  處置股線上查詢失敗({e}),用 fallback", file=sys.stderr)
    return DISPOSITION_FALLBACK


def fetch():
    from fubon_neo.sdk import FubonSDK, Mode
    pid = os.getenv("FUBON_PERSONAL_ID", "").strip() or os.getenv("FUBON_ACCOUNT", "").strip()
    key = os.getenv("FUBON_API_KEY", "").strip()
    cert = os.getenv("FUBON_CERT_PATH", "").strip()
    sdk = FubonSDK()
    if cert:
        sdk.apikey_login(pid, key, cert, os.getenv("FUBON_CERT_PASS", "").strip() or None)
    else:
        sdk.apikey_dma_login(pid, key)
    sdk.init_realtime(Mode.Normal)
    rest = sdk.marketdata.rest_client.stock

    disposition = fetch_disposition()

    try:
        pool, DAY = {}, None
        for market in ["TSE", "OTC"]:
            for direction in ["up", "down"]:
                _time.sleep(0.3)
                m = rest.snapshot.movers(market=market, direction=direction,
                                         change="percent", type="COMMONSTOCK")
                if DAY is None and isinstance(m, dict):
                    DAY = m.get("date")
                for r in rows_of(m):
                    pool.setdefault(r["symbol"], r)
            for trade in ["volume", "value"]:
                _time.sleep(0.3)
                a = rest.snapshot.actives(market=market, trade=trade)
                if DAY is None and isinstance(a, dict):
                    DAY = a.get("date")
                for r in rows_of(a)[:200]:
                    pool.setdefault(r["symbol"], r)

        all_cands = [s for s, r in pool.items() if passes_screen(r, disposition)]
        all_cands.sort(key=lambda s: -pool[s].get("changePercent", 0))
        print(f"\n  日期: {DAY}", file=sys.stderr)
        print(f"  篩出: {len(all_cands)} 檔 (振幅>{MIN_AMP_PCT}% 量>{MIN_VOL_LOTS}張"
              f" 排除{len(disposition)}檔處置)", file=sys.stderr)

        minute = {}
        for i, sym in enumerate(all_cands):
            _time.sleep(1.1)
            rm = rest.historical.candles(symbol=sym, from_=DAY, to=DAY, timeframe="1")
            by_day = defaultdict(list)
            for row in (rm or {}).get("data", []):
                dd = row.get("date", "")
                by_day[dd[:10]].append((dd[11:16], float(row["open"]), float(row["high"]),
                                        float(row["low"]), float(row["close"]),
                                        int(row.get("volume") or 0)))
            minute[sym] = {d: sorted(v) for d, v in by_day.items()}
            if (i + 1) % 20 == 0:
                print(f"  抓 1 分 K: {i + 1}/{len(all_cands)}...", file=sys.stderr)

        print(f"  完成: {len(all_cands)} 檔 1 分 K", file=sys.stderr)
        return DAY, all_cands, pool, minute
    finally:
        try:
            sdk.logout()
        except Exception:
            pass


def build_cdp(cands, pool, minute, day):
    """從前一日 1 分 K 推算 daily OHLC → compute_cdp。"""
    cdp_data = {}
    missing = []
    for sym in cands:
        sym_days = sorted(minute.get(sym, {}).keys())
        prev_days = [d for d in sym_days if d < day]
        if not prev_days:
            missing.append(sym)
            continue
        prev_day = prev_days[-1]
        candles = minute[sym][prev_day]
        if not candles:
            missing.append(sym)
            continue
        h = max(c[2] for c in candles)
        l = min(c[3] for c in candles)
        p = pool.get(sym, {})
        prev_close = round(p.get("closePrice", 0) - p.get("change", 0), 2)
        if prev_close <= 0:
            prev_close = candles[-1][4]
        levels = compute_cdp(h, l, prev_close)
        levels["prev_close"] = prev_close
        levels["as_of"] = prev_day
        cdp_data[sym] = levels
    return cdp_data, missing


def main():
    print("═══════════════════════════════════════════════════════")
    print("  每日盤後資料收集 — 自動監聽選股池 + 1 分 K + CDP")
    print("═══════════════════════════════════════════════════════")

    DAY, all_cands, pool, minute = fetch()

    cp = CACHE_DIR / f"_diag_auto_monitor_cache_{DAY}.json"
    if cp.exists():
        print(f"\n⚠️  cache 已存在: {cp.name}", file=sys.stderr)
        print(f"   覆蓋? [y/N] ", end="", file=sys.stderr)
        ans = input().strip().lower()
        if ans != "y":
            print("取消", file=sys.stderr)
            return

    cdp_data, cdp_missing = build_cdp(all_cands, pool, minute, DAY)

    data = {"DAY": DAY, "cands": all_cands, "pool": pool, "minute": minute,
            "cdp": cdp_data}
    cp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n✓ 已存 cache: {cp.name}")
    print(f"  {len(all_cands)} 檔 | {DAY}")

    with_candles = sum(1 for s in all_cands if minute.get(s, {}).get(DAY))
    print(f"  有 1 分 K: {with_candles} 檔")
    print(f"  CDP: {len(cdp_data)}/{len(all_cands)} 檔"
          + (f" | 缺: {', '.join(cdp_missing)}" if cdp_missing else ""))


if __name__ == "__main__":
    main()
