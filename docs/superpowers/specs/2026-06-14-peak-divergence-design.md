# 雙峰量價背離造山訊號(策略 5)設計 — 2026-06-14

策略候選清單第 5 條。原構想「午後轉弱」,2026-06-14 brainstorm 後重新定義為
**「雙峰量價背離造山」**:時段限制取消(造山可在早盤 10–11 點發生),範圍**鎖定純雙峰背離
一種形態**(其餘做頭形態見「未來工作」)。

> 註:`docs/notes/2026-06-12-strategy-candidates.md` 在 repo 不存在;7 個策略候選
> 清單的實際來源是 signal-rearm-and-strategy-roadmap memory。

## 背景動機

6/12 雷科(6207)、東捷(8064)、南茂(8150) 同一劇本:

- 早盤衝高、創當日前高 + 出量(**主峰**)
- 回落
- 二次反彈,但**量縮、攻不過前高**(**次峰**)
- 垮,收當日最低

(雷科 6/12 本機 OHLC:H134 / L124 / C124 —— 9:56 主峰 134、10:40 次峰量縮不過高、
收當日最低 124,高低差 −7.5%。)

現有引擎抓不到這種「**強勢股日內做頭轉空**」:碰CDP / 突爆拉只看上漲、碰線;突爆殺(策略3)
只抓「5 分鐘急殺 −2%」的**瞬間動量**,不分位階、**抓不到緩跌型結構轉空**。

## 結論先講

- **形態 = 雙峰量價背離**(主峰創高+量 → 回落 → 次峰量縮不過前高 → 滾頭確認)。
- **範圍鎖純雙峰**:單峰急垮、開高走低是**未來的其他做頭積木**(見未來工作),**不在本 PR**。
  (雷科正是雙峰,純雙峰對得上原始案例。)
- 純價量、可回測(限制見回測節);內外盤 order flow 不揉進(→ 策略4)。
- **核心狀態機抽成獨立 `PeakDetector` 單元**:現在以獨佔 strategy 上線,但邏輯模組化,
  未來做「可組合積木」框架時直接復用、不重寫。**本 PR 不做組合框架、不動 strategy 獨佔架構**
  (YAGNI,積木夠多再做)。
- 要寫的 code:`condition.py` model + `signal_engine.py` evaluator/狀態/reset + `PeakDetector` +
  `replay_engine.py` 加 `--preset peak`。

## 策略定義 — 雙峰造山狀態機

per-symbol、當日,跨午夜 reset。

**關鍵前提**:tick 引擎無 K 棒,「峰」用**回落 R 事後確認**(類 ZigZag)—— 價格從一個高點
回落 ≥ R 才算「一座峰封頂」。所以觸發必然比峰頂晚約 R(這也是為何策略5 偏「做頭確認」、
不是好的進場點;早進場靠未來策略4 order flow)。

### Phase 流轉

```
WATCH(找主峰)
  - 持續更新當日最高;每創新高,記錄主峰候選(高價 + 量,量取法見下)
  - 當 price 從當日最高回落 ≥ R  → 主峰確認:鎖定 peak1_price / peak1_vol
  → PULLBACK

PULLBACK(主峰後找次峰)
  - 追蹤回落谷底 trough
  - 反彈時,每創「反彈以來新高」,記錄次峰候選(高價 + 量)
  - 當 price 從反彈高點回落 ≥ R  → 次峰確認:鎖定 peak2_price / peak2_vol
  - 判定兩條件:
        不過前高:  peak2_price < peak1_price × (1 + ε)
        量縮:      peak2_vol  < peak1_vol  × volume_shrink_ratio
    皆成立 → 觸發「做頭轉弱」→ CONFIRMED(當日 latch)
    若次峰過前高 → 不是做頭:把次峰當新主峰(peak1 ← peak2),留 PULLBACK 找下一座
                  (等同「山越造越高」,尚未轉弱)

CONFIRMED(當日 latch)
  - 不再觸發;(未來)策略4 外盤轉弱可用此 latch 當「已做頭」gate 疊加
```

### 實作注意(review 抓到的坑)

- **heartbeat 去重**:`signal_engine` 每秒用 `ring_buffer.latest` 重評估;`PeakDetector` 推進
  (更新峰/谷)**必須對重餵的同一筆 tick 做 identity 去重**(比照 `_evaluate` 裡 `day_volume`
  的 `tick is not prev_tick`),否則 latest 停在當日高時每秒誤判「創新高」。
- **盤中重啟丟狀態**:當日峰/谷在 in-memory,盤中重啟(富邦重連)後 `day_high` 從重啟起算,
  主峰可能算錯。已知限制(同 `day_volume`「restart 後重算」),標明;v1 不做持久化。

### 觸發 metadata(fan-out)

推「**做頭轉弱**」,context 帶:`peak1_price`/`peak1_vol`、`peak2_price`/`peak2_vol`、
量縮幅度(`1 − peak2_vol/peak1_vol`)、觸發價。strategy 類不套 re-arm、cooldown per 股票;
當日 latch 已保證一天一次。

## 參數(初值,**以實盤迭代為主** —— 回測驗不出量,見回測節)

| 參數 | 初值/方向 | 作用 |
|---|---|---|
| `pullback_pct` (R) | **當日振幅比例**(非固定 %) | 峰回落確認。⚠️ review:固定 1% 對大型股是雜訊、對小型股會被穿過 → 改用當日已實現振幅(high−low)的比例適配波動;具體公式見開放問題 |
| `not_exceed_tolerance_pct` (ε) | 0 | 次峰不過前高的容差(0 = 嚴格不過) |
| `volume_shrink_ratio` | 0.8 | 次峰量 < 主峰量 × 此值(縮 ≥20%) |
| `volume_window_seconds` (N) | 60 | 峰量累積窗口 |

