"""Camarilla Pivot 8 線 — 從昨日 OHLC 算 8 個值,盤中為固定值。

公式(Nick Stott 原版):
  rng = H - L
  H4/L4 = C ± rng × 1.1 / 2    ← 突破位
  H3/L3 = C ± rng × 1.1 / 4    ← 反轉位
  H2/L2 = C ± rng × 1.1 / 6
  H1/L1 = C ± rng × 1.1 / 12   ← 最靠近昨收
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TypedDict

from services.cdp import round_to_tick_tw

logger = logging.getLogger(__name__)


class CamarillaLevels(TypedDict):
    h4: float
    h3: float
    h2: float
    h1: float
    l1: float
    l2: float
    l3: float
    l4: float
    as_of_date: str
    prev_close: float


def compute_camarilla(h: float, l: float, c: float) -> dict[str, float]:
    """純函式 — 從昨日 H/L/C 算 8 線,全部對齊台股 tick。

    每條線用自己的 price 決定 tick size（不統一用 close 的 tick），
    因此 H 側與 L 側可能落在不同 tick bracket。
    """
    rng = h - l
    raw = {
        "h4": c + rng * 1.1 / 2,
        "h3": c + rng * 1.1 / 4,
        "h2": c + rng * 1.1 / 6,
        "h1": c + rng * 1.1 / 12,
        "l1": c - rng * 1.1 / 12,
        "l2": c - rng * 1.1 / 6,
        "l3": c - rng * 1.1 / 4,
        "l4": c - rng * 1.1 / 2,
    }
    return {k: round_to_tick_tw(v, "nearest") for k, v in raw.items()}
