# 策略 B — 造山後無力緩跌跌破支撐（做空/出場訊號）設計

> 造山積木確認做頭後，價格持續弱勢下滑（drift），最終跌破 CDP 支撐線 → 做空/出場訊號。

## 前提

造山積木 v4 已在 signal_engine 內建，`_mountain_state[symbol]["phase"]` 自動追蹤。
策略 A（mountain_bounce）抓「碰壓力站不上」；策略 B 抓「持續弱勢跌破支撐」。
兩者消費同一個 `_mountain_state` confirmed phase，可同時 armed、不互斥。

## 觸發條件（全部 AND）

1. **造山 `phase == "confirmed"`**（同一檔、當天內）
2. **Drift 弱勢確認**：近 `drift_bars` 根 settled candle 中，≥ `ceil(drift_bars × drift_ratio)` 根 close < prev_close
3. **跌破支撐線**：`candle.close < CDP_level × (1 - tolerance_pct/100)`
4. **跌破確認**：連續 `break_confirm_bars` 根 close 在線下
5. **（可選）close < VWAP**（`require_below_vwap`，預設 false）

## 跟其他策略的邊界

| 場景 | 策略 | 區分邏輯 |
|------|------|----------|
| 造山後反彈碰壓力站不上 | A (mountain_bounce) | 碰壓力線 + close < 壓力線 |
| 造山後持續弱勢跌破支撐 | **B (mountain_drift_break)** | drift 弱勢 + close < 支撐線 |
| 5 分鐘急跌 > 2% | 策略 3 (window_conditions) | price_change_pct，無造山前提 |
| 造山偵測本身 | 造山積木 v4 (自動) | 引擎內建 |

drift 觀察窗口 ≥ 3 根（最少 3 分鐘），天然排除策略 3 的瞬間爆殺。

## 兩階段狀態機

```
造山 confirmed
  │
  ├─ 每根 settled candle: 維護 drift 滑動窗口
  │   drift_window = 最近 drift_bars 根的 (close < prev_close) 統計
  │   drift_down_count >= ceil(drift_bars × drift_ratio)?
  │   ├─ yes → DRIFT_OK（弱勢確認）
  │   └─ no  → DRIFT_NOT_OK（繼續觀察）
  │
  └─ DRIFT_OK + per-level 支撐線檢查:
      candle.close < CDP_level × (1 - tolerance_pct/100)?
      ├─ yes → break_confirm_count += 1
      │        >= break_confirm_bars? → FIRE signal
      └─ no  → break_confirm_count = 0（站回線上,重置）
```

注意：drift 判定是每根 candle 都重新計算（滑動窗口），不是一次 arm 就鎖定。
這意味著 drift 可能在某根 candle 成立、下一根又不成立（反彈一根拉高 ratio）。
break_confirm_count 只在 drift 成立 + close 在線下時才累加。

## VWAP 整合

複用策略 A 已實作的 `_update_vwap()` 和 `_field_cache[symbol]["vwap"]`。
`require_below_vwap=true` 時，drift 成立但 close >= VWAP → 不進入跌破確認。

## Pydantic Model

```python
class MountainDriftBreakStrategy(BaseModel):
    """策略 B：造山確認 + drift 弱勢 + 跌破 CDP 支撐線 → 做空訊號。"""

    type: Literal["mountain_drift_break"]
    levels: list[CdpLevel] = Field(
        default_factory=lambda: ["nl", "al", "cdp"], min_length=1,
    )
    drift_bars: int = Field(default=8, ge=3, le=20)
    drift_ratio: float = Field(default=0.6, ge=0.4, le=0.9)
    break_confirm_bars: int = Field(default=2, ge=1, le=5)
    tolerance_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    require_below_vwap: bool = False
```

加入 `StrategyConfig` 聯合型別，`schema_version` 升到 9。

