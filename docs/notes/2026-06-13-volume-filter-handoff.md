# 量能過濾碰線(策略 2)— 2026-06-13 handoff

## 背景

碰線訊號目前完全沒有量的概念 — `trigger_volume` 只帶單筆 tick size，引擎判碰線時不看量。
碰線無量 = 掛單磨；碰線有量 = 真攻防。策略 2 要加上量能過濾，讓碰線訊號更有價值。

來源：`docs/notes/2026-06-12-strategy-candidates.md` 策略 2。

## 設計決定

| 項目 | 決定 |
|---|---|
| 指標 | `volume_ratio = rolling N 秒量 / (day_volume / 開盤以來分鐘數)` |
| window | 用現有 `WindowSeconds`（60/180/300/600/1800），回測先跑 60s |
| 早盤門檻 | `min_elapsed_minutes` 參數，回測掃 0 / 5 / 10 三組 |
| X 倍數門檻 | 回測掃 1.5 / 2.0 / 3.0 / 5.0 |
| 規則定位 | 新建獨立規則（碰 CDP + volume_ratio AND），不取代現有純碰 CDP |
| 觸發時機 | rolling window，即時達標就觸發（不等 K 棒收盤） |

## 已完成的程式碼改動（未 commit，在 main 上）

### 1. condition.py — model 擴充
- `WindowConditionType` 加 `"volume_ratio"`
- `WindowCondition` 加 `min_elapsed_minutes: int`（0~60，預設 0，僅 volume_ratio 有效）

### 2. signal_engine.py — 引擎邏輯
- `_eval_window` 加 `volume_ratio` 分支：
  - `window_vol = sum(ticks in last N seconds)`
  - `avg_per_min = day_volume / minutes_since_open`
  - `ratio = window_vol / avg_per_min`
  - `min_elapsed_minutes` 門檻：開盤後經過分鐘數 < max(min_el, 1) 時 return False

### 3. replay_engine.py — 回測基礎設施
- `fetch_fubon`：candle tuple 加第 6 欄 `volume`
- `candles_to_ticks`：回 `(epoch, price, volume)`，candle volume 平分 4 筆（餘數給最後一筆）
- `replay_day`：`Tick(size=vol)` 取代 `size=1`
- `touch_with_volume_rule`：碰 CDP + volume_ratio AND 複合規則工廠
- `--preset volume`：掃描矩陣（min_elapsed × X_multiplier），含 per-symbol 明細
- 既有 `--preset touch` / `--preset crash` 也受益於 volume 分攤（tick 不再全是 size=1）

### 4. 單元測試驗證
- volume_ratio ≥ 2.0，實際 3x 均量 → True
- volume_ratio ≥ 5.0，實際 3x 均量 → False
- min_elapsed=5 在 09:03（3 分鐘）→ False
- min_elapsed=0 在 09:03 → True
- min_elapsed=5 在 09:13（13 分鐘）→ True

## 待做

### 立即（下個交易日盤中）
```powershell
cd backend
.venv\Scripts\python scripts/replay_engine.py --preset volume
```
輸出：3 組 min_elapsed（0/5/10）× 4 個 X 門檻（1.5/2.0/3.0/5.0）的矩陣 + per-symbol 明細。

### 回測後
1. 分析數據 → 決定預設 X 和 min_elapsed
2. 寫 spec（含回測結論佐證參數選擇）
3. 完整實作（前端 UI 規則設定 + config.json 範例）
4. PR

## 已知限制
- 回測用 1 分 K 近似：volume 平分 4 筆抹平了量的瞬間集中
- `day_volume` 包含當前 window 的量，分母偏高、ratio 略保守（一致性無問題）
- 富邦 SDK 週末/盤後 WebSocket 關閉，`FubonSDK()` constructor 連初始化都過不了
