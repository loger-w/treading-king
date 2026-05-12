"""條件 DSL — 篩股引擎的 input schema。

Plan §Phase 2b。Filter 的 JSON 形式存在 strategies.filter_json，
前端透過條件編輯器生成、後端解析翻成 supabase query。

v1 限制：
- operator 不含 cross_above/cross_below（indicator_cache 表無歷史）
- exclude_etf 保留但目前無實效（cache 表已只含 4 位純數字個股）
- days_ago 保留但目前只支援 0（同上）

未來 v2 cache 改保留 N 天歷史時，把 cross_above/cross_below 跟 days_ago>0 接回。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 篩股可用的 21 個欄位（16 個 indicator_cache + Phase 3 新增 5 個 CDP 線）
ConditionField = Literal[
    "close",
    "change_pct",
    "volume",
    "amount",
    "rsi_14",
    "macd",
    "macd_signal",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "sma_5",
    "sma_20",
    "sma_60",
    "bbands_upper",
    "bbands_middle",
    "bbands_lower",
    # Phase 3 新增（從 daily_ohlc 算出的 5 線）
    "cdp_ah",
    "cdp_nh",
    "cdp",
    "cdp_nl",
    "cdp_al",
]

# v1 不含 cross_above/cross_below
ConditionOperator = Literal["gt", "gte", "lt", "lte", "eq"]

ALL_FIELDS: tuple[ConditionField, ...] = (
    "close", "change_pct", "volume", "amount",
    "rsi_14", "macd", "macd_signal",
    "kdj_k", "kdj_d", "kdj_j",
    "sma_5", "sma_20", "sma_60",
    "bbands_upper", "bbands_middle", "bbands_lower",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
)


class Condition(BaseModel):
    """單一篩股條件。

    value 是 float 時：跟常數比較（譬如 rsi_14 < 30）
    value 是 str 時：跟其他欄位比較（譬如 close > sma_20，value="sma_20"）
        — str 必須是 ALL_FIELDS 之一
    """

    field: ConditionField
    operator: ConditionOperator
    value: float | str
    days_ago: int = Field(default=0, ge=0, le=0, description="v1 只支援 0")

    @field_validator("value")
    @classmethod
    def value_str_must_be_field(cls, v: float | str) -> float | str:
        if isinstance(v, str) and v not in ALL_FIELDS:
            raise ValueError(
                f"value 是 str 時必須是欄位名，{v!r} 不在 {ALL_FIELDS}"
            )
        return v


Market = Literal["TWSE", "OTC"]
Logic = Literal["AND", "OR"]


class Filter(BaseModel):
    """篩股請求的完整 schema。

    DSL 演進時 schema_version 加 1，保留舊版 strategies 可載入（Phase 2b 末若改設計）。
    """

    schema_version: int = 1
    market: list[Market] = Field(default_factory=lambda: ["TWSE", "OTC"])
    exclude_etf: bool = True
    conditions: list[Condition] = Field(default_factory=list)
    logic: Logic = "AND"
    limit: int = Field(default=200, ge=1, le=2000)

    @field_validator("market")
    @classmethod
    def market_non_empty(cls, v: list[Market]) -> list[Market]:
        if not v:
            raise ValueError("market 至少要有一個（TWSE 或 OTC）")
        return v

    @model_validator(mode="after")
    def conditions_non_empty(self):
        if not self.conditions:
            raise ValueError("至少要有一個 condition")
        return self


# ---------------------------------------------------------------------------
# Phase 3 擴充 — 即時訊號 DSL
# ---------------------------------------------------------------------------

WindowConditionType = Literal["price_change_pct", "volume_burst", "trade_count"]
WindowSeconds = Literal[60, 180, 300, 600, 1800]


class WindowCondition(BaseModel):
    """即時時窗條件 — 從 ring_buffer 算 N 秒內的數值。

    type:
      - price_change_pct: (latest_price / window_start_price - 1) * 100
      - volume_burst: 窗口累積成交量 / 過去 N 個窗口平均成交量
      - trade_count: 窗口內成交筆數
    """

    type: WindowConditionType
    window_seconds: WindowSeconds
    operator: Literal["gt", "gte", "lt", "lte"]
    value: float


class ActiveFilter(Filter):
    """即時訊號專用 Filter — 在 Filter 之上加時窗條件。

    跟 Filter 的差異：允許 conditions=[] 當 window_conditions 非空（即時訊號可單獨用時窗條件）。
    """

    window_conditions: list[WindowCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # 覆蓋 Filter.conditions_non_empty：允許 conditions=[] 當 window_conditions 非空
        if not self.conditions and not self.window_conditions:
            raise ValueError("至少要有一個 condition 或 window_condition")
        return self


class WatchlistScope(BaseModel):
    type: Literal["watchlist"]


class SymbolsScope(BaseModel):
    type: Literal["symbols"]
    symbols: list[str] = Field(min_length=1, max_length=500)


Scope = WatchlistScope | SymbolsScope


class ActiveSignalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    filter_json: ActiveFilter
    scope: Scope
    cooldown_seconds: int = Field(default=1800, ge=60, le=86400)
    ignore_auctions: bool = True
    enabled: bool = True


class ActiveSignalOut(ActiveSignalCreate):
    id: str
    created_at: str
