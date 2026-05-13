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
    active_signals, cache, candles, cdp as cdp_route, health, me as me_route,
    preview, quote, screen, signals_history, strategies, symbols,
    watchlist, ws,
)  # noqa: E402
from services.fubon_client import get_fubon  # noqa: E402
from services.fubon_ws import get_ws_pool  # noqa: E402
from services.logging_config import configure_logging  # noqa: E402
from services.overnight import overnight_loop  # noqa: E402
from services.signal_engine import get_signal_engine  # noqa: E402
from services.supabase_client import get_supabase  # noqa: E402
from services.supabase_writer import get_supabase_writer  # noqa: E402
from services.user_context import get_user_label, is_cache_job_owner  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    # Fail-fast: 壞 label 不讓 backend 起來
    label = get_user_label()
    cache_owner = is_cache_job_owner()
    logger.info("USER_LABEL=%s, cache_job_owner=%s", label, cache_owner)

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

    # 訂閱 watchlist 內所有 symbols（用 watchlist owner）
    if supabase.client is not None:
        try:
            res = await asyncio.to_thread(
                lambda: supabase.client.table("watchlist")
                .select("symbol")
                .eq("user_label", label)
                .execute()
            )
            for r in (res.data or []):
                try:
                    await pool.subscribe(r["symbol"], owner_id="watchlist")
                except RuntimeError as e:
                    logger.warning("startup ws sub %s failed: %s", r["symbol"], e)
        except Exception as e:
            logger.error("startup watchlist sub failed: %s", e)

    # 啟動 overnight 8:25 cron — 只在 CACHE_JOB_OWNER == USER_LABEL 的 instance 跑
    if cache_owner:
        overnight_task = asyncio.create_task(overnight_loop())
        logger.info("overnight loop started (this instance is the cache owner)")
    else:
        overnight_task = None
        logger.info("cache job skipped (CACHE_JOB_OWNER != USER_LABEL=%s)", label)

    logger.info("Startup done — fubon=%s, supabase=%s, ws_pool=%s",
                fubon.status.value, supabase.status.value, pool.status.value)
    yield

    logger.info("Shutting down…")
    if overnight_task is not None:
        overnight_task.cancel()
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

app.include_router(health.router)
app.include_router(quote.router)
app.include_router(preview.router)
app.include_router(symbols.router)
app.include_router(cache.router)
app.include_router(screen.router)
app.include_router(strategies.router)
app.include_router(watchlist.router)
app.include_router(active_signals.router)
app.include_router(signals_history.router)
app.include_router(candles.router)
app.include_router(cdp_route.router)
app.include_router(ws.router)
app.include_router(me_route.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "treading-king",
        "version": "0.3.0",
        "docs": "/docs",
        "health": "/api/health",
    }
