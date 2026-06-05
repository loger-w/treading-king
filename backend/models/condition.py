"""條件 DSL — 即時訊號 ActiveFilter 的 input schema。

Filter 的 JSON 形式存在 active_signals.filter_json,前端條件編輯器
(ActiveSignalEditor) 生成,signal_engine 評估時讀取。

限制:
- operator 不含 cross_above/cross_below(未保留歷史 series)
- days_ago 保留但目前只支援 0
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 即時訊號可用的欄位(1 個即時 close + 5 個 CDP 線 + 2 條 MA + 2 個日內)
ConditionField = Literal[
    "close",
    # 從 daily_ohlc 算出的 5 線
    "cdp_ah",
    "cdp_nh",
    "cdp",
    "cdp_nl",
    "cdp_al",
    # 富邦 tech.sma 拉的 MA(每日 refill 進 field_cache)
    "sma_5",
    "sma_20",
    # 日內動態欄位 — 每 tick 重算
    "day_change_pct",  # (tick.price - 昨日收盤) / 昨日收盤 * 100
    "day_volume",      # backend 累積今日成交量(restart 後重算)
]

ConditionOperator = Literal["gt", "gte", "lt", "lte", "eq"]

ALL_FIELDS: tuple[ConditionField, ...] = (
    "close",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
    "sma_5", "sma_20",
    "day_change_pct", "day_volume",
)


class Condition(BaseModel):
    """單一條件。

    value 是 float 時:跟常數比較(譬如 close < 100)
    value 是 str 時:跟其他欄位比較(譬如 cdp_ah eq close, value="close")
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


class CdpProximityCondition(BaseModel):
    """CDP 觸發條件 — tick price 落在所選 CDP 線的 ±N tick 範圍內。

    levels: 要監看的 CDP 線(5 條任意組合,預設全選)
    tolerance_ticks:
      - 0  → 嚴格「打到」(tick.price == cdp_X,已 round to tick 所以可 exact match)
      - >0 → 「接近」(tick.price 在 cdp_X ± tolerance × tick_size(cdp_X) 內)
    """

    levels: list[Literal["ah", "nh", "cdp", "nl", "al"]] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"],
        min_length=1,
    )
    tolerance_ticks: int = Field(default=0, ge=0, le=10)


class MAProximityCondition(BaseModel):
    """MA 觸發條件 — tick price 落在所選 MA 線的 ±N tick 範圍內。

    SMA raw 通常不在合法 tick 上(算術平均),tolerance=0 嚴格打到實務上很難命中;
    UI 預設應 ≥ 1。
    """

    levels: list[Literal["sma_5", "sma_20"]] = Field(
        default_factory=lambda: ["sma_5", "sma_20"],
        min_length=1,
    )
    tolerance_ticks: int = Field(default=0, ge=0, le=10)


CdpLevel = Literal["ah", "nh", "cdp", "nl", "al"]


class LimitUpOpenTouchStrategy(BaseModel):
    """策略 1:曾鎖死漲停 ≥ lock_seconds → 打開後由上而下碰所選 CDP 線。"""

    type: Literal["limit_up_open_touch"]
    lock_seconds: int = Field(default=60, ge=5, le=600)
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"], min_length=1,
    )
    tolerance_ticks: int = Field(default=1, ge=0, le=10)


class BreakoutRetestStrategy(BaseModel):
    """策略 2:早盤爆拉由下突破某 CDP 線 → arm → retest_within_minutes 分內回踩同線。"""

    type: Literal["breakout_retest"]
    early_window_minutes: int = Field(default=10, ge=1, le=60)
    surge_pct: float = Field(default=3.0, gt=0, le=20)
    surge_window_seconds: WindowSeconds = 60
    retest_within_minutes: int = Field(default=10, ge=1, le=120)
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"], min_length=1,
    )
    tolerance_ticks: int = Field(default=1, ge=0, le=10)


StrategyConfig = Annotated[
    LimitUpOpenTouchStrategy | BreakoutRetestStrategy,
    Field(discriminator="type"),
]


class ActiveFilter(Filter):
    """即時訊號專用 Filter — 在 Filter 之上加時窗條件 + CDP/MA 觸發 + preset 策略。

    跟 Filter 的差異:允許 conditions=[] 當 window_conditions / cdp_proximity /
    ma_proximity / strategy 任一非空。
    """

    schema_version: int = 5  # 4→5,加 strategy(preset 策略)
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None
    ma_proximity:  MAProximityCondition  | None = None
    strategy: StrategyConfig | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # 覆蓋 Filter.conditions_non_empty;strategy 存在時由 strategy 定義整條 filter
        if self.strategy is not None:
            return self
        if (not self.conditions
                and not self.window_conditions
                and self.cdp_proximity is None
                and self.ma_proximity is None):
            raise ValueError("至少要有一個 condition / window_condition / cdp_proximity / ma_proximity / strategy")
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
    notify_discord: bool = True


class ActiveSignalOut(ActiveSignalCreate):
    id: str
    created_at: str
