# Signal Rules Probe 強化設計

> Date: 2026-05-13
> Scope: 兩支盤後可跑的 probe — `probe_signal_engine.py` 擴充 + `probe_replay_ticks.py` 新增
> Est: ~0.5 天

## 1. 背景

Phase 3 完成後,訊號 evaluator (`backend/services/signal_engine.py`) 已可在盤中跑全鏈。但目前只有兩種 evaluator 驗證手段:

| 現有 probe | 範圍 | 盤後能跑? |
|---|---|---|
| `probe_signal_engine.py` | 1 種情境(`price_change_pct gt 1%` + cooldown) | ✅ |
| `probe_e2e_signal.py` | 真 WS 推 tick → engine → writer 全鏈 | ❌(需盤中真 tick) |

**目前盤後**:user 設了一條新規則,**無法驗證該規則的條件邏輯是否符合預期**,只能等下個交易日盤中觀察。

**目標**:加兩支可盤後跑的 probe,讓 user 在不開盤時就能:
1. 驗證 evaluator 對各種規則類型的處理邏輯正確(`probe_signal_engine.py` 擴充)
2. 用「今日盤後」實際市場 1 分 K 資料,replay 跑自己設的規則,看會觸發幾次、什麼時間、什麼價(`probe_replay_ticks.py` 新增)

## 2. 範圍邊界

| 屬於本 spec | 不屬於 |
|---|---|
| `probe_signal_engine.py` 加 8 個 mock test case | 跨日歷史 replay(Fubon intraday API 只有當日) |
| `probe_replay_ticks.py` 新增,讀 user 自己的 active_signals + watchlist | inline 自訂規則(透過 active_signals UI 編輯即可) |
| 用 Fubon `intraday.candles` 1 分 K 當資料源 | 用 `intraday.trades` 逐筆成交(複雜度 3 倍不值得) |
| 純報告輸出(console table) | 寫進 `signals_log`(monkey-patch `_fanout` 攔截) |
| 兩支獨立 probe(沿用既有 `scripts/probe_*.py` 慣例) | 改成 pytest 框架 |

## 3. `probe_signal_engine.py` 擴充(#1)

### 3.1 保留現有 3 case

不動 `[1] 達成條件 — 預期 fan-out` / `[2] cooldown skip` / `[3] cooldown 過期重觸發`。

### 3.2 新增 8 mock test case

所有 case 用 in-process mock,**不**碰 supabase / Fubon。預期執行 < 2 秒。

| # | 測試標的 | mock setup | 預期 |
|---|---|---|---|
| 4 | `volume_burst` window | 60s 內塞 5 個 tick volume 共 10000 | `gt 5000` → fanout |
| 5 | `trade_count` window | 60s 內塞 12 ticks | `gte 10` → fanout |
| 6 | Filter `close gt cdp_ah` 跨欄位 | `_field_cache["TEST"] = {"cdp_ah": 2280}`,tick.price=2285 | fanout |
| 7 | Filter `rsi_14 lt 30` 指標 | `_field_cache["TEST"] = {"rsi_14": 25}` | fanout |
| 8 | OR logic | 兩個 condition,只一個達成;logic="OR" | fanout |
| 8b | OR logic 反例 | 同上但 logic="AND" | skip |
| 9 | `symbols` scope | scope.type=symbols, list=["AAA"];塞 BBB tick | skip(scope 不包) |
| 10 | 多 active 各自獨立 cooldown | 兩個 active id 同 symbol,A 觸發後再灌 → A 在 cooldown / B 仍應觸發 | A skip, B fanout |
| 11 | Window 取不到資料 | `ring_buffer` empty | return False(不噴錯) |

**註**:`WindowCondition.window_seconds` 受 `WindowSeconds` enum 限制,只允許 60/180/300/600/1800 — 所有 case 必須沿用。

### 3.3 執行 / 通過標準

```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_signal_engine.py
```

每個 case 印 `✓` 或 `✗ expected X got Y`,任一 case fail → `sys.exit(1)`。全綠燈印 `All signal_engine smoke tests passed ✓`。

---

## 4. `probe_replay_ticks.py`(#2)

### 4.1 輸入(從 supabase 讀,不傳參)

