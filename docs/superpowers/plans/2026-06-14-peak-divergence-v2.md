# 策略5 v2 雙峰急拉量價背離造山(不對稱閘門)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重寫策略5 為「主峰急拉啟動 + 次峰出量弱反彈量價背離」的不對稱狀態機,翻轉 v1 的開盤假觸發缺陷。

**Architecture:** 一個 evaluator method `_eval_peak_divergence`(per-symbol `_peak_state` 狀態機 watch→retrace→confirmed)+ 抽出可重用積木 `_detect_surge`(陡升+出量,**只服務主峰啟動**)。次峰走較鬆的「出量反彈」門檻(`peak2_volume_ratio`,不要求陡升)——做頭轉弱的第二峰本質是無力反彈,陡升門檻會擋掉真做頭(實證見 spec 附錄二)。

**Tech Stack:** Python 3.13 / pydantic v2(`backend/models/condition.py`)/ FastAPI 引擎(`backend/services/signal_engine.py`)/ pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-14-peak-divergence-v2-surge-design.md`(已就地改為不對稱版,2026-06-14 使用者拍板)。

**驗收硬標準(spec):** 用 `_diag_cache.json` 真引擎跑 6/12 四檔 — **6207 觸發**(主峰≈134、次峰≈133、10:xx)、**8064 / 8150 / 6239 全排除**。

**環境前提:**
- 所有 pytest 在 `backend/` 下用 venv 跑:`Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest ...`
- **改 `signal_engine.py` 前先停 backend dev server**(`uvicorn --reload` 會每次存檔重啟+重登富邦 → 登入風暴被拒)。
- PowerShell 串接用分號 + `if($?)`,不用 `&&`。繁體中文回覆。

---

## File Structure

| 檔案 | 責任 | 動作 |
|---|---|---|
| `backend/models/condition.py` | `PeakDivergenceStrategy` DSL schema | 改:欄位全換、schema_version 7→8 |
| `backend/services/signal_engine.py` | 訊號引擎 evaluator | 改:重寫 `_eval_peak_divergence`、新增 `_detect_surge`;`_peak_state` 欄位換 |
| `backend/tests/test_condition_strategy.py` | strategy schema 單測 | 改:schema 斷言 + peak 預設斷言 |
| `backend/tests/test_condition_model.py` | filter schema 單測 | 改:schema_version 斷言 |
| `backend/tests/test_condition_breakout_confirm.py` | breakout schema 單測 | 改:schema_version 斷言(連帶) |
| `backend/tests/test_signal_engine_peak_divergence.py` | 策略5 引擎單測/整合測 | 重寫 |
| `backend/scripts/replay_engine.py` | 回測 harness | 改:`peak_rule` 簽名 + `run_peak` 掃描軸 |
| `backend/scripts/_diag_acceptance.py` | 回測驗收(untracked) | 建立:真引擎跑 cache 4 檔 |
| bot 圖卡(`bot/`) | distribution 圖卡 | **不動**(metadata 格式沿用 v1) |

依賴順序:Task 1(schema)→ Task 2(`_detect_surge`)→ Task 3(狀態機+單測)→ Task 4(整合測)→ Task 5(replay)→ Task 6(回測驗收)。

---

## Task 1: 換 PeakDivergenceStrategy schema(7→8)

**Files:**
- Modify: `backend/models/condition.py:209-223`(`PeakDivergenceStrategy` + `StrategyConfig`)、`:233`(schema_version)
- Test: `backend/tests/test_condition_strategy.py:31-43`、`backend/tests/test_condition_model.py:90-94`、`backend/tests/test_condition_breakout_confirm.py:33`

- [ ] **Step 1: 改連帶 schema 斷言測試(先讓測試表達新意圖)**

`backend/tests/test_condition_strategy.py` — 取代 `test_schema_version_bumped_to_7` 與 `test_peak_divergence_strategy_defaults`:

```python
def test_schema_version_bumped_to_8():
    assert ActiveFilter(strategy=LimitUpOpenTouchStrategy(type="limit_up_open_touch")).schema_version == 8


def test_peak_divergence_strategy_defaults():
    from models.condition import PeakDivergenceStrategy
    f = ActiveFilter(strategy=PeakDivergenceStrategy(type="peak_divergence"))
    assert f.strategy.surge_pct == 2.0
    assert f.strategy.surge_window_bars == 3
    assert f.strategy.surge_volume_ratio == 2.5
    assert f.strategy.peak2_volume_ratio == 2.0
    assert f.strategy.pullback_pct == 1.0
    assert f.strategy.volume_shrink_ratio == 0.6
    assert f.strategy.not_exceed_tolerance_pct == 0.0
    assert f.strategy.max_gap_minutes == 120
    assert not hasattr(f.strategy, "min_main_peak_volume_ratio")  # v1 欄位已移除
    assert f.conditions == []
```

`backend/tests/test_condition_model.py:90-94` — 改 `test_active_filter_schema_bumps_to_7`:

```python
def test_active_filter_schema_bumps_to_8():
    from models.condition import ActiveFilter
    f = ActiveFilter(conditions=[Condition(field="close", operator="gt", value=100)])
    assert f.schema_version == 8  # 7→8:peak_divergence v2 欄位大改
    assert f.ma_proximity is None
