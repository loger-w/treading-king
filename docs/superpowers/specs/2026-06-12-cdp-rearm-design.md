# CDP 碰線訊號降噪:re-arm(離場再武裝)設計

日期:2026-06-12
狀態:設計定稿,待實作

## 問題

「碰 CDP」訊號(cdp_proximity)在股價貼線磨盤時反覆觸發:2026-06-12 全日 71 筆中,
8150 在 NH 觸發 7 次、3357 在 NH 8 次、3042 在 CDP 8 次。cooldown(600s)只能拉開
間隔,擋不住「黏在線上每 10 分鐘推一次」。另外 cooldown 粒度是 (策略, 股票),
碰完 NH 後 10 分鐘內連碰 CDP 的有效訊號也被同一個 cooldown 吞掉。

## 決策(經 2026-06-08 ~ 06-12 五日 1 分 K 重播驗證)

1. **re-arm 機制**:觸碰訊號觸發後,該 `(策略, 股票, 價位)` 進入抑制狀態;
   之後價格須離線夠遠(`|price − level| ≥ rearm_ticks × tick_size(level)`)
   才重新武裝,回頭再碰才算新訊號。
2. **距離單位用固定 ticks,預設 5**,不用百分比。重播顯示台股 tick 級距
   (100 元檔 tick 較粗)天然符合「低價股毛刺大、需要更寬緩衝」:
   - pct=0.5% 對 ~100 元股票僅 1 tick,形同虛設(8150 當日 9 筆 → 14 筆,反而更吵)
   - ticks=5 把磨盤股砍 50~70%(8064 11→3、6207 11→5、8150 9→4),
     趨勢股一筆不少(6239 9→10、2327 6→6);全體 375→320
   - ticks=8 全體降至 261,但開始有錯殺真回踩的風險;5 為平衡點
3. **cooldown 保留 600s,粒度改 per (策略, 股票, 價位)**。重播顯示拿掉 cooldown
   後價格在 re-arm 邊界來回穿越會爆炸(ticks=5 無 cooldown:528 筆 vs 現況 375)。
   re-arm 管「同一價位黏線」,cooldown 管「頻率上限」,缺一不可。
4. **direction=horizontal 不推**(前一筆與本筆都貼在線上、判不出方向)。
   有 re-arm 後,真正的第一次觸碰必有方向;horizontal 只出現在已黏線情境,
   資訊價值最低(06-12 約 15/85 筆)。

## 實作範圍

### Schema(`filter_json.cdp_proximity` / `ma_proximity`)

- 新增選用欄位 `rearm_ticks: int ≥ 0`;**缺欄位時引擎預設 5**(既有設定檔
  不需遷移即生效),設 0 = 關閉 re-arm(回到僅 cooldown 的行為)。
- 驗證:`rearm_ticks = 0` 或 `rearm_ticks > tolerance_ticks`(否則永遠無法 re-arm)。

### SignalEngine(`backend/services/signal_engine.py`)

- 新增抑制狀態:`set[(active_id, symbol, level)]`。level 為 CDP 線名
  (`ah/nh/cdp/nl/al`)或 MA 線名(`sma_5/sma_20`)。
- `_evaluate` 內、proximity 評估前,先對該 (active, symbol) 的抑制項做 re-arm
  檢查:`|tick.price − level值| ≥ rearm_ticks × tick_size(level值)` → 解除抑制。
  level 值自 `_field_cache` 取(CDP / 當日 SMA 盤中皆固定,無漂移問題)。
- proximity 評估時跳過仍受抑制的 level(其他 level 照常可命中)。
- 觸發後將命中的 level 標記抑制。
- cooldown key 由 `(active.id, symbol)` 改為 `(active.id, symbol, level)`;
  level 取 cdp_touch 的 level,無則取 ma_touch 的,皆無(純 window 條件,
  如突爆拉)用空字串 — 非觸碰類行為不變。
- `_eval_with_touch_meta` 中 direction 判為 `horizontal` 的 proximity 命中
  視為未命中(CDP 與 MA 一致)。
- 跨日清空抑制狀態(掛進 `_reset_daily_strategy_state`)。

### 不在本次範圍

