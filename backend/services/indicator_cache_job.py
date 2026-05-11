"""Indicator cache job — parallel fetch all active symbols' technicals → supabase.

Plan §Phase 2a：
- 從 symbols WHERE is_active=true 讀（~2363 檔）
- ThreadPoolExecutor(max_workers=8) + token bucket (5 req/s)
- 每檔抓 1 intraday.quote + 5 technical（rsi/macd/kdj/sma×3/bb）= 8 API call
- 寫入 indicator_cache (symbol, date) 複合主鍵
- 進度寫 indicator_cache_runs (run_date PK)；失敗時 alerts.notify_critical

「同表 + date filter staging」路線：
- reader 在 routes/screen.py 用 SELECT MAX(run_date) WHERE status='done' 取最後一次成功 date
- 任意時刻 kill 半截 job → reader 還是讀「上一個成功 date」，沒 inconsistent 列

效能粗估：2363 × 8 = 18904 calls / 5 req/s ≈ 63 min（首跑驗證 rate limit 真實值）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any, Callable

from services import alerts
from services.fubon_client import FubonStatus, get_fubon
from services.rate_limiter import get_rate_limiter
from services.supabase_client import SupabaseStatus, get_supabase

logger = logging.getLogger(__name__)

# 每檔抓的指標
SMA_PERIODS = (5, 20, 60)
MACD_PARAMS = {"fast": 12, "slow": 26, "signal": 9}
KDJ_PARAMS = {"rPeriod": 9, "kPeriod": 3, "dPeriod": 3}
BB_PARAMS = {"period": 20, "stdDev": 2}
RSI_PERIOD = 14

# 平行度（rate limiter 才是真正節流者，這裡只是 worker 上限）
MAX_WORKERS = 8

# DB 寫入批次（supabase 單次 payload 太大會 413）
WRITE_BATCH_SIZE = 200

# 每處理 N 檔就 update cache_runs.symbols_done 一次（避免每檔都打 supabase）
PROGRESS_UPDATE_EVERY = 100


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_running = False
_running_lock = asyncio.Lock()


def is_cache_job_running() -> bool:
    """For routes — check before triggering a new run."""
    return _running


async def run_cache_job(limit: int | None = None) -> dict[str, Any]:
    """Run a full indicator cache build.

    Args:
        limit: dev/smoke option — only process first N active symbols.
            None = all active symbols.

    Returns:
        dict with run_date, symbols_total, symbols_done, failed, elapsed_seconds, status.

    Raises:
        RuntimeError: when supabase / fubon not OK, or another job already running.
    """
    global _running

    # Atomic claim via lock-protected bool — second concurrent caller raises.
    async with _running_lock:
        if _running:
            raise RuntimeError("cache job already running")
        _running = True

    try:
        return await asyncio.to_thread(_run_cache_job_sync, limit)
    except Exception as e:
        await alerts.notify_critical(
            "indicator_cache_job failed",
            error=f"{type(e).__name__}: {e}",
            limit=str(limit),
        )
        raise
    finally:
        async with _running_lock:
            _running = False


def get_latest_done_run(client: Any) -> dict[str, Any] | None:
    """Return the most recent cache_runs row with status='done', or None.

    Used by routes/screen.py to know which date the cache reads should target.
    """
    res = (
        client.table("indicator_cache_runs")
        .select("*")
        .eq("status", "done")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def get_latest_run(client: Any) -> dict[str, Any] | None:
    """Return the most recent cache_runs row regardless of status, or None.

    Used by /api/health to surface cache state.
    """
    res = (
        client.table("indicator_cache_runs")
        .select("*")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Internal — sync worker pipeline
# ---------------------------------------------------------------------------


def _run_cache_job_sync(limit: int | None) -> dict[str, Any]:
    supabase = get_supabase()
    fubon = get_fubon()

    if supabase.status != SupabaseStatus.OK or supabase.client is None:
        raise RuntimeError(f"supabase not available: {supabase.last_error}")
    if fubon.status != FubonStatus.OK or fubon.sdk is None:
        raise RuntimeError(f"fubon SDK not available: {fubon.last_error}")

    client = supabase.client
    sdk = fubon.sdk

    today = date.today()
    is_trading = today.weekday() < 5  # TODO: 真實 TWSE 行事曆（plan §Phase 2.5）

    symbols = _fetch_active_symbols(client, limit=limit)
    if not symbols:
        raise RuntimeError("no active symbols (run /api/symbols/refresh first)")

    logger.info(
        "cache job start: date=%s, symbols=%d, workers=%d, rate=%.1f/s",
        today,
        len(symbols),
        MAX_WORKERS,
        get_rate_limiter().rate,
    )

    _upsert_run_row(
        client,
        run_date=today,
        is_trading_day=is_trading,
        symbols_total=len(symbols),
        status="running",
    )

    started = time.monotonic()
    completed = 0
    failed_count = 0
    pending_rows: list[dict[str, Any]] = []

    def worker(sym: str) -> dict[str, Any] | None:
        try:
            return _fetch_symbol(sdk, sym, today)
        except Exception as e:
            logger.warning(
                "cache fetch failed: %s — %s: %s", sym, type(e).__name__, e
            )
            return None

    try:
        # as_completed iterator runs in this thread; only fetches inside `worker`
        # are parallel — so pending_rows / counters are single-threaded here.
        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="cache") as pool:
            futures = {pool.submit(worker, s): s for s in symbols}
            for fut in as_completed(futures):
                row = fut.result()
                if row is None:
                    failed_count += 1
                else:
                    pending_rows.append(row)
                completed += 1

                if completed % PROGRESS_UPDATE_EVERY == 0:
                    if pending_rows:
                        _upsert_cache_rows(client, pending_rows)
                        pending_rows.clear()
                    _update_run_progress(client, today, completed)
                    logger.info(
                        "cache progress: %d/%d (failed=%d)",
                        completed,
                        len(symbols),
                        failed_count,
                    )

        if pending_rows:
            _upsert_cache_rows(client, pending_rows)
            pending_rows.clear()

        elapsed = time.monotonic() - started
        _finalize_run(client, today, completed=completed, status="done")
        logger.info(
            "cache job DONE: %d/%d symbols in %.1fs (failed=%d)",
            completed,
            len(symbols),
            elapsed,
            failed_count,
        )
        return {
            "run_date": today.isoformat(),
            "symbols_total": len(symbols),
            "symbols_done": completed,
            "failed": failed_count,
            "elapsed_seconds": round(elapsed, 1),
            "status": "done",
        }

    except Exception as e:
        elapsed = time.monotonic() - started
        err = f"{type(e).__name__}: {e}"
        logger.error("cache job FAILED after %.1fs: %s", elapsed, err)
        try:
            _finalize_run(
                client, today, completed=completed, status="failed", error=err
            )
        except Exception as inner:
            logger.error("also failed to write cache_runs failed-state: %s", inner)
        raise


# ---------------------------------------------------------------------------
# Per-symbol fetch (runs in worker thread)
# ---------------------------------------------------------------------------


def _fetch_symbol(sdk: Any, symbol: str, today: date) -> dict[str, Any]:
    """Fetch one symbol's price + 5 technicals. Returns row dict for upsert."""
    limiter = get_rate_limiter()
    tech = sdk.marketdata.rest_client.stock.technical
    intraday = sdk.marketdata.rest_client.stock.intraday

    row: dict[str, Any] = {"symbol": symbol, "date": today.isoformat()}

    # ---- price (intraday.quote) ----
    # 實測 field：closePrice / changePercent / total.tradeVolume / total.tradeValue
    # （probe_technical_fields.py 2026-05-11 驗）
    limiter.acquire()
    quote = _safe_call(intraday.quote, symbol=symbol)
    if quote:
        row["close"] = _to_float(quote.get("closePrice") or quote.get("lastPrice"))
        row["change_pct"] = _to_float(quote.get("changePercent"))
        total = quote.get("total") or {}
        row["volume"] = _to_int(total.get("tradeVolume"))
        row["amount"] = _to_float(total.get("tradeValue"))

    # ---- RSI ----
    limiter.acquire()
    rsi = _safe_call(tech.rsi, symbol=symbol, period=RSI_PERIOD)
    row["rsi_14"] = _last_value(rsi, "rsi")

    # ---- MACD ----
    # 實測 field：macdLine / signalLine（probe_technical_fields.py 2026-05-11 驗）
    limiter.acquire()
    macd = _safe_call(tech.macd, symbol=symbol, **MACD_PARAMS)
    row["macd"] = _last_value(macd, "macdLine")
    row["macd_signal"] = _last_value(macd, "signalLine")

    # ---- KDJ ----
    limiter.acquire()
    kdj = _safe_call(tech.kdj, symbol=symbol, **KDJ_PARAMS)
    row["kdj_k"] = _last_value(kdj, "k")
    row["kdj_d"] = _last_value(kdj, "d")
    row["kdj_j"] = _last_value(kdj, "j")

    # ---- SMA 5/20/60 ----
    for period in SMA_PERIODS:
        limiter.acquire()
        sma = _safe_call(tech.sma, symbol=symbol, period=period)
        row[f"sma_{period}"] = _last_value(sma, "sma")

    # ---- Bollinger Bands ----
    limiter.acquire()
    bb = _safe_call(tech.bb, symbol=symbol, **BB_PARAMS)
    row["bbands_upper"] = _last_value(bb, "upper")
    row["bbands_middle"] = _last_value(bb, "middle")
    row["bbands_lower"] = _last_value(bb, "lower")

    return row


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------


