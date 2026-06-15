# 雙峰量價背離造山訊號(策略 5)設計 — 2026-06-14(candle 版)

策略候選清單第 5 條。原構想「午後轉弱」→ brainstorm 後定為「雙峰量價背離造山」,範圍鎖**純雙峰背離**。

> **重要修正(2026-06-14)**:本 spec 初版誤判「tick 引擎無 K 棒、要回落 R% 事後確認峰、量驗不出」。
> 經 6-agent codebase recon 核實:**引擎早有 1 分 K candle aggregation**(`MinuteCandle` + `_update_candle`,
> signal_engine.py:44-51, 561-604),策略1 `cdp_breakout_confirm` 就是吃結算 candle。本版改用 candle 模式 ——
> 更簡單、量用 `candle.volume`(回測還原原始 1 分 K 量)**驗得出**、跟現有策略一致。

## 背景動機

6/12 雷科(6207)、東捷(8064)、南茂(8150) 同一劇本:早盤衝高創當日前高 + 出量(主峰)→ 回落 →
二次反彈但量縮、攻不過前高(次峰)→ 垮,收當日最低。(雷科 6/12:H134/L124/C124,9:56 主峰、
10:40 次峰量縮不過高、收最低 124,−7.5%。)現有引擎抓不到這種「強勢股日內做頭轉空」:碰CDP/突爆拉
只看上漲;突爆殺(策略3)只抓 5 分鐘急殺、不分位階、抓不到緩跌型結構轉空。

## 結論先講

- 形態 = 雙峰量價背離:**主峰 high 創當日新高 → close 回落確認 → 次峰 high 不過前高 + volume 縮 →
  次峰滾頭 → 觸發「做頭轉弱」**。
- **吃引擎結算的 1 分 K candle**,照策略1 `cdp_breakout_confirm` 模式(signal_engine.py:320-323),
  **不走** tick-based `_eval_strategy`。
- 量用 `candle.volume` / `_candle_volume_ratio`(signal_engine.py:550),回測 4 tick 聚回原始 K 量,**量驗得出**。
- 範圍鎖純雙峰;單峰急垮 / 開高走低 = 未來的其他做頭積木。
- **核心是 candle 序列狀態機,照 codebase 慣例用 engine method + `_peak_state` dict(策略1/2 皆如此,不抽獨立 class)**;
  邏輯自包含,未來組合框架可復用。
- 落地三檔:`condition.py`(加 model + schema_version 6→7)、`signal_engine.py`(candle dispatch 分支 +
  `_eval_peak_divergence` + 狀態 + reset + 三處 stype)、`replay_engine.py`(`--preset peak`)。

## 策略定義 — candle 雙峰狀態機

吃 settled candle(每根 1 分 K 結算時餵一次)。per `(active.id, symbol)` 狀態 `_peak_state`:
`{phase, day_high, peak1_high, peak1_vol, trough_low, peak2_high, peak2_vol, peak1_minute}`,
phase ∈ `{watch, pullback, confirmed}`。

每根 settled candle(minute, open, high, low, close, volume)依序:

1. `day_high = max(day_high, candle.high)`
2. **watch(找主峰)**:
   - `candle.high` 觸及 `day_high`(創當日新高)→ 更新主峰候選 `peak1_high=candle.high`、`peak1_vol=candle.volume`、`peak1_minute`
     (可選 `min_main_peak_volume_ratio`:主峰那根 `_candle_volume_ratio` ≥ 門檻才認,否則不鎖主峰)
   - `candle.close < peak1_high × (1 − pullback_pct/100)`(從主峰收盤回落確認)→ 主峰確立,phase=pullback,`trough_low=candle.low`
3. **pullback(找次峰)**:
   - `trough_low = min(trough_low, candle.low)`
   - `candle.high` 創「主峰後反彈新高」→ 更新次峰候選 `peak2_high`、`peak2_vol`
   - 若 `peak2_high ≥ peak1_high × (1 + not_exceed_tolerance_pct/100)`(過前高)→ **不是做頭**:
     把次峰當新主峰(`peak1←peak2`)、清次峰、續找(山越造越高,尚未轉弱)
   - 若 `candle.minute − peak1_minute > max_gap_minutes`(主峰→次峰太遠)→ 放棄這組,回 watch
   - **次峰滾頭確認**:`candle.close < peak2_high × (1 − pullback_pct/100)` → 檢查背離:
     - 不過前高:`peak2_high < peak1_high × (1+tol)`(已保證)
     - 量縮:`peak2_vol < peak1_vol × volume_shrink_ratio`
     皆成立 → **觸發「做頭轉弱」**,phase=confirmed(當日 latch)
