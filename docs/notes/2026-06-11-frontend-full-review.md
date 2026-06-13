# 前端全規模 Review 報告(2026-06-11)

> 產出方式:12 個 finder agent(9 子系統 + 3 橫切面)並行深讀 → 同檔同面向 ±3 行去重 → 每個 finding 一個獨立對抗驗證者。
> 範圍:frontend/src 全部 89 檔(測試檔僅作意圖參考)。分支:feat/capital-order-panel-v2。
> 統計:原始 117 → 去重 99 → **確認 93 / 駁回 6**(駁回理由見文末)。

## 嚴重度分布

| 嚴重度 | 數量 |
|---|---|
| Critical | 0 |
| High | 11 |
| Medium | 32 |
| Low | 50 |

| 面向 | 數量 |
|---|---|
| 正確性 | 49 |
| React/效能 | 17 |
| 簡化/死碼 | 27 |

---

## High(11 項)

### 1. 單一書籤 view 的 × 移除只 refresh 該 group 的 items,sidebar 計數、「全部」view 與報價訂閱 union 全部不同步

- **位置**:`frontend/src/components/BookmarksPanel.tsx:186`
- **面向**:正確性 | **驗證信心**:high | finder: bookmarks-panel

SingleListView 的 onRemove 直接綁 useBookmarkItems 的 removeItem,它只打 DELETE + refreshSingle。對照 BookmarkEditMode 的所有操作都走 onChanged=refreshAfterMutation(同時 refresh groups/single/all),這條主線路徑漏了 refreshGroups 和 refreshAll。後果:(1) sidebar 的 group count 與 header 總數停在舊值;(2) 切到「全部」view 仍看得到剛移除的股票(byGroup/bySymbolFirst 沒更新);(3) bySymbolFirst 沒變 → onItemsChanged 不會重發 → Monitor 的 bookmarkSymbols 仍含已移除 symbol,報價訂閱 union 殘留。要等下一次其它 mutation 或整個 panel remount 才會自我修正。

**建議修法**:不要直接傳 removeItem,改傳 wrapper:async (s) => { await removeItem(s); await Promise.all([refreshGroups(), refreshAll()]); },或統一讓單列移除也走 refreshAfterMutation 的同一條路。

### 2. 匯入成功後前端狀態完全不刷新,訊息卻宣稱「設定已即時套用」

- **位置**:`frontend/src/components/ConfigIODialog.tsx:39`
- **面向**:正確性 | **驗證信心**:high | finder: bookmark-dialogs

匯入是「整包取代」後端的書籤/訊號規則/監聽清單(backend/routes/config_io.py 確實會套用並重訂閱),但前端沒有任何 refetch 路徑:ConfigIODialog 沒有 onImported callback;BookmarkManageDialog 的 groups 是 BookmarksPanel useBookmarks 傳下來的 prop;useBookmarks / useMonitorList / useActiveSignals 都只在各自 mutation 後 refresh、無輪詢。所以 user 匯入後關掉 dialog,sidebar 書籤、監聽清單、訊號規則顯示的全是匯入前的舊資料,而畫面上的成功訊息明確說「已即時套用」。對一個破壞性的整包取代操作,user 完全無法從 UI 確認匯入結果,可能誤判失敗再匯一次或基於舊清單繼續操作。

**建議修法**:匯入成功後讓前端真的重載:最簡單是 setMsg 後 window.location.reload()(本機工具可接受);或給 ConfigIODialog 加 onImported prop,由 BookmarkManageDialog → BookmarksPanel 串回 refreshGroups/refreshAll + monitor refresh + rules refresh。

### 3. 編輯 preset 策略規則切回「無(自訂條件)」後儲存,filter_json 仍殘留舊 strategy,自訂條件被後端無聲忽略

- **位置**:`frontend/src/components/ActiveSignalEditor.tsx:176`
- **面向**:正確性 | **驗證信心**:high | finder: signals

strategy 存在獨立 state,但 filter state 初始化自 initial.filter_json(含 strategy 鍵),之後從未移除。使用者編輯一條 preset 策略規則 → 下拉選「無(自訂條件)」→ setStrategy(null) 只清 strategy state → 加自訂條件後儲存,走 else 分支 `filterToSave = filter`,送出的 filter_json 仍帶著原本的 strategy 物件。後端 ActiveFilter(backend/models/condition.py:189)明定「strategy 存在時由 strategy 定義整條 filter」,所以這條規則會繼續照舊策略觸發,使用者新設的自訂條件完全不生效,且 UI 重新打開編輯器時 strategy select 又會顯示舊策略,看起來像沒存成功。

**建議修法**:else 分支改為 `filterToSave = { ...filter, strategy: null }`(或在 strategy select 切到 none 時同步 setFilter 清掉 strategy 鍵)。

### 4. hover.idx 過期越界時 filteredCandles[hover.idx] 為 undefined,render 直接 TypeError 白屏

- **位置**:`frontend/src/components/IntradayChart.tsx:195`
- **面向**:正確性 | **驗證信心**:high | finder: intraday-chart

hover state 只在 mousemove/mouseleave 更新,symbol 或 candles 變動時不會重設。切 symbol 時 candles 先清空、<svg> 被換成「載入中」div——DOM 移除不會觸發 mouseleave,hover 殘留舊 idx。新 symbol 資料回來後若 candle 數較少(冷門股分鐘 K 棒遠少於 270 根很常見),`const candle = filteredCandles[hover.idx]` 是 undefined,下一行 `scaleY(candle.close)` 直接 throw。全 app 沒有任何 ErrorBoundary(grep 確認),React 會 unmount 整棵樹 → 含真錢下單面板(TradingPanel)的 Monitor 頁整頁白屏。觸發路徑:滑鼠停在圖上、用鍵盤搜尋切股;或同 symbol 下 poll 回傳比現有更短的陣列(resolveCandleUpdate 只擋空陣列不擋變短)。姊妹元件 MXFIntradayChart.tsx:462 已用 `hover && visibleCandles[hover.idx] &&` 防了同一個洞,本元件漏防。

**建議修法**:比照 MXFIntradayChart 在 JSX 守門:`{hover && filteredCandles[hover.idx] && (() => {...})()}`;或在 candles/symbol 變動時 setHover(null)(useEffect on symbol)。前者一行解掉且涵蓋所有來源。

### 5. CDP/Camarilla/MA 三個 fetch effect 沒有過期回應防護,快速切 symbol 會把別檔的價位線畫在當前圖上

- **位置**:`frontend/src/components/IntradayChart.tsx:41`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: intraday-chart

candles 路徑已特地寫了 resolveCandleUpdate 防「飛行中舊請求回來蓋畫面」(intraday-candle-update.ts 註解明寫此 race 真實發生過),但同元件的 cdp/camarilla/ma 三個 effect(36–63 行)完全沒有同等防護:effect 開頭 setCdp(null) 清掉舊值後就裸發 `api.cdp(symbol).then(setCdp)`。A→B 快速切換時,若 A 的回應比 B 慢到達(後端 CDP 需查昨日 OHLC,延遲不穩),最終 state 停在 A 的 CDP 5 線——而且會一直顯示到下次切 symbol 或 toggle 才修正。價位相近的兩檔股很難肉眼察覺,使用者會拿錯誤的 CDP/Camarilla/MA 價位做交易決策(Monitor 頁同欄就是群益真錢下單面板)。

**建議修法**:三個 effect 各加 cleanup 旗標:`let stale = false; api.cdp(symbol).then((r) => { if (!stale) setCdp(r); })...; return () => { stale = true; };`。或抽一個共用的 useStaleSafeFetch,三處同模式重複本就可合併。

### 6. WS 推送的 1 分 K 不分 timeframe 直接 merge 進 5m/15m/60m 序列,右緣出現錯誤 K 棒

- **位置**:`frontend/src/hooks/useMXFCandles.ts:69`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: index-mxf

後端 fubon_futures_ws.py 訂閱富邦 WS candles channel 時沒帶 timeframe(subscribe 只傳 channel/symbol/afterHours),推的是 1 分 K;而本 hook 不論目前 timeframe 為何都把推來的 candle merge 進序列。tf=5(預設值)時,1m candle 的 date(如 09:03)不等於聚合 bar 的 bucket date(09:00),走 `candle.date > last.date` 的 append 分支 → 每個非 bucket 邊界的新分鐘都會在 5m 圖右緣 append 一根假的 1 分 K,直到 30 秒輪詢覆蓋才修正,然後下一分鐘又再發生。盤中 5m 圖右緣約一半時間顯示錯的 bar,MA5/MA20、VWAP、量副圖也跟著吃進混合週期資料;append 還會觸發 viewRange 右錨平移。

**建議修法**:在 WS merge handler 加 timeframe 守門:tf !== 1 時只把推送的 close/high/low 投影到「最後一根聚合 bar」(更新 close、必要時擴 high/low),不 append 也不整根覆蓋;或後端 broadcast 帶 timeframe、前端比對相符才 merge。

### 7. candles 長度縮水時 viewRange 不會夾回界內,圖表變空白且無法自動恢復

- **位置**:`frontend/src/components/MXFIntradayChart.tsx:61`
- **面向**:正確性 | **驗證信心**:high | finder: index-mxf

init effect 只在 candles.length === 0 或 viewRange === null 時重設 viewRange。但 /api/mxf/candles 回傳長度會縮水:每天 15:00 新夜盤開始時,REST 的 afterhours 從「昨晚整段(~840 根 1m)」換成「剛開始的新夜盤(幾根)」,總長大幅下降;另外 timeframe 切換的過期回應競態(見另一 finding)也會造成長度驟降。此時右錨的 viewRange.startIdx/endIdx 都超出新陣列 → `candles.slice(start, end)` 得到空陣列 → `Math.min(...[])` = Infinity、yMin/yMax 變 ±Infinity → 所有座標 NaN,主圖整片空白只剩格線。推送 effect 因 `candles.length <= prevLen` 直接 return、init effect 因 length≠0 不重設,所以會一直空白到使用者手動滾輪縮放或切 timeframe 才恢復。掛盤整天的頁面每天 15:00 必踩。

**建議修法**:在 init/reset effect 補一個夾界分支:當 viewRange.endIdx > candles.length - 1 時重算(例如重新右錨:endIdx = candles.length - 1、startIdx = max(0, endIdx - 原視窗大小 + 1)),或乾脆 viewRange 越界就設回 null 讓既有初始化邏輯接手。

### 8. 市價單的「閘用估價」可被五檔點價覆寫、或在 refPrice 缺失時殘留任意舊值

- **位置**:`frontend/src/components/OrderTicket.tsx:33`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

市價模式的設計是 price 欄自動帶漲停/跌停當閘用估價(line 53 effect),但有兩條路徑會破壞這個不變量:(1) line 33 的 subscribeOrderTicket callback 沒檢查 isMarket,勾了市價後在五檔點價仍會 setPrice 蓋掉閘價(輸入框 disabled 只是視覺,bus 直接寫 state);(2) api.quote 失敗或尚未返回時(catch 靜默),line 53 effect 因 refPrice==null 直接 return,price 殘留使用者先前手動輸入的值或前一檔的閘價,而 inputOk 在市價模式只檢查 Number(price)>0 仍放行送出。結果:market 單送出的 price(後端金額閘/稽核依據)與確認框的「閘用估價」「預估金額」可能嚴重低估(例:先輸入 5 元再勾市價買 80 元股票,低估 94%),金額安全閘形同失效。真錢面板上這是閘門完整性問題。

**建議修法**:submit() 時若 isMarket,不信任 price state,直接由 refPrice 重新推導 gate price((isBuy?limitUp:limitDown)(refPrice));isMarket && refPrice==null 時 disable 送出鈕並提示等參考價。bus callback 加 isMarket guard(用 ref 或把 isMarket 加進 effect deps 避免 stale closure)。

### 9. doSend 缺少 OrdersList 已修過的回應歸屬檢查,舊回應會關掉重開的確認框、且確認鈕在 sending 中被無聲忽略

- **位置**:`frontend/src/components/OrderTicket.tsx:84`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

OrdersList 在 b9b9ab1 用 latestPending ref 修過「Esc 後重開的新動作不可被舊回應關掉」,但 OrderTicket 的 doSend 在 finally 後無條件 setConfirm(null),且 OrderConfirmDialog 的確認鈕沒有 disabled(busy/sending 根本沒傳進去)。情境:點「確認買進」後(群益 COM 下單可耗時 1-2 秒、dialog 無任何 busy 指示)使用者 Esc 關窗、改價量重新送出開第二個確認框 → 點「確認」被 `if (!confirm || sending) return` 無聲吃掉 → 第一筆回應到達時 setConfirm(null) 把第二個框關掉、下方還顯示第一筆的「✓」訊息 → 使用者誤信第二筆已送出,實際從未送出。真錢面板上漏單/誤信已下單是嚴重行為錯誤。

**建議修法**:比照 OrdersList:用 ref 記錄當前 confirm 物件,回應到達時只在 `latestConfirm.current === myReq` 才 setConfirm(null);把 sending 傳給 OrderConfirmDialog disable 確認鈕並顯示 busy 狀態,sending 期間也應 disable onClose 或至少明示「送出中」。

### 10. roundToTick 先 round 到整數分再 floor/ceil,導致 limitUp/limitDown 在 947 個合法參考價上算錯且超出法定 ±10%

- **位置**:`frontend/src/lib/tick.ts:46`
- **面向**:正確性 | **驗證信心**:high | finder: quote-api

`const cents = Math.round(price * 100)` 假設輸入已對齊到「分」,但 limitUp/limitDown 餵進來的 ref×1.1 / ref×0.9 合法地帶有 0.1 分(deci-cent)尾數:例如 9.05×1.1=9.955、10.45×0.9=9.405。先 Math.round 到整數分等於把本該被 floor 捨去(或 ceil 進位)的半分尾數提前四捨五入,之後的 floor/ceil 形同虛設。用 Node 全掃描 0.01–1000 元所有合法 tick 參考價、對照精確十進位運算:947 個參考價算錯(<10 元 869 個、10–50 元 78 個,後者集中在 .05/.45/.95 等尾數)。實例:limitUp(9.05)=9.96(法定 9.95)、limitUp(5.55)=6.11(法定 6.10)、limitUp(10.45)=11.50(法定 11.45)、limitDown(10.45)=9.40(法定 9.41)。錯誤方向固定落在法定漲跌停區間之外(limitUp 偏高一檔、limitDown 偏低一檔),所以不會以錯價成交(交易所會拒單),但這些值直接餵真錢下單路徑:OrderTicket.tsx 的「漲停/跌停」快捷鈕把它設成委託價、市價單閘用估價(OrderTicket L55、PositionsList L119)、flash-ladder 的 ladder 上下界——對低價股(正是漲停打開策略的主要標的群)按「漲停」送單必遭券商拒絕,搶單時點直接失敗。函式 doc 自述「絕不超過 +10%」,與實際行為矛盾。

**建議修法**:roundToTick 改用 0.1 分(deci-cent)整數運算:`const deci = Math.round(price * 1000); const tickDeci = Math.round(tick * 1000); const units = dir === "down" ? Math.floor(deci / tickDeci) : Math.ceil(deci / tickDeci); return Math.round(units * tickDeci) / 1000;`。ref 為合法 tick 價時 ref×1.1/×0.9 在 deci-cent 下是精確整數,float 雜訊(~1e-12)遠小於 0.5 deci-cent 會被 round 殺掉。已驗證:全掃描 0 mismatch、tick.test.ts 既有測例全過、flash-ladder 傳入的分對齊輸入不受影響。補回歸測例 limitUp(9.05)=9.95、limitUp(10.45)=11.45、limitDown(10.45)=9.41。注意 backend/services/cdp.py 的 limit_up_price(L70 同款先 round 再 floor)是同源 bug,需同步修,否則鎖漲停 latch 對受影響價位永遠不觸發。

