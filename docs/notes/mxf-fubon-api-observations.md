# MXF Fubon API 實測觀察記錄

實測對象:富邦 Neo SDK 2.2.8 期貨 REST + WS。
實測目的:驗證 spec 風險點假設、為後續調整提供事實依據。

> 相關 spec:[`docs/superpowers/specs/2026-05-24-mxf-intraday-chart-design.md`](../superpowers/specs/2026-05-24-mxf-intraday-chart-design.md) 第 11 段。
> 相關 plan:[`docs/superpowers/plans/2026-05-24-mxf-intraday-chart.md`](../superpowers/plans/2026-05-24-mxf-intraday-chart.md) Task 16。

## 怎麼跑實測

1. 啟動完整後端 + 前端:`.\start.ps1`
2. 確認 富邦 SDK 登入成功(看 backend log 「fubon=READY」)
3. 開瀏覽器 → http://localhost:5173 → 點「mxf_backtest」頁面
4. 對照下面 5 個觀察項分別記錄
5. 若觀察結果跟 spec 假設不符,開新 task 修正並把該 commit 連結到本文件

---

## 觀察 1: `intraday.candles(session='afterhours')` 拿到的「夜盤」是哪一場?

**假設**:文件未明說「最近一場」是「已收的」還是「進行中的」。

**實測方法**:
- **日盤中(11:00 左右)**:打 `GET /api/mxf/candles?tf=5`,看回應的 candles 中:
  - 夜盤段第一根 date = ?
  - 夜盤段最後一根 date = ?
  - 日盤段第一根 date = ?
- **夜盤中(22:00 左右)**:同樣打,記錄三個值
- **休市中(14:30 / 06:00)**:同樣打

**預期判斷**:
- 如果「夜盤段最後一根」總是 < 「日盤段第一根」 → 後端拿到的是「已收」夜盤,符合 spec 假設
- 如果在夜盤中時拿到的夜盤段持續更新到當下 → 是「進行中」夜盤
- 如果跨日盤夜盤切換時有 1-2 根 candle 缺失 → 有時間 race,需後端補強

**結論**:_[實測後填寫]_

---

## 觀察 2: WS push 是「每根 K 完成才推一次」vs「每秒更新累積中那根」?

**假設**:文件未明說。

**實測方法**:
- 在交易時段啟動後端,訂閱 candles channel(後端會自動訂 MXF 近月 + 當前 session)
- 在 backend log 中加暫時的 debug logging(若沒有的話):
  ```
  logger.info("ws push: %s", raw)
  ```
- 觀察相同 date 的 candle 被推幾次、close 是否會變

**預期判斷**:
- 推一次後不再推 = 「K 完成才推」 → 前端 last-K-update 邏輯正確
- 同 date 持續推、close 慢慢累積 = 「每秒更新」 → 前端要繼續 update last K
- 完全沒推 = WS 沒連上或 subscribe payload 格式錯

**結論**:_[實測後填寫]_

---

## 觀察 3: Session 邊界精確時間

**假設**:13:45:00 收盤、15:00:00 開盤,但富邦可能在 13:45:30 才停推、15:00:00 後幾秒才開推。

**實測方法**:
- 在 13:44:50 ~ 13:46:00 持續觀察 WS push 頻率(看 backend log)
- 在 14:59:50 ~ 15:00:30 同樣觀察
- 紀錄最後一次推送的精確時間 / 第一次推送的精確時間

**預期判斷**:
- 若推送在 13:45:00 立即停止 → 後端 session 邏輯 (`determine_current_session`) 邊界精準
- 若有延遲 → 可能要加 grace period(13:45:00-13:45:30 算 day,不算 closed)

**結論**:_[實測後填寫]_

---

## 觀察 4: 「近月」products 結算當週的行為

**假設**:結算當週的合約是不是會立刻從 `futopt.intraday.tickers` 移除?

**實測方法**:
- 找一個結算日(每月第三週三)
- 結算日前一天(週二)13:45 + 結算當日(週三)13:45 後 各打一次 `/api/mxf/symbol/active`
- 比對 cache:當前近月有沒有變?

**預期判斷**:
- 結算當天 13:45 後立刻換月 → spec 假設正確
- 結算後仍回舊 symbol → cache 邏輯要加「expiry == today 視為已過期」判斷(目前 `expiry > today` 已實作)
- 若 products 不包含結算當週合約 → 需特別處理

**結論**:_[實測後填寫]_

---

## 觀察 5: 跨日(00:00)的 WS push 行為

**假設**:文件未提。

**實測方法**:
- 在 23:58 ~ 00:02 觀察 WS push 內容
- 紀錄:跨 00:00 時 candle 是否「跨午夜」(一根 23:55-00:00)還是「斷在午夜」(23:55 後直接跳 00:00 起算)

**預期判斷**:
- 若 K 棒在午夜斷開 → 前端排序正確(`date` 字串 ISO 字典序)
- 若 K 棒跨午夜 → 可能要特別處理 candle 時間表示

**結論**:_[實測後填寫]_

---

## 修正 / 後續行動

實測完成後,在此區記錄發現的 bug 與對應 commit:

- _[實測後填寫]_

---

## 已知前置條件 / risk

- **httpx / pydantic 等 backend 依賴**必須安裝(本機環境)
- **富邦 SDK 必須登入成功**(`.env` 的 `FUBON_PERSONAL_ID` / `FUBON_API_KEY` 設好)
- **MXF 近月合約必須在交易日**(週末 / 國定假日無資料)
- 實測 1-3 必須在交易時段才有意義(否則 WS 不訂)
- 實測 4 需要在結算日附近(每月第三週三)
