# 策略 A — 造山後碰 CDP 無力（做空訊號）設計

> 造山積木確認做頭後，價格反彈碰到 CDP 線但站不上去 → 做空進場訊號。

## 前提

造山積木 v4 已在 signal_engine 內建，`_mountain_state[symbol]["phase"]` 會自動追蹤。
策略 A 是第一個**消費** `_mountain_state` confirmed phase 的 evaluator。

## 觸發條件（全部 AND）

1. **造山 `phase == "confirmed"`**（同一檔、當天內，不限時間窗口）
2. **candle.high >= CDP 線**（arm：高點觸及 CDP 線）
3. **連續 N 根 candle close < 該 CDP 線**（confirm：站不上去）
4. **（可選）碰線時 close < VWAP**（`require_below_vwap`，預設 false）

CDP 線可選：`ah`、`nh`、`cdp`（中軸）。預設全選。

## 回測驗證（6/16 單日 303 檔）

| 方法 | 訊號 | 平均跌% | 獲利率 | 大賺率 | 虧損率 |
|------|------|---------|--------|--------|--------|
| 碰CDP + 2根確認（無VWAP） | 99 | -1.18% | 66% | 46% | 20% |
| VWAP下 + 碰CDP（無candle確認） | 70 | -1.03% | 61% | 41% | 17% |

深度分層分析（6/16）顯示 VWAP 下訊號品質遠優於 VWAP 上（71% vs 57% 獲利率，10% vs 38% 虧損率）。
GA 因 fitness 函數偏重量而選了 vwap=N，但條件式分層分析揭示 VWAP 的真實價值。
**Production 預設：confirm_bars=2 + require_below_vwap=true**。

## 兩階段觸發流程

```
settled candle
  │
  ├─ candle.high >= CDP_level × (1 - tolerance_pct/100)?  ──yes──→  ARM
  │                                        │
  │                                   candle.close < CDP_level?
  │                                   ├─ yes → confirm_count += 1
  │                                   │        >= N? → FIRE signal
  │                                   └─ no  → DISARM (重置)
  │
  └─ (require_below_vwap && close >= vwap) → skip
```

## VWAP 即時計算（引擎新增）

per-symbol，每根 settled candle 更新：

```python
tp = (candle.high + candle.low + candle.close) / 3
state["cum_tp_vol"] += tp * candle.volume
state["cum_vol"] += candle.volume
vwap = state["cum_tp_vol"] / state["cum_vol"]
```

存進 `_field_cache[symbol]["vwap"]`。收盤清除（同 mountain state）。

VWAP 不存 DB，只在引擎 session 內存活。其他 evaluator / 前端可讀 field_cache 取用。

## Pydantic Model

```python
class MountainBounceStrategy(BaseModel):
    """策略 A：造山確認 + 碰 CDP 線 + 連續 N 根 close 在線下 → 做空訊號。"""

    type: Literal["mountain_bounce"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["ah", "nh", "cdp"], min_length=1,
    )
    confirm_bars: int = Field(default=2, ge=1, le=5)
    tolerance_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    require_below_vwap: bool = False
```

加入 `StrategyConfig` 聯合型別，`schema_version` 升到 8。

| 欄位 | 預設 | 說明 |
|------|------|------|
| `levels` | `["ah", "nh", "cdp"]` | 要監控的 CDP 線 |
| `confirm_bars` | 2 | 碰線後連續幾根 close 在線下才觸發 |
| `tolerance_pct` | 0.0 | 碰線容差（price >= CDP × (1 - tolerance_pct/100)） |
| `require_below_vwap` | **true** | 是否要求碰線時 close < VWAP |

## Evaluator

```python
def _eval_mountain_bounce(
    self, strat: dict, active: ActiveSignalOut, symbol: str,
    candle: MinuteCandle, now: float,
) -> dict | None:
```

走 **candle 路徑**（同 `cdp_breakout_confirm`），每根 settled candle 檢查一次。

### 狀態