- 當前 `USER_LABEL` 的 `enabled=true` active_signals(全部)
- 當前 `USER_LABEL` 的 watchlist symbols(全部)
- `_refill_field_cache()` 拉的 indicator(`indicator_cache` 最後 done date)+ CDP 5 線(`cdp` service)

### 4.2 資料源

- Fubon `intraday.candles(symbol, timeframe="1")` — 當日 1 分 K
- 每根 K 轉成 1 個 `Tick(price=close, size=volume, time=close_epoch_seconds)`
- 缺失 candles 或 API 失敗 → skip 該 symbol + 印 warning

### 4.3 流程

```
1. load_dotenv() + fubon.init() + sb.init()
   失敗 → sys.exit(1)
2. 讀 watchlist symbols + enabled active_signals
   空 → 印「nothing to replay」exit 0
3. engine = get_signal_engine()
4. await engine.refresh_active_signals()  ← 一行做 load active + _refill_field_cache
5. monkey-patch engine._fanout 改成 record-to-list 不寫 supabase
6. **安裝 FakeClock**(見 4.3.1)— monkey-patch 兩個模組的 `time` 模組
7. for symbol in watchlist:
     - candles = fubon.sdk.marketdata.rest_client.stock.intraday.candles(
         symbol=symbol, timeframe="1"
       )
     - candles 缺/錯 → warning + continue
     - rb.discard(symbol); rb.ensure(symbol)   ← 清空避免互污染
     - 依時間排序 candles
     - for c in candles:
         tick = Tick(price=c["close"], size=c["volume"], time=epoch(c["date"]))
         fake_clock.now = tick.time       ← 推進模擬時間
         rb.append(symbol, tick)
         await engine._evaluate(symbol, tick)
8. 還原 time module(finally block)
9. 印報告(見 4.4)
10. 不 shutdown engine 因為沒呼 .start()(沒 background task)
```

### 4.3.1 FakeClock — 必要的時間注入(critical)

`ring_buffer.window()` line 71 跟 `signal_engine._evaluate()` cooldown 檢查都用 wall-clock `time.time()`。replay 餵歷史時間的 candles 時:
- **`window()`** 用 wall-clock cutoff 會把所有歷史 tick filter 掉,`_eval_window` 永遠 False
- **cooldown** 在 0.5 秒 wall-clock 內灌完全天 → 首次觸發後永遠在 cooldown,後續全 skip

**做法**:用 module-level monkey-patch 把兩個模組的 `time` 屬性換成 fake clock。

```python
import services.ring_buffer as rb_mod
import services.signal_engine as se_mod

class FakeClock:
    def __init__(self): self.now = 0.0
    def time(self): return self.now

fake = FakeClock()
orig_rb_time, orig_se_time = rb_mod.time, se_mod.time
rb_mod.time = fake
se_mod.time = fake
try:
    # replay loop — fake.now = tick.time before each evaluate
    ...
finally:
    rb_mod.time, se_mod.time = orig_rb_time, orig_se_time
```

只 patch 這兩個 module(其他模組沒在 hot path);不動 prod 程式碼。

### 4.4 輸出(console)

```
=== probe_replay_ticks 報告 (2026-05-13, USER_LABEL=loger) ===
Watchlist: 5 symbols (2330, 6505, 0050, 3008, 2454)
Active signals: 3 enabled
  - "60s 漲 1%"      (price_change_pct gt 1.0, window=60, cooldown=300)
  - "rsi<30 + 收跌"  (rsi_14 lt 30 AND close lt cdp, cooldown=600)
  - "close > cdp_ah" (close gt cdp_ah, cooldown=1800)

Candles fetched: 1342 bars total (avg 268/symbol, 0 failed)

觸發明細(按時間排序):
  時間        | symbol | 規則                    | 價          | window/cache 摘要
  -----------|--------|------------------------|-------------|------------------------
  09:23:00   | 2330   | 60s 漲 1%              |   635.0     | start=628.5 (+1.03%)
  10:45:00   | 6505   | rsi<30 + 收跌          |    81.2     | rsi=28.5 cdp=82.0
  11:12:00   | 2330   | close > cdp_ah         |   642.5     | cdp_ah=640.0

每規則統計:
  60s 漲 1%               × 8 次 (3 symbols)
  rsi<30 + 收跌           × 2 次 (1 symbol)
  close > cdp_ah          × 0 次 ← 今日完全沒觸發

完成。耗時 12.3s。
```

### 4.5 執行 / 不做

