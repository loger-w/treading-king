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
    config_io, ma, monitor_list as monitor_list_route, mxf,
    preview, quote, signals_history, symbols,
    watchlist, ws,
)  # noqa: E402
from routes.symbols import bootstrap_symbols_if_missing  # noqa: E402
from jobs.top_gainers_scheduler import top_gainers_loop  # noqa: E402
from services.fubon_client import get_fubon  # noqa: E402
from services.fubon_futures import resolve_active_symbol  # noqa: E402
from services.fubon_futures_ws import get_futures_ws_pool, session_reconcile_loop  # noqa: E402
from services.fubon_ws import get_ws_pool  # noqa: E402
from services.lifecycle_sync import resync_from_config  # noqa: E402
from services.local_store import get_local_store  # noqa: E402
from services.logging_config import configure_logging  # noqa: E402
from services.overnight import overnight_loop  # noqa: E402
from services.signal_engine import get_signal_engine  # noqa: E402
from services.signal_writer import get_signal_writer  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    fubon = get_fubon()
    await fubon.init()
    get_local_store().init()
    asyncio.create_task(bootstrap_symbols_if_missing())

    pool = get_ws_pool()
    await pool.start()

    writer = get_signal_writer()
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

    # 依 config 訂閱書籤/監聽清單並刷新訊號引擎
    # 系統書籤(大漲股)由 top_gainers_scheduler 在每次 refresh 時自行 sync 訂閱
    await resync_from_config()

    # 啟動 overnight 8:25 cron — 每個 instance 自己重 login + 重訂閱 ws
    overnight_task = asyncio.create_task(overnight_loop())
    logger.info("overnight loop started")

    # 大漲股排程 — 盤中每 1 分鐘更新 top_gainers 記憶體快取
    top_gainers_task = asyncio.create_task(top_gainers_loop())

    # 群益下單(可選,未設定或失敗都不影響富邦)
    try:
        from services.capital_factory import get_capital
        capital = get_capital()
        if capital is not None:
            capital.start(asyncio.get_running_loop())
            logger.info("Capital client started (env=%s)", os.getenv("CAPITAL_ENV", "test"))
    except Exception as e:  # noqa: BLE001
        logger.error("Capital startup skipped: %s", e)

    logger.info("Startup done — fubon=%s, ws_pool=%s",
                fubon.status.value, pool.status.value)
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
app.include_router(monitor_list_route.router)
app.include_router(signals_history.router)
app.include_router(candles.router)
app.include_router(cdp_route.router)
app.include_router(camarilla.router)
app.include_router(ma.router)
app.include_router(mxf.router)
app.include_router(config_io.router)
app.include_router(ws.router)
