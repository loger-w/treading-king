"""Discord notifier — 訊號觸發推送(跟 alerts.py 系統異常 webhook 分開)。"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_URL: str | None = None


def _get_webhook_url() -> str | None:
    global _WEBHOOK_URL
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("SIGNALS_DISCORD_WEBHOOK_URL", "").strip() or ""
    return _WEBHOOK_URL or None


async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
    """訊號觸發推 Discord;失敗 silent log(不影響主流程)。"""
    url = _get_webhook_url()
    if not url:
        return

    fields: list[dict[str, Any]] = [
        {"name": "代號", "value": symbol, "inline": True},
        {"name": "價格", "value": f"{price:.2f}", "inline": True},
        {"name": "量", "value": str(volume), "inline": True},
    ]
    if cdp_touch:
        fields.append({
            "name": "CDP",
            "value": f"{cdp_touch['level']} ({cdp_touch.get('role', 'touch')})",
            "inline": True,
        })
    if ma_touch:
        fields.append({
            "name": "MA",
            "value": f"{ma_touch['level']} ({ma_touch.get('role', 'touch')})",
            "inline": True,
        })

    embed = {
        "title": f"📈 {rule_name}",
        "description": f"`{symbol}` 觸發",
        "color": 0x32D27C,
        "fields": fields,
        "timestamp": triggered_at_iso,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"embeds": [embed]})
    except Exception as e:
        logger.warning("Discord signal notify failed: %s", e)