```

`backend/tests/test_condition_breakout_confirm.py:33` — 改斷言值:

```python
    assert f.schema_version == 8
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_condition_strategy.py tests/test_condition_model.py tests/test_condition_breakout_confirm.py -q`
Expected: FAIL(`surge_pct` AttributeError / schema_version 仍是 7)

- [ ] **Step 3: 換 `PeakDivergenceStrategy` 欄位**

`backend/models/condition.py` — 取代 `class PeakDivergenceStrategy`(原 209-217):

```python
class PeakDivergenceStrategy(BaseModel):
    """策略 5:雙峰急拉量價背離造山(做頭轉弱)。

    主峰由「急拉根」(陡升+出量)啟動;次峰只需「出量反彈」(不要求陡升)、不過前高、
    量縮、滾頭 → 觸發。次峰不要求陡升:做頭第二峰本質是無力反彈,陡升門檻會擋掉真做頭。
    """

    type: Literal["peak_divergence"]
    surge_pct: float = Field(default=2.0, gt=0, le=20)
    surge_window_bars: int = Field(default=3, ge=1, le=20)
    surge_volume_ratio: float = Field(default=2.5, ge=0.5, le=20.0)
    peak2_volume_ratio: float = Field(default=2.0, ge=0.5, le=20.0)
    pullback_pct: float = Field(default=1.0, gt=0, le=20)
    # 0.6(非 0.8):0.8 太鬆會放進單峰假訊號(6239 shrink 0.72 誤觸發,見 Task 6 後記 / spec 附錄三)
    volume_shrink_ratio: float = Field(default=0.6, gt=0, le=1.0)
    not_exceed_tolerance_pct: float = Field(default=0.0, ge=0, le=5)
    max_gap_minutes: int = Field(default=120, ge=1, le=240)
```

同檔 `:233` — 改 `ActiveFilter.schema_version` 預設與註解:

```python
    schema_version: int = 8  # 7→8,peak_divergence v2 不對稱閘門(欄位大改)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_condition_strategy.py tests/test_condition_model.py tests/test_condition_breakout_confirm.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
Set-Location C:\side-project\treading-king; git add backend/models/condition.py backend/tests/test_condition_strategy.py backend/tests/test_condition_model.py backend/tests/test_condition_breakout_confirm.py; git commit -m @'
feat(strategy): 策略5 v2 schema 換不對稱閘門欄位(7→8)

PeakDivergenceStrategy 改 surge_pct/surge_window_bars/surge_volume_ratio
(主峰急拉)+ peak2_volume_ratio(次峰出量,不要求陡升);移除 min_main_peak_volume_ratio。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

---

## Task 2: 新增 `_detect_surge` helper(急拉偵測積木)

**Files:**
- Modify: `backend/services/signal_engine.py`(在 `_eval_peak_divergence` 上方新增 method)
- Test: `backend/tests/test_signal_engine_peak_divergence.py`(本 task 先單獨建立 `_detect_surge` 的測試區塊)

> `_detect_surge` 簽名:`recent_closes` 含當根(最後一個 = candle.close),長度需 ≥ `surge_window_bars + 1` 才有「W 根前」可比。陡升用 close(收在高才是真拉);出量用既有 `_candle_volume_ratio`。

- [ ] **Step 1: 寫 `_detect_surge` 失敗測試**

`backend/tests/test_signal_engine_peak_divergence.py` — 先全檔換成下列「Task 2 區塊」(Task 3 會再 append 狀態機測試)。注意:`_day_volume=3000` 且 `now=MORNING`(09:30,開盤後 30 分)→ 每分鐘均量 = 3000/30 = 100/min,故 `vr = candle.volume / 100`。

```python
"""驗策略 5 v2:雙峰急拉量價背離造山(不對稱閘門,吃結算 1 分 K candle)。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from models.condition import ActiveFilter, ActiveSignalOut, PeakDivergenceStrategy
from services.ring_buffer import Tick
from services.signal_engine import MinuteCandle, SignalEngine

TZ = timezone(timedelta(hours=8))
MORNING = datetime(2026, 6, 15, 9, 30, tzinfo=TZ).timestamp()   # 週一開盤後 30 分 → 均量 = day_vol/30


def _candle(high, close, volume=100, minute=0, low=None, open_=None):
    o = open_ if open_ is not None else close
    lo = low if low is not None else min(o, close, high)
    return MinuteCandle(minute=minute, open=o, high=high, low=lo, close=close, volume=volume)


def _strat(surge_pct=2.0, surge_window_bars=3, surge_volume_ratio=2.5):
    return {"surge_pct": surge_pct, "surge_window_bars": surge_window_bars,
            "surge_volume_ratio": surge_volume_ratio}


# ---- _detect_surge 積木單測(陡升 AND 出量) ----

def test_detect_surge_steep_and_high_volume_true():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000                       # 均量 100/min @09:30
    # 3 根前 close=100,當根 close=103(漲 3% ≥ 2%),vol=300 → vr=3.0 ≥ 2.5
    candle = _candle(high=103, close=103, volume=300)
    assert engine._detect_surge("2330", candle, [100, 100, 100, 103], MORNING, _strat()) is True


def test_detect_surge_steep_but_low_volume_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=103, close=103, volume=50)        # vr=0.5 < 2.5
    assert engine._detect_surge("2330", candle, [100, 100, 100, 103], MORNING, _strat()) is False


def test_detect_surge_high_volume_but_not_steep_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=100.5, close=100.5, volume=300)   # 漲 0.5% < 2%
    assert engine._detect_surge("2330", candle, [100, 100, 100, 100.5], MORNING, _strat()) is False


def test_detect_surge_insufficient_history_false():
    engine = SignalEngine()
    engine._day_volume["2330"] = 3000
    candle = _candle(high=103, close=103, volume=300)
    assert engine._detect_surge("2330", candle, [100, 103], MORNING, _strat()) is False  # len 2 ≤ w
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py -q`
Expected: FAIL(`AttributeError: 'SignalEngine' object has no attribute '_detect_surge'`)

