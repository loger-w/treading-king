"""WS /ws/realtime — 前端訂閱即時訊號廣播。

X-API-Key 在 query string（標準 WS handshake 不能帶 header）。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/realtime")
async def realtime_ws(ws: WebSocket, api_key: str = Query("", alias="api_key")):
    expected = os.getenv("BFF_API_KEY", "").strip()
    if expected and api_key != expected:
        await ws.close(code=1008)  # policy violation
        return

    await ws.accept()
    bc = get_broadcaster()
    await bc.add(ws)
    try:
        while True:
            # 等 client 訊息（ping/keep-alive）；忽略內容
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws client error: %s", e)
    finally:
        await bc.remove(ws)