4. **confirmed**:當日不再觸發(latch);防重複靠此 phase 鎖 + cooldown。

**觸發延遲**:detector 只看 settled(已結算)candle、看不到 in-progress 當前根 → 訊號最快在峰後
**下一分鐘第一筆成交**觸發,落後盤面最多近 1 分鐘(signal_engine.py:301,321,同策略1)。

### 實作注意(recon 核實的坑,附 file:line)

- **三處 stype 比對都要加 peak 分支**:dispatch(signal_engine.py:320)、cooldown touch_level(343-348)、
  touch_count 排除(357)。散在 `_evaluate` 內,漏一處就行為錯(漏 357 → touch_index 混計)。
- **day_high 自存,不能放 `_field_cache`**:`_refill_field_cache` 在規則/監聽 CRUD 時被呼叫(137)會洗掉;
  存進 `_peak_state`,只在 `_reset_daily_strategy_state`(606-624)清。
- **heartbeat 去重免費**:candle 經 `_update_candle` 的 `is_new_tick` 去重(298);peak 吃 settled、不自累
  per-tick 量,天然不受 heartbeat 每秒重餵影響。
- **收盤前最後一兩根吃不到 confirm**:13:30 後 heartbeat 結算的 settled 被 in_session gate(303-304 return)
  擋掉、不評估 → 13:29 那根的滾頭確認拿不到(同策略1 已知限制)。
- **稀疏成交空窗根**:無成交分鐘 heartbeat 會結算「volume 停在上一根、close 停上一根」的空窗根(594-597);
  判回落/次峰要容忍低量平盤根,不誤判成新峰。主峰創新高一律比 `settled.high`(不是 close)。
- **strategy 類不套 re-arm**(337-339),cooldown 自理。

## 參數(`PeakDivergenceStrategy`,初值;**實盤迭代為主**)

| 欄位 | 型別 / 預設 | 作用 |
|---|---|---|
| `type` | `Literal["peak_divergence"]` | discriminator |
| `pullback_pct` | float, 1.0, gt0 le20 | 峰回落確認幅度(close 從峰 high 回落 ≥ 此% = 峰封頂) |
| `not_exceed_tolerance_pct` | float, 0.0, ge0 le5 | 次峰 high 不過主峰 high 的容差 |
| `volume_shrink_ratio` | float, 0.8, gt0 le1.0 | 次峰量 < 主峰量 × 此值 |
| `max_gap_minutes` | int, 120, ge1 le240 | 主峰→次峰最大間隔(用 candle.minute 差) |
| `min_main_peak_volume_ratio` | float\|None, None, ge0.5 le20 | (可選)主峰那根量門檻,對齊 `BreakoutConfirm.min_volume_ratio` |

engine 取用:必填 `strat['pullback_pct']` hard index、可選 `strat.get('min_main_peak_volume_ratio')`
(對齊 signal_engine.py:511-513 慣例;欄位名必須與 engine 取用 key 對齊,否則 KeyError)。

## 落地(照策略1 `cdp_breakout_confirm`,無新架構)

### condition.py
- 新增 `class PeakDivergenceStrategy(BaseModel)`,欄位如上(全 numeric 用 `Field(default=, ge=, le=)`)。
- 加進 `StrategyConfig` union(condition.py:209-212):`... | BreakoutConfirmStrategy | PeakDivergenceStrategy`。
- `ActiveFilter.schema_version` 6 → 7(line 222),註解「6→7,加 peak_divergence strategy」。

### signal_engine.py
- `__init__`(92-94 旁):`self._peak_state: dict[tuple[str, str], dict] = {}`。
- `_evaluate` 加 candle dispatch 分支(仿 320-323):
  ```python
  elif stype == "peak_divergence":
      if settled is None:
          continue
      cdp_touch = self._eval_peak_divergence(strat, active, symbol, settled, now)
      ma_touch = None
      ok = cdp_touch is not None
  ```
- cooldown 段(343-348):peak 無 price level → `touch_level = ""`(per 股票);觸發後 phase=confirmed latch
  已防重複,cooldown 設長(1800)當保險。
- touch_count 段(357):`if stype not in ("cdp_breakout_confirm", "peak_divergence"):`。
- `_eval_peak_divergence(strat, active, symbol, candle, now) → dict | None`:實作上述狀態機;**量縮直接比
  存下的 raw `peak1_vol` 與 `candle.volume`(`peak2_vol < peak1_vol × ratio`),不經 `_candle_volume_ratio`
  —— 避開 day_volume 重啟偏誤**;只有可選的 `min_main_peak_volume_ratio`(主峰絕對量門檻)才用 `_candle_volume_ratio`。
- `_reset_daily_strategy_state`(606-624):加 `self._peak_state.clear()`。

