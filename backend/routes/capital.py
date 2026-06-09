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
    return {"orders": [o.model_dump(mode="json") for o in c.store.orders()]}


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
