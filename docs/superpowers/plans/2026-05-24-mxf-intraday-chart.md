# MXF 小台指即時分時走勢圖 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `MXFBacktest` 頁面加入一張 MXF 期貨即時分時走勢圖(日盤+夜盤連續、K線/走勢線可切換、VWAP/MA/今日高低/成交量子圖、自動追近月)。

**Architecture:** 後端新建 `services/fubon_futures.py`(symbol 解析、candles 合併、session 邏輯)+ `services/fubon_futures_ws.py`(期貨專用 WS,跟既有股票 WS 完全分離)+ `routes/mxf.py`。前端把 `IntradayChart` 內可共用的純函式/子元件抽到 `lib/chart-svg.tsx`(等值重構不改外觀),新建 `MXFIntradayChart` 跟 `useMXFCandles` 組合使用。WS 訊息用既有 broadcaster + 新 `event: "mxf_candle"`,前端在 `useSignalsStream` 加 dispatch、新 `subscribeMxfCandles` helper。

**Tech Stack:** Python / FastAPI / pytest / Fubon Neo SDK 2.2.8 (`marketdata.rest_client.futopt`, `marketdata.websocket_client.futopt`) / React 18 / TypeScript / Vitest / Tailwind.

**Spec:** [`docs/superpowers/specs/2026-05-24-mxf-intraday-chart-design.md`](../specs/2026-05-24-mxf-intraday-chart-design.md)

---

## File Structure

**Backend(新建)**:
- `backend/services/fubon_futures.py` — symbol 解析 + REST candles fetch + session 邏輯
- `backend/services/fubon_futures_ws.py` — 期貨 WS pool(訂閱、回呼、broadcast、斷線重連)
- `backend/routes/mxf.py` — `/api/mxf/symbol/active` + `/api/mxf/candles`
- `backend/tests/test_fubon_futures.py` — pure function unit tests
- `backend/tests/test_fubon_futures_ws.py` — WS pool 行為 unit tests

**Backend(修改)**:
- `backend/main.py` — 註冊 `routes/mxf.py` 的 router

**Frontend(新建)**:
- `frontend/src/lib/chart-svg.tsx` — 從 IntradayChart 抽出的純函式 + 子元件
- `frontend/src/lib/chart-svg.test.ts` — 純函式單元測試
- `frontend/src/components/MXFIntradayChart.tsx` — 期貨即時圖元件
- `frontend/src/hooks/useMXFCandles.ts` — REST + WS 拼接
- `docs/notes/mxf-fubon-api-observations.md` — 實測筆記(Task 16 寫)

**Frontend(修改)**:
- `frontend/src/components/IntradayChart.tsx` — 改用 chart-svg(等值重構)
- `frontend/src/lib/api.ts` — 加 `api.mxfSymbolActive()` + `api.mxfCandles()` + `MXFCandle`/`MXFCandlesResponse` 型別
- `frontend/src/hooks/useSignalsStream.ts` — 新增 `mxf_candle` event 分發 + `subscribeMxfCandles` 匯出
- `frontend/src/pages/MXFBacktest.tsx` — 整合 `<MXFIntradayChart />`

---

## Task 1: 後端 — `determine_current_session` 純函式

**Files:**
- Create: `backend/services/fubon_futures.py`
- Test:   `backend/tests/test_fubon_futures.py`

判斷某個 datetime 屬於哪個 session(`"day"` / `"night"` / `"closed"`)。期貨交易日 D = D 的前一天 15:00 → D 當天 13:45。週五 13:45 後到下週一 08:45 之間皆 `closed`(週五無夜盤)。

- [ ] **Step 1: 建檔案,寫失敗測試**

`backend/tests/test_fubon_futures.py`:
```python
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.fubon_futures import determine_current_session

TPE = ZoneInfo("Asia/Taipei")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


@pytest.mark.parametrize(
    "iso,expected",
    [
        # ---- day session(08:45–13:45)----
        ("2026-05-25T08:45:00", "day"),    # 開盤瞬間
        ("2026-05-25T10:30:00", "day"),    # 日盤中
        ("2026-05-25T13:44:59", "day"),    # 收盤前
        # ---- day → closed 邊界 ----
        ("2026-05-25T13:45:00", "closed"), # 收盤瞬間
        ("2026-05-25T14:30:00", "closed"), # 日盤後休市
        # ---- night session(15:00–翌日 05:00)----
        ("2026-05-25T15:00:00", "night"),  # 夜盤開
        ("2026-05-25T22:00:00", "night"),  # 夜盤中
        ("2026-05-26T01:30:00", "night"),  # 跨日夜盤
        ("2026-05-26T04:59:59", "night"),  # 夜盤收盤前
        # ---- night → closed 邊界 ----
        ("2026-05-26T05:00:00", "closed"), # 夜盤收盤
        ("2026-05-26T07:00:00", "closed"), # 開盤前休市
        # ---- 週五無夜盤(5/29 為週五)----
        ("2026-05-29T13:44:59", "day"),    # 週五日盤
        ("2026-05-29T13:45:00", "closed"), # 週五收盤後
        ("2026-05-29T15:00:00", "closed"), # 週五本應夜盤但無
        ("2026-05-29T22:00:00", "closed"), # 週五本應夜盤但無
        ("2026-05-30T01:00:00", "closed"), # 週六凌晨無夜盤
        # ---- 週六、週日全 closed ----
        ("2026-05-30T10:00:00", "closed"), # 週六
        ("2026-05-31T22:00:00", "closed"), # 週日(夜盤本身不開)
        # ---- 週一 08:45 開盤 ----
        ("2026-06-01T08:44:59", "closed"), # 週一開盤前
        ("2026-06-01T08:45:00", "day"),    # 週一開盤
    ],
)
def test_determine_current_session(iso: str, expected: str):
    assert determine_current_session(dt(iso)) == expected
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && pytest tests/test_fubon_futures.py -v`
Expected: `ImportError` 或 `AttributeError` — `services.fubon_futures` 還沒寫。

- [ ] **Step 3: 寫最小實作**

`backend/services/fubon_futures.py`:
```python
"""MXF 期貨服務 — symbol 解析、candles 合併、session 邏輯。"""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal

Session = Literal["day", "night", "closed"]

DAY_OPEN = time(8, 45)
DAY_CLOSE = time(13, 45)
NIGHT_OPEN = time(15, 0)
NIGHT_CLOSE = time(5, 0)


def determine_current_session(now: datetime) -> Session:
    """判斷 now(必須帶 tz)屬於哪個 session。

    交易日 D = D-1 15:00 → D 13:45。
    週五日盤後到下週一日盤開盤之間皆 closed(週五無夜盤)。
    """
    weekday = now.weekday()  # Mon=0 ... Sun=6
    t = now.time()

    # 週六(5)整天 closed
    # 週日(6)整天 closed(週日夜盤是「週一交易日」的夜盤,但實務上不開,所以仍 closed)
    if weekday == 5:
        return "closed"
    if weekday == 6:
        return "closed"

    # 週五日盤後到 23:59:59 = closed(週五無夜盤)
    if weekday == 4 and t >= DAY_CLOSE:
        return "closed"

    # 週一凌晨 00:00-05:00 屬於「週日夜盤」— 但週日不開夜盤,所以 closed
    if weekday == 0 and t < DAY_OPEN:
        return "closed"

    # day session
    if DAY_OPEN <= t < DAY_CLOSE:
        return "day"

    # night session
    # 跨日:當日 15:00 ≤ t 或 t < 05:00
    if t >= NIGHT_OPEN or t < NIGHT_CLOSE:
        return "night"

    # 其他(05:00-08:44, 13:45-14:59)= closed
    return "closed"
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd backend && pytest tests/test_fubon_futures.py -v`
Expected: 全部 20 個 case PASS。

如果某個 case 失敗,先在 Step 3 的判斷邏輯中修正(常見錯誤:`>` vs `>=` 邊界、跨日邏輯)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/fubon_futures.py backend/tests/test_fubon_futures.py
git commit -m "feat(mxf): add determine_current_session pure function

判斷某時刻屬於日盤 / 夜盤 / 休市。處理週五無夜盤、跨日邏輯。"
```

---

## Task 2: 後端 — `merge_candles` 合併日盤夜盤

**Files:**
- Modify: `backend/services/fubon_futures.py`
- Modify: `backend/tests/test_fubon_futures.py`

把日盤 + 夜盤兩段 candles 合併,夜盤在前、日盤在後,ts 去重(以後到的為準)。

- [ ] **Step 1: 加失敗測試**

在 `backend/tests/test_fubon_futures.py` 末尾追加:
```python
from services.fubon_futures import merge_candles, MXFCandleDict


