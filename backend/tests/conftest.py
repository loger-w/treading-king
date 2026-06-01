"""Shared fixtures."""
import os
import sys
from pathlib import Path

# 讓 tests 可以 import backend/ 下的 module 不用裝成 package
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.local_store import reset_local_store

# 給 user_context.get_user_label() 一個合法值,避免測試時噴 RuntimeError
os.environ.setdefault("USER_LABEL", "test")


@pytest.fixture(autouse=True)
def local_store_tmp(tmp_path, monkeypatch):
    """每個測試指向獨立 tmp data dir,並重建已初始化的 LocalStore 單例。"""
    monkeypatch.setenv("TK_DATA_DIR", str(tmp_path))
    store = reset_local_store()
    store.init()
    return store