# 只跑 4 位純數字個股（1101 台泥 ~ 9962 太電）
# 排除：ETF（00xxx/006xxx/009xxx）、ETN（020xxx）、REITs（0xxxxT/R）、
# TDR（91xxxx）、特別股（1101B / 2887Z1 等 4位數+字母）
_INDIVIDUAL_STOCK_PATTERN = re.compile(r"^[0-9]{4}$")


_PAGE_SIZE = 1000  # PostgREST 預設 hard cap，每頁最多 1000


def _fetch_active_symbols(client: Any, limit: int | None) -> list[str]:
    """讀 active 個股 symbol，用 .range() 分頁繞過 PostgREST 1000 列硬上限。

    client side 用 regex `^[0-9]{4}$` 只留 4 位純數字（個股），
    把 ETF/ETN/REITs/TDR/特別股全切掉。
    """
    all_syms: list[str] = []
    page = 0
    while True:
        start = page * _PAGE_SIZE
        end = start + _PAGE_SIZE - 1
        res = (
            client.table("symbols")
            .select("symbol")
            .eq("is_active", True)
            .eq("is_etf", False)
            .order("symbol")
            .range(start, end)
            .execute()
        )
        rows = res.data or []
        all_syms.extend(r["symbol"] for r in rows)
        if len(rows) < _PAGE_SIZE:
            break
        page += 1

    syms = [s for s in all_syms if _INDIVIDUAL_STOCK_PATTERN.fullmatch(s)]
    logger.info(
        "fetched %d active symbols across %d page(s); kept %d individual stocks (filtered %d)",
        len(all_syms),
        page + 1,
        len(syms),
        len(all_syms) - len(syms),
    )

    if limit is not None:
        syms = syms[:limit]
    return syms


