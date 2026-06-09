# backend/tests/test_capital_client_reply.py
import asyncio
from services.capital_safety import SafetyConfig
from services.capital_client import CapitalClient


class FakeCom:
    def __init__(self): self.on_reply = None
    def setup(self, on_reply=None): self.on_reply = on_reply
    def set_authority(self, f): return 0
    def login(self, u, p): return 0
    def init_order(self): return 0
    def read_cert(self, u): return 0
    def return_code_message(self, c): return "ok"
    def pump(self): ...


def _client(com):
    return CapitalClient(
        com, user_id="u", password="p", full_account="A", env="test",
        safety=SafetyConfig(order_enabled=True, max_qty=5, max_amount=9e9),
        audit_path=None,
    )


def test_reply_updates_store_and_calls_broadcast():
    com = FakeCom()
    client = _client(com)
    pushed = []
    client.set_broadcast(lambda payload: pushed.append(payload))
    # 模擬 COM 執行緒收到 OnNewData(不啟動真執行緒,直接呼叫 handler)
    client._handle_reply("A1,,1,0,,,,,2330,,B1,590.00,,,,,,,,,3,,,,")
    orders = client.store.orders()
    assert len(orders) == 1 and orders[0].seq_no == "A1"
    assert pushed and pushed[0]["event"] == "capital_order"
    assert pushed[0]["data"]["seq_no"] == "A1"
