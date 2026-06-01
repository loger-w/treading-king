"""本機儲存 facade。

用法:
    store = get_local_store()
    store.init()
    store.config.list_groups()
    store.signals.append({...})
    store.market.search("23", 20)
"""
from __future__ import annotations

from services.local_store.config_store import ConfigStore
from services.local_store.market_cache import MarketCache
from services.local_store.paths import (
    config_path, daily_ohlc_path, signals_log_path, symbols_path,
)
from services.local_store.signals_log import SignalsLog


class LocalStore:
    def __init__(self) -> None:
        self.config = ConfigStore(config_path())
        self.signals = SignalsLog(signals_log_path())
        self.market = MarketCache(symbols_path(), daily_ohlc_path())

    def init(self) -> None:
        self.config.load()
        self.signals.load()
        self.market.load()


_store: LocalStore | None = None


def get_local_store() -> LocalStore:
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def reset_local_store() -> LocalStore:
    """測試用:依目前 TK_DATA_DIR 重建單例。"""
    global _store
    _store = LocalStore()
    return _store
