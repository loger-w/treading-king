# 每日盤後資料收集 + CDP 設計 — 2026-06-16

> 改動 `backend/scripts/_daily_pool_fetcher.py`，在現有篩股池 + 1 分 K 之外加存 CDP 五線。
> 目的：累積多日資料，給 GA 多日 walk-forward 回測 + 後續碰線/突破策略回測用。

## 現狀

`_daily_pool_fetcher.py` 每天盤後手動執行，產出 `_diag_auto_monitor_cache_{DAY}.json`：

```json
{
  "DAY": "2026-06-16",
  "cands": ["2399", ...],
  "pool": { "2399": { "highPrice": ..., "closePrice": ..., ... } },
  "minute": { "2399": { "2026-06-16": [["09:00", O, H, L, C, V], ...], "2026-06-15": [...] } }
}
```

富邦 1 分 K API 回傳 ~22 天歷史，`minute[sym]` 含前一日資料。**CDP 不在其中。**

## 改動

### 1. 新增 `cdp` 欄位

`fetch()` 完成後，對每檔 `cands` 計算 CDP 並存入 cache：

```json
{
  "DAY": "2026-06-16",
  "cands": [...],
  "pool": {...},
  "minute": {...},
  "cdp": {
    "2399": {
      "ah": 55.3, "nh": 53.8, "cdp": 51.8, "nl": 49.8, "al": 48.3,
      "prev_close": 48.0, "as_of": "2026-06-15"
    }
  }
}
```

欄位說明：

| 欄位 | 說明 |
|------|------|
| `ah` / `nh` / `cdp` / `nl` / `al` | CDP 五線，已對齊台股 tick（`round_to_tick_tw`） |
| `prev_close` | 前一日收盤價 — 取自 `pool[sym]["closePrice"] - pool[sym]["change"]`（官方收盤，比 1 分 K 最後一根更穩） |
| `as_of` | 計算用的前一日日期（通常 = DAY − 1 個交易日） |

### 2. CDP 計算來源

**主路徑（免費，不增加 API call）**：從 `minute[sym]` 中取日期 < DAY 的最近一天 1 分 K，推算 daily OHLC：

```python
prev_candles = minute[sym][prev_day]  # 前一日的 1 分 K
H = max(c[2] for c in prev_candles)   # max high
L = min(c[3] for c in prev_candles)   # min low
C = prev_candles[-1][4]               # last close
```

然後套 `from services.cdp import compute_cdp`：

```python
levels = compute_cdp(H, L, C)
# → {"ah": ..., "nh": ..., "cdp": ..., "nl": ..., "al": ...}
```

**無前一日資料時**：跳過該檔，不產 CDP。實測 6/16 cache 303/303 檔都有前一日 1 分 K（API 回傳 ~22 天），缺漏率極低，不值得為 fallback 增加 API call 複雜度。

### 3. 前一交易日判定

```python
prev_days = sorted([d for d in minute[sym] if d < DAY], reverse=True)
prev_day = prev_days[0] if prev_days else None
```

直接從 `minute` 裡已有的日期取最近一天，不需要自行處理假日邏輯。

### 4. 向下相容

- GA 腳本（`_ga_mountain_v4.py`）不讀 `cdp` key，完全不受影響
- 已有的 6/16 cache 沒有 `cdp` → GA `--day 2026-06-16` 照常運作
- 回測腳本（`_diag_auto_monitor_backtest.py`）也不受影響

### 5. 輸出統計

腳本結束前印 CDP 覆蓋率：

```
✓ CDP: 298/303 檔 (98.3%) | 缺: 2399, 6116, ...
```

## 不做的事

- 不存 VWAP — 從 1 分 K 即時算
- 不存 SMA — 另一個 API endpoint，不在這次範圍
- 不改 GA 適應度函數
- 不加排程自動化 — 手動執行
- 不修改 6/16 已有的 cache — 舊格式照用

## 改動位置

| 檔案 | 改動 |
|------|------|
| `backend/scripts/_daily_pool_fetcher.py` | `fetch()` 後加 CDP 計算 + 存入 cache |

新增 import：`from services.cdp import compute_cdp`（已有 `sys.path.insert` 設定 BACKEND）。

## 自我審查紀錄

- fallback API call 移除 — 303/303 覆蓋率下無必要，避免過度設計
- `prev_close` 改從 snapshot `closePrice - change` 取（實測與 1 分 K close 完全一致，但語義更清楚）
- 前一日 H/L 僅有 1 分 K 一個來源，無法交叉驗證，但覆蓋完整盤中成交，準確度足夠