def c(ts: str, **kw) -> MXFCandleDict:
    base: MXFCandleDict = {
        "date": ts, "open": 17000.0, "high": 17010.0, "low": 16990.0,
        "close": 17005.0, "volume": 100, "average": 17000.0,
    }
    base.update(kw)
    return base


def test_merge_candles_orders_night_first():
    # 夜盤 5/24 15:00、5/24 23:00、日盤 5/25 09:00、5/25 12:00
    night = [c("2026-05-24T23:00:00+08:00"), c("2026-05-24T15:00:00+08:00")]
    day = [c("2026-05-25T12:00:00+08:00"), c("2026-05-25T09:00:00+08:00")]
    out = merge_candles(day=day, night=night)
    assert [x["date"] for x in out] == [
        "2026-05-24T15:00:00+08:00",
        "2026-05-24T23:00:00+08:00",
        "2026-05-25T09:00:00+08:00",
        "2026-05-25T12:00:00+08:00",
    ]


def test_merge_candles_dedupes_by_date_keeping_last():
    # 同個 ts 出現在兩段(理論上不該,防呆) — 取後到的(close 不同)
    night = [c("2026-05-25T08:45:00+08:00", close=17000.0)]
    day = [c("2026-05-25T08:45:00+08:00", close=17050.0)]
    out = merge_candles(day=day, night=night)
    assert len(out) == 1
    assert out[0]["close"] == 17050.0  # day(後加)蓋掉 night


def test_merge_candles_empty_inputs():
    assert merge_candles(day=[], night=[]) == []
    assert merge_candles(day=[c("2026-05-25T09:00:00+08:00")], night=[]) == [
        c("2026-05-25T09:00:00+08:00")
    ]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && pytest tests/test_fubon_futures.py::test_merge_candles_orders_night_first -v`
Expected: `ImportError: cannot import name 'merge_candles'`.

- [ ] **Step 3: 寫實作**

在 `backend/services/fubon_futures.py` 加(放在 `determine_current_session` 上方):
```python
from typing import TypedDict


class MXFCandleDict(TypedDict):
    date: str       # ISO with tz offset, e.g. "2026-05-25T09:00:00+08:00"
    open: float
    high: float
    low: float
    close: float
    volume: int
    average: float  # 富邦回傳的 VWAP


def merge_candles(*, day: list[MXFCandleDict], night: list[MXFCandleDict]) -> list[MXFCandleDict]:
    """合併日盤 + 夜盤 candles,按 ts 排序(夜盤在前),同 ts 取後到的(day 蓋 night)。"""
    by_date: dict[str, MXFCandleDict] = {}
    for x in night:
        by_date[x["date"]] = x
    for x in day:
        by_date[x["date"]] = x  # day 蓋 night
    return sorted(by_date.values(), key=lambda c: c["date"])
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd backend && pytest tests/test_fubon_futures.py -v`
Expected: 23 個 case 全 PASS(20 個原本的 + 3 個新增)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/fubon_futures.py backend/tests/test_fubon_futures.py
git commit -m "feat(mxf): add merge_candles for day+night session merging

夜盤在前、日盤在後,以日期排序,同 ts 後到的蓋過舊的。"
```

---

## Task 3: 後端 — `resolve_active_symbol`(查近月 MXF)

**Files:**
- Modify: `backend/services/fubon_futures.py`
- Modify: `backend/tests/test_fubon_futures.py`

從富邦 `futopt.intraday.tickers` 拿到所有 MXF 系列商品,filter 近月。實際的富邦 SDK 呼叫包在「拉一次 raw products list,filter MXF + 排序」的純函式中,方便測試。

> **Fubon API 參考**(實作前 `WebFetch` 確認 schema):
> - <https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/products.txt>
> - <https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/tickers.txt>

- [ ] **Step 1: 加失敗測試(只測純函式 filter / sort,不呼叫真實 API)**

在 `backend/tests/test_fubon_futures.py` 末尾追加:
```python
from services.fubon_futures import filter_active_mxf_symbol, ProductRow


def test_filter_active_mxf_picks_nearest_unexpired():
    # 模擬富邦回傳的 products 清單(精簡欄位)
    today = "2026-05-24"
    products: list[ProductRow] = [
        {"symbol": "MXFD6", "expiry": "2026-04-15"},  # 已過期
        {"symbol": "MXFE6", "expiry": "2026-05-20"},  # 已過期(同月但結算已過)
        {"symbol": "MXFF6", "expiry": "2026-06-17"},  # 未過期、最近
        {"symbol": "MXFG6", "expiry": "2026-07-15"},  # 次月
        {"symbol": "MXFI6", "expiry": "2026-09-16"},  # 季月
        {"symbol": "TXFF6", "expiry": "2026-06-17"},  # 大台,要排除
        {"symbol": "MX1F6", "expiry": "2026-06-17"},  # 微小台,要排除
    ]
    assert filter_active_mxf_symbol(products, today=today) == "MXFF6"


def test_filter_active_mxf_returns_none_if_no_active():
    products: list[ProductRow] = [
        {"symbol": "MXFA5", "expiry": "2025-01-15"},  # 都過期
        {"symbol": "TXFF6", "expiry": "2026-06-17"},  # 大台
    ]
    assert filter_active_mxf_symbol(products, today="2026-05-24") is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && pytest tests/test_fubon_futures.py::test_filter_active_mxf_picks_nearest_unexpired -v`
Expected: `ImportError`.

- [ ] **Step 3: 寫實作**

在 `backend/services/fubon_futures.py` 加(放在頂部 import 區後):
```python
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class ProductRow(TypedDict):
    symbol: str
    expiry: str  # "YYYY-MM-DD"


# 精準匹配「MXF + 月碼一碼(A-L)+ 年碼一碼」, 排除 MX1, MXR 等其他系列
MXF_SYMBOL_RE = re.compile(r"^MXF[A-L][0-9]$")


def filter_active_mxf_symbol(products: list[ProductRow], *, today: str) -> Optional[str]:
    """從 products 清單挑出近月 MXF。

    規則:symbol 嚴格匹配 ^MXF[A-L][0-9]$、expiry > today、取最近一個。
    """
    candidates = [
        p for p in products
        if MXF_SYMBOL_RE.match(p["symbol"]) and p["expiry"] > today
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda p: p["expiry"])["symbol"]
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd backend && pytest tests/test_fubon_futures.py -v`
Expected: 25 個 case 全 PASS。

- [ ] **Step 5: 加 async wrapper 呼叫富邦(整合段,沒測試)**

繼續在 `backend/services/fubon_futures.py` 加:
```python
from services.fubon_client import get_fubon

_ACTIVE_SYMBOL_CACHE: dict[str, tuple[str, datetime]] = {}
_ACTIVE_SYMBOL_TTL = timedelta(hours=1)


async def resolve_active_symbol() -> Optional[str]:
    """查富邦近月 MXF symbol,1h cache。"""
    now = datetime.now()
    cached = _ACTIVE_SYMBOL_CACHE.get("mxf")
    if cached and (now - cached[1]) < _ACTIVE_SYMBOL_TTL:
        return cached[0]

    fubon = get_fubon()
    if fubon.sdk is None:
        logger.warning("Fubon SDK not initialized")
        return None

    try:
        # 期貨 tickers API。確切呼叫名請對照 docs/api/fubon-neo-llms.txt:
        #   futopt.intraday.tickers(type='FUTURE', exchange='TAIFEX')
        # 回傳結構可能是 {"data": [{"symbol": ..., "settlement_date": ...}, ...]} 或類似
        raw = await asyncio.to_thread(
            fubon.sdk.marketdata.rest_client.futopt.intraday.tickers,
            type="FUTURE",
            exchange="TAIFEX",
        )
    except Exception as e:
        logger.warning("resolve_active_symbol fubon call failed: %s", e)
        return None

    # 富邦回傳的結構欄位名稱以「settlement_date」、「symbol」為準(實裝時驗證)
    products: list[ProductRow] = []
    for row in raw.get("data", []):
        symbol = row.get("symbol")
        expiry = row.get("settlement_date") or row.get("expiry_date") or row.get("expiry")
        if symbol and expiry:
            products.append({"symbol": symbol, "expiry": expiry[:10]})

    today_str = now.strftime("%Y-%m-%d")
    sym = filter_active_mxf_symbol(products, today=today_str)
    if sym:
        _ACTIVE_SYMBOL_CACHE["mxf"] = (sym, now)
    return sym
```

- [ ] **Step 6: Commit**

```bash
git add backend/services/fubon_futures.py backend/tests/test_fubon_futures.py
git commit -m "feat(mxf): add resolve_active_symbol (near-month MXF)

純函式 filter 可單測,async wrapper 呼叫富邦 futopt.intraday.tickers,1h cache。"
```

---

## Task 4: 後端 — `fetch_candles`(REST 拉兩段並合併)

**Files:**
- Modify: `backend/services/fubon_futures.py`

