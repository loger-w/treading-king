# backend/routes/capital.py
"""群益下單面板 API。讀快取 / 送單。富邦無關。

get_capital 走 module 參照(capital_factory.get_capital())而非 from-import,
測試才能用 monkeypatch.setattr(factory, "get_capital", ...) 替換。
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from services import capital_factory
from services.capital_models import StockOrderRequest

router = APIRouter()

_SEC_MARKETS = {"TS", "TA", "TL", "TP", "TC"}


def _symbol_name(stock_no: str | None) -> str:
    """代號→名稱;查無(期貨代號/未爬)回空字串。獨立函式方便測試 monkeypatch。"""
    if not stock_no:
        return ""
    from services.local_store import get_local_store
    row = get_local_store().market.get_symbol(stock_no)
    return row["name"] if row else ""


@router.get("/api/capital/status")
async def capital_status() -> dict:
    c = capital_factory.get_capital()
    if c is None:
        return {"status": "disabled"}
    return {"status": c.status, "last_error": c.last_error}


@router.get("/api/capital/orders")
async def capital_orders() -> dict:
    c = capital_factory.get_capital()
    if c is None:
        return {"orders": []}
    out = []
    for o in c.store.orders():
        # v1 委託清單只顯證券;期權回報照存不顯(未來期貨面板用)。market 缺值寬鬆放行。
        if o.market is not None and o.market not in _SEC_MARKETS:
            continue
        o.name = _symbol_name(o.stock_no)
        out.append(o.model_dump(mode="json"))
    return {"orders": out}


@router.get("/api/capital/positions")
async def capital_positions() -> dict:
    c = capital_factory.get_capital()
    if c is None:
        return {"positions": []}
    return {"positions": [p.model_dump(mode="json") for p in c.store.positions()]}


@router.post("/api/capital/order/stock")
async def capital_order_stock(req: StockOrderRequest) -> dict:
    c = capital_factory.get_capital()
    if c is None:
        raise HTTPException(503, detail={"error": "capital_disabled"})
    res = await c.submit_stock_order(req)
    return res.model_dump(mode="json")
