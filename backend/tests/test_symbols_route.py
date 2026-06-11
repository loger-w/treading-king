from fastapi.testclient import TestClient
from main import app
from routes.symbols import _fetch_isin
from services.local_store import get_local_store

client = TestClient(app)


def test_search_reads_from_market_cache(local_store_tmp):
    get_local_store().market.replace_symbols([
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False, "is_active": True},
    ])
    r = client.get("/api/symbols", params={"search": "23", "limit": 10})
    assert r.status_code == 200
    assert r.json() == {"results": [
        {"symbol": "2330", "name": "台積電", "market": "TWSE", "is_etf": False}]}


# ---- refresh 防線 ----

class _FakeResp:
    def __init__(self, *, content: bytes | None = None, json_data=None):
        self.content = content
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeAsyncClient:
    """ISIN 回固定 HTML、OpenAPI 回空 — 模擬只抓到極少 symbol 的情境。"""

    def __init__(self, isin_html: str, *args, **kwargs):
        self._isin_html = isin_html

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        if "isin.twse.com.tw" in url:
            return _FakeResp(content=self._isin_html.encode("big5"))
        return _FakeResp(json_data=[])


_ONE_SYMBOL_ISIN_HTML = (
    '<tr><td colspan="7">股票</td></tr>'
    '<tr><td>2330　台積電</td></tr>'
)


async def test_fetch_isin_zero_rows_is_error():
    """section regex 沒命中(TWSE 改版)要回 error,不可當成功的 0 筆。

    否則 ISIN 來源靜默變零筆,主表會被只剩當日有交易股的 OpenAPI 覆蓋。
    """
    rows, err = await _fetch_isin(
        _FakeAsyncClient("<html>改版了沒有股票 section</html>"), 2, "TWSE")
    assert rows == []
    assert err is not None


def test_refresh_rejects_suspicious_shrink(local_store_tmp, monkeypatch):
    """新抓到的筆數 < 既有主表 50% → 拒絕整表替換,保住原主表。"""
    seed = [{"symbol": f"{1000 + i}", "name": f"股{i}", "market": "TWSE",
             "is_etf": False, "is_active": True} for i in range(10)]
    get_local_store().market.replace_symbols(seed)
    monkeypatch.setattr(
        "routes.symbols.httpx.AsyncClient",
        lambda *a, **k: _FakeAsyncClient(_ONE_SYMBOL_ISIN_HTML))
    r = client.post("/api/symbols/refresh")
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "suspiciously_few_symbols"
    assert get_local_store().market.has_symbol("1000")  # 主表未被覆蓋


def test_refresh_replaces_when_cache_empty(local_store_tmp, monkeypatch):
    """既有主表為空(bootstrap)時不適用防線,正常寫入。"""
    monkeypatch.setattr(
        "routes.symbols.httpx.AsyncClient",
        lambda *a, **k: _FakeAsyncClient(_ONE_SYMBOL_ISIN_HTML))
    r = client.post("/api/symbols/refresh")
    assert r.status_code == 200
    assert get_local_store().market.has_symbol("2330")