### 不動
`_fanout` / `_update_candle` / `ring_buffer` / `cdp` / `discord_notifier` / `signal_writer` 全沿用。

## 觸發 metadata 與 bot 渲染(跨 repo)

觸發回 `cdp_touch` dict,經 `_fanout`(signal_engine.py:925-968)自動三路扇出(WS + signals_log + discord),
對 strategy type 無感知,**不改 `_fanout`**:
```python
{"level": "peak", "direction": "from_above", "role": "distribution",
 "main_peak_price": peak1_high, "second_peak_price": peak2_high,
 "volume_shrink": round(peak2_vol / peak1_vol, 2)}
```
- `trigger_price` 用當前 `tick.price`(fanout 慣例 935-936),非 candle.close —— spec 明記此語意。
- 自訂欄位(main_peak_price 等)為原生型別(float),JSONL 原樣存、WS 原樣送;bot 圖卡 `parseTouch`
  只取 level/direction/role/touch_index,其餘丟棄(不影響 WS 前端與歷史)。
- **跨 repo(bot/src/signal.ts)**:role `distribution` 不在 bot `ROLE_ZH`(只 support/resistance/touch)
  會 fallback 顯英文;level `peak` 非 CDP 線名會 `.toUpperCase()` 顯 PEAK,文案「碰 CDP PEAK」會怪。
  → 第一版:bot `ROLE_ZH` 增 `distribution: "做頭轉弱"` + `levelLabel`/`formatBanner` 為 peak 加分支
  (或暫借 role="resistance" 顯「壓力」)。列為本 PR 的跨 repo 改動;WS 前端 + signals_log 不受影響,
  bot 文案盤中實機驗時定。

## 回測(replay `--preset peak`)

照 breakout preset 三處改(replay_engine.py):
1. `peak_rule(day, **params)` → `ActiveSignalOut(filter_json=ActiveFilter(strategy=PeakDivergenceStrategy(...)))`、
   scope watchlist、cooldown 1800、`notify_discord=False`(回測不灌真 Discord)。
2. `run_peak(days, day_syms, daily, minute, rearm)`:照 `run_breakout`(311-351)掃參數矩陣
   (模組級常數 `PEAK_*` + `assert ... in ...` 守門)、印 days[-1] per-symbol。
3. `main()`:`--preset` choices 加 `"peak"` + `if args.preset == "peak": await run_peak(...); return`。

**目標案例已確認在池**:2026-06-12 signals_log 股票池 12 檔(2327,2481,3042,3105,3357,6173,6207,6239,6415,
6531,8064,8150),含 6207/8064/8150(各 11/10/10 筆觸發)—— peak 回測驗得到三檔。

**回測限制(誠實 flag)**:
- `candle.volume` 回測 = 4 tick 量總和 ≈ 原始 K volume → **量縮可信**(推翻初版「量全盲」)。
- 但 1 分 K 轉 4 tick 的粗粒度,雙峰的「峰內回落幅度、峰間微結構」可能失真 → `run_peak` 輸出比照其他 preset
  看**相對門檻差距 + 目標案例命中**,不看絕對觸發數。
- 跨日造山測不到(每日全新 engine,單日內才成立);跨日是 production 才有的場景。

## 測試(TDD,照 strategy 慣例)

- `backend/tests/test_signal_engine_peak_divergence.py`:
  - helper:`_active()`(包 PeakDivergenceStrategy)、`_engine()`、`_candle(open, high, low, close, volume, minute)`
    —— **擴成六參**(雙峰要 high>close 表達峰形,既有 _candle 只平單值)。
  - 單元:逐根 `engine._eval_peak_divergence(strat, active, symbol, candle, now)`,斷言主峰前回 None、
    次峰滾頭那根回 dict;**負向測**(次峰過前高不算 / 主峰後續創高重設主峰 / 量沒縮不確認);
    **參數捕捉測**(鎖 `volume_shrink_ratio`、`pullback_pct` 被正確讀,仿 test_surge_query_passes_correct_params)。
  - 整合:`@pytest.mark.asyncio`,patch `services.signal_engine.time.time` / `get_broadcaster` /
    `get_signal_writer`,**跨分鐘餵 ≥2 tick**(candle settlement gate)直到觸發,斷言
    `fired[0]["data"]["cdp_touch"]["role"] == "distribution"`。
  - daily-reset 測:餵到 `_peak_state` 非空 → `_reset_daily_strategy_state()` → 斷言清空。
- `test_condition_strategy.py`:加 PeakDivergenceStrategy defaults/discriminator 測;**同步把
  `test_schema_version_bumped_to_6` 改成 7**(condition.py schema_version bump 後既有斷言會紅)。