- `limit_up_open_touch` / `breakout_retest`:各自已有狀態機(鎖死 latch / 觸發即
  disarm),不套 re-arm。
- 前端規則編輯 UI 的 `rearm_ticks` 欄位:先吃引擎預設 5,UI 後續再加。

## 測試(`backend/tests/`)

- 狀態機:觸發 → 抑制(近線 tick 不觸發)→ 離線 ≥ 5 ticks → 再武裝 → 回頭碰 → 再觸發。
- 離線不足(< rearm 距離)時持續抑制。
- per 價位 cooldown:碰 NH 觸發後 10 分鐘內碰 CDP 仍可觸發;同價位 10 分鐘內不重複。
- horizontal 被擋;from_above / from_below 照常。
- `rearm_ticks=0`:無抑制行為(僅 cooldown 粒度與 horizontal 規則生效)。
- 跨日 reset 清抑制狀態。
- 純 window 條件訊號(無 touch)cooldown 行為不變。

## 已知近似與風險

- 重播用 1 分 K 近似 tick 行為(K 棒掃過線即視為 touch、方向用前一根收盤判定),
  絕對數字有偏差,但變體間相對比較有效;基準校準:重播現況 375 vs 實際 371。
- 同一根 K 內「觸發後又拉開」在重播中棒尾即再武裝,實際 tick 行為更嚴格(逐筆判)。
- 預設 5 ticks 對極低價股(< 10 元,tick 0.01)僅 0.05 元緩衝 — 目前監聽池無此類
  標的;若未來納入,以 per 策略 `rearm_ticks` 調整,不在引擎內做價位分段。

## 實作後引擎級回測結果(2026-06-12,scripts/replay_engine.py)

近 5 日(06-08 ~ 06-12)1 分 K 轉 tick 餵實作後的 SignalEngine,
碰 CDP 規則(5 線、tolerance 0、cooldown 600s per 價位、horizontal 不推):

| day   | rearm=0 | rearm=5 |
|-------|---------|---------|
| 06-08 | 22      | 12      |
| 06-09 | 98      | 68      |
| 06-10 | 81      | 59      |
| 06-11 | 67      | 59      |
| 06-12 | 77      | 48      |
| 總計  | **345** | **246(−29%)** |

- rearm=0 基準(345)與設計階段獨立重播(375)/ 實際 signals_log(371)同量級 → 模型可信
- 06-12 per-symbol:磨盤股 8150 14→4、8064 10→3、6207 9→4(−65~70%);
  趨勢/低噪股 6239 7→7、2327 4→4、6173 1→1(零誤殺)
- 與設計預測一致,驗收通過

## Code review 後的修正決策(2026-06-13 follow-up)

PR #29 併入後 code review 發現四個問題,修正如下:

1. **rearm_ticks 預設改 None(未顯式設定)**。原「Field 預設 5 + 必須 > tolerance」
   使 tolerance 5~10 的規則(前端允許、舊設定檔可能存在)驗證必炸;且
   refresh_active_signals 走 pydantic 物化 filter_json,炸掉的規則會被**靜默跳過**。
   改為:顯式值才驗證;None 由引擎解析為 `max(REARM_TICKS_DEFAULT, tolerance+1)`,
   任何 tolerance 下保證可 re-arm 且舊資料永遠可載入。
2. **抑制標記移到規則整體成立後**(_evaluate 內、cooldown 檢查前)。原本在
   _eval_with_touch_meta 內標記,AND 組合規則「碰線但其他條件沒過」也會消耗
   armed,吃掉之後的第一筆合法訊號。cooldown 擋下仍標記(維持原意:黏線不等
   cooldown 到期重推)。
3. **strategy 類 cooldown 還原 per (策略, 股票)**。per 價位粒度原本連帶套到
   limit_up_open_touch / breakout_retest,使漲停打開回落連穿多線時一個冷卻窗
   可推多發 — 未經回測驗證的行為變更,先還原舊行為;要改另行決策。
4. **replay_engine 設 notify_discord=False**。_fanout 會對 notify_discord=True
   POST SIGNALS_BOT_PUSH_URL,重播數百筆觸發不可灌進真 Discord。
