# Discord Bot 五檔改圖 + CDP 全標米字號 + 圖片 feed 放大可讀 — 設計文件

**日期**:2026-06-05
**範圍**:Discord bot 回覆(`bot/`)+ 共用畫圖層(`frontend/src/lib/`)
**狀態**:設計完成,brainstorm 決策已由 user 拍板
**修訂**:本案修訂 [discord-bot-tweaks design](./2026-06-05-discord-bot-tweaks-design.md) 的決策 #2(CDP `*`)與 #3(五檔呈現)—— 都是 user 看了實作效果後的調整

---

## 1. 目的

針對 `p代號` 查詢 bot 的回覆做三項修訂:

1. **五檔**:從「左右掛單文字」改成獨立的視覺化圖片(照網頁 `QuoteBook` 樣式)。
2. **CDP 米字號 `*`**:從「只標中央樞紐那條」改成「5 條 CDP 全標」。
3. **圖片 feed 可讀性**:讓分時圖 + 五檔圖在 Discord feed 裡**免點擊**就看清(手機 / 電腦),靠橫版比例 + 加大字級 / 降密度。

---

## 2. 決策(user 拍板)

| # | 需求 | 決策 | 修訂自 |
|---|------|------|--------|
| 1 | 五檔呈現 | 改成獨立第二張 PNG(照 `QuoteBook` 視覺),文字五檔移除 | bot-tweaks #3(原:左右掛單文字) |
| 2 | 兩張圖擺法 | **單 embed**(分時圖 + 文字欄)+ 五檔圖當**額外附件**掛 embed 下方 | 新 |
| 3 | CDP `*` | 5 條(AH/NH/CDP/NL/AL)**全加** `*`,不再區分中央樞紐 | bot-tweaks #2(原:只標中央) |
| 4 | 圖片可讀性 | 兩張圖**都**調成 feed 友善(landscape + 大字 + 疏朗);維持 2x 解析度 | 新 |

---

## 3. 逐功能設計

### 3.1 功能 1 — 五檔改視覺化圖片

#### 3.1.1 新共用元件 `frontend/src/lib/quote-book-svg.tsx`

bot 產圖用;網頁 `QuoteBook.tsx` 維持 Tailwind 版**不動**。

- 模式同 `intraday-chart-svg.tsx`:純 `createElement`、顏色全 **inline hex**(resvg 不解析 Tailwind / `var(--color-…)`),共用 `INTRADAY_THEME`(漲紅 `#e85a4f`、跌綠 `#7fc99a`、`ink` / `inkDim` / `line` / `bg`)。
- import 既有 helper:`formatTickPrice`。
- 單一元件 `QuoteBookSvg({ quote, theme })`(五檔排版簡單,**不需** geometry / 碰撞層分離,跟 intraday 不同)。
- 視覺照 `QuoteBook`:
  - 抬頭「委買賣 五檔」+ 鎖漲停 / 鎖跌停 badge(沿用 `is_limit_up_bid || is_limit_up_ask` / `is_limit_down_*`)。
  - 委買總量(紅、左大字)/ 委賣總量(綠、右大字)= 五檔 `size` 加總。
  - 左欄 5 檔買(價紅、量條由右往左)/ 右欄 5 檔賣(價綠、量條由左往右);量條 width 用兩側共用 `maxQty` normalize。
  - `price === 0`(鎖漲跌停的市價單)顯示「市價」;不足 5 檔補「—」。
- 畫布:**landscape 橫版**(寬 > 高,~720×300、約 2.4:1);字級放大、版面疏朗,目標 feed 縮到 ~450px 寬時仍可讀(見 §3.3)。實作時微調定值 + 視覺驗證。

#### 3.1.2 產圖整合 `bot/src/render.ts`

- 抽共用 helper `svgToPng(node, width, height): Buffer`(現有 `renderChartPng` 內 resvg 那段抽出)。
- 新增 `renderQuotePng(quote: QuoteResp): Buffer` —— 包 `QuoteBookSvg` → `svgToPng`。

#### 3.1.3 即時性 —— 五檔圖**不走 30s 快取**(關鍵修正)

> 這是讀完 `bot/src/index.ts` 後發現、需從原始設計草案修正的點。

