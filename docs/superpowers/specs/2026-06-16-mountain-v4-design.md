# 造山積木 v4 設計 — 2026-06-16

> **取代 v3**（`2026-06-15-mountain-building-block-design.md` Part 1）。
> v3 問題：surge 用 close 漏掉戳高回落型急拉（榮科）；固定 10 根視窗開盤不足根數時跳過；
> 3 根確認太慢（南茂第二波 re-surge 要求 is_surge 擋住）。

## 三處核心改動

### 1. Surge 改用 high + 近期相對低點

**現行 v3**：`(close - close[10根前]) / close[10根前] >= 3%`

**v4**：`(high - recent_swing_low) / recent_swing_low >= 3%`

- **high**：戳高代表追價力道已現，是否守住由 confirm 階段驗證。
- **近期相對低點**（`recent_swing_low`）：從前一根往回掃，找當前上升波段的起漲谷底 — 不是視窗內全域最低價。

```python
def _find_surge_base(closes):
    """從 closes（不含當根）找當前上升波段的起漲谷底。
    
    從最末根往回掃，追蹤最低值；碰到比最低值高的根 = 下降已結束、谷底找到。
    """
    if len(closes) <= 1:
        return closes[0] if closes else 0.0
    running_min = closes[-1]
    for i in range(len(closes) - 2, -1, -1):
        if closes[i] < running_min:
            running_min = closes[i]
        elif closes[i] > running_min:
            break
    return running_min
```

範例：
```
closes = [100, 98, 95, 93, 94, 95, 96, 97, 96, 98, 99]
                        ↑ 全域最低(不用)  ↑ 相對低點(96)
_find_surge_base(closes[:-1]) → 掃到 96 時碰到 97 > 96 → 回傳 96
```

**`recent_closes` 仍存 close**（是基準來源），不存 high。

**最少根數**：`len(closes) >= min_bars`（預設 3）才啟動偵測，防開盤 price discovery 雜訊。

### 2. 分級確認

**現行 v3**：連續 `confirm_bars`（預設 3）根沒創新高才 confirmed。

**v4**：

```
surge_tracking 階段，candle.high 沒創新高時：
  黑 K（close < open）+ vr >= confirm_vr  → 1 根立即確認
  其他                                    → 計入 no_new_high_count，連續 2 根確認
```

**黑 K 確認需要量比門檻**（`confirm_vr` 預設 0.5x）：無量黑 K 可能是雜訊（大盤瞬間閃崩、午盤單筆小單），不算賣壓。

回測傳 `confirm_bars=N` 時走舊路徑（純計數，不分級），保持回測掃描相容。

### 3. Re-surge 放寬 + 容忍區間

**現行 v3**：`if is_surge and candle.high > peak_high`

**v4**：`if candle.high > peak_high * (1 + re_surge_margin / 100)`

- 移除 `is_surge` 要求：只要價格實質突破山頂就重回 surge_tracking。
- 加 `re_surge_margin`（預設 0.3%）：山頂 100 → 需要 100.3 才重置。防市場雜訊（0.1% 飄高）取消合法 confirmed。

## 參數一覽

| 參數 | 預設值 | v3 對比 | 說明 |
|------|--------|---------|------|
| `surge_pct` | 3.0% | 不變 | 急拉幅度（high vs 近期相對低點） |
| `surge_window` | 10 | 不變 | 回看視窗上限（找相對低點） |
| `surge_vr` | 1.5x | 不變 | 急拉量比門檻 |
| `min_bars` | **3** | 新增 | 最少根數才啟動偵測 |
| `confirm_vr` | **0.5x** | 新增 | 黑 K 確認根最低量比 |
| `re_surge_margin` | **0.3%** | 新增 | 新高需超過山頂此%才重置 |
| 黑 K 確認 | **1 根** | 原 3 根 | close < open + 未創新高 + vr >= confirm_vr |
| 非黑 K 確認 | **2 根** | 原 3 根 | 連續 2 根未創新高 |

## 改動位置

| 檔案 | 改動 |
|------|------|
| `signal_engine.py` `_detect_surge` | high vs recent_swing_low、min_bars 門檻 |
| `signal_engine.py` `_update_mountain` | 分級確認、re-surge margin、新參數 |
| `signal_engine.py` class 常數 | 新增 `MOUNTAIN_MIN_BARS`、`MOUNTAIN_CONFIRM_VR`、`MOUNTAIN_RE_SURGE_MARGIN` |
| `tests/test_mountain_building_block.py` | 更新預期行為 |

新增 helper：`_find_surge_base(closes)` — 放在 `SignalEngine` 內或 module-level 皆可（純函式）。

## 不動的

- `_candle_volume_ratio`
- `MinuteCandle` 結構
- 造山的 phase 三態（idle → surge_tracking → confirmed）
- `_mountain_state` 的 key 結構（phase/peak_high/peak_vr/peak_minute/confirmed_minute/no_new_high_count）
- 策略 A/B 草案
- `recent_closes` 仍存 close

## 預期回測效果

| 案例 | v3 | v4 預期 |
|------|-----|---------|
| 榮科 09:07 (high 83.9, 7 根) | 沒偵測到 | ✅ surge（3.33% from swing low 81.3）→ 09:08 黑 K 確認 |
| 南茂第二波 09:38 | stuck confirmed | ✅ re-surge（101.5 > 99.3×1.003）→ 新山追蹤 → 確認 |
| 南茂雜訊飄高 0.1% | — | ❌ 不觸發 re-surge（99.4 < 99.3×1.003=99.6） |
| 大盤閃崩無量黑 K | 不影響(3根) | ❌ 不確認（vr < 0.5x） |
| 九齊強勢多頭 | 3 座假山 | 假山可能增加（確認更快），但 re-surge 也更快重置 |

## 測試案例

1. **surge 用 high**：close 未達 3% 但 high 達 3% → 應觸發 surge
2. **近期相對低點 vs 全域最低**：先跌後緩漲再急拉 → base 用相對低點不用全域低
3. **min_bars 門檻**：只有 2 根資料 → 不觸發
4. **黑 K 1 根確認**：surge 後黑 K + vr >= 0.5x → 立即 confirmed
5. **黑 K 無量不確認**：surge 後黑 K + vr < 0.5x → 不確認，繼續 surge_tracking
6. **非黑 K 2 根確認**：surge 後 2 根綠 K 沒創新高 → confirmed
7. **re-surge margin**：confirmed 後 high 超過山頂 0.2% → 不重置；超過 0.4% → 重置
8. **confirm_bars 覆蓋**：傳 confirm_bars=N → 走舊路徑（純計數）
9. **相對低點 edge case**：單調上升序列 → base = 第一根 close
10. **相對低點 edge case**：單調下降序列 → base = 最末根 close（最低）