| 欄位 | 預設 | 說明 |
|------|------|------|
| `levels` | `["nl", "al", "cdp"]` | 要監控的 CDP 支撐線 |
| `drift_bars` | 8 | drift 滑動窗口大小（K 棒數） |
| `drift_ratio` | 0.6 | 窗口內 close < prev_close 的最低比例 |
| `break_confirm_bars` | 2 | 跌破線後連續幾根 close 在線下才觸發 |
| `tolerance_pct` | 0.0 | 跌破容差 |
| `require_below_vwap` | false | 是否要求 close < VWAP |

## Evaluator

```python
def _eval_mountain_drift_break(
    self, strat: dict, active: ActiveSignalOut, symbol: str,
    candle: MinuteCandle, now: float,
) -> dict | None:
```

走 candle 路徑（同策略 A），每根 settled candle 檢查一次。

### 狀態

```python
self._mountain_drift_state: dict[tuple, dict] = {}
# key = (active.id, symbol)
# value = {"prev_close": float, "drift_window": list[bool]}

self._mountain_drift_break_count: dict[tuple, int] = {}
# key = (active.id, symbol, level_name)
# value = break_confirm_count
```

- `drift_window`：滑動窗口，每根 candle append `(close < prev_close)`，保留最近 `drift_bars` 個
- `prev_close`：追蹤前一根 candle 的 close
- `break_confirm_count`：per-level 跌破確認計數

### 邏輯流程

1. 檢查 `_mountain_state[symbol]["phase"] == "confirmed"`，否則 return None
2. 更新 drift_window：`drift_window.append(candle.close < prev_close)`，trim 到 drift_bars
3. 如果 drift_window 長度 < drift_bars，更新 prev_close，return None（窗口不足）
4. 計算 `drift_down_count = sum(drift_window)`
5. `drift_ok = drift_down_count >= ceil(drift_bars × drift_ratio)`
6. 如果 `require_below_vwap` 且 close >= VWAP → drift_ok = False
7. 遍歷每個 level：
   - 如果 `not drift_ok` → 重置該 level 的 break_confirm_count = 0，continue
   - 如果 `candle.close < CDP_level × (1 - tolerance_pct/100)` → break_confirm_count += 1
   - 否則 → break_confirm_count = 0
   - 如果 break_confirm_count >= break_confirm_bars → FIRE，重置 count
8. 更新 prev_close

### Signal output

```python
{
    "level": "nl",
    "direction": "from_above",
    "role": "mountain_drift_break",
    "drift_bars_used": 8,
    "drift_down_count": 6,
    "break_confirm": 2,
    "peak_high": 105.3,
    "below_vwap": True,
}
```

`role: "mountain_drift_break"` 供 Discord 推播和前端訊號卡片顯示。

## Wiring

### signal_engine.py `_evaluate()`

在 strategy dispatch 加：

```python
elif stype == "mountain_drift_break":
    if settled is None:
        continue
    cdp_touch = self._eval_mountain_drift_break(strat, active, symbol, settled, now)
    ma_touch = None
    ok = cdp_touch is not None
```

### cooldown

跟策略 A 一致，per-level cooldown：

```python
elif stype in ("cdp_breakout_confirm", "mountain_bounce", "mountain_drift_break"):
    touch_level = (cdp_touch or {}).get("level", "")
```

### touch_count

跟策略 A 一致，不計入碰線觸碰次數：

```python
if stype not in ("cdp_breakout_confirm", "peak_divergence", "mountain_bounce", "mountain_drift_break"):
```

### 收盤清除

`_mountain_drift_state` 和 `_mountain_drift_break_count` 加入 `_reset_daily_strategy_state()`。

## GA 搜索（回測腳本）

新增 `_ga_strategy_b.py`，基於 `_ga_strategy_a.py` 改寫。

染色體 6 基因：

