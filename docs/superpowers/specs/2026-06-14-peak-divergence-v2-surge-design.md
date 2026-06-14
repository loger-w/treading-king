# 雙峰急拉量價背離造山(策略5 v2:加「急拉」判定)設計 — 2026-06-14

> **本 spec 取代 v1**(`2026-06-14-peak-divergence-design.md`)。v1 已實作於 PR #33,
> 但經 6/12 真實 1 分 K 診斷證實有**結構性缺陷**(下節),核心定義要重做。v1 spec 與 PR #33
> 保留為歷史;v2 實作完成後再決定 PR #33 的處置(取代 / 關閉)。

## 為何要 v2(v1 的結構性缺陷,診斷實證)

v1 把「主峰 = `candle.high` 創當日新高」,次峰 = 「pullback 後任何反彈 high」。用 6/12 真實 1 分 K
逐根 trace(`_eval_peak_divergence`,pb=1.0 vs=0.8)發現:

- **6207 雷科**:09:01 開盤第二根衝 133.5 又拉回 131(同根封頂)→ 09:04 就用「開盤假高 133.5 / 次峰 131」
  觸發 confirmed,**當日 latch**。真正的做頭(主峰 134@09:59 → 次峰 133@10:50 量縮)被完全錯過。
- **8064 / 8150**:都是「開盤衝高 → 整天陰跌」的**單峰**,沒有「回測後再急拉」;但 v1 把陰跌中的
  零星小反彈也當次峰,照樣誤觸發。
- 回測「全 12 檔 peak=1、參數矩陣鈍感」的真因 = v1 在開盤頭幾分鐘就用假高+小回落觸發,幾乎每檔都中。

**根因**:v1 完全沒有「急拉」概念 —— 主峰只要「創新高」(開盤第一根就是)、次峰只要「反彈」
(陰跌小彈也算)。早盤暖機(`min_elapsed_minutes`)實測只能讓主峰抓對,**排不掉單峰**(8064/8150
照樣觸發),不是對的方向。**正解是把「急拉」(陡升 + 出量)做成主峰/次峰的必要閘門。**

## 形態定義(使用者領域知識)

要抓的是**雷科 6207 型**:**急拉(出量)→ 回測 → 再急拉**;第二次急拉的特徵是
**量沒有前一次多(量縮)+ 沒過前高** → 滾頭垮掉 = 做頭轉弱。

明確排除(這次不抓,範圍見下):
- 開高走低 / 久盤後一波急拉到頂的**單峰**(8064、8150、6239 力成)→ 它們沒有「回測後第二波急拉」。

## 急拉根(策略核心的新閘門)

一根結算的 1 分 K 是「**急拉根**」,當且僅當**同時**滿足:

1. **陡升**:`close` 相對 `surge_window_bars` 根前的 close,漲幅 ≥ `surge_pct%`
   (用 close 不用 high:收在高才是真拉,避免長上影線假突破)
2. **出量**:`_candle_volume_ratio(這根) ≥ surge_volume_ratio`
   (= 這根量 ÷ 「開盤至今每分鐘均量」的倍數;既有 method,signal_engine.py:550)

「陡 + 快(短窗)+ 量」三者缺一不可。這道閘門就是用來擋掉**開盤高、慢拉、陰跌反彈**
(它們不是「短窗內陡升且爆量」)。

## 狀態機

per `(active.id, symbol)` 當日狀態 `_peak_state`,phase = `watch → retrace → confirmed`。
每根結算 candle 依序:

1. **watch(找主峰)**
   - 出現**急拉根** → 進入造主峰:`peak1_high` 追這波最高 high、`peak1_vr` 記這波出現過的**最大**量比
   - `close` 從 `peak1_high` 回落 ≥ `pullback_pct%` → 主峰封頂,進 `retrace`(記 `peak1_minute`)
   - **沒有急拉就不鎖主峰**(開盤高、緩漲一律不算主峰)