- 命令(在 `backend/` 下):`.venv\Scripts\python -m pytest tests/test_signal_engine_peak_divergence.py -v`。
  PowerShell 串接用分號 + `if($?)`,不用 `&&`。`asyncio_mode='auto'` 已開但既有測仍顯式標,peak 也顯式標。

## 不做的事(YAGNI)

- 內外盤 / order flow → 策略4。
- 進場 / 加碼 / 停利 / 停損 部位邏輯 → 後續衍生提示(引擎無部位概念 + CLAUDE.md 約束不下單)。
- 單峰急垮 / 開高走低 → 未來的其他做頭積木。
- 通用組合框架 → 積木夠多再做(strategy 目前獨佔,要改架構)。
- `confirm_bars` 連續站穩語意 → 雙峰滾頭用 `pullback_pct` 單根回落確認即可,不引入。

## 未來工作

- 做頭家族其他積木:一峰急垮(主峰後不彈次頭直接崩)、開高走低(開盤即當日高、整天陰跌)。
- 策略4 外盤轉弱(order flow,不可回測、實盤驗)。
- 可組合積木框架(造山 + 外盤等疊加成高置信)。

## 已知限制(多數與策略1共享)

- 收盤前最後一兩根吃不到滾頭確認(in_session gate,303-304)。
- 盤中重啟:`_peak_state` 從重啟起算 → 主峰可能漏(in-memory,v1 不持久化)。量縮用 raw volume 直接比、**不受重啟影響**;
  僅可選的 `min_main_peak_volume_ratio`(經 day_volume)在重啟當日會偏誤。
- 4 tick 近似:回測看相對 + 目標案例命中為準。
- 早盤主峰(09:00–09:01):`_candle_volume_ratio` elapsed<1 回 0 → `min_main_peak_volume_ratio` 門檻會擋(早盤盲區)。

## 開放問題

1. **命名 / bot 渲染**:level=`peak`、role=`distribution` 影響 bot 圖卡;要 bot 端增譯(跨 repo)還是暫借 `resistance`?
2. **滾頭確認**:單根 close 回落 `pullback_pct`,還是要連續 N 根?(先單根)
3. **不過前高基準**:次峰 high vs 主峰 high(spec 取 high 找峰、close 確認回落 —— 已定,列此供 review 確認)。
4. **`max_gap_minutes`** 上限值由回測 / 實盤定。
5. **是否抽獨立 detector class**:目前照 codebase 慣例用 engine method + state dict;未來做組合框架時再評估抽離。

## Code Review 後續追蹤(2026-06-14)

實作完成後跑兩輪 review(/code-review high + requesting-code-review subagent),Assessment = Ready to merge,無 Critical/Important。下列 finding 經 receiving-code-review 技術評估,判定皆為設計層級 / spec 未規範灰區 / 既有 replay 限制(**非實作偏離 spec 的 bug**),首版不改 code,列此供盤中實機驗收後迭代:

1. **roll-up 新主峰繞過 `min_main_peak_volume_ratio`**(次峰過前高轉新主峰分支):roll-up 時不重驗主峰量門檻;且量門檻擋掉的高點仍墊高 `day_high`,後續較低但帶量的真主峰可能因 `is_new_high` 失敗而鎖不上。預設 `min_vr=None` 不受影響。實機若啟用量門檻,驗收 roll-up 與 day_high desync 是否需收緊。
2. **同根 high+close 判封頂**:開高走低長黑根(high 創當日新高、close 大幅回落)會在創高那一根自身就進 pullback(spec「該根 close 回落確認」未要求下一根)。單邊上漲日可能誤啟動做頭判斷;實機驗是否要求「下一根才確認回落」。
3. **`peak1_vol==0` 靜默不觸發**:主峰那根 volume=0(稀疏成交分鐘 / replay 原始 K 量 0)時量縮條件恆 False,即使完美做頭也不觸發、無 log。語意上「無量主峰不算」可接受,但違反 fail-loud,實機若遇可加 guard/log。
4. **replay 無 end-of-day flush**(`replay_day`):最後一根 candle 不結算,`run_peak`(及既有 breakout preset)系統性漏尾盤滾頭訊號 → 回測數字偏低。既有 replay 限制、跨 preset,改善宜獨立處理。
5. **`trough_low` 預留未用**(`_peak_state` 欄位):狀態定義列 `trough_low`、pullback 階段以 `min()` 維護,但無任何觸發條件讀取(dead state)。未來「谷底深度過濾」可用,否則移除。
6. **confirmed 當日單發 latch**:符合上文「當日 latch」明定行為(一日只觸發一次);「早盤做頭→反彈→午後二次做頭」會漏第二訊號。屬產品取捨,上線觀察是否需放寬。
