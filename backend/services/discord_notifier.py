"""Discord notifier — 訊號觸發 → POST 給 bot 的 localhost 入口(bot 端渲三則圖卡)。

跟 alerts.py 的系統異常 webhook 是兩條獨立路徑。
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_PUSH_URL: str | None = None


def _get_push_url() -> str | None:
    global _PUSH_URL
    if _PUSH_URL is None:
        _PUSH_URL = os.getenv("SIGNALS_BOT_PUSH_URL", "").strip() or ""
    return _PUSH_URL or None


async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    name: str | None = None,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
    """訊號觸發 → POST 給 bot;URL 未設則 no-op。失敗 silent log,不影響主流程。"""
    url = _get_push_url()
    if not url:
        return
    payload = {
        "symbol": symbol,
        "name": name,
        "rule_name": rule_name,
        "price": price,
        "volume": volume,
        "triggered_at": triggered_at_iso,
        "cdp_touch": cdp_touch,
        "ma_touch": ma_touch,
    }
    try:
        # 3s timeout:bot 立刻回 202,渲圖走 bot 背景;localhost 連不上(bot 沒開)會很快失敗
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.warning("Discord signal push failed: %s", e)