### 11. 任何 symbol 的每個 tick 都觸發 Monitor 頁根 re-render,整頁四欄(含真錢下單面板)全量重繪

- **位置**:`frontend/src/hooks/useWatchlistQuotes.ts:25`
- **面向**:React/效能 | **驗證信心**:high | 被 3 個 reviewer 獨立發現 | finder: perf-rerender

useWatchlistQuotes 在 MonitorInner(頁根)被呼叫,subscribeTicks 的 handler 對「每一個」進來的 tick 無條件 setLivePrices(new object):(1) 不過濾 symbols 集合;(2) 價格相同也產生新物件,React 無法 bail out。後端會推所有書籤 union + 監聽 + preview 的 tick,行情尖峰時每秒數十次 setState 打在頁根。由於全專案零 React.memo,每次都連帶重繪:TriggerList(最多 550 列,含 Object.fromEntries(rules)、每列 new Date 解析、整批 sort)、BookmarksPanel 全列表、IntradayChart + 整棵靜態 SVG、QuoteBook、TradingPanel(含閃電下單階梯)。這是真錢下單 UI 的同一條 main thread,快市時點價延遲直接受影響。

**建議修法**:三層修法擇優並行:(1) handler 先查 prev[t.symbol] === t.price 直接 return prev,並用 ref 持有目前 symbols Set 過濾無關 tick;(2) 把 useWatchlistQuotes 下移到唯一消費者 BookmarksPanel 內(quotes prop 從 Monitor 移除),讓 per-tick 重繪侷限在書籤欄;(3) 對 TriggerList/BookmarksPanel/TradingPanel 加 React.memo,搭配 handleSelect 改 useCallback。另注意 useSnapshotCache 每 render 回傳新物件,加 memo 前需先以 [symbolsKey, version] useMemo 其回傳值,否則 prevCloseMap 鏈會破功。

---

## Medium(32 項)

### 12. useBookmarkItems 切換 groupId 時無 stale-response 防護,慢回應會把 A 書籤的 items 蓋到 B 書籤底下

- **位置**:`frontend/src/hooks/useBookmarkItems.ts:17`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: bookmarks-panel

refresh 依 groupId 重建、effect 重跑,但前一個 groupId 的 in-flight 請求沒有取消也沒有 token 檢查:快速從書籤 A 切到 B 時,若 A 的回應晚於 B 到達,setItems 會把 A 的 items 寫回,而且這是最後一次寫入,錯誤狀態會一直留著(不會自我修正)。另外切換瞬間也沒先清空 items(舊 group 的列表會短暫掛在新 group 標題下),且 catch 只 console.warn、items 保持舊值——fetch 失敗時舊 group 的 items 會永久顯示在新 group 名下。

**建議修法**:在 effect 裡用 cancelled flag(cleanup 設 true,resolve 後檢查再 setItems),groupId 變更時先 setItems([]);catch 路徑也清空或設 error state,避免殘留前一個 group 的資料。useAllBookmarkItems 的 refresh 也可以順手加同樣防護。

### 13. 刪除目前選中的書籤後 selectedGroupId 懸空,畫面停留在已刪 group 的 stale items 且 × 按鈕會打 404

- **位置**:`frontend/src/components/BookmarksPanel.tsx:81`
- **面向**:正確性 | **驗證信心**:high | finder: bookmarks-panel

在管理 dialog 刪掉目前選中的 group 後,groups 重抓但 selectedGroupId 沒重設:selectedGroup 變 null,但 singleGroupId 仍是已刪的 id,useBookmarkItems 因 groupId 沒變不會重抓,於是 SingleListView 繼續顯示已刪 group 的舊 items;sidebar 上沒有任何項目呈現選中態。此時 isSystem=false → 每列仍有 × 按鈕,點下去 removeItem 對已刪 group 打 DELETE 會 404,而 removeItem 沒有 catch → unhandled promise rejection,使用者得不到任何反饋。

**建議修法**:加一個 effect:groups 更新後,若 selectedGroupId 不是 ALL_VIEW/MONITOR_VIEW 且不在 groups 裡,setSelectedGroupId(ALL_VIEW)。

### 14. 搜尋 debounce 只清 timer、沒擋 in-flight 回應,晚到的舊結果會蓋掉新結果或在清空輸入後回填

- **位置**:`frontend/src/components/BookmarkEditMode.tsx:35`
- **面向**:正確性 | **驗證信心**:high | finder: bookmarks-panel

effect cleanup 只 clearTimeout;timer 一旦觸發,fetch 完成後的 setResults 沒有任何過期檢查。打「23」→ 請求 A,再打「2330」→ 請求 B,若 A 晚於 B 回來,結果列表顯示的是「23」的結果但輸入框是「2330」。同樣地,清空 query 時 effect 立即 setResults([]),但前一個 in-flight 回應晚到會把結果重新填回去,變成空輸入框下掛著一排結果。

**建議修法**:在 effect 裡宣告 let cancelled = false,cleanup 設 cancelled = true,await 回來後 if (!cancelled) setResults(...);或改用 AbortController 直接取消請求。

### 15. 批次移除用 Promise.all,部分失敗時已刪成功的項目不會反映到 UI(不 refresh、不清 selected)

- **位置**:`frontend/src/components/BookmarkEditMode.tsx:78`
- **面向**:正確性 | **驗證信心**:medium | finder: bookmarks-panel

removeSelected 對每個 symbol 各打一次 DELETE 並用 Promise.all 聚合。任一請求失敗就跳 catch:setSelected(new Set()) 和 onChanged() 都不會執行,但其它 DELETE 可能已在後端成功——列表繼續顯示實際上已刪除的股票、而且它們還保持勾選,使用者重按「移除」會對已刪項目再打 DELETE(404 又進 catch,卡死在不同步狀態直到離開編輯模式)。

**建議修法**:改用 Promise.allSettled,結束後無論成敗都 await onChanged() 重新同步;只把失敗的 symbol 留在 selected 並在 alert 中列出,成功的從 selected 移除。

**驗證者保留意見**:核心指控屬實:BookmarkEditMode.tsx:78 用 Promise.all,任一 DELETE 失敗即跳 catch,setSelected 清空與 onChanged() 都不執行;items 由 parent(BookmarksPanel refreshAfterMutation)只在 mutation 後刷新、無其它自動同步,部分成功時 UI 確會殘留已刪項目且保持勾選。但 finding 的惡化情節被反駁:後端 DELETE 冪等(backend/routes/bookmarks.py:232 + config_store.remove_item 找不到 symbol 默默回 False、照回 204),重按「移除」不會 404,反而全部成功 → 清 selected + onChanged() 重新同步,一次重試即完全復原,「卡死直到離開編輯模式」不成立。且部分失敗需後端(localhost FastAPI)恰在批次中途掛掉,情境邊緣。建議嚴重度由 medium 降為 low;allSettled+必 refresh 的修法仍是合理改進。

### 16. 每次 mutation 後所有 group 的 items 會被全量重抓兩次(顯式 refreshAll + groups identity 變化再觸發 effect)

- **位置**:`frontend/src/hooks/useBookmarkItems.ts:70`
- **面向**:React/效能 | **驗證信心**:high | finder: bookmarks-panel

useAllBookmarkItems 的 refresh 依賴 groups 陣列 identity,而 useBookmarks.refresh 每次都 setGroups 新陣列 → effect 必定重跑一次全量抓取。BookmarksPanel 的 refreshAfterMutation 又同時顯式呼叫 refreshAll(),於是每次編輯操作(加股/移除/移動)會對 N 個 group 各打 2 次 GET items(其中顯式那次還用 stale closure 裡的舊 groups 清單),外加 refreshSingle 第三次重複抓選中的 group。每次 mutation 共 2N+1 個請求,其中約一半是多餘的;byGroup 也會連續 setState 兩次造成多餘 re-render。

**建議修法**:二擇一:(a) refreshAfterMutation 拿掉 refreshAll()(refreshGroups 之後 groups identity 變化的 effect 已涵蓋,但要留註解說明這個耦合);(b) 把 useAllBookmarkItems 的 refresh 依賴改成 group id 串(groups.map(g=>g.id).join(",")),讓 effect 只在 group 集合真的變動時重抓,保留顯式 refreshAll 作為 items 變動的同步點。

### 17. 載入流程吞掉所有錯誤:list() 失敗變 unhandled rejection、items() 失敗讓已收藏書籤顯示未勾

- **位置**:`frontend/src/components/AddToBookmarksDialog.tsx:53`
- **面向**:正確性 | **驗證信心**:high | finder: bookmark-dialogs

load effect 的 async IIFE 只有 try/finally 沒有 catch——api.bookmarks.list() 失敗時是 unhandled promise rejection,loading 被設成 false,dialog 安靜地顯示「空書籤清單」,user 會誤以為自己沒有任何書籤。內層每個 group 的 items() 用 catch {} 吞錯(line 49),失敗的 group 即使實際含該股也顯示未勾。資料面是安全的(submit 用 selected/initial 差集,fetch 失敗的 group 兩邊都不在、不會誤刪;backend removeItem 也是冪等 204),但 UI 呈現的勾選狀態是錯的且毫無提示。

**建議修法**:外層加 catch:設一個 error state 顯示「載入失敗」並停用儲存鈕(或直接 alert + onClose);內層 items() 失敗至少 console.warn,並考慮把該 group 標成不可操作而不是默默顯示未勾。

### 18. 搜尋結果有 out-of-order race:慢的舊請求會覆蓋新請求的結果

- **位置**:`frontend/src/components/SymbolSearch.tsx:16`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: monitor-shell

debounce 的 cleanup 只 clearTimeout,不會取消已發出的 fetch。連續輸入時(例如打「23」停 200ms 又打成「2330」),「23」的請求可能比「2330」的請求晚回來,setResults 會把較新的結果蓋成舊查詢的結果,下拉清單顯示與輸入框不符的選項。另外點選後 setQ("") 雖清空 results,但飛行中的舊回應仍可能 setResults,下次 focus 會閃出舊清單。

**建議修法**:在 timeout callback 內捕捉當下的查詢字串,回應到達時比對 q(或用遞增的 seq ref / AbortController),不符就丟棄:`const cur = q.trim(); ... if (cur !== qRef.current) return; setResults(...)`。

### 19. 三個頁面恆常 mount,隱藏頁的輪詢與訂閱在背景持續打 API

- **位置**:`frontend/src/App.tsx:18`
- **面向**:React/效能 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: monitor-shell

App 用 `hidden` 切頁,三頁同時存活:停留在 Monitor 時,MXFBacktest 的 MXFIntradayChart(useMXFCandles,30s)與 IndexBoard 的兩張 IndexIntradayChart(useIntradayCandles,各 30s)仍持續輪詢後端→富邦 REST;反過來離開 Monitor 時,其 WS、watchlist snapshot、usePreviewSubscribe 的富邦預覽訂閱也都掛著。長時間開著等於每分鐘 4–6 條背景請求外加一條沒人在看的富邦行情訂閱,白耗富邦 rate limit。

**建議修法**:若要保留切頁狀態,可改 lazy keep-alive:首次造訪才 mount(`visited` set),或把 `active` prop 傳進頁面讓輪詢 hook 在 hidden 時暫停(`useIntradayCandles(active ? symbol : null)` 即可借用現有 null 短路)。

### 20. 內嵌 ActiveSignalEditor 開啟時按 Esc 會關掉底層規則 dialog,editor 卻留在畫面上

- **位置**:`frontend/src/components/SignalRulesDialog.tsx:40`
- **面向**:正確性 | **驗證信心**:high | finder: signals

Esc handler 只依 `open` 註冊,不檢查 creating/editing。而 editor 的 render 條件是 `(creating || editing)`,與 open 無關。使用者在 editor 輸入框內按 Esc → onClose 把 Monitor 的 dialogOpen 設 false → 規則 dialog 淡出,但 editor 仍掛著(fixed z-50);editor 關閉後底下的規則列表已經不見了,要重新點開。行為與使用者預期(Esc 先關最上層 modal)相反。

**建議修法**:Esc handler 加 guard:`if (creating || editing) return;`(讓 editor 開啟時 Esc 不關外層),或進一步讓 Esc 先關 editor(setCreating(false); setEditing(null))。effect 依賴需加上 creating/editing。

### 21. TriggerList 無 memo,選中股的每個 tick 都整表重算 + 重新 reconcile 最多 550 列

- **位置**:`frontend/src/components/TriggerList.tsx:43`
- **面向**:React/效能 | **驗證信心**:high | finder: signals

Monitor 把 useIntradayCandles.onTick 接進 useSignalsStream,選中 symbol 的每筆 WS tick 都 setCandles → MonitorInner re-render → TriggerList 重跑:Object.fromEntries、recentRows/historicalRows map(historicalToday 上限 500 筆 + recent 50,每筆 new Date 格式化)、Set 去重、sort,再 reconcile 整個 ul。盤中熱門股 tick 頻率高,這是持續性的無效工作——TriggerList 的 props(historical/recent/rules/symbolNames/prevCloseMap)在 tick 期間參考都沒變。

**建議修法**:把 ruleNameById + combined 的計算包進 useMemo(依賴 historical/recent/rules/symbolNames),並以 React.memo 包 TriggerList(Monitor 端的 handleSelect 若非 useCallback 需補上),tick 造成的 parent render 就會整個跳過。

### 22. prevClose 會被降級 payload 的 null 蓋掉,圖表基準線/漲跌%/紅綠填色整個換口徑

- **位置**:`frontend/src/hooks/useIntradayCandles.ts:21`
- **面向**:正確性 | **驗證信心**:high | finder: intraday-chart

candles 有 resolveCandleUpdate 擋空資料(註解明寫富邦會回降級 payload;useSnapshotCache 註解也記錄過 prev_close=null 的降級回應),但 prevClose 只擋過期、不擋 null:盤中某次輪詢富邦降級回 prev_close=null 時,`setPrevClose(null)` 直接蓋掉已知的昨收。下游 IntradayChart/geometry 全部 fallback 到「今日開盤」當 baseline——漲跌%、±10% 格線、紅綠填色、CDP 可見性過濾(refMin/refMax)瞬間全換基準,顯示錯誤口徑直到下次輪詢成功(≥30 秒)。昨收在盤中不會合法地變成 null,蓋掉純屬倒退。

**建議修法**:改成 `if (s === symbolRef.current && r.prev_close !== null) setPrevClose(r.prev_close);`(symbol 切換時 effect 已負責重設 null,這裡不需要接受 null)。

### 23. 每次 mousemove 都重 render 整張靜態圖:IntradayChartStatic 未 memo、setHover 每次都建新物件

- **位置**:`frontend/src/components/IntradayChart.tsx:187`
- **面向**:React/效能 | **驗證信心**:high | finder: intraday-chart

