"""條件 DSL — 即時訊號 ActiveFilter 的 input schema。

Filter 的 JSON 形式存在 active_signals.filter_json,前端條件編輯器
(ActiveSignalEditor) 生成,signal_engine 評估時讀取。

v1 限制：
- operator 不含 cross_above/cross_below(indicator_cache 表無歷史)
- days_ago 保留但目前只支援 0(同上)

未來 v2 cache 改保留 N 天歷史時,把 cross_above/cross_below 跟 days_ago>0 接回。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 即時訊號可用的 21 個欄位(16 個 indicator_cache + 5 個 CDP 線)
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
    # 從 daily_ohlc 算出的 5 線
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
    """單一條件。

    value 是 float 時:跟常數比較(譬如 rsi_14 < 30)
    value 是 str 時:跟其他欄位比較(譬如 close > sma_20, value="sma_20")
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
                f"value 是 str 時必須是欄位名,{v!r} 不在 {ALL_FIELDS}"
            )
        return v


Logic = Literal["AND", "OR"]


class Filter(BaseModel):
    """Condition 集合的 base — 供 ActiveFilter 繼承。

    DSL 演進時 schema_version 加 1,保留舊版 active_signals.filter_json 可載入。
    """

    schema_version: int = 1
    conditions: list[Condition] = Field(default_factory=list)
    logic: Logic = "AND"

    @model_validator(mode="after")
    def conditions_non_empty(self):
        if not self.conditions:
            raise ValueError("至少要有一個 condition")
        return self


# ---------------------------------------------------------------------------
# 即時訊號 DSL — Filter 之上加時窗條件
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

    跟 Filter 的差異:允許 conditions=[] 當 window_conditions 非空
    (即時訊號可單獨用時窗條件)。
    """

    window_conditions: list[WindowCondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # 覆蓋 Filter.conditions_non_empty:允許 conditions=[] 當 window_conditions 非空
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
    enabled: bool = True


class ActiveSignalOut(ActiveSignalCreate):
    id: str
    created_at: str