包裝富邦 `futopt.intraday.candles` 兩次(日盤 + 夜盤),用 Task 2 的 `merge_candles` 合併。沒有純函式測試(完全是 IO);手動 smoke 驗證放在 Task 16。

> **Fubon API 參考**:
> <https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/candles.txt>

- [ ] **Step 1: 寫實作**

在 `backend/services/fubon_futures.py` 末尾追加:
```python
SUPPORTED_TIMEFRAMES = (1, 5, 10, 15, 30, 60)


async def fetch_candles(symbol: str, timeframe: int) -> list[MXFCandleDict]:
    """拉日盤 + 夜盤 candles 並合併,夜盤在前。

    參數:timeframe ∈ {1, 5, 10, 15, 30, 60}。
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")

    fubon = get_fubon()
    if fubon.sdk is None:
        return []

    tf_str = str(timeframe)

    async def _fetch(session: Optional[str]) -> list[MXFCandleDict]:
        kwargs = {"symbol": symbol, "timeframe": tf_str}
        if session:
            kwargs["session"] = session
        try:
            raw = await asyncio.to_thread(
                fubon.sdk.marketdata.rest_client.futopt.intraday.candles,
                **kwargs,
            )
        except Exception as e:
            logger.warning("fetch_candles session=%s failed: %s", session, e)
            return []
        # raw 預期結構:{"date": ..., "symbol": ..., "timeframe": ..., "data": [{...}]}
        return list(raw.get("data", []))

    day, night = await asyncio.gather(_fetch(None), _fetch("afterhours"))
    return merge_candles(day=day, night=night)
```

- [ ] **Step 2: Commit**

```bash
git add backend/services/fubon_futures.py
git commit -m "feat(mxf): add fetch_candles (REST day+afterhours merge)

平行打兩段 REST、用 merge_candles 合併。實際對接行為留待手動 smoke 驗證。"
```

---

## Task 5: 後端 — `fubon_futures_ws.py`(期貨 WS pool)

**Files:**
- Create: `backend/services/fubon_futures_ws.py`
- Create: `backend/tests/test_fubon_futures_ws.py`

期貨 WS 是富邦獨立的 endpoint(`marketdata.websocket_client.futopt`),跟股票 WS 完全分離。Pool 設計**精簡版**:單一連線、單一 symbol(MXF 近月)、session 切換時自動 re-subscribe。回呼進來後推 broadcaster。

> **Fubon API 參考**:
> - <https://www.fbs.com.tw/TradeAPI/docs/market-data-future/making-connection.txt>
> - <https://www.fbs.com.tw/TradeAPI/docs/market-data-future/websocket-api/market-data-channels/candles.txt>

- [ ] **Step 1: 寫純邏輯測試(target session 推導)**

`backend/tests/test_fubon_futures_ws.py`:
```python
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.fubon_futures_ws import target_after_hours_flag

TPE = ZoneInfo("Asia/Taipei")


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TPE)


@pytest.mark.parametrize(
    "iso,expected",
    [
        ("2026-05-25T10:00:00", False),  # day → afterHours=False
        ("2026-05-25T22:00:00", True),   # night → afterHours=True
        ("2026-05-26T03:00:00", True),   # 跨日夜盤
        ("2026-05-25T14:00:00", None),   # 休市 → 不訂閱
        ("2026-05-30T10:00:00", None),   # 週六 → 不訂閱
        ("2026-05-25T13:45:00", None),   # 收盤瞬間 → 不訂閱
    ],
)
def test_target_after_hours_flag(iso: str, expected):
    assert target_after_hours_flag(dt(iso)) == expected
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && pytest tests/test_fubon_futures_ws.py -v`
Expected: `ImportError`.

- [ ] **Step 3: 寫實作(完整檔)**

`backend/services/fubon_futures_ws.py`:
```python
"""期貨 WS 連線管理(MXF 近月)。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from services.fubon_client import get_fubon
from services.fubon_futures import determine_current_session
from ws_broadcaster import get_broadcaster

logger = logging.getLogger(__name__)

TPE = ZoneInfo("Asia/Taipei")
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30, 60]


def target_after_hours_flag(now: datetime) -> Optional[bool]:
    """根據 now 回傳訂閱所需的 afterHours 旗標,或 None 表示「目前休市、不訂閱」。"""
    sess = determine_current_session(now)
    if sess == "day":
        return False
    if sess == "night":
        return True
    return None


class FuturesWSPool:
    """單例:管理 MXF 近月的 WS 連線。

    - 訂閱 candles channel(timeframe 由富邦推、不參與)
    - Session 邊界(15:00 / 05:00 / 08:45 / 13:45)自動 re-subscribe
    - 收到推送 → broadcast 給前端
    - 斷線指數重試
    """

    def __init__(self) -> None:
        self._ws = None  # type: ignore[assignment]
        self._symbol: Optional[str] = None
        self._current_after_hours: Optional[bool] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reconnect_attempt = 0
        self._lock = asyncio.Lock()

    async def start(self, symbol: str) -> None:
        """啟動 / 切換到指定 symbol。"""
        async with self._lock:
            self._loop = asyncio.get_running_loop()
            if self._symbol != symbol:
                await self._teardown_ws()
                self._symbol = symbol
            await self._ensure_subscribed_for_now()

    async def stop(self) -> None:
        async with self._lock:
            await self._teardown_ws()
            self._symbol = None

    async def reconcile_session(self) -> None:
        """供 scheduler 在 session 邊界呼叫,確認訂閱對齊現況。"""
        async with self._lock:
            await self._ensure_subscribed_for_now()

    # ---------------- internals ----------------

    async def _ensure_subscribed_for_now(self) -> None:
        if self._symbol is None:
            return
        now = datetime.now(TPE)
        want = target_after_hours_flag(now)

        if want is None:
            # 休市 → 取消訂閱(若有)
            await self._teardown_ws()
            return

        if self._ws is not None and self._current_after_hours == want:
            return  # 已是正確訂閱,不動

        await self._teardown_ws()
        await self._subscribe(want)

    async def _subscribe(self, after_hours: bool) -> None:
        fubon = get_fubon()
        if fubon.sdk is None:
            logger.warning("futures_ws: fubon sdk not initialized")
            return
        try:
            ws = fubon.sdk.marketdata.websocket_client.futopt
            ws.on("message", self._on_message_raw)
            ws.on("disconnect", self._on_disconnect_raw)
            await asyncio.to_thread(ws.connect)
            await asyncio.to_thread(
                ws.subscribe,
                {"channel": "candles", "symbol": self._symbol, "afterHours": after_hours},
            )
            self._ws = ws
            self._current_after_hours = after_hours
            self._reconnect_attempt = 0
            logger.info("futures_ws subscribed symbol=%s afterHours=%s", self._symbol, after_hours)
        except Exception as e:
            logger.warning("futures_ws subscribe failed: %s", e)
            await self._schedule_reconnect()

    async def _teardown_ws(self) -> None:
        if self._ws is None:
            return
        try:
            await asyncio.to_thread(self._ws.disconnect)
        except Exception as e:
            logger.debug("futures_ws disconnect raised: %s", e)
        finally:
            self._ws = None
            self._current_after_hours = None

    # 富邦 SDK 是 sync callback。bridge 回 asyncio。
    def _on_message_raw(self, message) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(asyncio.create_task, self._handle_message(message))
        except RuntimeError:
            pass

    def _on_disconnect_raw(self, *_args) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(asyncio.create_task, self._handle_disconnect())
        except RuntimeError:
            pass

    async def _handle_message(self, raw) -> None:
        # raw 結構(預期):{"event": "data", "channel": "candles",
        #                  "data": {"symbol": "MXFF6", "date": "...", "open": ..., ...}}
        try:
            data = raw.get("data") if isinstance(raw, dict) else None
            if not data:
                return
            symbol = data.get("symbol")
            if symbol != self._symbol:
                return
            await get_broadcaster().broadcast({
                "event": "mxf_candle",
                "data": {
                    "symbol": symbol,
                    "candle": {
                        "date": data.get("date"),
                        "open": data.get("open"),
                        "high": data.get("high"),
                        "low": data.get("low"),
                        "close": data.get("close"),
                        "volume": data.get("volume", 0),
                        "average": data.get("average", 0),
                    },
                },
            })
        except Exception as e:
            logger.warning("futures_ws handle_message error: %s", e)

    async def _handle_disconnect(self) -> None:
        logger.info("futures_ws disconnected, scheduling reconnect")
        self._ws = None
        self._current_after_hours = None
        await self._schedule_reconnect()

    async def _schedule_reconnect(self) -> None:
        if self._symbol is None:
            return
        delay = RECONNECT_DELAYS[min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
        self._reconnect_attempt += 1
        await asyncio.sleep(delay)
        await self._ensure_subscribed_for_now()


_pool: Optional[FuturesWSPool] = None


def get_futures_ws_pool() -> FuturesWSPool:
    global _pool
    if _pool is None:
        _pool = FuturesWSPool()
    return _pool
```