def _upsert_run_row(
    client: Any,
    *,
    run_date: date,
    is_trading_day: bool,
    symbols_total: int,
    status: str,
) -> None:
    client.table("indicator_cache_runs").upsert(
        {
            "run_date": run_date.isoformat(),
            "is_trading_day": is_trading_day,
            "symbols_total": symbols_total,
            "symbols_done": 0,
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error_text": None,
        },
        on_conflict="run_date",
    ).execute()


def _update_run_progress(client: Any, run_date: date, symbols_done: int) -> None:
    try:
        client.table("indicator_cache_runs").update(
            {"symbols_done": symbols_done}
        ).eq("run_date", run_date.isoformat()).execute()
    except Exception as e:
        logger.warning("progress update failed (non-fatal): %s", e)


def _finalize_run(
    client: Any,
    run_date: date,
    *,
    completed: int,
    status: str,
    error: str | None = None,
) -> None:
    client.table("indicator_cache_runs").update(
        {
            "symbols_done": completed,
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error_text": error,
        }
    ).eq("run_date", run_date.isoformat()).execute()


def _upsert_cache_rows(client: Any, rows: list[dict[str, Any]]) -> None:
    """Batched upsert (composite PK = symbol+date)."""
    for i in range(0, len(rows), WRITE_BATCH_SIZE):
        batch = rows[i : i + WRITE_BATCH_SIZE]
        client.table("indicator_cache").upsert(
            batch, on_conflict="symbol,date"
        ).execute()


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------


def _safe_call(fn: Callable[..., Any], **kwargs: Any) -> dict[str, Any] | None:
    """Call SDK method, swallow exceptions (per-symbol failure non-fatal)."""
    try:
        return fn(**kwargs)
    except Exception as e:
        logger.debug("SDK call failed: %s(%s) — %s", fn.__name__, kwargs, e)
        return None


def _last_value(result: dict[str, Any] | None, field: str) -> float | None:
    """Pull `field` from the most recent entry in result['data']."""
    if not result:
        return None
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None
    last = data[-1]
    if not isinstance(last, dict):
        return None
    return _to_float(last.get(field))


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
