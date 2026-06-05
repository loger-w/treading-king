# Discord 個股查詢 Bot — 設計

日期:2026-06-05
狀態:設計待 user 複審

---

## 1. 背景與目標

使用者在 Discord 打一個代號(例 `p2330`),bot 立刻回那檔**當下的分時走勢圖** + **CDP 5 線** + **日均(MA5/MA20)** + **委買賣五檔**,類似 Line 看盤 bot 的「打代號查股」。

服務對象與在線需求(已與 user 確認):**交易群組共用,盤中在線即可** —— 一台機器(通常 user 的)盤中跟 `start.ps1` 一起開著當 host,群組成員都能查;收盤關掉沒差。**不需要雲端 / 24-7**。

關鍵前提:資料(分時 / CDP / MA / 五檔)在專案裡**幾乎全現成**,都已接好富邦並以 FastAPI endpoint 暴露。本功能要新做的只有「**接收輸入 → 產圖 → 回覆**」這一段。

## 2. 範圍

**做(in scope)**
- 一個獨立的 Node Discord bot(discord.js),盤中常駐。
- 觸發:純訊息 `p<代號>`(需 Message Content Intent)。
- 回覆:一則 embed(精確數字 + 五檔階梯)+ 一張分時走勢 PNG。
- 產圖:**重用現有網頁圖的畫圖邏輯**,抽成共用 `chart-svg` 模組,用 `@resvg/resvg-js` 轉 PNG(不開瀏覽器)。
- 短快取:圖 / CDP / 均線快取 30 秒;五檔即時抓。

**不做(out of scope,列入未來)**
- Slash 指令(`/股票`)。本版只做 `p代號` 純訊息。
- 臨時旗標(如 `p2330 cam` 開 Camarilla)。本版疊圖固定。
- 24-7 / 雲端 host、憑證搬遷。
- 下單、改單(專案硬約束:只訂行情,後端不動富邦)。
- 改動富邦 / Python 後端邏輯(本功能只當後端 REST 的消費者)。

## 3. 整體架構

新增**一個 Node 程序**當 bot,跟 backend(:8000)、frontend(:5173)並列,由 `start.ps1` 開第三個視窗。所有新程式碼都在 Node / 前端這一側 —— 因為**畫圖邏輯本來就是 TypeScript、resvg 也是 JS 生態**,同一技術棧最連貫;富邦 / Python 後端**完全不碰**。

```
Discord 訊息 "p2330"
   │
   ▼
Node bot (discord.js)
   │  ① 抓資料(打現有 Python endpoint, http://127.0.0.1:8000, 帶 X-API-Key)
   │       即時:   GET /api/quote/{s}            → 五檔 bids/asks + 漲跌停旗標
   │       30s 快取: GET /api/candles/{s}/intraday → 1m K + VWAP + prev_close
   │                GET /api/cdp/{s}             → CDP 5 線
   │                GET /api/ma/{s}              → SMA5 / SMA20
   │                GET /api/symbols?search={s}  → 股名(找完全相符)
   │  ② 餵進共用 chart-svg 模組 → 產出跟網頁一模一樣的 SVG 字串
   │  ③ @resvg/resvg-js:SVG → PNG(2x 清晰度,內嵌 CJK 字型)
   │  ④ 組 embed(現價/漲跌/開高低/均價/量/CDP/MA + 五檔階梯)
   ▼
回貼頻道:embed + chart.png
```

### 為什麼是「resvg 重用現有 SVG」而非 matplotlib / 截圖

User 要求**最穩 + 最好看、品質優先**。三選項評比:

| 方式 | 好看 | 穩定 | 取捨 |
|---|---|---|---|
| **resvg 重用現有 SVG**(採用) | ★ 你那張圖本人 | ★ Rust 算圖引擎、決定性、無瀏覽器 | 要抽共用模組 + 顏色 inline + 內嵌字型 |
| 無頭瀏覽器截網頁圖 | ★ 本人 | △ 要開 Chromium、等前端載完才能截、會 crash/timeout | host 多 ~300MB、依賴前端開著 |
| matplotlib 後端重畫 | △ 神似非本尊、要調中文字型 | ★ 純 Python | 重畫一套、跟網頁長期漂移 |