2. **retrace(回測找次峰)**
   - `close` 回落中(記回測;主峰已封頂)
   - 出現**第二個急拉根** → 造次峰:`peak2_high`、`peak2_vr`(同樣取這波最大量比)
   - 次峰**過前高**(`peak2_high ≥ peak1_high × (1 + not_exceed_tolerance_pct/100)`)
     → 不是背離:次峰升級為新主峰(`peak1 ← peak2`)、回 `watch` 續找
   - 次峰造峰後 `close` 回落 ≥ `pullback_pct%`(滾頭)→ 檢查背離:
     - 不過前高(已保證)**＋ 量縮**(`peak2_vr < peak1_vr × volume_shrink_ratio`)
       → **觸發「做頭轉弱」**,phase = `confirmed`
     - 量沒縮 → 重置次峰候選(`peak2` 清 0)、繼續找
   - 主峰 → 次峰間隔 > `max_gap_minutes` → 放棄這組,回 `watch`
3. **confirmed** — 當日 latch(防重複;cooldown 設長當保險)

**觸發回傳**(走現有 `_fanout`,沿用 v1 metadata 格式,bot 已支援 `level=peak`/`role=distribution`):
```python
{"level": "peak", "direction": "from_above", "role": "distribution",
 "main_peak_price": peak1_high, "second_peak_price": peak2_high,
 "volume_shrink": round(peak2_vr / peak1_vr, 2)}
```

## 參數(`PeakDivergenceStrategy` v2;預設為回測掃描起點)

| 欄位 | 預設 | 白話 |
|---|---|---|
| `surge_pct` | 2.0 | 陡升:close 相對 W 根前漲 ≥ 此% |
| `surge_window_bars` | 3 | 陡升回看根數 W(跟 surge_pct 合定「速度」,W 必須小才擋慢拉) |
| `surge_volume_ratio` | 2.5 | 出量:`_candle_volume_ratio ≥ 此`(到當下均量的倍數) |
| `pullback_pct` | 1.0 | 峰封頂 / 次峰滾頭的回落確認幅度 |
| `volume_shrink_ratio` | 0.8 | 次峰量比 < 主峰量比 × 此(量縮背離,策略靈魂) |
| `not_exceed_tolerance_pct` | 0.0 | 次峰不過主峰高的容差(0 = 完全不准超過) |
| `max_gap_minutes` | 120 | 主峰→次峰最大間隔 |

(移除 v1 的 `min_main_peak_volume_ratio` —— 被 `surge_volume_ratio` 取代)
schema_version 7 → 8(欄位大改;v1 PeakDivergenceStrategy 整個換掉)。

## 量基準(統一用 `_candle_volume_ratio`)

出量(急拉資格)與量縮(背離)**共用一套基準** = `_candle_volume_ratio`(到當下每分鐘均量的倍數):
- `peak1_vr` / `peak2_vr` = 各自造峰期間出現過的**最大**量比(代表該波出量峰值)
- 背離 = `peak2_vr < peak1_vr × volume_shrink_ratio`

**為何用 ratio 不用 raw volume**:「出量」的正確語意是「相對近期均量爆量」(raw 量早盤晚盤不可比);
而出量門檻必須是 ratio,量縮也跟著用 ratio,避免兩套基準。

**誠實權衡(已知限制)**:`_candle_volume_ratio` 基於 `day_volume / 開盤至今分鐘`,有「盤中重啟偏誤」
(重啟當日 day_volume 從 0 重算)。回測無此問題;production 重啟是已知限制(同 v1)。

## 架構:一個 method + 抽出「急拉偵測」積木

- **策略5 = 一個 evaluator method**(`_eval_peak_divergence`,照 codebase 慣例,同策略 1/2/3),
  用 `_peak_state` dict 狀態機。**不是**一堆散條件,也**不是**現在就蓋組合框架。
- **抽出一塊可重用積木**:`_detect_surge(symbol, candle, recent_closes, now, strat) → bool`
  (陡升 + 出量判定)。理由:6239 單峰、未來其他做頭家族都會複用同一個「急拉」判定 → 抽成
  獨立、可單元測的 helper。峰追蹤 / 量縮 / 狀態機這次留在 method 內(只一個策略用,YAGNI)。
- **「組合框架」(整個策略當積木疊加投票成高置信)= 未來**;積木累積夠(急拉、量縮、峰偵測…)再做。

## 與現有 code 的關係

- **沿用**:`_update_candle`(candle 結算)、`_candle_volume_ratio`、`_evaluate` 的
  `peak_divergence` dispatch 分支(三處 stype)、`_fanout`、`_reset_daily_strategy_state`、bot 圖卡。
