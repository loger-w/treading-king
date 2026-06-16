"""GET /api/auto_monitor — 自動監聽清單(記憶體快取,由排程填充)。"""
from __future__ import annotations

from fastapi import APIRouter

from routes._item_enrich import enrich_item
from services.local_store import get_local_store

router = APIRouter()


@router.get("/api/auto_monitor")
async def list_auto_monitor() -> dict:
    store = get_local_store()
    rows = store.market.get_auto_monitor()
    items = []
    for r in rows:
        base = enrich_item({"symbol": r["symbol"]}, store.market)
        base.update({
            "change_pct": r.get("change_pct"),
            "amplitude_pct": r.get("amplitude_pct"),
            "volume_lots": r.get("volume_lots"),
            "market": r.get("market"),
            "rank": r.get("rank"),
            "captured_at": r.get("captured_at"),
        })
        items.append(base)
    return {"items": items, "count": len(items)}
