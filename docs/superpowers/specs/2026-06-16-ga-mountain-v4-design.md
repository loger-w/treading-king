# 造山積木 v4 GA 參數優化設計 — 2026-06-16

> 反向 spec：程式碼（`backend/scripts/_ga_mountain_v4.py`）先於本文件產出，
> 本文件記錄設計決策並附正確性審查結果。

## 目的

用遺傳演算法搜索造山積木 v4 的 6 個參數最佳組合。以每日盤後 cache（獨立篩股池 + 1 分 K）
為回測資料，累積多日後跑 walk-forward 避免單日過擬合。

## 染色體

| # | 基因 | 範圍 | 步長 | 值數 | 對應 Engine 常數 |
|---|------|------|------|------|-----------------|
| 0 | `surge_pct` | 2.0 – 5.0 | 0.5 | 7 | `MOUNTAIN_SURGE_PCT` |
| 1 | `surge_window` | 5 – 20 | 1 | 16 | `MOUNTAIN_SURGE_WINDOW` |
| 2 | `surge_vr` | 1.0 – 3.0 | 0.25 | 9 | `MOUNTAIN_SURGE_VR` |
| 3 | `confirm_vr` | 0.0 – 1.0 | 0.25 | 5 | `MOUNTAIN_CONFIRM_VR` |
| 4 | `re_surge_margin` | 0.0 – 0.5 | 0.1 | 6 | `MOUNTAIN_RE_SURGE_MARGIN` |
| 5 | `noise_pct` | 0.2 – 1.0 | 0.1 | 9 | `_SURGE_BASE_NOISE_PCT`（module-level） |

**固定不搜索**：`min_bars=3`、黑K 1 根確認、非黑K 2 根確認。

搜索空間：7 × 16 × 9 × 5 × 6 × 9 = **272,160** 組合。

**範圍選擇理由**：

| 基因 | 下界理由 | 上界理由 |
|------|----------|----------|
| `surge_pct` 2.0% | < 2% 在台股 tick 下是雜訊（$50 股票的 2% = $1 = 2 個 tick） | 5% 已是極端急拉 |
| `surge_window` 5 | 開盤前幾根 price discovery 雜訊需跳過 | 20 根 = 20 分鐘，超過已不算「急」拉 |
| `surge_vr` 1.0 | 1.0 = 平均量，低於此非「出量」 | 3.0 = 3 倍量，再高太嚴格 |
| `confirm_vr` 0.0 | 0.0 = 停用量比門檻（任何黑 K 確認） | 1.0 = 需高於均量才算有效賣壓 |
| `re_surge_margin` 0.0 | 0.0 = 任何新高就重置 | 0.5% = 需實質突破才重置 |
| `noise_pct` 0.2 | 最小有意義的雜訊門檻 | 1.0% 以上會漏掉合法的相對低點 |

## 適應度函數

```
fitness = 真做頭數 × abs(平均山後跌幅%) − α × 假山數
```

| 項目 | 定義 |
|------|------|
| 真做頭 | 山頂 = 當日最高價（`abs(peak_high - day_high) < 0.01`，浮點安全網 — 實測同源 float 精確相等） |
| 假山 | 山頂後被超越 |
| 山後跌幅 | `(closePrice / peak_high − 1) × 100`（當日收盤價 vs 山頂） |
| α | CLI `--alpha`，預設 1.0；加大懲罰假山 → 推高精準率 |

**設計選擇**：跌幅用收盤價而非盤中最低價。收盤價確定性高，貼近實際交易情境（山頂確認後放空、收盤回補）。後續版本可改為「山後出量下殺低點」（需在確認後繼續掃描 candles 找 vr≥1 且 close<open 的最低點）。

**維度解讀**：`真 × |跌%|` 單位是 `次·%`，`α × 假` 單位是 `次`。α=1 意味「1 個假山的懲罰 = 1 個真做頭 × 1% 跌幅的獎勵」。若平均跌幅 -3.5%，每個真做頭值 3.5 個假山。α 越大越重視精準率（少假山），越小越重視召回率（多真做頭）。

## GA 機制

