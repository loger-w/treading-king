# Discord Bot 五檔改圖 + CDP 全標米字號 — 設計文件

**日期**:2026-06-05
**範圍**:Discord bot 回覆(`bot/`)+ 共用畫圖層(`frontend/src/lib/`)
**狀態**:設計完成,brainstorm 決策已由 user 拍板
**修訂**:本案修訂 [discord-bot-tweaks design](./2026-06-05-discord-bot-tweaks-design.md) 的決策 #2(CDP `*`)與 #3(五檔呈現)—— 兩者都是 user 看了實作效果後的調整

---

## 1. 目的

針對 `p代號` 查詢 bot 的回覆做兩項修訂:

1. **五檔**:從「左右掛單文字」改成獨立的視覺化圖片(照網頁 `QuoteBook` 樣式)。
2. **CDP 米字號 `*`**:從「只標中央樞紐那條」改成「5 條 CDP 全標」。

---

## 2. 決策(user 拍板)

| # | 需求 | 決策 | 修訂自 |
|---|------|------|--------|
| 1 | 五檔呈現 | 改成獨立第二張 PNG(照 `QuoteBook` 視覺),文字五檔移除 | bot-tweaks #3(原:左右掛單文字) |
| 2 | 兩張圖擺法 | **單 embed**(分時圖 + 文字欄)+ 五檔圖當**額外附件**掛 embed 下方 | 新 |
| 3 | CDP `*` | 5 條(AH/NH/CDP/NL/AL)**全加** `*`,不再區分中央樞紐 | bot-tweaks #2(原:只標中央) |

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
- 畫布:寬 ~600、高依內容(抬頭 + 總量列 + 5 檔列)~280;實作時微調定值。

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
| 前端單元 | `intraday-chart-svg.test.ts` | 改:5 條 CDP label **全帶** `*`(原本只斷言中央);snapshot `-u` |
| 前端單元 | `quote-book-svg.test.ts`(新) | 給 bids/asks → 含委買 / 委賣總量、5 檔價量、`price=0`→「市價」、缺檔補「—」、鎖漲停→badge |
| bot 單元 | `embed.test.ts` | 改:CDP 欄位含 `AH*` / `NL*` / `AL*`(不只 `CDP*`);移除文字五檔斷言,改斷言 description **不含**五檔 code block;`buildReply` 收 `quotePng` → `files` 含兩張 |
| bot 單元 | `render.test.ts` | 新:`renderQuotePng(quote)` 不拋、回 `Buffer` |
| 視覺 | 手動 render 一張五檔 PNG | 確認排版、紅綠、量條、市價、badge |
| 盤中 | user | 兩張圖在 Discord 的實際堆疊順序符合預期 |

測試 why(對齊 CLAUDE.md Rule 9):
- CDP 測試 encode「user 要五條都標米字號」—— 業務語意改了測試才會動。
- 五檔圖測試 encode「市價 / 缺檔 / 鎖漲停的降級顯示意圖」。

---

## 6. 不在本案 scope

- 網頁 `QuoteBook.tsx` 改動(維持 Tailwind 版)。
- 訊號推播附五檔圖(屬 bot-tweaks 第二階段)。
- 五檔圖即時刷新(bot 是查詢當下 snapshot,跟現況一致)。
- 改動五檔 / CDP 的**資料來源**(`/api/quote`、`/api/cdp` 後端不動;bot 不直接碰富邦 SDK)。
