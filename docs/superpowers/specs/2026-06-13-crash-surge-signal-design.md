# 突爆殺訊號(策略 3)設計 — 2026-06-13

策略候選清單(`docs/notes/2026-06-12-strategy-candidates.md`)第 3 條:突爆拉的鏡像。
引擎只看得到上漲爆拉,6/12 國巨(2327)11:42 三分鐘殺 26 元(881→855)系統無感。

## 結論先講

- **規則本體零程式碼**:引擎 `price_change_pct` 帶正負號、`lt` 運算子已存在、
  前端 `ActiveSignalEditor` 運算子下拉含 `lt` 且數值欄可輸入負數 — 規則直接從 UI 建。
- **唯一要寫的程式碼是回測擴充**:`replay_engine.py` 目前不餵 ring_buffer,
  window 條件類規則在回放中永遠不觸發。本案擴充之,先回測再上線。

## 規則參數(鏡像突爆拉)

| 項目 | 值 |
|---|---|
| window_conditions | `{type: price_change_pct, operator: lt, value: -2.0, window_seconds: 300}` |
| value 基線 | −2.0,最終值由回測門檻掃描決定 |
| scope | watchlist |
| cooldown_seconds | 1800(純 window 條件 cooldown level 為空字串 = per 股票,行為與突爆拉一致) |
| notify_discord | true |

## 回測擴充設計(replay_engine.py)

選定做法:直接擴充現有腳本(另開腳本會複製 fetch/合成邏輯;抽共用 lib 對兩個用例過度設計)。

1. **餵 ring_buffer**:合成 tick 在 `engine._evaluate` 前 `append` 進 ring buffer。
   無條件餵 — 碰線規則不讀 ring_buffer,不受影響。
2. **時鐘 patch**:`RingBuffer.window()` 內部用 `time.time()` 算 cutoff
   (`ring_buffer.py:75`),必須與 `services.signal_engine.time.time` patch 成
   同一個假時鐘,否則回放歷史 tick 時視窗永遠為空。
3. **跨日隔離**:每次 `replay_day` 用全新 `RingBuffer` 實例(兩個模組的
   `get_ring_buffer` 都要指到它),避免單例跨日污染。
4. **規則注入**:加 `--preset` 參數 — `touch`(現狀,預設)/ `crash`(突爆殺)。
   crash preset 單次執行內完成門檻掃描:每個門檻(−1.5 / −2.0 / −2.5 / −3.0)
   各跑一輪 `replay_day`,外加突爆拉(`gt +2.0`)同池一輪當對照 —
   user 已實際體驗過突爆拉的吵度,是最直觀的雜訊量標尺。
   輸出比照現有格式:per-day × 各門檻訊號量表 + 最後一日 per-symbol 明細。
5. **量分攤不做**:突爆殺只看價格;`volume_burst` 類的量分攤留給策略 2(量能過濾碰線)。

### 已知近似(沿用既有 replay 的限制)

- 1 分 K 每根轉 4 筆 tick(15 秒間距),300 秒窗約 20 筆 tick。
  `price_change_pct` 比視窗第一筆 vs 現價,此密度足夠。
- 絕對數字僅供參考,重點是「各門檻訊號量」與「突爆拉對照」的相對關係。
- 股票池 = 該日 signals_log 出現過的股票(當天確實被監聽且碰過線的活躍股),
  用來估突爆殺雜訊量是合理樣本。

### 驗算

國巨 6/12 案例:881→855 / 3 分鐘 = −2.95%,300s 窗 `lt -2.0` 會觸發。

## 流程與驗收

1. 擴充 replay_engine.py → 跑 5 日回測(先停 dev server — 腳本會登入富邦)
2. 回測數字出來 → user 定最終門檻
3. UI 建「突爆殺」規則上線(零程式碼)
4. replay 擴充以 PR 進 main
5. 上線後驗證:盤中遇國巨型急殺能推 Discord 圖卡

## 不做的事(YAGNI)

- 不動 signal_engine.py
- 不做量分攤 / volume_burst 回測(策略 2 再做)
- 不抽回測共用 lib(策略 5/7 也要回測時再評估)