| 項目 | 設計 | 理由 |
|------|------|------|
| 族群 | 50（CLI `--pop`） | 搜索空間 27 萬，50 個已足夠覆蓋 |
| 世代 | 100（CLI `--gen`） | 實測 gen 10 收斂，100 代確認穩定 |
| 菁英 | 5（前 10%） | 防止最佳解被突變破壞 |
| 選擇 | Tournament k=3 | 適度選擇壓力 |
| 交叉 | Uniform（每基因 50/50 來自雙親） | 基因間獨立性高，無需保留鄰近連鎖 |
| 突變 | Per-gene 20% 隨機跳到合法值 | 維持多樣性，離散值不適合高斯突變 |
| 快取 | `dict[tuple, result]` | 相同染色體不重複評估 |
| Seed | V4 預設值注入 `pop[0]` | 確保基準被評估、提供好的起點 |
| 覆蓋率 | ~1,600 獨立染色體 / 272K 空間 ≈ 0.6% | GA 靠選擇壓力聚焦有效區域，非暴力搜索 |
| Top-10 | 從全歷史 eval_cache 選（含早期被淘汰個體） | 不限於最終族群，找全局最佳 |

## Inline 造山偵測

為了速度，`eval_chromosome()` 不建立 `SignalEngine` 物件，直接 inline 四個核心函式。
以下為逐行比對 `signal_engine.py` 的審查結果。

### `_find_surge_base` — GA L128-138 vs Engine L57-74

GA 用 `recent_closes[0..nb-1]` index 操作代替 `recent_closes[:-1]` slice 複製。

```
nb = n_rc - 1        # = len(recent_closes[:-1])
recent_closes[nb-1]  # = recent_closes[:-1][-1]
```

- `nb=0`（空）→ `base=0.0` ✓ 對應原始 `not closes → 0.0`
- `nb=1`（1 元素）→ `base=recent_closes[0]` ✓ 對應原始 `len<=1 → closes[0]`
- `nb>=2` → 從末尾往回掃，碰到 `> running_min × noise` 就 break ✓

**結論：等價。**

### `_detect_surge` — GA L125-140 vs Engine L697-715

原始分三步 early return：min_bars 門檻 → base > 0 → rise_pct + VR。
GA 合併為一個 `if` 條件。

```python
# GA
if base > 0 and (h / base - 1) * 100 >= surge_pct - 1e-9 and vr >= surge_vr:
    is_surge = True
```

- `h` = `candle.high` ✓
- `base` = `_find_surge_base(recent_closes[:-1])` ✓
- `surge_pct - 1e-9` 浮點容差 ✓
- VR 門檻 ✓

**結論：等價。**

### `_candle_volume_ratio` — GA L122 vs Engine L717-726

原始：`candle.volume / (day_vol / elapsed)`
GA：`vol * emin / day_vol`（代數簡化）

- `emin` = `_minutes_since_open(now)` 的整數近似（seconds 成分被丟棄；candle 時戳為 HH:MM 無秒數，影響極小）
- `emin >= 1` 近似原始 `elapsed < 1` 檢查（整數 vs 浮點）
- `day_vol` 從 candle 累積量重建（`cum[i] + vols[i+1]//4`），非 engine 的 tick-level 累積

**結論：代數簡化正確，但輸入為近似值（VR 差異 < 2%）。足夠用於參數搜索。**

### `_update_mountain` — GA L142-177 vs Engine L634-695

GA 走 `use_graded=True` 路徑（不傳 `confirm_bars`）：

| 原始 | GA | 等價 |
|------|-----|------|
| `cb = 2` | `nhh >= 2` | ✓ |
| `is_black and vr >= CONFIRM_VR` → 立即確認 | `c < o and vr >= confirm_vr` | ✓ |
| `peak_vr = max(peak_vr, vr)` | `if vr > peak_vr: peak_vr = vr` | ✓ |
| `candle.high > peak_high * margin` → re-surge | `h > peak_high * (1 + re_surge_margin / 100)` | ✓ |

省略 `peak_minute` / `confirmed_minute`（適應度不需要）。

Engine 的 `_detect_surge` 內部再算一次 VR，GA 共用同一個 `vr` 變數。
因為 `_day_volume` 在兩次呼叫間不變，VR 值相同，共用正確。

**結論：等價。**

### 分類邏輯

山頂確認當下立即分類（用預算好的 `day_high`），等價原始回測的事後分類。
若後續 re-surge 產生新山頂：舊山已分類為假山（正確），新山另行分類。

## 多日支援

```bash
python scripts/_ga_mountain_v4.py --days 2026-06-16,2026-06-17,2026-06-18
```

載入多日 cache，適應度跨日加總（所有天的真做頭數 / 假山數 / 跌幅一起算）。
目前為 in-sample 全日優化，不做 train/validation split。

**Walk-forward 預期用法**：累積 5+ 天後，手動拆前 N 天 train + 最後 1 天 validation，比較 fitness 是否一致。

## 資料來源

每日 cache 由 `backend/scripts/_daily_pool_fetcher.py` 產出：