- [ ] **Step 3: 實作 `_detect_surge`**

`backend/services/signal_engine.py` — 在 `_eval_peak_divergence` 定義上方新增:

```python
    def _detect_surge(
        self, symbol: str, candle: MinuteCandle, recent_closes: list[float],
        now: float, strat: dict,
    ) -> bool:
        """急拉根判定(主峰啟動用):同時滿足陡升 + 出量才回 True。

        陡升:close 相對 surge_window_bars 根前 close 漲幅 ≥ surge_pct%(用 close 不用 high,
              收在高才是真拉、避開長上影線假突破)。
        出量:_candle_volume_ratio(這根) ≥ surge_volume_ratio。
        recent_closes 含當根(最後一個 = candle.close),需 ≥ surge_window_bars+1 根才有可比基準。
        次峰不經此 helper(走較鬆的出量門檻),理由見 PeakDivergenceStrategy docstring。
        """
        w = strat["surge_window_bars"]
        if len(recent_closes) <= w:
            return False
        base = recent_closes[-(w + 1)]
        if base <= 0:
            return False
        rise_pct = (recent_closes[-1] / base - 1) * 100
        if rise_pct < strat["surge_pct"]:
            return False
        return self._candle_volume_ratio(symbol, candle, now) >= strat["surge_volume_ratio"]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```powershell
Set-Location C:\side-project\treading-king; git add backend/services/signal_engine.py backend/tests/test_signal_engine_peak_divergence.py; git commit -m @'
feat(engine): 抽 _detect_surge 急拉偵測積木(陡升+出量)

策略5 v2 主峰啟動用;6239 單峰/未來做頭家族可複用。次峰不經此 helper。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

---

## Task 3: 重寫 `_eval_peak_divergence` 狀態機 + 單元測試

**Files:**
- Modify: `backend/services/signal_engine.py:558-629`(整個 `_eval_peak_divergence`)
- Test: `backend/tests/test_signal_engine_peak_divergence.py`(append 狀態機測試)

> 統一測試 setup:`engine._day_volume["2330"]=3000`、`now=MORNING+minute*60`。`_feed` 逐根餵 candle。懸殊 volume 讓 vr 門檻清楚分離(大量根 vr~20、次峰出量根 vr~3、雜訊根 vr~0.1)。

- [ ] **Step 1: append 狀態機單元測試**

`backend/tests/test_signal_engine_peak_divergence.py` — 在 Task 2 區塊**之後** append:

