# 台股每週市場研究 Skill — 設計與分數模型

**Date**: 2026-05-27(spec) / 2026-05-28(skill 完成)
**Status**: Skill 已完成(Phase 1-3 done);Phase 4-7 待做(fetcher / score service / 視覺風格 skill)
**Branch**: feat/monitor-list-and-discord-notify(目前所在,skill 不影響此 branch)
**Skill 位置**: `~/.claude/skills/tw-market-research-distilled/`(SKILL.md + templates.md + vocabulary.md)
**TDD 紀錄**: RED baseline → GREEN(17/17 pass)→ REFACTOR(P1 buy/sell PASS、P2 incomplete data PASS、P3 simplify 第一輪 FAIL → SKILL.md 補 letter override loophole → 重跑 PASS)

## Summary

把一份 ChatGPT 生成的「台指期 / 台股高概率路徑研究」6 圖卡(2026/05/25 版本)逆向蒸餾成可
重用的 skill。Skill 接受「當週量化資料 + 自製分數 + 焦點 framing」三類輸入,產出符合同
一套**紀律語法**的研究文字(預設不出圖,圖卡另開 spec)。

同時設計一份**自製分數模型**(亞洲熱錢 4 模組 + RRI 反身性指數)取代原 ChatGPT 自創的
無公式分數,讓分數可程式化計算、可入庫、可回測。

## Goals

- 蒸餾「分析思維 + 資訊架構 + 視覺風格」三層,先以前兩層為主
- 設計一份可實作的分數模型 spec(4 模組 + RRI),取代 ChatGPT 自創無公式版本
- 釐清資料來源四分類(trading-king 現成 / 寫得出來 / 接外部 API / 必須 user 提供)
- 用 writing-skills TDD 流程做 baseline + skill v1 + refactor
- Skill 最終 deliverable 是純文字研究,圖卡產出由 ChatGPT/設計工具另行處理

## Non-goals

- **不做圖卡產出**:本 skill 只到「結構化文字 + Markdown 表格」,出圖在另開 spec
- **不實作 fetcher**:本 spec 只設計分數模型,fetcher 寫進另一份 implementation spec
- **不做即時推播**:這是「週節奏」研究,不是 monitor signal
- **不重寫既有 fubon_* service**:沿用,只在缺資料時往外接 TAIFEX / TWSE / FRED / yfinance

## Background — 6 圖逆向工程結果

原始研究是 6 張 cyber-style dashboard 卡(2026/05/25 22:28 台灣時間基準),涵蓋:

| # | 圖名 | layout 類型 |
|---|---|---|
| 1 | 半導體 / 封測 / 記憶體 | 三欄族群 × 6 row(現況 / 風險 / 目標 / 合理位置 / 高概率走勢) |
| 2 | 台指期風險圖 + 選擇權買方避險布局 | 6 編號區塊 + 中央價位地圖 + 底部警示 |
| 3 | 總覽 | hero(主結論 + 環圖分數 + 趨勢)+ 三欄判讀(知道/推論/警戒)+ 風險條帶 |
| 4 | 未來 6 日高概率路徑 | 時間軸 × 條件分歧 flowchart(綠多 / 紅空 雙路徑) |
| 5 | 驅動因子與背離 | 左綠驅動 / 右紅背離 split panel + 失效條件 + 事件 timeline + Unknown |
| 6 | 加權 / 台積電 / 電子權值股 | 三欄個股 × 4 row + 底部 dashboard 列 |

共同元素(視覺風格 layer):cyber neon 配色、深紫黑底、霓虹漸層標題、card 化、icon 系統、
色彩語意系統(綠多 / 紅空 / 黃警戒 / 橘事件 / 青接受 / 灰 Unknown)。

## 三層蒸餾 Scope

### A. 分析思維(最 portable)
- 三檔位階法:**防線 / 接受測試 / 失守風險**(每次研究找出三組數字)
- 條件成立才右側(不猜頂底)
- 核心句模板:`{level} 未接受不追高多;{level} 未破不追低空`
- 雙路徑分歧(綠延伸 / 紅震盪),每條路有「進入條件 + 目標位階」
- 4 模組分數 + RRI 反身性風險(獨立計算)
- regime label(中性偏多 / 熱錢修復、REDUCED SIZE / EVENT MODE ONLY 等)
- 失效條件(this thesis breaks if...)
- 我知道 / 我推論 / 我警戒 三段判讀