```
_diag_auto_monitor_cache_{DAY}.json
├── DAY: "2026-06-16"
├── cands: [sym, ...]           # 篩選後候選清單
├── pool: {sym: snapshot}       # 快照（含 closePrice/change/highPrice 等）
├── minute: {sym: {day: [candles]}}  # 多日 1 分 K
└── cdp: {sym: {ah,nh,cdp,nl,al,prev_close,as_of}}  # CDP 五線（GA 不讀）
```

篩選條件：振幅 > 3% + 量 > 3000 張 + 4 位純數字 + 排除處置股。

## 輸出

### 終端

- 每 10 代：best/avg fitness、真/假山數、跌幅、耗時、cache 大小
- 結束：最佳參數、vs 基準差異、Top 10、α 敏感度、基因收斂度

### 檔案

`_ga_result_{days}.json`：

```json
{
  "days": [...], "alpha": 1.0, "pop": 50, "gen": 100, "seed": 42,
  "baseline": {"params": [...], "fitness": ..., "true_tops": ..., "false_tops": ..., "avg_drop": ...},
  "best": {"params": [...], "fitness": ..., "true_tops": ..., "false_tops": ..., "avg_drop": ...,
           "per_day": [["2026-06-16", 67, 106, -3.1]]},
  "top10": [{"params": [...], "fitness": ..., "true_tops": ..., "false_tops": ..., "avg_drop": ...}, ...],
  "history": [[gen, best_fit, avg_fit], ...],
  "total_evals": 1624, "elapsed_sec": 98.0
}
```

## 已知限制與後續

| 限制 | 說明 | 後續 |
|------|------|------|
| 單日過擬合 | 單日 303 檔不足以泛化 | 累積 5+ 天 walk-forward |
| 跌幅用收盤價 | 不反映盤中最大回撤 | 可改「山後出量下殺低點」 |
| 單執行緒 | 50×100 跑 ~100 秒 | 如需更大搜索加 multiprocessing |
| 不含 CDP | GA 只優化造山偵測 | CDP 給碰線/突破策略另行回測 |
| in-sample 全日 | 無 validation split | 多日後手動拆 train/val |
| α 隨波動率漂移 | avg_drop -5% 日每個真做頭值 5 個假山，-2% 日只值 2 個；多日混合時 fitness 排名不穩定 | 如 walk-forward 跨高低波動日，考慮改用 F-beta 或 precision × f(avg_drop) 正規化 |
| 單鏈偵測 | phase 不會從 confirmed 重置回 idle；同一檔第二波獨立急拉只能靠 re-surge margin 偵測 | 可加 idle reset（N 根無活動後），但需避免重複計數 |

## 6/16 單日結果參考

| α | 最佳 surge_pct | window | surge_vr | confirm_vr | margin | noise | 真 | 假 | 跌 | fitness |
|---|---|---|---|---|---|---|---|---|---|---|
| 1.0 | 2.5% | 7 | 1.00 | 1.00 | 0.2% | 0.6% | 93 | 146 | -3.5% | 181.0 |
| 2.0 | 3.5% | 5 | 1.00 | 1.00 | 0.2% | 0.5% | 52 | 56 | -3.5% | 69.1 |

收斂穩定（不受 α 影響）：`surge_vr→1.00`、`re_surge_margin→0.2%`、`noise_pct≈0.5-0.6%`。
α 敏感：`surge_pct`（2.5% vs 3.5%）、`surge_window`（7 vs 5）。

基準（v4 預設）：fitness=103.6 | 真=67 假=106 跌=-3.1%。

## 改動位置

| 檔案 | 狀態 |
|------|------|
| `backend/scripts/_ga_mountain_v4.py` | 已實作（本 spec 為反向記錄） |

## 反身性審查紀錄

1. 適應度維度不匹配 — 補充 α 的實際 trade-off 解讀
2. 基因範圍理由 — 補充每個上下界的選擇理由表
3. `< 0.01` 容差 — 標註為浮點安全網（實測 near_but_not_exact = 0）
4. 搜索覆蓋率 — 記錄 ~0.6%，說明 GA 靠選擇壓力而非暴力覆蓋
5. Top-10 來源 — 說明從全歷史 cache 選，非僅最終族群

### Ultracode 多 agent 審查（28 agents, 36 findings → 17 refuted, 6 confirmed）

6. VR 等價聲明過強 — 改為「代數簡化正確，輸入為近似值」，列出兩個近似來源
7. emin 整數近似 — 改為「近似」非「等價」，說明 seconds 被丟棄但影響極小
8. 已知限制補 α 波動率漂移 — 多日 walk-forward 時 α 的有效 tradeoff 隨 avg_drop 變化
9. 已知限制補 single-chain-per-stock — phase 不重置回 idle
10. 輸出 JSON 範例補 per_day 欄位
