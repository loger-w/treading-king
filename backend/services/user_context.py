"""USER_LABEL / CACHE_JOB_OWNER 讀取 + 驗證。

backend 啟動時 call get_user_label() 一次，驗證失敗直接 raise → uvicorn 不會起來。
所有 route / service 透過 get_user_label() 拿 label，避免散落 os.getenv 拼字錯誤。
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_LABEL_RE = re.compile(r"^[a-z0-9_-]{2,20}$")


@lru_cache(maxsize=1)
def get_user_label() -> str:
    raw = (os.getenv("USER_LABEL") or "").strip()
    if not _LABEL_RE.match(raw):
        raise RuntimeError(
            f"USER_LABEL invalid: {raw!r}. Must match [a-z0-9_-]{{2,20}}."
        )
    return raw


def is_cache_job_owner() -> bool:
    """OWNER 跟 USER_LABEL 相符才回 True。OWNER 未設或不符都回 False。"""
    owner = (os.getenv("CACHE_JOB_OWNER") or "").strip().lower()
    if not owner:
        return False
    return owner == get_user_label()
