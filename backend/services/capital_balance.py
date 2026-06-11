"""OnRealBalanceReport(即時庫存)解析與收集。

⚠ 欄位 index 為「假設表」(參考官方範例慣例,未實測):
  [0]市場別 [1]帳號 [2]商品代號 [3]昨日餘額 [4]今日買進 [5]今日賣出 [6]現股餘額(股) [7]均價
首測流程:scripts/capital_smoke.py --balance 印原始字串 → 對照群益 App 持倉校準 index
→ 真實樣本(去敏)換進 test_capital_balance.py。解析失敗整筆略過 + log,
錯誤假設只會讓清單缺列,不會出垃圾。

事件節奏未知(可能每檔一事件、結尾 ## 標記)→ BalanceCollector 雙保險:
收到結束標記 flush,或 timeout 後由 COM 執行緒 poll() flush。
"""
from __future__ import annotations
import logging
import time
from typing import Callable

from services.capital_models import Position

logger = logging.getLogger(__name__)

_IDX_STOCK_NO = 2
_IDX_SHARES = 6
_IDX_AVG = 7
_MIN_FIELDS = 8


def parse_balance_line(raw: str) -> Position | None:
    """一筆事件字串 → Position;結束標記/欄位不足/數字壞/餘額 0 → None。"""
    if not raw or raw.startswith("#"):
        return None
    parts = raw.split(",")
    if len(parts) < _MIN_FIELDS:
        return None
    try:
        shares = int(float(parts[_IDX_SHARES]))
        avg = float(parts[_IDX_AVG])
    except ValueError:
        logger.warning("balance line 解析失敗(index 假設可能要校準): %r", raw)
        return None
    if shares == 0:
        return None
    stock_no = parts[_IDX_STOCK_NO].strip()
    if not stock_no:
        return None
    return Position(stock_no=stock_no, qty=shares // 1000, avg_price=avg)


class BalanceCollector:
    """收集一輪查詢的多筆事件,結束標記或 timeout 後一次 flush(全量替換語意)。
    只在 COM 執行緒上被呼叫(feed=事件、poll=幫浦圈、reset=發查詢前),無鎖。"""

    def __init__(self, on_complete: Callable[[list[Position]], None], timeout_s: float = 1.0) -> None:
        self._on_complete = on_complete
        self._timeout_s = timeout_s
        self._staging: list[Position] = []
        self._last_feed: float | None = None

    def reset(self) -> None:
        self._staging = []
        self._last_feed = None

    def feed(self, raw: str) -> None:
        if raw and raw.startswith("#"):     # 結束標記
            self._flush()
            return
        p = parse_balance_line(raw)
        self._last_feed = time.monotonic()
        if p is not None:
            self._staging.append(p)

    def poll(self, now_monotonic: float | None = None) -> None:
        """COM 幫浦圈呼叫:距最後一筆事件超過 timeout → flush(沒等到 ## 的保險)。"""
        if self._last_feed is None:
            return
        now = time.monotonic() if now_monotonic is None else now_monotonic
        if now - self._last_feed >= self._timeout_s:
            self._flush()

    def _flush(self) -> None:
        out, self._staging, self._last_feed = self._staging, [], None
        self._on_complete(out)
