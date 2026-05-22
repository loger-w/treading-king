"""POST /api/symbols/refresh — 從 TWSE/OTC 公開檔抓全市場 symbol 主表 → upsert supabase.
GET  /api/symbols?search=&limit= — 給 Monitor 搜尋框 + 加入自選股用。

主要來源:ISIN 表(全部上市/上櫃股票,不限當日有交易)
  - 上市: https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
  - 上櫃: https://isin.twse.com.tw/isin/C_public.jsp?strMode=4

補充來源(若 ISIN 解析失敗):OpenAPI(只回當日有交易股)
  - TWSE: https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  - OTC: https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes
"""
from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Query

# 非普通股判斷:ETF (00xx) / ETN (020x) / 特別股 (4位數字+字母)
_PREFERRED_RE = re.compile(r"^\d{4}[A-Z]$")


def _is_non_stock(code: str) -> bool:
    return (
        code.startswith("00")
        or code.startswith("020")
        or bool(_PREFERRED_RE.match(code))
    )


# ISIN 表的「代號 名稱」格式:`0050　元大台灣50`(中間是全形空格 U+3000)
# 部分 row 可能用 ASCII 空格,兩種都吃
_ISIN_CODE_NAME_RE = re.compile(
    r"<td[^>]*>\s*(\d{4}[A-Z]?)[　\s]+([^<]+?)\s*</td>",
    re.IGNORECASE,
)
# ISIN 「股票」section 邊界(只取『股票』下面那段、不含特別股/ETF/ETN/可轉債)
_ISIN_SECTION_START_RE = re.compile(
    r'<tr[^>]*>\s*<td[^>]*colspan="?7"?[^>]*>\s*股票\s*</td>',
    re.IGNORECASE,
)
_ISIN_NEXT_SECTION_RE = re.compile(
    r'<tr[^>]*>\s*<td[^>]*colspan="?7"?',
    re.IGNORECASE,
)


def _parse_isin_html(html: str) -> list[tuple[str, str]]:
    """從 ISIN 表 HTML 抽「股票」section 的 (代號, 名稱) list。
    非 4 位純數字代號(權證/特別股/可轉債)在這裡先抓,呼叫端自行篩。"""
    m = _ISIN_SECTION_START_RE.search(html)
    if not m:
        return []
    section_start = m.end()
    # 「股票」section 之後遇到下一個 colspan=7 row 就結束(代表進入特別股/ETF 等)
    next_m = _ISIN_NEXT_SECTION_RE.search(html, section_start)
    section_end = next_m.start() if next_m else len(html)
    section = html[section_start:section_end]
    return [(m.group(1), m.group(2).strip()) for m in _ISIN_CODE_NAME_RE.finditer(section)]


async def _fetch_isin(client: httpx.AsyncClient, mode: int, market: str) -> tuple[list[dict], str | None]:
    """抓單一 ISIN 表(strMode=2 上市 / strMode=4 上櫃)。
    回 (rows, error_str)。失敗時 rows 為空、error_str 描述原因。"""
    url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
    try:
        r = await client.get(url)
        r.raise_for_status()
        # ISIN 表是 big5
        html = r.content.decode("big5", errors="replace")
        pairs = _parse_isin_html(html)
        rows = [
            {
                "symbol": code,
                "name": name,
                "market": market,
                "is_etf": _is_non_stock(code),
                "is_active": True,
            }
            for code, name in pairs
            if code and name
        ]
        return rows, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


from services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/symbols")
async def search_symbols(
    search: str = Query("", description="Prefix match on symbol, contains match on name"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """搜 symbol（給 Monitor 搜尋框 / 加入自選股用）。"""
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
    by_symbol: dict[str, dict] = {}

    # verify=False: TWSE/OTC public 站,cert 缺 Subject Key Identifier extension
    # (Python 3.13 嚴格驗證 fail)。資料公開、無 secret;Supabase / Fubon API 另一 client
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        # ----- 主來源: ISIN 表(全部上市/上櫃股票) -----
        for mode, market in ((2, "TWSE"), (4, "OTC")):
            isin_rows, isin_err = await _fetch_isin(client, mode, market)
            if isin_err:
                logger.warning("ISIN strMode=%d (%s) failed: %s", mode, market, isin_err)
                errors.append(f"ISIN strMode={mode}: {isin_err}")
            else:
                for row in isin_rows:
                    by_symbol[row["symbol"]] = row
                logger.info("ISIN %s: parsed %d symbols", market, len(isin_rows))

        # ----- 補充來源: TWSE OpenAPI STOCK_DAY_ALL(補 ISIN 漏掉的) -----
        try:
            r = await client.get(
                "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            )
            r.raise_for_status()
            data = r.json()
            new_count = 0
            for item in data:
                code = (item.get("Code") or "").strip()
                name = (item.get("Name") or "").strip()
                if not code or not name:
                    continue
                if code in by_symbol:
                    continue  # ISIN 已有
                by_symbol[code] = {
                    "symbol": code,
                    "name": name,
                    "market": "TWSE",
                    "is_etf": _is_non_stock(code),
                    "is_active": True,
                }
                new_count += 1
            if new_count:
                logger.info("TWSE OpenAPI: +%d symbols(ISIN 漏掉的)", new_count)
        except Exception as e:
            err = f"TWSE OpenAPI fetch failed: {type(e).__name__}: {e}"
            logger.warning(err)
            errors.append(err)

        # ----- 補充來源: TPEx OpenAPI(補 ISIN 漏掉的) -----
        try:
            r = await client.get(
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
            )
            r.raise_for_status()
            data = r.json()
            new_count = 0
            for item in data:
                code = (item.get("SecuritiesCompanyCode") or "").strip()
                name = (item.get("CompanyName") or "").strip()
                if not code or not name:
                    continue
                if code in by_symbol:
                    continue
                by_symbol[code] = {
                    "symbol": code,
                    "name": name,
                    "market": "OTC",
                    "is_etf": _is_non_stock(code),
                    "is_active": True,
                }
                new_count += 1
            if new_count:
                logger.info("OTC OpenAPI: +%d symbols(ISIN 漏掉的)", new_count)
        except Exception as e:
            err = f"OTC OpenAPI fetch failed: {type(e).__name__}: {e}"
            logger.warning(err)
            errors.append(err)

        rows = list(by_symbol.values())

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