### B. 資訊架構(6 圖節奏)
- 圖 1 = 族群結構(本週 focus 的 2-3 個族群)
- 圖 2 = 風險地圖 + 避險布局
- 圖 3 = 總覽 dashboard
- 圖 4 = 時間軸路徑
- 圖 5 = 驅動因子 vs 背離 + 失效條件 + 事件
- 圖 6 = 主引擎個股(加權 / 台積電 / 權值)

每張圖的 row 結構固定,值得寫進 skill 當 template。

### C. 視覺風格(優先度最低)
- 此 skill 暫不負責出圖
- 但 skill 可附帶「視覺敘述」段落供出圖時參考(配色 / icon / layout 描述)

## 分數模型設計(取代 ChatGPT 自創版)

### 設計原則
1. 每個模組 0-100,**越高 = 越友善(對多方延伸)**(例外:RRI 越高 = 風險越高)
2. 用百分位數(percentile)做 normalization — 把任意單位轉成「過去 252 交易日中的位置」
3. 每個模組由多個 indicator 加權平均,**不靠單一指標**
4. 主分 = 4 模組等權平均(0.25 × 4),簡單可解釋
5. RRI 獨立計算,不進主分

### Module 1:AsiaFX(亞洲匯率強度)
量「亞洲貨幣是否強 → 熱錢是否流亞洲」

| Indicator | 計算 | 方向 | 權重 |
|---|---|---|---|
| DXY 5D 變動 | `percentile(−5D%, 252D)` | 跌=高分 | 25% |
| USDTWD 5D 變動 | `percentile(−5D%, 252D)` | 跌=高分 | 30% |
| USDKRW 5D 變動 | `percentile(−5D%, 252D)` | 跌=高分 | 15% |
| USDCNH 5D 變動 | `percentile(−5D%, 252D)` | 跌=高分 | 15% |
| ADXY 水準 | `percentile(level, 252D)` | 高=高分 | 15% |

### Module 2:USMacroShock(美國總經衝擊)
量「美國總經對 risk asset 的友善度」 — 注意方向:**分數高 = 鴿派 / 降溫**

| Indicator | 計算 | 方向 | 權重 |
|---|---|---|---|
| 10Y UST 5D 變動 | `percentile(−5D bp change, 252D)` | 降=高分 | 30% |
| Core CPI YoY surprise | `(expected − actual) / expected` normalized | 低於預期=高分 | 20% |
| PCE YoY surprise | 同上 | 低於預期=高分 | 20% |
| Fed funds futures 下次決議鴿派概率 | `percentile(rate-cut prob, 90D)` | 鴿=高分 | 15% |
| VIX 水準 | `percentile(−VIX, 252D)` | 低=高分 | 15% |

### Module 3:SemiTaiwan(台灣半導體強度)
量「半導體鏈整體強度 → 加權的核心 driver」

| Indicator | 計算 | 方向 | 權重 |
|---|---|---|---|
| SOXX 5D return | `percentile(5D return, 252D)` | 漲=高分 | 25% |
| SOXX 20MA vs 50MA | `binary(20MA > 50MA) × 100` | 是=100 | 15% |
| TSM ADR 5D return | `percentile(5D return, 252D)` | 漲=高分 | 20% |
| 台積電現股 5D return | `percentile(5D return, 252D)` | 漲=高分 | 20% |
| NVDA 5D return | `percentile(5D return, 252D)` | 漲=高分 | 20% |

### Module 4:LocalConfirm(本地確認)
量「台股內部結構是否確認此次行情 — 籌碼 + 廣度 + 資金面」

| Indicator | 計算 | 方向 | 權重 |
|---|---|---|---|
| 外資現貨買賣超 5D 累計 | `percentile(5D sum, 252D)` | 買=高分 | 25% |
| 三大法人合計 5D 累計 | `percentile(5D sum, 252D)` | 買=高分 | 15% |
| 上漲家數 / (漲+跌)家數 | `ratio × 100`(當日) | 高=高分 | 20% |
| 均線以上家數 % | `ratio × 100`(當日) | 高=高分 | 20% |
| 市場差值(現貨−期貨) | `percentile(level, 60D)` | 高=高分 | 20% |

### 主分

```
Main Score = (AsiaFX + USMacroShock + SemiTaiwan + LocalConfirm) / 4
```

驗算 2026/05/25:`(62+52+78+70)/4 = 65.5` ≈ 圖中 64(差 1.5 分,合理偏差)

### RRI(市場反身性風險指數)— 獨立
量「漲多了大家就追、追了就更漲」這種脆弱循環

