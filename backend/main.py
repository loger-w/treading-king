"""FastAPI app entry."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent / ".env")

from middleware.auth import APIKeyMiddleware  # noqa: E402
from routes import (
    active_signals, bookmarks, camarilla, candles, cdp as cdp_route,
    ma, mxf,
    preview, quote, signals_history, symbols,
    watchlist, ws,
)  # noqa: E402
from jobs.top_gainers_scheduler import top_gainers_loop  # noqa: E402
from services.fubon_client import get_fubon  # noqa: E402
from services.fubon_futures import resolve_active_symbol  # noqa: E402
from services.fubon_futures_ws import get_futures_ws_pool, session_reconcile_loop  # noqa: E402
from services.fubon_ws import get_ws_pool  # noqa: E402
from services.logging_config import configure_logging  # noqa: E402
from services.overnight import overnight_loop  # noqa: E402
from services.signal_engine import get_signal_engine  # noqa: E402
from services.supabase_client import get_supabase  # noqa: E402
from services.supabase_writer import get_supabase_writer  # noqa: E402
from services.user_context import get_user_label  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    # Fail-fast: 壞 label 不讓 backend 起來
    label = get_user_label()
    logger.info("USER_LABEL=%s", label)

    fubon = get_fubon()
    await fubon.init()
    supabase = get_supabase()
    supabase.init()

    pool = get_ws_pool()
    await pool.start()

    writer = get_supabase_writer()
    await writer.start()

    engine = get_signal_engine()
    await engine.start()

    # MXF 期貨 WS 訂閱 — 取近月、啟動 session reconcile loop
    futures_reconcile_task: asyncio.Task | None = None
    try:
        mxf_symbol = await resolve_active_symbol()
        if mxf_symbol:
            await get_futures_ws_pool().start(mxf_symbol)
            logger.info("MXF futures WS started for symbol=%s", mxf_symbol)
        else:
            logger.warning("MXF active symbol unavailable at startup; will retry on reconcile")
    except Exception as e:
        logger.error("MXF futures WS startup failed: %s", e)
    futures_reconcile_task = asyncio.create_task(session_reconcile_loop())
    logger.info("MXF session reconcile loop started")

    # 訂閱 user 所有書籤內的 symbols(每個 group 一個 owner_id = "bookmark:{gid}")
    # 系統書籤(大漲股)由 top_gainers_scheduler 在每次 refresh 時自行 sync 訂閱
    if supabase.client is not None:
        try:
            groups_res = await asyncio.to_thread(
                lambda: supabase.client.table("bookmark_groups")
                .select("id")
                .eq("user_label", label)
                .eq("is_system", False)
                .execute()
            )
            for g in (groups_res.data or []):
                gid = g["id"]
                items_res = await asyncio.to_thread(
                    lambda group_id=gid: supabase.client.table("watchlist_items")
                    .select("symbol")
                    .eq("group_id", group_id)
                    .execute()
                )
                owner = f"bookmark:{gid}"
                for r in (items_res.data or []):
                    try:
                        await pool.subscribe(r["symbol"], owner_id=owner)
                    except RuntimeError as e:
                        logger.warning("startup ws sub %s failed: %s", r["symbol"], e)
        except Exception as e:
            logger.error("startup bookmarks sub failed: %s", e)

    # 啟動 overnight 8:25 cron — 每個 instance 自己重 login + 重訂閱 ws
    overnight_task = asyncio.create_task(overnight_loop())
    logger.info("overnight loop started")

    # 大漲股排程 — 盤中每 1 分鐘 refresh top_gainers_snapshot
    top_gainers_task = asyncio.create_task(top_gainers_loop())

    logger.info("Startup done — fubon=%s, supabase=%s, ws_pool=%s",
                fubon.status.value, supabase.status.value, pool.status.value)
    yield

    logger.info("Shutting down…")
    overnight_task.cancel()
    top_gainers_task.cancel()
    if futures_reconcile_task:
        futures_reconcile_task.cancel()
    await get_futures_ws_pool().stop()
    await engine.shutdown()
    await writer.shutdown()
    await pool.shutdown()
    await fubon.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(title="treading-king BFF", version="0.3.0", lifespan=lifespan)

allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
extra_origin = os.getenv("FRONTEND_ORIGIN", "").strip()
if extra_origin:
    allowed_origins.append(extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(APIKeyMiddleware)

app.include_router(quote.router)
app.include_router(preview.router)
app.include_router(symbols.router)
app.include_router(watchlist.router)
app.include_router(bookmarks.router)
app.include_router(active_signals.router)
app.include_router(signals_history.router)
app.include_router(candles.router)
app.include_router(cdp_route.router)
app.include_router(camarilla.router)
app.include_router(ma.router)
app.include_router(mxf.router)
app.include_router(ws.router)