```python
# ---- 狀態機測試 setup ----

def _active(surge_pct=2.0, surge_window_bars=3, surge_volume_ratio=2.5, peak2_volume_ratio=2.0,
            pullback_pct=1.0, volume_shrink_ratio=0.8, not_exceed_tolerance_pct=0.0,
            max_gap_minutes=120):
    return ActiveSignalOut(
        id="pk", name="造山",
        filter_json=ActiveFilter(strategy=PeakDivergenceStrategy(
            type="peak_divergence", surge_pct=surge_pct, surge_window_bars=surge_window_bars,
            surge_volume_ratio=surge_volume_ratio, peak2_volume_ratio=peak2_volume_ratio,
            pullback_pct=pullback_pct, volume_shrink_ratio=volume_shrink_ratio,
            not_exceed_tolerance_pct=not_exceed_tolerance_pct, max_gap_minutes=max_gap_minutes,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at="2026-06-14", notify_discord=False,
    )


def _feed(engine, active, candles):
    """逐根餵 candle,回最後一根結果。now = MORNING + minute*60;day_volume 固定 3000。"""
    engine._day_volume["2330"] = 3000
    strat = engine._strategy_of(active)
    last = None
    for c in candles:
        last = engine._eval_peak_divergence(strat, active, "2330", c, MORNING + c.minute * 60)
    return last


# 共用前綴:3 根暖機(湊滿 recent_closes)+ 主峰急拉根(m3, close103 漲 3%、vol2000 → vr~22)
_WARMUP = [
    _candle(high=100, close=100, volume=10, minute=0),
    _candle(high=100, close=100, volume=10, minute=1),
    _candle(high=100, close=100, volume=10, minute=2),
]


def test_main_peak_locked_only_by_surge():
    # 急拉根 → 鎖主峰
    engine = SignalEngine()
    active = _active()
    _feed(engine, active, _WARMUP + [_candle(high=103, close=103, volume=2000, minute=3)])
    st = engine._peak_state[(active.id, "2330")]
    assert st["surge_seen"] is True
    assert st["peak1_high"] == 103


def test_high_volume_but_not_steep_does_not_lock_main_peak():
    # 出量但不陡(漲 0.5%)→ 不算急拉、不鎖主峰
    engine = SignalEngine()
    active = _active()
    _feed(engine, active, _WARMUP + [_candle(high=100.5, close=100.5, volume=2000, minute=3)])
    st = engine._peak_state[(active.id, "2330")]
    assert st["surge_seen"] is False
    assert st["peak1_high"] == 0.0


def test_steep_but_no_volume_does_not_lock_main_peak():
    # 陡(漲 3%)但無量 → 不算急拉、不鎖主峰
    engine = SignalEngine()
    active = _active()
    _feed(engine, active, _WARMUP + [_candle(high=103, close=103, volume=10, minute=3)])
    st = engine._peak_state[(active.id, "2330")]
    assert st["surge_seen"] is False
    assert st["peak1_high"] == 0.0


def test_main_peak_tracks_high_on_non_surge_bar_after_start():
    # 急拉啟動後,後一根「非急拉根」(無量)但創更高 → 主峰仍追到那根的 high
    engine = SignalEngine()
    active = _active()
    _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),   # 急拉啟動,peak1=103
        _candle(high=105, close=104, volume=10, minute=4),     # 無量(非急拉)但 high105 創高 → 追到 105
    ])
    st = engine._peak_state[(active.id, "2330")]
    assert st["surge_seen"] is True
    assert st["peak1_high"] == 105                             # 追到非急拉根的高
    assert st["phase"] == "watch"                              # close104 > 105*0.99 未封頂


def test_asymmetric_double_peak_with_shrink_fires():
    # 主峰急拉(vr~22)→ 封頂 → 次峰出量弱反彈(vr~3,不過前高)→ 滾頭 + 量縮 → 觸發
    engine = SignalEngine()
    active = _active()
    r = _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),    # 主峰急拉
        _candle(high=103, close=101, volume=10, minute=4),      # close101 < 103*0.99 → 封頂進 retrace
        _candle(high=102.5, close=102.5, volume=300, minute=5), # 次峰出量(vr~3.5≥2.0)不過前高
        _candle(high=102.5, close=101, volume=10, minute=6),    # 滾頭 close101 < 102.5*0.99 → 量縮觸發
    ])
    assert r is not None
    assert r["level"] == "peak"
    assert r["direction"] == "from_above"
    assert r["role"] == "distribution"
    assert r["main_peak_price"] == 103
    assert r["second_peak_price"] == 102.5
    assert r["volume_shrink"] < active.filter_json.strategy.volume_shrink_ratio  # 量縮成立(<0.8)


def test_second_peak_exceeds_prior_high_becomes_new_main():
    # 次峰出量反彈過前高 → 不是做頭、升級新主峰、回 watch
    engine = SignalEngine()
    active = _active()
    r = _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),    # 主峰 103
        _candle(high=103, close=101, volume=10, minute=4),      # 封頂 retrace
        _candle(high=105, close=105, volume=300, minute=5),     # 出量反彈 high105 ≥ 103 → 升級新主峰
    ])
    assert r is None
    st = engine._peak_state[(active.id, "2330")]
    assert st["phase"] == "watch"
    assert st["peak1_high"] == 105


def test_second_peak_volume_not_shrunk_no_fire():
    # 次峰不過前高但量沒縮(次峰 vr ≈ 主峰 vr)→ 不觸發
    engine = SignalEngine()
    active = _active()
    r = _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=400, minute=3),     # 主峰 vr~4.4
        _candle(high=103, close=101, volume=10, minute=4),      # 封頂
        _candle(high=102.5, close=102.5, volume=400, minute=5), # 次峰 vr~4.7 ≥ 主峰*0.8 → 沒縮
        _candle(high=102.5, close=101, volume=10, minute=6),    # 滾頭但量沒縮 → 不觸發
    ])
    assert r is None


def test_low_volume_rebound_not_counted_as_second_peak():
    # 回測中只有「無量小反彈」(vr < peak2_volume_ratio)→ 不算次峰 → 不觸發
    engine = SignalEngine()
    active = _active()
    r = _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),    # 主峰急拉
        _candle(high=103, close=101, volume=10, minute=4),      # 封頂
        _candle(high=102, close=102, volume=10, minute=5),      # 無量小彈 vr~0.1 < 2.0 → 不算次峰
        _candle(high=102, close=100, volume=10, minute=6),      # 續跌,無次峰可滾頭
    ])
    assert r is None
    assert engine._peak_state[(active.id, "2330")]["peak2_high"] == 0.0


def test_max_gap_minutes_abandons_stale_main_peak():
    # 主峰後超過 max_gap_minutes 才出現次峰 → 放棄、回 watch
    engine = SignalEngine()
    active = _active(max_gap_minutes=5)
    _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),    # 主峰 minute=3
        _candle(high=103, close=101, volume=10, minute=4),      # 封頂 retrace
        _candle(high=102.5, close=102.5, volume=300, minute=10),# minute 10-3=7 > 5 → 放棄回 watch
    ])
    assert engine._peak_state[(active.id, "2330")]["phase"] == "watch"


def test_confirmed_latches_no_repeat():
    # 觸發後 phase=confirmed,後續 candle 不再觸發
    engine = SignalEngine()
    active = _active()
    _feed(engine, active, _WARMUP + [
        _candle(high=103, close=103, volume=2000, minute=3),
        _candle(high=103, close=101, volume=10, minute=4),
        _candle(high=102.5, close=102.5, volume=300, minute=5),
        _candle(high=102.5, close=101, volume=10, minute=6),    # 觸發 → confirmed
    ])
    r2 = _feed(engine, active, [_candle(high=102.5, close=99, volume=10, minute=7)])
    assert r2 is None
    assert engine._peak_state[(active.id, "2330")]["phase"] == "confirmed"


def test_daily_reset_clears_peak_state():
    engine = SignalEngine()
    engine._peak_state[("x", "2330")] = {"phase": "retrace"}
    engine._reset_daily_strategy_state()
    assert engine._peak_state == {}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py -q`