| Indicator | 計算 | 方向 | 權重 |
|---|---|---|---|
| 加權指數 RSI(14D) | `max(0, (RSI−50)×2)` | 超買=高分 | 20% |
| 台積電 RSI(14D) | 同上 | 超買=高分 | 15% |
| SOXX RSI(14D) | 同上 | 超買=高分 | 15% |
| 5D 漲幅 ÷ 量能放大倍數 | `percentile(漲/量, 252D)` | 量沒跟上=高分 | 15% |
| 融資餘額 5D 變動 | `percentile(5D %change, 252D)` | 散戶追=高分 | 15% |
| 期貨溢價(basis)偏離 | `percentile(basis − basis_60d_mean, 60D)` | 偏高=高分 | 10% |
| 上漲家數 vs 加權漲幅背離 | `percentile(漲幅 − 廣度, 252D)` | 集中拉抬=高分 | 10% |

**分級**
- 0-40 正常
- 40-65 留意
- 65-80 偏高反身性
- **80+ 高反身性風險**(驗算 2026/05/25 圖中 82)

### 命名澄清

第 3 張第三個模組叫 `SemiTaiwan`,第 6 張叫 `LocalTaiwan`,**統一採用 SemiTaiwan**
(更精準描述其量的內容,且避免跟 `LocalConfirm` 語意混淆)。

## Skill Input / Output

### Skill Input(每次研究)

```yaml
date: 2026-05-27           # 研究日(資料基準)
period: 2026-05-27..2026-06-03   # 涵蓋未來 N 個交易日

# 4a — 自製分數(MVP 階段由 user 提供,Phase 3 後由 score service 算)
scores:
  asia_fx: 62
  us_macro_shock: 52
  semi_taiwan: 78
  local_confirm: 70
  rri: 82

# 4c — 當週 framing(必須 user 提供)
framing:
  regime: "中性偏多 / 熱錢修復"
  trading_permission: "REDUCED SIZE / EVENT MODE ONLY"
  focus_sectors: [半導體, 封測, 記憶體]
  focus_stocks: [加權, 台積電, 電子權值]

# 4b — 質性敘事(必須 user 提供)
narratives:
  - "FOPLP / AMD AI 投資與 TSMC-ASE 合作為中期題材"
  - "Nvidia 80B buyback 對 AI 鏈 sentiment 正面"

# 三檔位階(必須 user 提供 — 此為紀律核心)
levels:
  defense: [44000, 43800]         # 防線
  acceptance_test: [44400, 44600]  # 接受測試
  break_risk: [43800, 43200]       # 失守風險
  upper_targets: [44800, 45000, 45200, 45600]
  lower_targets: [43600, 43200, 42800, 42400]

# 事件 calendar(必須 user 提供,Phase 3 後可自動)
events:
  - {date: 2026-05-28, name: PCE, type: macro}
  - {date: 2026-05-29, name: "週選 / 月末倉位調整", type: structural}
  - {date: 2026-06-01, name: "PCE 後方向確認", type: confirm, end: 2026-06-02}

# 量化資料(MVP 階段由 user 貼,Phase 2+3 後 fetcher 自動抓)
data:
  taiwan: { ... }   # 加權、台積、聯發、南亞科...
  futures: { ... }  # TXF、外資未平倉、加空、市差
  options: { ... }  # TXO P/C、外資/自營部位
  us: { ... }       # SOXX、QQQ、TSM ADR、NVDA
  macro: { ... }    # DXY、USDTWD、油價、利率、CPI...
```

### Skill Output(每次研究)

6 段 Markdown,每段對應一張圖的「文字版」:

```markdown
## 1. 族群結構 — {focus_sectors}
[三欄,每欄 6 row]

## 2. 風險地圖 + 選擇權避險布局
[價位地圖 + 4 種布局 + 失效條件 + 警示]

## 3. 總覽
[主結論 + 4 模組分數 + RRI + 我知道/我推論/我警戒]

## 4. 未來 N 日高概率路徑
[第一段條件列表 + 第二、三段雙路徑分歧]

## 5. 驅動因子與背離
[左綠驅動 + 右紅背離 + 失效條件 + 事件 + Unknown]

## 6. 主引擎個股 — {focus_stocks}
[三欄,每欄 4 row + 底部分數列]
```

## 紀律語法(寫進 skill 的 vocabulary)

Skill 必須只用以下語法寫研究(防止亂發明):

**位階用詞**:防線 / 接受測試 / 失守 / 站穩 / 跌破 / 反抽不回 / 突破延伸 / 回測 / 深回測
**狀態用詞**:中性偏多 / 中性偏空 / 強多延伸 / 高檔過熱 / 高檔派發 / 高反身性 / 熱錢修復
**動作用詞**:右側交易 / 不追高 / 不擔低 / 條件成立 / 守紀律
**結論句模板**:`{level} 未接受不追高多;{level} 未破不追低空`

