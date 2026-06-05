"""從環境變數組裝 CapitalClient 單例。"""
from __future__ import annotations
import os
from services.capital_client import CapitalClient
from services.capital_com import SkcomCapitalCom
from services.capital_safety import SafetyConfig

_client: CapitalClient | None = None


def get_capital() -> CapitalClient | None:
    """未設定 CAPITAL_USER_ID 時回 None(功能未啟用)。"""
    global _client
    if _client is not None:
        return _client
    user_id = os.getenv("CAPITAL_USER_ID", "").strip()
    if not user_id:
        return None
    _client = CapitalClient(
        SkcomCapitalCom(),
        user_id=user_id,
        password=os.getenv("CAPITAL_PASSWORD", "").strip(),
        full_account=os.getenv("CAPITAL_FULL_ACCOUNT", "").strip(),
        env=os.getenv("CAPITAL_ENV", "test").strip(),
        safety=SafetyConfig(
            order_enabled=os.getenv("CAPITAL_ORDER_ENABLED", "false").strip().lower() == "true",
            max_qty=int(os.getenv("CAPITAL_MAX_QTY", "0") or 0),
            max_amount=float(os.getenv("CAPITAL_MAX_AMOUNT", "0") or 0),
        ),
        audit_path=None,
    )
    return _client
