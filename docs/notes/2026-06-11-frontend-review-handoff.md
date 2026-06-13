# 前端全規模 Review 完工 Handoff(2026-06-11)

93 個確認 findings(High 11 / Medium 32 / Low 50)**全數修畢**。
完整 findings 報告:`docs/notes/2026-06-11-frontend-full-review.md`。

## 分支狀態(兩支疊著,都未併)

```
feat/capital-order-panel-v2          ← PR #22 基底
  └─ fix/frontend-review-1           ← High+Medium,9 commits
       └─ fix/frontend-review-lows   ← Low 50 項,1 commit(d8308c1,net -211 行)
```

驗收 OK 後:`fix/frontend-review-lows` 一路併回即可(包含前兩層)。
驗證狀態:前端 182 測 + `tsc -b` + vite build 全綠;後端 324 測全綠(改過 `services/cdp.py`)。

## 主要改了什麼(按影響排序)

### 真錢下單面板(capital)
- **漲跌停計算修正**:`tick.ts` 與後端 `cdp.py` 同源 bug——947 個合法參考價算出超出法定 ±10% 的價(deci-cent 整數運算修正)。漲停快捷、市價閘價、閃電梯夾界、後端鎖漲停 latch 都吃這個值
- **市價單閘價完整性**:送出當下由參考價重新推導,不信任可被五檔點價覆寫/跨標的殘留的輸入框;缺參考價鎖送出
- **損益口徑統一**:下單分頁 PositionCard 改券商基底(含費稅息),與庫存分頁同數字;前端自估的 netPnl 移除
- **useQuoteBook 重構**:module-level 共用 poller(QuoteBook+FlashPanel 同標的共用一條 1Hz,省一半富邦額度)、同內容不重繪、reference_price 併入回傳(閃電梯夾界天然帶重試)
- 確認框送出中鎖鈕+回應歸屬檢查;健康燈連續失敗降級;委託清單 30s 輪詢備援;序號守門(剛刪的單不再短暫復活);平倉閘改參考價基準;委託價驗 tick 檔位;平倉確認跟最新部位

### 穩定性
- 分時圖 hover 越界白屏(會連下單面板一起死)一行守門
- 全站 stale-response race 掃蕩 ~10 處:快速切股/切書籤/切 timeframe 不再被晚到的舊回應蓋掉
- 訊號規則:preset 切回自訂條件後殘留 strategy 鍵(新條件被引擎無聲忽略)已修
- prevClose 拒收降級 null;MXF viewRange 縮水夾界(每天 15:00 夜盤重開不再空白)

### 效能
- **頁根零 per-tick state**:quotes 下移書籤欄、candles 下移分時圖(tick 改走 module bus)——行情尖峰時下單面板的 main thread 不再被整頁重繪佔住
- TriggerList/IntradayChartStatic/IndexIntradayStatic memo;MXF spans/dayOpenBaseline 緩存;wheel 改原生 non-passive(縮放不再帶動頁面)
- 隱藏頁暫停富邦 REST 輪詢(active prop;下單面板輪詢刻意常駐)

### 清理
- 死碼 11 項(WatchlistWithChips/useWatchlist 整檔等);新共用件:ModalShell+EnvNotice(8 dialog)、useEscapeKey、ProximityEditor、signal-labels(CDP 正名「中軸」)、isTickAligned、X_AXIS_TICKS

## 明日開盤實測清單

> 建議順序:先 test 環境過 A,再上 prod 小單。

### A. 下單面板(真錢,最優先)
- [ ] 限價單:切標的後委託價**清空**;輸入 off-tick 價(如 105.13)送出鈕鎖住;低價股快捷正確(參考價 9.05 → 漲停 9.95 不是 9.96)
- [ ] 市價單:勾市價自動帶閘價;切標的瞬間顯示「等待參考價…」;市價模式點五檔**不可**覆寫價格欄
- [ ] 確認框:點確認後顯示「送出中…」且不可連點;送出中 Esc 關窗重開,舊回應不會關掉新窗
- [ ] 損益一致:同一部位「下單」與「庫存」分頁數字相同(含費稅息)
- [ ] 閃電梯:五檔每秒動、點價送單、紅方格刪單、全部刪單;換標的後 hint 清掉;「估」徽章不該掛整天;跟隨置中只在中心價移動時捲
- [ ] 健康燈:手動關後端 → ~20 秒內變 unreachable、送單鈕鎖;重啟恢復
- [ ] 平倉:確認框張數隨部分成交更新;環境警示三態正確(prod 綠框真錢警示)

### B. 圖表
- [ ] 分時圖:**滑鼠停在圖上快速連切冷門股**(原白屏路徑)不崩;CDP/MA 線不殘留別檔價位
- [ ] MXF:滾輪縮放頁面不動、console 無 passive 錯誤;5m 右緣不再每分鐘長假 K 棒;**15:00 夜盤重開圖不空白**(這條要等下午);輪詢失敗顯示角落提示、圖不消失
- [ ] 行情尖峰流暢度:書籤多檔跳動時,點閃電梯/hover 圖表不卡(頁根已無 per-tick 重繪)

### C. 同步與切頁
- [ ] 書籤:單列 × 移除 → sidebar 計數/「全部」view 立即同步;刪除選中書籤 → 退回「全部」;dialog 內新增書籤按取消 → sidebar 也有;面板不再整個閃白(remount 改 refresh)
- [ ] 訊號規則:preset 規則列表顯示「策略:漲停打開碰 CDP」;editor 開著按 Esc 只關 editor;**preset 切回自訂條件儲存後,確認觸發走新條件**(原 bug 會無聲照舊策略觸發)
- [ ] 切頁:Monitor↔大盤↔MXF 來回,圖表短暫載入後恢復;停在 Monitor 時後端 log 不應出現大盤/MXF 的背景輪詢
- [ ] 匯入設定後頁面自動重載;toolbar 搜尋框與分時走勢欄對齊

## 後端 Review(新 session 啟動指引)

1. Workflow 腳本參考:`docs/notes/2026-06-11-full-codebase-review.workflow.js`(前端版)——換掉 `UNITS`(後端按子系統分組:fubon 行情 client/WS、futures、signal_engine、capital_*(COM/safety/store/balance)、routes、local_store、cdp/rate_limiter)與 `CROSSCUT`(建議:asyncio/執行緒安全(COM 單執行緒佇列+event loop 混用)、錯誤處理與降級、rate limit 消耗、死碼)
2. **CLAUDE.md 富邦工作流程適用**:reviewer 評斷 `fubon_*` 行為前要查 `docs/api/fubon-neo-llms.txt` 索引 + WebFetch 對應文件,不可憑印象判 SDK 行為
3. capital_* 是真錢路徑,嚴重度從嚴;後端測試跑法:`backend/.venv/Scripts/python.exe -m pytest -q`(324 測)
4. 流程同前端:finders 並行 → 去重 → 每 finding 一個對抗驗證者 → 報告人工過目再修
