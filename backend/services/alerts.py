"""System alert dispatch via Discord webhook.

Use for critical conditions only:
- WS 連續 5 次重連失敗（circuit open）
- evaluator_lag > 5s 連續 30s
- init_realtime 連 3 次失敗
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_URL: str | None = None


def _get_webhook_url() -> str | None:
    global _WEBHOOK_URL
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("ALERTS_DISCORD_WEBHOOK_URL", "").strip() or ""
    return _WEBHOOK_URL or None


async def notify_critical(message: str, **fields: Any) -> None:
    """Send a critical alert. Fail silently on webhook errors (only logs)."""
    logger.error("ALERT: %s | fields=%s", message, fields)

    webhook_url = _get_webhook_url()
    if not webhook_url:
        return

    embed = {
        "title": "🚨 treading-king alert",
        "description": message,
        "color": 0xFF3355,
        "fields": [
            {"name": k, "value": str(v)[:1024], "inline": True}
            for k, v in fields.items()
        ][:25],
    }
    payload = {"embeds": [embed]}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 429:
                # 最後一道告警通道撞 rate limit — 照 Retry-After 重試一次再放棄
                try:
                    delay = float(resp.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(min(delay, 5.0))
                resp = await client.post(webhook_url, json=payload)
            if resp.status_code >= 400:
                # httpx 不對 4xx/5xx 拋例外 — 不檢查的話 webhook 失效(401/404)
                # 或 embed 格式錯(400)完全無感
                logger.error(
                    "Discord alert webhook returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as e:
        logger.warning("Failed to send Discord alert: %s", e)