handleMouseMove 每個 mousemove 都 `setHover({ idx })`——即使 snap 到同一根 candle 也是新物件,必觸發 re-render;而 IntradayChartStatic 沒有 React.memo,包含 ~270 根成交量 rect、polyline、十多個 label 的整棵 SVG 子樹每次滑鼠移動都重建 element 並 reconcile。geometry 已經 useMemo 了,但白做——靜態層還是每次重跑。滑鼠在圖上掃動時這是高頻路徑,且 Monitor 頁還有 WS tick 持續進來疊加 render 壓力。

**建議修法**:三步:(1) `setHover(prev => prev?.idx === bestIdx ? prev : { idx: bestIdx })` 同 idx 直接 bail-out;(2) `export const IntradayChartStatic = memo(function ...)`;(3) 傳入的 `flags` object literal 改 useMemo(deps 為五個 toggle),否則 memo 比對必失敗。

### 24. 切換 timeframe 時飛行中的舊 tf 回應沒有過期守門,會把舊週期資料寫進新狀態

- **位置**:`frontend/src/hooks/useMXFCandles.ts:30`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: index-mxf

fetchCandles 依 timeframe 重建、舊 interval 會清掉,但已發出的舊 tf 請求 resolve 時仍無條件 setState。使用者切 1m→15m 時:舊 1m 回應若晚於切換落地,會把 ~1100 根 1m candles 顯示在標示 15m 的 UI 下;若它比新 15m 回應更晚到(後端有 rate limiter 排隊,延遲變異大),最終狀態就是錯的週期,要等 30 秒輪詢才修正。即使順序正常,中間態也會讓 MXFIntradayChart 以 1m 長度初始化 viewRange,新 15m 資料(僅 ~80 根)到達時走進「長度縮水」路徑直接空白(與 viewRange 夾界 finding 連鎖)。同 codebase 的 useIntradayCandles 已用 resolveCandleUpdate 處理過同型問題,這裡漏了。

**建議修法**:比照 useIntradayCandles 的過期守門:用 ref 記當前 timeframe(或 effect 內 cancelled flag / AbortController),回應落地時比對不符就整筆丟棄。

### 25. onWheel 內 e.preventDefault() 在 React 17+ 是 passive listener,擋不住頁面捲動

- **位置**:`frontend/src/components/MXFIntradayChart.tsx:234`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: index-mxf

React 17 起 wheel 事件在 root 以 passive listener 註冊,synthetic onWheel 裡呼叫 preventDefault() 無效,Chrome 會印「Unable to preventDefault inside passive event listener invocation」。註解寫「always block page scroll on chart」但實際上滾輪縮放時頁面(MXFBacktest 外層 overflow-y-auto)會同時捲動,圖表縮放與頁面捲動疊在一起,且 console 每次滾動都噴錯誤。

**建議修法**:改用原生非 passive listener:svg 掛 ref,在 useEffect 裡 `el.addEventListener("wheel", handler, { passive: false })` 並在 cleanup 移除;handler 內再呼叫 preventDefault 與縮放邏輯,移除 JSX 的 onWheel。

### 26. 輪詢偶發失敗時 error placeholder 直接蓋掉仍可顯示的圖

- **位置**:`frontend/src/components/MXFIntradayChart.tsx:255`
- **面向**:正確性 | **驗證信心**:high | finder: index-mxf

useMXFCandles 失敗時刻意保留舊 candles 只設 error(同 useIntradayCandles 的「失敗不清圖」設計),但元件的 placeholder 判斷是 `loading ? … : error ? …`,只要 error 非 null 就整塊圖換成錯誤文字——即使 candles 還有完整資料。後端重啟或單次 30 秒輪詢逾時,盤中走勢圖就消失 30 秒以上,直到下次輪詢成功;hook 保留資料的用意被元件抵銷。

**建議修法**:error placeholder 加上 `candles.length === 0` 條件才整塊替換;已有資料時繼續畫圖,error 以小型 badge/角落提示呈現。

### 27. dayOpenBaseline 每次 render(含每個 mousemove)對全量 candles 做 O(n) Date 解析

- **位置**:`frontend/src/components/MXFIntradayChart.tsx:167`
- **面向**:React/效能 | **驗證信心**:high | finder: index-mxf

`dayOpenBaseline(candles, new Date())` 沒有 memo,而 hover/drag 的每個 mousemove 都 setState 觸發 re-render。taipeiDateStr 對每根 candle new 兩個 Date + toISOString,1m 日夜盤約 1100 根 → 每次 render ~2200 個 Date 物件 + 1100 次 toISOString,滑鼠掃過圖表時以 60Hz+ 頻率重複,造成無謂 CPU 與 GC 壓力(拖曳平移時同時還在跑大 useMemo,更明顯)。

**建議修法**:用 useMemo 以 [candles] 為依賴快取 baselineOpen(candles 每 30 秒/每分鐘才變,render 時間點的 now 差異不影響結果);或在 dayOpenBaseline 內改成從尾端找/早退出。

### 28. 切換標的不清空委託價,限價單可能帶前一檔的價格送出

- **位置**:`frontend/src/components/OrderTicket.tsx:39`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

selected 變更時 effect 只重置 refPrice 並重抓 quote,price state 原樣保留。在同價位帶的兩檔股票間切換時(例:從 85 元的 A 切到 88 元的 B,price 殘留 85),殘值在漲跌停帶內、交易所不會退單,使用者若沒注意確認框就會以前一檔的價格成交。價位帶差很大時雖會被交易所退單,但快節奏操作下這是真錢面板的常見失誤來源。

**建議修法**:在 selected 變更的 effect 裡同時 setPrice("")(必要時也重置 isMarket),讓使用者必須為新標的重新定價或按快捷鍵。

### 29. PositionCard 損益口徑與 PositionsList 不一致——同一部位兩個分頁顯示差數千元

- **位置**:`frontend/src/components/OrderTicket.tsx:182`
- **面向**:正確性 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: capital-ui

commit 6754a77 把 PositionsList 改成券商口徑(pnl_base 含費稅息基底 + brokerPnl 即時平移,commit 訊息實例:毛損益 -68,250 vs App 淨損益 -73,141),但下單分頁的 PositionCard 仍用 grossPnl(avg_price)+netPnl(env 的 VITE_CAPITAL_FEE_RATE/TAX_RATE 估算)。同一檔部位在「下單」與「庫存」兩個 tab 看到的未實現損益會差數千元(費率折扣、融資利息等都不在前端估算內),交易中對帳會困惑、也容易誤判出場時點。Props 型別把 pos 窄化成 {qty, avg_price, name} 是遷移漏掉的痕跡——TradingPanel 實際傳入的是完整 CapitalPosition。

**建議修法**:PositionCard 的 prop 型別放寬為 CapitalPosition,pnl_base/pnl_base_price 存在時改用 brokerPnl(與 PositionsList 同口徑),缺基底時才退 grossPnl;netPnl(env 費率)那行若無人依賴可一併移除。

### 30. 平倉閘用估價以現價/均價當漲跌停基準,長抱部位斷線時可低估 30% 以上

- **位置**:`frontend/src/components/PositionsList.tsx:119`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

limitUp/limitDown 的語意是「以平盤參考價推當日漲跌停」,ClosePositionDialog 卻用 cur(現價)、缺現價時退 avg_price 當基準。現價基準的偏差約 ±10% 還算可控,但 avg_price 備援在長抱部位完全失真:空單均價 50、現價 80 而行情中斷時,gate=limitUp(50)=55,buy-back 市價單的閘用估價比實際成本低 30%+,後端金額閘審核的數字嚴重失真,註解宣稱的「最保守的金額上限」不成立。OrderTicket 同樣場景是正確地抓 api.quote 的 reference_price。

**建議修法**:開 dialog 時比照 OrderTicket 抓 api.quote(pos.stock_no).reference_price 當 limitUp/limitDown 基準,現價/均價只在 quote 失敗時備援且應在 UI 標示估價可能失真;或至少取 max(cur, avg)(買回)/min(cur, avg)(賣出)讓估價偏保守。

### 31. symbols 每 15 秒換 identity,快照 30s interval 永遠在觸發前被清掉、實際每 15 秒全量重抓並重掛 WS 訂閱

- **位置**:`frontend/src/components/PositionsList.tsx:17`
- **面向**:React/效能 | **驗證信心**:high | finder: capital-ui

useCapitalPositions 每 15 秒 setPositions(新 array,內容相同也換 reference),useMemo deps 是 [positions],所以 symbols 每輪 poll 都是新 array → 兩個以 [symbols] 為 deps 的 effect 每 15 秒 teardown/重建:quotesSnapshot 的 30 秒 setInterval 永遠活不到第一次觸發,實際變成每 15 秒(加上每次 capital_order bus 事件)立刻 load() 一次,WS tick 訂閱也每 15 秒重掛。註解寫的「30 秒刷新」與實際行為不符,對後端 quote snapshot 端點造成兩倍以上的請求量。

**建議修法**:讓 symbols 的 identity 穩定:`const key = positions.map(p=>p.stock_no).join(",")`,再 `useMemo(() => key.split(",").filter(Boolean), [key])`;或在 useCapitalPositions 內容相同時保留舊 array reference(淺比較後才 setPositions)。

### 32. refetch 沒有 stale-response guard,慢的舊回應可蓋掉新快照——debounce 註解宣稱的保證並不成立

- **位置**:`frontend/src/hooks/useCapital.ts:38`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

line 26 註解宣稱 debounce「消除並發 GET 舊回應蓋掉新快照的窗口」,但 debounce 只收斂 bus 連發;mount 時的立即 load()、useCapitalPositions 的 15s interval load、與 bus 觸發的 debounced load 三者仍可並發在途。若先發的請求較慢返回(後端 capital store 查詢可能被 COM 執行緒查詢卡住),晚到的舊回應會覆寫掉較新的委託/部位快照——例如剛刪單成功、回報已把該筆標成不可動作,舊回應一蓋 actionable 又短暫復活。下一輪事件/輪詢會修正,但在真錢面板上委託狀態回退會誤導操作。

**建議修法**:在 hook 內用遞增序號:每次 load 取 `const seq = ++latestSeq`,await 後 `if (seq !== latestSeq) return` 才 setState,只採納最新一次請求的結果。

### 33. status 輪詢失敗永遠保留舊值,後端掛掉後健康燈停在「已連線」、送單鈕維持可按

- **位置**:`frontend/src/hooks/useCapital.ts:16`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

useCapitalStatus 的 catch 是 /* keep */——對單次暫態失敗合理,但後端整個不可達時(uvicorn 掛掉/重啟中)status 永遠停在最後一次的 "ok",TradingPanel 的健康燈持續綠燈顯示「已連線」、ready=true、送單鈕可按。健康燈的存在意義就是反映可下單性,持續錯誤下它變成反指標;使用者送單會在 doSend 才失敗,只得到籠統的「✗ 送單失敗」。

**建議修法**:在 effect 內計連續失敗次數,連續 2-3 次 fetch 失敗即 setStatus("unreachable")(成功時歸零),讓燈號降級、ready 變 false。

### 34. useCapitalOrders 只靠 WS bus 刷新、無輪詢備援,WS 斷線期間委託清單永久 stale

- **位置**:`frontend/src/hooks/useCapital.ts:43`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

useCapitalPositions 有 15s poll + bus 雙保險,useCapitalOrders 只有 mount 時一次 load + capital_order bus。WS 斷線期間(REST 與 WS 是獨立通道,送單仍會成功)送出的單收不到回報事件,委託分頁完全不更新;TradingPanel 的群益健康燈仍是綠的(它反映 backend↔群益,不反映 browser↔backend WS),使用者看不到剛送的委託,容易誤判失敗而重複送單。WS 重連後雖有 backlog 重播補救,但斷線窗口內的盲區在真錢面板不該存在。

**建議修法**:比照 useCapitalPositions 給 useCapitalOrders 加一個保守的 setInterval(load, 15000~30000) 當 WS 失效時的備援;或把 WS status 接進 TradingPanel 一併顯示。

### 35. netPnl 對放空部位把證交稅課在出場(買回)邊,稅基用錯價

- **位置**:`frontend/src/lib/capital-pnl.ts:16`
- **面向**:正確性 | **驗證信心**:high | finder: capital-flash-lib

台股證交稅只課在「賣出」那一邊。多單:進場買無稅、出場賣以 currentPrice 計稅,公式正確。但空單(qty<0;OrderTicket.tsx:183 直接以 pos.qty 呼叫,放空為負是既定口徑)進場是賣出、稅基應為 avgPrice(且已發生),出場買回無稅 — 公式卻一律 tax = shares * currentPrice * taxRate。誤差 = shares × taxRate × (cur − avg),例如 5 張、價差 10 元就差 150 元,且方向性偏誤(空單獲利時高估淨損益)。capital-pnl.test.ts 的 netPnl 案例只覆蓋多單,這條路徑無測試。

**建議修法**:依方向選稅基:const taxBase = qty >= 0 ? currentPrice : avgPrice; const tax = Math.round(shares * taxBase * taxRate);並補一個空單 netPnl 測試(qty 負、驗證稅課在 avgPrice)。

### 36. 平盤參考價只在換標的時抓一次、失敗不重試,且與 useQuoteBook 每秒輪詢同端點重複

- **位置**:`frontend/src/components/FlashPanel.tsx:45`
- **面向**:正確性 | **驗證信心**:high | finder: capital-flash-lib

mount/換標的時的一次性 api.quote() 若剛好失敗(後端短暫逾時/500),catch 吞掉後整個 session refPrice 停在 null:buildLadder 改用「現價 ±10%」當漲跌停夾界,夾界隨現價漂移 — 當日跌幅超過約 0.5% 後,階梯下緣會長出低於真實跌停的價位,且在 ±5% 可點帶內可直接點擊送單(會被券商以價格逾限退單、還累進 failStreak 觸發自動解除武裝);「估」徽章也會誤掛一整天。而 useQuoteBook 對同一 symbol 每秒打同一支 /api/quote/{symbol},回應本來就含 reference_price,這次性 fetch 是重複的資料來源。

**建議修法**:讓 useQuoteBook 把 reference_price 一併放進回傳值(每秒輪詢天然帶重試),FlashPanel 刪掉第 41–47 行的一次性 fetch 改讀 hook — 同時解掉不重試與來源重複兩個問題。

### 37. AbortController 從未接上 fetch——abort 只丟結果不取消請求,且延遲 >1s 時所有回應被永久丟棄

- **位置**:`frontend/src/hooks/useQuoteBook.ts:54`
- **面向**:正確性 | **驗證信心**:high | finder: quote-api

fetchOnce 建立 ctrl 後呼叫 `api.quote(symbol!)`,但 api.quote 不接受 signal、也沒把 ctrl.signal 傳進 fetchJSON 的 init,所以 doc comment 寫的「取消前一個未完成的 request(AbortController)」實際上沒發生:abort 只是讓回來的結果被 `ctrl.signal.aborted` 檢查丟掉,網路請求照常進行。兩個後果:(a) 後端變慢時(本機 FastAPI 被同步 SKCOM COM 呼叫卡住 event loop 是真實場景)每秒疊加一條未完成請求,加重後端負擔;(b) 每次 poll tick 開頭都 `abortRef.current?.abort()`,當單次請求延遲 >1 秒,每個回應抵達時其 controller 必已被下一個 tick abort → 所有回應永遠被丟棄,五檔永遠空白(或停在舊資料),且 error 維持 null,UI 完全沒有失敗訊號。