Expected: FAIL(v1 `_eval_peak_divergence` 不認 `surge_seen`/`recent_closes`/`peak2_volume_ratio`,多個 assert 失敗 / KeyError)

- [ ] **Step 3: 重寫 `_eval_peak_divergence`**

`backend/services/signal_engine.py` — 整個取代 `_eval_peak_divergence`(原 558-629):

```python
    def _eval_peak_divergence(
        self, strat: dict, active: ActiveSignalOut, symbol: str,
        candle: MinuteCandle, now: float,
    ) -> dict | None:
        """策略 5 v2:雙峰急拉量價背離造山(做頭轉弱)。每根結算 candle 推進 per-symbol 狀態機。

        主峰:由「急拉根」(_detect_surge:陡升+出量)啟動;啟動後追這波最高 high / 最大量比,
              close 回落 pullback_pct 確認封頂、進 retrace。
        次峰:回測中「出量反彈」(_candle_volume_ratio ≥ peak2_volume_ratio,**不要求陡升**)的
              反彈高點;不過前高 + 量縮(peak2_vr < peak1_vr × volume_shrink_ratio)+ close 滾頭
              → 觸發「做頭轉弱」、當日 latch。次峰不要求陡升:第二峰本質是無力反彈(見 spec 附錄二)。
        量基準統一用 _candle_volume_ratio(到當下每分鐘均量的倍數);承擔 day_volume 盤中重啟偏誤(同 v1)。
        """
        key = (active.id, symbol)
        st = self._peak_state.get(key)
        if st is None:
            st = {"phase": "watch", "surge_seen": False, "recent_closes": [],
                  "peak1_high": 0.0, "peak1_vr": 0.0, "peak1_minute": candle.minute,
                  "peak2_high": 0.0, "peak2_vr": 0.0}
            self._peak_state[key] = st

        # recent_closes 含當根、只留最近 W+1 根(供 _detect_surge 算陡升);先維護再判定
        rc = st["recent_closes"]
        rc.append(candle.close)
        del rc[:-(strat["surge_window_bars"] + 1)]

        pullback = strat["pullback_pct"] / 100.0
        tol = strat["not_exceed_tolerance_pct"] / 100.0
        shrink = strat["volume_shrink_ratio"]
        max_gap = strat["max_gap_minutes"]
        peak2_vr_min = strat["peak2_volume_ratio"]

        vr = self._candle_volume_ratio(symbol, candle, now)
        is_surge = self._detect_surge(symbol, candle, rc, now, strat)

        if st["phase"] == "watch":
            if is_surge:
                st["surge_seen"] = True
            # 急拉啟動後,持續追這波最高 high / 最大量比(不限急拉根本身那一根)
            if st["surge_seen"]:
                if candle.high > st["peak1_high"]:
                    st["peak1_high"] = candle.high
                    st["peak1_minute"] = candle.minute
                st["peak1_vr"] = max(st["peak1_vr"], vr)
            if st["peak1_high"] > 0 and candle.close < st["peak1_high"] * (1 - pullback):
                st["phase"] = "retrace"
                st["surge_seen"] = False
                st["peak2_high"] = 0.0
                st["peak2_vr"] = 0.0
            return None

        if st["phase"] == "retrace":
            if candle.minute - st["peak1_minute"] > max_gap:
                st["phase"] = "watch"
                return None
            # 次峰候選 = 出量反彈高點(只要出量,不要求陡升)
            if vr >= peak2_vr_min and candle.high > st["peak2_high"]:
                st["peak2_high"] = candle.high
                st["peak2_vr"] = max(st["peak2_vr"], vr)
            if st["peak2_high"] >= st["peak1_high"] * (1 + tol):
                # 出量反彈過前高 → 不是做頭,次峰升級為新主峰、回 watch 續找
                st["peak1_high"] = st["peak2_high"]
                st["peak1_vr"] = st["peak2_vr"]
                st["peak1_minute"] = candle.minute
                st["phase"] = "watch"
                st["surge_seen"] = True
                st["peak2_high"] = 0.0
                st["peak2_vr"] = 0.0
                return None
            if st["peak2_high"] > 0 and candle.close < st["peak2_high"] * (1 - pullback):
                if st["peak2_vr"] < st["peak1_vr"] * shrink:
                    st["phase"] = "confirmed"
                    return {
                        "level": "peak", "direction": "from_above", "role": "distribution",
                        "main_peak_price": st["peak1_high"],
                        "second_peak_price": st["peak2_high"],
                        "volume_shrink": (round(st["peak2_vr"] / st["peak1_vr"], 2)
                                          if st["peak1_vr"] else 0.0),
                    }
                # 量沒縮:這波反彈不算背離,重置次峰候選續找
                st["peak2_high"] = 0.0
                st["peak2_vr"] = 0.0
            return None

        return None  # confirmed:當日 latch
```

- [ ] **Step 4: 跑測試確認通過**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py -q`
Expected: PASS(15 passed:4 個 `_detect_surge` + 11 個狀態機)

- [ ] **Step 5: Commit**

```powershell
Set-Location C:\side-project\treading-king; git add backend/services/signal_engine.py backend/tests/test_signal_engine_peak_divergence.py; git commit -m @'
feat(engine): 重寫策略5 _eval_peak_divergence 為不對稱急拉狀態機

