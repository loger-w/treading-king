"""OnRealBalanceReport(即時庫存)解析與收集。

欄位定義 = 官方 4-2-c(策略王COM元件使用說明_V2.13.58.docx),
2026-06-11 以正式環境真實回報逐欄驗證(樣本見 test_capital_balance.py):
  [0]股票代號 [1]庫存種類(T:集保 C:融資 L:融券) [2][3]資額度(原始/可用)
  [4][5]券額度(原始/可用) [6]昨日庫存(股) [7]今日委買 [8]今日委賣
  [9]今日買進成交 [10]今日賣出成交 [11]今日資券可回補/集保可賣出
  [12]可資沖 [13]可券沖 [14]即時庫存 [15]忽略 [16]即時個股維持率
  [17]LOGIN_ID [18]帳號
⚠ 此報告**沒有均價欄**([16] 是維持率、會隨現價跳動,絕不可當價格)→
Position.avg_price 一律 None,均價待接損益試算 GetProfitLossGWReport。

事件節奏(實測):每檔一事件、結尾 ## 標記;BalanceCollector 雙保險:
收到結束標記 flush,或 timeout 後由 COM 執行緒 poll() flush。
"""
from __future__ import annotations
import logging
import time
from typing import Callable

from services.capital_models import Position

logger = logging.getLogger(__name__)

_IDX_STOCK_NO = 0
_IDX_KIND = 1
_IDX_SHARES = 14          # 即時庫存(股)
_MIN_FIELDS = 15
_KIND = {"T": "cash", "C": "margin", "L": "short"}


def parse_balance_line(raw: str) -> Position | None:
    """一筆事件字串 → Position;結束標記/欄位不足/數字壞/餘額 0/未知種類 → None。
    未知種類寧缺勿錯:平倉映射依 kind 送單,猜錯=送錯單種。"""
    if not raw or raw.startswith("#"):
        return None
    parts = raw.split(",")
    if len(parts) < _MIN_FIELDS:
        return None
    try:
        shares = int(float(parts[_IDX_SHARES]))
    except ValueError:
        logger.warning("balance line 解析失敗: %r", raw)
        return None
    if shares == 0:
        return None
    stock_no = parts[_IDX_STOCK_NO].strip()
    kind = _KIND.get(parts[_IDX_KIND].strip())
    if not stock_no or kind is None:
        if stock_no:
            logger.warning("balance line 未知庫存種類 %r: %r", parts[_IDX_KIND], raw)
        return None
    lots = shares // 1000
    if lots == 0:               # 零股不足 1 張:清單以張為單位,暫不顯示
        return None
    if kind == "short":
        lots = -lots            # 融券放空 → 負張數(close 映射靠 qty>0 判多空)
    return Position(stock_no=stock_no, qty=lots, kind=kind)


def dedupe_positions(positions: list[Position]) -> list[Position]:
    """同檔多種庫存列(集保+融資並存)→ 保留張數大者。store 以 stock_no 為鍵,
    被捨棄的部分平倉鍵不到 — 部位資料分種類建模前的過渡取捨,寧少不錯。"""
    seen: dict[str, Position] = {}
    for p in positions:
        q = seen.get(p.stock_no)
        if q is not None:
            logger.warning("同檔多種庫存列 %s: %s %d張 / %s %d張 — 保留張數大者",
                           p.stock_no, q.kind, q.qty, p.kind, p.qty)
            if abs(p.qty) <= abs(q.qty):
                continue
        seen[p.stock_no] = p
    return list(seen.values())


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