> **峰量取法(review 抓到)**:做頭的量在「高檔換手」(峰當下 + 之後),不是純「峰前 N 秒」
> —— 用峰前 N 秒會系統性低估主峰量 → 次峰顯得沒那麼縮 → 漏訊號。實作改用「峰確認區間」
> (主峰形成到回落確認之間)累積量,回測校。

## 為可組合性設計

`PeakDetector`(獨立單元):

- **輸入**:逐筆 `(price, size, time)`;**輸出**:造山確認事件(含 peak1 / peak2 / 量縮幅度)。
- **不依賴** `signal_engine` 內部狀態,可獨立單元測試(TDD 友善)。
- **現在**:`_eval_peak_divergence` 薄包它 → 走現有 `_fanout`(WS + log + Discord)。
- **未來**:組合框架做好時,`PeakDetector` 直接當「積木」被組合(造山 AND 外盤 AND …),不重寫。

**本 PR 不改 strategy 獨佔架構、不做組合框架。** (現狀:`ActiveFilter` 有 strategy 時由它
獨佔整條規則,strategy 之間 / 與其他條件不能組合;通用組合是未來工作。)

## 落地(沿用現有 strategy 機制,無新架構)

- **`condition.py`**:新增 `PeakDivergenceStrategy` 進 `StrategyConfig` union
  (`pullback_pct` / `not_exceed_tolerance_pct` / `volume_shrink_ratio` / `volume_window_seconds`)。
- **`PeakDetector`**(新單元):純價量狀態機,如上。
- **`signal_engine.py`**:`_eval_strategy` 加 `peak_divergence` dispatch → `_eval_peak_divergence`
  (薄包 `PeakDetector`);`__init__` 持有 per-symbol `PeakDetector` 狀態;
  `_reset_daily_strategy_state` 清。
- **不動**:`ring_buffer` / `cdp` / `ma_service` / `_fanout`。

## 回測設計

擴 `replay_engine.py` 加 `--preset peak`:股票池沿用 signals_log 近 N 日股票,餵價+量,
掃 R / `volume_shrink_ratio`,per-symbol 明細對照走勢。**驗收**:目標案例(雷科/東捷/南茂 6/12)命中。

### ⚠️ 限制(誠實 flag)

1. **量驗不出**:即使有量分攤,1 分 K 把一分鐘量平均攤到 4 tick,60s 窗(4–8 tick)的
   「量縮背離」鑑別力**幾乎是零**。→ 回測**只驗得了價格雙峰形態,量是全盲的**。
2. **時間解析度**:1 分 K 轉 4 tick,密集雙峰被抹平(雷科主峰→次峰隔 44 分鐘夠寬、抓得到;
   更近的抓不到)。
3. **量分攤依賴**:main 的 replay 餵 `size=1`(假量);策略2 的量分攤改動**需先確認可用**。
4. → 結論:**「先回測定參」對這策略幾乎失效**,回測只驗「目標案例形態中不中」,
   **參數以盤中實盤迭代為主**(跟突爆殺「回測定案」不同)。

## 不做的事(YAGNI)

- **內外盤 / order flow** → 策略4(不可回測、雜訊高,見 `large-order-detection` spec)。
- **進場 / 加碼 / 停利 / 停損 部位邏輯** → 後續衍生提示(多數復用現有碰線);引擎無部位
  概念 + CLAUDE.md 約束不下單,最多到「提示」。
- **絕對量門檻**:主峰不要求絕對放量,只看相對量縮。
- **開盤震盪保護**:先不設,回測觀察開盤前誤觸再說。
- **單峰急垮 / 開高走低** → 未來的其他做頭積木(見未來工作)。
- **通用組合框架** → 積木夠多再做。

## 未來工作

- **做頭家族其他積木**(user 常空的形態,雙峰抓不到):
  - **一峰急垮**:主峰後不彈次頭、直接崩(等不到次峰)。
  - **開高走低**:09:00 即當日最高、整天陰跌(無盤中衝高、無主峰)。
- **策略4 外盤轉弱**:order flow(打外盤 < 打內盤),不可回測、實盤驗。
- **可組合積木框架**:讓上述積木 + 造山自由 AND/OR 組合成高置信訊號
  (如雷科 = 造山 + 外盤)。需改 strategy 獨佔架構;疊加機制(人腦看兩則 / 引擎合併推一則)那時定。

## 流程與驗收

1. TDD 寫 `PeakDetector` + `condition.py` model + `signal_engine` evaluator/狀態
2. 擴 `replay_engine.py` `--preset peak`(先確認量分攤可用)
3. 跑回測:目標案例(雷科/東捷/南茂 6/12)形態命中(先停 dev server — 腳本登入富邦)
4. 上線:UI 建規則 or strategy 寫進 config.json
5. **盤中實盤驗 + 微調參數(主力)**
6. PR 進 main

## 開放問題

1. **命名**:`peak_divergence` 暫定(造山 / 做頭 / `double_top_divergence`)。
2. **R 適配波動的具體公式**:當日 high−low 的幾 %?還是用 ATR?回測/實盤定。
3. **峰量「確認區間」邊界**:主峰量累積到何時截止(回落確認點?還是峰後固定 N 秒?)。
4. **單邊強勢盤**:「次峰過前高 → 當新主峰」會不會在一路創高的股整天不觸發?
   (預期是正確行為 —— 一直創高就是沒轉弱。)