| 基因 | 範圍 | 步長 | 值數 |
|------|------|------|------|
| `drift_bars` | 3-15 | 1 | 13 |
| `drift_ratio` | 0.4-0.9 | 0.1 | 6 |
| `break_confirm_bars` | 1-4 | 1 | 4 |
| `require_below_vwap` | 0/1 | — | 2 |
| `levels_combo` | 7 組合 | — | 7 |
| `tolerance_pct` | 0.0-0.5 | 0.1 | 6 |

搜索空間 = 13 × 6 × 4 × 2 × 7 × 6 = **26,208** 組合。

levels 組合（支撐線為主，含壓力線探索）：
- NL only / AL only / CDP only
- NL+AL / NL+CDP / AL+CDP
- NL+AL+CDP

GA 也可擴充加 NH/AH 組合（高位股跌破壓力線場景），但預設 7 組合先聚焦支撐。

適應度 = 獲利訊號數 × abs(平均跌幅) − α × 虧損訊號數（同策略 A）。

建議 GA 參數：pop=60, gen=120（搜索空間比策略 A 大 ~78 倍）。

## 改動位置

| 檔案 | 改動 |
|------|------|
| `backend/models/condition.py` | 新增 `MountainDriftBreakStrategy`，加入 `StrategyConfig`，`schema_version` → 9 |
| `backend/services/signal_engine.py` | 新增 `_eval_mountain_drift_break()`、drift 狀態管理、wiring、收盤清除 |
| `backend/tests/test_strategy_b_mountain_drift_break.py` | 新增測試 |
| `backend/scripts/_ga_strategy_b.py` | GA 回測腳本 |

## 行為細節

- **同檔策略 A + B 共存**：同一檔造山確認後，A 和 B 可以同時 armed。A 先觸發（碰壓力無力）→ B 後觸發（續跌破支撐）是合理場景。兩者 cooldown 獨立（不同 rule）。
- **drift 窗口在造山確認前不累積**：只有 `phase == "confirmed"` 後才開始記錄 drift_window。避免造山前的波動被計入。
- **re-surge 後重置**：造山 re-surge（phase → surge_tracking）時，evaluator 每根都先檢查 `phase == "confirmed"`。非 confirmed 時 return None，drift 窗口不推進。再次 confirmed 後 drift_window 可能有殘留 — 但因為 re-surge 期間沒 append，窗口長度不足 drift_bars，自然不會誤觸發。
- **fired per-level**：跟策略 A 不同，不加 fired set。同一 level 可以重複觸發（靠 cooldown 控頻率）。理由：支撐跌破後可能短暫站回再跌，多次跌破有加碼意義。
- **prev_close 初始化**：第一根 candle 時沒有 prev_close，drift_window 不 append。需要 drift_bars + 1 根 candle 才有完整窗口。

## 反身性審查紀錄

1. **drift 跟爆殺重疊** — drift_bars ≥ 3 保證觀察窗口 ≥ 3 分鐘，天然排除 5 分鐘急跌的策略 3 場景。但邊界 case：drift_bars=3 + drift_ratio=1.0 = 3 根連跌，仍可能跟短急跌重疊。可接受，讓 GA 和回測數據決定。
2. **CDP 中軸在 A/B 角色不同** — A 視 CDP 為壓力（碰不過），B 視 CDP 為支撐（跌破）。同一檔同天兩者都可能觸發，邏輯正確。
3. **VWAP 在 B 場景鑑別力可能低** — 緩跌場景 close 通常已在 VWAP 下。作為可選參數讓 GA 判斷。預設 false。
4. **drift_window 在 re-surge 後殘留** — 安全。re-surge 期間不 append，長度不足自然不觸發。再次 confirmed 後重新累積。
5. **per-level 不加 fired set** — 與策略 A 不同。A 碰壓力無力是一次性判斷，B 跌破支撐可重複（反覆測試支撐意義不同）。cooldown 防洗版。

## 不做的事

- 不做多方訊號
- 不改造山積木參數
- 不加 VWAP 到前端顯示
- 不改 Discord 推播格式（複用現有 signal fanout）
- 不加時間窗口限制（先不限造山確認後多久可觸發，回測觀察）
