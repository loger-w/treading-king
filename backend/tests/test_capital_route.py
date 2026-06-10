# backend/tests/test_capital_route.py
from fastapi.testclient import TestClient
import main
import services.capital_factory as factory
from services.capital_models import OrderResult, Position, OrderRecord


class FakeClient:
    status = "ok"
    last_error = None

    class _Store:
        def orders(self):
            return [OrderRecord(seq_no="A1", stock_no="2330", status_label="委託成功")]

        def positions(self):
            return [Position(stock_no="2330", name="台積電", qty=5, avg_price=575.0)]

    store = _Store()

    async def submit_stock_order(self, req):
        return OrderResult(ok=True, code=0, message="委託成功", seq_no="A1")


def _client(monkeypatch, fake):
    monkeypatch.setattr(factory, "get_capital", lambda: fake)
    return TestClient(main.app)


def test_status_unavailable_when_none(monkeypatch):
    c = _client(monkeypatch, None)
    r = c.get("/api/capital/status")
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_orders_and_positions(monkeypatch):
    c = _client(monkeypatch, FakeClient())
    assert c.get("/api/capital/orders").json()["orders"][0]["seq_no"] == "A1"
    assert c.get("/api/capital/positions").json()["positions"][0]["stock_no"] == "2330"


def test_submit_order_ok(monkeypatch):
    c = _client(monkeypatch, FakeClient())
    r = c.post("/api/capital/order/stock", json={
        "stock_no": "2330", "buy_sell": "buy", "price": 590.0, "qty": 1,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["seq_no"] == "A1"


def test_orders_filters_futures_and_enriches_name(monkeypatch):
    from services.capital_models import OrderRecord

    class _Store:
        def orders(self):
            return [
                OrderRecord(seq_no="S1", stock_no="3357", market="TS", status_label="已刪單"),
                OrderRecord(seq_no="F1", stock_no="QEF06", market="TF", status_label="委託成功"),
            ]
        def positions(self):
            return []

    fake = FakeClient()
    fake.store = _Store()
    c = _client(monkeypatch, fake)

    import routes.capital as capital_route
    monkeypatch.setattr(capital_route, "_symbol_name", lambda code: "臺慶科" if code == "3357" else "")

    orders = c.get("/api/capital/orders").json()["orders"]
    assert len(orders) == 1                      # TF 被過濾
    assert orders[0]["seq_no"] == "S1"
    assert orders[0]["name"] == "臺慶科"
