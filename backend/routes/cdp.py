"""GET /api/cdp/{symbol} — 回 CDP 5 線值。

get_levels_or_503 同時給 routes/camarilla.py 用 — 兩條 route 流程同構
(get → 失敗 lazy backfill 一次 → 再 get → 503),抽共用避免只修一邊分岔。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from services.cdp import get_cdp_service

router = APIRouter()

# (error_prefix, symbol) → 當日 backfill 已失敗。富邦沒這檔歷史資料(下市/
# 打錯代碼)時 service cache 永遠填不起來,沒有這層 negative cache 的話每個
# 請求都會真打一次 historical API(1 req/s 配額),排擠背景 backfill。
_failed_backfill: dict[tuple[str, str], date] = {}


async def get_levels_or_503(service: Any, symbol: str, error_prefix: str) -> dict:
    """get → 失敗 lazy backfill(當日最多一次)→ 再 get → 503。"""
    levels = await service.get(symbol)
    if levels is not None:
        return levels  # type: ignore[return-value]

    key = (error_prefix, symbol)
    today = date.today()
    if _failed_backfill.get(key) == today:
        raise HTTPException(503, detail={
            "error": f"{error_prefix}_data_unavailable", "symbol": symbol})

    ok = await service.backfill_from_fubon(symbol)
    if not ok:
        _failed_backfill[key] = today
        raise HTTPException(503, detail={
            "error": f"{error_prefix}_data_unavailable", "symbol": symbol})
    levels = await service.get(symbol)
    if levels is None:
        raise HTTPException(503, detail={
            "error": f"{error_prefix}_data_unavailable_after_backfill"})
    return levels  # type: ignore[return-value]


@router.get("/api/cdp/{symbol}")
async def get_cdp(symbol: str) -> dict:
    return await get_levels_or_503(get_cdp_service(), symbol, "cdp")
