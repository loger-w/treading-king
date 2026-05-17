# 富邦 Neo API 文件參考

這個目錄存放富邦 TradeAPI 的 LLM-friendly 文件索引,供開發或 review 時 Claude(或人類)快速定位官方文件。

## 檔案

- `fubon-neo-llms.txt` — 從 <https://www.fbs.com.tw/TradeAPI/llms.txt> 鏡像來的索引(~75KB,~500 條目)。每行格式:`- [標題](https://...txt): 一行描述`。

## 為什麼只放索引、不放完整文件

完整版 `llms-full.txt`(每頁 `.txt` 的完整內容串接)約 3.7MB,不適合 commit。
索引夠用 — 找到 URL 後,用 `WebFetch` 直接抓對應頁面的最新版本即可。

## 怎麼用(給 Claude / LLM)

1. **Grep** `fubon-neo-llms.txt` 找跟任務相關的關鍵字(例:`place_order`、`websocket`、`condition`、`reconnect`)
2. 從匹配行取出 URL(結尾是 `.txt`)
3. **WebFetch** 該 URL 取得最新內容
4. 才開始實作 / review / 解釋

### 範例

| 任務 | grep 關鍵字 | 取得 URL | 動作 |
| :--- | :--- | :--- | :--- |
| review 下單程式 | `PlaceOrder` 或 `place_order` | `.../trading/library/python/trade/PlaceOrder.txt` | WebFetch |
| 行情斷線重連 | `reconnect` | `.../trading/guide/advance/reconnect.txt` | WebFetch |
| 當沖條件單 | `ConditionDayTrade` | `.../smart-condition/library/python/daytrade/...txt` | WebFetch |
| 期貨行情 K 線 | `market-data-future` `candles` | `.../market-data-future/http-api/intraday/candles.txt` | WebFetch |
| 速率限制 | `rate-limit` | `.../market-data/rate-limit.txt` | WebFetch |

## 觸發機制

專案根的 `CLAUDE.md` 已經寫入觸發規則,Claude Code 啟動時會自動載入。
當使用者的需求涉及富邦 API 關鍵字(`FubonSDK`、`fubon_*`、`sdk.stock.*`、行情、下單、條件單、…),Claude 應該先走上面的「怎麼用」流程,才動手。

## 更新索引

富邦 SDK 或文件更新後,索引可能多新條目或路徑改變。執行:

```powershell
.\scripts\update-fbs-docs.ps1
```

腳本會從 <https://www.fbs.com.tw/TradeAPI/llms.txt> 抓最新版覆寫本地。建議:
- 升級 SDK 版本時跑一次
- 任何時候發現 WebFetch 抓到 404 / 路徑變動,跑一次

## 與 `neoapi-python` skill 的關係

`neoapi-python` 是 Claude Code 的全域 skill(住在 `~/.claude/skills/neoapi-python/`),提供:
- 完整 `llms.txt` + `llms-full.txt`(3.7MB)
- 實作 cheatsheet:登入、下單、行情、當沖、版本相容性、常見錯誤
- 條件單與期貨 SDK 對照表

專案內這份只是輕量索引,讓**沒裝 skill 的 Claude 或 review 流程也能用**。
兩者搭配最好:skill 給通用 pattern,本目錄索引 + WebFetch 抓官方頁面最新內容。