```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\probe_replay_ticks.py
```

**不做**:
- 不啟動 `ws_pool` / `supabase_writer` / engine background loops(`_consume_loop` / `_heartbeat_loop` / `_monitor_loop`)— 只用 `_evaluate()` 同步呼叫
- 不寫 `signals_log`(monkey-patch)
- 不寫 `signals` ws broadcast
- 不接受 CLI 參數(`USER_LABEL` 從 `.env` 讀,跟其他 probe 一致)

---

## 5. 為什麼這樣設計

### 5.1 為什麼 #1 不直接寫成 pytest test
專案目前完全沒有 pytest 框架(只有 `scripts/probe_*.py` 慣例)。為加 8 個 case 引入新測試框架 = yagni。沿用現有 pattern,user 之後想接 pytest 再說。

### 5.2 為什麼 #2 用 candles 不用 trades
- Fubon `intraday.candles` 一次拉一整天(~270 根 1 分 K)
- Fubon `intraday.trades` 預設 limit=50,一天可能上萬筆,要分頁 + rate limit 管理
- User 設的 window 規則目前最小 60s(`WindowSeconds` enum),candles 完全夠精準
- 若未來要 volume_burst 秒級偵測再加 `--use-trades` flag,目前 yagni

### 5.3 為什麼 #2 從 DB 讀規則 / watchlist,不允許 inline
- User 想驗的是「自己現在設的規則」,規則已經在 DB
- Inline JSON 等於再做一份「規則編輯器」,而 active_signals UI 已存在
- 簡化 input surface(讀 `.env` 的 `USER_LABEL` 自動定位)

### 5.4 為什麼 #2 不寫 signals_log
- Replay 是「假設性問題」(`昨/今天若有這條規則,會觸發嗎`)
- 寫進 signals_log 會污染真實觸發紀錄 + 跟 today_counts 衝突
- monkey-patch `engine._fanout` → record to local list,純報告

### 5.5 為什麼用 monkey-patch 而不抽介面
- `engine._fanout` 是 1 個 method,只在 `_evaluate` 內呼叫
- 抽 callable 介面要改 engine 簽名 + 影響 prod path
- monkey-patch 限定在 probe scope 內,prod 不動,yagni

---

## 6. 風險 / 踩雷預測

| 風險 | 機率 | 緩解 |
|---|---|---|
| Fubon `intraday.candles` 盤後拉不到 | 低 | `probe_candles.py` (`2026-05-12`) 已驗 OK |
| `_refill_field_cache()` 在 user 沒 watchlist 時行為未知 | 低 | 已先檢查 watchlist 空 → exit |
| cooldown / window cutoff 用 wall-clock `time.time()`(高破壞性) | 高 | 已設計 FakeClock module-monkey-patch(§4.3.1)。所有 cooldown / window cutoff 改用模擬 tick time |
| candles `date` 欄位 timezone | 中 | 看 `probe_candles.py` 輸出確認;若是 ISO8601 → `datetime.fromisoformat()`,記得 `.timestamp()` 拿 epoch 前先 `tz_localize` 到 Asia/Taipei |
| 多個 active_signals 跟 watchlist 多 symbol 時觸發爆量 → console 印太多 | 低 | 預期單天 < 100 觸發。超過時加 `--limit N` 或截斷顯示 |
| FakeClock 漏 restore(exception path) | 中 | try/finally 包住,任何 path 都 restore |

## 7. 驗收

- `probe_signal_engine.py` 跑完印 `All signal_engine smoke tests passed ✓` 且 exit 0
- `probe_replay_ticks.py` 盤後在 user 有 watchlist + active_signals 的狀態下跑完:
  - 不噴 exception
  - 印報告(無論是否有觸發)
  - 不在 `signals_log` 新增 row(SQL 確認)

## 8. 後續(明確 future)

- 跨日 replay → 需要先建 `tick_archive` 表 + 每天 13:35 自動存 intraday.trades
- pytest 框架建置 → 把 mock probe 們搬進 `backend/tests/`
- replay UI 接進前端 ActiveSignalEditor:「按此規則 dry-run 今日 candles」
- replay 支援 trades 逐筆(`--use-trades` flag)

關連:[[trading-king-phase-3]] / [[trading-king-phase-3-1-watchlist-signals-integration]] / [[reference_dev_commands]]
