"""Supabase client wrapper — graceful degradation when not configured."""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SupabaseStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class SupabaseService:
    def __init__(self) -> None:
        self._client: Any = None
        self._status: SupabaseStatus = SupabaseStatus.ERROR
        self._last_error: str | None = None

    @property
    def status(self) -> SupabaseStatus:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def client(self) -> Any:
        """Raw supabase Client. None when not configured."""
        return self._client

    def init(self) -> None:
        """Sync init — supabase create_client doesn't need to be in a thread."""
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_KEY", "").strip()

        if not url or not key:
            self._status = SupabaseStatus.ERROR
            self._last_error = "SUPABASE_URL or SUPABASE_KEY missing in .env"
            logger.warning("Supabase not configured (degraded mode)")
            return

        try:
            from supabase import create_client

            self._client = create_client(url, key)
            self._status = SupabaseStatus.OK
            self._last_error = None
            logger.info("Supabase client created (URL=%s...)", url[:30])
        except Exception as e:
            self._client = None
            self._status = SupabaseStatus.ERROR
            self._last_error = f"{type(e).__name__}: {e}"
            logger.error("Supabase init failed: %s", self._last_error)


_service: SupabaseService | None = None


def get_supabase() -> SupabaseService:
    global _service
    if _service is None:
        _service = SupabaseService()
    return _service