- 現況:`loadSlow`(分時 / CDP / MA / 分時圖 PNG)結果被 `index.ts` 的 `TtlCache` 快取 **30s**;但**五檔 `getQuote` 每次即時抓**(`handle()` 內 `Promise.all`,不快取)。
- 因此五檔圖**必須跟即時 quote 一起產**,不能塞進 `loadSlow`(否則五檔圖會被快取 30s,顯示與當下五檔不符)。
- 作法:在 `index.ts handle()` 抓到 `quote` 後產五檔圖:

  ```ts
  const quotePng = quote ? safeRender(() => renderQuotePng(quote)) : null;
  await msg.reply(composeReply(symbol, s, quote, quotePng));
  ```

  `composeReply` 維持純函式 —— 收**已產好**的 buffer,不自己產圖(好測)。

#### 3.1.4 組裝 `bot/src/reply.ts` / `bot/src/embed.ts`

- `composeReply(symbol, s, quote, quotePng)`:多收 `quotePng: Buffer | null`,往下傳 `buildReply`。
- `buildReply`:
  - **移除** `formatLadder` 與 description 裡的文字五檔 code block。
  - **移除**重複的「委買 / 委賣(張)」總量欄位(圖已含總量大字)。
  - `files`:分時圖 `chart.png`(被 `embed.setImage("attachment://chart.png")` 引用)+ 五檔圖 `quote.png`(**不**被任何 embed 引用 → Discord 自動顯示在 embed 下方,即「單 embed + 附件」擺法)。
  - 現價列的「🔺鎖漲停 / 🔻鎖跌停」文字**保留**(surgical,不動);五檔圖的 badge 是額外、不衝突。
  - 其餘欄位(開 / 高 / 低、均價 / 量、CDP、均線)不變。

#### 3.1.5 降級

- `quote = null`(五檔抓不到)→ 不產五檔圖、`quotePng = null` → 不附;embed(分時圖 + 現價 + CDP* + 均線)照常。
- `renderQuotePng` 拋例外 → `safeRender` 回 null → 不附五檔圖。
- 與現有「分時圖產圖失敗退純文字」同模式(spec §8 不讓整則炸)。

### 3.2 功能 2 — CDP 5 條全加 `*`

#### 3.2.1 圖 `frontend/src/lib/intraday-chart-svg.tsx`(網頁 + bot 共用)

`computeIntradayGeometry()` 組 CDP label(現 ~line 182–188):移除 `k === "cdp"` 特例,5 條全加 `*`:

```diff
- text: k === "cdp" ? `${formatTickPrice(cdp[k])}*` : formatTickPrice(cdp[k]),
+ // 5 條 CDP label 全標 *(不再只標中央樞紐 —— 改為一眼分出哪些是 CDP 線)
+ text: `${formatTickPrice(cdp[k])}*`,
```

#### 3.2.2 embed 文字 `bot/src/embed.ts`(現 ~line 42)

```diff
- ? `AH ${ah} ／ NH ${nh} ／ CDP* ${cdp} ／ NL ${nl} ／ AL ${al}`
+ ? `AH* ${ah} ／ NH* ${nh} ／ CDP* ${cdp} ／ NL* ${nl} ／ AL* ${al}`
```

#### 3.2.3 共用層副作用

此圖層**網頁分時圖也用** → 網頁的 CDP 5 條 label 也會全帶 `*`。user 已接受(不再區分樞紐)。若日後要 bot-only,再加 prop 隔開。

### 3.3 功能 3 — 圖片在 Discord feed 放大可讀

#### 3.3.1 限制(誠實寫明,管理期望)

- Discord 把圖縮到聊天欄寬顯示:桌面約 **400–520px**(embed image 上限更窄 ~400px),手機 fit 螢幕寬(通常比桌面大)。**這是硬上限,圖在 feed 無法更寬。**
- **提高解析度(像素)不會讓 feed 顯示變大**,只讓「點開後」更清楚。現已 2x,維持即可。
- 「免點擊就看清」靠的是**比例 + 密度**,不是解析度。

#### 3.3.2 兩個槓桿

1. **landscape 橫版比例**:寬 > 高的圖,Discord 給足欄寬;窄高 portrait 圖會被限制高度、顯示更小。
2. **加大字級 / 降密度**:feed 顯示寬 W_feed≈450px,圖內字級 F_img 在 feed 顯示 ≈ F_img × (450 / 圖寬)。要 feed 顯示 ≥ ~11px(可讀),例:圖寬 720 → 圖內字需 ≥ ~18px。**這是設計時的量化目標。**