**核心 framework — 三檔位階法**

每次研究都要找出三組數字:
1. **防線**(2 個):跌破第一道 = 結構轉弱
2. **接受測試**(2 個):站上第一道 = 確認延伸
3. **失守風險**(2 個):跌破第二道 = thesis 失效

## 6 圖 layout pattern

| 圖 | Layout 名 | Row / Column 結構 |
|---|---|---|
| 1 | `sector-triple-column` | 3 col × {現況/風險/目標/合理位置/高概率走勢} |
| 2 | `risk-map-with-hedge` | 6 編號區塊 + 中央地圖 + 警示 |
| 3 | `dashboard-overview` | hero + 環圖 + 三欄判讀 + 風險條帶 |
| 4 | `timeline-paths` | 時間軸 × 3 段 × 雙路徑(綠延伸 / 紅震盪) |
| 5 | `split-driver-divergence` | 左綠驅動 / 右紅背離 + 失效 + 事件 + Unknown |
| 6 | `engine-stocks-triple` | 3 col 個股 × 4 row + 底部 dashboard |

每個 layout 都有對應的 Markdown template,寫進 skill。

## Phase 化實作計畫

| Phase | 範圍 | 預估 | Deliverable |
|---|---|---|---|
| **Phase 1**(此 spec) | 設計分數模型 + skill scope + TDD baseline | done | 這份 spec |
| **Phase 2** | 寫 skill v1(MVP)— user 提供所有資料,skill 做 framework | 1 session | `~/.claude/skills/tw-market-research-distilled/SKILL.md` |
| **Phase 3** | TDD baseline + iterate(跑 subagent scenario) | 1 session | refactor 過的 skill |
| **Phase 4** | 寫分數計算 service(`backend/services/composite_scores.py`) | 1-2 session | 可呼叫的 API |
| **Phase 5** | 寫外部資料 fetcher(TAIFEX / TWSE / FRED / yfinance) | 2-3 session | 自動填 input |
| **Phase 6** | 接 trading-king + 自動化 pipeline | 1 session | end-to-end |
| **Phase 7**(選做) | 視覺風格 skill(C 層蒸餾,for 出圖工具) | 1 session | 另一個 skill |

## TDD Test Scenarios(initial draft)

### Baseline scenario(RED — 沒 skill 時 subagent 怎麼做)

> Prompt: 「以下是 2026/05/25 收盤後的量化資料,請幫我寫一份未來 6 個交易日的台股研究,
> 涵蓋方向判斷、風險、目標、避險布局。」+ 完整資料

預期 baseline 會犯的錯:
- 寫成投顧文,「我認為應該逢低布局」這種主觀建議,不是條件式紀律
- 沒有三檔位階法(防線 / 接受測試 / 失守風險)
- 沒有雙路徑分歧
- 結論句格式不一致,沒有「未接受不追高多;未破不追低空」
- 對「反身性」「高檔派發」這種術語沒概念
- 直接 recommend buy/sell 不寫條件

### GREEN scenario(skill 在的時候)

跑同樣 prompt,subagent 應該:
- 自動找出三檔位階
- 寫雙路徑分歧
- 用紀律語法
- 6 段對應 6 圖
- 結論句符合模板

### Pressure scenarios(REFACTOR — 找漏洞)

- 「你能不能直接告訴我該買還是不買?」(對抗紀律語法)
- 「這些紀律太囉嗦了,簡單點」(對抗結構完整性)
- 給壞資料(分數缺、levels 沒提供),看 skill 是否要求補
- 給超出範圍的事件(地緣戰爭),看 skill 怎麼處理

## Open Questions

1. **Skill 名稱**:`tw-market-research-distilled` 還是 `taiwan-weekly-pathway-research`?
2. **語言**:skill body 用繁中還是英文?(內容生成必然繁中,但 trigger 描述呢?)
3. **是否同步寫一份「視覺風格 skill」**(C 層)給 ChatGPT/設計工具用?
4. **分數模型 RRI 第 6、7 個 indicator(basis 偏離、廣度背離)是否難算?** 若難,可砍至 5 個。
5. **MVP 是否真的「user 貼所有資料」?** 還是先寫個薄薄的 fetcher 抓最痛的幾個(美股、匯率)?
6. **歷史 backtest**:分數模型出來後,要不要回測過去 1 年的分數是否能解釋走勢?
