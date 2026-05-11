"""POST /api/symbols/refresh — 從 TWSE/OTC 公開檔抓全市場 symbol 主表 → upsert supabase.
GET  /api/symbols?search=&limit= — Phase 2b：給 watchlist / 條件編輯器搜 symbol 用。

資料源（公開、免登入）：
- TWSE 上市：https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  或 ISIN 表 https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
- OTC 上櫃：https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes
  或 ISIN https://isin.twse.com.tw/isin/C_public.jsp?strMode=4
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/symbols")
async def search_symbols(
    search: str = Query("", description="Prefix match on symbol, contains match on name"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """搜 symbol（給 watchlist / 條件編輯器選股用）。"""
    sb = get_supabase()
    if sb.status.value != "ok" or sb.client is None:
        raise HTTPException(
            503, detail={"error": "supabase_unavailable", "last_error": sb.last_error}
        )

    q = (
        sb.client.table("symbols")
        .select("symbol, name, market, is_etf")
        .eq("is_active", True)
    )
    s = search.strip()
    if s:
        # symbol 前綴 OR name 模糊
        q = q.or_(f"symbol.ilike.{s}%,name.ilike.%{s}%")
    res = q.order("symbol").limit(limit).execute()
    return {"results": res.data or []}


@router.post("/api/symbols/refresh")
async def refresh_symbols() -> dict:
    supabase = get_supabase()
    if supabase.status.value != "ok":
        raise HTTPException(
            503,
            detail={"error": "supabase_unavailable", "last_error": supabase.last_error},
        )

    rows: list[dict] = []
    errors: list[str] = []

    # verify=False: TWSE/OTC public OpenAPI 抓行情清單，cert 缺 Subject Key Identifier
    # extension（Python 3.13 嚴格驗證會 fail）。資料是公開股票代碼，無 secret，
    # 不需 SSL 認證；Supabase / Fubon API 用另一個 client 不受影響
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        # ----- 上市 TWSE (透過 OpenAPI v1 STOCK_DAY_ALL) -----
        try:
            r = await client.get(
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            )
            r.raise_for_status()
            data = r.json()
            for item in data:
                code = (item.get("Code") or "").strip()
                name = (item.get("Name") or "").strip()
                if not code or not name:
                    continue
                rows.append(
                    {
                        "symbol": code,
                        "name": name,
                        "market": "TWSE",
                        "industry": None,
                        "is_etf": code.startswith("00"),
                        "is_active": True,
                    }
                )
            logger.info("TWSE: parsed %d symbols", len(data))
        except Exception as e:
            err = f"TWSE fetch failed: {type(e).__name__}: {e}"
            logger.warning(err)
            errors.append(err)

        # ----- 上櫃 OTC (TPEx OpenAPI) -----
        try:
            r = await client.get(
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
            )
            r.raise_for_status()
            data = r.json()
            for item in data:
                code = (item.get("SecuritiesCompanyCode") or "").strip()
                name = (item.get("CompanyName") or "").strip()
                if not code or not name:
                    continue
                rows.append(
                    {
                        "symbol": code,
                        "name": name,
                        "market": "OTC",
                        "industry": None,
                        "is_etf": code.startswith("00"),
                        "is_active": True,
                    }
                )
            logger.info("OTC: parsed %d symbols", len(data))
        except Exception as e:
            err = f"OTC fetch failed: {type(e).__name__}: {e}"
            logger.warning(err)
            errors.append(err)

    if not rows:
        raise HTTPException(
            502,
            detail={"error": "no_symbols_fetched", "fetch_errors": errors},
        )

    # Upsert in batches (supabase has payload size limits)
    BATCH = 500
    inserted_total = 0
    try:
        client_obj = supabase.client
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            res = client_obj.table("symbols").upsert(batch, on_conflict="symbol").execute()
            inserted_total += len(res.data) if hasattr(res, "data") and res.data else len(batch)
        logger.info("Upserted %d symbols", inserted_total)
    except Exception as e:
        logger.error("supabase upsert failed: %s", e)
        raise HTTPException(
            500,
            detail={"error": "supabase_upsert_failed", "detail": str(e)},
        )

    return {
        "status": "ok",
        "fetched": len(rows),
        "upserted": inserted_total,
        "errors": errors,
    }