**建議修法**:api.quote 加 optional signal 參數並傳入 fetchJSON 的 init(fetchJSON 已展開 init,fetch 會接到 signal),讓 abort 真正取消網路請求;同時把「每 tick 先 abort」改成「上一請求 in-flight 時跳過本 tick」(用一個 inFlight flag),或改 setTimeout 自鏈(完成後再排下一次),避免延遲略超過 poll 間隔時回應被系統性丟棄。

### 38. useQuoteBook 多 instance 各自 1Hz 輪詢且隱藏頁不暫停,五檔請求直打富邦、額度會被加倍消耗

- **位置**:`frontend/src/hooks/useQuoteBook.ts:68`
- **面向**:React/效能 | **驗證信心**:high | finder: hooks-effects

後端 /api/quote/{symbol} 每次都即時呼叫 fubon.intraday_quote(routes/quote.py:39,無 cache)。兩個跨檔案因素疊加:(1) Monitor 頁的 QuoteBook 與 FlashPanel 各自呼叫 useQuoteBook(selected),開閃電 tab 時同一 symbol 每秒打 2 次富邦;(2) App.tsx 用 hidden 屬性保留所有頁面 mounted(刻意設計,WS 需常駐),但 useQuoteBook 只檢查 document.hidden(瀏覽器分頁背景),user 切到大盤/MXF 頁時 Monitor 的五檔仍以 1Hz 持續打富邦。富邦 REST 額度是全 app 共用的,被五檔輪詢吃掉會波及 snapshot/CDP/MA 等其他即時查詢,閃電面板的階梯也依賴這份五檔。

**建議修法**:把五檔輪詢提升為 module-level per-symbol 共享 poller(比照 tickBus 模式:同 symbol 多訂閱者共用一條 interval,最後一個退訂才停),順手解掉雙 instance 重複請求;或至少讓 FlashPanel 從 TradingPanel 接收 QuoteBook 已有的資料。頁面級暫停可由 Monitor 傳 active prop 控制。

### 39. 分時圖 candles state 提升到頁根:選中股每個 tick 經 onTick→setCandles 再次全頁重繪

- **位置**:`frontend/src/pages/Monitor.tsx:79`
- **面向**:React/效能 | **驗證信心**:high | finder: perf-rerender

useIntradayCandles(selected) 在 MonitorInner 呼叫,WS tick 經 useSignalsStream 的 onTick 回呼 setCandles(新陣列),state owner 是頁根。即使修好 useWatchlistQuotes 的過濾,選中股(通常是最活躍、tick 最密的那檔)每筆成交仍會讓整頁四欄重繪一次,而實際需要更新的只有第三欄的 IntradayChart header 與走勢線。

**建議修法**:把 useIntradayCandles 連同 IntradayChart 抽成一個自有 state 的欄位元件(props 只收 selected、symbolNames[selected] 等穩定值),或最少將其餘三欄用 React.memo 隔離。useIntradayCandles 可改為內部直接 subscribeTicks(模組 bus 已存在),不必再經 Monitor 把 onTick 穿針引線給 useSignalsStream。

### 40. IntradayChartStatic 無 memo 且 flags 為 inline 新物件:hover 滑鼠每動一下就 reconcile 整棵靜態 SVG

- **位置**:`frontend/src/lib/intraday-chart-svg.tsx:254`
- **面向**:React/效能 | **驗證信心**:high | finder: perf-rerender

IntradayChart 的 hover crosshair 用 setHover(每次 mousemove 觸發),以及頁根每個 tick 的重繪,都會重跑 IntradayChartStatic——最多 270 根 1 分 K 的背景多邊形、走勢 polyline、約 270 支量能 bar、格線與標籤,數百個 SVG 節點全部重建 JSX 再 diff。geometry 已用 useMemo 穩定(非選中股 tick 時不重算),但元件沒包 memo,等於白做;且 IntradayChart.tsx:189 每次 render 都建立新的 flags 物件,就算加了 memo 也會被打破。

**建議修法**:IntradayChartStatic 以 React.memo 匯出(props 中 candles/cdp/camarilla/ma/geometry 都已是穩定 ref);IntradayChart 中把 flags 用 useMemo([showVwap, showCdp, showCamarilla, showVolume, showMa]) 穩定後同時傳給 computeIntradayGeometry 與 IntradayChartStatic。同手法適用 index-intraday-svg 的 IndexIntradayStatic。

### 41. useQuoteBook 每秒無條件 setState 新陣列:五檔沒變也讓 QuoteBook 重繪、FlashPanel 階梯重算並強制 scrollIntoView

- **位置**:`frontend/src/hooks/useQuoteBook.ts:56`
- **面向**:React/效能 | **驗證信心**:high | finder: perf-rerender

每秒輪詢成功後一律 setBids(r.bids ?? [])/setAsks(新陣列 ref),內容相同也不會 bail。下游兩個消費者都在熱路徑:QuoteBook 每秒全量重繪;FlashPanel(真錢閃電下單)的 ladder useMemo 以 bids/asks ref 為 dep,每秒必重跑 buildLadder,且 FlashPanel.tsx:74 的跟隨置中 effect 以 [ladder] 為 dep,連帶每秒(加上每個 tick)執行一次 scrollIntoView 強制同步 layout——即使畫面完全沒變。閃電面板對點擊延遲最敏感,capital 檔案從嚴認定。

**建議修法**:在 useQuoteBook 內快取上一輪結果,bids/asks/漲跌停旗標逐項相等(5+5 筆淺比較)時跳過 setState 保留舊 ref;ladder 的 useMemo 與 scrollIntoView effect 即自動止血。FlashPanel 可再把置中 effect 的 dep 從 ladder 改成 center 價位,只有中心價真的移動才捲動。

### 42. scaleX_compressed 每次呼叫都重建 spans,MXF 圖每根 K 棒/每次 mousemove 重複解析 session 日期

- **位置**:`frontend/src/lib/chart-svg.tsx:45`
- **面向**:React/效能 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: dead-code

scaleX_compressed 與 sessionBoundaries 內部每次呼叫都跑 buildSpans(對所有 sessions new Date 解析 ISO)。MXFIntradayChart 的 sx(line 174)被 CandlestickSeries/LineSeries/MALine/VolumeSubChart 對每根可見 K 棒各呼叫一次,mousemove hit-test(line 218-224)又對全部可見 K 棒線性掃描各呼叫一次 sx — 拖曳/滑動時每個 mouse event 都是 O(candles × sessions) 的 Date 配置與解析,純粹浪費。另 line 381 在 visibleSessions.map 內每個 session 重算一次 sessionBoundaries。

**建議修法**:把 buildSpans 升為 export,在 MXFIntradayChart 用 useMemo 對 (visibleSessions, innerW) 緩存 spans,讓 scaleX/sessionBoundaries 接受預建 spans;或至少把 sx 包進 useMemo 的閉包內快取 spans。

### 43. 閃電分頁開啟時,同一 symbol 有兩個獨立的 1 秒 /api/quote 輪詢

- **位置**:`frontend/src/components/FlashPanel.tsx:19`
- **面向**:React/效能 | **驗證信心**:high | finder: dead-code

Monitor.tsx:248 的 QuoteBook 常駐渲染並以 useQuoteBook(selected) 每秒輪詢;切到閃電分頁後 FlashPanel 又自己 useQuoteBook(selected) 再開一條 1 秒輪詢 — 同一 symbol 同一 endpoint 雙倍打後端(後端是吃富邦 rate limit 的本機 FastAPI)。useQuoteBook 是 per-instance state,沒有像 useSnapshotCache 那樣的 module-level 共用。

**建議修法**:比照 useSnapshotCache 的模式,把 useQuoteBook 改成 module-level 以 symbol 為 key 的共用 poller(refcount 訂閱),多個元件共用同一條輪詢與資料。

---

## Low(50 項)

### 44. loading 狀態被忽略:首載與後端故障時都顯示「所有書籤都還是空的」誤導性空狀態

- **位置**:`frontend/src/components/BookmarksPanel.tsx:271`
- **面向**:正確性 | **驗證信心**:high | finder: bookmarks-panel

useBookmarks/useBookmarkItems/useAllBookmarkItems 都回傳 loading,但 BookmarksPanel 完全沒用。首次 mount(以及父層 bookmarksRefreshKey bump 造成的整棵 remount)時 groups=[]、bySymbolFirst 為空,會先閃一下「所有書籤都還是空的」再跳出資料;若 backend 沒起來,fetch 失敗只進 console.warn(useAllBookmarkItems 更是逐 group .catch 吞成 []),畫面永久停在「都還是空的」——使用者會誤以為資料遺失而不是連線失敗,違反專案的 fail loud 原則。

**建議修法**:loading 時渲染載入中的 placeholder 而非空狀態文字;hooks 失敗時保留 error state 並在面板顯示「載入失敗」與重試入口,至少跟「真的空」區分開。

### 45. MonitorRow 與 ItemRow 近乎整段重複(約 60 行相同的 li markup)

- **位置**:`frontend/src/components/BookmarksPanel.tsx:500`
- **面向**:簡化/死碼 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: bookmarks-panel

兩個 row component 的選中態邊框、hit marker、代號/名稱/價格列、SignalChip 列、× 按鈕的 JSX 與 class 字串完全一致,僅差 pct 來源(resolveDisplayChangePct(quote, item) vs quote?.changePct)與 aria-label 文案。而 resolveDisplayChangePct(quote, {}) 的結果就等於 quote?.changePct ?? null(item 沒有 change_pct 時 fallback 為 null),所以 MonitorRow 可以直接由 ItemRow 取代,不會改變顯示行為。之後任何視覺調整都得改兩處,已經是會漂移的重複。

**建議修法**:刪除 MonitorRow,MonitorListView 改 render ItemRow 並傳 item={{ symbol: it.symbol, name: it.name }}、showRemove、onRemove;aria-label 文案差異可用一個 optional prop 帶入。

### 46. rulesForSymbol 完全忽略 symbol 參數,卻在每個 row render 時各自重算同一個 filter

- **位置**:`frontend/src/components/BookmarksPanel.tsx:33`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: bookmarks-panel

註解已說明 post-refactor 後規則不再分 symbol,函式本體就是 rules.filter(r => r.enabled),但它仍以「per-symbol」的簽名被 ItemRow 和 MonitorRow 每列各呼叫一次——每次 render 對 N 列 × M 條規則重複做同一件事並各配置一個新陣列,簽名上的 _symbol 也持續誤導讀者以為結果與 symbol 有關。

**建議修法**:在 BookmarksPanel 用 useMemo(() => rules.filter(r => r.enabled), [rules]) 算一次 enabledRules 往下傳,刪掉 rulesForSymbol;原本解釋「視覺謊言」的註解搬到 enabledRules 旁。

### 47. useBookmarks.reorder 與 useBookmarkItems.addItems 是無呼叫者的死碼

- **位置**:`frontend/src/hooks/useBookmarks.ts:36`
- **面向**:簡化/死碼 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: bookmarks-panel

全 codebase grep 不到 reorder 的使用處(BookmarkManageDialog 註明「排序留待後續(暫不實作拖拉)」);useBookmarkItems 回傳的 addItems 也沒人用——BookmarkEditMode 的加入流程直接打 api.bookmarks.addItems 再走 onChanged。兩段都是為未來功能預留的 speculative code,留著會讓人誤以為有現役呼叫路徑。

**建議修法**:刪除 useBookmarks 的 reorder 與 useBookmarkItems 的 addItems(連同回傳值欄位);等排序功能真的實作時再加回來。

### 48. dialog 內新增書籤後按「取消」,parent 的書籤列表不會更新

- **位置**:`frontend/src/components/AddToBookmarksDialog.tsx:97`
- **面向**:正確性 | **驗證信心**:high | finder: bookmark-dialogs

handleCreate 立即打 api.bookmarks.create 在 server 建立群組,但只更新 dialog 自己的 groups state。onChanged(觸發 Monitor 的 bookmarksRefreshKey → BookmarksPanel 重抓)只在 submit 成功時呼叫;user 若新增書籤後按「取消」或 Escape 關閉,server 上群組已存在,但 BookmarksPanel sidebar 看不到它,直到下一次其他 mutation 或重整頁面,造成「新增失蹤」的錯覺(再建同名還會撞 409 bookmark_name_taken)。

**建議修法**:handleCreate 成功後也呼叫 onChanged()(它只 bump refresh key、成本低);或改成取消路徑也通知 parent 一次。

### 49. 改名 input 同掛 onBlur 與 Enter,await 期間會雙重提交;失敗時 alert 搶焦點連跳兩次

- **位置**:`frontend/src/components/BookmarkManageDialog.tsx:100`
- **面向**:正確性 | **驗證信心**:high | finder: bookmark-dialogs

submitRename 是 async(await onRename → PATCH + refresh),期間 renamingId 還沒被清成 null。按 Enter 後若焦點移出(user 點別處,或 onRename 失敗時 alert() 搶走焦點觸發 blur),onBlur 會再跑一次 submitRename,用同一個 renamingId 再 PATCH 一次。成功路徑是重複請求+重複 refresh(冪等、只是浪費);失敗路徑則是兩個 alert 連跳,體驗明顯錯誤。

**建議修法**:submitRename 進入時先把 renamingId 存進 local 變數並立刻 setRenamingId(null)(input 卸載、blur 不會再觸發),再 await onRename;或加一個 submitting flag 擋重入。

### 50. Escape listener 依賴不穩定的 onClose,Monitor 每個行情 tick 都重註冊一次

- **位置**:`frontend/src/components/AddToBookmarksDialog.tsx:31`
- **面向**:React/效能 | **驗證信心**:high | finder: bookmark-dialogs

effect 依賴 [onClose],而 Monitor.tsx:268 傳的是 inline arrow(每次 render 新 identity)。MonitorInner 持有 useSignalsStream / useIntradayCandles / useWatchlistQuotes 等 tick 驅動 state,盤中每秒多次 re-render;dialog 開著時 window keydown listener 就以同樣頻率 remove/add。單次成本低,但這是四個 dialog 共用的 pattern,且依賴鏈意圖(「onClose 變了要換 listener」)其實不存在——要的只是最新 callback。

**建議修法**:抽一個 useEscapeKey(onClose) hook:callback 存進 ref(每次 render 更新 ref.current),effect 依賴 [] 只註冊一次;四個 dialog(AddToBookmarks/BookmarkManage/BookmarkNew/MoveCopy)一起換掉。

### 51. 四個 dialog 逐字重複 modal shell、Escape effect、checkbox 列與「新增書籤」流程

- **位置**:`frontend/src/components/AddToBookmarksDialog.tsx:110`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: bookmark-dialogs

重複有三層:(1) backdrop(fixed inset-0 z-20 + blur)+ 置中 panel + 標題列 + × 按鈕 + Escape effect 在 AddToBookmarksDialog / BookmarkManageDialog / BookmarkNewDialog / MoveCopyDialog 四檔幾乎逐字相同;(2) checkbox 群組列(toggle(gid) + Set state + 勾選 span li)在 AddToBookmarksDialog 與 MoveCopyDialog 重複;(3)「新增書籤」的 trim→create→alert 流程在 AddToBookmarksDialog.handleCreate、BookmarkManageDialog.handleCreate、BookmarkNewDialog 三處重複,BookmarkNewDialog 整個檔案功能上是 BookmarkManageDialog 新增列的子集。另外 AddToBookmarksDialog:40 的 setGroups([...systemGroups, ...userGroups]) 重排是死邏輯——render 時 line 105-106 又重新 filter 分組,排序從未被用到。