只有 resvg 重用同時站在「最好看(就是你的圖)」與「最穩(決定性、無瀏覽器)」兩邊,故採用。

## 4. 元件分解

每個單元一個清楚職責、可獨立測試。

### 4.1 共用畫圖模組 `frontend/src/lib/chart-svg.ts`(新)
- **職責**:吃圖資料 → 回傳分時走勢的 **SVG 字串**(純函式,無 React、無 DOM、無資料抓取)。
- **介面**:
  - `computeChartGeometry(input): Geometry` — scaleX/scaleY、走勢/VWAP polyline、可見 CDP/Cam/MA key、今日高低、量能 scale、碰撞撐開後的右側 label。(目前 `IntradayChart` useMemo 內的計算原封移來)
  - `renderIntradayChartSvg(input): string` — 用 geometry 產出完整 static SVG 字串(紅綠填色、±2% 格線、CDP/Cam/MA/VWAP 線、走勢線、今日高低、量能子圖、X 軸時間)。
- **input**:`{ candles, prevClose, cdp, camarilla, ma, flags, theme, size }`(代號 / 股名不在此 —— 圖本身不畫那兩個,網頁放 HTML header、bot 放標題帶)。
- **關鍵約束(resvg 限制)**:顏色一律**寫死 hex**(由 `theme` 帶入,值取自 Tailwind theme 的 bull/bear/accent/line/ink 等),**不可**用 Tailwind class 或 `var(--color-…)` —— resvg 不解析。這是本次重構的主要工。
- **依賴**:現有 `lib/tick.ts`(formatTickPrice / roundToNearestTick)、`lib/chart-labels.ts`(resolveCollisions)、`lib/intraday-time.ts`。皆已是純 lib,可直接共用。

### 4.2 `frontend/src/components/IntradayChart.tsx` 重構(改)
- 改為**消費** `chart-svg.ts`:
  - 用 `computeChartGeometry` 取代內嵌 useMemo(scales 給 hover crosshair 用)。
  - static 圖層改用 `renderIntradayChartSvg` 產出的字串,以 `dangerouslySetInnerHTML` 注入一層 `<svg>`/`<g>`。
  - **互動層(hover crosshair)、toggle 按鈕、資料抓取 useEffect 仍留在 component**,以 React 疊在 static 圖上(用同一份 geometry,確保對齊一致)。
- **網頁外觀必須前後一致** —— 用 snapshot test + 手動目視把關。

### 4.3 bot 資料 client `bot/src/data.ts`(新)
- 包裝對 Python endpoint 的 fetch,統一帶 `X-API-Key`、timeout、錯誤轉譯。
- 提供 `getQuote`(即時)、`getCandles`/`getCdp`/`getMa`/`getName`(走 4.6 快取)。

### 4.4 bot 產圖 `bot/src/render.ts`(新)
- 組 `chart-svg` input(疊圖旗標見 §6)→ `renderIntradayChartSvg` → 包一層含**標題帶**(代號 名稱 現價 漲跌%)的外層 SVG → `@resvg/resvg-js` 轉 PNG(2x、內嵌 `bot/assets/` 的 CJK 字型)→ 回 PNG Buffer。

### 4.5 bot 回覆組裝 `bot/src/embed.ts`(新)
- 組 discord.js `EmbedBuilder`:標題、現價/漲跌、開高低、均價(VWAP)、量、CDP 5 值、MA5/MA20、委買/委賣總量、五檔等寬字階梯、漲跌停 badge、資料時間。詳見 §5。