主峰急拉啟動+追高;次峰出量弱反彈(peak2_volume_ratio,不要求陡升)
+不過前高+量縮+滾頭。翻轉 v1 開盤假觸發缺陷。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

---

## Task 4: 整合測試(`_evaluate` → fanout 帶 distribution)

**Files:**
- Test: `backend/tests/test_signal_engine_peak_divergence.py`(append 整合測)

> 整合測走完整 `_evaluate`(candle 結算 + `_day_volume` 自動累積 + dispatch + `_fanout`)。逐 tick 餵,每分鐘一筆收盤 tick;跨分鐘結算前一根,最後多餵一筆把觸發根結算出來。`_day_volume` 由 `_evaluate` 自行累加(不手動 set),故 tick.size 要夠大讓主峰那根 vr 過門檻。

- [ ] **Step 1: 寫整合失敗測試**

`backend/tests/test_signal_engine_peak_divergence.py` — append:

```python
@pytest.mark.asyncio
async def test_evaluate_fires_double_peak_through_fanout():
    """整合:逐 tick 跨分鐘結算 candle → _evaluate → fanout payload 帶 distribution。"""
    engine = SignalEngine()
    active = _active()
    engine._active = [active]
    engine._field_cache["2330"] = {}            # scope 閘門:monitor symbol 一律建 entry
    fired = []

    async def fake_broadcast(payload):
        fired.append(payload)

    # (price, size, minute):每分鐘一筆;跨分鐘結算前一根。size 累積成 _day_volume。
    # m0-m2 暖機湊 recent_closes;m3 主峰急拉(price103 漲3%、size2000);m4 封頂;
    # m5 次峰出量反彈(size300);m6 滾頭結算 m5 並觸發。需第 8 筆(m7)結算 m6。
    ticks = [
        (100.0, 10, 0), (100.0, 10, 1), (100.0, 10, 2),
        (103.0, 2000, 3), (101.0, 10, 4),
        (102.5, 300, 5), (101.0, 10, 6), (100.0, 10, 7),
    ]
    with patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        for price, size, minute in ticks:
            ts = MORNING + minute * 60
            with patch("services.signal_engine.time.time", return_value=ts):
                await engine._evaluate("2330", Tick(price=price, size=size, time=ts))

    assert len(fired) == 1
    assert fired[0]["data"]["cdp_touch"]["role"] == "distribution"
    assert fired[0]["data"]["cdp_touch"]["main_peak_price"] == 103.0
    assert fired[0]["data"]["cdp_touch"]["second_peak_price"] == 102.5
```

- [ ] **Step 2: 跑測試確認結果**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py::test_evaluate_fires_double_peak_through_fanout -q`
Expected: PASS。

> 若 FAIL(`len(fired)==0`):多半是 `_day_volume` 累積後主峰那根 vr 未過 `surge_volume_ratio`(整合測 vr 用「逐 tick 累積的 day_volume / minutes_since_open」,與單元測固定 3000 不同)。用 systematic-debugging:在 `_eval_peak_divergence` 暫印 `vr`/`is_surge` 確認主峰根 vr,再調 m3 的 size(加大)或暖機根 size(減小)使主峰 vr ≥ 2.5、次峰 vr ≥ 2.0。**不要**改門檻遷就測試。

- [ ] **Step 3: 跑全檔測試確認通過**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest tests/test_signal_engine_peak_divergence.py -q`
Expected: PASS(16 passed)

- [ ] **Step 4: Commit**