**建議修法**:抽 ModalShell(backdrop + panel + title + 關閉鈕)與 useEscapeKey hook;checkbox 列抽成 GroupChecklist 元件;新增書籤流程抽共用 helper,並評估 BookmarkNewDialog 是否可整檔移除、改用 manage dialog 的新增列。順手刪掉 AddToBookmarksDialog:40 的無效重排。

### 52. Toolbar grid 第 4 欄 300px 與 Monitor 主 grid 的 380px 不一致,對齊失效

- **位置**:`frontend/src/components/TopToolbar.tsx:52`
- **面向**:正確性 | **驗證信心**:high | finder: monitor-shell

檔頭註解寫「grid 4-col 對齊 main grid」,但 Monitor.tsx 主 grid 是 "300px 460px 1fr 380px"(第 4 欄為 TradingPanel 加寬過),toolbar 仍是 "300px 460px 1fr 300px"。第 3 欄都是 1fr,寬度因此差 80px,搜尋框與分時走勢欄、訊號規則鈕與下單面板欄都對不齊 — 屬主 grid 改寬後的 drift。

**建議修法**:把 toolbar 改成 "300px 460px 1fr 380px",或把 gridTemplateColumns 抽成共用常數讓兩處引用同一份。

### 53. 加入/移除監聽失敗時 promise rejection 無人接,UI 無任何回饋

- **位置**:`frontend/src/pages/Monitor.tsx:242`
- **面向**:正確性 | **驗證信心**:high | finder: monitor-shell

`onAddToMonitor={() => selected && addToMonitor(selected)}` 丟棄了回傳的 promise;useMonitorList 的 add/remove 內部不 catch(`await api...; await refresh()`),API 失敗(後端沒起、404 等)時變成 unhandled rejection,按鈕狀態不變、使用者以為已加入監聽——但訊號引擎實際不會對該股觸發,期待中的訊號永遠不來。

**建議修法**:在 useMonitorList 的 add/remove 內 catch 並寫入既有的 error state(與 refresh 一致),或在 Monitor 的 handler 加 `.catch()` 顯示提示;至少避免 unhandled rejection。

### 54. unknownTriggerSymbols 沒排除監聽清單已有名稱的 symbol,造成多餘的 /api/symbols 請求

- **位置**:`frontend/src/pages/Monitor.tsx:124`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: monitor-shell

過濾條件只看 `!(s in bookmarkSymbolNames)`。但 post-refactor 後訊號只對 monitor_list 內的 symbol 觸發,這些 symbol 的名稱早就在 monitorItems 裡(後端回 name),且 buildSymbolNames 的優先序本來就是監聽名稱蓋過 resolved。結果是:純監聽股每次觸發都會經 useSymbolNames 打一輪用不到的名稱查詢(含 20s retry 機制的維護成本)。

**建議修法**:過濾時一併排除 monitorItems 中已有非 null name 的 symbol,例如先建 `monitorNameSet`,條件改為 `!(s in bookmarkSymbolNames) && !monitorNameSet.has(s)`。

### 55. 用 key 強制 remount BookmarksPanel,重拉全部資料且丟失面板 UI 狀態

- **位置**:`frontend/src/pages/Monitor.tsx:210`
- **面向**:React/效能 | **驗證信心**:high | finder: monitor-shell

AddToBookmarksDialog onChanged 後 bump `bookmarksRefreshKey`,整個 BookmarksPanel unmount/remount:selectedGroupId 重設回「全部」、editMode 退出、groups + 每組 items 全部重抓(N+1 請求)。情境:使用者正停在書籤 X 的檢視,從 chart header 把股票加進 X,面板卻跳回「全部」。

**建議修法**:改為把 refresh 能力暴露給 Monitor(useImperativeHandle / 把 groups+items 的 fetch state 提升到 Monitor 再下傳),onChanged 時只呼叫 refresh,不 remount。

### 56. WatchlistWithChips 是死碼:已被 BookmarksPanel 取代且無任何 import

- **位置**:`frontend/src/components/WatchlistWithChips.tsx:38`
- **面向**:簡化/死碼 | **驗證信心**:high | 被 4 個 reviewer 獨立發現 | finder: monitor-shell

全 src 搜尋只剩 BookmarksPanel 的註解提到它(「取代舊 WatchlistWithChips」「沿用其視覺」),沒有任何元件 import。留著會在之後改 WatchlistQuote / ActiveSignal 型別時持續產生無意義的維護成本,也容易誤導 reviewer 以為仍有兩套自選清單 UI。

**建議修法**:刪除 frontend/src/components/WatchlistWithChips.tsx(SignalChip 仍被 BookmarksPanel 使用,保留)。

### 57. useWatchlist 是死碼:無任何呼叫端

- **位置**:`frontend/src/hooks/useWatchlist.ts:4`
- **面向**:簡化/死碼 | **驗證信心**:high | 被 2 個 reviewer 獨立發現 | finder: monitor-shell

全 src 搜尋沒有任何元件 import useWatchlist;舊單一 watchlist 流程已被 bookmarks(useBookmarks/useBookmarkItems)與 monitor_list(useMonitorList)取代。它還會在 mount 時打 /api/watchlist,若被誤用會造成混淆的雙資料來源。

**建議修法**:刪除 frontend/src/hooks/useWatchlist.ts;若後端 /api/watchlist 路由也已退役,可另開 task 一併清理 api.watchlist surface(api.ts 不在本次範圍)。

### 58. useSidebarState 與 useLocalToggle 重複實作 localStorage boolean,且少了錯誤保護

- **位置**:`frontend/src/hooks/useSidebarState.ts:5`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: monitor-shell

兩個 hook 做同一件事(localStorage 持久化的 boolean state),但編碼不同('1'/'0' vs 'true'/'false')、key 前綴慣例不同(無 tk: 前綴),且 useSidebarState 的 initializer 直接呼叫 localStorage.getItem 沒有 try/catch — 在封鎖第三方儲存/隱私模式等 localStorage 會丟 SecurityError 的環境,首屏直接 throw 白屏;useLocalToggle 已正確處理這個 case。

**建議修法**:讓 useSidebarState 直接以 useLocalToggle 實作(`return useLocalToggle('tk:sidebar:expanded', false)`,接受舊 key 一次性重設,或先讀舊 key 做遷移),同時自然獲得 try/catch 保護。注意 useLocalToggle 的 setter 已支援 functional update,Sidebar 的 `setExpanded(v => !v)` 不需改。

### 59. toggleEnabled / removeRule 無錯誤處理,API 失敗時 UI 無任何回饋

- **位置**:`frontend/src/components/SignalRulesDialog.tsx:46`
- **面向**:正確性 | **驗證信心**:high | finder: signals

兩個 async function 直接 await api 呼叫,沒有 try/catch。後端掛掉或回 4xx/5xx 時變成 unhandled promise rejection,onChanged 不會執行,toggle 開關看起來「點了沒反應」,刪除也一樣無聲失敗。同子系統的 ActiveSignalEditor.save 有 try/catch + error 顯示,這裡是漏洞。

**建議修法**:包 try/catch,失敗時至少 console.warn + 顯示錯誤(可用簡單的 alert 或 dialog 內 error state),維持與 editor 一致的 fail-loud 行為。

### 60. pillsForRule 只數 conditions + window_conditions,preset 策略與 CDP/MA proximity 規則顯示「0 條件」誤導

- **位置**:`frontend/src/components/SignalRulesDialog.tsx:25`
- **面向**:正確性 | **驗證信心**:high | finder: signals

編輯器允許存純 strategy 規則(conditions=[])或純 cdp_proximity / ma_proximity 規則,這些在列表會顯示「AND · 0 條件」,使用者會誤以為規則是空的。strategy 規則連邏輯 AND 都無意義。

**建議修法**:strategy 存在時顯示策略名稱 pill(如「漲停打開碰 CDP」);否則把 cdp_proximity / ma_proximity 各算一條條件再顯示數量。

### 61. baseline fetch 在途時的 WS bump 會被 setCounts(grouped) 整包覆寫掉

- **位置**:`frontend/src/hooks/useTodayHits.ts:30`
- **面向**:正確性 | **驗證信心**:high | finder: signals

mount 時非同步抓 today_counts,期間若 WS 推來訊號,onSignal → bump 先把計數寫進 state;baseline 回來後 `setCounts(grouped)` 是整包取代,不是 merge。若 server snapshot 時間點早於該訊號,該次命中就從 chip 上消失(少 1),要等重新整理才正確。視窗雖小(一次 HTTP roundtrip),但訊號常在開盤瞬間密集觸發、正好與頁面載入重疊。

**建議修法**:改 `setCounts(prev => merge(grouped, prev))`,同 key 取兩邊較大值;或在 baseline 落地前把 bump 暫存,落地後重放。

### 62. 過期 WebSocket 的 onclose 無條件 setStatus("closed"),StrictMode 下會蓋掉新連線的 open 狀態

- **位置**:`frontend/src/hooks/useSignalsStream.ts:134`
- **面向**:正確性 | **驗證信心**:high | finder: signals

main.tsx 有開 React.StrictMode:dev 下 effect 跑兩次 → ws1 建立後立刻被 cleanup close、ws2 接著建立。ws1 的 close event 是非同步到達,常落在 ws2 onopen 之後,此時 ws1 的 stale onclose 先執行 `setStatus("closed")` 才檢查 reconnect flag → 連線明明活著,狀態 badge 卻顯示 closed,且要等下次真正斷線/重連才會修正。reconnect 的擋板(managed.reconnect)有做,但 setStatus 沒擋。

**建議修法**:onclose 開頭先 guard:`if (currentRef.current !== managed) return;`(自己已不是現役連線就整段跳過,setStatus 與 reconnect 一起擋掉)。

### 63. formatTouch 對 role 的查表無 fallback,非預期 role 會 render「第 N 次undefined」

- **位置**:`frontend/src/lib/signal-format.ts:16`
- **面向**:正確性 | **驗證信心**:high | finder: signals

extractTouch 只驗 `typeof obj.role === "string"`,不驗是否屬於 resistance/support/touch;formatTouch 對 LEVEL_ZH 有 `?? t.level` fallback,ROLE_ZH 卻沒有。後端日後加新 role 或資料異常時,觸發列會直接顯示字串 undefined。

**建議修法**:改 `const role = ROLE_ZH[t.role] ?? t.role;`,或在 extractTouch 驗 role 的 enum membership(與 level 的寬鬆策略二擇一,保持一致)。

### 64. CDP 觸發與 MA 觸發整組邏輯 + JSX 近乎逐行重複(~80 行)

- **位置**:`frontend/src/components/ActiveSignalEditor.tsx:106`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: signals

enableCdpProx/disableCdpProx/toggleCdpLevel/updateCdpTolerance(106-133)與 MA 版(135-162)只差 levels 型別與預設 tolerance;JSX 區塊(301-341 vs 343-383)也是同構,只差文案。兩邊的「至少留 1 個」「clamp 0-10」規則要改就得改兩處,已是 drift 溫床。

**建議修法**:抽一個泛型 ProximityEditor<L extends string> 子元件(props: levels 全集、label map、value、onChange、說明文案),CDP/MA 各 render 一次;handler 收斂成一組。

### 65. ALL_CDP_LEVELS / CDP_LEVEL_LABEL 與 ActiveSignalEditor 重複定義,且中文標籤已 drift(中線 vs 中軸)

- **位置**:`frontend/src/components/PresetStrategyFields.tsx:3`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: signals

同一組常數在 PresetStrategyFields.tsx:3-6 與 ActiveSignalEditor.tsx:33-38 各定義一份。標籤已經不一致:兩處 CDP_LEVEL_LABEL 用「CDP 中線」,但同檔 ActiveSignalEditor 的 FIELD_LABEL(line 13)與 signal-format.ts 的 LEVEL_ZH 用「CDP 中軸」——專案先前已正名為「中軸」,這裡是漏網。api.ts 已有 CdpLevel 型別,常數卻散落。

**建議修法**:把 ALL_CDP_LEVELS 與 CDP_LEVEL_LABEL 抽到 lib(緊鄰 api.ts 的 CdpLevel 型別或 signal-format.ts),兩個元件共用,並統一用「CDP 中軸」。

### 66. enabled 用無 setter 的 useState,實為死狀態

- **位置**:`frontend/src/components/ActiveSignalEditor.tsx:70`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: signals

`const [enabled] = useState(initial?.enabled ?? true)` 從未提供 setter,值在元件生命週期內不變,沒必要進 state。

**建議修法**:改成 `const enabled = initial?.enabled ?? true;` 一般常數即可。

### 67. in-flight dedup 跨 hook instance 不通知:被別的 instance 抓進 cache 的 symbol 要等 20 秒 retryTick 才顯示

- **位置**:`frontend/src/hooks/useSnapshotCache.ts:37`
- **面向**:正確性 | **驗證信心**:high | finder: intraday-chart

missing 過濾掉 inFlight 中的 symbol(跨 instance dedup,docstring 宣稱是 feature),但完成時只有發起 fetch 的那個 instance setVersion re-render——等待中的 instance 沒訂閱該 promise,資料已進 module cache 卻不重算 out,要等自己的 20 秒 retry timer 才撿到。目前兩個使用點(useWatchlistQuotes 與 triggerSnapshot)恰好都在 MonitorInner 同一個元件,任一 instance 的 setVersion 會重 render 整個元件、連帶另一個 instance 重讀 cache,所以現在沒有可見症狀——但這是靠呼叫位置巧合成立,hook 自身的 contract 是破的,未來任何放到不同元件的消費者都會吃到最多 20 秒的「—」。

**建議修法**:對「已 inFlight 但不是自己發起」的 symbol 也掛完成通知:`const waiting = symbols.filter(s => inFlight.has(s) && !cache.has(s)); for (const p of new Set(waiting.map(s => inFlight.get(s)!))) p.then(() => setVersion(v => v + 1));`。

### 68. clipPath id 寫死 above-baseline/below-baseline,同頁渲染兩個 instance 會互相吃到對方的 clip

- **位置**:`frontend/src/lib/intraday-chart-svg.tsx:284`
- **面向**:正確性 | **驗證信心**:high | finder: intraday-chart