```python
self._mountain_bounce_armed: dict[tuple, dict] = {}
# key = (active.id, symbol, level_name)
# value = {"confirm_count": int, "cdp_val": float}
```

- candle.high >= CDP 線 → arm（若已 arm 則不重置 count）
- armed 後 candle.close < CDP 線 → confirm_count += 1
- candle.close >= CDP 線 → disarm
- confirm_count >= N → fire signal + 清除 armed

### Signal output

```python
{
    "level": "nh",
    "direction": "from_below",
    "role": "mountain_bounce",
    "confirm_bars": 2,
    "peak_high": 105.3,
    "below_vwap": true,  # 觸發時是否在 VWAP 下（metadata，不影響觸發）
}
```

`role: "mountain_bounce"` 讓 Discord 推播和前端訊號卡片可以顯示對應的文案。

## Wiring

### signal_engine.py `_evaluate()`

在 `_process_tick` → `_evaluate()` 的 strategy dispatch 裡加：

```python
if stype == "mountain_bounce":
    cdp_touch = self._eval_mountain_bounce(strat, active, symbol, settled, now)
```

走 `settled` candle 路徑（同 `cdp_breakout_confirm`），需要 `settled is not None` 才執行。

### 收盤清除

`_mountain_bounce_armed` 在 session 結束時清除（同 `_mountain_state.clear()`）。

## GA 搜索（回測腳本）

新增 `_ga_strategy_a.py`，染色體：

| 基因 | 範圍 | 步長 |
|------|------|------|
| `confirm_bars` | 1-4 | 1 |
| `require_below_vwap` | true/false | — |
| `levels_combo` | 7 種組合 | — |
| `tolerance_pct` | 0.0-0.5 | 0.1 |

造山參數用 v4 defaults。適應度 = 獲利訊號數 × abs(平均跌幅) − α × 虧損訊號數。

levels 7 種組合：AH only / NH only / CDP only / AH+NH / AH+CDP / NH+CDP / AH+NH+CDP。

## 改動位置

| 檔案 | 改動 |
|------|------|
| `backend/models/condition.py` | 新增 `MountainBounceStrategy`，加入 `StrategyConfig`，`schema_version` → 8 |
| `backend/services/signal_engine.py` | 新增 `_eval_mountain_bounce()`、VWAP 計算（`_update_vwap`）、wiring、收盤清除 |
| `backend/tests/test_strategy_a_mountain_bounce.py` | 新增（TDD） |
| `backend/scripts/_ga_strategy_a.py` | GA 回測腳本 |
| `backend/scripts/_backtest_strategy_a.py` | 已存在，可擴充 |

## 行為細節

- **同檔多 CDP 線**：若 candle.high 碰 AH（> NH > CDP），三條線同時 arm。各線獨立 confirm、獨立 fire。跟 `breakout_confirm` 的 per-level 行為一致。cooldown 機制（per rule+symbol+level）防止同一線的重複訊號。
- **Re-surge 後 armed 殘留**：造山 re-surge（phase → surge_tracking）時 armed 狀態不清除。evaluator 每次先檢查 `phase == "confirmed"`，非 confirmed 時直接 return None → 不會誤觸發。再次 confirmed 後 armed 恢復有效（CDP 線全天不變）。
- **VWAP 欄位覆蓋**：VWAP 只在有 settled candle 後才有值。第一根 candle 前 VWAP 為 None → `require_below_vwap` 不影響（造山也需至少幾根 candle 才確認）。

## 反身性審查紀錄

1. tolerance_pct 在流程圖遺漏 — 已補
2. re-surge + armed 殘留 — 安全，不需修正
3. 同檔多 CDP 線同時觸發 — by design，跟既有 evaluator 一致
4. CDP 中軸可能較雜訊（20% 股票收盤在 CDP±1%）— 留給 GA 搜索

## 不做的事

- 不做多方訊號（留給策略 B）
- 不改造山積木參數
- 不加 VWAP 到前端顯示（只在引擎內計算）
- 不存 VWAP 到 DB
- 不改 Discord 推播格式（複用現有 signal fanout）
