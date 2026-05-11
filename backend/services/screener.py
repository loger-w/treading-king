"""篩股引擎 — Filter DSL → indicator_cache query → 結果列表。

Plan §Phase 2b：cache JOIN symbols → 套 conditions → 回 symbols + 各 condition 當下值。
**不打富邦 API**，純 supabase 讀取。

策略：
- 拉「最後一次成功 cache_runs.run_date」對應的 indicator_cache 全列（pagination）
- JOIN symbols 拿 name / market / is_etf
- Python 端套 filter conditions（市場過濾 + 16 個 numeric 條件 + AND/OR）
- 排序：按第一個 condition 的 field 升冪
- limit

效能：1962 row × ~22 col ≈ < 1MB，Python 端 5 conditions × 1962 比較 < 10ms。
未來 user 量大才考慮搬 Postgres function。
"""
from __future__ import annotations

import logging
from typing import Any

from models.condition import ALL_FIELDS, Condition, Filter

logger = logging.getLogger(__name__)


def screen(client: Any, filter_: Filter) -> dict[str, Any]:
    """執行篩股，回傳 metadata + matched results。

    Raises:
        RuntimeError: 沒有成功的 cache run。
    """
    target_date = _get_latest_done_date(client)
    if not target_date:
        raise RuntimeError("no successful cache run yet — POST /api/cache/refresh first")

    rows = _fetch_latest_cache_rows(client, target_date)
    logger.info("screen: loaded %d cache rows for date=%s", len(rows), target_date)

    matched = []
    for r in rows:
        sym_meta = r.get("symbols") or {}

        if sym_meta.get("market") not in filter_.market:
            continue
        if filter_.exclude_etf and sym_meta.get("is_etf"):
            continue

        results = [_check_condition(r, c) for c in filter_.conditions]
        ok = all(results) if filter_.logic == "AND" else any(results)
        if not ok:
            continue

        matched.append(_format_row(r, sym_meta))

    # 排序：第一個 condition 的 field 升冪（NULL 排最後）
    sort_field = filter_.conditions[0].field
    matched.sort(key=lambda x: (x.get(sort_field) is None, x.get(sort_field) or 0))

    return {
        "as_of_date": target_date,
        "total_scanned": len(rows),
        "count": len(matched[: filter_.limit]),
        "results": matched[: filter_.limit],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_latest_done_date(client: Any) -> str | None:
    res = (
        client.table("indicator_cache_runs")
        .select("run_date")
        .eq("status", "done")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0]["run_date"] if rows else None


_PAGE_SIZE = 1000  # PostgREST hard cap


def _fetch_latest_cache_rows(client: Any, target_date: str) -> list[dict[str, Any]]:
    """分頁拉指定 date 的 indicator_cache 全部 row + JOIN symbols。"""
    fields = (
        "symbol, date, "
        + ", ".join(ALL_FIELDS)
        + ", symbols(name, market, is_etf)"
    )
    all_rows: list[dict[str, Any]] = []
    page = 0
    while True:
        start = page * _PAGE_SIZE
        end = start + _PAGE_SIZE - 1
        res = (
            client.table("indicator_cache")
            .select(fields)
            .eq("date", target_date)
            .range(start, end)
            .execute()
        )
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < _PAGE_SIZE:
            break
        page += 1
    return all_rows


def _check_condition(row: dict[str, Any], c: Condition) -> bool:
    """套單一 condition；NULL 一律算 False（保守，避免假命中）。"""
    lhs = row.get(c.field)
    if lhs is None:
        return False

    if isinstance(c.value, str):
        # value 是欄位名 → 跨欄位比較（譬如 close > sma_20）
        rhs = row.get(c.value)
        if rhs is None:
            return False
    else:
        rhs = c.value

    op = c.operator
    if op == "gt":
        return lhs > rhs
    if op == "gte":
        return lhs >= rhs
    if op == "lt":
        return lhs < rhs
    if op == "lte":
        return lhs <= rhs
    if op == "eq":
        return lhs == rhs
    return False


def _format_row(r: dict[str, Any], sym_meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "symbol": r["symbol"],
        "name": sym_meta.get("name"),
        "market": sym_meta.get("market"),
        "is_etf": sym_meta.get("is_etf"),
    }
    for f in ALL_FIELDS:
        out[f] = r.get(f)
    return out
