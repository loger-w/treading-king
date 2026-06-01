"""X-API-Key middleware.

⚠️ 這只是 obscurity 不是 auth。VITE_BFF_API_KEY 注入前端 build 後任何打開網站
的人從 DevTools 都能撈出。Production 部署前必須換成正式認證(JWT / OAuth)，或
最簡單的 basic auth password gate。

Dev 環境用 X-API-Key 擋零成本掃描者就好。
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# Endpoints that don't require X-API-Key (publicly accessible)
PUBLIC_PATHS = {
    "/docs",
    "/redoc",
    "/openapi.json",
}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],  # type: ignore[name-defined]
    ):
        # Allow public paths + WS upgrade (auth via query string handled per-route)
        if (
            request.url.path in PUBLIC_PATHS
            or request.url.path.startswith("/docs")
            or request.url.path.startswith("/openapi")
            or request.url.path == "/ws/signals"  # WS auth check in route
        ):
            return await call_next(request)

        expected = os.getenv("BFF_API_KEY", "").strip()
        if not expected:
            # No key configured = open dev mode. Log a warning but allow.
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Missing or invalid X-API-Key"},
            )

        return await call_next(request)


# Re-export for typing
from typing import Any  # noqa: E402
