"""FastAPI app entry — Phase 1.

Run:
    cd backend
    .venv\\Scripts\\Activate.ps1   # Windows
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env BEFORE importing services that read env at module load
load_dotenv(Path(__file__).resolve().parent / ".env")

from middleware.auth import APIKeyMiddleware  # noqa: E402
from routes import cache, health, quote, screen, strategies, symbols  # noqa: E402
from services.fubon_client import get_fubon  # noqa: E402
from services.logging_config import configure_logging  # noqa: E402
from services.supabase_client import get_supabase  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init fubon SDK + supabase. Shutdown: cleanup."""
    logger.info("=" * 60)
    logger.info("treading-king BFF starting up")
    logger.info("=" * 60)

    fubon = get_fubon()
    await fubon.init()

    supabase = get_supabase()
    supabase.init()

    logger.info("Startup complete — fubon=%s, supabase=%s",
                fubon.status.value, supabase.status.value)

    yield

    logger.info("Shutting down...")
    await fubon.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(
    title="treading-king BFF",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow Vite dev server (proxy is the primary path, but keep CORS
# open for direct access during dev with explicit origins)
allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
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
app.include_router(symbols.router)
app.include_router(cache.router)
app.include_router(screen.router)
app.include_router(strategies.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "treading-king",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
    }
