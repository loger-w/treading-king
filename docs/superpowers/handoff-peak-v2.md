# 策略5 v2 實作 Handoff — 雙峰急拉量價背離造山

> 新 session 從這份開始。任務:在 `feat/peak-divergence` 分支實作「策略5 v2」。
> (memory `project_peak_divergence_strategy` 有摘要,會自動載入。)

## 先做:用新鮮眼睛 review spec

critically review 設計 spec:
`docs/superpowers/specs/2026-06-14-peak-divergence-v2-surge-design.md`

檢查:急拉定義、狀態機(watch→retrace→confirmed)、7 個參數、量基準、驗收標準有無
漏洞 / 矛盾 / 可簡化處。**有疑慮先跟使用者確認;沒問題再走 `writing-plans` → TDD 實作。**

## 背景:為什麼有 v2

策略5 v1 已實作於 PR #33,但經 6/12 真實 1 分 K 診斷證實**結構性缺陷**:
- v1「主峰 = 創當日新高、次峰 = 任意反彈」→ 6207 在 **09:04** 就用**開盤假高 133.5** + 小回落
  觸發 latch,真正的做頭(主峰 **134@09:59** → 次峰 **133@10:50** 量縮)被完全錯過。
- 開高走低的**單峰**(8064 / 8150)沒有「回測後第二波急拉」,卻被 v1 誤抓。
- 回測「全 12 檔 peak=1 + 參數鈍感」就是這個開盤假觸發,不是真做頭。

**v2 核心修正 = 加「急拉根」閘門**:
- 急拉根 = **陡升**(close 相對 `surge_window_bars` 根前漲 ≥ `surge_pct%`)
  **＋ 出量**(`_candle_volume_ratio ≥ surge_volume_ratio`),兩者皆必要。
- **主峰、次峰都必須由急拉啟動** → 排掉開盤高、慢拉、陰跌反彈。
- 量價背離:次峰量比 < 主峰量比 × `volume_shrink_ratio`(「再急拉但量沒前一次多」)。

## 關鍵資源:診斷 cache(回測驗收免登入富邦)

`backend/scripts/_diag_peak.py` + `backend/scripts/_diag_cache.json`(均 untracked)
含 6/12 四檔 **6207 / 8064 / 8150 / 6239** 的 daily + 1 分 K。**回測驗收直接讀 cache、
不必登入富邦**。`_diag_peak.py` 可改參數重跑(已示範逐根 trace / 均量 / 出量倍數)。

## 驗收硬標準(v2 必須翻轉 v1)

用 cache 跑:
- **6207 要觸發**,且主峰 ≈ **134**(非開盤 133.5)、觸發時點在 **10:xx**(非 09:04)
- **8064 / 8150 / 6239 都要排除**(單峰、無第二波急拉)

## 實作範圍

- 重寫 `signal_engine.py` 的 `_eval_peak_divergence`(急拉造峰狀態機)
- 抽 `_detect_surge(...)` helper 積木(陡升+出量;6239 單峰 / 未來做頭家族複用)
- `condition.py` 的 `PeakDivergenceStrategy` 換欄位(見 spec 參數表),schema_version **7→8**
- `replay_engine.py` 的 `peak_rule` / `run_peak` 改掃新參數(surge_pct × surge_volume_ratio 主軸)
- 單元 / 整合測試重寫(含 `_detect_surge` 獨立單測)
- bot 圖卡(`level=peak` / `role=distribution`)v1 已做、**沿用不改**
- **範圍只做純雙峰(6207)**;單峰(6239 力成型:久盤→一波急拉到頂→陰跌)後續另開

## 環境

- backend pytest 用 `backend\.venv\Scripts\python`
- PowerShell 串接用分號 + `if($?)`,不用 `&&`
- 改後端前先停 dev server(避免登入風暴)
- 繁體中文回覆

## 實作後

再決定 PR #33(v1)的處置(取代 / 關閉)。