#### 3.3.3 分時圖 `intraday-chart-svg.tsx`(網頁 + bot 共用)

- 加大字級:現多處 `fontSize: 12`(軸標 / label)、`11`(成交量)在 ~820 寬圖縮到 feed 後僅 ~6–7px。提高到讓 feed 顯示 ≥ ~11px(依最終圖寬回推,約 16–20px)。
- 降密度:`±2%` 一條的 Y 軸格線(11 條)可改 `±4%`(6 條),減少擁擠;X 軸 6 個整點維持。
- 比例:必要時微調 `CHART_W` / `CHART_H` 讓整體更橫(landscape)。
- ⚠️ 共用層副作用:**網頁分時圖字級 / 格線也會跟著變**。網頁是大畫面,字變大影響小;但需在網頁 + bot 兩邊都肉眼確認不破版(見測試)。若衝突過大,改用 `theme` 帶字級係數區隔 bot / web(備案,非首選)。

#### 3.3.4 五檔圖

於 §3.1.1 已定 landscape ~720×300 + 大字疏朗,直接照 §3.3.2 目標設計。

#### 3.3.5 擺法已避開 grid

查到 Discord 對「多張純附件」會排成 grid 並排縮小;本案「分時圖 = embed image + 五檔 = 唯一自由附件」剛好讓兩張各自獨立全寬,不觸發 grid(§2 決策 #2)。

---

## 4. 錯誤處理 / 降級(彙整)

| 場景 | 處理 |
|---|---|
| 五檔抓不到 `quote = null` | 不附五檔圖,embed 其餘照常 |
| 五檔圖產圖失敗 | `safeRender` → null → 不附五檔圖 |
| 分時圖產圖失敗 | 既有:`safeRender` → null → 純文字 embed(現價 / CDP* / 均線仍在) |

---

## 5. 測試計畫

| 層 | 檔案 | 重點 |
|---|---|---|
| 前端單元 | `intraday-chart-svg.test.ts` | 改:5 條 CDP label **全帶** `*`(原本只斷言中央);字級 / 格線密度調整 → snapshot `-u` |
| 前端單元 | `quote-book-svg.test.ts`(新) | 給 bids/asks → 含委買 / 委賣總量、5 檔價量、`price=0`→「市價」、缺檔補「—」、鎖漲停→badge |
| bot 單元 | `embed.test.ts` | 改:CDP 欄位含 `AH*` / `NL*` / `AL*`(不只 `CDP*`);移除文字五檔斷言,改斷言 description **不含**五檔 code block;`buildReply` 收 `quotePng` → `files` 含兩張 |
| bot 單元 | `render.test.ts` | 新:`renderQuotePng(quote)` 不拋、回 `Buffer` |
| 視覺(關鍵) | 手動 render 兩張 PNG | 五檔排版 / 紅綠 / 量條 / 市價 / badge;**且把兩張縮到 ~450px 寬,確認字免點開可讀**(feed 模擬) |
| 視覺(網頁) | 開網頁分時圖 | 確認字級 / 格線改動沒把網頁圖弄破版 |
| 盤中 | user | 兩張圖在 Discord(手機 + 電腦)實際 feed 免點開可讀、堆疊順序正確 |

測試 why(對齊 CLAUDE.md Rule 9):
- CDP 測試 encode「user 要五條都標米字號」—— 業務語意改了測試才會動。
- 五檔圖測試 encode「市價 / 缺檔 / 鎖漲停的降級顯示意圖」。
- feed 模擬驗證 encode「圖的目的是免點開可讀」—— 純單元測試測不到視覺尺寸,故列為手動關卡。

---

## 6. 不在本案 scope

- 網頁 `QuoteBook.tsx` 改動(維持 Tailwind 版)。
- 訊號推播附五檔圖(屬 bot-tweaks 第二階段)。
- 五檔圖即時刷新(bot 是查詢當下 snapshot,跟現況一致)。
- 改動五檔 / CDP 的**資料來源**(`/api/quote`、`/api/cdp` 後端不動;bot 不直接碰富邦 SDK)。
- **突破 Discord feed 欄寬上限**(技術不可能;本案只在上限內最佳化可讀性)。