- **重寫**:`_eval_peak_divergence`(改成急拉造峰狀態機)+ 新增 `_detect_surge` helper。
- **`_peak_state` 欄位**:`phase, recent_closes(最近 W 根 close,算陡升), peak1_high, peak1_vr,
  peak1_minute, peak2_high, peak2_vr`(day_high / trough_low 不再需要;主峰由急拉鎖、不靠當日新高)。
- **schema**:`PeakDivergenceStrategy` 欄位全換(見參數表),schema_version 7→8。
- **回測**:`replay_engine.py` 的 `peak_rule` / `run_peak` 改掃新參數
  (surge_pct × surge_volume_ratio 為主軸)。

## 測試 / 回測驗收

- **單元測試**(重寫 `test_signal_engine_peak_divergence.py`):
  - 急拉根判定(陡升+出量皆過 / 只陡不出量 / 只出量不陡 → 各案例)
  - 雙急拉觸發、次峰過前高升級新主峰、次峰量沒縮不觸發、無第二波急拉不觸發、max_gap、daily reset
  - 整合測(逐 tick 跨分鐘結算 → `_evaluate` → fanout 帶 distribution)
- **`_detect_surge` 獨立單元測**(積木可單測)
- **回測驗收(用 `_diag_cache.json`,已含 6207/8064/8150/6239)**:
  - **6207 必須觸發**,且主峰 ≈134(非開盤 133.5)、次峰 ≈133、觸發時點在 10:xx(非 09:04)
  - **8064 / 8150 / 6239 必須都不觸發**(單峰、無第二波急拉)
  - 這是 v2 的硬驗收標準(v1 的「6207 假觸發 + 8064/8150 誤抓」必須翻轉)

## 範圍(YAGNI)

- **這次只做純雙峰**(6207 型)。
- 單峰急拉見頂(6239 力成型:久盤→一波急拉到頂→陰跌)= **下一個積木**,用自己的衰竭邏輯獨立做。

## 未來工作

- **6239 型單峰急拉見頂** strategy(複用本次的 `_detect_surge` 積木)。
- **用 `_detect_surge` 升級既有「突爆拉」WindowCondition**:目前 `price_change_pct`(如 5 分 >2%)
  只看漲幅、缺出量,會誤觸發慢拉/無量;未來給它加出量維度(需整合 tick-window 與 candle 基礎)。
- **組合框架**:做頭家族積木夠多後,策略疊加投票成高置信。

## 已知限制

- 收盤前最後一兩根吃不到滾頭確認(in_session gate,同 v1)。
- 盤中重啟:`_peak_state` 從重啟起算 + `_candle_volume_ratio` 的 day_volume 偏誤(同 v1)。
- 4 tick 近似:回測看相對門檻 + 目標案例命中(6207 中 / 8064・8150・6239 排除)為準,不看絕對數。
- 急拉綁「短窗陡升」:若某股是「多根極溫和但持續爆量」的急拉,W / surge_pct 要回測調。

## 附錄:診斷實證數據(6/12,from `_diag_cache.json`)

**急拉前均量**:6207 = 120/分(09:00–09:54)、64/分(全天);6239 = 362/分(11 點前)、346/分(全天)。
9:55前均量 > 全天均量(開盤量大、午後枯)→ 即時只能用「到當下均量」(`_candle_volume_ratio`),
不能用全天均量。

**6207 急拉那幾根的出量倍數(相對到當下均量)**:
- 急拉①:09:55 V456 ≈3.8x、09:56 V1012 ≈7x(峰值)、09:59 V348
- 急拉②:10:40 V294 ≈2.9x、10:49–50 V246/227 → **明顯 < 急拉①(量縮)**
- 主峰 vr≈7、次峰 vr≈2.9 → 2.9 < 7×0.8=5.6 ✓ 量縮成立
- 對比:8064/8150 陰跌反彈根多 <1.5x(沒出量,被擋)

→ **出量門檻 `surge_volume_ratio` 設 ~2.5(留安全邊際分開真急拉 ~3-7x vs 雜訊 <1.5x);
急拉② 的 2.9x 剛好在門檻上,回測需確認 6207 急拉②不被門檻誤殺**(可能要 2.0–2.5 之間掃)。
