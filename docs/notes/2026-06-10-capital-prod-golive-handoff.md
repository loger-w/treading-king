# 群益正式環境上線 + 首次實單測試 — 交接(2026-06-10)

## 為什麼有這份
模擬/測試沙盒在**群益端未開通**(官方 `SKCOMTester` 用 `2:測試` 也回 1097、`0:正式` 可登 → 群益側問題,非程式;待群益開通)。user 決定**跳過模擬、直接接正式環境手動掛單測試**。本檔給新 session 接手(原 session context 太長)。

## 已驗證(可放心)
- **正式登入/憑證/初始化**:`backend/scripts/capital_login_probe.py prod` 於 2026-06-10 12:05 **全綠**(`Login/InitOrder/ReadCertByID = 0`、`狀態 ok`),用的就是主 repo `backend/.env`。
- 下單面板已在 **main**(PR #19 merged):後端 `routes/capital.py` `/api/capital/*`、前端 `TradingPanel`、WS `capital_order` 都在。
- 後端 233 pytest、前端 72 vitest + build 綠。
- `.env` 的 `CAPITAL_USER_ID/PASSWORD/DLL_DIR` 已填且已驗(正式登入過)。

## ⚠️ 最大風險:下單鏈路「從來沒實跑過」
送單路徑(`to_stockorder_fields` → `SendStockOrder` → `OnNewData` 回報解析)**只有單元測試,從沒對真實群益跑過**(那本來是模擬環境要驗的)。所以**第一筆正式單 = 整條鏈路第一次真的執行,而且是真錢**。

**最關鍵的未驗欄位:`nQty` 是「張」還是「股」。** 我們 model 的 `qty` 當「張」直接塞給 `nQty`(`backend/services/capital_mapping.py:23`)。群益 `SendStockOrder` 的 `nQty` 真實語意**未對實際環境驗過** —— 若群益要的是「股」,你輸入 `1` 會變 1 股(零股)而非 1 張。**務必用下面安全程序、對照群益自家 App 確認。**

## .env 設定(主 repo `C:\side-project\treading-king\backend\.env`)
```
CAPITAL_ENV=prod
CAPITAL_FULL_ACCOUNT=<你的群益證券完整帳號>   # 送單必填(登入不需、送單需要)
CAPITAL_MAX_QTY=1            # 先鎖 1,確認語意後再放寬
CAPITAL_MAX_AMOUNT=100000    # 預估金額(價×量×1000)超過直接擋
CAPITAL_ORDER_ENABLED=false  # ← 先 false 看面板;確認一切 OK 才翻 true,送得出單
```

## 啟動(主 repo、main 分支)
`.\start.ps1`(或 backend `uvicorn main:app --reload`、frontend `npm run dev`)
- 面板在 Monitor 最右欄。`CAPITAL_ENV=prod` → 後端啟動會**登入你真實帳號**(只登入;`ORDER_ENABLED=false` 時送單一律被擋回 423)。
- 群益健康燈應轉綠。

## 安全首單程序(務必照做)
1. 先 `ORDER_ENABLED=false` 跑,確認:健康燈綠、五檔點價帶入委託價、版面正常。
2. 要實測送單時,才把 `CAPITAL_ORDER_ENABLED=true` + 填 `CAPITAL_FULL_ACCOUNT`,重啟後端。
3. **第一筆下「絕不會成交」的限價單**:挑一檔,**限價買掛在遠低於市價**(例:市價 100,掛買 50),數量 **1**。→ 會送出、掛著、不成交。
4. **打開群益自家 App 看委託**,核對:標的 / 買賣別 / 價格 / **數量(在這裡確認 `nQty` 到底是張還是股)**。和你輸入的一致 = 映射正確。
5. v1 面板**沒有刪單功能** → 那筆測試掛單請到**群益 App 取消**(或等收盤自動失效)。
6. 數量語意確認無誤後,才放寬 `CAPITAL_MAX_QTY` 做正常交易。
7. 每次送出前面板有**二次確認彈窗**(標的/買賣/價/量/預估金額)—— 每次都核對再按。正式環境彈窗是**紅底**警示。

## 欄位映射參考(`capital_mapping.py`,送錯=下錯單)
- `sBuySell` 買=0 賣=1｜`nSpecialTradeType` 市價=1 限價=2｜`nTradeType`(TIF) ROD=0 IOC=1 FOK=2｜`sFlag` 現股=0 融資=1 融券=2｜`sPeriod`=0 盤中｜`sPrime`=0 上市櫃｜`bstrPrice`=`%.2f`｜`nQty`=`req.qty`(張?股?**待驗**)

## 完整背景
- 登入除錯全程:`docs/notes/2026-06-09-capital-m1-login-handoff.md`、memory `project_capital_order_panel`。
- 模擬開通卡群益(證據:官方 `SKCOMTester` `2:測試` 也登不進、`0:正式` 可以)。