- [ ] **Step 4: 跑測試確認 PASS(只有 target_after_hours_flag 被測)**

Run: `cd backend && pytest tests/test_fubon_futures_ws.py -v`
Expected: 6 個 case PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/fubon_futures_ws.py backend/tests/test_fubon_futures_ws.py
git commit -m "feat(mxf): add FuturesWSPool managing MXF realtime WS

獨立於股票 WS,訂閱 futopt.candles channel,session 邊界自動切。
推送透過 ws_broadcaster broadcast event='mxf_candle'。"
```

---

## Task 6: 後端 — session reconcile 排程器

**Files:**
- Modify: `backend/services/fubon_futures_ws.py`
- Modify: `backend/main.py`

每分鐘檢查一次當前 session,如果 session 邊界跨過去了 → 呼叫 `pool.reconcile_session()` 重新訂閱。最簡單做法是 startup 啟動一個 asyncio task loop sleep 60s。

- [ ] **Step 1: 在 fubon_futures_ws.py 加 scheduler**

繼續編輯 `backend/services/fubon_futures_ws.py`,在末尾追加:
```python
async def session_reconcile_loop() -> None:
    """每分鐘檢查 session,跨邊界時切訂閱。Startup 後 fire-and-forget 跑。"""
    pool = get_futures_ws_pool()
    while True:
        try:
            await pool.reconcile_session()
        except Exception as e:
            logger.warning("session_reconcile_loop error: %s", e)
        await asyncio.sleep(60)
```

- [ ] **Step 2: 在 main.py 註冊 startup hook**

開 `backend/main.py`,找到 startup section(讀現有 startup pattern,參考既有的 fubon_ws 啟動方式),加:
```python
# 在現有 startup 區段附近加入(沿用既有 lifecycle pattern):
from services.fubon_futures import resolve_active_symbol
from services.fubon_futures_ws import get_futures_ws_pool, session_reconcile_loop

# 在 lifespan 或 startup event 中(讀 main.py 確認):
async def _start_futures_ws():
    symbol = await resolve_active_symbol()
    if symbol:
        await get_futures_ws_pool().start(symbol)
    # 跑 reconcile loop 當作 background task
    asyncio.create_task(session_reconcile_loop())
