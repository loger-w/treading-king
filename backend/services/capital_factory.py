"""從環境變數組裝 CapitalClient 單例。"""
from __future__ import annotations
import logging
import os
from services.capital_client import CapitalClient
from services.capital_com import SkcomCapitalCom
from services.capital_models import CapitalEnv
from services.capital_safety import SafetyConfig

logger = logging.getLogger(__name__)

_client: CapitalClient | None = None


def _env_num(name: str, cast) -> int | float:
    """上限環境變數解析。解析失敗回 0(=安全閘拒單),不可拋出 —
    get_capital 在每個 route 都會被呼叫,拋 ValueError 會變成難診斷的 500。"""
    raw = os.getenv(name, "0").strip()
    try:
        return cast(raw or 0)
    except ValueError:
        logger.error("%s=%r 解析失敗 → 視為 0(上限未設,送單將被安全閘拒絕)", name, raw)
        return cast(0)


def get_capital() -> CapitalClient | None:
    """未設定 CAPITAL_USER_ID 時回 None(功能未啟用)。"""
    global _client
    if _client is not None:
        return _client
    user_id = os.getenv("CAPITAL_USER_ID", "").strip()
    if not user_id:
        return None
    env_raw = os.getenv("CAPITAL_ENV", "test").strip().lower()
    try:
        env = CapitalEnv(env_raw)
    except ValueError:
        # 未知值不可默認正式:SetAuthority 不呼叫/失敗的預設就是正式環境,
        # 拼錯 env 寧可整個功能不啟用,也不可疑似落在真錢環境
        logger.error("CAPITAL_ENV=%r 不是 test/prod,群益功能不啟用", env_raw)
        return None
    safety = SafetyConfig(
        order_enabled=os.getenv("CAPITAL_ORDER_ENABLED", "false").strip().lower() == "true",
        max_qty=int(_env_num("CAPITAL_MAX_QTY", int)),
        max_amount=float(_env_num("CAPITAL_MAX_AMOUNT", float)),
    )
    if safety.order_enabled and (safety.max_qty <= 0 or safety.max_amount <= 0):
        # 上限未設時安全閘 fail-closed 全擋,啟動就要講清楚,不要等送單才發現
        logger.error("CAPITAL_ORDER_ENABLED=true 但 CAPITAL_MAX_QTY=%s / CAPITAL_MAX_AMOUNT=%s"
                     " 未設(≤0)— 所有送單/改價將被安全閘拒絕",
                     safety.max_qty, safety.max_amount)
    _client = CapitalClient(
        SkcomCapitalCom(dll_dir=os.getenv("CAPITAL_DLL_DIR", "").strip() or None),
        user_id=user_id,
        password=os.getenv("CAPITAL_PASSWORD", "").strip(),
        full_account=os.getenv("CAPITAL_FULL_ACCOUNT", "").strip(),
        env=env.value,
        safety=safety,
        audit_path=None,
    )
    return _client