### 4.6 bot 快取 `bot/src/cache.ts`(新)
- per-symbol、TTL 30 秒,快取 **candles / cdp / ma / 已 render 的 PNG**。
- **五檔(quote)不進快取**,每次即時抓(逐秒在動)。

### 4.7 bot 入口 `bot/src/index.ts`(新)
- discord.js client(intents 含 `MessageContent`),`messageCreate` handler:
  - 忽略自己 / 其他 bot 的訊息。
  - 比對 `^[pP]([0-9]{4,6}[A-Z]{0,2})$`;不符**靜默忽略**(不回「未知指令」以免洗頻)。
  - 符合 → 正規化代號大寫 → 抓資料 → 產圖 → 回 embed + PNG。

## 5. 回覆內容規格

一則 embed + 一張 `chart.png`(`setImage('attachment://chart.png')`)。

- **accent 顏色**:漲用紅、跌用綠(台股慣例;hex 取自 web theme 的 bull/bear)。
- **title**:`{name} {symbol}`(name 抓不到時只顯示代號)。
- **現價 / 漲跌**:`{close} ▲{Δ} (+{pct}%)`(現價 = 最新一根分時 K 的 close;基準 = prev_close)。
- **欄位(inline)**:開 / 高 / 低、均價(VWAP)、量。
- **CDP**:`AH / NH / CDP / NL / AL` 五值(沿用 tick 對齊後的值)。
- **均線**:`MA5 / MA20`(無值顯示 —)。
- **委買/委賣總量**:五檔加總,單位**張**(沿用 QuoteBook)。
- **五檔階梯**(等寬 code block,賣5→賣1、分隔、買1→買5;量單位張;`price===0` 顯示「市價」):
  ```
  賣5  636.5    55
  賣4  636.0   120
  賣3  635.5    88
  賣2  635.0   210
  賣1  634.5   340
  ───────────────
  買1  634.0   410
  買2  633.5   150
  買3  633.0    90
  買4  632.5   120
  買5  632.0    60
  ```
- **漲跌停**:`isLimitUp`/`isLimitDown` 為真時加「鎖漲停 / 鎖跌停」標記。
- **資料時間**:embed timestamp 或一行 footer。

## 6. 圖規格

- **沿用網頁視覺**(就是 `IntradayChart` 那張),固定尺寸 820×(460+量能),2x render。
- **預設疊圖**:走勢線(紅綠)+ 紅綠填色 + ±2% 格線 + VWAP + **CDP 5 線** + **MA5 / MA20** + 成交量子圖 + 今日高低點。
- **Camarilla 預設關**(8 條線在小圖太擠,CDP 已給 5 條參考位)。本版疊圖固定,不開臨時旗標。
- **標題帶**:圖頂加一條 代號 名稱 現價 漲跌%,讓圖單獨被看時也自描述。
- **字型**:內嵌一支 CJK 字型(如 Noto Sans TC)於 `bot/assets/`,resvg `loadSystemFonts:false` 指定它 —— 跨機器一致、中文不變豆腐字。

## 7. 設定與祕密

bot 自己的 `.env`(`bot/.env`,不進 git):
- `DISCORD_BOT_TOKEN` — Discord bot token(必填)。
- `BACKEND_BASE_URL` — 預設 `http://127.0.0.1:8000`。
- `BFF_API_KEY` — **後端有設時必填且需一致**(否則被 `APIKeyMiddleware` 擋 401)。
- (可選)`BOT_ALLOWED_CHANNELS` — 頻道白名單;留空 = 任何看得到的頻道都回。

**Discord 開發者後台一次性設定**:建立 Application + Bot、取得 token、**開 Message Content Intent**(privileged,私人小 bot 只是開關,免審核)、用 OAuth2 URL 邀請進群組(權限:讀訊息 / 送訊息 / 嵌入連結 / 附加檔案)。

## 8. 錯誤處理(不靜默,Rule 12)