```powershell
Set-Location C:\side-project\treading-king; git add backend/tests/test_signal_engine_peak_divergence.py; git commit -m @'
test(engine): 策略5 v2 整合測 _evaluate→fanout 帶 distribution

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

---

## Task 5: replay_engine 改新參數(peak_rule / run_peak)

**Files:**
- Modify: `backend/scripts/replay_engine.py:234-246`(`peak_rule`)、`:369-374`(掃描常數)、`:377-409`(`run_peak`)

> 主峰急拉門檻已穩,回測掃描主軸改 **`peak2_volume_ratio` × `volume_shrink_ratio`**(次峰那組才是區分做頭 vs 噪音的關鍵)。`peak_rule` 其餘參數(surge_pct/window/surge_volume_ratio/pullback)用 schema 預設。replay 無單元測,正確性由 Task 6 真引擎驗收。

- [ ] **Step 1: 改 `peak_rule` 簽名**

`backend/scripts/replay_engine.py` — 取代 `peak_rule`(原 234-246):

```python
def peak_rule(peak2_volume_ratio: float, volume_shrink_ratio: float, day: str):
    """雙峰急拉量價背離造山規則(v2 不對稱)— peak preset 用。
    主峰急拉門檻用 schema 預設(surge_pct=2.0/window=3/surge_volume_ratio=2.5);
    掃描主軸 = 次峰出量門檻 × 量縮比。"""
    from models.condition import ActiveFilter, ActiveSignalOut, PeakDivergenceStrategy
    return ActiveSignalOut(
        id="replay", name=f"造山p2={peak2_volume_ratio}vs={volume_shrink_ratio}",
        filter_json=ActiveFilter(strategy=PeakDivergenceStrategy(
            type="peak_divergence", peak2_volume_ratio=peak2_volume_ratio,
            volume_shrink_ratio=volume_shrink_ratio,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
        notify_discord=False,
    )
```

- [ ] **Step 2: 改掃描常數**

`backend/scripts/replay_engine.py` — 取代 peak 掃描常數(原 369-374):

```python
PEAK_P2VRS = [1.5, 2.0, 2.5]
PEAK_SHRINKS = [0.6, 0.8, 1.0]
PEAK_DETAIL_P2VR = 2.0
PEAK_DETAIL_VS = 0.8
assert PEAK_DETAIL_P2VR in PEAK_P2VRS
assert PEAK_DETAIL_VS in PEAK_SHRINKS
```

- [ ] **Step 3: 改 `run_peak` 掃描軸**

`backend/scripts/replay_engine.py` — 取代 `run_peak`(原 377-409):

```python
async def run_peak(days, day_syms, daily, minute, rearm: int):
    """雙峰造山(v2)門檻掃描:peak2_volume_ratio × volume_shrink_ratio 矩陣 + 碰 CDP baseline。"""
    baseline = {}
    for day in days:
        baseline[day] = await replay_day(
            day, day_syms[day], daily, minute, touch_rule(rearm, day))

    detail_fired = {}

    for vs in PEAK_SHRINKS:
        cols = [f"p2={p2}" for p2 in PEAK_P2VRS]
        print(f"\n== volume_shrink_ratio={vs} ==")
        print(f"{'day':<12}{'touch':>9}" + "".join(f"{c:>9}" for c in cols))
        totals = [0] * (1 + len(PEAK_P2VRS))
        for day in days:
            base_count = sum(baseline[day].values())
            row = [base_count]
            for p2 in PEAK_P2VRS:
                f = await replay_day(day, day_syms[day], daily, minute,
                                     peak_rule(p2, vs, day))
                row.append(sum(f.values()))
                if vs == PEAK_DETAIL_VS and p2 == PEAK_DETAIL_P2VR and day == days[-1]:
                    detail_fired = f
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{day:<12}" + "".join(f"{c:>9}" for c in row))
        print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))

    last = days[-1]
    print(f"\n-- {last} per-symbol (p2={PEAK_DETAIL_P2VR} vs={PEAK_DETAIL_VS}) --")
    base = baseline[last]
    print(f"{'sym':<8}{'touch':>6}{'peak':>6}")
    for s in sorted(day_syms[last]):
        print(f"{s:<8}{base.get(s, 0):>6}{detail_fired.get(s, 0):>6}")
```

- [ ] **Step 4: 語法檢查(import 不執行登入)**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -c "import ast; ast.parse(open('scripts/replay_engine.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```powershell
Set-Location C:\side-project\treading-king; git add backend/scripts/replay_engine.py; git commit -m @'
feat(replay): 策略5 v2 回測掃 peak2_volume_ratio × volume_shrink_ratio

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

---

## Task 6: 回測驗收(真引擎跑 cache 4 檔)— 硬驗收關卡

**Files:**
- Create: `backend/scripts/_diag_acceptance.py`(untracked,不 commit;同 `_diag_peak.py`/`_diag_surge.py`)

> 用 `_diag_cache.json` + 真 `SignalEngine`(經 `replay_day`)跑 6/12 四檔,**免登入富邦**。這是 spec 的硬驗收:6207 觸發、8064/8150/6239 排除。`replay_day` 每 tick 累積真實 `_day_volume`、4-tick 展開,比 `_diag_surge.py` 的模擬更權威。

- [ ] **Step 1: 建立驗收腳本**

`backend/scripts/_diag_acceptance.py`:

```python
"""回測驗收(不 commit):用 _diag_cache.json + 真 SignalEngine(replay_day)跑 4 檔 6/12,
驗證 6207 觸發、8064/8150/6239 排除。免登入富邦(只用 cache、不呼叫 fetch_fubon)。"""
import asyncio
import importlib.util as ilu
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

CACHE = BACKEND / "scripts" / "_diag_cache.json"
DAY = "2026-06-12"
_c = json.loads(CACHE.read_text(encoding="utf-8"))

# JSON 把 tuple 存成 list — replay_day / compute_cdp / candles_to_ticks 解包需 tuple
daily = {s: {d: tuple(v) for d, v in _c["daily"][s].items()} for s in _c["daily"]}
minute = {s: {d: [tuple(r) for r in rows] for d, rows in _c["minute"][s].items()}
          for s in _c["minute"]}

_spec = ilu.spec_from_file_location("re_mod", BACKEND / "scripts" / "replay_engine.py")
_re = ilu.module_from_spec(_spec)
_spec.loader.exec_module(_re)

EXPECT = {"6207": True, "8064": False, "8150": False, "6239": False}


async def main():
    print(f"=== 策略5 v2 回測驗收 (peak2_volume_ratio=2.0, volume_shrink_ratio=0.6) {DAY} ===")
    all_ok = True
    for sym, want in EXPECT.items():
        fired = await _re.replay_day(DAY, [sym], daily, minute, _re.peak_rule(2.0, 0.6, DAY))
        n = sum(fired.values())
        got = n > 0
        ok = got == want
        all_ok = all_ok and ok
        mark = "✅" if ok else "❌"
        print(f"  {mark} {sym}: fired={n} (期望{'觸發' if want else '排除'})")
    print(f"\n{'✅ 驗收通過' if all_ok else '❌ 驗收失敗'}")
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
```

