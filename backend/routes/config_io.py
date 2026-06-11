"""GET /api/config/export、POST /api/config/import — 個人設定可攜檔。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from services.lifecycle_sync import current_owner_map, resync_from_config
from services.local_store import get_local_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/config/export")
async def export_config() -> dict:
    return get_local_store().config.export_config()


@router.post("/api/config/import")
async def import_config(payload: dict) -> dict:
    prev = current_owner_map()  # 匯入前的訂閱快照
    try:
        get_local_store().config.import_config(payload)
    except ValueError as e:
        raise HTTPException(400, detail={"error": "bad_schema", "detail": str(e)})
    try:
        await resync_from_config(prev_owners=prev)  # 熱套用:退訂舊→訂新→refresh
    except Exception as e:  # noqa: BLE001
        # 此時 config 已落盤(import_config 內含備份 + persist),回 500 會讓
        # client 誤判匯入失敗而重匯;回 200 並標明 resync 失敗 — 訂閱狀態
        # 不完整,重啟時 startup resync 會補齊
        logger.exception("config import: resync failed")
        return {"status": "imported", "resync": "failed", "detail": str(e)}
    return {"status": "ok"}
