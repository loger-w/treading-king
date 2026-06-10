"""下單安全閘 —— 純函式,所有寫入(下單/改/刪/平倉)送群益前必過。"""
from __future__ import annotations
from dataclasses import dataclass
from services.capital_models import StockOrderRequest


@dataclass(frozen=True)
class SafetyConfig:
    order_enabled: bool
    max_qty: int
    max_amount: float


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str | None = None


def _master(cfg: SafetyConfig) -> GateResult | None:
    if not cfg.order_enabled:
        return GateResult(False, "下單總開關關閉(CAPITAL_ORDER_ENABLED=false)")
    return None


def check_stock_order(req: StockOrderRequest, cfg: SafetyConfig) -> GateResult:
    blocked = _master(cfg)
    if blocked:
        return blocked
    if req.qty <= 0:
        return GateResult(False, "數量必須大於 0")
    if cfg.max_qty and req.qty > cfg.max_qty:
        return GateResult(False, f"數量 {req.qty} 張超過上限 {cfg.max_qty} 張")
    est = req.price * req.qty * 1000
    if cfg.max_amount and est > cfg.max_amount:
        return GateResult(False, f"預估金額 {est:.0f} 超過上限 {cfg.max_amount:.0f}")
    return GateResult(True)


def check_cancel(cfg: SafetyConfig) -> GateResult:
    """刪單只降風險:僅過總開關。"""
    return _master(cfg) or GateResult(True)


def check_correct_price(new_price: float, remaining_shares: int, cfg: SafetyConfig) -> GateResult:
    """改價改變曝險:總開關 + 新價×未成交股數過金額閘。"""
    blocked = _master(cfg)
    if blocked:
        return blocked
    if new_price <= 0:
        return GateResult(False, "價格必須大於 0")
    est = new_price * remaining_shares
    if cfg.max_amount and est > cfg.max_amount:
        return GateResult(False, f"預估金額 {est:.0f} 超過上限 {cfg.max_amount:.0f}")
    return GateResult(True)


def check_decrease(qty_lots: int, cfg: SafetyConfig) -> GateResult:
    """減量只降風險:總開關 + 量>0。"""
    blocked = _master(cfg)
    if blocked:
        return blocked
    if qty_lots <= 0:
        return GateResult(False, "減量必須大於 0")
    return GateResult(True)