SVG 的 url(#id) 以整份 document 解析,同頁出現第二個 IntradayChartStatic 時,兩張圖的紅綠填色與主價線會 clip 到「文件中第一個」baselineY——baseline 不同的兩檔股,第二張圖的漲跌紅綠切割位置直接錯。目前 Monitor 只渲染一張、bot 端是獨立 SVG,所以是 latent;但姊妹檔 index-intraday-svg.tsx:116 已經為「並排兩張同頁」加了 idPrefix 參數解過一模一樣的問題,證明這個需求在本專案真實存在,本檔遲早踩到。

**建議修法**:比照 index-intraday-svg 加 `idPrefix?: string` prop(預設空字串保住既有 snapshot),id 改成 `${idPrefix}above-baseline`,或用 React.useId。

### 69. Y 軸範圍只取 close/average,不含 high/low——無漲跌幅限制的標的高低點 marker 會畫出圖外

- **位置**:`frontend/src/lib/intraday-chart-svg.tsx:145`
- **面向**:正確性 | **驗證信心**:high | finder: intraday-chart

priceMin/priceMax 只掃 closes 與 vwaps,但今日高低 marker(第 9 區塊)用的是 candle.high/low。一般股票受 ±10% 漲跌幅保護(high ≤ ref×1.1 ≤ refMax)不會出界;但新上市前五日、權證等無漲跌幅限制的標的,盤中 spike 的 high 可以高於所有 close——此時 scaleY(todayHigh) 算出的 y 小於 padT 甚至為負,marker 圓點與價位 label 被 viewBox 裁掉或疊進 padding 區,使用者看不到正確高點標示。

**建議修法**:priceMin/priceMax 一併納入 high/low:`Math.min(...closes, ...vwaps, ...filteredCandles.map(c => c.low))`(max 同理用 high),或至少對 marker 的 y 做 clamp 到 [padT, CHART_H - padB]。

### 70. baseline(prevClose ?? 首根開盤)同一 fallback 邏輯重複三處,geometry 算了 refPrice 卻不輸出

- **位置**:`frontend/src/lib/intraday-chart-svg.tsx:266`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: intraday-chart

`prevClose ?? filteredCandles[0].open` 出現在 computeIntradayGeometry 的 refPrice(124 行)、IntradayChartStatic 的 baseline(266 行)、IntradayChart.tsx 的 baseline(113 行)三處。geometry 是這三者的單一事實來源(±10% 過濾、格線、填色、header 漲跌全都該用同一個基準),卻沒把它放進 IntradayGeometry 回傳值,消費端各自重算——其中一處改了 fallback 規則(例如改用昨收快照)其他兩處就漂移,而紅綠填色 vs header 漲跌色不一致是使用者會直接看到的 bug。

**建議修法**:IntradayGeometry 增加 `baseline: number` 欄位(= refPrice),IntradayChartStatic 與 IntradayChart 改讀 geometry.baseline,刪掉兩處重算。

### 71. 靜態圖層未 memo,hover 十字線移動時整層 SVG(含 ~270 根量能 bar)每個 mousemove 重建

- **位置**:`frontend/src/components/IndexIntradayChart.tsx:68`
- **面向**:React/效能 | **驗證信心**:high | finder: index-mxf

hover 是 state,mousemove 每動一下就 re-render,IndexIntradayStatic 重新執行:重組 fillPoints 字串、map 出 ~270 個量能 rect 與高低標記等元素讓 React 重新 diff。它的 props(candles、prevClose、geometry(已 useMemo)、idPrefix)在 hover 變化間參考完全穩定,是 React.memo 的理想對象。IndexOverlayChart 的 IndexOverlayStatic 同樣型態(props 只有 memo 過的 geometry)。並排模式兩張圖同頁,滑鼠掃過時 diff 成本加倍。

**建議修法**:把 IndexIntradayStatic 與 IndexOverlayStatic 用 React.memo 包起來(或在元件端 useMemo 包住 <IndexIntradayStatic …/> 元素),hover 變化就只重畫十字線那個 <g>。

### 72. computeNewViewRange 在 candlesLen < MIN_VISIBLE 時回傳 endIdx 越界的 ViewRange

- **位置**:`frontend/src/lib/mxf-chart.ts:51`
- **面向**:正確性 | **驗證信心**:high | finder: index-mxf

newVisible 先 Math.min(maxByPx, candlesLen, …) 再 Math.max(MIN_VISIBLE, …),candlesLen=3 時 newVisible 被抬回 5,endIdx = 4 超出資料範圍,違反 ViewRange 的 invariant。實際追了下游:slice 會夾住、todayHigh/Low 判斷不受影響,且長度成長到 6 時 anchoredRight 檢查(endIdx===prevLen-1=4)碰巧重新成立、右錨追蹤自動恢復,所以目前無使用者可見的錯誤——但這是靠巧合成立,之後任何依賴 endIdx 合法性的程式(例如新的標記、座標計算)都可能踩到。

**建議修法**:最後再用 candlesLen 夾一次:`newVisible = Math.min(candlesLen, Math.max(MIN_VISIBLE, Math.min(maxByPx, newVisible)))`,讓回傳值永遠滿足 endIdx <= candlesLen - 1。

### 73. IndexIntradayStatic 宣告必填的 candles/scale props 實際從未使用

- **位置**:`frontend/src/lib/index-intraday-svg.tsx:114`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: index-mxf

IndexIntradayStaticProps extends IndexChartInput,迫使呼叫端(IndexIntradayChart.tsx:68 傳了 candles={candles})提供 candles 與可選 scale,但元件本體只讀 props.geometry(內含 filteredCandles、fontScale)與 props.prevClose、props.theme。candles/scale 是死參數,誤導讀者以為元件會自己過濾 candles,也讓未來改動容易把「傳進來的 candles」與「geometry.filteredCandles」搞混。

**建議修法**:Props 改成只收實際用到的欄位:{ geometry, prevClose, theme?, idPrefix? },不要 extends IndexChartInput;呼叫端移除 candles={candles}。

### 74. OverlayGeometry.zeroY 是死碼,全 repo 無人使用

- **位置**:`frontend/src/lib/index-overlay-svg.tsx:75`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: index-mxf

zeroY 在 computeOverlayGeometry 計算並放進回傳值與介面,但 IndexOverlayStatic 解構時沒拿(0% 線改由 niceTicks 必含 0 來畫),整個 repo(含 bot)grep 不到其他使用點,只剩 plan 文件裡的舊設計引用。

**建議修法**:從 OverlayGeometry 介面與回傳值移除 zeroY。

### 75. X 軸固定 6 點時間標籤陣列在三處重複

- **位置**:`frontend/src/lib/index-overlay-svg.tsx:113`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: index-mxf

{min:540…810, label:"9:00"…"13:30"} 同一份陣列出現在 index-overlay-svg.tsx:113、index-intraday-svg.tsx:199、intraday-chart-svg.tsx:487。改開收盤標示(例如想加 12:30 或改字串)要記得改三處,容易漂移。

**建議修法**:抽成共用常數(放 intraday-time.ts 或 intraday-chart-svg.tsx export,如 X_AXIS_TICKS),三處引用同一份。

### 76. sessionBoundaries 在同一次 render 內被重複計算多次

- **位置**:`frontend/src/components/MXFIntradayChart.tsx:381`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: index-mxf

render 中 line 370 已為 gap 虛線呼叫一次 sessionBoundaries(visibleSessions, innerW),line 381 又在 visibleSessions.map 的每個元素裡重新呼叫整個函式再取 [i-1],每次都重跑 buildSpans。session 數量小所以不是效能問題,但同值重算多次徒增閱讀負擔。

**建議修法**:render 前(或併入既有 useMemo)算一次 `const boundaries = sessionBoundaries(visibleSessions, innerW)`,兩處共用。

### 77. 圖例迴圈內 indexMeta(s.code)! 是多餘查表,s 本身就是 IndexSymbol

- **位置**:`frontend/src/components/IndexOverlayChart.tsx:36`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: index-mxf

map 的迭代變數 s 來自 INDEX_SYMBOLS,已含 color/short 等欄位,卻再呼叫 indexMeta(s.code) 加非空斷言取回同一個物件,平白引入一個 `!`。

**建議修法**:直接用 s.color,刪掉 meta 查表與非空斷言。

### 78. ClosePositionDialog 持有開窗當下的 pos 快照,確認文案的張數可能與實際平倉量不符

- **位置**:`frontend/src/components/PositionsList.tsx:97`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

closing state 存的是點「平」當下的 CapitalPosition 物件;dialog 開著期間若部位變動(部分成交回報進來 → positions 重抓),確認框仍顯示舊張數的「反向單 賣出 X 張」,而 capitalClosePosition 請求不帶 qty、後端以當下實際部位平倉——使用者確認的數字與實際執行的數字不同。平倉操作恰好常發生在部位正在變動的時刻,真錢確認框顯示與執行不一致值得修。

**建議修法**:render 時以 stock_no 從最新 positions 重新查找:`const live = positions.find(p => p.stock_no === closing.stock_no)`,找不到(已全平)就自動關閉 dialog;dialog 顯示 live 的張數。

### 79. 價格驗證只擋小數位數、不驗 tick 級距,高價股輸入無效檔位要到券商/交易所才被退

- **位置**:`frontend/src/components/OrderTicket.tsx:60`
- **面向**:正確性 | **驗證信心**:high | finder: capital-ui

priceOk 只檢查 `^\d+(\.\d{1,2})?$`,但台股 100-500 元的 tick 是 0.5、500-1000 是 1、1000 以上是 5;輸入 105.13 或 1003 會通過前端驗證與二次確認,送出後才被退單。lib/tick.ts 已有 tickSize/roundToNearestTick 可直接用。OrdersList 的改價輸入(line 35)有同樣的洞。影響只是多一輪失敗往返(交易所會退單、不會錯價成交),故列 low。

**建議修法**:priceOk 補 tick 對齊檢查:`Math.round(p*100) % Math.round(tickSize(p)*100) === 0`;或失焦時用 roundToNearestTick 自動吸附並提示。OrdersList 改價同步套用。

### 80. 三個確認 dialog 重複同一套 modal 殼(Esc listener + backdrop + 置中卡 + prod 警示)

- **位置**:`frontend/src/components/OrderConfirmDialog.tsx:14`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: capital-ui

OrderConfirmDialog、OrdersList 的 ActionConfirm、PositionsList 的 ClosePositionDialog 各自手刻一份完全相同的結構:Escape keydown effect、backdrop(blur + onClick 關閉)、fixed 置中卡片、prod/test 邊框與警示文案。三份已經出現微小漂移(ActionConfirm 有「環境未知」分支、其他兩個沒有;確認鈕 disabled 行為不一),未來再加 dialog(智慧單下輪)會繼續複製。

**建議修法**:抽一個 ModalShell({ prod, title, onClose, children, footer }) 收攏 Esc effect、backdrop、置中卡與環境警示列;三個 dialog 只剩各自的內容列與確認鈕,環境文案與 busy/disabled 行為也順勢統一。

### 81. cancelAll 沒套 cancelling 防重入旗標,且與 cancelAt 重複同一段刪單/計失敗邏輯

- **位置**:`frontend/src/components/FlashPanel.tsx:121`
- **面向**:正確性 | **驗證信心**:high | finder: capital-flash-lib

cancelAt 用 cancelling.current 防連點(註解明寫:第二輪對同批 seq_no 必被拒、失敗 hint 會蓋掉成功訊息),但 cancelAll 既不檢查也不設這個旗標:全刪進行中再點紅方格、或紅方格刪單未返回時按下全刪,會對同批 seq_no 重複送刪單,第二批必遭拒,真錢面板上會顯示「✗ n/m 筆刪單失敗」誤導使用者以為掛單還在。兩個函式的 Promise.allSettled + rejected/!ok 計數五行幾乎逐字複製(113–115 與 123–125)。

**建議修法**:抽一個 cancelMany(targets, okMsg) helper:內部統一持有 cancelling.current 守門與 allSettled 失敗計數,cancelAt 與 cancelAll 都呼叫它 — 一次修掉防重入不對稱與重複碼。

### 82. 換標的不清 hint,前一檔的送單/刪單結果殘留在新標的畫面

- **位置**:`frontend/src/components/FlashPanel.tsx:34`
- **面向**:正確性 | **驗證信心**:high | finder: capital-flash-lib

hint 文案(例「⚡ 買 83.65 × 1:委託成功」「已刪 83.65 的 2 筆掛單」)不含標的代號,切換 selected 後 state 原樣保留,新標的的面板底部仍顯示上一檔的操作結果,在連續切換多檔快速操作的閃電情境容易誤讀成目前標的的回報。

**建議修法**:在 selected 變更的 effect(第 34 行的 setLast(null) 旁)同步 setHint(null);或在 hint 文案固定前綴標的代號。

### 83. 置中 effect 依賴 ladder 陣列 identity,中心價沒動也每秒強制 scrollIntoView

- **位置**:`frontend/src/components/FlashPanel.tsx:74`
- **面向**:React/效能 | **驗證信心**:high | finder: capital-flash-lib

useQuoteBook 每秒輪詢後 setBids/setAsks 一律是新陣列(內容相同也換 identity),ladder 的 useMemo 因 deps 含 bids/asks 而每秒重算出新陣列,effect [ladder, followCenter] 隨之每秒執行 scrollIntoView——即使中心價與列集完全沒變。scrollIntoView 每次呼叫都強制同步 layout,加上每筆 WS tick 也觸發一次,在盤中高頻 tick 時是固定的無效 reflow 開銷。

**建議修法**:改依賴中心價的值而非陣列 identity:const centerPrice = ladder.find(r => r.isCenter)?.price,effect deps 用 [centerPrice, followCenter];若要涵蓋接近漲跌停時上方列數縮減造成的位移,可再加 ladder[0]?.price 進 deps。

### 84. buildLadder 內 myOrders 與 myFills 的聚合是兩份複製的 Map 累加碼

- **位置**:`frontend/src/lib/flash-ladder.ts:98`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: capital-flash-lib

第 92–97 行(myBuy/mySell)與第 98–103 行(fillBuy/fillSell)是同構的五行:建兩個 Map、迴圈依 buySell 選邊、累加 lots。兩處唯一差異是輸入陣列,之後若改聚合規則(例如價位正規化)要記得改兩處。

**建議修法**:抽 function aggregateBySide(lots: MyOrderLot[]): { B: Map<number, number>; S: Map<number, number> },呼叫兩次取代四個 Map 與兩個迴圈。

### 85. buildSymbolNames 讓書籤來源的 null 名稱覆蓋監聽清單/觸發解析已查到的名稱

- **位置**:`frontend/src/lib/symbol-name.ts:34`
- **面向**:正確性 | **驗證信心**:high | finder: quote-api

合併用 `{ ...resolved, ...monitorNames, ...bookmarkNames }`,書籤來源無條件最優先。但 BookmarksPanel 組 name map 時是 `names[sym] = entry.item.name ?? null`(BookmarksPanel.tsx L69),書籤 item 的 name 為 null 時(例如系統「大漲股」書籤 capture 時沒帶名稱)會以 null 進 map,蓋掉 monitorNames / resolved 已查到的真名 → 顯示「—」。且 Monitor.tsx 只對「key 不在 bookmarkSymbolNames」的 symbol 補查名稱(L124 用 `in` 檢查 key 存在即跳過),所以這個 null 不會被 useSymbolNames 補救,會一直停在「—」。doc 自述書籤優先的理由是「使用者明確命名的來源」,null 不是明確命名,覆蓋已知名稱違反該意圖。

**建議修法**:合併時讓高優先來源只在值非 null 時覆蓋:先 spread 全部,再對 bookmarkNames / monitorNames 中值為 null 且低優先來源有非 null 值的 key 回填;或改成依優先序逐 key 取第一個非 null 值(全 null 才留 null)。並補測例:書籤 name=null + 監聽有名 → 取監聽名。

### 86. MXFCandle 與 IntradayCandle 介面欄位完全重複

- **位置**:`frontend/src/lib/api.ts:225`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: quote-api

MXFCandle(L225–233)的七個欄位(date/open/high/low/close/volume/average)與 IntradayCandle(L215–223)完全相同,純粹複製貼上。兩型別將來若有一邊加欄位(例如期貨補 session 標記),另一邊不會跟著動,消費端拿到的型別保證就開始漂移。

**建議修法**:改成 `export type MXFCandle = IntradayCandle;`(保留名稱讓呼叫端語意不變);若預期期貨之後會分岔,至少改 `interface MXFCandle extends IntradayCandle {}` 讓共同欄位單一來源。

### 87. 買/賣兩欄 JSX 鏡像重複,業務規則(市價顯示、點價 guard)各寫一份

- **位置**:`frontend/src/components/QuoteBook.tsx:63`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: quote-api

bids.map(L63–78)與 asks.map(L85–100)是 ~16 行的鏡像複製,差異只有顏色 class、量條靠左/靠右、價量欄順序。其中兩條業務規則被重複:`price > 0` 才 emitOrderTicket 的點價 guard(鎖停市價檔不可帶價下單)、`price === 0 → "市價"` 與 `size > 0 ? 張 : "—"` 的顯示規則。之後改任一規則(例如市價檔也要能點、或量改千分位)要記得改兩處,漏改一邊就是買賣兩側行為不一致——點價直通下單匣,這種漂移值得防。

**建議修法**:抽一個 BookSideRow(或 BookSide)子元件,props 帶 side: "bid" | "ask" 與 level,顏色/對齊由 side 決定,點價 guard 與市價/缺量顯示只寫一份。

### 88. AbortController 從未接到 fetch,「取消前一個 request」的註解與實際行為不符

- **位置**:`frontend/src/hooks/useQuoteBook.ts:51`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: hooks-effects

api.quote(symbol) 不收 AbortSignal(lib/api.ts fetchJSON 雖收 init 但這裡沒傳),所以 abortRef.current?.abort() 並不會取消任何網路請求,ctrl.signal.aborted 只是事後丟棄回應的 stale flag。正確性沒問題(舊回應確實被丟棄),但 L19 文件註解「取消前一個未完成的 request(AbortController)」會誤導後續維護者;且後端慢於 1 秒時請求會持續堆疊,每秒新增一條直打富邦的連線。

**建議修法**:讓 api.quote 接受 options 並把 ctrl.signal 傳進 fetch(fetchJSON 已支援 init),真正取消舊請求;或刪掉 AbortController 改用單純的 generation counter 並修正註解,兩者擇一,不要維持名實不符的中間態。

### 89. onTick 回呼參數與 module-level tickBus 雙軌並存,同一份 tick 分發機制重複兩套

- **位置**:`frontend/src/hooks/useSignalsStream.ts:75`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: perf-rerender

useSignalsStream 同時維護 opts.onTick(經 onTickRef 的 ref 同步 effect、由 Monitor 穿線給 useIntradayCandles)與 tickBus(subscribeTicks,FlashPanel/OrderTicket/PositionsList/useWatchlistQuotes 都用它)。兩套機制語意完全相同,onTick 路徑徒增 Monitor 與 hook 之間的耦合,也是 finding「chart state 提升到頁根」的促成因素。

**建議修法**:刪除 opts.onTick 與 onTickRef 機制,useIntradayCandles 內部直接 subscribeTicks 過濾自身 symbol;onSignal 亦可比照改為 signal bus,讓 useSignalsStream 純粹做連線管理 + bus 發佈。

### 90. api.mxfSymbolActive 與 MXFActiveSymbolResponse 無任何呼叫者

- **位置**:`frontend/src/lib/api.ts:492`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: dead-code

useMXFCandles 只用 api.mxfCandles(後端回應已含 symbol);全 src 與 bot/src grep 都找不到 mxfSymbolActive 或 MXFActiveSymbolResponse 的使用點。

**建議修法**:刪除 api.ts 的 mxfSymbolActive(492-493)與 MXFActiveSymbolResponse interface(235-237)。

### 91. computeVWAP 僅測試引用、HoverCrosshair 完全無人使用

- **位置**:`frontend/src/lib/chart-svg.tsx:75`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: dead-code

computeVWAP 只被 chart-svg.test.ts import;app 內 VWAP 一律用後端 candle.average 畫(MXFIntradayChart.tsx:402 LineSeries field="average"、intraday-chart-svg 直接用 c.average),前端自算 VWAP 的路徑早已不存在。HoverCrosshair(line 272)連測試都沒有引用 — MXFIntradayChart 自己 inline 畫 crosshair(line 462-501)。

**建議修法**:刪除 computeVWAP(含 VWAPInputCandle)與 HoverCrosshair(含 HoverCrosshairProps),並同步移除 chart-svg.test.ts 對 computeVWAP 的測試。

### 92. CandlestickSeries 的 width prop 沒人讀(解構成 _width 即丟棄)

- **位置**:`frontend/src/lib/chart-svg.tsx:125`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: dead-code

CandlestickSeries 解構 `width: _width` 後從未使用(K 棒寬實際由相鄰兩根 X 距離推算,line 137),但唯一呼叫端 MXFIntradayChart.tsx:399 仍傳 width={innerW},誤導讀者以為寬度受此 prop 控制。

**建議修法**:從 CandlestickProps 移除 width,並刪掉 MXFIntradayChart.tsx:399 的 width={innerW}。

### 93. refPrice 抓取與 last-tick 訂閱兩段 effect 在 OrderTicket 與 FlashPanel 逐字重複

- **位置**:`frontend/src/components/FlashPanel.tsx:41`
- **面向**:簡化/死碼 | **驗證信心**:high | finder: dead-code

FlashPanel.tsx:41-47 的「平盤參考價」effect 與 OrderTicket.tsx:38-44 逐字相同(setRefPrice(null) → api.quote → alive guard);FlashPanel.tsx:34-38 的 tick→last 訂閱與 OrderTicket.tsx:175-179(PositionCard)也是同一模式。兩個面板輪流掛載,同 symbol 各自重打 api.quote。這正是專案「無 hook 測試環境就抽 lib/hook」慣例該收斂的重複。

**建議修法**:抽 useRefPrice(symbol: string | null) 與 useLastTick(symbol: string | null) 兩個小 hook 放 hooks/,兩個面板與 PositionCard 共用;useRefPrice 可順便做 module-level 同 symbol cache(平盤價一天一值)。

---

## 駁回項(驗證者判定誤報,6 項)

- `frontend/src/hooks/useCapital.ts:58` — useCapitalPositions 的 15s interval 與 WS 觸發的 debounced load 並發,舊回應可蓋掉成交後的新庫存快照
  - 駁回理由:誤報。finding 的核心機制不成立:(1) GET /api/capital/positions 與 /orders(backend/routes/capital.py:35-55)只讀 in-memory CapitalStore(capital_store.py:215-217,lock 下複製 dict),完全不經 SKCOM COM——「後端 capital 查詢走 COM 串行、回應延遲 >200ms」是錯的;COM 串行只存在於 COM 執行緒對券商的查詢,不在 HTTP 路徑。(2) Handler 從讀 store 到 return 零 await、單 worker uvicorn(start.ps1:35)+ localhost:請求是「處理時」讀快取,被處理得晚就讀到更新的資料;要回傳舊內容必然在 store 寫入前就讀完並於 ~1ms 內寫出 socket,而 debounced 新請求最早在 WS 事件(寫入後才 broadcast)+200ms 才發出——舊回應比新請求發出還早 ≥200ms 上路,loopback/vite proxy 無法倒置送達順序。(3) 庫存真正的新快照流程是成交→_mark_balance_dirty(2s)→COM 重查→set_positions 後 broadcast capital_position(capital_client.py:112-117),前端把它映射到同一 bus 再觸發 debounced refetch(useSignalsStream.ts:126-128),該 refetch 嚴格讀到寫入後資料;orders 亦是 apply_reply 先寫 store 才 broadcast(capital_client.py:84-91)。任何能造成 staleness 的 store 寫入都伴隨事件再觸發一次校正 refetch,即使微秒級理論視窗成立也 ~200ms 自癒,宣稱的「stale 最長 15 秒」「要等下一個 capital_order 才修復」不可能發生。遞增序號 guard 作為通用防禦無害,但此 finding 描述的具體 bug 機制與影響在此架構下均不存在。
- `frontend/src/hooks/usePreviewSubscribe.ts:27` — preview POST 不序列化,回應亂序時後端 preview owner 停在舊 symbol,新選股收不到即時 tick
  - 駁回理由:誤報。finding 假設後端併發處理導致完成順序反轉,但 backend/routes/preview.py:42 整個 handler 包在模組級 asyncio.Lock 內,富邦真訂閱(fubon_ws.py:100 await asyncio.to_thread)在鎖內完整 await 完才回應——完成順序嚴格等於到達順序(asyncio.Lock waiter FIFO),富邦訂閱的數百 ms 耗時只會讓後到請求排隊,不可能讓 A 比 B 晚完成而覆蓋。start.ps1:35 確認單 worker uvicorn 跑 127.0.0.1,模組級鎖有效;前端兩次 POST 由連續 render 的 effect 依序發出,到 localhost 的到達順序反轉只剩次毫秒 TCP 競態,且即使發生,後端 unsub/sub 用自己的 _current_preview(preview.py:49-58)而非前端認知,使用者下次任何選股變動即自動修正。retry 只靠 deps 變動是 usePreviewSubscribe.ts:29 註解自承的已知取捨,finding 也承認,不構成 medium bug。reviewer 未讀後端 preview route,該鎖正是為此情境設計。
- `frontend/src/pages/Monitor.tsx:191` — 觸發歷史標題計數未去重,與 TriggerList 實際顯示筆數可能不符
  - 駁回理由:finding 宣稱的症狀(標題 historicalToday.length+recent.length 大於 TriggerList 去重後筆數)在實務上不可能發生,因為去重 key `symbol|ruleName|isoTime` 跨來源永遠撞不上:WS 推送的 triggered_at 是 signal_engine.py:660 _fanout 內的 datetime.now().isoformat()(微秒精度),而寫入 JSONL 的 row 不帶 triggered_at(signal_engine.py:673-679),由 signals_log.py:40 setdefault 在 append 時另呼叫一次 now()——兩次呼叫之間還隔著 await broadcast(),微秒級必不相同;signals_history 路由與前端 api.ts 全程原樣透傳、無秒級截斷,後端 WS 也無 backlog 重播。因此即使 race 窗口內同一訊號同時進入 recent 與 historicalToday,isoTime 字串不同、去重不觸發,combined.length 恆等於兩邊長度之和,標題與清單永遠一致。cap 50 也只是 recent 掉舊筆時標題與清單同步減少,仍一致。附帶觀察:真正的潛在問題其實相反——race 窗口內同一訊號會以兩個不同時間戳重複渲染兩行(該去重對其防禦目的無效),但那是症狀不同的另一個 finding(重複顯示,非計數不符),且當下標題數字仍與清單筆數相符。
- `frontend/src/components/TriggerList.tsx:74` — recent/historical 去重 key 用 ruleName 而非 active_signal_id,規則改名或同名時去重失效
  - 駁回理由:機制誤診。finding 隱含的前提是「不改規則名時,recent 與 historical 的去重能成功配對」,但讀後端可證此前提不成立:signal_engine.py 的 _fanout(line 652-679)廣播給 WS 的 triggered_at 是 line 660 的 datetime.now(timezone.utc).isoformat(),而寫入 signals_log 的 row 並未帶 triggered_at,由 SignalsLog.append(signals_log.py:40)的 setdefault 另外再呼叫一次 datetime.now() 生成——同一筆觸發在兩個來源的 isoTime 是兩次獨立時鐘取樣(含微秒),字串幾乎必然不相等。因此 TriggerList.tsx:74 的去重 key 中 isoTime 分量已使跨來源去重永遠 miss,改名與否毫無差別;「同名規則被誤合併」更需要兩筆 row 撞同一微秒,實務不可能。建議修法 `${symbol}|${active_signal_id}|${isoTime}` 保留 isoTime,跨來源一樣永遠對不上,修了等於沒修。註:確實存在一個相鄰的真問題——跨來源去重是死碼,訊號落在 WS 連上後、history fetch 回應前的窗口會無條件出現重複列(非低機率的改名情境),正確修法在後端讓 _fanout 把 data["triggered_at"] 傳給 signal writer 使兩邊共用同一時間戳;但這與本 finding 的診斷與修法皆不同,應作為獨立 finding 另報。
- `frontend/src/components/TriggerList.tsx:64` — trigger_price 為 null 的歷史列顯示 0.00 並算出 -100.00% 紅字
  - 駁回理由:顯示機制描述正確(null→0.00→-100%紅字),但前提「後端寫入 null」在現行系統中不可能發生:(1) signals_log 唯一寫入點 backend/services/signal_engine.py:661,676 固定寫 tick.price,而 Tick.price 是非 optional float(ring_buffer.py:22),生產環境唯一建構處 fubon_ws.py:202 有 `price is None: return` 防護加 float() 強轉;(2) 實測本機 backend/data/signals_log.jsonl 全部 1831 筆,null 與缺 key 均為 0;(3) 無其他注入路徑——config_io 匯出匯入不含 signals_log,Supabase 一次性遷移已完成且資料乾淨(遷移腳本若重跑會因檔案存在而拒絕)。前端 `number | null` 型別是 Supabase 舊 schema(0004_realtime_signals.sql:21 nullable numeric)的遺留,與實際 runtime 不變量不符。殘餘風險僅剩手動竄改 JSONL 檔,屬型別衛生(把型別收緊為 number)的 nit,非會發生的 bug。
- `frontend/src/lib/intraday-chart-svg.tsx:82` — formatVolume 邊界:999,500–999,999 顯示成「1000K」而非「1.0M」
  - 駁回理由:誤報——情境實務上不可能發生。finding 的數學沒錯(formatVolume(999500) 確實輸出 "1000K"),但其前提「重量級股的分鐘量達百萬股很常見」基於錯誤的單位假設:富邦 intraday candles 官方文件明載 volume 欄位「整股:成交張數」(backend/routes/candles.py 是純 proxy,未轉換單位),所以餵進 formatVolume 的是每分鐘「張」數。兩個呼叫處(intraday-chart-svg.tsx:464 取分鐘 K 最大值、IntradayChart.tsx:219 hover 單根分鐘 K)都沒有日累積加總。要踩到 999,500–999,999 邊界等於單一個股一分鐘成交近 100 萬張(約 10 億股)——台股單股全日歷史天量也僅約 150 萬張,一分鐘百萬張物理上不可能;以張為單位,連 v>=1_000_000 的 M 分支都幾乎是不可達碼。唯一理論縫隙是興櫃股(volume 以股計)極端情況下分鐘量可能落入這 500 股寬窗口,但本專案實際監控上市櫃個股,且即便觸發,「1000K」與「1.0M」數值等價,僅是瞬時 hover label 的單位呈現美觀問題,不構成 bug。

## 覆蓋範圍與 reviewer 備註

- bookmarks-panel:四個目標檔案均已完整讀過,並讀了 context:lib/api.ts 的 bookmarks/monitorList 區段、pages/Monitor.tsx(onItemsChanged 接收端)、hooks/useMonitorList.tsx、hooks/useWatchlistQuotes.ts、hooks/useSnapshotCache.ts、components/MoveCopyDialog.tsx、BookmarkNewDialog.tsx、BookmarkManageDialog.tsx(後三者僅作上下文,問題不報)。兩個刻意未報的點:(1) BookmarkEditMode 第 62 行 addStock 的 1.4s setTimeout 沒在 unmount 時清——React 18 對 unmounted component 的 setState 是無警告 no-op,無實害;(2) header 總數(groups count 加總)與「全部」計數(去重 size)在同檔多書籤時不一致——語意可解讀為「總項目數 vs 去重檔數」,無法確證違反設計意圖。BookmarksPanel 的 onItemsChanged effect 與 useAllBookmarkItems 的 bySymbolFirst memo 鏈已驗證不會形成 setState 迴圈(parent callback 為穩定 useCallback,且 useSnapshotCache 以 symbols.join(\",\") 為 key,identity churn 不會引發重複訂閱)。本組檔案與群益(capital)下單面板無關,嚴重度按一般前端標準認定。
- bookmark-dialogs:五個目標檔案全部完整讀過;另讀了呼叫端(Monitor.tsx、BookmarksPanel.tsx、BookmarkEditMode.tsx)、hooks(useBookmarks、useMonitorList)與後端 routes(bookmarks.py、config_io.py)作上下文,用以確認 finding 的實際影響(如 removeItem 冪等性、import 後端確實套用)。本組檔案不含 capital 下單相關程式碼,無真錢下單風險的 finding。兩個刻意未列的候選:(1) ConfigIODialog:24 在 a.click() 後同步 revokeObjectURL——舊版 Firefox/Safari 可能中斷下載,但現代 Chromium(本機環境)是同步 deref、實際不會壞,屬防禦性建議而非 bug;(2) AddToBookmarksDialog 的 symbol prop 變更競態(effect 無 abort)——overlay 蓋住全畫面,實際無法在 dialog 開啟時切換 symbol,判定為猜測性、不報。
- monitor-shell:範圍內 13 檔全部完整讀過(App.tsx、main.tsx、Monitor.tsx、Sidebar.tsx、TopToolbar.tsx、WatchlistWithChips.tsx、SymbolSearch.tsx、useMonitorList.tsx、useSidebarState.ts、useWatchlist.ts、useWatchlistQuotes.ts、useSymbolNames.ts、useLocalToggle.ts)。另讀了 useSignalsStream、useSnapshotCache、usePreviewSubscribe、useIntradayCandles、useTodayHits、BookmarksPanel、TriggerList、IndexBoard/MXFBacktest/IndexIntradayChart、lib/api、lib/symbol-name 與後端 market_cache.search 作為上下文,只報範圍內檔案的問題。已查證後排除的疑慮:(1) useSymbolNames 以 limit=5 查精確名稱——後端 search 依 symbol 升冪排序、精確碼必排在同前綴的加長碼之前,不會落在 top-5 外;(2) useSymbolNames/useSnapshotCache 的 inFlight 註冊時序——JS 單執行緒下 effect 同步段先完成,無 race;(3) useMonitorList 的 context value 已 useMemo(註解記載過去的無窮迴圈教訓);(4) TopToolbar「連線中」配色 text-bear=綠=正常,屬刻意。main.tsx 與 Sidebar.tsx 無發現。capital/TradingPanel 不在本組範圍,未審。
- signals:範圍內 9 個檔案全部完整讀過。useActiveSignals.ts 無 finding(fail-soft refresh 模式合理);SignalChip.tsx 無 finding(toSuperscript 的 n<10 邊界由 hit guard 保護)。校準時讀過的上下文:lib/api.ts(型別)、pages/Monitor.tsx(呼叫端,historicalToday limit 500、StrictMode 開啟於 main.tsx)、hooks/useIntradayCandles.ts(確認 tick→re-render 鏈)、backend/models/condition.py(確認 strategy 優先於 conditions,支撐 high finding;cooldown_seconds 有 ge=60 後端驗證,故前端 cooldown 未 clamp 不列為 finding——失敗會以 422 錯誤浮現)。Monitor.tsx 本身的問題(如 historicalToday 只在 mount 抓一次)不在範圍內未報。本組無 capital 下單檔案,severity 採一般標準。
- intraday-chart:範圍內 9 檔全數完整讀過。無 finding 的檔案與理由:intraday-time.ts(minuteOfDay 對無效日期回 NaN 會被 filteredCandles 過濾,安全)、intraday-candle-update.ts(純守門函式,邏輯正確)、chart-labels.ts(pass-2 回彈理論上可把 label 推出 yRange[0] 上界,但本專案 label 上限 12 個 × 20px 間距遠小於 560px 圖高,實際不可達,故不報)。另外追查過但判定不報的:usePreviewSubscribe 在 StrictMode 雙重 mount 下的 ref 殘留問題(Monitor 初始 selected=null,desired=null 時序無害,實際追過 mount 順序確認)、useIntradayCandles onTick 在 commit 與 effect 之間的微小 race(視覺上不可見,隨即被清空)、CandlestickSeries 單根 candle 回 null(需兩點估寬,屬防禦設計且 MXF bootstrap 後幾乎不發生)。佐證:全 app 無 ErrorBoundary(grep 確認),故 IntradayChart hover 越界是真白屏;MXFIntradayChart 與 index-intraday-svg 分別已修過同型的 hover 越界與 clipPath id 衝突,證明兩個 finding 的場景在本專案真實存在。
- index-mxf:10 個 in-scope 檔案全部完整讀過。IndexBoard.tsx、MXFBacktest.tsx、index-symbols.ts 無可辯護的 finding。為驗證跨檔行為另讀了 context:useIntradayCandles、useSignalsStream(subscribeMxfCandles)、chart-svg、intraday-chart-svg、intraday-time、api.ts、useLocalToggle,以及後端 routes/mxf.py、services/fubon_futures_ws.py、services/fubon_futures.py(用來確認 WS 推送為不帶 timeframe 的 1 分 K、REST 回傳為當日日+夜盤合併,支撐前兩個 high finding);context 檔案本身的問題未報。此子系統不含 capital 下單檔案,皆為行情顯示,severity 按一般標準認定。另曾追過幾個疑點但判定不報:dayOpenBaseline 週末凌晨回 null 屬文件化設計、overlay 圖 hover 空白區仍畫十字線僅屬外觀、useMXFCandles 將 symbol 釘在首次解析結果疑似刻意避免盤中換月。
- capital-ui:六個 scope 檔案全部完整讀過,並讀了上下文:lib/api.ts(capital 端點與型別)、lib/capital-pnl.ts、lib/capital-orders.ts、lib/tick.ts、lib/qty-quick.ts、lib/capital-labels.ts、hooks/useSignalsStream.ts、FlashPanel.tsx,且用 git 驗證 6754a77 損益口徑遷移只改了 PositionsList(支持 PositionCard 口徑不一致的 finding)。範圍外觀察(不列入 findings、僅供參考):(1) lib/capital-pnl.ts 的 netPnl 對空單把證交稅課在出場價(買回),台股證交稅應課在賣出端(空單=進場),差額極小但口徑不精確——該檔不在 review 範圍;(2) FlashPanel 與 OrderTicket 各自抓一次 api.quote 的 reference_price,屬可接受的重複。TradingPanel 本身較薄,主要問題都落在其子件與 hooks;委託 tab 徽章計入全部今日委託(含已成/已刪)屬產品取捨,未列 finding。
- capital-flash-lib:七個指定檔案全數完整讀過;flash-arm.ts、qty-quick.ts、capital-labels.ts 與 capital-orders.ts 讀完無可辯護的 finding(capital-orders 的 FAILED 標籤集合為純顯示用途且有註解說明 actionable 由後端決定,不算前端抄狀態表)。另交叉讀了上下文:tick.ts、useQuoteBook.ts、useSignalsStream.ts、PositionsList.tsx、OrderTicket.tsx 與 flash-ladder/capital-pnl 測試。逐項驗證過但判定非問題、不報:階梯價位與後端回報價的浮點相等性(雙方都是正確捨入的 cents/100 double,=== 成立)、stepUp/stepDown 跨級距與漲跌停夾界、progScroll 旗標與 scroll 事件在 HTML rendering steps 的先後順序(scroll 事件先於 rAF,旗標時序正確)、武裝狀態機所有解除路徑與 failStreak 重置、同格 500ms 防抖與「連點繼續加」的取捨(有註解、屬刻意設計)、clickPrice 閉包內 arm/ready 的毫秒級 stale 視窗(React 事件閉包固有、視窗極小)。未發現 critical/high 等級問題——送單路徑的價格對齊、夾界、防抖、武裝守門經查皆正確。
- quote-api:七個指定檔案全部完整讀過(QuoteBook.tsx、useQuoteBook.ts、quote-book-svg.tsx、quote-display.ts、tick.ts、symbol-name.ts、api.ts),並讀了 caller/測試作上下文(OrderTicket.tsx、PositionsList.tsx、flash-ladder.ts、BookmarksPanel.tsx、Monitor.tsx、useSymbolNames.ts、tick.test.ts、symbol-name.test.ts、quote-book-svg.test.ts、useSignalsStream.ts、intraday-chart-svg.tsx)。tick.ts finding 用 Node 腳本全掃描 0.01–1000 元所有合法 tick 參考價、對照精確十進位運算驗證(947 mismatch),建議修法也以同腳本驗證 0 mismatch 且既有測例全過。範圍外但重要:backend/services/cdp.py 的 limit_up_price(L70)是同款「先 round 到分再 floor」寫法,與 tick.ts 同源 bug——它供鎖漲停 latch 偵測,受影響價位的漲停打開策略會永遠不觸發,建議與前端同步修。quote-display.ts 無問題(契約清楚、測試釘住 ?? 語意)。刻意不報的邊界項:web 版 toFixed(2) 與 svg 版 formatTickPrice 顯示位數不一致(500 元以上股 web 顯示 .00,非錯誤)、QuoteBook 每秒固定 re-render(1Hz 小元件,成本可忽略)、quote-book-svg 鎖漲停+鎖跌停 badge 同座標重疊(兩 flag 實務互斥)。
- hooks-effects:技術棧:React 18.3 + Vite 5 + TS 5.5,無 router(App.tsx 以 hidden 屬性保留三頁常駐)。覆蓋範圍:hooks/ 全部 17 檔逐行讀完;含 useEffect/timer/listener 的元件(FlashPanel、OrderTicket、OrdersList、PositionsList、TradingPanel、IntradayChart、MXFIntradayChart、QuoteBook、BookmarksPanel、BookmarkEditMode、SymbolSearch、TopToolbar、各 dialog、IndexBoard/IndexIntradayChart/IndexOverlayChart、Monitor、App)讀完;並交叉驗證後端 routes/quote.py、routes/mxf.py、services/fubon_futures_ws.py 以確立兩個跨層 finding 的事實基礎。未深讀:lib/ 純函式與 *-svg 模組(無 effect,且依指示測試檔不在範圍)、TriggerList/ActiveSignalEditor/ConfigIODialog/MXFBacktest/Sidebar/SignalChip/PresetStrategyFields(grep 確認無 useEffect/timer/listener,僅瀏覽)。檢查過但刻意不報的:useSignalsStream 的重連/清理(managed flag 設計正確,無洩漏)、各 dialog 的 Escape keydown cleanup(全數正確)、useSnapshotCache/useSymbolNames 的「in-flight dedup 不通知其他 instance」問題(兩個 instance 都掛在 MonitorInner,fetch 完成的 version bump 會重渲染整個 host,目前用法下被完全遮蔽,屬未來風險不列 finding)、useTodayHits baseline 與 WS bump 的覆寫窗口(毫秒級且只差 1 次計數)、BookmarkEditMode 的 1.4s highlight setTimeout 未清(React 18 unmount 後 setState 為 no-op,無實害)。FlashPanel/OrderTicket/OrdersList 的下單路徑(武裝、防抖、二次確認、busy guard)逐一檢過,未發現會造成錯誤下單的 effect/closure 競態。
- perf-rerender:範圍與方法:先確認技術棧(React 18.3 + Vite,無狀態庫、零 React.memo),再沿 tick 熱路徑追:useSignalsStream(單一 WS + 4 個 module-level EventTarget bus)→ 各訂閱者 state 落點 → 重繪半徑。已深讀:Monitor.tsx、App.tsx、useSignalsStream/useIntradayCandles/useWatchlistQuotes/useQuoteBook/useSnapshotCache/useSymbolNames/useTodayHits/usePreviewSubscribe/useMonitorList/useCapital/useMXFCandles/useBookmarkItems、TradingPanel/FlashPanel/OrderTicket/OrdersList/PositionsList(capital 全套)、TriggerList/BookmarksPanel/QuoteBook、IntradayChart/MXFIntradayChart/IndexIntradayChart/IndexOverlayChart/IndexBoard/MXFBacktest、intraday-chart-svg.tsx 前 360 行,並查後端 fubon_futures_ws.py 確認 mxf_candle 為逐筆 broadcast。正面確認過、無問題故未報:MonitorListContext value 已 useMemo;FlashPanel/OrderTicket/PositionsList 的 tick 訂閱均有 symbol 過濾(setState 同值會被 React bail,PositionsList 還額外做了同價 bail);useCapital 的 capital_order bus 有 200ms trailing debounce;useSnapshotCache/useSymbolNames 的 module cache + in-flight dedup 設計正確;OrdersList key=seq_no、BookmarksPanel 各列 key 正常。略過未逐行讀:對話框類(AddToBookmarksDialog、BookmarkManageDialog、BookmarkNewDialog、BookmarkEditMode、MoveCopyDialog、ConfigIODialog、SignalRulesDialog、ActiveSignalEditor、PresetStrategyFields)、TopToolbar/Sidebar/SymbolSearch/SignalChip(互動開啟或低頻,不在 tick 熱路徑)、lib 純函式細節(chart-svg.tsx 360 行後、index-*-svg、capital-pnl/orders/labels、flash-ladder/arm、qty-quick、tick)——這些有單檔 reviewer 與 .test.ts 覆蓋。另:useMXFCandles 將 WS 推來的 1 分 K 併入非 1 分 timeframe 的正確性疑慮屬 MXF 單模組議題(有專責 handoff 文件),未列入本橫切面報告。
- dead-code:查證方法:先列出全部 named export(約 200 個),再以 import 語句全量比對 + 對每個可疑者逐一 grep 全 src 與 bot/src 確認。重要前提:bot/src 透過 ../../frontend/src/lib 相對路徑 import 前端 lib(index-symbols、intraday-time、index-intraday-svg、intraday-chart-svg、quote-book-svg、tick、api 型別),所以 isIndexCode/indexName/resolveIndexAlias/fmtIndex/fmtIndexVol/QuoteBookSvg 等「前端內看似無人用」的 export 實際有 bot 消費者,均未列為死碼 — 後續清理死碼時務必同樣檢查 bot/src。未列入的邊界項:api.ts 的 ALL_OPERATORS/CdpLevel/Scope/CapitalCloseReq 等只有同檔引用、無外部 import(僅 export 關鍵字多餘,非可刪實體);niceTicks/TICK_FRESH_MS 同檔+測試使用;index-intraday-svg 與 intraday-chart-svg 的 baseline clip 填色邏輯確有重複,但檔頭註解明示是刻意精簡 fork(指數無 tick/average 欄位),且兩者都被 bot resvg 渲染共用,合併的視覺回歸風險大於收益,故不報。覆蓋度:capital 全部元件與 lib、三套 chart lib、Monitor/BookmarksPanel/TriggerList/MXFIntradayChart/IndexIntradayChart、全部 hooks 已深讀;ActiveSignalEditor、SymbolSearch、TopToolbar、Sidebar、SignalChip、ConfigIODialog、MoveCopyDialog、BookmarkNewDialog/BookmarkManageDialog/AddToBookmarksDialog/BookmarkEditMode、IndexOverlayChart、IndexBoard、PresetStrategyFields 僅做 export/props 使用面掃描(無未用 export/props),未逐行深讀其內部邏輯。