- [ ] **Step 2: 執行驗收**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" scripts/_diag_acceptance.py`
Expected:
```
  ✅ 6207: fired=... (期望觸發)
  ✅ 8064: fired=0 (期望排除)
  ✅ 8150: fired=0 (期望排除)
  ✅ 6239: fired=0 (期望排除)

✅ 驗收通過
```

> 若 6207 `fired=0`:`_diag_surge.py` 模擬已證實 `peak2_volume_ratio=2.0` 會觸發、主峰 134/次峰 133@10:54;真引擎與模擬的差異主要在 4-tick 展開讓 vr 略不同。用 systematic-debugging:跑 `_diag_surge.py` 對照,確認是次峰 vr 在 4-tick 下掉到 2.0 以下,或主峰封頂/滾頭時點偏移。**先查清根因再動參數**;若需微調,在 2.0–2.5 之間掃 `peak2_volume_ratio`(同時確認 6239 仍排除)。
> 若 8064/8150/6239 任一 `fired>0`:檢查是否該股有「主峰急拉 + 次峰出量反彈 + 量縮」的偶發組合;記錄該股觸發時點,評估是否需收緊 `peak2_volume_ratio`(往 2.5)或 `surge_volume_ratio`。

- [ ] **Step 3: 全套測試回歸**

Run: `Set-Location C:\side-project\treading-king\backend; & ".\.venv\Scripts\python.exe" -m pytest -q`
Expected: PASS(全綠;確認 schema_version 改動沒打到其他測試)

- [ ] **Step 4: 記錄驗收結果(不 commit `_diag_acceptance.py`)**

把 Step 2 的實際輸出貼回對話;`_diag_acceptance.py` 與既有 `_diag_peak.py`/`_diag_surge.py` 一樣保持 untracked。

---

## Self-Review

**1. Spec coverage:**
- 急拉根定義(陡升+出量,主峰)→ Task 2 `_detect_surge` + 單測 ✓
- 狀態機 watch→retrace→confirmed(含主峰追高、不對稱次峰、過前高升級、量縮、max_gap、latch)→ Task 3 ✓
- 8 個參數(surge_pct/window/surge_volume_ratio/peak2_volume_ratio/pullback/shrink/tol/max_gap)→ Task 1 schema ✓,Task 3 全用到 ✓
- 量基準統一 `_candle_volume_ratio` → Task 2/3 ✓
- schema_version 7→8 → Task 1 ✓(含 3 處連帶斷言)
- replay 改新參數 → Task 5 ✓
- 回測硬驗收(6207 觸發 / 三檔排除)→ Task 6 ✓
- bot 圖卡沿用 → 不動(payload 格式 level=peak/role=distribution 與 v1 一致,Task 3 回傳 dict 保持)✓
- 整合測 fanout 帶 distribution → Task 4 ✓

**2. Placeholder scan:** 無 TBD/TODO;每個 code step 都有完整 code;每個 run step 都有 exact command + expected。整合測(Task 4)與回測(Task 6)各附 debug 指引(指向 systematic-debugging,非佔位)。

**3. Type consistency:**
- `_detect_surge(self, symbol, candle, recent_closes, now, strat) -> bool` — Task 2 定義、Task 3 呼叫一致 ✓
- `_peak_state` keys:`phase, surge_seen, recent_closes, peak1_high, peak1_vr, peak1_minute, peak2_high, peak2_vr` — Task 3 初始化與使用一致 ✓
- strat keys 與 `PeakDivergenceStrategy` 欄位名一致(`peak2_volume_ratio` 等)— Task 1 schema、Task 3 讀取、Task 5 `peak_rule` 一致 ✓
- 觸發 dict keys(`level/direction/role/main_peak_price/second_peak_price/volume_shrink`)— Task 3 回傳、Task 4 斷言、bot(沿用 v1)一致 ✓
- `peak_rule(peak2_volume_ratio, volume_shrink_ratio, day)` — Task 5 定義、Task 6 呼叫一致 ✓

---

## Task 6 執行後記:6239 真引擎誤觸發 → volume_shrink_ratio 0.6 修正

Task 6 首次驗收(`volume_shrink_ratio=0.8`)時 **6239 誤觸發**(fired=1),`_diag_surge.py` 模擬卻顯示
排除——模擬的簡化狀態機(次峰 `h<p1h` + 無升級邏輯)與真 method 有出入。經 systematic-debugging:
- 真引擎攔截:6239 在 11:21 觸發(main=357.5、shrink=**0.72**),但當日真頂 363.5 在 **11:39**——
  觸發後股價還漲 6 塊 = 假訊號(攻頂半山腰的小回檔反彈被誤判做頭)。
- 對照 6207(真做頭)shrink=**0.38**:區分維度是「量縮程度」,`volume_shrink_ratio=0.8` 太鬆。
- **修正**:預設 0.8 → **0.6**(condition.py + `_active` helper + defaults 斷言 + spec 附錄三),
  新增 regression 測試 `test_mild_volume_shrink_does_not_fire`。6207(0.38<0.6)仍觸發、6239(0.72)排除。
- 真引擎 4 檔驗收最終通過:6207 觸發、8064/8150/6239 全排除。詳見 spec 附錄三。

## 實作後

回測驗收通過後,再決定 PR #33(v1)的處置:v2 是否與 v1 同分支(`feat/peak-divergence`)→ v2 commits 直接取代 v1、PR #33 更新為 v2;或關閉 PR #33 另開。跟使用者確認。