```

> **實作備註**:`main.py` 的 lifecycle 結構不確定(`@app.on_event("startup")` 或 `lifespan` context manager),先讀現有 startup code,**沿用該模式加入**。本步驟不寫死。

- [ ] **Step 3: 手動 smoke**

啟動後端:
```bash
cd backend && uvicorn main:app --reload
```
Expected:啟動 log 看到 `futures_ws subscribed symbol=MXFF6 afterHours=...`(若 fubon 已登入且在交易時段)或 `target_after_hours_flag = None → 不訂閱`(休市)。

- [ ] **Step 4: Commit**

```bash
git add backend/services/fubon_futures_ws.py backend/main.py
git commit -m "feat(mxf): wire FuturesWSPool startup + 60s session reconcile loop"
```

---

## Task 7: 後端 — `routes/mxf.py` 兩個 REST endpoint

**Files:**
- Create: `backend/routes/mxf.py`
- Modify: `backend/main.py`

`/api/mxf/symbol/active` 跟 `/api/mxf/candles`,模式模仿 `routes/camarilla.py`。

- [ ] **Step 1: 寫 route 檔**

`backend/routes/mxf.py`:
```python
"""MXF 期貨即時行情 REST endpoints。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from services.fubon_futures import (
    SUPPORTED_TIMEFRAMES,
    determine_current_session,
    fetch_candles,
    resolve_active_symbol,
)

router = APIRouter()
TPE = ZoneInfo("Asia/Taipei")


@router.get("/api/mxf/symbol/active")
async def get_active_symbol() -> dict:
    symbol = await resolve_active_symbol()
    if not symbol:
        raise HTTPException(503, detail={"error": "mxf_symbol_unavailable"})
    return {"symbol": symbol}


@router.get("/api/mxf/candles")
async def get_mxf_candles(
    tf: int = Query(5, description="timeframe in minutes"),
    symbol: str | None = Query(None, description="若指定就用,否則自動取近月"),
) -> dict:
    if tf not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(400, detail={"error": "unsupported_timeframe", "supported": list(SUPPORTED_TIMEFRAMES)})

    use_symbol = symbol or await resolve_active_symbol()
    if not use_symbol:
        raise HTTPException(503, detail={"error": "mxf_symbol_unavailable"})

    candles = await fetch_candles(use_symbol, tf)
    return {
        "symbol": use_symbol,
        "timeframe": tf,
        "candles": candles,
        "current_session": determine_current_session(datetime.now(TPE)),
    }
```

- [ ] **Step 2: 註冊到 main.py**

開 `backend/main.py`,找到既有 `from routes import ...` / `app.include_router(...)` 區段,**加入兩行**(沿用現有 pattern):
```python
from routes import mxf as mxf_routes  # 加在既有 import 群
app.include_router(mxf_routes.router)  # 加在既有 include_router 群
```

- [ ] **Step 3: 手動 smoke**

啟動後端,瀏覽器或 curl:
```bash
curl -H "X-API-Key: <key>" http://localhost:8000/api/mxf/symbol/active
# 預期:{"symbol": "MXFF6"}(或當前近月)

curl -H "X-API-Key: <key>" "http://localhost:8000/api/mxf/candles?tf=5"
# 預期:{"symbol": "MXFF6", "timeframe": 5, "candles": [...], "current_session": "day"|"night"|"closed"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/routes/mxf.py backend/main.py
git commit -m "feat(mxf): add /api/mxf/symbol/active and /api/mxf/candles routes"
```

---

## Task 8: 前端 — 抽 `chart-svg.tsx` 純函式 + 單元測試

**Files:**
- Create: `frontend/src/lib/chart-svg.tsx`
- Create: `frontend/src/lib/chart-svg.test.ts`

從 `IntradayChart` 抽出可純函式測試的部分:`scaleX_compressed`(支援跨日盤+夜盤的 gap 壓縮)、`scaleY_clamped`、`computeVWAP`、`computeMA`。**這 task 只新建檔、不動 IntradayChart**,等 Task 10 才整合替換。

- [ ] **Step 1: 寫失敗測試**

`frontend/src/lib/chart-svg.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import {
  scaleX_compressed,
  scaleY_clamped,
  computeVWAP,
  computeMA,
} from "./chart-svg";

describe("scaleX_compressed (futures day+night)", () => {
  // 期貨交易日 = 前一天 15:00 → 當天 13:45
  // sessions: [{ start: "2026-05-24T15:00:00+08:00", end: "2026-05-25T05:00:00+08:00" },  // 夜盤 14h
  //           { start: "2026-05-25T08:45:00+08:00", end: "2026-05-25T13:45:00+08:00" }]   // 日盤 5h
  // gap 壓縮:gap (05:00→08:45 = 3h45m) 在視覺上佔極小寬度(用 sessions 總時長之 1%)
  const sessions = [
    { startIso: "2026-05-24T15:00:00+08:00", endIso: "2026-05-25T05:00:00+08:00" },
    { startIso: "2026-05-25T08:45:00+08:00", endIso: "2026-05-25T13:45:00+08:00" },
  ];
  const width = 1900;  // 1900px:夜盤 1400 + gap ~10 + 日盤 ~490 約略對齊

  it("夜盤開始時 = 0", () => {
    expect(scaleX_compressed("2026-05-24T15:00:00+08:00", sessions, width)).toBeCloseTo(0, 0);
  });

  it("日盤結束時 = width", () => {
    expect(scaleX_compressed("2026-05-25T13:45:00+08:00", sessions, width)).toBeCloseTo(width, 0);
  });

  it("夜盤中間點", () => {
    // 夜盤 14h 一半 = 7h,夜盤結束於 1400px 左右(width × 14/(14+5+gap_small))
    const x = scaleX_compressed("2026-05-24T22:00:00+08:00", sessions, width);
    expect(x).toBeGreaterThan(600);
    expect(x).toBeLessThan(800);
  });

  it("休市時段內(gap):落在 sessions 之間,回 NaN", () => {
    expect(Number.isNaN(scaleX_compressed("2026-05-25T07:00:00+08:00", sessions, width))).toBe(true);
  });
});

describe("scaleY_clamped", () => {
  it("min 對應 height", () => {
    expect(scaleY_clamped(17000, 17000, 17100, 400)).toBeCloseTo(400);
  });
  it("max 對應 0", () => {
    expect(scaleY_clamped(17100, 17000, 17100, 400)).toBeCloseTo(0);
  });
  it("中間值線性內插", () => {
    expect(scaleY_clamped(17050, 17000, 17100, 400)).toBeCloseTo(200);
  });
});

describe("computeVWAP", () => {
  it("空陣列回空", () => {
    expect(computeVWAP([])).toEqual([]);
  });
  it("累積 VWAP 計算正確", () => {
    const candles = [
      { close: 100, volume: 10, average: 100, date: "", open: 0, high: 0, low: 0 },
      { close: 110, volume: 10, average: 0, date: "", open: 0, high: 0, low: 0 },
      { close: 120, volume: 10, average: 0, date: "", open: 0, high: 0, low: 0 },
    ];
    // 第一根 = 100, 第二根 = (100*10+110*10)/20 = 105, 第三根 = (100+110+120)*10/30 = 110
    const v = computeVWAP(candles);
    expect(v[0]).toBeCloseTo(100);
    expect(v[1]).toBeCloseTo(105);
    expect(v[2]).toBeCloseTo(110);
  });
});

describe("computeMA", () => {
  it("不夠期數的位置為 NaN", () => {
    const closes = [100, 102, 101];
    const ma = computeMA(closes, 5);
    expect(ma.every(Number.isNaN)).toBe(true);
  });
  it("MA-3 計算正確", () => {
    const closes = [100, 110, 120, 130, 140];
    const ma = computeMA(closes, 3);
    expect(Number.isNaN(ma[0])).toBe(true);
    expect(Number.isNaN(ma[1])).toBe(true);
    expect(ma[2]).toBeCloseTo(110);
    expect(ma[3]).toBeCloseTo(120);
    expect(ma[4]).toBeCloseTo(130);
  });
});
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/chart-svg.test.ts`
Expected: 全部失敗(import error)。

- [ ] **Step 3: 寫實作**

`frontend/src/lib/chart-svg.tsx`:
```tsx
/**
 * Chart 繪圖工具 — 純函式部分。
 *
 * 跨多 session 的 X 軸壓縮:給定 sessions(每段有 startIso/endIso)、
 * scaleX_compressed 把 ISO 時間映射到 [0, width],session 之間的 gap 視覺上佔 1%。
 */

export interface ChartSession {
  startIso: string;  // ISO 8601 with tz offset
  endIso: string;
}

interface Span {
  start: number;  // epoch ms
  end: number;
  pxStart: number;
  pxEnd: number;
}

const GAP_RATIO = 0.01;  // 每個 gap 佔總寬 1%(雙線視覺夠用)

function buildSpans(sessions: ChartSession[], width: number): Span[] {
  if (sessions.length === 0) return [];
  const durations = sessions.map((s) => new Date(s.endIso).getTime() - new Date(s.startIso).getTime());
  const totalSessionDuration = durations.reduce((a, b) => a + b, 0);
  const gapCount = sessions.length - 1;
  // gap 總寬 = width × (gapCount × GAP_RATIO);剩下分給 sessions(按 duration 比例)
  const totalGapPx = width * gapCount * GAP_RATIO;
  const sessionTotalPx = width - totalGapPx;

  const spans: Span[] = [];
  let cursorPx = 0;
  for (let i = 0; i < sessions.length; i++) {
    const sStart = new Date(sessions[i].startIso).getTime();
    const sEnd = new Date(sessions[i].endIso).getTime();
    const widthPx = (durations[i] / totalSessionDuration) * sessionTotalPx;
    spans.push({ start: sStart, end: sEnd, pxStart: cursorPx, pxEnd: cursorPx + widthPx });
    cursorPx += widthPx;
    if (i < sessions.length - 1) cursorPx += width * GAP_RATIO;
  }
  return spans;
}

export function scaleX_compressed(iso: string, sessions: ChartSession[], width: number): number {
  const spans = buildSpans(sessions, width);
  const t = new Date(iso).getTime();
  for (const span of spans) {
    if (t >= span.start && t <= span.end) {
      const ratio = (t - span.start) / (span.end - span.start);
      return span.pxStart + ratio * (span.pxEnd - span.pxStart);
    }
  }
  return NaN;
}

/** 算出每個 session 邊界的 px 位置(gap 在哪) — 給虛線分隔用 */
export function sessionBoundaries(sessions: ChartSession[], width: number): { gapStartPx: number; gapEndPx: number }[] {
  const spans = buildSpans(sessions, width);
  return spans.slice(0, -1).map((span, i) => ({
    gapStartPx: span.pxEnd,
    gapEndPx: spans[i + 1].pxStart,
  }));
}

export function scaleY_clamped(value: number, yMin: number, yMax: number, height: number): number {
  if (yMax === yMin) return height / 2;
  return height - ((value - yMin) / (yMax - yMin)) * height;
}

interface VWAPInputCandle {
  close: number;
  volume: number;
}

export function computeVWAP(candles: VWAPInputCandle[]): number[] {
  const out: number[] = [];
  let sumPV = 0;
  let sumV = 0;
  for (const c of candles) {
    sumPV += c.close * c.volume;
    sumV += c.volume;
    out.push(sumV > 0 ? sumPV / sumV : c.close);
  }
  return out;
}

export function computeMA(closes: number[], period: number): number[] {
  const out: number[] = new Array(closes.length).fill(NaN);
  if (period <= 0 || closes.length < period) return out;
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= period) sum -= closes[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}
```

- [ ] **Step 4: 跑測試確認 PASS**

Run: `cd frontend && npx vitest run src/lib/chart-svg.test.ts`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/chart-svg.tsx frontend/src/lib/chart-svg.test.ts
git commit -m "feat(chart-svg): extract pure functions (scale, VWAP, MA)

新增 lib/chart-svg.tsx 收容跨 chart 的純函式 — 跨多 session 的 X 軸
壓縮、Y 軸 clamp、累積 VWAP、簡單 MA。配套 vitest 單元測試。"
```

---

## Task 9: 前端 — 抽 `chart-svg.tsx` 子元件

**Files:**
- Modify: `frontend/src/lib/chart-svg.tsx`

加四個 stateless SVG 子元件:`<CandlestickSeries />`、`<LineSeries />`、`<VolumeSubChart />`、`<HoverCrosshair />`。設計成「資料 + scaler 進來、SVG 出來」,不擁有任何 state。

- [ ] **Step 1: 加 component code**

在 `frontend/src/lib/chart-svg.tsx` 末尾追加:
```tsx
// ============================================================
// SVG 子元件 — 都是 stateless,接 props 後輸出 <g>
// ============================================================

export interface OHLCCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average?: number;
}

interface ScaleProps {
  scaleX: (iso: string) => number;
  scaleY: (v: number) => number;
}

interface CandlestickProps extends ScaleProps {
  candles: OHLCCandle[];
  width: number;
  bullColor?: string;
  bearColor?: string;
}

export function CandlestickSeries({
  candles,
  scaleX,
  scaleY,
  width,
  bullColor = "#d9534f",
  bearColor = "#2e7d32",
}: CandlestickProps) {
  if (candles.length < 2) return null;
  // 估每根 K 棒寬:取相鄰兩根 X 距離的 60%
  const x0 = scaleX(candles[0].date);
  const x1 = scaleX(candles[1].date);
  const barW = Math.max(1, Math.abs(x1 - x0) * 0.6);
  return (
    <g>
      {candles.map((c) => {
        const cx = scaleX(c.date);
        if (Number.isNaN(cx)) return null;
        const yOpen = scaleY(c.open);
        const yClose = scaleY(c.close);
        const yHigh = scaleY(c.high);
        const yLow = scaleY(c.low);
        const up = c.close >= c.open;
        const color = up ? bullColor : bearColor;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(1, Math.abs(yClose - yOpen));
        return (
          <g key={c.date}>
            <line x1={cx} x2={cx} y1={yHigh} y2={yLow} stroke={color} strokeWidth={1} />
            <rect x={cx - barW / 2} y={bodyTop} width={barW} height={bodyH} fill={color} />
          </g>
        );
      })}
    </g>
  );
}

interface LineSeriesProps extends ScaleProps {
  candles: OHLCCandle[];
  field: "close" | "average";
  stroke?: string;
  strokeWidth?: number;
  dashed?: boolean;
}

export function LineSeries({ candles, scaleX, scaleY, field, stroke = "#d9534f", strokeWidth = 1.5, dashed = false }: LineSeriesProps) {
  const points = candles
    .map((c) => {
      const x = scaleX(c.date);
      if (Number.isNaN(x)) return null;
      const v = field === "close" ? c.close : c.average ?? c.close;
      return `${x},${scaleY(v)}`;
    })
    .filter((p): p is string => p !== null)
    .join(" ");
  return (
    <polyline
      fill="none"
      stroke={stroke}
      strokeWidth={strokeWidth}
      strokeDasharray={dashed ? "3 3" : undefined}
      points={points}
    />
  );
}

interface MALineProps extends ScaleProps {
  candles: OHLCCandle[];
  maValues: number[];  // 跟 candles 同長度,NaN 表示該根沒值
  stroke: string;
  label?: string;
}

export function MALine({ candles, maValues, scaleX, scaleY, stroke, label }: MALineProps) {
  const points: string[] = [];
  let lastX = 0;
  let lastY = 0;
  for (let i = 0; i < candles.length; i++) {
    if (Number.isNaN(maValues[i])) continue;
    const x = scaleX(candles[i].date);
    if (Number.isNaN(x)) continue;
    const y = scaleY(maValues[i]);
    points.push(`${x},${y}`);
    lastX = x;
    lastY = y;
  }
  if (points.length === 0) return null;
  return (
    <g>
      <polyline fill="none" stroke={stroke} strokeWidth={1.2} points={points.join(" ")} />
      {label && <text x={lastX + 4} y={lastY + 3} fontSize={10} fill={stroke}>{label}</text>}
    </g>
  );
}

interface VolumeSubChartProps {
  candles: OHLCCandle[];
  scaleX: (iso: string) => number;
  yTop: number;       // sub-chart 頂端 y
  height: number;     // sub-chart 高
  barWidth: number;
  bullColor?: string;
  bearColor?: string;
}

export function VolumeSubChart({
  candles,
  scaleX,
  yTop,
  height,
  barWidth,
  bullColor = "#d9534f",
  bearColor = "#2e7d32",
}: VolumeSubChartProps) {
  if (candles.length === 0) return null;
  const maxVol = Math.max(...candles.map((c) => c.volume), 1);
  return (
    <g>
      {candles.map((c) => {
        const cx = scaleX(c.date);
        if (Number.isNaN(cx)) return null;
        const h = (c.volume / maxVol) * height;
        const up = c.close >= c.open;
        return (
          <rect
            key={c.date}
            x={cx - barWidth / 2}
            y={yTop + (height - h)}
            width={barWidth}
            height={h}
            fill={up ? bullColor : bearColor}
            opacity={0.7}
          />
        );
      })}
    </g>
  );
}

interface HoverCrosshairProps {
  x: number;
  y: number;
  height: number;
  width: number;
  label?: string;
}

export function HoverCrosshair({ x, y, height, width, label }: HoverCrosshairProps) {
  return (
    <g pointerEvents="none">
      <line x1={x} x2={x} y1={0} y2={height} stroke="#999" strokeDasharray="2 2" strokeWidth={1} />
      <line x1={0} x2={width} y1={y} y2={y} stroke="#999" strokeDasharray="2 2" strokeWidth={1} />
      {label && (
        <g>
          <rect x={x + 4} y={y - 18} width={120} height={16} fill="white" stroke="#999" />
          <text x={x + 8} y={y - 6} fontSize={11} fill="#333">{label}</text>
        </g>
      )}
    </g>
  );
}
```

- [ ] **Step 2: Smoke test — 元件 lint / 編譯**

Run: `cd frontend && npx tsc --noEmit`
Expected: 無 type error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/chart-svg.tsx
git commit -m "feat(chart-svg): add stateless SVG sub-components

CandlestickSeries / LineSeries / MALine / VolumeSubChart / HoverCrosshair。
全部 stateless,僅吃 data + scaler。"
```

---

## Task 10: 前端 — `IntradayChart` 等值重構(改用 chart-svg)

**Files:**
- Modify: `frontend/src/components/IntradayChart.tsx`

把 IntradayChart 內部「走勢線、VWAP、MA、量子圖、hover crosshair」改用 `chart-svg.tsx` 的子元件;純函式 `computeVWAP`/`computeMA` 改 import。**外觀行為不變,Monitor 頁要 smoke 驗證**。

⚠️ 重要:IntradayChart 用「**台股 9:00-13:30 連續單一 session**」的 X 軸,不適用 `scaleX_compressed`(那是給多 session 用的)。所以 X 軸計算保持原樣,只抽指標 / K 線 / 量子圖 / crosshair。

- [ ] **Step 1: 在 IntradayChart 替換 VWAP 計算**

讀 `frontend/src/components/IntradayChart.tsx` 找到原本的「polyVwap」、「polyClose」、「volume bar」、「hover crosshair」、「MA line」相關 JSX,把它們**逐個** 換成 `chart-svg` 元件;原本 useMemo 中的 polyline points 字串可以移除,直接傳 `candles` 給子元件。

由於 IntradayChart 較複雜(656 行)、重構需逐 section 操作,建議**在此 task 內分多 commit**:
1. 把 VWAP polyline 換成 `<LineSeries field="average" dashed />`,跑 Monitor 頁手動 smoke,commit
2. 把 close polyline 換成 `<LineSeries field="close" />`,smoke,commit
3. 把 volume bars 換成 `<VolumeSubChart />`,smoke,commit
4. 把 MA 線換成 `<MALine />`,smoke,commit
5. 把 hover crosshair 換成 `<HoverCrosshair />`,smoke,commit

每 commit 前都跑 `npm run dev` 打開 Monitor 頁、選一檔股票、確認:
- 走勢線位置 / 顏色不變
- VWAP 虛線位置不變
- MA 線、CDP、Camarilla(若有 toggle 開)位置不變
- 成交量 bar 位置不變
- hover 十字線跟價格 label 不變

- [ ] **Step 2: 把 computeMA / computeVWAP 重複實作刪掉、改 import**

IntradayChart 原本如果有自己算 VWAP 或 MA 的程式碼,刪掉、改 import `chart-svg`(若 average 已從富邦 API 直接給,VWAP 計算可能不需要;以實際狀況為準)。

- [ ] **Step 3: Commit(最後一次)**

```bash
git add frontend/src/components/IntradayChart.tsx
git commit -m "refactor(IntradayChart): use chart-svg sub-components

等值重構,外觀行為不變。從重複的 SVG 繪圖 inline JSX 改用
chart-svg.tsx 提供的 stateless 子元件 — 為 MXFIntradayChart 鋪路。"
```

---

## Task 11: 前端 — `api.ts` 加 MXF endpoints

**Files:**
- Modify: `frontend/src/lib/api.ts`

加 MXFCandle 型別、`api.mxfSymbolActive()`、`api.mxfCandles()`。

- [ ] **Step 1: 加型別 + fetcher**

在 `frontend/src/lib/api.ts` 適當位置(沿用現有 interface / api 物件 pattern):
```typescript
export interface MXFCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  average: number;
}

export interface MXFActiveSymbolResponse {
  symbol: string;
}

export type CurrentSession = "day" | "night" | "closed";

export interface MXFCandlesResponse {
  symbol: string;
  timeframe: number;
  candles: MXFCandle[];
  current_session: CurrentSession;
}
```

然後在 `api` 物件加:
```typescript
  mxfSymbolActive: () =>
    fetchJSON<MXFActiveSymbolResponse>("/api/mxf/symbol/active"),
  mxfCandles: (tf: number, symbol?: string) =>
    fetchJSON<MXFCandlesResponse>(
      `/api/mxf/candles?tf=${tf}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`,
    ),
```

- [ ] **Step 2: Smoke**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type error.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(api): add api.mxfSymbolActive and api.mxfCandles"
```

---

## Task 12: 前端 — `useSignalsStream` 增加 `mxf_candle` event 分發

**Files:**
- Modify: `frontend/src/hooks/useSignalsStream.ts`

跟現有 `tickBus` 一樣的 pattern,加 `mxfCandleBus` + `subscribeMxfCandles` helper。

- [ ] **Step 1: 加 bus 跟 helper**

在 `frontend/src/hooks/useSignalsStream.ts` 適當位置(跟 `tickBus` 同 module-level):
```typescript
import type { MXFCandle } from "../lib/api";

export interface MXFCandleEvent {
  symbol: string;
  candle: MXFCandle;
}

const mxfCandleBus = new EventTarget();

export function subscribeMxfCandles(handler: (e: MXFCandleEvent) => void): () => void {
  const fn = (ev: Event) => handler((ev as CustomEvent<MXFCandleEvent>).detail);
  mxfCandleBus.addEventListener("mxf_candle", fn);
  return () => mxfCandleBus.removeEventListener("mxf_candle", fn);
}
```

在 `ws.onmessage` 處理區(現有的 `if (msg.event === "signal") ... else if (msg.event === "tick") ...`)加一段:
```typescript
        } else if (msg.event === "mxf_candle") {
          const evt: MXFCandleEvent = { symbol: msg.data.symbol, candle: msg.data.candle };
          mxfCandleBus.dispatchEvent(new CustomEvent<MXFCandleEvent>("mxf_candle", { detail: evt }));
        }
```

- [ ] **Step 2: Smoke**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type error.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSignalsStream.ts
git commit -m "feat(ws): dispatch mxf_candle events and expose subscribeMxfCandles"
```

---

## Task 13: 前端 — `useMXFCandles` hook

**Files:**
- Create: `frontend/src/hooks/useMXFCandles.ts`

開頁面時拿 active symbol + candles,訂閱 WS 把 push 合進 candles。切 timeframe 時重抓 REST。

- [ ] **Step 1: 寫 hook**

`frontend/src/hooks/useMXFCandles.ts`:
```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type CurrentSession, type MXFCandle } from "../lib/api";
import { subscribeMxfCandles } from "./useSignalsStream";

const REFRESH_MS = 30_000;

export interface UseMXFCandlesState {
  symbol: string | null;
  candles: MXFCandle[];
  currentSession: CurrentSession | null;
  loading: boolean;
  error: string | null;
}

export function useMXFCandles(timeframe: number) {
  const [state, setState] = useState<UseMXFCandlesState>({
    symbol: null,
    candles: [],
    currentSession: null,
    loading: true,
    error: null,
  });
  const symbolRef = useRef<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchCandles = useCallback(async (sym: string | null) => {
    try {
      const r = await api.mxfCandles(timeframe, sym ?? undefined);
      symbolRef.current = r.symbol;
      setState((prev) => ({
        ...prev,
        symbol: r.symbol,
        candles: r.candles,
        currentSession: r.current_session,
        loading: false,
        error: null,
      }));
    } catch (e) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  }, [timeframe]);

  // 初始化 + timeframe 變動 → 重新拉
  useEffect(() => {
    setState((prev) => ({ ...prev, loading: true, candles: [] }));
    fetchCandles(symbolRef.current);
    pollTimer.current = setInterval(() => fetchCandles(symbolRef.current), REFRESH_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [fetchCandles]);

  // WS push → 合進 candles
  useEffect(() => {
    const unsub = subscribeMxfCandles(({ symbol, candle }) => {
      if (symbol !== symbolRef.current) return;
      setState((prev) => {
        const arr = prev.candles;
        if (arr.length === 0) return { ...prev, candles: [candle] };
        const last = arr[arr.length - 1];
        if (last.date === candle.date) {
          return { ...prev, candles: [...arr.slice(0, -1), candle] };
        }
        if (candle.date > last.date) {
          return { ...prev, candles: [...arr, candle] };
        }
        // candle.date < last.date → 丟棄
        return prev;
      });
    });
    return unsub;
  }, []);

  return state;
}
```

- [ ] **Step 2: Smoke**

Run: `cd frontend && npx tsc --noEmit`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useMXFCandles.ts
git commit -m "feat(mxf): useMXFCandles hook (REST + WS merging)"
```

---

## Task 14: 前端 — `MXFIntradayChart` 元件

**Files:**
- Create: `frontend/src/components/MXFIntradayChart.tsx`

組合 `chart-svg.tsx` 的元件,加 toggle UI(形態、週期、指標)。X 軸用 `scaleX_compressed` + `sessionBoundaries`(虛線分隔)。

- [ ] **Step 1: 寫元件**

`frontend/src/components/MXFIntradayChart.tsx`:
```tsx
import { useMemo, useState } from "react";
import { useMXFCandles } from "../hooks/useMXFCandles";
import {
  scaleX_compressed,
  scaleY_clamped,
  sessionBoundaries,
  computeMA,
  CandlestickSeries,
  LineSeries,
  MALine,
  VolumeSubChart,
  type ChartSession,
} from "../lib/chart-svg";

const TIMEFRAMES = [1, 5, 10, 15, 30, 60];
const CHART_W = 1000;
const CHART_H = 460;
const VOL_H = 80;
const PAD_L = 56;
const PAD_R = 56;
const PAD_T = 12;
const PAD_B = 28;

type ChartMode = "candle" | "line";

export function MXFIntradayChart() {
  const [tf, setTf] = useState(5);
  const [mode, setMode] = useState<ChartMode>("candle");
  const [showVwap, setShowVwap] = useState(true);
  const [showMa, setShowMa] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [showHighLow, setShowHighLow] = useState(true);

  const { symbol, candles, currentSession, loading, error } = useMXFCandles(tf);

  const { sessions, yMin, yMax, ma5, ma20, todayHigh, todayLow } = useMemo(() => {
    if (candles.length === 0) {
      return {
        sessions: [] as ChartSession[],
        yMin: 0, yMax: 0,
        ma5: [] as number[], ma20: [] as number[],
        todayHigh: 0, todayLow: 0,
      };
    }
    // sessions = 用最早/最晚 candle 算夜盤/日盤兩段(後端已只回當前交易日)
    // 簡化:直接從 candles 推 — 看 hour 區分夜盤(15-24, 0-5)vs 日盤(8.45-13.45)
    // 為避免複雜,sessions 用「candles.first.date ~ 第一個 break ~ 最後一段結束」近似
    // 這裡先用 candles 內第一根與最後一根當邊界,gap 內無 candle 自然不會出現
    const first = candles[0].date;
    const last = candles[candles.length - 1].date;
    // 預設一段 session,實際多段邏輯可依 candle gap 推斷
    const sess: ChartSession[] = inferSessions(candles);

    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    const yMin = Math.min(...lows) * 0.998;
    const yMax = Math.max(...highs) * 1.002;

    const closes = candles.map((c) => c.close);
    const ma5 = computeMA(closes, 5);
    const ma20 = computeMA(closes, 20);

    const todayHigh = Math.max(...highs);
    const todayLow = Math.min(...lows);
    return { sessions: sess, yMin, yMax, ma5, ma20, todayHigh, todayLow };
  }, [candles]);

  const innerW = CHART_W - PAD_L - PAD_R;
  const innerH = CHART_H - PAD_T - PAD_B - VOL_H - 8;

  const sx = (iso: string) => PAD_L + scaleX_compressed(iso, sessions, innerW);
  const sy = (v: number) => PAD_T + scaleY_clamped(v, yMin, yMax, innerH);

  if (loading) return <div className="p-8 text-center text-ink-muted">載入中…</div>;
  if (error) return <div className="p-8 text-center text-bear">{error}</div>;
  if (!symbol) return <div className="p-8 text-center text-ink-muted">無法取得 MXF 近月合約</div>;

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center gap-4 text-sm">
        <span className="font-mono">{symbol}</span>
        <span className="label-tiny">{currentSession === "closed" ? "目前休市" : currentSession === "day" ? "日盤中" : "夜盤中"}</span>
        <div className="flex gap-1">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              className={`px-2 py-0.5 rounded ${tf === t ? "bg-ink text-paper" : "hover:bg-line"}`}
              onClick={() => setTf(t)}
            >
              {t}m
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          <button className={`px-2 py-0.5 rounded ${mode === "candle" ? "bg-ink text-paper" : "hover:bg-line"}`} onClick={() => setMode("candle")}>K 線</button>
          <button className={`px-2 py-0.5 rounded ${mode === "line" ? "bg-ink text-paper" : "hover:bg-line"}`} onClick={() => setMode("line")}>走勢線</button>
        </div>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showVwap} onChange={(e) => setShowVwap(e.target.checked)} /> VWAP</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showMa} onChange={(e) => setShowMa(e.target.checked)} /> MA</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showVolume} onChange={(e) => setShowVolume(e.target.checked)} /> 量</label>
        <label className="flex gap-1 items-center"><input type="checkbox" checked={showHighLow} onChange={(e) => setShowHighLow(e.target.checked)} /> 高/低</label>
      </div>

      {/* SVG */}
      <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: "100%", height: "auto" }}>
        {/* Y 軸格線 */}
        {[0, 0.25, 0.5, 0.75, 1].map((r) => {
          const y = PAD_T + r * innerH;
          return <line key={r} x1={PAD_L} x2={CHART_W - PAD_R} y1={y} y2={y} stroke="#eee" strokeDasharray="2 4" />;
        })}

        {/* session gap 虛線 */}
        {sessionBoundaries(sessions, innerW).map((g, i) => (
          <g key={i}>
            <line x1={PAD_L + g.gapStartPx} x2={PAD_L + g.gapStartPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
            <line x1={PAD_L + g.gapEndPx} x2={PAD_L + g.gapEndPx} y1={PAD_T} y2={PAD_T + innerH} stroke="#bbb" strokeDasharray="3 3" />
          </g>
        ))}

        {/* 主圖 */}
        {mode === "candle" ? (
          <CandlestickSeries candles={candles} scaleX={sx} scaleY={sy} width={innerW} />
        ) : (
          <LineSeries candles={candles} scaleX={sx} scaleY={sy} field="close" stroke="#d9534f" />
        )}

        {/* VWAP */}
        {showVwap && <LineSeries candles={candles} scaleX={sx} scaleY={sy} field="average" stroke="#9aa0a6" dashed />}

        {/* MA */}
        {showMa && (
          <>
            <MALine candles={candles} maValues={ma5} scaleX={sx} scaleY={sy} stroke="#f59e0b" label="MA5" />
            <MALine candles={candles} maValues={ma20} scaleX={sx} scaleY={sy} stroke="#3b82f6" label="MA20" />
          </>
        )}

        {/* 今日高低標記 */}
        {showHighLow && candles.length > 0 && (
          <g>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayHigh)} y2={sy(todayHigh)} stroke="#d9534f" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayHigh) + 3} fontSize={10} fill="#d9534f">H {todayHigh}</text>
            <line x1={PAD_L} x2={CHART_W - PAD_R} y1={sy(todayLow)} y2={sy(todayLow)} stroke="#2e7d32" strokeWidth={0.5} strokeDasharray="1 3" />
            <text x={CHART_W - PAD_R + 4} y={sy(todayLow) + 3} fontSize={10} fill="#2e7d32">L {todayLow}</text>
          </g>
        )}

        {/* 量子圖 */}
        {showVolume && candles.length > 1 && (
          <VolumeSubChart
            candles={candles}
            scaleX={sx}
            yTop={PAD_T + innerH + 8}
            height={VOL_H}
            barWidth={Math.max(1, (innerW / candles.length) * 0.6)}
          />
        )}
      </svg>
    </div>
  );
}

// 從 candles 推 sessions(夜盤段 + 日盤段)
// 規則:遇到時間 gap > 1 小時的相鄰 candle = session 邊界
function inferSessions(candles: { date: string }[]): ChartSession[] {
  if (candles.length === 0) return [];
  const sess: ChartSession[] = [];
  let curStart = candles[0].date;
  for (let i = 1; i < candles.length; i++) {
    const prev = new Date(candles[i - 1].date).getTime();
    const cur = new Date(candles[i].date).getTime();
    if (cur - prev > 60 * 60 * 1000) {
      sess.push({ startIso: curStart, endIso: candles[i - 1].date });
      curStart = candles[i].date;
    }
  }
  sess.push({ startIso: curStart, endIso: candles[candles.length - 1].date });
  return sess;
}
```

- [ ] **Step 2: Smoke compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type error。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MXFIntradayChart.tsx
git commit -m "feat(mxf): MXFIntradayChart with toggles + session-aware X axis

K 線 / 走勢線切換、6 種週期、VWAP / MA / 量子圖 / 高低 toggle。
從 candles 推 sessions(>1h gap 視為 session 邊界)。"
```

---

## Task 15: 前端 — 整合到 `MXFBacktest` 頁

**Files:**
- Modify: `frontend/src/pages/MXFBacktest.tsx`

把 placeholder 換成 `<MXFIntradayChart />`,留一個空欄位給未來的回測介面。

- [ ] **Step 1: 改頁面**

```tsx
import { MXFIntradayChart } from "../components/MXFIntradayChart";

export function MXFBacktest() {
  return (
    <div className="h-full flex flex-col gap-6 px-8 py-6">
      <header>
        <span className="label-tiny mb-1">Module · Live + Backtest</span>
        <h1 className="h-display text-2xl text-ink">小台指(MXF)</h1>
      </header>

      <section className="rounded-lg border border-line p-4">
        <div className="label mb-3">即時分時走勢</div>
        <MXFIntradayChart />
      </section>

      <section className="rounded-lg border border-line p-4 text-ink-muted">
        <div className="label mb-3">回測介面</div>
        <p>策略回測引擎開發中 — 多週期 K 線、視覺化策略編輯、績效報表。</p>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: 啟動 + 手動驗證**

```bash
.\start.ps1   # 或分別啟 backend / frontend
```

打開瀏覽器 → MXFBacktest 頁面,確認:
- 圖能渲染、近月 symbol 顯示在左上(例 "MXFF6")
- 6 個 timeframe 按鈕能切、切後 candles 重抓
- K 線 / 走勢線 toggle 能切
- VWAP / MA / 量 / 高低 4 個 checkbox 能切
- 「目前休市 / 日盤中 / 夜盤中」chip 顯示正確
- 開盤時段:WS push 進來 → 最後一根 K 棒實時更新

如有問題 → 在這個 task 內修。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/MXFBacktest.tsx
git commit -m "feat(mxf): integrate MXFIntradayChart into MXFBacktest page"
```

---

## Task 16: 實測富邦 API 風險點 + 留底文件

**Files:**
- Create: `docs/notes/mxf-fubon-api-observations.md`

針對 spec 第 11 段「風險與待驗證項」做實測,把觀察結果留底。實測時段需橫跨日盤 + 夜盤 + 休市,理想要連續 24-48 小時觀察。

- [ ] **Step 1: 建文件,列待驗證項**

`docs/notes/mxf-fubon-api-observations.md`:
```markdown
# MXF Fubon API 實測觀察記錄

實測對象:富邦 Neo SDK 2.2.8 期貨 REST + WS。
實測目的:驗證 spec 風險點假設、為後續調整提供事實依據。

## 觀察 1: `intraday.candles(session='afterhours')` 拿到的「夜盤」

**假設**:文件未明說「最近一場」是「已收的還是進行中的」。

**實測方法**:
- 在日盤中(例 11:00)call → 記錄第一根 / 最後一根 candle date
- 在夜盤中(例 22:00)call → 記錄第一根 / 最後一根
- 在休市中(例 14:30、06:00)call → 記錄第一根 / 最後一根

**結論**:[實測後填寫]

## 觀察 2: WS push 是「每根 K 完成才推」vs「累積中那根每秒更新」

**假設**:文件未明說。

**實測方法**:
- 訂閱 candles channel(1m timeframe),log 每次 push 的 date 與 close
- 觀察相同 date 是否多次 push、close 是否會變

**結論**:[實測後填寫]

## 觀察 3: Session 邊界精確時間

**假設**:13:45:00 收盤、15:00:00 開盤,但富邦可能在 13:45:30 才停推、15:00:00 後幾秒才開推。

**實測方法**:
- 在 13:44:50 ~ 13:46:00 觀察 push 頻率
- 在 14:59:50 ~ 15:00:30 觀察 push 頻率

**結論**:[實測後填寫]

## 觀察 4: 近月 products 結算當週行為

**假設**:結算當週的合約是不是會立刻從 products 移除?

**實測方法**:
- 在結算日(每月第三週三 13:45)前後 call `futopt.intraday.tickers`,記錄 MXF 系列的內容差異

**結論**:[實測後填寫]

## 觀察 5: 跨日(00:00)的 push 行為

**假設**:文件未提。

**實測方法**:
- 在 23:58 ~ 00:02 觀察 push 內容

**結論**:[實測後填寫]
```

- [ ] **Step 2: 執行實測 + 填寫結論**

在交易時段啟動後端 + 前端,觀察各 endpoint 行為,把實際觀察填回文件。

如果發現實測結果跟 spec 假設不符 → 開新 task 修正 service/hook 邏輯,並把該 commit 連結到本文件。

- [ ] **Step 3: Commit**

```bash
git add docs/notes/mxf-fubon-api-observations.md
git commit -m "docs(mxf): record fubon futures API observations from live trading session"
```

---

## Self-Review Notes

完成 plan 後做以下自查:

1. **Spec coverage:**
   - 2.1 第一版包含項目 → Task 7 + Task 14 / 15 涵蓋
   - 2.2 不在 scope:CDP / Camarilla → 未被任何 task 引入 ✓
   - 7.1 「交易日 = 前一天 15:00 → 當天 13:45」 → Task 1 (determine_current_session) ✓
   - 7.3 Session 切換 → Task 5 + Task 6 ✓
   - 11 風險點 → Task 16 ✓

2. **Placeholder scan**:無 TBD / TODO / "implement later"。

3. **Type 一致性**:
   - `MXFCandleDict` (backend) ↔ `MXFCandle` (frontend api.ts) ↔ `OHLCCandle` (chart-svg) — 欄位名稱對齊(date, open, high, low, close, volume, average)
   - `current_session` (backend `routes/mxf.py`) ↔ `current_session` (frontend `MXFCandlesResponse.current_session`)
   - `subscribeMxfCandles` (Task 12) ↔ `useMXFCandles` 使用(Task 13)

---

## 執行模式選擇

Plan 寫好了。下一步請選一種執行方式:

**1. Subagent-Driven(推薦)** — 每個 task 派一個 fresh subagent 跑、中間我審 + 修正、迭代快。適合這種「16 個 task、長期跑」的 plan。

**2. Inline Execution** — 在這個 session 直接 batch 跑、有 checkpoint。適合你想在線陪跑、控制更直接的場景。

請選一個。