- **代號格式不符**:靜默忽略(根本不是要查股)。
- **找不到該檔 / 富邦不可用(503)**:回一行友善錯誤(「查無此檔」/「行情暫時不可用」)。
- **盤前 / 假日無分時資料**(candles 空):**不靜默** —— 回文字「目前無分時資料」+ 仍附 CDP / MA 數字(還是有用)。
- **CDP / MA 個別失敗**:該區塊顯示 —,其餘照回(沿用網頁「失敗欄位 null」風格)。
- **產圖 / resvg 失敗**:退回純文字 embed(現價 + 五檔 + CDP/MA),不讓整則炸掉。

## 9. 共用模組重構策略與防漂移(品質關鍵)

User 選「抽出共用模組」(網頁 + bot 共用一份,零漂移)。風險是動到能用的 800 行 `IntradayChart`。防護:
1. **先寫 snapshot test**:對固定 fixture 資料呼叫 `renderIntradayChartSvg`,鎖住 SVG 輸出字串。
2. **重構分兩步**:先抽 `computeChartGeometry`(純計算,不改外觀)→ 跑既有測試;再抽 `renderIntradayChartSvg` 並讓 component 注入它 → snapshot + 目視確認網頁圖前後一致。
3. 之後任何改圖,snapshot 變動即顯示,網頁與 bot 同步、不會偷偷走樣。

## 10. 啟動整合

`start.ps1` 加第三個視窗:`Set-Location bot; npm run start`。`install.ps1` 加 `cd bot; npm install`。bot 啟動時檢查 `DISCORD_BOT_TOKEN` 存在,缺則明確報錯退出。

## 11. 測試策略(Rule 9:測意圖)

- **chart-svg snapshot**:鎖外觀;意圖 = 「網頁與 bot 的圖永遠同源、改動需自覺」。
- **代號解析**:`p2330`/`p0050`/`p00878`/`p2330B` 命中;`people`/`2330`(無 p)/`p12` 不命中 —— 意圖 = 只在真的查股時觸發,不洗頻。
- **五檔階梯格式化**:張、市價(price=0)、不足五檔補 —、總量加總、對齊 —— 意圖 = 跟 QuoteBook 語意一致。
- **embed 組裝**:漲/跌 accent 顏色、欄位齊全、CDP/MA null 處理。
- **快取**:30s 內同檔不重打 candles/cdp/ma;quote 每次都抓。

## 12. 成功標準(Rule 4)

1. 群組成員在頻道打 `p2330`,數秒內收到 embed + 一張**與網頁同款**的分時走勢圖。
2. 圖含走勢(紅綠)、CDP 5 線、MA5/MA20、VWAP、量能、今日高低;中文清晰。
3. embed 含現價/漲跌、開高低、均價、量、CDP 值、MA 值、**委買賣五檔階梯**(張 / 市價 / 漲跌停)。
4. 五檔是即時的;圖 / CDP / 均線 30 秒內重複查同檔走快取。
5. 盤前 / 查無 / 富邦掛掉時回明確訊息,不靜默、不整則炸掉。
6. 富邦 / Python 後端零改動;網頁分時圖外觀零變化。

## 13. 風險與開放問題

- **重構 `IntradayChart` 的回歸風險** → 靠 snapshot + 目視(§9)。
- **resvg 對 SVG 特性支援**:用到 clipPath / strokeDasharray / fillOpacity / polygon / text,resvg 皆支援;但顏色與字型須顯式提供(§4.1、§6)。
- **現價可能 ≤30 秒舊**(取自快取的分時 K;`/api/quote` 不回 lastPrice)。本版可接受。若要 tick 級即時現價:之後可在 `/api/quote` 加回 last/prev 欄位(一次富邦 quote 拿齊,小幅後端改動)或即時打 `/api/quotes/snapshot`。
- **Message Content Intent** 是 privileged;群組規模大到要審核時才需處理,目前私人群組免。
- **濫用 / 洗頻**:暫靠 30s 快取吸收;若需要再加 per-user cooldown。
