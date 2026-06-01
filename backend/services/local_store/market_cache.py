"""市場資料本機快取:symbols(寫檔)/ daily_ohlc(寫檔)/ top_gainers(記憶體)。

symbols 和 daily_ohlc 持久化到 JSON 檔案,因為這些資料來自公開來源 / Fubon,
重建有成本但不是即時資料,值得跨程序保存。

top_gainers 是純記憶體快取:每分鐘重算,重啟後立即重填,不需要持久化。
"""
from __future__ import annotations

from pathlib import Path

from services.local_store.paths import atomic_write_json, read_json


class MarketCache:
    def __init__(self, symbols_path: Path, daily_ohlc_path: Path) -> None:
        self._symbols_path = Path(symbols_path)
        self._daily_ohlc_path = Path(daily_ohlc_path)
        self._symbols: list[dict] = []
        self._symbol_set: set[str] = set()
        self._symbol_index: dict[str, dict] = {}   # symbol -> row(O(1) 查單筆 metadata)
        self._daily_ohlc: dict[str, dict] = {}   # symbol -> 最新一筆
        self._top_gainers: list[dict] = []

    def load(self) -> None:
        self._symbols = read_json(self._symbols_path, []) or []
        self._symbol_set = {s["symbol"] for s in self._symbols}
        self._symbol_index = {s["symbol"]: s for s in self._symbols}
        self._daily_ohlc = read_json(self._daily_ohlc_path, {}) or {}

    # ---- symbols ----

    def symbols_loaded(self) -> bool:
        return len(self._symbols) > 0

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self._symbol_set

    def get_symbol(self, symbol: str) -> dict | None:
        """精確查單筆 metadata;不存在回 None。給 route 補 item 的 name/market/is_etf。"""
        r = self._symbol_index.get(symbol)
        if r is None:
            return None
        return {"symbol": r["symbol"], "name": r["name"],
                "market": r["market"], "is_etf": r.get("is_etf", False)}

    def replace_symbols(self, rows: list[dict]) -> None:
        """全量取代 symbols 清單並持久化到檔案。"""
        self._symbols = rows
        self._symbol_set = {s["symbol"] for s in rows}
        self._symbol_index = {s["symbol"]: s for s in rows}
        atomic_write_json(self._symbols_path, rows)

    def search(self, search: str, limit: int) -> list[dict]:
        """對 symbol(前綴匹配)或 name(包含匹配)做過濾,只回傳 active 的。"""
        s = (search or "").strip()
        rows = [r for r in self._symbols if r.get("is_active", True)]
        if s:
            rows = [r for r in rows
                    if r["symbol"].startswith(s) or s in r.get("name", "")]
        rows.sort(key=lambda r: r["symbol"])
        return [{"symbol": r["symbol"], "name": r["name"],
                 "market": r["market"], "is_etf": r.get("is_etf", False)}
                for r in rows[:limit]]

    # ---- daily_ohlc ----

    def get_latest_daily_ohlc(self, symbol: str) -> dict | None:
        return self._daily_ohlc.get(symbol)

    def upsert_daily_ohlc(self, rows: list[dict]) -> None:
        """日 OHLC upsert:date 字串比大小,只保留最新一筆,有變動才寫檔。"""
        changed = False
        for r in rows:
            sym = r["symbol"]
            cur = self._daily_ohlc.get(sym)
            if cur is None or r["date"] >= cur["date"]:
                self._daily_ohlc[sym] = {
                    "date": r["date"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                }
                changed = True
        if changed:
            atomic_write_json(self._daily_ohlc_path, self._daily_ohlc)

    # ---- top_gainers(記憶體,每分鐘重算) ----

    def get_top_gainers(self) -> list[dict]:
        return list(self._top_gainers)

    def replace_top_gainers(self, rows: list[dict]) -> None:
        self._top_gainers = list(rows)

    def top_gainers_count(self) -> int:
        return len(self._top_gainers)
