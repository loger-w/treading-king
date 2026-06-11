"""POST /api/preview — 設定當前預覽的 symbol 訂閱 (owner='preview')。

使用情境：前端 Monitor 頁使用者用搜尋預覽一檔不在 watchlist 的股票，
需要臨時訂閱 trades 才能在 IntradayChart / TradeTape 看到即時 tick。

行為：
  - body {"symbol": "X"}：若 X 跟 current 一樣 noop；否則 unsub current、sub X
  - body {"symbol": null}：unsub current (若有)，清空
  - 單一全域 owner='preview'：同時間只有一個 preview symbol（與此頁的 selected state 對應）

注意：preview owner 與 watchlist / active_signal owner 並存。fubon_ws.WSPool
使用 refcount 機制，preview 對 watchlist 內的股票呼叫 subscribe 也安全
（只是多加一個 owner，並不會重複真打富邦）。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.fubon_ws import get_ws_pool

logger = logging.getLogger(__name__)
router = APIRouter()

# Module-level state — 只記住「目前 preview 的是哪一檔」
_current_preview: str | None = None
_lock = asyncio.Lock()


class PreviewPayload(BaseModel):
    symbol: str | None = None


@router.post("/api/preview")
async def set_preview(payload: PreviewPayload) -> dict:
    global _current_preview
    new_sym = payload.symbol

    async with _lock:
        if new_sym == _current_preview:
            return {"ok": True, "current": _current_preview}

        pool = get_ws_pool()

        # 先 unsubscribe 上一個
        if _current_preview is not None:
            try:
                await pool.unsubscribe(_current_preview, owner_id="preview")
            except Exception as e:
                logger.warning("preview unsubscribe %s failed: %s", _current_preview, e)
            # 舊 owner 已退訂 — 立刻清空,讓下面 subscribe 失敗 raise 時狀態
            # = 「目前無 preview」與真實訂閱一致;否則殘留舊 symbol,之後重
            # POST 舊 symbol 會命中 noop 分支、實際已退訂卻收不到 tick
            _current_preview = None

        # 再 subscribe 新的（若非 null）
        if new_sym is not None:
            try:
                await pool.subscribe(new_sym, owner_id="preview")
            except RuntimeError as e:
                # capacity 滿等情況
                logger.error("preview subscribe %s failed: %s", new_sym, e)
                raise HTTPException(503, detail={"error": "subscribe_failed", "detail": str(e)})

        _current_preview = new_sym
        return {"ok": True, "current": _current_preview}
