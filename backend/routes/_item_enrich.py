"""共用:把 config item(只有 symbol)補上 name/market/is_etf(查不到全為 None,best-effort)。"""
from __future__ import annotations


def enrich_item(item: dict, market) -> dict:
    meta = market.get_symbol(item["symbol"])
    return {
        **item,
        "name": meta["name"] if meta else None,
        "market": meta["market"] if meta else None,
        "is_etf": meta["is_etf"] if meta else None,
    }
