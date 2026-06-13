# 後端全規模 Review 報告(2026-06-11)

Workflow:8 子系統 + 6 橫切面 finder 並行 → 去重 → 每 finding 一個對抗驗證者。
統計:原始 156 → 去重 115 → 確認 103 / 駁回 12 / 未驗證 0。

## Critical(2 項)

### 1. `backend/services/capital_client.py:270` — close_position 無併發/重複送單防護,兩個同時平倉請求會雙重送單

面向:concurrency | finder:concurrency | 驗證信心:high | 重複回報:3 個 finder

close_position 在 event loop 讀 store.position_for() 取整個部位量,通過「無部位可平」檢查後送單;但從讀部位到 COM 佇列執行之間沒有任何 in-flight 去重或冪等防護。前端雙擊、網路重送、或兩個分頁同時按平倉,兩個請求都會讀到同一份部位、都過閘、都進 COM 佇列 → 同檔部位被平兩次,現股賣會變成超賣/違約交割風險,融資/券更糟。capital_safety 的閘只驗單筆(總開關/量/金額),完全沒有「同檔已有飛行中平倉單」的檢查;submit_stock_order 同樣無任何 client request id 去重。這是真錢寫入鏈,該擋未擋。

**建議修法**:在 CapitalClient 加 per-stock_no 的 in-flight 平倉鎖(asyncio.Lock 或 dict[stock_no]→pending flag,送單回報後釋放),並在 close 閘內查 store 是否已有同檔同方向的活躍委託(_RANK 1/2)而拒絕;更通用解是讓前端帶 client_order_id、後端短窗去重。

**驗證者**:確認為真問題,且實際比 finding 描述更廣:不只併發 race,連循序重送都擋不住。close_position(backend/services/capital_client.py:270)讀 store.position_for() 過「無部位可平」閘後送單,但 store 部位只靠 OnRealBalanceReport 更新(成交後 debounce 2s + COM 重查),飛行中未成交的平倉委託完全不反映在部位快取——第一筆平倉送出後的數秒窗口內,第二筆請求照樣讀到原始部位、照樣過閘、照樣全量送單(build_close_order 預設 lots=全部持有量,前端 PositionsList.tsx 送的正是不帶 qty 的全量市價平倉)。grep 全 backend 確認 capital 寫入鏈零鎖、零 in-flight 去重、無 client_order_id;capital_safety 各閘只驗單筆參數,無「同檔已有活躍委託」檢查;routes/capital.py:89 是裸轉發。前端 PositionsList.tsx:143 的 busy flag 只防同一 component instance 雙擊,兩個分頁/remount/bot/curl 全繞過,client-side 防護不構成伺服端反駁。券商端是否擋現股超賣是 codebase 外的縱深防禦,不能據此判程式碼正確。依專案標準(capital_* 重複送單/該擋未擋=critical)維持 critical。

### 2. `backend/services/capital_client.py:270` — 下單/平倉鏈無重複送單防護:平倉連按兩次會送出兩張全量反向單

面向:error-handling | finder:test-gaps | 驗證信心:high

close_position 用 store.position_for 的快取部位組反向單,但部位要等成交回報→2s debounce→GetRealBalanceReport 回來才更新;這個窗口內(數秒)第二次 close 請求看到的仍是原始持倉,build_close_order 的 lots>holding 檢查照樣通過,兩張全量賣單都送進群益——現股或可被券商以庫存不足擋下,融資/融券回補與一般下單(submit_stock_order 完全沒有任何冪等或 in-flight 去重)則沒有兜底,連點/前端重試/上一條 finding 的懸掛重送都會變成真錢重複委託。route 層(routes/capital.py)也沒有任何防護。測試只驗單發請求路徑,沒有任何「同部位在途平倉再平倉」或「相同參數短窗重複下單」的案例。

**建議修法**:最小修法:CapitalClient 內記 in-flight/近期已送的 (stock_no, action) 集合,close_position 在送出到下一次 balance 刷新前拒絕同股號再平倉(或把可平量改成「持有 − 在途平倉量」);一般下單可由前端帶 client_order_id、後端短窗(如 5s)對 (stock_no, side, price, qty) 去重並寫稽核。各補一個測試:連續兩次 close 同股號,斷言第二次被擋且留稽核。

**驗證者**:逐層驗證屬實。(1) capital_client.py close_position 只讀 store.position_for 快取組反向單,而部位唯一更新路徑是成交回報 D → _mark_balance_dirty(2s debounce)→ _maybe_query_balance → GetRealBalanceReport 回完才 set_positions——窗口內第二次 close 看到原始全量持倉,build_close_order 的 lots>holding 檢查照過。(2) submit_stock_order 的 check_stock_order 只有總開關/價格/量/金額閘,全鏈(client、capital_safety、routes/capital.py)無任何 in-flight 去重或冪等機制。(3) 前端 ClosePositionDialog 有 busy 旗標擋對話框內連點,但成功即關窗,數秒窗口內重開再按一次照樣送第二張全量單;且擋不住 API 重試/bot。(4) 測試確認:test_capital_client.py 對 close/dedup/idempotent 零命中,test_capital_close.py 只測純函式,無任何重複送單案例。(5) 風險成立:融券回補是 BUY,第二張券商不會以庫存擋、直接變新多頭部位,真錢重複委託。依專案標準(capital_* 重複送單=critical)維持 critical。純本地邏輯,不涉富邦 SDK。

## High(29 項)

### 3. `backend/services/fubon_ws.py:161` — 重連時對同一個 SDK singleton WS 實例重複 _wire_callbacks,pyee handler 累加導致 tick 倍增

面向:bug | finder:fubon-market | 驗證信心:high | 重複回報:3 個 finder

_reconnect 先 pop _ws_handles 再走 _ensure_handle,但 _ensure_handle 取的 fubon.sdk.marketdata.websocket_client.stock 是同一個實例(已驗證:MarketData 只在 init_realtime 建一次、WebSocketClientWrapper 會快取 _stock_wrapper),而 fugle_marketdata WebSocketClient.on() 直接呼叫 pyee EventEmitter.on(),是累加不是覆蓋。每次斷線重連後 on_message/on_disconnect/on_error 都多掛一份:N 次重連後每筆 tick 被處理 N+1 次(ring_buffer 重複 append → 量被放大 N+1 倍、signal_engine 收 N+1 份、前端 broadcast N+1 份),且下次斷線會同時觸發 N+1 個 _reconnect task 互踩。量基訊號(爆量、漲停打開)會直接誤判。

**建議修法**:對每個 conn_idx 只 wire 一次:記住已 wire 的 ws 實例(identity 比對),重連時若是同一實例就跳過 _wire_callbacks;或 wire 前先 ws.off() 移除舊 handler。

**驗證者**:已逐層驗證實際安裝的 fubon-neo 2.2.8 原始碼,finding 完全成立。(1) 同一實例:sdk.py init_realtime 只建一次 MarketData;adapter.py WebSocketClientWrapper 快取 _stock_wrapper(120-126 行);底層 fugle_marketdata websocket factory 也用 __clients dict 快取 WebSocketStockClient——fubon_ws.py:27-29 註解自己就寫明是 process-level singleton。(2) 累加:fugle_marketdata/websocket/client.py:148-149 的 on() 直通 pyee EventEmitter.on(純 append),且 _wire_callbacks 每次建新 closure,必然疊加。(3) 全檔無 ws.off(),shutdown 也不解綁。(4) _reconnect 先 pop _ws_handles 再呼 _ensure_handle,handle 不在 dict 必走 _wire_callbacks 重掛。(5) 唯一反駁路徑不成立:FubonClient 只在 status==ERROR 時 background retry 重 login(fubon_client.py:125),純 WS 斷線不改 FubonClient 狀態,重連拿到的是同一個舊實例。(6) 觸發不罕見:adapter.py health check 預設開啟、2 次 miss pong 即 disconnect。後果鏈成立:N 次重連後每筆 tick 處理 N+1 次(ring_buffer 量放大→爆量/漲停打開等量基訊號誤判、signal_engine 與前端 broadcast 各收 N+1 份),on_disconnect 也累加導致下次斷線同時開 N+1 個 _reconnect task。建議修法(identity 比對跳過重 wire,或 wire 前 off 舊 handler)合理。

### 4. `backend/services/fubon_ws.py:199` — _handle_raw_message 不過濾 isTrial 試撮 tick,收盤前 13:25–13:30 的模擬撮合價會被當真成交

面向:bug | finder:fubon-market | 驗證信心:high

官方 trades channel 文件明載 data 帶 isTrial(試撮階段才出現)。signal_engine 的正盤 gate 是 09:00 ≤ t < 13:30,只擋掉 08:30–09:00 開盤試撮;但收盤集合競價 13:25–13:30 的試撮 tick 落在 gate 之內,模擬價/量會被累積進今日總量、進 ring_buffer window、參與訊號評估與前端分時圖最後一根 K 棒——這些都不是真實成交。signal_engine.py:368 的註解也自承 window 會被 08:30–09:00 試撮 tick 扭曲,根因就是這裡沒在源頭過濾。

**建議修法**:在組 Tick 之前加 `if data.get("isTrial"): return`(或視需求另走 indicative 路徑),從源頭擋掉,signal_engine 的時間 gate 與 window 扭曲問題一併解掉。

**驗證者**:確認為真問題。(1) fubon_ws.py:188-238 的 _handle_raw_message 完全沒讀 isTrial,全 repo grep isTrial/is_trial 零命中——試撮 tick 會原樣進 ring_buffer、signal_engine queue 與前端 tick broadcast。(2) 已照富邦文件工作流程 WebFetch 官方 trades channel 文件:data 確實帶 isTrial 欄位(試撮階段為 true),且訂閱參數只有 channel/symbol/intradayOddLot,沒有排除試撮的選項,只能 client 端過濾。(3) signal_engine.py 的 _in_trading_session 是 09:00 ≤ t < 13:30 半開區間,只擋開盤試撮(08:30-09:00);收盤集合競價 13:25-13:30 的試撮揭示落在 gate 內,模擬量會累進 _day_volume(line 230)、模擬價會參與訊號評估與 prev_tick 更新。專案自己在 signal_engine.py:28-29 註解承認「Fubon WS 推 indicative tick,實際沒成交」,line 368 也自承 window 被試撮扭曲——根因確為源頭未過濾。建議修法(在組 Tick 前 if data.get("isTrial"): return)合理且能同時解掉開盤試撮的 window 扭曲問題。唯一微小保留:官方文件未明列試撮具體時段,但 isTrial 欄位的存在與 TWSE 收盤集合競價模擬揭示機制使 13:25-13:30 情境高度可信,且即使僅計開盤試撮,源頭未過濾的問題仍成立。

### 5. `backend/services/fubon_client.py:107` — 重複 init()(overnight 8:25 重登)建立新 FubonSDK 但不清舊 session,舊 WS 連線繼續推 tick 造成雙倍行情

面向:bug | finder:fubon-market | 驗證信心:high

_do_login_sync 每次都 new FubonSDK() 並覆蓋 self._sdk,從不 logout/disconnect 舊 SDK。overnight.run_overnight_reconnect 每天 8:25 呼叫 fubon.init() 重登,之後 WSPool 只把舊 handle 從 dict pop 掉(overnight.py:42),從未對舊 ws 呼叫 disconnect——舊連線的 run_forever thread 與 on_message callback 仍然活著,持續把 tick 餵進同一個 _handle_raw_message。在富邦端主動踢線之前,ring_buffer/訊號量/前端 broadcast 每筆 tick 都是雙份;舊 session 也持續占用富邦帳號的連線額度。

**建議修法**:_do_login_sync(或 init)在建新 SDK 前,先把舊 _sdk 的 marketdata websocket disconnect + logout;或讓 overnight 流程在 relogin 前先呼叫 pool 對舊 handle 的 disconnect。

**驗證者**:機制鏈全部核實:(1) fubon_client.py:107-121 _do_login_sync 每次 new FubonSDK() 並覆蓋 self._sdk,全 repo 只有 app shutdown 會 logout,relogin 路徑零清理;(2) overnight.py:42 只把舊 handle 從 _ws_handles pop 掉,從未呼叫 disconnect(repo 內 ws.disconnect 僅在 WSPool.shutdown 與 futures ws);(3) 已照富邦文件工作流程查官方 making-connection.txt——官方範例用同一 SDK 迴圈呼叫 5 次 init_realtime() 各取 websocket_client.stock 得到 5 條不同連線,證明每次 init_realtime 產生新 ws client,relogin 後 _ensure_handle 拿到的必是新物件,舊 ws 連線仍存活;fubon_ws.py:27 的 process-level singleton 註解指同一 SDK session 內重複取屬性,不構成反駁;(4) 舊 ws 的 callback 閉包指向同一個 pool._handle_raw_message,thread 持有引用不會被 GC,server 端訂閱未取消,舊連線存活期間 ring_buffer/訊號引擎/前端 broadcast 每筆 tick 雙份;(5) 官方文件未提及新 login 會踢舊 session;加重證據:舊 ws 的 on_disconnect 仍 wired 到 _reconnect(conn_idx),富邦最終踢舊線時會把新 handle pop 掉再重連,造成額外 churn;舊 session 也占用每帳號 5 連線額度。唯一小不確定是 8:25 當下舊連線是否已被富邦夜間斷開,但該流程設計前提就是主動換 token,程式缺陷與後果機制成立,維持 high。

### 6. `backend/services/fubon_ws.py:136` — subscribe() 真訂閱失敗被吞掉,bookkeeping 已寫入但永遠收不到 tick,呼叫端拿到成功

面向:error-handling | finder:fubon-market | 驗證信心:high | 重複回報:3 個 finder

subscribe() 先把 symbol 寫進 _refcount/_symbol_to_conn/_conn_subs 才呼叫 _real_subscribe;_real_subscribe 失敗只 logger.error,_ensure_handle 失敗(SDK 不 OK 或 connect 例外)回 None 也靜默 return。結果:watchlist/monitor_list/bookmarks 等 route 回 200,使用者以為已監控,實際該 symbol 永遠不會有 tick、訊號永遠不會觸發,也沒有任何補救 retry(只有恰好發生斷線重連時才會靠 _conn_subs 重訂閱救回)。富邦端 silent reject(error 1001)同樣只進 on_error log。

**建議修法**:_real_subscribe/_ensure_handle 失敗時向上 raise,subscribe() 回滾 refcount/_symbol_to_conn/_conn_subs 後把例外丟給 route 回 5xx;或保留 bookkeeping 但標記 pending 並排程重試,至少不能對呼叫端裝沒事。

**驗證者**:無法反駁,確認為真問題。(1) fubon_ws.py:96-100 先寫 _symbol_to_conn/_conn_subs 才呼叫 _real_subscribe;_real_subscribe(:126-136)中 _ensure_handle 回 None 靜默 return、ws.subscribe 例外只 logger.error,不拋不回滾——對照同函式 capacity 滿分支(:91-95)有 discard+raise,證明作者有失敗回滾意圖但只做了一半。(2) 失敗後 symbol 已在 _symbol_to_conn,之後任何重試都走 need_real_sub=False 直接回成功,永遠不再打真訂閱;_ensure_handle 失敗時 handle 未建立、不會有 disconnect 事件,_reconnect 救援路徑不會觸發;lifecycle_sync 只在啟動/匯入跑且呼叫同一個吞錯的 subscribe(),無補救。(3) 呼叫端意圖被打臉:routes/monitor_list.py:49-53 註解明寫「失敗就不寫 store 避免狀態不一致」、routes/preview.py:58-62 對 RuntimeError 回 503,但真訂閱失敗不會以 RuntimeError 浮上來,防護形同虛設,route 回 200、訊號永不觸發。(4) 不涉 SDK 行為假設(code 自己 try/except 包訂閱、error 1001 是檔內 :32 自己的註解),屬純本地錯誤處理,不需查富邦文件。對監控+訊號引擎而言是核心功能靜默失效,high 嚴重度合理。

### 7. `backend/services/fubon_futures_ws.py:150` — WS 推送訊息是 JSON 字串,_handle_message 只接受 dict,所有即時 candle 推送被靜默丟棄

面向:bug | finder:fubon-futures | 驗證信心:high | 重複回報:3 個 finder

富邦 Neo SDK 的 WS message callback 收到的是 JSON 字串:官方文件 Node.js 範例需 JSON.parse(message),且本專案已上線驗證的股票 WS(services/fubon_ws.py:191)正是 `json.loads(raw) if isinstance(raw, str) else raw`。但 _handle_message 寫 `raw.get("data") if isinstance(raw, dict) else None`,字串訊息會落到 None 直接 return——整個 MXF 即時 candle broadcast 永遠不會發出,且無任何 log(連 debug 都沒有),前端只剩 REST 輪詢,WS 即時功能靜默失效。

**建議修法**:比照 fubon_ws.py 的正規化:`payload = json.loads(raw) if isinstance(raw, str) else raw`,失敗或非 dict 才丟棄;並在丟棄非預期型別時至少 log 一次,避免再次靜默失效。盤中實機驗證一次推送有到前端。

**驗證者**:本地 SDK 原始碼直接證實:fubon-neo 2.2.8 的 futopt WS client 繼承自 fugle_marketdata\websocket\client.py,其 __on_message(第 126-128 行)為 `message = orjson.loads(data); self.ee.emit(MESSAGE_EVENT, data)`——emit 給 "message" listener 的是原始 JSON 字串(websocket-client text frame 為 str),parse 後的 dict 只用於內部 auth/pong 判斷。因此 fubon_futures_ws.py:150 的 `raw.get("data") if isinstance(raw, dict) else None` 對所有推送都得到 None 並靜默 return,MXF 即時 candle broadcast 永遠不會發出。佐證:官方期貨 candles 文件 Node.js 範例需 JSON.parse;同 repo 已上線的股票 WS(fubon_ws.py:191)正確做了 json.loads 字串正規化;fubon_client.py:120 用 Mode.Normal 故 candles 訂閱不會被擋(訊息確實會進來再被丟);backend/tests/ 無任何測試覆蓋 _handle_message;handoff 文件(2026-05-27-mxf-realtime-update-handoff.md)顯示推送從未實機觀察過,無 live 證據可反駁。建議修法(比照 fubon_ws.py 正規化+丟棄時 log)合理。

### 8. `backend/services/fubon_futures.py:173` — 「週五無夜盤」是錯誤的市場假設——台指期週五 15:00 至週六 05:00 有盤後交易

面向:bug | finder:fubon-futures | 驗證信心:high

期交所盤後交易時段為每一交易日(週一至週五)15:00 至次日 05:00,週五夜盤照開(歸屬下一交易日週一)。determine_current_session 把週五 13:45 後與週六整天判為 closed(測試也以「週五本應夜盤但無」固定了這個錯誤認知),導致每週五夜盤:target_after_hours_flag 回 None → WS pool 主動 teardown 不訂閱、/api/mxf/candles 回 current_session=closed,前端整個週五夜盤(美股交易時段、波動最大的時段之一)拿不到即時行情。這是市場規則判斷,非 SDK 行為。

**建議修法**:改為:weekday==4 且 t>=15:00 → night;weekday==5 且 t<05:00 → night;週六 05:00 後至週一 08:45 前 closed。同步修正 backend/tests/test_fubon_futures.py 中 5/29–5/30 的 fixture 與註解。

**驗證者**:真問題。期交所盤後交易時段為每一交易日 15:00 至次日 05:00,週五為交易日、夜盤照開(歸屬下一交易日週一),已用外部來源(期交所盤後交易介紹、元大期貨、期交所夜盤三大法人資料頁)交叉確認;期交所夜盤資料頁可查到週五日期的夜盤成交,唯一不開的是「次日為假日」的休市安排而非每週五。程式碼 fubon_futures.py:167-174 把週五 13:45 後與週六全天判 closed、docstring 明寫「週五無夜盤」,test_fubon_futures.py:34-41 也以「週五本應夜盤但無」固定錯誤認知,且 docs/notes/mxf-fubon-api-observations.md 無任何實測佐證此假設。下游影響鏈屬實:fubon_futures_ws.py:19-26 target_after_hours_flag 在 closed 回 None → _ensure_subscribed_for_now 主動 _teardown_ws 不訂閱,/api/mxf/candles 回 closed,前端每週五夜盤(對應美股交易時段)拿不到即時行情。此為純市場規則判斷,與富邦 SDK 行為無關,建議修法方向正確。

### 9. `backend/services/signal_engine.py:230` — heartbeat 每秒重餵 latest tick,day_volume 被大量重複累加

面向:bug | finder:signal-engine | 驗證信心:high

_heartbeat_loop 每秒對所有 monitor symbol 用 ring_buffer.latest 呼叫 _evaluate,而 _evaluate 第 230 行無條件把 tick.size 加進 _day_volume。正盤內同一筆 latest tick 在下一筆成交到來前,每個 heartbeat(1 秒)都被重加一次;tick-driven path 又加一次。對成交間隔 10 秒的股票,day_volume 會膨脹約 10 倍以上,任何 day_volume gte X 的條件都會嚴重提早觸發——訊號靜默出錯。測試(test_signal_engine_day_metrics)只直接呼叫 _evaluate 一次,沒覆蓋 heartbeat 重入情境。

**建議修法**:只在「新 tick」時累積:比較 tick 與 _prev_tick.get(symbol) 是否同一物件(heartbeat 重餵時 is 同一個 Tick instance)即可跳過累加;或把累加搬到 _consume_loop 的 tick-driven path,heartbeat 不做量累積。

**驗證者**:確認為真問題。_heartbeat_loop(signal_engine.py:208-212)每秒以 ring_buffer.latest 重餵 _evaluate,而 latest()(ring_buffer.py:76-82)在無新成交時回傳同一個 Tick 物件;_evaluate 第 230 行無條件把 tick.size 加進 _day_volume,唯一閘門 _in_trading_session 在正盤內必然通過,且全 backend grep 確認沒有任何去重或重算邏輯。tick-driven path(_consume_loop)對同筆 tick 又加一次,故每筆成交的 size 被加「1 + 到下筆成交前的 heartbeat 秒數」次,成交稀疏的股票 day_volume 會膨脹數倍至數十倍,_resolve_field 的 day_volume(649 行)直接讀此污染值,day_volume gte 條件會嚴重提早觸發。測試 test_signal_engine_day_metrics.py 只直接呼叫 _evaluate、無 heartbeat 重餵情境,確實未覆蓋。建議修法可行(heartbeat 重餵時 tick is _prev_tick[symbol] 為同一物件,可據此跳過累加)。純本地邏輯,不涉富邦 SDK 行為假設。

### 10. `backend/services/signal_engine.py:175` — _consume_loop / _heartbeat_loop 對 _evaluate 無例外保護,一次例外永久殺死引擎

面向:error-handling | finder:signal-engine | 驗證信心:high | 重複回報:3 個 finder

_consume_loop(175 行)與 _heartbeat_loop(212 行)await self._evaluate(...) 都沒包 try/except。_fanout 內的 broadcaster.broadcast 與 get_signal_writer().append(SignalsLog.append 是同步檔案 I/O,磁碟滿 / Windows 檔案被鎖會丟 OSError)都不在任何 try 裡;任一例外往上傳就讓 consumer 或 heartbeat task 靜默死亡(只在 task 被 GC 時印 'exception never retrieved'),之後 tick 永遠堆在 queue、所有訊號停擺直到重啟,health endpoint 也看不出來。

**建議修法**:兩個 loop 內把 await self._evaluate(...) 包 try/except Exception 並 logger.exception;或在 _fanout 把 broadcast / signal_writer.append 各自包 try。也可在 health() 檢查 self._consumer.done() 暴露 task 死亡狀態。

**驗證者**:屬實。_consume_loop(175)與 _heartbeat_loop(212)的 await self._evaluate(...) 確無例外保護(loop 內 try/except 只包 queue.get/sleep 抓 CancelledError;_evaluate 是 try/finally 無 except)。例外來源真實:_fanout 的 get_signal_writer().append → SignalsLog.append(signals_log.py:36-44)是裸 mkdir+open+write 同步檔案 I/O,磁碟滿/Windows 檔案鎖會丟 OSError 直達 task;另 _eval_strategy 的 strat["lock_seconds"]/strat["levels"] 與 _eval_filter_cond 的 float(value) 也是裸取值,一條格式錯的 filter_json 規則就能每 tick 丟 KeyError 殺死 task。全 backend 無 add_done_callback/重啟監督,health() 不檢查 _consumer.done();且 consumer 死後 _last_lag_ms 凍結,_monitor_loop 的 backpressure 自動 disable+alert 也跟著失效,連 degraded 警報都不會響——靜默停擺比 finding 描述還徹底。唯一不精確處:broadcaster.broadcast 內部已 per-client try/except,send 失敗不會外漏,該子鏈不成立,但不影響整體結論。

### 11. `backend/services/capital_client.py:248` — 寫入 future 無逾時、COM 執行緒亡故時佇列中的命令永不 resolve,真錢請求可永久懸掛

面向:concurrency | finder:capital-core | 驗證信心:high

_execute_write 把 com_call 丟進 _cmd_q 後無條件 `await fut`,沒有任何逾時。兩個踩雷情境:(1) SendStockOrder 是 bAsync=0 同步呼叫,若群益端網路掛起,fn() 永不返回 → HTTP 請求永久懸掛,且 COM 執行緒卡住 → pump 停擺、OnNewData/庫存輪詢全停,但 status 仍是 ok(_pump_once 的防護只擋例外、擋不了阻塞);(2) _run 的 finally 只降 status,不 drain _cmd_q——status 檢查通過後、執行緒因 loop 關閉等原因死亡時,已入佇列命令的 future 永不 resolve。使用者面對懸掛的下單請求不知道單送出沒,自然重送 → 真錢重複下單風險。

**建議修法**:(1) `await asyncio.wait_for(fut, timeout=N)`,逾時回明確的「結果未知,請先核對委託回報,勿直接重送」訊息並照樣稽核;(2) _run 的 finally 內 drain _cmd_q,對每個未消化的 future 以 call_soon_threadsafe set_exception;(3) 可選:幫浦心跳時間戳 + 監看,COM 呼叫卡死超過門檻就降 status。

**驗證者**:逐項核實後無法反駁:(1) capital_client.py:248 的 `await fut` 確無逾時,全檔僅 _cmd_q.get(timeout=0.05) 一處 timeout,routes/capital.py:67-91 五個寫入端點也是裸 await,呼叫鏈無任何一層補逾時;(2) capital_com.py:106 確為 `SendStockOrder(user_id, 0, order)` 即 bAsync=0 同步呼叫(註解自證),群益端掛起時 fn() 阻塞 COM 執行緒 → pump 停擺、OnNewData/庫存輪詢全停,而 _status 只在 _init_com 失敗或執行緒退出才改變,阻塞期間仍是 ok,後續請求照過 239 行檢查繼續入佇列永久懸掛——_pump_once 的 try/except 確實只擋例外擋不了阻塞;(3) _run 的 finally(205-210 行)只降 status 不 drain _cmd_q,已入佇列的 future 永不 resolve,且 put(246 行)在 status 檢查(239 行)之後存在競態;production 無人 put None sentinel(僅測試),執行緒死亡多來自非預期路徑。無 watchdog/心跳監看,前端逾時亦無法消除「下單結果未知 → 誘發重送」的真錢風險。依 capital_* 從嚴標準,此為真問題,建議修法(wait_for + 結果未知訊息照樣稽核 + finally drain future)合理。

### 12. `backend/services/capital_com.py:170` — OnNewData/OnRealBalanceReport/OnProfitLossGWReport 回呼例外 `except: pass`,回報靜默丟失且零痕跡

面向:error-handling | finder:capital-core | 驗證信心:high | 重複回報:3 個 finder

_ReplyEvents.OnNewData 與 _OrderEvents 兩個事件 sink 把 on_reply/on_balance/on_profit 的例外整個吞掉,連 log 都沒有。_handle_reply 鏈上 store.apply_reply、broadcast(loop 關閉時 call_soon_threadsafe 會丟 RuntimeError)或 parse_onnewdata 對異常格式 pydantic 驗證失敗,任何一個炸掉就等於一筆委託/成交回報無聲蒸發:委託清單與實際市場狀態脫節(已成交的單看起來還掛著),使用者可能據此重複平倉或誤刪改,而且事後完全無法追查為什麼漏。不讓例外炸掉 COM 事件迴圈是對的,但「不炸」不等於「不留痕」。

**建議修法**:三處 except 改為 `logger.exception("reply 回呼例外,該筆回報丟棄: %r", bstrData)` 之類——logging 在 COM 執行緒上安全且便宜;保持吞例外不 re-raise 即可。

**驗證者**:逐一驗證屬實:capital_com.py 167/185/192 三處 `except Exception: pass` 零 logging。下游 _handle_reply(capital_client.py:75)內部無 try/except——parse_onnewdata 走 pydantic 驗證可炸、broadcast 是 main.py:93 注入的 loop.call_soon_threadsafe(loop 關閉時拋 RuntimeError;同 repo fubon_futures_ws.py:133 對同一呼叫有防 RuntimeError,佐證風險真實)。capital_client.py:79 的 INFO log 在 parse 成功後才打,parse 失敗的回報零痕跡;稽核只覆蓋寫入操作不覆蓋進來的回報;_handle_balance/_handle_profit 連 INFO 都沒有。test_capital_com.py 只斷言例外不逃出 sink、未禁止 logging,建議修法與既有測試全相容。真錢回報無聲蒸發還會連帶跳過成交觸發的庫存重查(_mark_balance_dirty),severity high 成立。

### 13. `backend/services/capital_com.py:157` — 回報主機斷線無偵測:sink 未處理 OnDisconnect,斷線後委託回報靜默停更而 status 仍 ok

面向:error-handling | finder:capital-core | 驗證信心:high | 重複回報:2 個 finder

_ReplyEvents 只處理 OnConnect/OnNewData/OnReplyMessage。SKReplyLib 的回報連線盤中斷掉時(網路抖動、群益端踢線),沒有任何事件處理 → 不降 status、不記 last_error、不重連。之後 OnNewData 全收不到:委託清單凍結在斷線前的狀態、成交不再觸發庫存重查 debounce(僅剩 60s 定時輪詢兜底部位),前端 status 卻一路全綠。真錢面板上「看起來還掛著的單其實已成交/已刪」是高危資訊。capital_store 的 docstring 已備註重連前要 clear(),代表重連是已知未做——但連『偵測+降級』這層也缺。

**建議修法**:最小修:_ReplyEvents 實作 OnDisconnect(bstrUserID, nErrorCode):log + 透過 callback 通知 client 設 last_error / 降 status 為 degraded,讓 /api/capital/status 反映出來。完整修:幫浦圈定期重試 ConnectByID,重連成功前先 store.clear() 再吃 backlog 重播(store 註解已寫明此前置條件)。

**驗證者**:已逐項驗證:(1) capital_com.py:150-170 的 _ReplyEvents 確實只實作 OnReplyMessage/OnConnect/OnNewData;本機 venv 的 comtypes 產生檔(gen/_75AAD71C...py:1666)證實 SKReplyLib 事件介面 _ISKReplyLibEvents 有 OnDisconnect(bstrUserID, nErrorCode),comtypes 對未實作事件靜默忽略,斷線完全偵測不到。(2) capital_client.py 的 _status 只有三處賦值(init 前 error/init 成功 ok/COM 執行緒亡故 error),註解宣告的 degraded 從未被設;回報主機斷線不會讓幫浦圈丟例外,沒有任何路徑降 status 或記 last_error,/api/capital/status 會一路回 ok。(3) 委託清單唯一來源是 OnNewData→store.apply_reply,無輪詢兜底;部位僅剩 60s 定時重查,成交觸發的 debounce 重查也隨 OnNewData 失效。(4) capital_store.py:8-10 docstring 明寫重連未做且重播前須 clear(),與 finding 引述一致。整個 backend grep 無任何 capital 回報斷線處理。真錢面板上委託狀態靜默凍結而 status 全綠屬實,high 嚴重度恰當(資訊陳舊風險,非主動錯誤下單,不到 critical)。

### 14. `backend/services/capital_client.py:152` — _init_com 丟棄 SetAuthority 與 SKOrderLib_Initialize 的回傳碼,且 CAPITAL_ENV 非 'test' 一律當正式環境

面向:error-handling | finder:capital-core | 驗證信心:high

line 148 `self._com.set_authority(...)` 與 line 152 `self._com.init_order()` 的回傳碼都被忽略(對照 capital_login_probe.py 會逐一印出這兩個 rc,client 卻不看)。SetAuthority(2) 在 test 模式失敗被吞時,後續登入可能落在預設(正式)環境——CAPITAL_ENV=test + ORDER_ENABLED=true 跑 capital_smoke --send-test 的「測試單」就有進真實市場的可能。另外 `2 if self._env == "test" else 0` 表示 env 拼錯(如 'Test'、'testing')會靜默取得正式環境權限,方向與安全預設相反。init_order 失敗被吞則是 status=ok 但下單必敗的延遲爆炸,錯誤訊息也會更難懂。

**建議修法**:兩個 rc 都要接住:init_order 非 0 視同 init 失敗 raise(與 login/read_cert 同等待遇);SetAuthority 至少 log 回傳碼,env=test 時非 0 應中止 init(寧可不啟動也不可疑似落在正式環境)。CAPITAL_ENV 在 factory 用既有的 CapitalEnv enum 驗證,未知值直接拒絕啟用而非默認正式。

**驗證者**:逐項核實均屬實:(1) capital_client.py:148/152 的 set_authority 與 init_order rc 確實被丟棄,而同函式 login/read_cert 的 rc 有檢查並 raise,處理不一致;capital_login_probe.py:49-56 逐一印出這兩個 rc,證明作者也認為它們有意義。(2) capital_factory.py:24 對 CAPITAL_ENV 只 .strip() 不 .lower() 也不用既有 CapitalEnv enum 驗證,而 probe 腳本卻 .strip().lower()——「Test」在探針當 test、在正式後端拿 SetAuthority(0) 正式權限,矛盾坐實;capital_safety.py 安全閘只看 order_enabled 不看 env,env!="test" 擋板只在 capital_smoke.py:31 且比對 .env 字串、不驗 SetAuthority 是否實際生效。(3) M1 handoff(docs/notes/2026-06-09)記載官方範例不呼叫 SetAuthority 即預設正式(0),故 SetAuthority(2) 失敗被吞的 fallback 方向正是真錢;目前 env=test 連 login 都連不上測試主機,若哪天 env=test 下登入「成功」反而最可疑,現行碼無從分辨。唯一未能驗證的是群益 COM 在 SetAuthority 失敗時是否保持預設權限(docx 文件未讀),但 rc 丟棄+env 無驗證+兩路徑行為矛盾均為純程式碼可證事實,capital_* 該擋未擋依專案規則從嚴認定,finding 成立。

### 15. `backend/services/capital_balance.py:120` — 損益試算回填忽略交易種類欄,同檔多種庫存並存時均價/損益基底可能套錯成本基礎

面向:bug | finder:capital-flow | 驗證信心:high

GetProfitLossGWReport 的回報每列帶交易種類(test_capital_balance.py 的真實樣本 [3]=「現股」/「融資」),但 parse_profit_line 不解析它,ProfitRow 只以 stock_no 為鍵。capital_store.apply_profit_rows 也是 last-wins:同一檔股票若同時持有集保+融資(dedupe_positions 的註解明言這是「穩定狀態、每 60s 查詢都會走到」),損益報告會回兩列,最後一列覆蓋前一列——被 dedupe 保留的部位(例如融資 3 張)可能拿到現股列的均價/含費稅息損益/成交價金,成本基礎整個套錯。store 的 set_positions 特地用 prev.kind == p.kind 防跨種類沿用,證明作者知道資/券成本基礎不可混用,但這條回填路徑沒有同等防護。真錢面板上損益與均價靜默顯示錯值,會誤導平倉決策。

**建議修法**:parse_profit_line 解析交易種類欄(現股→cash、融資→margin、融券→short)放進 ProfitRow,apply_profit_rows 只回填 r.kind == p.kind 的列;對不上的列略過並 debug log。補一個「同檔兩列不同種類」的測試固定行為。

**驗證者**:逐層驗證後無法反駁,finding 成立。(1) 損益回報確實逐列帶交易種類:test_capital_balance.py:112-113 的正式環境真實樣本,欄位 [3] 分別是「現股」與「融資」——既然每列只有單一種類值,同檔同時持有現股+融資時報告必然回兩列(單列無法彙總兩種成本基礎)。(2) parse_profit_line(capital_balance.py:103-125)完全不讀 [3],ProfitRow 無 kind 欄。(3) capital_store.py:201-213 apply_profit_rows 只以 r.stock_no 查 self._positions,同檔兩列後到者覆蓋先到者(last-wins),不檢查種類。(4) 情境真實存在:dedupe_positions(capital_balance.py:71)註解明言「資+集保並存是穩定狀態,每 60s 查詢都會走到這裡」,且 dedupe 保留張數大者——若保留融資部位而損益報告現股列排在後,融資部位就拿到現股的均價/含費稅息損益/成交價金,成本基礎錯置;正確與否取決於券商回報列序,完全無防護。(5) set_positions(capital_store.py:194)的 prev.kind == p.kind 防護證明作者已知資/券成本基礎不可混用,但回填路徑(capital_client.py:106-108 → apply_profit_rows)確實缺同等檢查。鏈路完整接通(_handle_profit → BalanceCollector → _on_profit_complete → apply_profit_rows),非死碼。影響是真錢面板均價/損益靜默錯值誤導平倉判斷,但不直接送錯單,high(非 critical)定級合理。此為群益 SKCOM 本地解析邏輯,不涉富邦 SDK,毋須查富邦文件。

### 16. `backend/services/local_store/config_store.py:220` — import_config 先替換記憶體狀態才驗證形狀,壞 payload 無 rollback,可寫穿壞資料導致下次啟動掛掉

面向:error-handling | finder:local-store | 驗證信心:high

import_config 只驗 schema_version,對 bookmark_groups 等四個 key 完全不驗形狀(data.get(k, []) 接受字串、非 dict 元素等)。流程是先 self._data = new 再 _seed_defaults() → _persist()。若 payload 的 bookmark_groups 是字串或含非 dict 元素,_seed_defaults 的 g.get("is_system") 會丟 AttributeError:此時 (a) route 回 500,但 self._data 已被換成壞資料、無 rollback;(b) 之後任何不相干的寫入(如 add_monitor)的 _persist 會把壞資料寫到 config.json;(c) 下次啟動 load() 在 _seed_defaults 再丟 AttributeError,不在 (JSONDecodeError, OSError) 的 except 範圍 → 後端起不來。匯入檔是使用者上傳/可手改的(POST /api/config/import 收 raw dict),手滑改壞 export 檔就會踩到。

**建議修法**:在動 self._data 之前先驗證:四個 key 都必須是 list 且元素是含必要欄位的 dict,不合就 raise ValueError(route 已有 400 轉換)。驗證通過後再 swap + persist,確保失敗時記憶體與磁碟都維持原狀。

**驗證者**:已用實際程式重現整條故障鏈,finding 完全成立。(1) config_store.py:220 import_config 只驗 schema_version,`new[k] = data.get(k, [])` 對四個 key 不驗型別;(2) 先 `self._data = new` 再 _seed_defaults(),壞 bookmark_groups(字串/含非 dict 元素)在 `g.get("is_system")` 丟 AttributeError——routes/config_io.py:25 只 except ValueError,故回 500 且 singleton(get_local_store().config)記憶體已是壞資料、無 rollback;(3) 實測之後呼叫 add_monitor("2330") 的 _persist 把 bookmark_groups='oops' 寫穿到 config.json;(4) 實測下次 load() 在 _seed_defaults 丟 AttributeError,不在 except (JSONDecodeError, OSError) 範圍(.corrupt 備援不觸發),而 main.py:48 lifespan 直接呼叫 get_local_store().init() 無保護 → 後端起不來。情境可達:POST /api/config/import 收 raw dict(payload: dict),匯出檔使用者可手改。既有測試(test_config_store.py / test_config_io.py)只蓋 schema_version 錯與正常 roundtrip,沒蓋形狀錯誤。建議修法(swap 前先驗四個 key 為 list[dict] 並 raise ValueError)合理,route 已有 400 轉換。唯一可商榷處是嚴重度:這是個人本機工具、有 config.backup-N.json 可手動救回,且不涉下單;但「壞匯入 → 之後任意寫入毒化磁碟 → 重啟掛死」對使用者表現為無法啟動且難以自行診斷,high 合理。

### 17. `backend/scripts/migrate_supabase_to_local.py:36` — Supabase 查詢無分頁,PostgREST 預設 1000 筆上限會靜默截斷 signals_log / watchlist_items

面向:bug | finder:local-store | 驗證信心:high

pull() 用 .select("*").execute() 一次拉,PostgREST 預設最多回 1000 筆,超過的部分靜默丟失、summary 也看不出來(實際已發生:signals_log 漏了 435 筆)。watchlist_items 更嚴重:它連 user_label 都沒過濾、拉的是「所有使用者」的 rows 再在本機用 group_id 過濾,1000 筆上限是吃在全體資料上,自己的 items 被截斷的機率更高。另外無 .order(),回傳順序任意,append 時 id 重編後 JSONL 順序與原始 id 都不穩定。

**建議修法**:pull() 改用 .order("id") + .range(offset, offset+999) 迴圈直到回傳筆數 < 頁大小;watchlist_items 也走同樣分頁。遷移完把各表筆數與 Supabase count 對帳後再印 summary。

**驗證者**:技術上完全屬實:backend/scripts/migrate_supabase_to_local.py 的 pull()(line 36)與 watchlist_items(line 41)均無分頁,PostgREST 預設 1000 筆上限會靜默截斷,且 watchlist_items 確實未過濾 user_label、summary 也只數本機收到的筆數無從對帳——此 bug 非推測,專案記錄明載實際造成 signals_log 漏 435 筆。但嚴重度應從 high 下修:這是一次性遷移腳本,loger 的遷移已於 2026-06-02 完成,user 已知此 bug 並明確決定不補漏的 435 筆(wontfix),腳本有 rerun guard 且 Supabase 路徑已退役,現況下沒有活的受害路徑;除非還有其他使用者要跑此腳本遷移,否則修復價值極低,建議列為已知歷史問題而非待修項。

### 18. `backend/services/cdp.py:137` — backfill 失敗仍標記「今日已嘗試」,CDP 全日鎖在 stale OHLC 且無告警

面向:error-handling | finder:analytics-jobs | 驗證信心:high

get() 先寫 _last_backfill_attempt[symbol]=today 再 await backfill_from_fubon(),且完全不看回傳值。backfill 失敗的常見情境:後端在開盤前啟動、富邦 login 還在 background retry(每 5 分鐘)期間 status != OK;或 8:25 overnight relogin 視窗、網路抖動。一旦當天第一次呼叫踩到失敗,該 symbol 整天不再重試,fallback 讀到的 daily_ohlc 可能是前天的 OHLC——signal_engine._refill_field_cache 會把這組錯的 CDP 5 線寫進 field_cache,訊號照舊參考價靜默觸發(docstring 自己也提過 6531 缺日造成「永遠在算前天 OHLC」的 stale 案例)。camarilla.py L71-73 是同一份邏輯、同一個洞。

**建議修法**:只有 backfill_from_fubon() 回傳 True 才寫 _last_backfill_attempt[symbol]=today;失敗時改記一個短 cooldown 時間戳(例如 5-10 分鐘內不重打),兼顧「每天不重複打 historical API」與「暫時性失敗可恢復」。camarilla.py 同步修。

**驗證者**:真問題。cdp.py:136-138 與 camarilla.py:71-73 確實先寫 _last_backfill_attempt[symbol]=today 才 await backfill_from_fubon() 且不看回傳值;backfill 在 fubon.status != OK、API exception、無資料三種路徑都回 False(cdp.py:185-208),但標記已寫下,該 symbol 當天不再重試。失敗情境具體可達:main.py:46-58 啟動順序是 fubon.init()(3 次失敗即 ERROR + 背景 5 分鐘 retry,啟動照常)→ engine.start() → refresh_active_signals() → _refill_field_cache()(signal_engine.py:100→128-151)立即對 monitor_list 全部 symbol 呼叫 cdp.get(),login 失敗時整批標記「今日已嘗試」,即使 5 分鐘後恢復也整天不再 backfill;跨午夜 heartbeat refill 踩到暫時性網路錯誤同樣鎖死。既有補救都蓋不到:routes/cdp.py:14-21 只在 levels is None 時重打 backfill,有 stale OHLC 時 fallback refresh() 成功、levels 非 None 直接回 stale;watchlist/monitor_list/bookmarks 的直接 backfill 只在新增 symbol 時觸發;下游無任何 as_of_date 過期檢查、無告警,signal engine 會拿錯的 CDP 5 線靜默觸發訊號。唯一瑕疵:finding 提的「8:25 overnight relogin 視窗」在 fubon_client.py 不存在(grep 無此機制),屬臆測細節,但不影響主論點成立。建議的「成功才標記 + 失敗短 cooldown」修法合理,camarilla.py 需同步修。

### 19. `backend/services/overnight.py:42` — 8:25 重連把舊 ws handle 直接 pop 丟棄,不 disconnect、callback 仍掛著

面向:error-handling | finder:analytics-jobs | 驗證信心:high

run_overnight_reconnect 先 fubon.init() 重 login(fubon_client._do_login_sync 會 new 一個 FubonSDK 取代 _sdk,舊 SDK 不 logout),再 pool._ws_handles.pop(conn_idx) 丟掉舊 handle 後 _ensure_handle 建新連線。舊 ws 物件從未呼叫 disconnect(對照 WSPool.shutdown 有做),其 on("message")/on("disconnect") callback 仍接在 pool 上:(a) 若舊連線在 relogin 後仍存活,每個 tick 會被新舊兩條連線各送一次進 ring_buffer 與 signal_engine queue,量能/內外盤雙倍計算、訊號靜默誤觸;(b) 當舊連線稍後被 server 收掉,舊 handle 的 on_disconnect 會觸發 pool._reconnect(conn_idx),把剛建好的新 handle 又 pop 掉重連重訂,開盤前造成訂閱中斷與 churn。

**建議修法**:pop 舊 handle 前先 await asyncio.to_thread(old_ws.disconnect)(包 try/except 忽略錯誤),確保舊連線與其 callback 鏈確實終止;更好的做法是把這段「換 handle + 重訂」收進 WSPool 提供的公開方法,跟 _reconnect/shutdown 共用同一套 teardown 邏輯。

**驗證者**:反駁失敗,finding 屬實。程式碼可直接證實:(1) overnight.py:42 pop 舊 handle 前沒呼叫 disconnect,而 WSPool.shutdown(fubon_ws.py:273)的正規 teardown 有做,且官方文件(making-connection.txt)確認 ws.disconnect() 存在且是官方示範的清理方式;(2) fubon_client._do_login_sync 確實 new 一個 FubonSDK 取代 _sdk、舊 SDK 不 logout,舊 ws 物件被 SDK 內部 thread 持有不會被 GC 隱性關閉;(3) _wire_callbacks 的 on_disconnect closure 只捕 conn_idx、不檢查觸發者是否仍是 current handle(無 `self._ws_handles.get(conn_idx) is ws` 檢查),所以被丟棄的舊 ws 之後斷線時會呼叫 _reconnect(conn_idx) 把剛建好的新 handle 再 pop 掉重連重訂——(b) churn 純程式碼即成立,且發生在開盤前。(a) 雙倍 tick 取決於富邦 server 是否在 relogin 後保留舊連線:查了 getting-started.txt 與 making-connection.txt,官方文件對「重新 login 後舊 WS 連線行為」與「token 過期是否強制斷線」均無記載,無法以「舊連線必死」反駁;舊 ws 的 on_message 仍接 _handle_raw_message,只要舊連線存活每筆 tick 就進兩次 ring_buffer 與 signal_engine queue。即使 (a) 不發生,(b) 與 teardown 不對稱已構成真缺陷。建議修法(pop 前 to_thread(old_ws.disconnect) 包 try/except)與官方 API 相符、可行。

### 20. `backend/services/fubon_ws.py:161` — 每次 _ensure_handle 都重綁 callback,重連後 handler 疊加 → tick 重複處理 N 倍

面向:concurrency | finder:concurrency | 驗證信心:high

SDK 的 websocket_client.stock 是 process 級單例(檔內註解自承),底層 fugle_marketdata.WebSocketClient.on() 走 pyee EventEmitter,以 function object 為 key 存進 OrderedDict(已驗 .venv 內 pyee/base.py:152)。_wire_callbacks 每次都定義新的 closure(on_message/on_disconnect/on_error),物件不同 → 每次斷線重連走 _reconnect → _ws_handles.pop → _ensure_handle → _wire_callbacks,就在同一個 ws 實例上多掛一組 handler。N 次重連後每個 tick 被處理 N+1 次:ring_buffer 重複 append、day_volume 翻倍、訊號條件失真、前端收到重複 tick;on_disconnect 也 N+1 份 → 下次斷線排 N+1 個 _reconnect task,重訂閱風暴。另外 _reconnect 在鎖外呼叫 _ensure_handle,與 subscribe() 路徑可同時建立連線、重複 wire。

**建議修法**:wire 一次即可:在 WSPool 記 per-conn 的 wired flag(或先 ws.off 舊 handler 再 on),且 _reconnect 內的 _ensure_handle 移進 self._lock;或把 handler 定義成存在 self 上的固定 callable(pyee 以物件相等去重,bound method 天然冪等,futures_ws 就是因此沒踩到)。

**驗證者**:確認為真問題。已逐層核實 .venv 內 fubon-neo 2.2.8 實際安裝碼:(1) sdk.marketdata 只在 login 時 init_realtime 建一次,adapter.py WebSocketClientWrapper lazy cache _stock_wrapper + fugle factory __clients cache → 每次 _ensure_handle 拿到同一個 WebSocketStockClient 實例;(2) client.py:148 on() 直通 pyee EventEmitter,pyee/base.py:152 以 function object 為 key 存 OrderedDict、emit 時全部呼叫,而 _wire_callbacks 每次定義新 closure → 必疊加;(3) SDK disconnect()/__on_close 完全不清 ee listeners;(4) _reconnect 先 pop handle 再 _ensure_handle 必重 wire,且 wiring 在 ws.connect() 之前,retry 迴圈每次失敗嘗試還會再多疊一組(比 finding 說的更糟);(5) health check 預設啟用(max_missed_pongs=2 主動 disconnect),斷線情境真實;(6) grep 全 backend 無 ws.off/wired flag 防護;(7) 對照組 fubon_futures_ws.py 用 bound method 確實因 pyee dict key 冪等而不踩,finding 論述正確;(8) 鎖外 _ensure_handle 與 subscribe() 路徑的並發重複 wire 副指控也成立。後果(tick N+1 倍處理、ring_buffer 重複、訊號失真、reconnect task 疊加風暴)由 _handle_raw_message 內容直接推得。建議修法(wired flag 或改 bound method)合理。

### 21. `backend/services/fubon_client.py:121` — 重新 login 直接替換 _sdk,舊 SDK 不 logout、舊 WS 連線/執行緒/ping Timer 全洩漏

面向:bug | finder:concurrency | 驗證信心:high

_do_login_sync 每次建立新 FubonSDK 後 `self._sdk = sdk`,舊 SDK 從不 logout。overnight_loop 每天 8:25 呼叫 fubon.init() 必走這條 → 每天洩漏一組舊 SDK:舊 websocket_client 的 run_forever 執行緒與 health-check ping Timer 鏈(每 30 秒自我重排,永不停)繼續活著;若舊連線在 server 端仍有效,舊 ws 上掛的 _handle_raw_message handler 會與新連線同時餵 tick → ring_buffer/day_volume 重複計算、訊號失真。run_overnight_reconnect 又只 pop pool._ws_handles 而不對舊 ws 呼叫 disconnect,舊連線連主動關閉的機會都沒有。

**建議修法**:_do_login_sync 替換前先對舊 _sdk 做 marketdata.websocket_client.stock/futopt.disconnect() + logout()(包 try/except);overnight 重訂閱前也應先 disconnect 舊 handle 再 pop。

**驗證者**:逐層驗證後確認為真問題,反駁失敗。證據鏈:(1) fubon_client.py:92-121 `_do_login_sync` 每次 new FubonSDK()+init_realtime 後直接 `self._sdk = sdk` 覆蓋,全檔只有 shutdown() 會 logout 當前 SDK,被覆蓋的舊 SDK 永遠沒人 logout。(2) main.py:79 lifespan 確實啟動 overnight_loop,overnight.py:29 每天 8:25 呼叫 fubon.init() 必走此路徑,洩漏每日累積。(3) 讀了實際安裝的 fubon_neo 2.2.8 原始碼(backend/.venv/Lib/site-packages/fubon_neo/sdk.py + adapter.py):init_realtime 每次都 build_websocket_client 建「全新」fugle_marketdata WebSocketClient(health_check enabled、ping_interval=30s)——fubon_ws.py 第 27-28 行「process-level singleton」的註解只在同一 SDK instance 內成立(wrapper lazy cache),跨 SDK instance 是不同物件,無法以此反駁。(4) fugle_marketdata/websocket/client.py:connect() 起非 daemon Thread 跑 run_forever(L191);__send_ping 每 30 秒用 threading.Timer 自我重排(L177),只有 disconnect() 或 ping 送出失敗才停,且 Timer 持有 bound method 引用使舊 client 無法被 GC——執行緒+Timer 洩漏屬實。(5) overnight.py:42 確實只 pop pool._ws_handles 不呼叫 ws.disconnect,舊連線無主動關閉。(6) 官方 rate-limit 文件(已 WebFetch)確認每帳號可同時 5 條 WS 連線、未提及 re-login 會使舊連線失效——舊連線存活情境完全可能,此時舊 ws 上仍掛 _wire_callbacks 的 on_message → _handle_raw_message 與新連線同餵 ring_buffer,重複 tick 屬實;且每天漏一條,數日後恐撞 5 連線帳號上限使新連線被拒。額外加重(finding 未提):舊 ws 的 on_disconnect 仍掛 pool._reconnect(conn_idx),若 server 日後關掉舊連線會誤觸 reconnect 把「新」handle pop 掉重建。唯一小瑕疵:「ping Timer 鏈永不停」說得過重——若 server 端關閉舊連線,ping 送出會丟例外 → except 呼叫 disconnect 鏈會自停(client.py:179-181);但在舊連線存活的主要洩漏情境下確實永不停,不影響 finding 成立。建議修法方向(替換前對舊 _sdk disconnect+logout、overnight pop 前先 disconnect)正確。

### 22. `backend/services/fubon_ws.py:160` — handle 先存進 _ws_handles 再 connect,connect 失敗後留下殭屍 handle → 永久靜默無行情

面向:error-handling | finder:concurrency | 驗證信心:high

_ensure_handle 先 `self._ws_handles[conn_idx] = ws` 才 await ws.connect();connect 拋例外時 handle 不移除。之後所有 subscribe 走 `if conn_idx in self._ws_handles: return ...` 拿到這個從未連線(或從未認證)的 ws,_real_subscribe 的 ws.subscribe 失敗被 swallow(只 log),refcount/_symbol_to_conn 卻照記 → 全部 symbol 標記已訂閱但實際零行情;且因為這條 ws 從未成功連線,不會有 disconnect 事件,_reconnect 永遠不會被觸發,直到重啟都收不到 tick。啟動時富邦短暫不通(login 慢/網路抖動)就會進入這個死狀態。

**建議修法**:connect 成功後才寫入 _ws_handles(失敗路徑 pop 掉);並讓 _real_subscribe 失敗時回滾 refcount/_symbol_to_conn 或記入待重試集合,由背景任務重試。

**驗證者**:逐行驗證屬實:fubon_ws.py:160 先把 ws 存進 _ws_handles 才在 162 行 connect,except 只 log 不 pop;_ensure_handle 開頭(152)以 dict membership 早退,之後永不重試 connect;subscribe()(96-100)在 _real_subscribe 前就寫入 refcount/_symbol_to_conn/_conn_subs,_real_subscribe 失敗只 log(135-136)不回滾。復原路徑方面:_reconnect 只由 disconnect 事件觸發,已查富邦官方 WebSocket 文件(getting-started),disconnect 事件定義為「連線/斷線時觸發」、connect 失敗只提到 error 事件,而本程式 on_error 只 log 不復原;grep 全 backend 無 health check,tests/ 也沒有 stock WS pool 測試。唯一修正:overnight.py 每天 8:25 會 pop 全部 handle 重連重訂閱,所以不是「直到重啟」而是「最久到隔天 8:25」——但盤中進入此態即整個交易日零行情且 status 仍顯示 OK,high 嚴重度仍成立。建議修法(connect 成功才入 dict、失敗回滾或待重試)合理。

### 23. `backend/services/fubon_ws.py:170` — _wire_callbacks 每次重連在同一個單例 ws client 上疊加重複 listener,tick 雙倍計入

面向:bug | finder:error-handling | 驗證信心:high

已驗證 .venv 內 fugle_marketdata/websocket/client.py 的 on() 走 pyee EventEmitter,以 callable 為 key 累積;_wire_callbacks 每次都建立新的 closure(on_message/on_disconnect/on_error),且 sdk.marketdata.websocket_client.stock 是 factory 快取的同一實例(專案註解也承認單例)。每次斷線重連 _ensure_handle 重新 _wire_callbacks → listener 數 +1:每筆 tick 被 _handle_raw_message 處理 N 次(ring_buffer 重複 append、_day_volume 與 volume_burst 視窗量加倍 → 量條件訊號誤觸發、前端 broadcast 重複),且 N 個 on_disconnect 在下次斷線時各排一個 _reconnect task,重連風暴隨斷線次數指數放大。對照組 fubon_futures_ws 用 bound method(pyee 以相等 key 去重)所以沒這問題。

**建議修法**:把 callback 改成 instance bound method(pyee 會以相等 key 覆寫),或在 _wire_callbacks 前先 ws.off 既有 listener,或記錄「此 client 已 wire 過」旗標只 wire 一次。

**驗證者**:已逐層從 .venv 實際原始碼驗證,finding 成立:(1) fubon_neo/adapter.py 的 WebSocketClientWrapper 對 stock 做 lazy cache、fugle_marketdata websocket/factory.py 以 type 快取 client,且 fubon_client.py 的 WS 重連路徑不重建 sdk(marketdata 只在 init_realtime 建一次)→ _ensure_handle 每次拿到同一實例與同一個 pyee EventEmitter;(2) pyee base.py 的 _add_event_handler 是 self._events[event][k]=v 以 callable 為 key,_wire_callbacks 每次建新 closure → 每次重連 +3 個 listener,fugle WebSocketClient.disconnect() 不清 ee;(3) _reconnect 先 pop handle 再 _ensure_handle → 必重跑 _wire_callbacks(fubon_ws.py:161),repo 內無任何 off() 或已 wire 旗標;(4) 對照組 fubon_futures_ws.py:101 用 bound method,pyee dict 以 (instance,func) 相等去重,確無此問題。實際比 finding 描述更嚴重:_wire_callbacks 在 ws.connect() 之前執行,_reconnect backoff 迴圈中每次失敗的嘗試也各疊一組 listener;N 個 on_disconnect 下次斷線各排一個 _reconnect task 形成倍增。後果(tick N 倍計入 ring_buffer/signal engine 量能視窗 → 量條件訊號誤觸發、前端重複 broadcast、重連風暴)鏈條完整,且斷線在實務必然發生(health_check missed pongs 會主動 disconnect)。建議修法三選一皆可行,最貼近對照組慣例的是改 bound method。

### 24. `backend/services/fubon_ws.py:271` — 重連放棄後(DEGRADED/CIRCUIT_OPEN)無任何自動恢復路徑,DEGRADED 分支連告警都沒有

面向:error-handling | finder:error-handling | 驗證信心:high

_reconnect 用盡 7 個 delay 或連續 5 次失敗開 circuit 後直接 return/設狀態,之後沒有任何排程(stock pool 沒有 reconcile loop,fubon_client._background_retry 只管 login 不管 WS)會再嘗試重連 — 全部訂閱行情靜默停止直到重啟,而 registry 還顯示已訂閱。其中 DEGRADED 分支(connect 成功讓 _ensure_handle 重置 _reconnect_failures=0、但 resubscribe 持續失敗的情境)連 notify_critical 都不會發,是完全無聲的死亡。

**建議修法**:circuit open / DEGRADED 後啟動低頻背景重試(如每 5 分鐘,比照 fubon_client._background_retry),並在 DEGRADED 路徑也發 notify_critical;health 端點已有 status 欄位,確保前端對 degraded 有可見提示。

**驗證者**:屬實。逐一排除候選恢復路徑後確認:盤中無任何機制會在 _reconnect 放棄後重試——top_gainers _sync_subscriptions 只 diff symbol 集合且已訂閱 symbol 走 need_real_sub=False 跳過;_ensure_handle 在 connect 前就把 handle 塞進 _ws_handles、connect 失敗不移除,後續新訂閱拿到死 handle 也不重連(subscribe 失敗 log 吞掉);fubon_client._background_retry 只管 login;session_reconcile_loop 是期貨專用。DEGRADED 分支(fubon_ws.py:271)確實無 notify_critical,且 finding 描述的計數器互動正確:_ensure_handle 成功重置 _reconnect_failures=0,resubscribe 持續失敗時計數每輪歸零再+1 永達不到 threshold 5,必然走無聲 DEGRADED 路徑。唯一不精確處:overnight_loop(overnight.py,main.py:79 有啟動)每天 8:25 會重連+重訂閱,所以是「死到次日 8:25 或重啟」而非永久——但盤中死掉等於整個剩餘交易時段無行情,且若失敗發生在 fubon SDK 非 OK 路徑 _ws_handles 為空,overnight 迭代空 dict 什麼都不重訂還回報成功,overnight 成功也不重設 pool._status。severity high 成立。

### 25. `backend/services/fubon_client.py:121` — relogin 直接覆蓋 self._sdk,舊 SDK 不登出、舊 WS 不斷線 → 連線洩漏與雙倍 tick

面向:error-handling | finder:error-handling | 驗證信心:high

_do_login_sync 每次建立新 FubonSDK 並覆蓋 _sdk,從不 logout 舊實例;overnight 8:25 每天呼叫 fubon.init() relogin 一次。舊 SDK 的 stock WS client(已 connect、已訂閱、callbacks 已 wire 進 WSPool._handle_raw_message)沒有任何人 disconnect — 只是被 pool._ws_handles.pop 丟掉參照。在富邦伺服器關閉舊 session 之前,新舊兩條 WS 同時推 trades 進同一個 ring_buffer → 整天雙倍量(day_volume/volume_burst 訊號誤觸發);長期下來每天洩漏一條登入 session 與 WS 連線,可能撞到富邦每帳號 5 條 WS 的上限(fubon_ws.py 註解引官方 rate-limit 文件)。

**建議修法**:relogin 前先對舊 sdk 的 websocket_client.stock/futopt 呼叫 disconnect、再 sdk.logout(),全部包 try/except;或把「relogin + WS 重建」整合成 WSPool 上的一個原子操作。

**驗證者**:真問題,三方證據確證。(1) 程式碼:fubon_client.py:107-121 的 _do_login_sync 每次 new FubonSDK 後直接覆蓋 self._sdk,從不 logout 舊實例;overnight.py:29-43 每天 8:25 先 fubon.init() relogin、再對 pool._ws_handles 純 pop 丟參照,全 repo grep 確認只有 WSPool.shutdown()(app 關閉)會對 ws 呼叫 disconnect,relogin 路徑零清理。(2) SDK 原始碼(backend/.venv 安裝的 fubon-neo 2.2.8):sdk.py init_realtime 每次建新 MarketData,adapter.py:149 build_websocket_client 每次 new 一個全新 WebSocketClient——fubon_ws.py:27 「process-level singleton」註解只在同一 SDK 實例內成立,relogin 後是全新 WS 連線,無法用此反駁。(3) 官方文件(rate-limit.txt / making-connection.txt 已 WebFetch):每帳號上限 5 條 WS、超限回 error 1001;官方範例正是重複呼叫 init_realtime 建 5 條並存連線,且無任何「relogin 踢舊 session/舊連線」機制記載。故舊 ws(callbacks 仍 wire 進同一個 pool._handle_raw_message、ws thread 持參照不被 GC)持續推 trades 進 ring_buffer → 新舊雙倍 tick 汙染量能訊號;每天洩漏一條連線,約 5 天撞官方 5 連線上限。另舊 ws 的 on_disconnect 仍掛著,舊連線被動斷線時會觸發 _reconnect 把新 handle pop 掉重建,額外 churn。唯二小修正不影響成立:pop 發生在 overnight.py 而非 WSPool 內部;雙倍 tick 持續多久取決於富邦何時關舊連線(文件未載,finding 已自標此前提)。建議修法方向正確:relogin 前先 disconnect 舊 ws + logout 舊 sdk(各包 try/except),或把 relogin+WS 重建整合為原子操作。

### 26. `backend/services/capital_client.py:158` — 群益回報主機斷線完全無偵測/無重連/無告警,且重連重播會讓 store 雙計成交

面向:error-handling | finder:error-handling | 驗證信心:high

connect_reply 只在 _init_com 做一次,失敗僅 warning 且 status 仍轉 ok;_ReplyEvents 只處理 OnConnect,回報主機盤中斷線沒有任何 handler — 委託/成交回報靜默停止,store 凍結在斷線前狀態,前端持續輪詢拿到過期委託/部位卻顯示健康。capital_store 的 docstring 自己警告:聚合非冪等,若(SKCOM 自動或未來手動)重連重播當日 backlog 而沒先 clear(),filled_qty 會重複累計 → 活單被誤標全部成交而失去刪改按鈕。

**建議修法**:在 _ReplyEvents 掛斷線事件(SKReplyLib 的 disconnect 回呼,需對官方 docx 確認事件名)→ 降 status/推 WS 告警,並在重連成功後先 store.clear() 再吃 backlog 重播;status 端點同步反映「回報通道」與「下單通道」兩個獨立健康度。

**驗證者**:核心缺陷在程式碼可直接驗證:connect_reply 全 repo 僅 _init_com 一處(capital_client.py:158),rc!=0 只 warning、status 仍無條件轉 ok;_ReplyEvents(capital_com.py:150-170)只有 OnReplyMessage/OnConnect/OnNewData,盤中回報主機斷線零偵測、零告警、零重連;/api/capital/status 只回單一 status,init 後恆為 ok。委託聚合唯一來源是 OnNewData,斷線後委託/成交狀態靜默凍結,前端照常顯示健康並保留刪改按鈕——真錢面板下已成交的單可能仍顯示可刪改,符合 capital_* 從嚴標準。雙計部分:capital_store.apply_reply 對 D 事件累加 filled_qty 確屬非冪等,docstring 與 spec(2026-06-10-capital-orders-reply-display-design.md:126)自承重播前必須 clear();惟「SKCOM 自動重連重播」是否會發生無法從 repo 驗證,該半句屬條件性風險而非現行必發 bug。另一處輕微誇大:部位有 SKOrderLib 的 60s 定時重查(_maybe_query_balance),不完全依賴回報連線,「部位凍結」偏重——但委託面凍結+健康度誤報已足以支撐 high 嚴重度。

### 27. `backend/services/overnight.py:42` — 每日 8:25 重登入洩漏舊 SDK:舊 session 不 logout、舊 WS 不 disconnect,吃掉富邦連線額度

面向:rate-limit | finder:rate-limit | 驗證信心:high

run_overnight_reconnect() 呼叫 fubon.init() → _do_login_sync() 無條件 new 一個 FubonSDK() 蓋掉 self._sdk,舊 SDK 從未 logout;接著把 pool._ws_handles pop 掉(不 disconnect)再 _ensure_handle 拿新 SDK 的 ws。舊 WS client 的 run_forever 執行緒、每 30 秒自我重排的 ping Timer、和仍掛著的 on_disconnect handler 全部留著。官方 rate-limit 文件明定 WS「每帳號可同時開啟 5 連線」、短時間大量建線會被判攻擊回 404:24/7 跑數天後洩漏的連線/半死連線會吃滿帳號額度,8:25 重連開始失敗。更糟的是舊連線之後被 server 收掉時,殘留的 on_disconnect 觸發 _reconnect(conn_idx),把『新』handle pop 掉(又不 disconnect)並在新 singleton 上重複 wire callbacks + 對已連線的 WebSocketApp 再跑一條 run_forever 執行緒。

**建議修法**:重登入前先完整收尾:對所有 _ws_handles 做 disconnect(復用 pool.shutdown 的迴圈)、await to_thread(old_sdk.logout) 再建新 SDK。fubon_client._do_login_sync 內也應在 self._sdk 已存在時先 logout 舊的,讓任何呼叫 init() 的路徑都不洩漏。

**驗證者**:逐項驗證後無法反駁,finding 成立。(1) 洩漏路徑屬實:fubon_client.py:_do_login_sync(107-121 行)無條件 `sdk = FubonSDK()` 後 `self._sdk = sdk`,全檔只有 shutdown() 會 logout,overnight 重登入路徑舊 SDK 從未 logout;overnight.py:42 `pool._ws_handles.pop(conn_idx, None)` 只丟引用、不呼叫 disconnect(對照 fubon_ws.py shutdown() 是有 disconnect 迴圈的)。(2) SDK 內部行為屬實(讀 .venv 內 fubon_neo 2.2.8 + vendored fugle_marketdata 2.4.1 原始碼):WebSocketClient.connect() 起 `Thread(target=run_forever)`;adapter.build_websocket_config 對富邦客戶固定 `HealthCheckConfig(enabled=True, ping_interval=30000)`,__send_ping 用 Timer 每 30 秒自我重排;新 FubonSDK.init_realtime 建全新 MarketData→全新 WebSocketClient,舊 client 的執行緒/Timer/pyee listener 全數殘留,且 pyee 的 `.on()` 是 append 不是 replace——舊連線日後被 server 收掉時,殘留 on_disconnect 觸發 _reconnect(conn_idx),會 pop 新 handle(再洩一條)、對同一 ws 重複 _wire_callbacks(訊息 listener 翻倍=tick 重複進 ring_buffer)、並對已連線的 WebSocketApp 再跑一條 run_forever 執行緒,連鎖惡化部分也屬實。(3) 已照工作流程 WebFetch 官方 rate-limit 文件(market-data/rate-limit.txt)確認:「單一連線 200 訂閱數;可同時開啟 5 連線」、短時間大量建線會被判惡意攻擊「阻擋您的連線請求(404連線錯誤)」。(4) 情境會發生:main.py:79 lifespan 確實 create_task(overnight_loop()),每天 8:25 必跑。唯一不確定處是富邦 server 是否會在重登入時主動踢舊行情 WS——但兩種分支都是真問題:不踢=連線洩漏吃 5 條額度,踢=觸發上述 on_disconnect 連鎖(重複 listener + 雙 run_forever),不影響 isReal 判定。建議修法方向(重登入前 disconnect 全部 handle + logout 舊 SDK)與現有 pool.shutdown()/fubon.shutdown() 程式碼一致,可行。

### 28. `backend/services/fubon_ws.py:45` — WSPool 全檔零測試:refcount、容量、訊息解析、重連/熔斷狀態機都是可 fake 的純邏輯卻沒有任何測試

面向:test-gap | finder:test-gaps | 驗證信心:high | 重複回報:3 個 finder

tests/ 裡沒有 test_fubon_ws.py。subscribe/unsubscribe 的 refcount 語意(兩 owner 同 symbol 取消其一不互踩)、容量滿回滾 refcount、_handle_raw_message 的欄位缺漏防護、_reconnect 的 backoff/重訂閱/熔斷計數,全部是不碰真 SDK 就能測的本地邏輯(monitor_list route 測試已示範如何 mock ws_pool)。這是行情的唯一入口:這裡靜默壞掉=所有訊號、分時圖、書籤報價一起出錯。上面兩個 high bug(重複 listener、futures 字串解析)正是這個缺口放過去的實證。

**建議修法**:至少補四類:1) refcount——A/B 兩 owner 訂同 symbol,B 退訂後仍只有一次 real unsubscribe、A 退訂後才 discard ring_buffer;2) 容量滿 raise 且 refcount 回滾(目前只在 route 層測 503);3) _handle_raw_message 餵字串/dict/缺 symbol/缺 price 的官方格式樣本,斷言 ring_buffer 與 on_tick 行為;4) _reconnect 用假 ws + 假 sleep 驗證重訂閱清單與熔斷計數歸零/開路。

**驗證者**:已實際查證:backend/tests/ 沒有 test_fubon_ws.py;test_fubon_futures_ws.py 測的是 fubon_futures_ws.py 的 target_after_hours_flag,與 WSPool 無關;其餘引用 ws_pool 的測試(monitor_list/bookmarks/lifecycle_sync/config_io)全是把 pool mock 成 fake_pool/AsyncMock,沒有任何測試直接驗 WSPool。且 finding 對可測性的判斷正確——refcount(subscribe/unsubscribe)、容量滿回滾 refcount(fubon_ws.py:91-95)、_handle_raw_message 解析(188-238)、_reconnect backoff/熔斷(240-271)都是本地純邏輯,真 SDK 呼叫隔離在 _real_subscribe/_real_unsubscribe/_ensure_handle 可 monkeypatch。「行情唯一入口」措辭略誇大(另有 REST quote 與 futures WS),但個股 tick→ring_buffer→signal_engine→前端 broadcast 全經此檔,影響面評估成立;程式碼也佐證重複 listener 風險(websocket_client.stock 是 singleton,reconnect 後 _wire_callbacks 會對同一 ws 物件重複掛 callback),屬有測試即可攔截的缺陷。test-gap 屬實。

### 29. `backend/services/capital_factory.py:27` — CAPITAL_MAX_QTY / CAPITAL_MAX_AMOUNT 未設定時預設 0=無上限,真錢安全閘 fail-open

面向:error-handling | finder:type-safety | 驗證信心:high | 重複回報:2 個 finder

SafetyConfig 以 int(os.getenv("CAPITAL_MAX_QTY", "0") or 0) 組裝,而 check_stock_order / check_correct_price 的閘是 `if cfg.max_qty and ...`、`if cfg.max_amount and ...` — 0 等於停用該閘。在 ORDER_ENABLED=true(目前 .env 已上膛)而漏設上限的環境,任何數量/金額的單都直接放行;改價金額閘也同時失效。對真錢寫入而言,「忘了設上限」應該 fail-closed 而不是無上限。另外 int()/float() 解析失敗會讓 get_capital() 在每個 capital route 丟未捕捉 ValueError(500),與 lifespan 的「Capital startup skipped」訊息不一致,難以診斷。

**建議修法**:ORDER_ENABLED=true 時要求 max_qty/max_amount 必須為正值,否則拒絕建立 client(或強制給保守預設並大聲 log);env 解析失敗時把錯誤存進 client 狀態而非讓 route 500。

**驗證者**:查實為真,且比 finding 描述更嚴重:capital_safety.py:48/51/69 的 `if cfg.max_qty and ...`/`if cfg.max_amount and ...` 在 0 時整道閘跳過=無上限(fail-open),但 backend/.env.example:53 明文註解「單筆數量上限(0=擋)、單筆金額上限(0=擋)」且預設值就是 0——文件宣稱 fail-closed、實作卻 fail-open,照 example 抄設定只翻 ORDER_ENABLED=true 的人會誤以為全擋實則無限額放行。test_capital_safety.py 無任何 max_qty=0/max_amount=0 案例,測試也沒鎖住這個語意。次要點亦成立:main.py:86-101 lifespan 雖 catch 例外,但 _client 留 None,routes/capital.py:29/37/52/59 每個 route 再呼叫 get_capital() 會重新建構,int()/float() 解析失敗丟未捕捉 ValueError→500。唯一緩解:目前實際 backend/.env 已設 MAX_QTY=1/MAX_AMOUNT=1000000,當下環境未暴險,但不構成對設計缺陷的反駁。鑑於 capital_* 從嚴標準(該擋未擋),high 甚至可上修 critical。

### 30. `backend/services/local_store/config_store.py:231` — import_config 對 active_signals 等清單零驗證,壞匯入檔半套用後可讓後端每次啟動都起不來

面向:type-safety | finder:type-safety | 驗證信心:high

POST /api/config/import 只驗 schema_version,四個清單 `data.get(k, [])` 原樣落地。匯入的 active_signal 缺 id/name/filter_json/scope/enabled 任一鍵,後續 list_active_signals 的 s["enabled"](line 137)或 signal_engine._row_to_active 的 r["id"] 直接 KeyError;filter_json 形狀不對則 ActiveSignalOut 建構丟 ValidationError。時序是:config 已寫進磁碟 → resync_from_config 炸 → import 回 500 但設定已被取代;之後每次重啟 lifespan 的 engine.start() → refresh_active_signals 再炸 → FastAPI startup 失敗,後端 brick 到手動修 config.json 為止。bookmark/monitor 清單缺 symbol/group_id 鍵也會在 lifecycle_sync/route 各處 [] 取值炸掉。

**建議修法**:import_config 逐筆用 pydantic(ActiveSignalCreate + id/created_at、書籤/監聽各自的最小 schema)驗證,任一筆不合法整批拒絕並 400,不落地;不依賴匯出端自律。

**驗證者**:逐檔驗證後攻擊鏈完整成立:(1) config_store.py:220-234 import_config 只驗 schema_version,四清單原樣落地並先 _persist();routes/config_io.py 的 payload 是裸 dict、只 catch ValueError。(2) 落地後才跑 resync_from_config → signal_engine.refresh_active_signals,其中 list_active_signals 的 s["enabled"](config_store.py:137)與 _row_to_active 的 r["id"]/r["name"]/r["filter_json"]/r["scope"](signal_engine.py:116-117)皆硬取鍵,缺鍵即 KeyError、filter_json 形狀錯則 ActiveSignalOut ValidationError——此時壞 config 已持久化,import 回 500。(3) main.py:58/76 的 engine.start() 與 resync_from_config() 在 lifespan 內無 try/except,例外直接讓 FastAPI startup 失敗;ConfigStore.load() 的壞檔復原只接 JSONDecodeError/OSError,壞匯入檔是合法 JSON 照常載入,後端每次重啟都炸到手動修 config.json(或從 config.backup-N.json 救回)為止。(4) lifecycle_sync.py:30-38 的 it["symbol"]/m["symbol"] 硬取鍵且 try 只接 RuntimeError,bookmark/monitor 缺鍵同樣穿透。(5) test_config_store.py 只測 schema_version 拒絕與合法 round-trip,無畸形項目測試。唯一緩解是單人本機工具,但匯出檔設計上可攜可手改且後果是啟動 brick,severity high 合理。

### 31. `backend/services/signal_engine.py:96` — refresh_active_signals 載入規則是全有全無,一筆壞規則讓所有規則失效甚至阻斷啟動

面向:error-handling | finder:type-safety | 驗證信心:high | 重複回報:2 個 finder

`self._active = [self._row_to_active(r) for r in rows]` 一筆 ValidationError/KeyError 就讓整個 list comprehension 失敗:啟動路徑(engine.start 內 await)直接讓 lifespan 失敗、後端起不來;route 路徑(monitor/watchlist 增刪有包 try)則 refresh 被吞、引擎繼續用舊規則跑,使用者剛做的變更靜默不生效。磁碟上的 filter_json 可能來自舊 schema_version 或匯入檔,不是永遠可信。

**建議修法**:逐筆 try 包 _row_to_active,壞列 log warning 後跳過(載入其餘規則),並把壞列數量曝露到 health();與 import 驗證(另一條)互補。

**驗證者**:三條路徑實讀程式碼全數證實。(1) 啟動阻斷:main.py:58 await engine.start() 與 signal_engine.py:79 的 refresh 皆無 try,line 96 裸 list comprehension,一筆壞列拋例外即 lifespan 失敗、後端起不來。(2) 壞列確實可落盤:config_store.py import_config(L220-234)只驗頂層 schema_version,active_signals rows 原樣寫穿、零逐筆驗證;ConfigStore.load 只接 JSONDecodeError,row 結構錯不擋;本機 JSON local-first 也可能被手改。_row_to_active 建 ActiveSignalOut(filter_json 是強型別 ActiveFilter 含 conditions_non_empty validator)缺鍵 KeyError、結構錯 ValidationError 都會拋;甚至 list_active_signals 的 s["enabled"](config_store.py:137)缺鍵就先炸。(3) route 靜默吞:monitor_list.py:62-66/79-83 與 watchlist.py:93-97/116-120 的 refresh 包 try 只 log warning,引擎繼續用舊規則,變更靜默不生效;active_signals.py:39/56/66 未包 try,寫入落盤後 refresh 炸 500、引擎與磁碟分歧。唯一反駁路線「rows 必經 Pydantic route 寫入」被 import_config 繞過驗證的事實推翻。建議修法(逐筆 try + 跳過 + health 曝露壞列數)合理。

## Medium(42 項)

### 32. `backend/services/fubon_ws.py:264` — circuit breaker 開了永遠不關,盤中觸發後行情死到隔天 8:25;迴圈尾的 DEGRADED 幾乎不可達

面向:error-handling | finder:fubon-market | 驗證信心:high | 重複回報:2 個 finder

_reconnect 累計 5 次失敗進 CIRCUIT_OPEN 後直接 return,沒有任何半開重試排程;之後也不會再有 disconnect 事件來重新觸發(連線已死),行情就停到隔天 overnight reconnect 或人工重啟。另外 RECONNECT_DELAYS 有 7 格但 threshold=5,正常路徑會在第 5 次失敗就 return,line 271 的 DEGRADED 只有 _reconnect_failures 中途被 _ensure_handle 成功重置過才到得了——而真到了 DEGRADED 也一樣沒人重試、沒 alert。

**建議修法**:CIRCUIT_OPEN 後排程一個固定間隔(如 5 分鐘)的半開探測 task 嘗試重連,成功才關 circuit;DEGRADED 尾巴要嘛移除、要嘛同樣接上重試與 alert。

**驗證者**:嘗試反駁失敗,finding 屬實。逐點驗證:(1) circuit 開了沒人關——grep 全 backend,_status 唯一設回 OK 的地方是 _reconnect 成功路徑(fubon_ws.py:258),CIRCUIT_OPEN 後 return(line 270)沒有任何半開探測排程;連線已死,SDK 不會再發 disconnect 事件來重新觸發 _reconnect。(2) 之後的新 subscribe 也救不回來——_ensure_handle 在 connect 之前就把 handle 塞進 _ws_handles(line 160),connect 失敗時 stale handle 留著,後續 _real_subscribe 拿到死 handle、subscribe 失敗只 log(line 136)不重連。(3) 唯一自動恢復確實是隔天 8:25 的 overnight_loop(overnight.py:69-81),且 run_overnight_reconnect 連 _status 都不重置(只有 _ensure_handle 重置 _reconnect_failures),恢復後 status 還會永遠卡在 circuit_open——比 finding 說的還多一個瑕疵。(4) DEGRADED(line 271)可達性分析正確:threshold=5 < DELAYS 7 格,只有中途 _ensure_handle 成功(line 164 重置計數)但重訂閱失敗的情境才會走完 7 格落到 DEGRADED,該尾巴無 alert、無重試。唯一要釐清的是 CIRCUIT_OPEN 本身有打 alerts.notify_critical(Discord webhook,fubon_ws.py:266),finding 也只說 DEGRADED 沒 alert,陳述準確。severity medium 合理(有每日 overnight 恢復 + circuit open 有告警,但盤中觸發 = 行情死一整天)。

### 33. `backend/services/fubon_ws.py:177` — on_disconnect 每次都 spawn 新 _reconnect task 無去重,且 create_task 不保留強參考

面向:concurrency | finder:fubon-market | 驗證信心:high | 重複回報:3 個 finder

底層 websocket-client 多次觸發 disconnect、或重連期間又斷線時,會有多個 _reconnect(conn_idx) 並行:各自 pop handle、各自 connect、各自重訂閱,加上重複 _wire_callbacks 的問題互相放大。另外 call_soon_threadsafe(asyncio.create_task, coro) 產生的 task 沒有任何強參考,event loop 只持弱參考,task 可能在 await 中被 GC 掉(CPython 文件明載的陷阱)——_reconnect 睡在 backoff sleep 時被回收等於重連靜默消失。同樣 pattern 也用在每筆 tick 的 _on_tick 與 broadcast task。

**建議修法**:per-conn 保存 reconnect task 參考,若 task 還在跑就直接 return(去重 + 防 GC);tick/broadcast 路徑可改用 asyncio.run_coroutine_threadsafe(回傳 future 有強參考)或集中收進一個由常駐 task 消費的 queue。

**驗證者**:確認為真問題。(1) 去重缺失屬實:fubon_ws.py:174-179 的 on_disconnect 每次觸發都無條件經 call_soon_threadsafe spawn 新 _reconnect task,無任何 in-flight 檢查;不需假設 SDK 重複觸發,只要 _reconnect 在 connect 成功後、subscribe(to_thread)未返回前連線再斷,新舊兩個 _reconnect 就會並行——各自 pop handle、各自對 process-level singleton ws(檔頭註解 27-29 行自承)connect+重訂閱+重複 _wire_callbacks,並競爭共享的 _reconnect_failures 計數。(2) repo 內旁證:同專案 fubon_futures_ws.py:44/184/193 已用 self._reconnecting flag 做去重,證明 codebase 自己認定此 pattern 需要去重,fubon_ws.py 是漏掉而非設計。(3) GC 弱參考部分:CPython 文件確實明載 create_task 結果需保留強參考;實務上睡在 asyncio.sleep 的 task 通常被 TimerHandle callback 鏈間接持有、被回收機率偏低,此腿單獨看偏理論,但建議修法(per-conn 保存 task 參考)一次解決去重+GC,屬標準做法。嚴重度 medium 合理:此為富邦行情訂閱路徑非群益下單路徑,最壞為重連風暴/重複訂閱/circuit breaker 計數錯亂,不涉錯單。

### 34. `backend/services/ring_buffer.py:56` — append/window/latest 取 lock 與取 buffer 兩步非原子,unsubscribe 的 discard 可使 SDK callback 執行緒爆 KeyError

面向:concurrency | finder:fubon-market | 驗證信心:high | 重複回報:4 個 finder

三個方法都先 self._locks.get(symbol) 再 self._buffers[symbol]。discard()(最後一個 owner unsubscribe 時從 event loop 執行)在兩步之間把 entry 移除的話,_buffers[symbol] 直接 KeyError。append 的 KeyError 發生在富邦 SDK callback 執行緒裡,_handle_raw_message 沒有 try/except,例外會往上穿過 pyee emit 進 websocket-client 的 callback 路徑,可能中斷該連線的訊息迴圈——一次 unsubscribe race 可能影響整條行情線。

**建議修法**:改成 buf = self._buffers.get(symbol),None 就 return(append 比照現有 lock is None 的 drop 路徑);另在 _handle_raw_message 外層包 try/except 兜底,callback 執行緒永不外漏例外。

**驗證者**:race 機制確認為真,但後果被誇大、嚴重度應降為 low。已驗證:(1) append 在富邦 WS callback 執行緒跑、discard 在 event loop 執行緒由 unsubscribe 呼叫(fubon_ws.py:116),且 discard 在 _real_unsubscribe 之後才執行,in-flight 訊息可同時進 callback——append 的 _locks.get → _buffers[symbol] 兩步不持 _registry_lock,中間被 discard 插入確會 KeyError(ring_buffer.py:52-56),真 bug。(2) 但「中斷整條行情線」不成立:實讀安裝依賴,pyee 11.1.1 base.py 的 _emit_run 確實不接例外(會穿出 ee.emit),但 websocket-client 1.9.0 _app.py:610-620 的 _callback 對 on_message 包了 try/except——例外只 log error 並轉 on_error handler,run_forever 訊息迴圈不中斷、連線存活;實際損害僅為丟一筆剛退訂標的的 tick + error log。(3) window/latest 的兩步讀不可利用:僅被 signal_engine 的 async coroutine 呼叫,與 discard 同在 event loop 執行緒,discard 同步無 await 不可能插入。建議修法(_buffers.get + None return)正確且應做;外層 try/except 屬可選兜底。

### 35. `backend/ws_broadcaster.py:34` — 每 tick 一個 broadcast task 且對所有 client 逐一 await,慢 client 造成 task 無上限堆積與同一 WebSocket 並發 send

面向:concurrency | finder:fubon-market | 驗證信心:high | 重複回報:2 個 finder

fubon_ws 每筆 tick 都 create_task 一個 broadcast(),task 內對每個 client 依序 await send_json:一個 TCP backpressure 的慢 client 會卡住該 task,後續 tick 的 broadcast task 持續堆積(無界記憶體+延遲),其他健康 client 也被拖慢;同時多個 broadcast task 並發對同一個 Starlette WebSocket 呼叫 send_json,並發 send 的順序/安全性沒有保證,tick 可能亂序送達前端(分時圖最後一根 K 棒會跳動)。

**建議修法**:改 per-client bounded queue + 每 client 一個常駐 sender task(queue 滿就丟舊 tick 或踢 client),broadcast 只做非阻塞 put;最小修法也應 gather 並行送 + per-client send lock。

**驗證者**:程式碼核實屬實:fubon_ws.py:236-238 每筆 tick 都 call_soon_threadsafe(asyncio.create_task, broadcast(...)),無 in-flight 上限;ws_broadcaster.py:32-36 對所有 client 逐一 await send_json,無 per-client lock 也無並行送。uvicorn 的 WS send 在 write buffer 滿時會 await(TCP backpressure),所以一個慢 client 確實會卡住該 broadcast task,後續 tick 的 task 無界堆積,且後開的 task 可先於先開的 task 對同一 ws 送出較新 tick——同 WebSocket 並發 send 與 tick 亂序(分時圖最後一根 K 棒跳動)在程式碼層面都成立。同一模式也出現在 fubon_futures_ws.py 與 main.py 的 capital broadcast 注入。唯一緩解因素是本機單人部署、client 通常為 1 個 localhost 前端,backpressure 機率低,故 medium 嚴重度恰當而非高估。不涉富邦 SDK 行為假設(callback 跨 thread 橋接為程式碼可見事實),無需查官方文件。

### 36. `backend/services/fubon_client.py:148` — rate limiter 的阻塞式 acquire 跑在 to_thread,額度滿時會占光預設 thread pool 拖垮全部 to_thread 操作

面向:rate-limit | finder:fubon-market | 驗證信心:high

intraday_quote 等 wrapper 用 await asyncio.to_thread(acquire),acquire 不夠 token 時 time.sleep 等待,期間整條 default executor worker(預設 min(32, cpu+4) 條,全 app 共用)被占住。snapshot poller 對多 symbol 連打或前端多頁輪詢時,排隊等 token 的 acquire 可占滿 pool,連帶卡住所有其他 to_thread——包括 WS subscribe/reconnect 與 relogin,行情側被 REST 限流連坐。

**建議修法**:為 event loop 路徑提供 async 版 acquire(在 lock 內算 wait、用 asyncio.sleep 等待),REST wrapper 改 await limiter.acquire_async();sync 版保留給真正的 thread 呼叫端。

**驗證者**:確認為真問題。(1) rate_limiter.py:67-81 的 acquire() 在等 token 時用 time.sleep 阻塞迴圈,占住整條 thread;(2) 全 backend grep 無自訂 executor,所有 to_thread 共用 default pool(min(32, cpu+4)),包括 fubon_ws.py 的 subscribe/connect/reconnect(131/143/162/254)、relogin(fubon_client.py:68)、期貨 WS connect;(3) 飽和情境真實存在:routes/quote.py:93 的 /api/quotes/snapshot 用 gather 對最多 50 檔並發呼叫 intraday_quote(每檔先 to_thread(acquire)),前端 useSnapshotCache.ts 確實湊滿 50 檔一批、多 chunk 還 Promise.all 平行打,PositionsList 另有 30s 輪詢——default limiter 5/s+capacity 5 下,單批 50 檔即產生 ~45 條 sleeping acquire、需 ~9 秒排空,超過 32 worker 上限,期間 WS 重連/relogin 等全部 to_thread 操作 FIFO 排隊被連坐。純 asyncio/threading 本地邏輯,不涉 SDK 行為假設,免查富邦文件。建議修法(async 版 acquire 用 asyncio.sleep)正確,medium 嚴重度合理(突發窗口劣化、可自行恢復、不涉下單路徑)。

### 37. `backend/services/fubon_client.py:116` — apikey_dma_login/apikey_login 回傳 Result 不會 raise,_do_login_sync 沒檢查 is_success

面向:error-handling | finder:fubon-market | 驗證信心:high

官方 Python 文件明載登入回傳 Result 物件(is_success/message),失敗不丟例外。_do_login_sync 直接忽略回傳值,憑證錯誤時要靠後續 init_realtime 的 exchange_realtime_token 間接失敗才被 retry 邏輯捕捉——錯誤訊息變成 token exchange 的內部錯誤,遮蔽真正原因(API key 無效/憑證錯),troubleshoot 會被誤導;且若某些失敗型態下 exchange 沒炸,_sdk 會被設成未登入的 SDK、status=OK 但所有行情呼叫壞掉。

**建議修法**:接住回傳值:result = sdk.apikey_dma_login(...);if not result.is_success: raise RuntimeError(f"Fubon login failed: {result.message}")。

**驗證者**:確認為真問題。(1) fubon_client.py:113/116 兩條登入路徑(apikey_login/apikey_dma_login)都直接丟棄回傳值,grep 整個 backend 找不到任何 is_success 檢查。(2) 官方 Python 文件(trading/library/python/login/loginAPIKey.txt)明載回傳 Result 物件(is_success/message/data),官方範例即 if result.is_success else print(result.message)——憑證/金鑰錯誤走回傳值、不丟例外。(3) 本機安裝的 fubon-neo 2.2.8 以 inspect.getdoc 實證:apikey_dma_login 與 apikey_login 的 docstring 都是「Returns: CustomReturnType with list of Account objects」、無 raises 描述,DMA 路徑同樣是 Result 模式。因此登入失敗時 _do_login_sync 不會在登入行報錯,只能靠 init_realtime 的 token exchange 連帶失敗才被 _login_with_retry 捕捉,_last_error 記成 token exchange 內部錯誤、遮蔽真因(API key 無效);若 exchange 在某些失敗型態不炸,_sdk 會被設成未登入的 SDK、status=OK 但所有行情呼叫壞掉。建議修法(接住 result,not is_success 即 raise RuntimeError)正確,raise 會被既有 retry/alert 機制自然接住。severity medium 合理(僅行情、不涉下單,屬可觀測性/除錯誤導問題)。

### 38. `backend/services/fubon_futures_ws.py:61` — 合約換月後 WS pool 永遠訂著舊 symbol,直到重啟

面向:bug | finder:fubon-futures | 驗證信心:high

pool 的 _symbol 只在 startup 時由 main.py 用 resolve_active_symbol() 的結果 start() 一次;reconcile_session 每分鐘只對齊 afterHours 旗標,不會重新解析近月。結算日後 REST 端(1h cache 過期)會切到新合約,但 WS 仍訂閱已下市的舊合約——_handle_message 又以 `symbol != self._symbol` 過濾,即使富邦推了什麼也不會錯播,結果是每月結算日 15:00 起即時推送整段靜默消失,直到後端重啟。

**建議修法**:在 session_reconcile_loop(或 reconcile_session 內)定期呼叫 resolve_active_symbol(),結果與 pool._symbol 不同時呼叫 pool.start(new_symbol) 切換;resolve 已有 1h cache,額度成本可忽略。

**驗證者**:反駁失敗,finding 屬實。grep 全 backend 確認 get_futures_ws_pool().start(symbol) 唯一呼叫點在 main.py:65(startup);reconcile_session(fubon_futures_ws.py:61-70)只重置 reconnect 計數並走 _ensure_subscribed_for_now,而後者(L74-89)只對齊 afterHours 旗標,_symbol 永不更新;session_reconcile_loop 也從不呼叫 resolve_active_symbol。REST 端 resolve_active_symbol(fubon_futures.py:24-36, 1h TTL)以 expiry > today 排除已結算合約、會切新近月,但 WS 停留舊 symbol,且 _handle_message L154 以 symbol != self._symbol 過濾,換月後即時推送整段靜默直到重啟。額外事證:main.py:68 log 寫「will retry on reconcile」,但 _ensure_subscribed_for_now 在 _symbol is None 時直接 return,startup 解析失敗同樣永久靜默——原作者預期 reconcile 會補救 symbol 但未實作。核心是純本地狀態機邏輯,不依賴富邦 SDK 行為假設,無需查官方文件即可定論。medium 嚴重度合理(每月一次、僅行情非下單、重啟可恢復)。

### 39. `backend/services/fubon_futures.py:230` — fetch_candles 兩個 session 全失敗時回空陣列,API 回 200 空圖,無法與「真的沒資料」區分

面向:error-handling | finder:fubon-futures | 驗證信心:high

_fetch 把除 ValueError 外的所有例外吃掉回 [],日夜盤同時失敗(富邦 REST 整段故障、token 失效)時 /api/mxf/candles 回 200 + candles:[],前端只會看到圖表清空,和「剛換月新合約還沒成交」「假日無資料」無法區分,行情中斷被靜默吞掉。

**建議修法**:_fetch 回傳 (ok, rows) 或在兩段都失敗時 raise,route 轉成 502/503 帶 error code;部分失敗(單一 session 掛)可維持降級合併但 log 升為 error 或在 response 帶 degraded 旗標。

**驗證者**:事實鏈逐項驗證成立:(1) fubon_futures.py:226-231 的 _fetch 除 ValueError 外吞掉所有例外回 [],日夜盤同時失敗時 merge 出空陣列;(2) routes/mxf.py:40-46 直接回 200 + candles:[],無 degraded/error 欄位,與同檔 resolve_active_symbol 失敗丟 503 的行為不一致;(3)「resolve 會先 503 擋掉」的反駁不成立——symbol 有 1h cache,且前端 useMXFCandles.ts:32 第一次成功後輪詢都帶 symbol param 繞過 resolve,穩態下富邦 REST 全掛只會拿到 200 空陣列;(4) 前端 useMXFCandles.ts:35-42 成功路徑直接覆寫 candles=[] 且 error=null,已畫好的圖 30s 後被清空無提示,tf≠1 時 WS 推送在空陣列下也被丟棄(line 75),無法靠 WS 補救;(5) test_fubon_futures.py 無測試把此行為編碼為刻意設計。與「新合約沒成交」「假日無資料」確實不可區分,行情中斷被靜默吞掉。medium/error-handling 定級合理(行情顯示路徑、非下單路徑)。

### 40. `backend/services/signal_engine.py:163` — 盤中編輯任何規則會清掉 _day_volume 當日累積量

面向:bug | finder:signal-engine | 驗證信心:high | 重複回報:2 個 finder

_day_volume.clear() 放在 _refill_field_cache 裡,但 _refill_field_cache 不只在跨午夜被呼叫——每次 active_signals / monitor_list / watchlist 的 CRUD route 都會經 refresh_active_signals 觸發它。盤中編輯一條規則,所有 symbol 的今日累積量歸零、之後只從當下重新累積,day_volume 條件靜默低估。_reset_daily_strategy_state 的註解已明確指出「_refill_field_cache 也在規則編輯時被呼叫,會誤清盤中累積狀態」,卻漏了搬走這行——和自家設計筆記矛盾。

**建議修法**:把 self._day_volume.clear() 從 _refill_field_cache 移到 _reset_daily_strategy_state(只在 heartbeat 跨午夜分支執行),跟 limit-up latch / breakout arming 同一個 daily reset 路徑。

**驗證者**:實證確認為真問題。(1) signal_engine.py:163 的 _day_volume.clear() 在 _refill_field_cache 內,而 refresh_active_signals(line 100)每次都呼叫它;(2) grep 證實 active_signals.py:39/56/66、monitor_list.py:64/81、watchlist.py:95/118 的每個寫入 route 都 await refresh_active_signals——盤中任何規則/監聽/自選編輯都會清空全部 symbol 的當日累積量;(3) _day_volume 只靠正盤 tick 累積(line 230)、無任何回補來源,清掉後 day_volume 條件(line 649)靜默低估、訊號漏觸發;(4) _reset_daily_strategy_state(line 400-401)註解明寫 daily 狀態不可放 _refill_field_cache 因規則編輯也會呼叫,limit-up latch / breakout arming 已照辦,唯 _day_volume.clear() 漏搬,與自家設計矛盾屬實。反駁角度也排除:清空非為逐出已移除 symbol(stale entry 在 field_cache 是逐一 pop,_day_volume 殘留無害),且 watchlist 編輯也觸發整批清空,顯非刻意。建議修法可行——跨午夜 heartbeat 分支(line 197-199)同時執行兩個方法,搬移後 daily reset 語意不變。純本地邏輯,不涉富邦 SDK。

### 41. `backend/services/signal_engine.py:533` — 平日休市日(國定假日/颱風假)heartbeat 用前一交易日 stale tick 反覆觸發假訊號

面向:bug | finder:signal-engine | 驗證信心:high

_in_trading_session 只擋週末與時段,沒擋平日休市(台股每年約 10 個平日國定假日 + 颱風假)。後端 24/7 跑時,休市日 09:00–13:30 wall-clock gate 全開,ring_buffer.latest 停在前一交易日收盤 tick;若收盤價落在 CDP/MA proximity tolerance 內,每個 cooldown 週期就重複 fanout 一次(Discord 推播 + 寫入 signals_log),整個「假盤中」連發 4.5 小時。檔頭註解只分析了盤後/隔夜情境,漏了這個窗。

**建議修法**:heartbeat path 加 tick 新鮮度檢查:rb.latest 的 tick.time 距今超過 N 分鐘(例如 10 分)就跳過評估——休市日不會有新成交,自然全擋;或引入休市日曆。

**驗證者**:逐項驗證屬實:(1) _in_trading_session(signal_engine.py:526)只檢查 weekday>=5 與 09:00–13:30,整個 backend grep 不到任何休市日曆;(2) ring_buffer 的 trim 只在 append() 時執行,假日無新 tick 時 latest() 永遠回傳前一交易日最後一筆 tick,且 buffer 無每日清空;(3) heartbeat(line 186-211)每秒對所有 field_cache symbol 用 latest tick 跑 _evaluate,gate 用 wall-clock(line 220 刻意設計),平日國定假日 09:00–13:30 全開;(4) CDP/MA proximity 只需 price 落在容差內即成立(不需 crossing),cooldown 到期就重複 _fanout(WS broadcast+signals_log+Discord),fanout 內無 tick 新鮮度檢查;(5) 檔頭註解只分析試撮/盤後/隔夜/週末,確實漏了平日休市窗。午夜 refill 會用最近交易日 daily bar 重算 CDP,收盤價落在容差內完全可能。風險僅為假訊號洗版/log 污染(訊號不觸發下單),medium 嚴重度恰當。純本地邏輯,不涉富邦 SDK 行為假設。

### 42. `backend/services/signal_engine.py:156` — 每次規則/監聽 CRUD 都對全部 monitor symbol 重打 SMA REST,無當日 memo

面向:rate-limit | finder:signal-engine | 驗證信心:high | 重複回報:2 個 finder

refresh_active_signals → _refill_field_cache 對每個 monitor symbol 打 fetch_sma_5_20(2 個 rate-limited REST call)。CdpService 有 _last_backfill_attempt 每日一次的防重,SMA 完全沒有——而 CLAUDE.md 明載當日 daily SMA 不變。monitor_list 50 檔時,使用者每存一次規則就燒 100 個 REST 配額,且 POST /api/active_signals 是同步 await 這整串(限速下可能拖數十秒到分鐘級才回應)。

**建議修法**:仿 CdpService 加 per-symbol 當日 fetch memo(date 相同且 cache 已有 sma_5/sma_20 就跳過);或把 SMA refill 移到 heartbeat 跨午夜分支,規則 CRUD 只重讀規則不重抓行情。

**驗證者**:finding 屬實。signal_engine.py:155-160 的 _refill_field_cache 對每個 monitor symbol 無條件呼叫 ma_service.fetch_sma_5_20(2 個經 get_rate_limiter().acquire 的富邦 REST call),ma_service.py 全檔無任何 cache/memo;對比 cdp.py:126-138 的 CdpService 確有 _last_backfill_attempt per-symbol 當日防重。同步 await 鏈也屬實:routes/active_signals.py(POST/PUT/DELETE)、monitor_list.py、watchlist.py 都在回應前 await refresh_active_signals(),tests/test_active_signals_route.py 還明文驗證此行為。量級:rate limiter 預設 5 req/s(富邦 300/min),50 檔 = 100 call ≈ 純限速下限 20 秒起跳,若與其他 REST 消費者搶 token 會更久。CLAUDE.md 明載當日 daily SMA 不變,加當日 memo(date 相同且 _field_cache 已有 sma_5/sma_20 即跳過)安全可行,且 _field_cache 用 setdefault 保值、逐出邏輯不受影響。唯一緩解因素是單人本機、實際 monitor 檔數可能少於 50,但結構問題(每次規則 CRUD 重燒整輪 SMA 配額並阻塞 API 回應)確實存在,medium 嚴重度恰當。

### 43. `backend/services/signal_engine.py:708` — _degraded 一旦設起永不重置:health 永遠顯示 degraded,且 auto-disable 保護變一次性

面向:error-handling | finder:signal-engine | 驗證信心:high

_auto_disable_all 設 _degraded=True 後沒有任何路徑清回 False(refresh_active_signals、lag 恢復都不會)。後果:(1) health endpoint 在恢復後仍永遠回 degraded;(2) 使用者重新 enable 規則後若再度過載,_monitor_loop 的 `if not self._degraded` 擋住第二次 _auto_disable_all——保護機制與 critical alert 都不會再發,直到重啟。

**建議修法**:在 refresh_active_signals(代表操作者已介入重載規則)或 lag 恢復正常的 _monitor_loop else 分支重置 _degraded=False 與 _lag_violation_started=None。

**驗證者**:確認為真問題。grep 全 backend,_degraded 僅 4 處:__init__ 設 False(signal_engine.py:69)、health 讀取(:108)、_monitor_loop 閘門(:708)、_auto_disable_all 設 True(:719),無任何重置路徑。refresh_active_signals(:93-101)只重載 _active 與 field cache,不碰 _degraded;_monitor_loop 的 lag 恢復分支(:711)只清 _lag_violation_started。後果兩者皆成立:(1) health endpoint 觸發一次 auto-disable 後永遠回 degraded=true,而檔內註解(line 50)明示後端是 24/7 不重啟設計,影響實際存在;(2) 使用者重新 enable 規則後再度過載時,:708 的 `if not self._degraded` 永遠擋住第二次 _auto_disable_all,disable_all 與 critical alert 都不再發,自動保護機制變一次性。tests/ 無覆蓋此路徑。純本地邏輯,不涉富邦 SDK 行為假設。severity medium 合理(不涉下單,但保護機制失效)。

### 44. `backend/services/signal_engine.py:146` — field_cache 兼任 scope 閘門:CDP 與 SMA 同時抓不到的 symbol 被靜默踢出評估

面向:error-handling | finder:signal-engine | 驗證信心:high

_scope_includes / _scope_symbols 以 _field_cache 有無 key 為唯一依據。refill 時若某 monitor symbol 的 cdp.get 回 None(無 daily_ohlc 且富邦不可用)且 SMA 兩條都失敗,該 symbol 完全不建 cache entry → tick-driven 與 heartbeat 兩路都不評估它——即使規則只用 close / day_volume 這類不依賴 cache 的欄位。只有 cdp service 的 info log,沒有任何「此監聽股已退出訊號評估」的警告,要等下次 refill 才有機會恢復。

**建議修法**:refill 時對 monitor_list 的每個 symbol 一律 setdefault(sym, {}) 建空 entry(scope 資格與欄位有無解耦,缺欄位的條件自然回 False);對 cdp+sma 全失敗的 symbol 打 logger.warning。

**驗證者**:確認為真問題。signal_engine.py 中 _scope_includes(L490)與 _scope_symbols(L216)確以 _field_cache 有無 key 為唯一閘門,且全檔只有 _refill_field_cache(L143-160)會寫入 cache——entry 僅在 cdp.get 有值或 SMA 至少一條成功時建立。失敗情境可達且兩來源高度相關:富邦未就緒時 ma_service 直接回 None、cdp backfill 同時失敗,若該 symbol 本機 daily_ohlc 從無資料(新加入監聽、新上市股只有今日 K、或從未 backfill 過)則 cdp.get 也回 None → 完全不建 entry → tick-driven 與 heartbeat 兩路都靜默跳過。而 _eval_window(走 ring_buffer)與 close/day_volume(動態算)本不依賴 cache,這類規則被連坐。加重因素:cdp._last_backfill_attempt 同日只試一次,當天後續 refill 不會自動恢復;monitor_list.py:59 的 backfill 是背景 task,與 L64 的 refresh_active_signals 有 race。唯一緩解是 entry 一旦建立即跨 refill 持續(test_signal_engine_monitor.py L137 測試證實),窗口限於「進程啟動後從未成功過的 symbol」,但這正是 finding 描述的情境。無任何 warning 級 log,severity medium 合理。建議修法(對 monitor symbol 一律 setdefault 空 entry)與既有逐出邏輯一致,缺欄位條件自然回 False,可行。

### 45. `backend/services/lifecycle_sync.py:24` — 匯入 resync 對重疊 symbol 做全退訂再重訂:清空 ring_buffer 歷史 + 訂閱空窗

面向:bug | finder:signal-engine | 驗證信心:high

resync_from_config 先把 prev_owners 全部 unsubscribe 再依新 config subscribe。匯入的 config 與現況大量重疊是常態,重疊 symbol 的 refcount 落到 0 → WSPool 真退訂富邦 + ring_buffer.discard 把最多 30 分鐘的 tick 視窗整個清掉,之後 window_conditions / breakout surge 偵測在重新累積前全部回 False(訊號盲區);退訂到重訂之間還有漏 tick 的空窗,並對富邦多打一輪無謂的 unsub/sub。

**建議修法**:先算 diff:新 config 的 owner→symbols 集合與 prev_owners 取差集,只退訂「舊有但新無」的 (owner, symbol),只新訂「新有但舊無」的;交集不動,ring buffer 與富邦訂閱都保留。

**驗證者**:逐層核實成立:lifecycle_sync.py:21-26 確實先全退訂 prev_owners 再重訂、無 diff;fubon_ws.py:102-116 在 owner 清空時真打富邦退訂並 ring_buffer.discard;ring_buffer.py:44-48 的 discard 整個 pop 掉 deque,重訂後 ensure 建的是空 buffer,歷史不可復原。owner 全集 grep 確認只有 bookmark:*/monitor_list/preview/top_gainers,而匯入快照(current_owner_map)恰涵蓋前兩類,重疊 symbol 除非碰巧被 preview/top_gainers 持有否則 refcount 必落 0。signal_engine.py:603 _eval_window 依賴 ring_buffer.window,清空後 window/surge 條件在重新累積前全回 False,訊號盲區與多餘 unsub/sub 往返均屬實。觸發路徑 routes/config_io.py:22-27 與描述一致。緩頰處僅在匯入是低頻手動操作、盲區長度受限於最長 window_seconds,medium 定級合理。建議的差集修法正確且不影響啟動路徑(prev_owners=None)。

### 46. `backend/services/capital_balance.py:159` — timeout flush 可把一輪庫存查詢撕成兩半,殘餘事件之後被當「全量」發布,部位整批被尾段或空集合取代

面向:error-handling | finder:capital-flow | 驗證信心:high

BalanceCollector 的 timeout 保險(poll)在事件流中途停頓 >1s 時會先 flush 部分清單;此後同一輪的殘餘事件繼續 feed 進 staging(沒有任何「本輪已關閉」狀態),等到遲來的 ## 或下一次 timeout 又 flush 一次——而 _on_balance_complete → set_positions 是全量取代語意,第二次 flush 會把部位快取整批換成只有尾段幾檔;若 timeout flush 剛好發生在所有資料列之後、## 之前,遲來的 ## 會 flush 空集合,部位面板直接清空。後果是最長 60s(下一次定時重查前)部位靜默消失:平倉按鈕鍵不到、損益顯示不見。方向上安全(不會多平),但這是真錢面板的靜默資料錯誤,且每次多餘 flush 還會多打一次損益查詢。

**建議修法**:flush(不論 timeout 或 ##)之後把 collector 標記為 closed,feed 在未 reset 前丟棄事件並 log(reset 由發查詢前呼叫,語意不變);或 timeout flush 後不發布、改為標記本輪失敗並立即重發查詢。補「timeout flush 後殘餘事件+##」的測試。

**驗證者**:機制逐行驗證成立:feed() 收到 # 開頭無條件 _flush(capital_balance.py:145-147),即使 _last_feed 已被 timeout flush 清成 None、staging 為空也照發;collector 沒有「本輪已關閉」狀態,reset 只在發新查詢前呼叫。timeout flush 先發布部分清單後,殘餘事件會重新進 staging,遲到的 ## 或下一次 timeout 會二次 flush——而 set_positions 是全量取代(capital_store.py:199,test_set_positions_replaces_not_merges 鎖定此語意),空集合也照收,部位快取會被尾段幾檔或空集合整批取代。後果鏈也對:_on_balance_complete 每次重設 _balance_last_ts(capital_client.py:115),錯誤狀態最長撐到 60s 定時重查;close_position 查無部位直接擋單(方向安全但平倉鍵不到);每次多餘 flush 多打一次損益查詢。嘗試反駁:本地執行緒停頓(COM 命令阻塞、幫浦例外 sleep)因 pump 先於 poll 的順序確實不會誤觸,但來源端(群益回報主機)事件間 >1s 的停頓無任何保護,且 timeout 保險的存在本身就表示作者不信任 ## 送達時序。既有測試只測單獨 timeout flush 與單獨 ## flush,無組合情境。純本地狀態機缺陷、capital_* 真錢面板從嚴認定,medium 合理。

### 47. `backend/services/capital_safety.py:51` — max_qty / max_amount 為 0 時上限閘靜默停用,而 factory 預設值就是 0(fail-open)

面向:error-handling | finder:capital-flow | 驗證信心:high | 重複回報:2 個 finder

check_stock_order 與 check_correct_price 都用 `if cfg.max_qty and ...`、`if cfg.max_amount and ...`,0 為 falsy → 整個上限檢查跳過。capital_factory 從環境變數讀值的預設正是 "0"(CAPITAL_MAX_QTY / CAPITAL_MAX_AMOUNT 未設或留空 → 0)。在 ORDER_ENABLED=true 已上膛的前提下,.env 漏設或打錯一個變數名,數量與金額上限就靜默全開,沒有任何啟動警告——真錢系統的限額應該 fail-closed 而非 fail-open,且「0=無上限」的語意在程式碼與設定範例中都沒有文件化。

**建議修法**:至少在 order_enabled=true 且任一上限為 0 時於 factory/啟動時 log warning(或直接拒絕啟用下單);更嚴格的做法是 SafetyConfig 要求 order_enabled=true 時兩個上限必須 >0,否則 check_* 一律擋下並回明確原因。

**驗證者**:確認為真問題,且比原 finding 更嚴重:backend/.env.example:53 明文承諾「單筆數量上限(0=擋)、單筆金額上限(0=擋)」,即 0 應為 fail-closed;但 capital_safety.py:48/:51/:69 用 `if cfg.max_qty and ...`、`if cfg.max_amount and ...`,0 為 falsy 直接跳過整個上限檢查,實際行為是 0=無上限(fail-open)——文件與程式碼語意直接相反。capital_factory.py:27-28 預設值與留空(`or 0`)都落到 0,且 factory 無任何啟動 warning。test_capital_safety.py 全部測試都用非零上限,0 的語意從未被覆蓋。在 ORDER_ENABLED=true 已上膛的專案現況下,漏設或打錯一個環境變數名,數量/金額/改價三道閘靜默全開。屬真錢下單路徑「該擋未擋」,依專案標準應從嚴認定(建議升為 high/critical 而非 medium)。

### 48. `backend/routes/capital.py:55` — /api/capital/positions 不補股票名稱,前端部位清單與平倉確認對話框的名稱永遠空白

面向:bug | finder:capital-flow | 驗證信心:high

Position.name 預設空字串,parse_balance_line 與 set_positions 全鏈都不填;orders 端點有用 _symbol_name 補 name,positions 端點卻直接 model_dump 回傳。前端 PositionsList.tsx 渲染 `{p.stock_no} {p.name}`、ClosePositionDialog 也顯示 `{pos.stock_no} {pos.name}`,useCapital.ts 直接用回傳值不做 enrich——名稱永遠是空白。顯示性問題,但平倉確認框只剩代號沒有名稱,對真錢平倉的人為核對少了一道視覺確認。

**建議修法**:capital_positions 比照 capital_orders,逐筆 p.name = _symbol_name(p.stock_no) 後再 model_dump(Position 物件是 store 發布的共享參考,應 model_copy 或先 dump 成 dict 再填 name,避免就地改寫快取物件)。

**驗證者**:確認為真問題。全鏈查證:(1) backend/services/capital_balance.py:61 的 parse_balance_line 建 Position 不帶 name,Position.name 預設空字串(capital_models.py:83 註解即寫「route enrich 填,store 不管」),grep 全部 capital_* services 無任何地方填 name;(2) backend/routes/capital.py:55 positions 端點直接 model_dump,對照同檔 orders 端點第 45 行有 o.name = _symbol_name(o.stock_no),確實漏 enrich;(3) frontend useCapital.ts:71 直接 setPositions(r.positions) 無前端補名,PositionsList.tsx:88 與平倉對話框 :161 都渲染 {stock_no} {name},無 fallback——名稱永遠空白。test_capital_route.py 是手動帶 name 建 Position,只測欄位透傳、掩蓋了缺口。屬顯示性問題但發生在真錢平倉確認框,medium 嚴重度合理;建議修法中的 model_copy/先 dump 再填提醒也成立(store.positions() 回的是共享物件,orders 端點其實已有就地改寫前例,positions 修法可一併留意)。

### 49. `backend/routes/preview.py:64` — preview subscribe 失敗後 _current_preview 殘留舊值,重試同一檔會 noop 而實際已退訂

面向:error-handling | finder:routes-app | 驗證信心:high

流程是先 unsubscribe 舊的 _current_preview、再 subscribe 新的;若新訂閱丟 RuntimeError(容量滿)直接 raise HTTPException,_current_preview 沒更新仍指向舊 symbol,但舊 symbol 的 preview owner 已經退訂。之後前端若 POST 回舊 symbol,會命中 `new_sym == _current_preview` 的 noop 分支回 ok,實際上 preview owner 已不存在 → 分時圖 / TradeTape 靜默收不到 tick(若該檔沒有其他 owner,連富邦訂閱都已真退)。

**建議修法**:unsubscribe 成功後立刻 `_current_preview = None`,再嘗試 subscribe 新 symbol;失敗 raise 時狀態就是「目前無 preview」,與真實訂閱一致,重試任何 symbol 都會真的走 subscribe。

**驗證者**:逐行核實成立:preview.py:49-53 先退訂舊 symbol(fubon_ws.py unsubscribe 會在最後 owner 時真退富邦並 discard ring buffer),preview.py:58-62 新訂閱在容量滿時 raise RuntimeError→503,line 64 的 `_current_preview = new_sym` 不會執行,狀態殘留舊 symbol。之後 POST 回舊 symbol 命中 line 43 的 noop 分支回 ok,但 preview owner 已被移除——若該檔無其他 owner,富邦訂閱與 ring buffer 都已清,前端靜默收不到 tick。觸發前提是 WS pool 容量滿(MAX_CONNS=1×200,較罕見),屬嚴重度校準範圍,medium 合理。建議修法(unsubscribe 後先設 _current_preview=None)正確。純本地狀態機邏輯,不涉 SDK 行為假設。

### 50. `backend/routes/bookmarks.py:222` — add_items 先寫 store 再 subscribe,訂閱失敗只 warn 仍回 201 — 與 monitor_list 的訂閱先行模式矛盾

面向:error-handling | finder:routes-app | 驗證信心:high

monitor_list.add_monitor 刻意「先試 ws subscribe,失敗就不寫 store,避免狀態不一致」並回 503;add_items 卻是先 store.config.add_item 全部寫入、再逐檔 subscribe,RuntimeError(pool 容量滿 200)只 logger.warning,照樣回 {added: [...]}。結果是書籤裡看得到該股票、但完全沒有即時行情,且這個不一致會持續整個 session(要到重啟 resync_from_config 才會再嘗試)。同檔案 move_items 的 subscribe 也是同樣只 warn。

**建議修法**:對齊 monitor_list 模式:subscribe 失敗的 symbol 從 store 回滾(remove_item)並列入回應的 failed 清單(或部分失敗回 207/明確欄位),讓前端能呈現「加入失敗」而不是靜默沒行情。

**驗證者**:核實屬實。(1) bookmarks.py:215-224 確實先全部 store.config.add_item 再逐檔 subscribe,RuntimeError 只 warn、symbol 照列在 added 回 201;move_items:275-277 同模式。monitor_list.py:49-53 有明文註解「先試 ws subscribe;失敗就不寫 store,避免狀態不一致」並回 503,兩者矛盾且 bookmarks 無任何註解聲明 best-effort 是刻意設計。(2) 失敗情境可達:fubon_ws.py 單 process 容量 = 200(富邦官方 WS 每連線 200 subscriptions 限制,程式註解已引官方文件),滿了 _pick_conn_with_capacity 回 None 即拋 RuntimeError;書籤單次可加 200 檔、與 monitor_list 共池,超過 200 檔 distinct 可達。(3)「沒行情持續整個 session」屬實:前端 useWatchlistQuotes 價格 = 一次性 snapshot + WS tick bus,訂閱失敗的 symbol 只剩加入當下的 snapshot 價且永不更新、無錯誤提示;唯一補救是啟動/匯入時的 resync_from_config(lifecycle_sync.py),失敗也只 warn。唯一反駁角度是「書籤非訊號關鍵、刻意 best-effort」,但無程式碼註解支持,且回應把失敗 symbol 照列 added 讓前端零感知,仍是靜默失敗——頂多影響修法選擇(回滾 vs 回報 failed 欄位),不構成誤報。severity medium 合理。

### 51. `backend/routes/symbols.py:53` — ISIN 解析回空被當成功,replace_symbols 會用 OpenAPI 殘缺資料覆蓋整張全市場主表

面向:error-handling | finder:routes-app | 驗證信心:high

_parse_isin_html 在 section regex 沒命中時回 [] 且 _fetch_isin 回 (rows=[], error=None) — 只有 exception 才算失敗。若 TWSE 改版 HTML 讓 regex 失配,ISIN 來源會靜默變零筆,refresh 只剩 OpenAPI 補充來源(TWSE 當日有交易股 + TPEx 僅主板),rows 非空就直接 market.replace_symbols(rows) 整表替換 → 全市場主表縮水成「今天有成交的股票」。之後 has_symbol 檢查會對合法但當日無成交/非主板的代碼回 404,書籤/監聽/自選都加不進去,而 API 回應 status=ok、errors=[] 完全看不出來。

**建議修法**:把「ISIN 解析 0 筆」視為錯誤計入 errors(section regex 沒命中時回傳 error 描述);另在 replace_symbols 前加防線:若新 rows 數量遠低於既有筆數(例如 < 50%),拒絕整表替換並回報。

**驗證者**:逐行驗證屬實:symbols.py:53-54 section regex 失配回 []、_fetch_isin:84 無 exception 即回 error=None,refresh_symbols:117-123 只有 isin_err 才計入 errors,ISIN 0 筆完全靜默;market_cache.py:47-52 的 replace_symbols 是無條件全量取代且無筆數防線,只剩 OpenAPI 補充來源(TWSE 當日有交易股+TPEx 主板)時 rows 非空照樣覆蓋主表。下游 watchlist.py:74 / monitor_list.py:46 / bookmarks.py:205 在 symbols_loaded()(縮水後仍非空)成立時對缺漏代碼回 404,且 API 回 status=ok、errors=[]。反駁角度均不成立:refresh 端點公開且設計文件明示供手動重爬;regex 綁 TWSE HTML 具體結構(colspan=7+全形「股票」),外站改版即失配,而模組 docstring 自述 OpenAPI 是「ISIN 解析失敗」的 fallback,意圖與實作(只抓 exception 不抓 0 命中)有明確落差。純本地邏輯不涉富邦 SDK,免查官方文件。medium 定級合理(需 TWSE 改版+觸發 refresh 兩條件,非下單路徑)。

### 52. `backend/routes/candles.py:30` — intraday.candles 直打 SDK、未過共用 rate limiter,前端輪詢會讓全 app 配額記帳失真

面向:rate-limit | finder:routes-app | 驗證信心:high | 重複回報:4 個 finder

專案內所有富邦 REST 呼叫都先 `get_rate_limiter().acquire()`(fubon_client 的 wrapper、ma.py、cdp/camarilla backfill 用 historical bucket),唯獨這裡用 to_thread 直打 sdk...intraday.candles,完全繞過 5 req/s token bucket。這條 endpoint 是前端三個圖表頁的輪詢主力(d8308c1 才剛做隱藏頁暫停輪詢),繞過 limiter 代表實際打富邦的速率 = limiter 額度 + 輪詢量,尖峰時會吃掉同帳號 Intraday 300/min 配額,讓有照規矩排隊的 quote/sma 呼叫吃 429,行情靜默劣化。

**建議修法**:fetch_candles 內先 `await asyncio.to_thread(get_rate_limiter().acquire)` 再打 SDK,與 ma.py 同款;或把 candles 包進 fubon_client 的 high-level wrapper 統一管。

**驗證者**:查證屬實。candles.py:30 直打 sdk.marketdata.rest_client.stock.intraday.candles,無任何 acquire();grep 全 backend 確認這是唯一繞過 limiter 的富邦 REST 呼叫——fubon_client wrapper(148/157/166)、ma.py:42、ma_service.py:39、fubon_futures.py:91/221(連期貨版 futopt.intraday.candles 都有過 limiter)、cdp/camarilla 走 historical bucket,全部照規矩。更明顯的是同一 handler 內 fetch_prev_close 走 fubon.intraday_quote 有過 limiter,同請求一半記帳一半不記帳,顯然是遺漏。已照富邦文件工作流程 WebFetch 官方 rate-limit 頁:日內行情 300/min、超限回 429 需等 1 分鐘,candles 與 quote 同屬日內行情類別共用此配額,limiter 5 req/s 正是對應它(rate_limiter.py:89 docstring 自證)。呼叫方:前端 useIntradayCandles 每 30s 輪詢、bot getCandles(個股查詢+訊號圖卡)都打此 endpoint。唯一緩解是平時輪詢量不大(30s/頁)離 300/min 還遠,但 bot 查詢爆量或訊號推播尖峰疊加時繞道流量會讓守規矩的 quote/sma 吃 429,medium 嚴重度與建議修法(fetch_candles 內先 acquire,與 ma.py 同款)均恰當。

### 53. `backend/routes/quote.py:93` — snapshot 一批 50 檔併發 to_thread(acquire) 會佔滿共用 thread pool、卡住全後端的 SDK 呼叫

面向:concurrency | finder:routes-app | 驗證信心:high | 重複回報:2 個 finder

asyncio.gather 同時起最多 50 個 one(),每個都 `to_thread(get_rate_limiter().acquire)` — acquire 是阻塞式 sleep 等 token(5 tokens/s,50 個要 ~10 秒消化)。asyncio 預設 executor 只有 min(32, cpu+4) 個 worker,這批阻塞中的 acquire 會把 worker 占好占滿,期間所有其他 to_thread 使用者(intraday_quote、tech.sma、historical backfill、ws subscribe/connect)全部排隊 → 一次 snapshot 請求就能讓整個後端的富邦呼叫停擺數秒。前端 useSnapshotCache 註明會湊滿 50 檔一批打,這是實際會發生的路徑。

**建議修法**:在 one() 外圍加 asyncio.Semaphore(約 5,與 rate 對齊)限制同時進 to_thread 的數量;或把 token 等待改成 async 端計算 wait 後 `await asyncio.sleep()`,只有真正打 SDK 的那一下才進 thread pool。

**驗證者**:反駁失敗,機制鏈全部證實:(1) quote.py:93 asyncio.gather 同時起最多 50 個 one(),全 backend grep 無 Semaphore、無自訂 executor;(2) fubon_client.py:148 intraday_quote 確實 to_thread(acquire);(3) rate_limiter.py acquire() 是 time.sleep 阻塞迴圈,default rate=5/s、capacity=5,50 個 acquire 約 9 秒才消化完;(4) asyncio.to_thread 共用 loop 預設 executor(max_workers=min(32,cpu+4)≤32<50),滿批 snapshot 必佔滿 pool,期間 fubon_ws 訂閱、ma/sma、candles、cdp/camarilla backfill 等 30+ 處 to_thread 全排隊;(5) frontend useSnapshotCache.ts 確認單批 50 檔且 missing>50 還切 chunk「平行打」,比 finding 描述更糟、是實際發生的路徑。純本地併發邏輯不涉 SDK 行為假設,不需查富邦文件。medium 嚴重度合理(數秒級行情停擺,不涉下單安全)。

### 54. `backend/services/local_store/config_store.py:45` — load() 的壞檔防護只擋 JSON 解析錯,合法 JSON 但非 dict(或元素非 dict)仍會讓啟動掛掉

面向:error-handling | finder:local-store | 驗證信心:high

read_json 回傳 truthy 非 dict(例如檔案內容是 [1] 或 "x")時,self._data.setdefault 直接 AttributeError;bookmark_groups 內含非 dict 元素時 _seed_defaults 的 g.get 同樣炸。這些都不在 (json.JSONDecodeError, OSError) 的 except 裡,違背註解「壞檔不讓後端起不來」的設計意圖——半毀損檔案(尤其上面 import_config 寫穿的壞資料)會讓 store.init() 在 lifespan 直接失敗。

**建議修法**:load() 讀完後加 isinstance(self._data, dict) 檢查,且四個 list key 逐一驗 isinstance(v, list) 並過濾非 dict 元素;不合格走同一條「備份成 .corrupt + 重建」路徑。

**驗證者**:確認為真問題。(1) 崩潰機制屬實:read_json(paths.py)回傳任意 JSON 值,config.json 內容為 truthy 非 dict(如 [1]、"x")時 load() line 51-52 的 self._data.setdefault 直接 AttributeError;bookmark_groups 含非 dict 元素時 _seed_defaults(line 57)的 g.get 也炸;皆不在 (json.JSONDecodeError, OSError) 的 except 內,違背 line 47「壞檔不讓後端起不來」的註解意圖。(2) 啟動掛掉屬實:main.py:48 lifespan 無防護呼叫 get_local_store().init() → config.load(),例外即後端起不來。(3) 壞資料可達:POST /api/config/import 收 raw dict、import_config 對四個 list key 零型別驗證(new[k]=data.get(k,[]))、route 只 catch ValueError;含非 dict 的 bookmark_groups 雖會讓 _seed_defaults 在該次 _persist 前先炸(回 500 未立即寫檔),但 line 232 已把 self._data 換成壞資料,之後任何一次正常 mutation(add_monitor/create_group 等)的 _persist() 就把壞資料寫穿到磁碟,下次重啟必掛——finding 說的 import 寫穿路徑成立,只是比描述多繞一步;手改檔案則是直接路徑。唯一細節修正:_seed_defaults 的 any() 短路使「壞元素排在使用者群組之後」的情況 load 不炸,但這是運氣非防護,不影響結論。medium 嚴重度恰當(單機單人、需壞 payload 或手改檔觸發)。

### 55. `backend/services/local_store/signals_log.py:31` — load/_read_all 對「合法 JSON 但非 object」的行容忍不完整:load 會炸啟動、query 會在 API 時 500

面向:error-handling | finder:local-store | 驗證信心:high

load() 的 except 只接 (ValueError, TypeError),但若某行是合法 JSON 的 scalar(如 123 或 "x"),json.loads(line).get 丟 AttributeError → store.init() 失敗、後端起不來。_read_all 則會把非 dict 的值原樣 append 進 rows,query() 的 r.get("symbol") 在查詢時丟 AttributeError → 歷史 API 500。兩處對壞行的「跳過」策略都只蓋到 parse 失敗,沒蓋到型別不對。

**建議修法**:兩處 parse 後加 isinstance(rec, dict) 才採用,否則 continue;load() 的 except 順便涵蓋 AttributeError 或改成 parse 與取值分開處理。

**驗證者**:機制驗證屬實:load() 的 `json.loads(line).get("id", 0)` 對合法 JSON 但非 dict 的行(如 123、"x"、null)會丟 AttributeError,except 只接 (ValueError, TypeError) 攔不到;load() 經 LocalStore.init()(__init__.py:28)在 main.py:48 lifespan 啟動時呼叫,例外會讓後端起不來。_read_all() 同樣只接 ValueError、非 dict 值原樣進 rows,query() 的 r.get() 在 /api/signals/history 觸發 AttributeError → 500(route 無額外包覆)。反駁面已查:寫入者只有 append()(永遠寫 dict)與遷移腳本,/api/config/import 不碰此檔,crash 截斷的壞行是 parse 失敗、現有 except 已蓋;故觸發前提是手動編輯或外部損壞。但這是 user 可見可編輯的本機 JSONL,且程式碼已明確展現「壞行跳過」容錯意圖卻只蓋一半,屬防護不完整的真缺陷;load() 炸的是整個後端啟動,finding 成立(嚴重度 medium 偏寬,實務接近 low-medium)。

### 56. `backend/services/local_store/signals_log.py:62` — query/today_rows 每次呼叫都全檔重讀重 parse,append-only 檔無上限成長

面向:rate-limit | finder:local-store | 驗證信心:high

signals_log.jsonl 只增不減(使用者遷移當下已 >1000 筆,之後每個訊號觸發都 append),而 query() 與 today_rows() 每次 API 呼叫都 _read_all() 把整個檔案讀進來逐行 json.loads 再過濾排序。前端歷史頁/今日訊號每次載入都是 O(全檔),隨時間線性變慢,且這是同步 I/O、直接在事件迴圈上跑,檔案大了會卡住整個 loop(包括行情 tick 消費)。

**建議修法**:單一 process、append-only 的前提下,load() 時把 rows 讀進記憶體 list,append() 同步 append 進去,query/today_rows 直接查記憶體;或退一步做按日輪轉/只讀檔尾 N 筆。

**驗證者**:finding 的每個事實主張都經實讀驗證為真:(1) signals_log.py 的 append() 只增不刪,整個 repo 無任何輪轉/截斷邏輯,且 signal_writer.py:20 每次訊號觸發都 append,使用者遷移時已 >1000 筆;(2) query()/today_rows()(signals_log.py:62/75)每次都呼叫 _read_all() 全檔逐行 json.loads,無快取;(3) routes/signals_history.py:19/35 兩個 endpoint 都是 async def 直接呼叫這些同步函式——FastAPI 對 async def handler 不會丟 threadpool,所以同步檔案 I/O+parse 確實在事件迴圈上跑;(4) 行情 tick 確實共用同一個 loop(fubon_ws.py:177、main.py:94 都用 call_soon_threadsafe 把 SDK callback marshal 到主 loop),loop 被卡時 tick 廣播/消費會延遲。模組 docstring「同步、不 await → 對事件迴圈原子」說明同步是刻意設計,但那是為了 append 原子性,擋不掉全檔重讀的線性退化。緩解因素僅影響嚴重度而非真偽:兩個 endpoint 只在前端頁面 mount 時打一次(useTodayHits.ts:18、Monitor.tsx:60),非輪詢;且 cooldown ≥1800s 限制了成長斜率,單機單人要長到「卡死 loop」等級需相當時日——medium 評級合理不誇大。建議修法(load() 時讀進記憶體、append 同步維護)也與現況一致:load() 本來就在啟動時全檔掃一次算 next_id,改成順手留住 rows 成本極低。判定為真問題。

### 57. `backend/services/overnight.py:40` — 直接改 WSPool 私有狀態且不取 pool._lock,與 subscribe/_reconnect 競態

面向:concurrency | finder:analytics-jobs | 驗證信心:high | 重複回報:3 個 finder

overnight 直接讀寫 pool._ws_handles / pool._conn_subs 並呼叫私有的 _ensure_handle,全程沒有 acquire pool._lock。WSPool 的 subscribe/unsubscribe/_reconnect 都在 lock 內讀寫同一批 dict;8:25 期間若有 API 請求觸發 subscribe(加自選/書籤)或舊連線斷線觸發 _reconnect,兩邊會交錯 pop/寫 _ws_handles——例如 subscribe 在 overnight pop 之後、_ensure_handle 完成之前進來,會自己再建一次 handle,造成重複 connect 或訂閱落在被丟棄的 handle 上。另外迴圈只走 _ws_handles.keys():若某 conn 的 handle 先前建立失敗(_conn_subs 有 symbol 但無 handle),這些 symbol 會被整個跳過、永不重訂。

**建議修法**:把重連流程搬進 WSPool 成為公開 async 方法(內部 async with self._lock),overnight 只呼叫該方法;迭代來源改用 _conn_subs(有訂閱需求的 conn)而非 _ws_handles(有 handle 的 conn)。

**驗證者**:逐點核實皆成立。(1) overnight.py:40-43 確實無鎖直接迭代 pool._ws_handles、pop handle、呼叫 _ensure_handle,而 fubon_ws.py 的 subscribe/unsubscribe/_reconnect 都在 self._lock 內讀寫同批 dict。(2) 競態不是巧合情境而是每天必然:run_overnight_reconnect 第一步 fubon.init() 會 new 全新 FubonSDK(fubon_client.py:121),舊 WS 被丟棄時舊 handle 的 on_disconnect 會排程 _reconnect(0)(1 秒後執行),與 overnight 迴圈並發;overnight 在 to_thread(connect)/to_thread(subscribe) 有 await point 讓出事件迴圈,_reconnect/API subscribe 拿鎖無阻(overnight 不持鎖),可交錯 pop/重建,造成 _wire_callbacks 對同一 ws 重複註冊 on("message")(tick 雙倍處理)+ 重複 connect/subscribe,或訂閱落在被棄置的 handle。(3) 第二點也屬實:subscribe() 先寫 _conn_subs(fubon_ws.py:96-97)再 _real_subscribe,_ensure_handle 失敗時靜默 return 不回滾,此時 _ws_handles 無 entry,overnight 迭代 _ws_handles.keys() 會整個跳過該 conn,這些 symbol 無任何路徑重訂。反駁角度(單 loop 安全、MAX_CONNS=1 singleton)都檢查過不成立——relogin 後是新 SDK 新 singleton,重複 wire 傷害仍在。屬純本地鎖邏輯,不涉 SDK 行為假設。嚴重度 medium 合理(行情層非下單路徑,最壞為 tick 重複/行情靜默斷流)。

### 58. `backend/jobs/top_gainers_scheduler.py:109` — 富邦 API 失敗與「真的沒有大漲股」混為一談——清空快取並退訂全部訂閱

面向:error-handling | finder:analytics-jobs | 驗證信心:high

_fetch_market_movers 在 fubon 未 ready 或 movers 拋例外時回 [],refresh_top_gainers 看到 raw 為空就走「清空 snapshot + _sync_subscriptions(set())」。一次暫時性失敗(rate limit、網路抖動、8:25-9:00 後 relogin 未完成)就會:前端大漲股頁瞬間變空、最多 50 檔 ws 退訂;下一分鐘恢復後又整批重訂——一來一回最多 100 次 ws sub/unsub churn,期間這些股票的即時 tick 中斷。「清空表示無 stale」的設計意圖只適用於「查詢成功但過濾後為空」,不該套用在抓取失敗。

**建議修法**:_fetch_market_movers 改回傳 list | None(None=失敗),兩市場都失敗時保留上一輪 snapshot 與訂閱、只 log warning;僅在「至少一個市場查詢成功且結果為空」時才清空。

**驗證者**:程式碼行為與 finding 描述完全相符:_fetch_market_movers(backend/jobs/top_gainers_scheduler.py:68-86)在 fubon 未 ready 或 movers 拋例外時都回 [],與「查詢成功但無資料」無法區分;refresh_top_gainers(line 106-111)看到 raw 為空即清空 snapshot 並 _sync_subscriptions(set())。fubon_ws.py 的 refcount 機制確認 last owner 退訂是真打富邦 WS unsubscribe(line 110-115),而大漲股榜多數股票只有 system:top_gainers 一個 owner,故暫時性失敗→真退訂最多 50 檔→下分鐘恢復→真重訂,churn 與 tick 中斷屬實。此外 line 113-114 註解顯示作者在 symbols 快取未載入時刻意「保留全部、不拿空快取砍光候選」,證明「區分資料不可用 vs 真的為空」本是設計意圖,movers 失敗路徑漏了同樣處理。無對應測試固化此行為。finding 不依賴 SDK 行為假設(except Exception 路徑是程式碼明寫),不需查富邦文件即可確認。建議修法(回傳 None 表失敗、兩市場皆敗時保留上輪 snapshot 與訂閱)正確可行,medium 嚴重度合理。

### 59. `backend/jobs/top_gainers_scheduler.py:159` — ws 訂閱失敗的 symbol 仍被記入 _subscribed_symbols,之後永不重試

面向:error-handling | finder:analytics-jobs | 驗證信心:high

_sync_subscriptions 對 to_add 的 pool.subscribe 失敗(目前唯一會浮上來的是 capacity full 的 RuntimeError;自選+書籤+監聽+大漲股共用 200 額度,是真實可達的狀態)只 log 後繼續,最後仍無條件 _subscribed_symbols = new_set。失敗的 symbol 從此被當成「已訂閱」:只要它持續留在 top 50,每一輪 diff 都不會再嘗試訂閱——即使期間有其他退訂釋出額度。結果是該股出現在大漲股清單上,但 ring_buffer 無 tick、前端即時價不動,靜默壞掉。

**建議修法**:subscribe 失敗的 symbol 不要併入 _subscribed_symbols(收集 failed set,結尾 _subscribed_symbols = new_set - failed),讓下一輪 refresh 自然重試。

**驗證者**:逐點驗證成立:(1) fubon_ws.py:90-95 在 capacity full 時 raise RuntimeError 且 rollback refcount,pool 端確實沒訂閱該 symbol;(2) 200 額度由 bookmarks/watchlist/monitor_list/preview/top_gainers(50檔) 共用(MAX_CONNS=1×200,檔頭註解明寫單 process 限制),test_monitor_list_route.py:65 甚至以此 RuntimeError 當測試 fixture,證明是被認知的真實錯誤路徑;(3) top_gainers_scheduler.py:146-159 catch 後只 log,第 159 行無條件 _subscribed_symbols = new_set,下一輪 diff(to_add = new_set - _subscribed_symbols)永不重試失敗 symbol——即使期間退訂釋出額度——造成 scheduler view 與 pool 真實狀態永久分歧,該股在榜上但無 tick、前端價格靜默不動。此為純本地 diff 邏輯錯誤,不涉富邦 SDK 行為假設。建議修法(new_set - failed)正確且最小,unsubscribe 對未訂閱 symbol 是安全 no-op 不會有副作用。嚴重度 medium 合理:需累積到 200 檔才觸發,但 top 50 一次吃 50 檔、多 owner 共用下可達。

### 60. `backend/services/cdp.py:136` — 同 symbol 並發 get() 競態:第二個呼叫者在 backfill 完成前拿到 stale/None;_lock 宣告了從未用

面向:concurrency | finder:analytics-jobs | 驗證信心:high

get() 的「檢查 _last_backfill_attempt → 標記 → await backfill」不是原子的:task A 標記今天後在 backfill 上 await(historical limiter 1 req/s,多 symbol 排隊時可 block 數十秒),task B 此時進來看到已標記,直接讀 _cache——拿到昨天留下的 stale levels 或 None。開盤時 signal_engine._refill_field_cache 與前端 /api/cdp 請求同時打同一批 symbol,正是這個交錯場景:route 拿到 None 後還會再發一次 backfill_from_fubon(額外消耗 historical 額度)。__init__ 建的 asyncio.Lock 從頭到尾沒人 acquire,是死碼。camarilla.py L66-78 完全相同(它的註解甚至承認 lock 沒在用)。

**建議修法**:per-symbol 的 in-flight de-dup:用 dict[str, asyncio.Task] 記進行中的 backfill,後到者 await 同一個 task 而非直接讀 cache;或最簡單把 get() 的 check-mark-backfill 區段包進 self._lock。修好後刪掉(或真正使用)_lock,camarilla 同步。

**驗證者**:逐項驗證後無法反駁,finding 全部屬實。(1) 競態:cdp.py get() 在 L137 先標記 _last_backfill_attempt 才 await backfill,backfill 內有 historical limiter acquire(to_thread)與 REST call 兩個讓出點,多 symbol 排隊時窗口達數十秒;第二個呼叫者看到已標記即跳過 backfill,直接讀 stale cache 或 refresh() 讀過期 daily_ohlc,回 stale/None。(2) 後果屬實:routes/cdp.py L14-16 拿到 None 會再打一次 backfill_from_fubon 多耗 historical 額度;更糟的是 signal_engine._refill_field_cache(L142-152)一天只跑一次,若交錯拿到 stale CDP 會寫進 field_cache 影響整天訊號判定。(3) 死碼屬實:grep 全 backend,cdp.py 的 _lock 只在 L123 建立從未 acquire;camarilla.py L64-66 相同且註解自承未使用,無任何外部程式碼取用這兩個 service 的 _lock。不涉下單路徑,medium 嚴重度恰當,建議修法(per-symbol in-flight de-dup)合理。

### 61. `backend/services/camarilla.py:106` — 與 cdp.py 近乎逐行重複,且同一 symbol 同一天打兩次相同的 historical.candles

面向:simplify | finder:analytics-jobs | 驗證信心:high

CamarillaService 的 get/refresh/backfill_from_fubon 與 CdpService 結構、流程、log 訊息幾乎完全相同,唯一差異是 compute 函式與 cache 欄位。兩個 service 各自維護 _last_backfill_attempt,而前端圖表同時要 CDP 與 Camarilla:同一 symbol 每天會對富邦 historical.candles(官方 60 req/min)發兩次內容完全相同的請求、upsert 同一份 daily_ohlc。在 1 req/s 的 historical limiter 下,開盤首載 N 檔要排 2N 秒,後到的請求全部被 block。重複碼也意味著前述 get() 的兩個缺陷要修兩遍。

**建議修法**:抽一個共用的 daily-OHLC backfill 模組(單一 _last_backfill_attempt + 單一富邦呼叫 + upsert),CdpService/CamarillaService 退化成「從 daily_ohlc 算各自 levels 的薄層」;同 symbol 每天只打一次 historical API。

**驗證者**:確認為真問題。camarilla.py:56-157 與 cdp.py:112-230 的 get/refresh/discard/has/backfill_from_fubon 近乎逐行相同(camarilla.py:59 docstring 自承「跟 CdpService 同設計」),唯一差異是 compute 函式與 cache 欄位;兩 service 各自維護 _last_backfill_attempt、各自打 historical.candles、upsert 同一份 daily_ohlc。grep 全 repo 證實雙重 fetch 情境真實:前端 IntradayChart 的 CDP/CAM 兩個 toggle(useLocalToggle,開過即持久化)同時開啟時,同 symbol 同一天會對富邦發兩次完全相同的 historical 請求。唯一保留:「開盤首載 N 檔要排 2N 秒」略誇大——signal_engine 開盤批次只走 CDP(N 次非 2N),Camarilla 僅在前端開 CAM toggle 看圖時逐檔觸發;但此量化瑕疵不影響 finding 核心(重複碼要修兩遍 + 同 symbol 雙重 fetch 確實發生),severity medium/simplify 標得恰當。建議修法(抽共用 daily-OHLC backfill 模組)合理可行。

### 62. `backend/services/fubon_ws.py:220` — SDK 執行緒以 call_soon_threadsafe(asyncio.create_task, coro) 大量產生無人持有的 task

面向:concurrency | finder:concurrency | 驗證信心:high

tick 熱路徑每筆產生兩個 fire-and-forget task(_on_tick enqueue + broadcaster.broadcast),同模式還有 fubon_ws.py:236、fubon_futures_ws.py:133/141、main.py:49(bootstrap_symbols_if_missing)與 main.py:94(capital broadcast lambda)。asyncio 官方明文:event loop 只持 task 弱參考,無人保存參考的 task 可能執行中被 GC;且這些 task 的例外永遠無人觀察(只剩 'Task exception was never retrieved' log)。盤中每秒數百 tick 下也是無上限的 task 生成,broadcast 慢時會堆積。

**建議修法**:建一個共用 helper:task 加進 module 級 set 並 add_done_callback(set.discard + 例外 log);tick→broadcast 路徑更好的解是 SDK 執行緒只投遞到單一 asyncio.Queue(loop.call_soon_threadsafe(queue.put_nowait, item)),由一個常駐 consumer task 消費,徹底消掉 per-tick task。

**驗證者**:已實讀 fubon_ws.py、fubon_futures_ws.py、main.py、ws_broadcaster.py、signal_engine.py 驗證:所有點名位置確實是 call_soon_threadsafe(asyncio.create_task, coro) 或裸 create_task,task 參考無人保存、無 done_callback,repo 內也沒有共用 holder。最實質的危害成立:Broadcaster.broadcast 逐 client await send_json 且無任何 queue/背壓,任一前端 client 慢時 per-tick broadcast task 會無上限堆積(200 檔訂閱、開盤每秒數百 tick 是本專案設計負載);且 signal_engine.enqueue 其實只做同步 put_nowait,per-tick 包 task 純屬浪費,建議的 call_soon_threadsafe(queue.put_nowait, item) 修法與既有 bounded queue 架構完全吻合。兩個論點需弱化但不推翻:(1) task 被 GC 是官方文件明文警告的 contract,但此處 await 鏈(lock waiter/transport)實務上都持強參考,enqueue task 更是單步完成無 GC 視窗,屬理論風險;(2) 例外非全然無聲,default handler 會印 'Task exception was never retrieved'。嚴重度 medium 恰當——main.py:94 的 capital lambda 只是回報 broadcast、不在下單路徑,不升 critical。本 finding 為純本地 asyncio 橋接邏輯,不涉富邦 SDK 行為假設,免查官方文件。

### 63. `backend/ws_broadcaster.py:28` — 每 tick 一個 broadcast task 並發對同一 WebSocket send_json,無 per-client 序列化與背壓

面向:concurrency | finder:concurrency | 驗證信心:high

fubon_ws 對每筆 tick 排一個獨立 broadcast task;send_json 會在網路 I/O 上讓出,多個 broadcast task 可同時對同一個前端 WebSocket 並發送出 — Starlette/uvicorn 不保證並發 send 安全,訊息可能交錯或拋例外(該 client 被誤判 dead 移除);routes/ws.py 的 pong 回覆也與 broadcast 並發寫同一 socket。慢 client 還會讓 task 無上限堆積(無佇列上限、無丟棄策略)。

**建議修法**:改成 per-client 發送佇列(有界 asyncio.Queue + 每 client 一個 sender task,滿了丟最舊 tick),broadcast 只做 put_nowait;順便天然解掉 per-tick task 堆積。

**驗證者**:事實主張全數在程式碼中驗證屬實:(1) fubon_ws.py:236-238 對每筆 tick 都 call_soon_threadsafe(asyncio.create_task, broadcast(...)),無節流合併,main.py:93-95 的 capital 事件同 pattern;(2) ws_broadcaster.py 的 _lock 只保護 client set 快照,send_json 在鎖外逐一 await,多個 broadcast task 可並發對同一 WebSocket send;(3) routes/ws.py:33 的 pong 回覆確實與 broadcast 並發寫同一 socket;(4) 無佇列、無上限、無丟棄策略,慢 client 會讓 per-tick task 無上限堆積,且 broadcast 內依序送所有 client,一個慢 client 還會拖慢同筆 tick 對其他 client 的遞送。唯一過度陳述處:「訊息可能交錯」在 uvicorn 預設 websockets 實作下實務上不易發生(完整訊息 frame 是單次同步 transport.write),但 ASGI/Starlette 規格不保證並發 send 安全,且並發例外導致 client 被誤判 dead 移除、無背壓堆積等核心問題不依賴該子主張仍成立。考量全員本機跑、前端在 localhost,medium 嚴重度恰當;建議的 per-client 有界佇列修法是標準解。

### 64. `backend/routes/mxf.py:41` — MXF candles 把富邦故障降級成 200 + 空陣列,前端無法區分休市與行情源死亡

面向:error-handling | finder:error-handling | 驗證信心:high

fubon_futures.fetch_candles 在 sdk 未初始化(line 212)或兩個 session 呼叫全失敗(line 228-230)時都回 [],route 照樣 200 回 candles=[]。前端拿到空圖只能當「沒資料」,跟 stock intraday candles 的 503 fubon_unavailable 口徑不一致 — 富邦掛掉時 MXF 頁面靜默空白,沒有降級提示。

**建議修法**:fetch_candles 區分「無資料」與「全部來源失敗」(如回 None 或拋專屬例外),route 對後者回 503 {error: fubon_unavailable},與股票 candles 對齊。

**驗證者**:確認為真問題。fubon_futures.py fetch_candles 在 sdk=None(line 211-212)與兩 session 呼叫全失敗(line 228-230 各自吞例外回 [])時都回空陣列,routes/mxf.py:40-46 一律 200;對照 routes/candles.py:26-27 股票 candles 在 SDK 不健康時拋 503 fubon_unavailable、呼叫失敗拋 502,口徑確實不一致。前端 useMXFCandles.ts:35-42 收到 200 空陣列會設 error:null 並用空陣列覆蓋既有 candles,30 秒輪詢持續抹掉畫面=靜默空白;其 catch 分支(line 43-49)有完整 error 顯示能力,503 口徑下即可呈現降級提示。唯一可能的反駁點(route line 37-38 的 503 mxf_symbol_unavailable 防線)實際擋不住:前端首拉後輪詢都帶 symbolRef(hook line 60)跳過 symbol 解析,且 resolve_active_symbol 的 1h cache(fubon_futures.py:74-76)在檢查 sdk 之前就回 cache,SDK 盤中死亡後至少 1 小時不會觸發該防線。不涉富邦 SDK 行為假設(純本地例外處理),不需查官方文件;severity medium 合理(僅行情顯示、不涉下單)。

### 65. `backend/routes/bookmarks.py:225` — 批次加股票對每檔 fire-and-forget CDP backfill,1 req/s 限速在 to_thread 內阻塞會耗盡共用執行緒池

面向:rate-limit | finder:error-handling | 驗證信心:high

add_items/move_items 上限 200 symbols,每檔 asyncio.create_task(backfill_from_fubon);backfill 內 get_historical_rate_limiter().acquire(1 req/s)是「阻塞 sleep」且包在 to_thread — 200 個 task 會佔滿 asyncio 預設 executor(min(32, cpu+4) 條 thread)各自睡等 token,總時長 ~200 秒。期間所有其他 to_thread 呼叫(quote/candles/ma/所有富邦 REST、rate limiter acquire 本身)排隊在後 → 一次大批匯入讓整個 API 卡頓數分鐘。task 參照也未保留,例外無人收。

**建議修法**:改成單一背景 task 序列處理 backfill 佇列(一條 worker 逐檔 await),或 historical limiter 提供 async acquire(asyncio.sleep)避免占住 executor thread;保留 task 參照並 log 失敗。

**驗證者**:逐點驗證屬實,無法反駁。(1) bookmarks.py:225/:278 確實對每檔 fire-and-forget create_task(backfill_from_fubon),symbols 上限 200;move_items 還乘上 to_group_ids(上限 20),理論上限 4000 task,且直接呼叫 backfill 繞過 CdpService.get() 的 _last_backfill_attempt 每日 dedup,同 symbol 跨群組重複打富邦。(2) rate_limiter.py:54-81 的 acquire 是 while+time.sleep 同步阻塞,historical bucket 1 req/s;cdp.py:194 包在 to_thread → 每個等 token 的 task 佔住一條 thread 睡。(3) grep 全 backend 無自訂 executor,所有富邦 REST(quote/candles/ma/futures/ws subscribe)的 to_thread 共用 asyncio 預設池 min(32, cpu+4)——一般 8 核機只有 12 條,實際比 finding 寫的更嚴重,十幾檔批次就能塞滿池子,後續呼叫 FIFO 排在以 1/s drain 的 acquire 後面,大批匯入期間 API 卡頓數分鐘成立。(4) task 參照確實未保留、後段例外(upsert/refresh)無人 retrieve。嚴重度 medium 合理(單人本機 app,影響限於自身卡頓);建議修法(單 worker 序列佇列或 async acquire)正確。

### 66. `backend/services/fubon_futures_ws.py:101` — 期貨 WS 每次 _subscribe 都重掛 ws.on(),session 切換/重連累積重複 listener;主動 teardown 又觸發 reconnect 循環

面向:bug | finder:rate-limit | 驗證信心:high

_subscribe() 每次執行都 ws.on('message', ...) + ws.on('disconnect', ...),而 futopt client 也是 SDK singleton、on() 是 append:每天日盤/夜盤切換(≥2 次)加上每次重連都各多掛一份,長期跑下來每根 mxf_candle 推送會被 broadcast N 次、每次斷線 spawn N 個 _handle_disconnect task(_reconnecting 旗標擋住併發 reconnect,但 task churn 和 listener 串列無上限成長)。另外 _teardown_ws() 主動 disconnect 也會讓 SDK 發 disconnect 事件 → _handle_disconnect 把剛建好的新訂閱狀態(self._ws/_current_after_hours)清掉並排程 reconnect,session 切換時多繞一圈斷線重連。

**建議修法**:只在第一次拿到 futopt client 時掛 listener(記旗標),或 subscribe 前先 ws.off() 舊的;_teardown_ws 設一個 self._expect_disconnect 旗標讓 _on_disconnect_raw 忽略主動斷線。

**驗證者**:部分成立、核心問題為真。(1) 「listener 重複累掛」半部被原始碼反駁:fubon-neo 2.2.8 的 ws.on() 委派 pyee 11.1.1 EventEmitter,_add_event_handler 用 OrderedDict 以 handler 為 key(pyee/base.py:152 `self._events[event][k] = v`),而 FuturesWSPool 是 singleton、self._on_message_raw 的 bound method 相等且 hash 相同 → 重複 on() 是覆寫不是 append,「每根 candle broadcast N 次 / N 個 disconnect task」不會發生。(2) 「_teardown_ws 主動 disconnect 觸發假性重連循環」半部經原始碼確認為真:websocket-client _app.py teardown() 在含主動 close() 的所有關閉路徑都 fire on_close(_app.py:385)→ fugle client emit DISCONNECT_EVENT(websocket/client.py:124)→ _handle_disconnect 在 session 切換鎖釋放後把剛建好的新訂閱狀態清掉(fubon_futures_ws.py:177-178)並排程 reconnect;1 秒後對實際仍連線的 client 再 connect()(run_forever 在新 thread raise "socket is already opened",_app.py:343)並對活 socket 重送同 channel subscribe → 每次日/夜盤切換都有狀態錯亂窗口 + 重複訂閱(可能導致重複推送,機制與 finding 所述不同)。建議修法中 _expect_disconnect 旗標正確必要;「ws.off()/只掛一次」則非必要(pyee 已天然去重)。嚴重度 medium 合理。官方期貨 WS 文件(getting-started.txt)未涵蓋這些事件語義,以上以本機安裝 SDK 原始碼為準。

### 67. `backend/services/fubon_ws.py:236` — 每筆 tick 對 broadcaster spawn 一個無界 task,慢前端 client 會讓 task 與記憶體無上限堆積

面向:concurrency | finder:rate-limit | 驗證信心:high

_handle_raw_message 對每筆成交 tick 都 call_soon_threadsafe(asyncio.create_task, broadcast(...))(加上 _on_tick 共兩個 task/tick)。Broadcaster.broadcast 對所有 client 逐一 await send_json:一個 TCP 緩衝塞住的慢/殭屍前端會讓每個 broadcast task 卡住,而上游不感知、繼續每 tick 開新 task——200 檔訂閱在盤中每秒數百 tick,幾分鐘就堆出上萬個 pending task(每個都持有 payload),event loop 與記憶體無上限成長,也拖累同 loop 上的訊號評估。tick 順序也不保證(多 task 並行)。

**建議修法**:改成單一 bounded asyncio.Queue + 一條 broadcaster consumer task(滿了丟最舊 tick——行情 tick 可丟),send_json 包 per-client timeout 或對慢 client 直接踢除,確保一個壞 client 不殃及全體。

**驗證者**:確認為真問題。fubon_ws.py:236-238 每筆 tick 無界 spawn broadcast task;ws_broadcaster.py:28-40 的 broadcast 對所有 client 逐一 await send_json 且無 timeout,client 只在 send 拋例外時才移除——TCP 緩衝塞住但連線仍活的殭屍 client 不會拋例外(uvicorn WS flow control 會讓 send 無限期 await),該 task 永久卡住,而 SDK thread 不感知、繼續每 tick 開新 task(每個持有 payload),pending task 與記憶體無上限成長;多 task 並行也確實不保證 tick 順序。routes/ws.py 的 receive loop 只能偵測斷線、偵測不到不收資料的慢 client,無互補防護。最有力旁證:同 codebase 的 signal_engine.py:40/87-91 對同一條 tick 流已採用 bounded Queue + put_nowait + 滿丟計數(即 finding 建議的修法),唯獨 broadcast 路徑漏掉;main.py:94-96 群益回報也共用同一個有缺陷的 broadcaster。唯一小修正:描述中「_on_tick 共兩個 task/tick」裡的 _on_tick 是 signal_engine.enqueue(put_nowait 非阻塞、task 立即完成),只是 task churn 不會堆積,堆積風險專屬 broadcast 路徑,不影響 finding 主體成立。本機部署、client 少使發生機率較低,medium 嚴重度恰當。

### 68. `backend/routes/watchlist.py:51` — 整個 /api/watchlist legacy route(121 行)已無任何呼叫端,且行為與 bookmarks 路徑分歧

面向:simplify | finder:dead-code | 驗證信心:high

grep 過 frontend/src、bot/src、scripts:前端 lib/api.ts 雖定義 api.watchlist.{list,add,remove}(L391-399),但沒有任何元件呼叫它(BookmarksPanel 等全走 api.bookmarks);bot 與 scripts 也不打 /api/watchlist。僅 backend/tests/test_bookmarks_route.py 還在測它。更糟的是這條死路徑與活路徑行為分歧:watchlist add/remove 會 refresh signal_engine(L95、L118),而 bookmarks add/remove 刻意不 refresh(routes/bookmarks.py L227 註解說明書籤不在訊號評估範圍)——同一份「自選」書籤兩套入口、兩種副作用,留著只會養出漂移。

**建議修法**:刪 backend/routes/watchlist.py、main.py 的 include_router(watchlist.router) 與 import;同步刪 frontend/src/lib/api.ts 的 watchlist client 與 WatchlistResponse/WatchlistRow 型別、backend/tests/test_bookmarks_route.py 中 /api/watchlist 的測試段。

**驗證者**:無法反駁,finding 屬實。(1) 死碼確認:grep 全 repo,`/api/watchlist` 的呼叫端只有 frontend/src/lib/api.ts L391-399 的 client 定義,而 `api.watchlist` 在 frontend/src 沒有任何元件引用(BookmarksPanel 等全走 api.bookmarks);bot/src 與 scripts/ 對 watchlist 零命中;其餘命中都是 docs/specs 與 test_bookmarks_route.py 本身。(2) 行為分歧確認:routes/watchlist.py L93-97(add)與 L116-120(remove)都會 refresh_active_signals,而 routes/bookmarks.py L227-228、L249-251 明確註解「不 refresh signal_engine — 書籤股票不在訊號評估範圍(monitor_list),refresh 只會白白重算 monitor 的 CDP(實測幾秒)」,且 test_bookmarks_route.py L97-163 有四個測試專門鎖死 bookmarks 不得 refresh——這代表 watchlist route 留存的 refresh 副作用正是團隊已認定為「多餘且拖慢操作」的行為,死路徑帶著已被否決的副作用,分歧風險如 finding 所述。另注意 active_signals 的 scope type "watchlist"(models/condition.py WatchlistScope)是資料模型、與此 route 無關,刪 route 不影響。唯一修法細節要修正:WatchlistRow 型別不能刪——api.ts L203 的 BookmarkItem extends WatchlistRow,仍在使用;可刪的是 WatchlistResponse 與 api.watchlist client。

### 69. `backend/services/camarilla.py:106` — CamarillaService.backfill_from_fubon 與 CdpService 逐行重複,且同 symbol 每日對富邦 historical API 打兩次拉同一份資料

面向:rate-limit | finder:dead-code | 驗證信心:high

camarilla.py L106-157 與 cdp.py L174-230 幾乎逐行相同(同樣的 fubon status 檢查、10 天範圍、historical limiter acquire、historical.candles 呼叫、過濾今日、upsert 同一個 daily_ohlc store)。兩個 service 各自維護 per-day 的 _last_backfill_attempt(camarilla.py L67 / cdp.py L126),前端每檔股票同時打 /api/cdp/{s} 與 /api/camarilla/{s}(frontend/src/lib/api.ts L488、L491),所以每個 symbol 每天會消耗 2 次 historical 額度抓完全相同的昨日 OHLC——historical limiter 只有 1 req/s(官方 60/min),monitor/書籤股票多時 refill 與圖表載入會被白白拖慢一倍。

**建議修法**:抽共用的 ensure_daily_ohlc(symbol)(共用一份 per-day attempt map + historical limiter + upsert_daily_ohlc),CdpService/CamarillaService 只保留各自的 compute+refresh;重複的 ~50 行 fetch 邏輯與兩邊都沒在用的 self._lock(cdp.py L123 / camarilla.py L66,後者註解自承未用)一併刪除。

**驗證者**:確認為真問題。(1) 逐行重複屬實:camarilla.py L106-157 與 cdp.py L174-230 除 log 前綴外完全相同(同樣的 FubonStatus 檢查、10 天範圍、historical limiter、candles 呼叫、過濾今日、upsert 同一個 daily_ohlc store)。(2) 雙打機制屬實:兩 service 是獨立 singleton 各自維護 _last_backfill_attempt(cdp L126 / camarilla L67),backfill 無條件打富邦、不檢查共用 store 是否已有昨日資料,historical limiter 確為 1 req/s(rate_limiter.py L100-103);同日同 symbol 兩端點都被打時確實消耗 2 次額度拉同一份資料。(3) 死 _lock 屬實:cdp.py 全檔僅 L123 宣告、camarilla.py L64-66 註解自承未用。惟影響面有一處誇大需修正:前端 IntradayChart 的 CDP/CAM fetch 各有 toggle 閘且預設皆 false(L32-33),非「每檔股票必然同時打兩端點」,只有 user 同時開兩 toggle 才雙打;且 monitor/書籤/signal_engine 的 backfill 路徑全走 CdpService 不經 camarilla,「monitor/書籤股票多時拖慢一倍」是高估。bot 亦只打 cdp。核心重複碼+額度浪費+死碼成立,建議修法(抽共用 ensure_daily_ohlc)合理,medium 嚴重度恰當。

### 70. `backend/models/condition.py:203` — active_signals 的 scope(含 SymbolsScope 最多 500 檔)被引擎完全忽略,API 接受並儲存但不生效

面向:simplify | finder:dead-code | 驗證信心:high | 重複回報:2 個 finder

signal_engine 的 _scope_includes/_scope_symbols(L488、L214)完全以 monitor_list(field_cache keys)為準,參數 active 根本沒用;前端寫死 scope: {type:"watchlist"} 並註明 "legacy; backend ignores"(ActiveSignalEditor.tsx L123)。但 POST/PUT /api/active_signals 仍完整驗證、儲存、回傳 scope,SymbolsScope 還接受 1-500 檔清單——直接打 API 的人會以為訊號被限縮在指定 symbols,實際照樣對 monitor_list 全體評估,觸發範圍與預期不符。

**建議修法**:刪 SymbolsScope/WatchlistScope/Scope 與 ActiveSignalCreate.scope(或最小修:create/update 時拒絕 type != "watchlist",並在 schema_version 註記淘汰計畫),routes/active_signals.py 與 ConfigStore 對應欄位一併移除、舊資料載入時容忍缺欄。

**驗證者**:事實全部核實:signal_engine.py L488-490 _scope_includes 只查 `symbol in self._field_cache`、L214-216 _scope_symbols 只回傳 field_cache.keys(),active 參數未被讀取;test_signal_engine_monitor.py docstring 明寫「不再讀 active.scope」並有測試鎖住此行為,證明 scope 失效是刻意 refactor。但 routes/active_signals.py L33/L49 仍完整驗證並儲存 payload.scope,condition.py L203-205 的 SymbolsScope(1-500 檔)仍是 API 合法輸入,送 type:"symbols" 會通過驗證、儲存、回傳,實際卻被默默無視、照樣對 monitor_list 全體評估。grep backend/frontend/bot 確認:frontend 唯一寫入點寫死 {type:"watchlist"} 且註明 "legacy; backend ignores",SignalRulesDialog 因「顯示會誤導」已移除 scope chip,bot 完全不用 scope——SymbolsScope 在真實 client 零使用。API contract 與實際行為不符且只有 repo 內部註解知情,屬誤導性死碼,simplify/medium 定性恰當,無法反駁。

### 71. `backend/services/capital_models.py:91` — capital_models 三個死成員:Position.unrealized_gross、CapitalEnv enum、OrderResult.seq_no 恆為 None

面向:simplify | finder:dead-code | 驗證信心:high

(1) Position.unrealized_gross(L91-94)全 repo 含 tests 零呼叫端——前端損益走 pnl_base/pnl_base_price 平移口徑,這個 method 是舊口徑殘留,留著有人誤用會跟面板損益對不上。(2) CapitalEnv(L11-13)無人使用,env 全程是 plain str(capital_factory 直接傳 os.getenv 字串)。(3) OrderResult.seq_no(L54)後端從不填值(capital_client 所有 OrderResult 建構都不帶 seq_no),前端也不讀 result.seq_no(刪改用的序號全來自回報 store 的 OrderRecord)——恆 null 的欄位會誤導開發者以為送單結果可直接拿序號刪改。因屬真錢下單模組,從嚴認定。

**建議修法**:刪 unrealized_gross 與 CapitalEnv;OrderResult.seq_no 二選一:刪欄位並同步刪 frontend CapitalOrderResult 型別的 seq_no,或真的從群益送單回訊 parse 出序號填入(若要保留就必須填值,不可恆 None)。

**驗證者**:三子項逐一 grep 驗證皆成立。(1) unrealized_gross 生產碼零呼叫端,前端損益走 pnl_base/pnl_base_price(PositionsList.tsx);唯一呼叫處是 backend/tests/test_capital_position.py 這個專測該 method 的單元測試——finding 說「含 tests 零呼叫端」措辭不精確(test 有呼叫),但測死碼的測試不構成活用途,刪 method 時須連 test_capital_position.py 一起刪。(2) CapitalEnv 全 repo 只有定義處與 plan 文件,capital_factory.py:24 直接傳 os.getenv 的 plain str,enum 無人引用。(3) OrderResult.seq_no:capital_client.py 全部 7 處 OrderResult 建構(L238/243/250/253/275/281/306)皆不帶 seq_no,成功路徑 L253 只填 ok/code/message,真序號走 capital_reply 回報執行緒進 OrderRecord store;前端 api.ts:351 型別宣告了 seq_no 但無任何地方讀 result.seq_no(FlashPanel/OrdersList 的序號全來自 OrderRecord)。tests 有帶值建構但屬測試腳手架,生產恆 None 成立。屬真錢下單模組,恆 null 欄位確有誤導刪改流程的風險,medium 合理。

### 72. `backend/services/local_store/config_store.py:46` — config.json 壞檔復原與匯入備份路徑零測試,且 UnicodeDecodeError 未被涵蓋會讓後端起不來

面向:test-gap | finder:test-gaps | 驗證信心:high

load() 的壞檔處理(改名 .json.corrupt + 重建空設定)是唯一防止「使用者書籤/訊號規則全滅 + 後端起不來」的程式碼,但 grep 整個 tests/ 沒有任何 corrupt 案例;import_config 的 backup-N 編號迴圈同樣沒測。此外 except 只接 (json.JSONDecodeError, OSError):檔案被寫成非 UTF-8(斷電半寫、編輯器改壞)時 open(encoding="utf-8") 丟 UnicodeDecodeError,直接讓 FastAPI startup 炸掉——恰好是這段程式碼宣稱要避免的結局。這種「只在災難時執行的程式碼」沒測試等於沒寫。

**建議修法**:except 加上 UnicodeDecodeError(或乾脆 ValueError + OSError)。補三個測試:1) 寫入壞 JSON 後 load(),斷言產生 .json.corrupt、_data 重建且 seed 出「自選」;2) 非 UTF-8 bytes 同樣不炸;3) import_config 在已有 backup-1 時備到 backup-2。全部用 tmp_path,成本極低。

**驗證者**:已實際重現,無法反駁。(1) UnicodeDecodeError 缺口屬實:用非 UTF-8 bytes 寫入 config.json 後呼叫 ConfigStore.load(),UnicodeDecodeError 直接逃出 except (json.JSONDecodeError, OSError)(實測 issubclass 對兩者皆 False——它是 ValueError 子類、不是 JSONDecodeError 也不是 OSError)。而 load() 在 main.py:48 的 FastAPI lifespan 內被 get_local_store().init() 呼叫,炸掉即後端起不來——正是 config_store.py:47 註解「壞檔不讓後端起不來」宣稱要擋的結局。Windows 環境下使用者用非 UTF-8 編輯器(CP950)改含中文(如「自選」)的 config.json 即可觸發,情境並非不可能。(2) 測試缺口大致屬實:grep backend/tests/ 確認零個 corrupt 案例——.json.corrupt 改名、壞檔後重建+seed「自選」這條災難路徑完全沒測。唯一可挑剔處:finding 說「backup-N 編號迴圈沒測」略有誇大——test_config_store.py:110 的 test_import_backs_up_old_file 已測首次備份(backup-1),但 while 迴圈的遞增分支(已有 backup-1 時備到 backup-2)確實未被執行過,該句仍實質成立。另 atomic_write_json 用 os.replace 原子替換可降低「程式自己寫壞檔」機率,但擋不住外部編輯器/檔案系統層損壞,不足以推翻 finding。severity medium 合理(單機個人工具、可手動救回,但防護本身有洞)。

### 73. `backend/services/cdp.py:218` — backfill 把富邦 historical candle 的 None high/low/close 原樣 upsert,毒化只留最新一筆的 daily_ohlc 快取

面向:type-safety | finder:type-safety | 驗證信心:medium

upserts 用 row.get("high")/get("low")/get("close") 不檢查 None 或非數值;market_cache.upsert_daily_ohlc 以 date 較新者覆蓋且每檔只留一筆。若富邦某交易日 candle 缺欄(停牌/異常列),含 None 的新列會覆蓋掉原本完好的舊列並持久化到 daily_ohlc.json;之後 refresh 的 float(row["high"]) 丟 TypeError 被吞成 warning → 該檔 CDP/prev_close 從 field_cache 消失,依賴 prev_close 的 day_change_pct、漲停策略全部靜默失效,且因為「每天只 backfill 一次」要等隔天才有機會自癒。camarilla.py 第 144 行同樣寫法。

**建議修法**:append 前驗 high/low/close 皆為可轉 float 的數值,缺欄列直接跳過(沿用「寧缺勿錯」慣例);cdp.py 與 camarilla.py 兩處同步修。

**驗證者**:程式缺陷屬實:cdp.py:212-227 與 camarilla.py:139-154 確實把 row.get("high"/"low"/"close") 不經驗證直接 upsert;market_cache.upsert_daily_ohlc(71-86 行)是每檔單槽、date 較新者覆蓋、立即持久化,一筆含 None 的新列會永久毀掉舊的完好列。下游 refresh() 的 try/except (ValueError, TypeError) 證明作者本就預期壞資料,但驗證點在落盤之後,毒列已持久化;加上 _last_backfill_attempt 每天只 backfill 一次,要等隔日才自癒——finding 的失效鏈推演正確。已照富邦文件工作流程 WebFetch 官方 Historical Candles 文件:文件未規範 OHLC 欄位是否可為 null、也無停牌日行為說明,無法證明缺欄列不可能出現,反駁不成立。兩點保留:(1) 觸發機率未知——官方文件沉默,無法證實富邦實際會回 null 欄位列,故 confidence 取 medium 而非 high;(2) finding 輕微誇大——refresh 失敗不會清掉既有 in-memory cache,同 process 內既有 symbol 只是 stale,真正「從 field_cache 消失」要在 process 重啟後或新加入的 symbol 才發生,但本機後端日常重啟,實質影響仍成立。建議修法(append 前驗可轉 float、缺欄跳過,cdp/camarilla 同步)成本極低且符合專案寧缺勿錯慣例。

## Low(30 項)

### 74. `backend/services/rate_limiter.py:60` — rate < 1 時 capacity 預設等於 rate,acquire(1) 直接 ValueError,所有 REST 呼叫全掛

面向:bug | finder:fubon-market | 驗證信心:high

capacity 預設 = rate;若使用者把 FUBON_RATE_LIMIT_PER_SEC 或 FUBON_HISTORICAL_RATE_LIMIT_PER_SEC 設成 0.5 之類想再調慢,capacity=0.5 < tokens=1,每次 acquire 都 raise ValueError 而不是等久一點——調慢限流的合法操作會讓全部富邦 REST 直接失效。

**建議修法**:capacity 下限取 max(rate, 1.0),或文件化並在建構時對 rate<1 提早報錯;語意上 rate<1 應該是「等更久」而非拒絕。

**驗證者**:已實際重現:python 執行 TokenBucket(rate=0.5).acquire() 確實 raise「requested 1 tokens > capacity 0.5」。capacity 預設=rate(rate_limiter.py:29),acquire 在 tokens>capacity 時直接 ValueError(:60-63);grep 確認全部六個呼叫點(fubon_client/ma_service/routes/ma/cdp/camarilla/fubon_futures)都是 acquire() 預設 1 token,rate 直接吃 env FUBON_RATE_LIMIT_PER_SEC / FUBON_HISTORICAL_RATE_LIMIT_PER_SEC 無下限保護,backend/tests 也沒有 rate_limiter 測試。使用者把 env 設 <1(合法的「再調慢」操作,historical 預設 1.0 已踩在邊界)會讓所有富邦 REST 呼叫每次都 raise 而非等更久。預設值下不觸發、僅誤設時爆且會 fail loud,low 嚴重度認定恰當。建議修法合理:capacity 下限 max(rate, 1.0) 或建構時對 rate<1 提早報錯。

### 75. `backend/services/fubon_client.py:129` — _background_retry 失敗時每 5 分鐘發一則 notify_critical,整夜斷線會 alert 轟炸

面向:error-handling | finder:fubon-market | 驗證信心:high

_login_with_retry 每次耗盡嘗試都呼叫 alerts.notify_critical,而 _background_retry 每 300 秒呼叫一次 max_attempts=1 的 _login_with_retry——富邦長時間不可用時(例如夜間維護),critical alert 每 5 分鐘一發,造成 alert 疲勞,真正需要注意的訊息會被淹掉。

**建議修法**:背景重試路徑只在狀態轉換時通知(第一次失敗發一次、恢復時發一次 recovered),或對 notify_critical 加 dedup/冷卻時間。

**驗證者**:確認為真問題。fubon_client.py:123-131 的 _background_retry 在 status==ERROR 期間每 300 秒呼叫 _login_with_retry(max_attempts=1),而 _login_with_retry 在嘗試耗盡時(第 87-90 行)無條件呼叫 alerts.notify_critical,沒有狀態轉換判斷(首次失敗才發/恢復才發)。alerts.py 的 notify_critical 每次都 logger.error + POST Discord webhook,grep 全 backend 確認無任何 dedup/cooldown/throttle 機制(signal_engine 的 cooldown 是訊號觸發冷卻,與 alert 無關;rate_limiter 只管富邦 REST)。富邦長時間不可用時(夜間維護期啟動後端)會每 5 分鐘一則 critical,8 小時約 96 則,alert 疲勞描述屬實。唯一緩解是 ALERTS_DISCORD_WEBHOOK_URL 未設時只剩 error log,但那是部署組態非程式防護。純本地邏輯,不涉 SDK 行為假設,不需查富邦文件。嚴重度 low 合理。

### 76. `backend/services/fubon_ws.py:276` — shutdown 的 disconnect 會觸發 on_disconnect 重連 handler,關機中 spawn _reconnect task

面向:error-handling | finder:fubon-market | 驗證信心:high

shutdown() 逐一 ws.disconnect,但 on_disconnect handler 仍掛著且沒有 closing flag,disconnect 事件會經 call_soon_threadsafe 排 _reconnect task——在 lifespan 收尾階段嘗試重連,輕則 log 噪音、重則 loop 已關時 create_task 丟 RuntimeError 進 loop exception handler。

**建議修法**:shutdown 開頭設 self._closing = True,on_disconnect 與 _reconnect 檢查 flag 直接 return;或 disconnect 前先 ws.off('disconnect', ...) 拆 handler。

**驗證者**:反駁失敗,finding 成立。直接讀本機 venv 的 SDK 原始碼驗證(backend/.venv/Lib/site-packages/fugle_marketdata/websocket/client.py,即 fubon-neo 2.2.8 實際使用的 WS client):(1) disconnect()(L200)呼叫 WebSocketApp.close(),run_forever 收尾必回呼 __on_close(L123)→ emit DISCONNECT_EVENT——主動關閉一樣會觸發 disconnect 事件,SDK 無區分旗標;(2) fubon_ws.py 全檔無 _closing flag,on_disconnect(L174-179)無條件 call_soon_threadsafe 排 _reconnect,shutdown()(L273-282)也沒先 ws.off('disconnect', ...)(SDK 有提供 off,L151);(3) main.py:115 在 lifespan 收尾 await pool.shutdown(),loop 必然還在跑,_reconnect task 一定會 spawn。後果視時序:輕則 log 噪音+pending task 被 _cancel_all_tasks 取消;若 loop 多活 1 秒,_reconnect 會真的重連富邦,且 connect()(L190-198)是無 sleep 的 busy-wait,關機階段可能卡住;事件若晚於 loop 關閉,call_soon_threadsafe 在 SDK thread 丟 RuntimeError。建議修法(closing flag 或 disconnect 前 ws.off)皆可行。嚴重度 low 合理——只影響關機收尾,不影響行情/交易正確性。

### 77. `backend/services/fubon_ws.py:254` — trades 訂閱 dict 字面值在 _real_subscribe/_reconnect 重複,overnight.py 還伸手進 pool 私有狀態抄第三份

面向:simplify | finder:fubon-market | 驗證信心:high

{"channel": "trades", "symbols": ...} 的訂閱呼叫在 _real_subscribe(131)與 _reconnect(254)各寫一份,services/overnight.py:40-49 又直接操作 pool._ws_handles/_conn_subs/_ensure_handle 私有成員抄了第三份同樣的「pop handle → ensure → 重訂閱」流程。三份漂移風險高(改 channel 或加 books 訂閱時容易漏一處),overnight 越過 _lock 直接動共享狀態也跟 _reconnect 有 race 可能。

**建議修法**:抽一個 WSPool 公開方法 async def resubscribe_conn(conn_idx)(內含 pop handle、ensure、批次重訂閱、持 _lock),_reconnect 與 overnight 都改呼叫它。

**驗證者**:grep 實證三份 {"channel": "trades", "symbols": ...} subscribe 呼叫確實存在:fubon_ws.py:132(_real_subscribe)、:255(_reconnect)、overnight.py:48;overnight.py:40-43 確實直接操作 pool._ws_handles/_conn_subs/_ensure_handle 私有成員,完整複製 _reconnect 的 pop→ensure→重訂閱流程且全程不持 _lock。race 主張也成立:雖同屬單一 event loop 非 thread race,但 _ensure_handle 內 await asyncio.to_thread(ws.connect) 是讓出點,8:25 overnight 與 disconnect 觸發的 _reconnect 可交錯,對同一個 SDK singleton ws 重複 ws.on() 註冊 callback + 重複 connect(機率低,low 嚴重度恰當)。backend/tests/ 無測試釘死這些私有存取,建議的 resubscribe_conn 公開方法重構可行。純結構重複問題,不涉 SDK 行為假設,無需查富邦文件。確認為真問題。

### 78. `backend/services/fubon_client.py:154` — intraday_ticker/technical_rsi 無任何呼叫端,FubonStatus.DEGRADED 從未被賦值

面向:simplify | finder:fubon-market | 驗證信心:high | 重複回報:2 個 finder

全 backend 只有 routes 呼叫 intraday_quote,intraday_ticker 與 technical_rsi 是死碼;三個 wrapper 重複同一套 guard+limiter+to_thread 樣板。FubonStatus.DEGRADED 列在 enum 裡但沒有任何路徑會設它,docstring 的 degraded mode 與實際只有 OK/ERROR 兩態不符,讀碼會誤導。

**建議修法**:刪掉兩個無呼叫端的 wrapper(要用時再加),DEGRADED 要嘛移除、要嘛實作真正的降級語意;若保留多個 wrapper,抽一個 _call(fn, **kwargs) 共用 guard+limiter。

**驗證者**:已 grep 全 repo(backend/、frontend/src/、bot/src/、scripts/)驗證:(1) intraday_ticker 與 technical_rsi 只在 fubon_client.py L154/L163 定義,零呼叫端——routes/quote.py 只用 intraday_quote,其餘服務(candles/ma/cdp/camarilla)直接拿 fubon.sdk 打 SDK 不經 wrapper,確為死碼;(2) FubonStatus._status 只在 L35/L85 設 ERROR、L69 設 OK,DEGRADED 無任何賦值路徑(fubon_ws.py:271 是另一 enum WSPoolStatus、signal_engine.py:108 的 "degraded" 是自家 _degraded 欄位,皆無關);所有消費端只比對 != FubonStatus.OK,DEGRADED 即使設了也無區別語意;(3) 檔案 docstring「degraded mode」與 L147 錯誤訊息「(degraded mode)」與實際 OK/ERROR 兩態狀態機不符,確會誤導。純本地死碼判定不涉 SDK 行為,無需查富邦文件。嚴重度 low/simplify 恰當,finding 成立。

### 79. `backend/services/fubon_futures.py:73` — resolve_active_symbol 用 naive datetime.now() 比對 expiry,與子系統其他處的 Asia/Taipei 不一致

面向:bug | finder:fubon-futures | 驗證信心:high

today_str 來自無 tz 的 datetime.now()(機器本地時間),而 routes/mxf.py 與 fubon_futures_ws.py 都明確用 ZoneInfo("Asia/Taipei")。若主機時區不是台北(VPS、改機器),結算日前後 expiry > today 的比較會差一天,可能在結算日仍選到已結算合約、或提前一天跳月。目前本機跑在台灣影響有限,但屬同子系統內的時區口徑分裂。

**建議修法**:改用 datetime.now(ZoneInfo("Asia/Taipei")),today_str 與 cache 時間皆以 TPE 為準,和 route/WS 對齊。

**驗證者**:查證屬實:fubon_futures.py:73 確為 naive datetime.now(),today_str(L113)與 expiry 比較(filter_active_mxf_symbol 的 expiry > today 字串比較)都依機器本地日期;而同子系統 routes/mxf.py:17/45、fubon_futures_ws.py:14/77、甚至 test_fubon_futures.py:9 都明確用 ZoneInfo("Asia/Taipei"),時區口徑分裂為真。錯一天場景可具體推演:expiry 來自富邦 endDate(台北曆日),若主機時區在台北之後(如 UTC+13),結算日前一晚夜盤(台北 D-1 19:00 起)本地日期已是 D,expiry(D) > today(D) 為 false,仍在交易的近月合約被提前一天剔除而跳月。此函式非死碼(routes/mxf.py、main.py 啟動、WS 重訂閱鏈都呼叫)。唯一緩解是專案「所有人本機跑在台灣」故實際觸發機率極低,但 finding 已據此標 low,嚴重度恰當。屬真問題,建議修法(datetime.now(ZoneInfo("Asia/Taipei")))正確。

### 80. `backend/services/fubon_futures_ws.py:29` — FuturesWSPool 狀態機完全沒有測試,只測了純函式 target_after_hours_flag

面向:test-gap | finder:fubon-futures | 驗證信心:high

test_fubon_futures_ws.py 只覆蓋 target_after_hours_flag;pool 的核心行為——session 切換 teardown/subscribe、reconnect 上限與 reconcile 重置、stop 後不再重連、訊息過濾與 broadcast 形狀——全靠盤中實機驗證。本次發現的字串訊息丟棄(line 150)正是這類測試會直接抓到的 bug。

**建議修法**:用 fake ws 物件(記錄 on/connect/subscribe/disconnect 呼叫)注入,對 _handle_message 餵 JSON 字串與 dict 兩種輸入驗證 broadcast payload;對 _schedule_reconnect 驗證達上限後停止、reconcile 後恢復。

**驗證者**:查證屬實:test_fubon_futures_ws.py 全檔僅 7 個 target_after_hours_flag 參數化 case,grep 全 backend 無任何測試 import 或建構 FuturesWSPool,session 切換/reconnect 上限與 reconcile 重置/stop/訊息過濾與 broadcast 形狀全無覆蓋。finding 引用的 line 150 佐證也成立:期貨端 `raw.get("data") if isinstance(raw, dict) else None` 會把字串訊息靜默丟棄,而同 repo 股票端 fubon_ws.py:191 明確以 `json.loads(raw) if isinstance(raw, str) else raw` 處理 SDK 送字串的情況——對 _handle_message 餵 JSON 字串與 dict 兩種輸入的單元測試會直接抓到這個差異。建議修法(fake ws + monkeypatch get_fubon/get_broadcaster)符合專案既有 fake 注入測試慣例(如 test_capital_client.py 的 fake COM),pool 邏輯為純 asyncio、可離線測。嚴重度 low(test-gap)認定合理。

### 81. `backend/services/signal_engine.py:492` — _eval_conditions 是死碼,且與現行 combine 邏輯已分岔

面向:simplify | finder:signal-engine | 驗證信心:high | 重複回報:2 個 finder

全 backend 只有定義、無任何呼叫點(_evaluate 走 _eval_with_touch_meta + _eval_non_proximity + _combine_results)。它把 proximity 結果與其他條件攤平在同一個 results list 的合併方式,跟 _combine_results 的三段式語意已不一致,留著會誤導後續維護者改錯地方。

**建議修法**:直接刪除 _eval_conditions。

**驗證者**:確認為真死碼。全 repo grep「_eval_conditions」,程式碼命中僅 backend/services/signal_engine.py:492 的定義本身,backend/frontend/src/bot/src/scripts/tests 均無任何呼叫點,也無 getattr 動態呼叫。現行 _evaluate(signal_engine.py:244-246)走 _eval_with_touch_meta + _eval_non_proximity + _combine_results 三段式路徑。docs/superpowers/plans/2026-05-19-chart-and-signal-tweaks.md:2014 更直接記載當時已用新路徑「取代」_eval_conditions,只是沒刪。且 _eval_conditions 把 proximity ok 與其他條件攤平在同一 list,缺少現行 touch metadata(direction/role)語意,留著會誤導維護者。建議修法(直接刪除)成立。

### 82. `backend/services/alerts.py:49` — critical alert webhook 不檢查 HTTP 回應狀態,4xx/429 靜默吞掉

面向:error-handling | finder:signal-engine | 驗證信心:high

notify_critical 只 catch 連線層例外;Discord 回 400(embed 格式錯)、401(webhook 失效)、429(rate limit)都當成功。這是 WS 斷線 circuit-open、evaluator 過載自動停用等最後一道告警通道,失效時完全無感(只剩本機 log)。

**建議修法**:resp = await client.post(...) 後檢查 resp.status_code >= 400 時 logger.error 帶 status 與 body 前 200 字;429 可讀 retry_after 重試一次。

**驗證者**:確認屬實。httpx 預設不對 4xx/5xx 拋例外(需顯式 raise_for_status),alerts.py:49 的 await client.post() 回傳值被丟棄、無任何 status 檢查,try/except 只攔得到連線層錯誤;Discord 回 400/401/404/429 都會被當成功且零記錄。docstring 宣稱「Fail silently (only logs)」但 HTTP 層失敗連 log 都沒有,連自述的最低標準都未達。呼叫處(fubon_ws.py:266 circuit-open、fubon_client.py:87、signal_engine.py:720、overnight.py:32/55)全是最後一道告警通道,webhook 被撤銷或告警風暴撞 429 是現實場景。backend/tests/ 無 alerts 測試。嚴重度 low 合理(本機 logger.error 仍會記下告警本身)。

### 83. `backend/services/discord_notifier.py:52` — 訊號推播不檢查 bot 回應狀態,非 2xx 視同成功

面向:error-handling | finder:signal-engine | 驗證信心:high

send_signal 只 catch 例外;bot 端若回 4xx/5xx(payload schema 不合、bot 內部錯誤)會被當成功靜默吞掉,訊號圖卡沒送到也不留痕,只能事後對 signals_log 才發現缺推播。

**建議修法**:post 後檢查 status_code 非 2xx 時 logger.warning(status + body 摘要),與既有的連線失敗 log 同層級。

**驗證者**:確認為真問題。discord_notifier.py:52 的 client.post 後無 status 檢查也無 raise_for_status(),httpx 對 4xx/5xx 不拋例外,只有 transport error 會進 except,故非 2xx 被靜默當成功。對端 bot/src/push-server.ts 確實會回非 2xx:405/404/415/400(invalid json、body >64KB、parseSignalPayload schema 不合),成功才回 202;URL 設錯路徑或 bot/backend schema 漂移時推播會無聲丟失。docstring 宣稱「失敗 silent log」但非 2xx 連 log 都沒有,屬遺漏而非設計。嚴重度 low 與建議修法(非 2xx 時 logger.warning status+body 摘要)均恰當。

### 84. `backend/services/signal_engine.py:91` — _dropped_today 名為當日計數但永不歸零

面向:bug | finder:signal-engine | 驗證信心:high

queue 滿時累加的 _dropped_today 沒有任何 daily reset(heartbeat 跨午夜分支清了 day_volume / touch_count / strategy state,獨漏它),health endpoint 顯示的是 process 起算的累積值,跨多日後無法判斷今天是否仍在掉 tick。

**建議修法**:在 heartbeat 跨午夜 daily reset 分支加 self._dropped_today = 0;或改名 _dropped_total。

**驗證者**:確認屬實。_dropped_today 全 repo 僅三處:__init__ 初始化為 0(signal_engine.py:66)、enqueue 的 QueueFull 累加(:91)、health() 以 "dropped_today" 輸出(:107)。heartbeat 跨午夜 daily reset 分支(:193-203)清了 _day_volume(經 _refill_field_cache 的 clear)、touch_count(_gc_touch_counts)、strategy state(_reset_daily_strategy_state),唯獨沒重設 _dropped_today——與 finding 描述完全一致。後端是長駐 process,跨多日後 health 顯示的是累積值,無法判斷當天是否仍在掉 tick,名實不符。無測試覆蓋、無其他消費者依賴累積語意,修法(daily 分支加歸零或改名 _dropped_total)皆可行。嚴重度 low 合理:只影響監控指標語意,不影響訊號評估或下單路徑。

### 85. `backend/services/capital_models.py:11` — capital_models 三處死碼:CapitalEnv 無引用、OrderResult.seq_no 生產端永遠 None、unrealized_gross 僅測試引用

面向:simplify | finder:capital-core | 驗證信心:high

(1) CapitalEnv enum 全 repo 只有定義處,factory/client 都用裸字串比對 env——正好是上一條 env 驗證缺口該用的東西;(2) OrderResult.seq_no 在 _execute_write 從不填(SendStockOrder 同步回的 message 內含序號但未解析),前端 api.ts 也不讀它,只有測試假資料在填,介面上是個永遠為 null 的承諾;(3) Position.unrealized_gross 只被 test_capital_position.py 引用,生產損益走 pnl_base 平移。死欄位/死方法留著會誤導之後接手的人以為有值可用。

**建議修法**:CapitalEnv 接進 factory 的 env 解析(一石二鳥);OrderResult.seq_no 要嘛從 SendStockOrder 回傳 message 解析填值、要嘛移除欄位並同步改前端型別;unrealized_gross 若無近期使用計畫連同測試移除。

**驗證者**:三處死碼均屬實:(1) CapitalEnv 全 repo 僅 capital_models.py:11 定義處(另一處是 docs plan 文件),capital_client.py:148 用裸字串 `self._env == "test"`、capital_factory.py:24 用 `os.getenv("CAPITAL_ENV", "test")`,enum 未接線;(2) OrderResult.seq_no 在 capital_client.py 全部 7 處建構(238/243/250/253/275/281/306)皆未填,_execute_write 拿到 (message, code) 後不解析序號,唯一填值處全在 backend/tests/;前端 api.ts:351 型別宣告了 seq_no 但無元件讀取(FlashPanel/OrdersList 讀的是 OrderRecord.seq_no),bot/src、scripts 無引用——生產上是永遠 null 的承諾;(3) unrealized_gross 全 repo 只被 test_capital_position.py 與 plan 文件引用,且為 method 不參與 pydantic 序列化,前端不可能間接使用,生產損益走 pnl_base 平移。severity low 合理,不影響下單安全。

### 86. `backend/services/capital_client.py:184` — 測試缺口:COM 執行緒亡故後「已入佇列寫入」與回呼例外路徑無測試

面向:test-gap | finder:capital-core | 驗證信心:high

test_run_thread_exit_drops_status 只驗執行緒結束會降 status,沒驗「執行緒死時佇列裡還有 (fn, fut)」的下場——也就是上面懸掛 finding 的核心場景;test_pump_once_swallows_exceptions 驗了不炸,但沒驗回呼例外有無留痕(目前確實無痕,見 capital_com finding)。這兩條都是真錢路徑的失效模式,行為一旦修正也需要測試釘住意圖(future 必須被 fail、回報丟棄必須有 log)。

**建議修法**:修懸掛/吞例外兩個 finding 時同步加測:(1) 佇列預塞一筆命令再讓 _run 收 sentinel 結束,斷言該 future 被 set_exception 而非懸掛;(2) caplog 斷言 OnNewData 回呼例外時有 error/exception 級 log。

**驗證者**:逐項核實成立。(1) capital_client.py _run() 的 finally(行205-210)只降 status、不 drain _cmd_q,執行緒亡故時佇列殘留的 (fn, fut) 永不 resolve;test_run_thread_exit_drops_status(test_capital_client.py:112)只斷言 status/last_error,沒有預塞命令驗 future 被 set_exception——缺口屬實,且 _execute_write 的 status 檢查(行239)與 put(行246)間有 race window、await fut 無 timeout,場景可發生。(2) capital_com.py 的 OnNewData/OnRealBalanceReport/OnProfitLossGWReport(行164-193)都是 except Exception: pass,無任何 log;test_capital_com.py:103/122 的 swallow 測試只驗不炸,無 caplog 斷言——回報丟棄無痕屬實。兩者皆真錢路徑失效模式,test-gap/low 定性恰當(行為缺陷本身由另兩個 finding 承擔)。

### 87. `backend/routes/cdp.py:16` — CDP/Camarilla route 對永遠無資料的 symbol 每個請求都重打一次 historical backfill

面向:rate-limit | finder:routes-app | 驗證信心:high

CdpService.get() 已有「每 symbol 每天最多嘗試一次 backfill」的 guard,但 route 在 get() 回 None 後又無條件呼叫 backfill_from_fubon — 這條路不受每日 guard 管。對下市、打錯或富邦無歷史資料的代碼,每次 GET /api/cdp/{symbol}(camarilla.py 同構)都會真打一次 historical.candles:先 block 等 1 req/s 的 historical token、再吃一次 60/min 配額,還會排擠書籤新增時的背景 backfill。前端每次選股 + Discord bot 每次查詢都會踩。

**建議修法**:把 lazy backfill 移進 service.get()(讓每日 attempt 紀錄統一管),route 只讀 get() 結果;或對「backfill 回 False」的 symbol 記 negative cache(當日不再重試)。

**驗證者**:確認為真問題。CdpService.get()(services/cdp.py:136-138)的每日 backfill guard 只管 get() 內部那條路;route(routes/cdp.py:16、camarilla.py:15)在 get() 回 None 後無條件直呼 backfill_from_fubon,該方法本身無任何當日 attempt 紀錄。對富邦永遠無歷史資料的 symbol(下市/打錯),backfill 回 False、cache 永不填,get() 永遠回 None,於是當日每個 GET 都經 route 真打一次 historical.candles(當日首次甚至兩次:get() 內 + route),每次先 blocking acquire 共用的 1 req/s historical token bucket(cdp.py:194),與 watchlist.py:90、bookmarks.py:225/278、monitor_list.py:59 的背景 backfill 搶同一桶。呼叫端也屬實:前端 api.ts:488/491 每次選股、bot data.ts:26 每次查詢都打這兩個端點。finding 描述、影響路徑、嚴重度(low,limiter 是 blocking 不會真 429 但會消耗配額並排擠)全部與程式碼相符,建議修法(guard 統一進 service 或 negative cache)合理。

### 88. `backend/routes/camarilla.py:12` — camarilla.py 與 cdp.py 路由整段重複(get → 失敗 backfill → 再 get → 503)

面向:simplify | finder:routes-app | 驗證信心:high

兩檔除了 service 與 error 字串外逐行同構,連 `# type: ignore[return-value]` 都一樣;service 層(cdp.py/camarilla.py services)的 backfill_from_fubon 也是大段複製。之後修 lazy-backfill 行為(如上一條 finding)得改兩處,容易只修一邊造成行為分岔。

**建議修法**:抽一個共用 helper `get_levels_or_503(service, symbol, error_prefix)` 給兩條 route 用;service 層的 historical 抓取+upsert 也可抽共用函式,只留各自的 compute。

**驗證者**:實讀兩組檔案確認重複屬實。Route 層:backend/routes/camarilla.py:11-21 與 backend/routes/cdp.py:11-22 除 service getter 與 error 字串前綴外逐行同構,「get → backfill → 再 get → 503」流程與結尾 `# type: ignore[return-value]` 完全相同。Service 層:camarilla.py 的 backfill_from_fubon(L106-157)與 cdp.py 的同名方法(L174-230)除 log 前綴外整段複製(fubon 檢查、10 天範圍、rate limiter、candles 呼叫、過濾今日、upsert、refresh),get() 的每日首次 backfill 邏輯也各複製一份。且已出現漂移徵兆:cdp 版 get() 有完整 stale-state docstring 而 camarilla 版沒有、camarilla 的 _lock 多了 "kept for future use" 註解——正是「只修一邊造成行為分岔」的前兆。建議的抽共用 helper 修法合理,嚴重度 low 標得恰當(現行為正確,純維護性問題)。

### 89. `backend/routes/watchlist.py:43` — 「自選」書籤用名稱解析,被 PATCH 改名後 /api/watchlist 會默默另建空群組

面向:bug | finder:routes-app | 驗證信心:high

_default_group_id 以 name == "自選" 找群組,找不到就 create_group 新建。但 PATCH /api/bookmarks/{bid} 允許把自選書籤改名(沒有任何保護),改名後 watchlist alias 找不到 → 自動建一個全新的空「自選」群組:GET /api/watchlist 變空、POST 加進新群組,使用者原本的自選股「看似消失」。前端主要已走 bookmarks API,但 api.ts 仍保留 watchlist surface,且 add 路徑同樣有「先寫 store 再 subscribe、失敗只 warn」的不一致(同 bookmarks add_items finding)。

**建議修法**:在 seed 時給自選群組固定 id 或加 is_default 標記,alias 以標記解析而非名稱;或在 update_bookmark 擋預設群組改名。若 /api/watchlist 已確定退役,直接移除這條 route 與前端 surface 更乾淨。

**驗證者**:機制屬實:watchlist.py:40-43 以 name=="自選" 解析、找不到就 create_group;bookmarks.py 的 PATCH 只擋系統書籤(SYSTEM_TOP_GAINERS_ID),「自選」可自由改名;config_store.py 的 _seed_defaults 只在「零 user 書籤」時補建,改名後不會重 seed——改名後打 /api/watchlist 確會默默另建空「自選」群組。影響面比描述再小一點:grep 確認 frontend/src 無任何地方呼叫 api.watchlist(api.ts:391-400 的 surface 是死碼)、bot/scripts 也不打此 API,且 active_signals scope=watchlist 實際走 monitor_list 不靠名稱解析;但 route 仍在 main.py:140 註冊、對外可達,缺陷存在於 live code path。low 嚴重度恰當,建議的修法(移除 route + 前端死 surface)合理。

### 90. `backend/middleware/auth.py:38` — PUBLIC_PATHS 的 /ws/signals 豁免是死碼:路徑早改成 /ws/realtime,且 BaseHTTPMiddleware 根本不處理 websocket scope

面向:simplify | finder:routes-app | 驗證信心:high | 重複回報:3 個 finder

實際 WS 路由是 routes/ws.py 的 /ws/realtime;更根本的是 Starlette BaseHTTPMiddleware 對 scope type != "http" 直接 pass-through,WS handshake 從來不會進 dispatch,這條豁免永遠不會命中,只會誤導讀者以為 WS 是靠這裡放行。另外檔尾 `from typing import Any` 配 `# Re-export for typing` 是為了 31 行 annotation 的補丁(靠 __future__ annotations 才不炸),屬同一類待清理瑕疵。

**建議修法**:刪掉 `request.url.path == "/ws/signals"` 分支,在 docstring 註明「WS 認證在 routes/ws.py 用 query param 自行處理(BaseHTTPMiddleware 不經手 websocket)」;`from typing import Any` 移回檔頭正常 import。

**驗證者**:三項主張皆實證確認:(1) 全 repo grep「/ws/signals」只命中 auth.py:38 自身,實際 WS 路由是 routes/ws.py:18 的 /ws/realtime,前端 useSignalsStream.ts:87 也只連 /ws/realtime,該豁免路徑無任何使用者;(2) 讀本機安裝的 starlette base.py(backend/.venv/.../starlette/middleware/base.py:102-104),BaseHTTPMiddleware.__call__ 對 scope type != "http" 直接 pass-through、不進 dispatch,故 WS handshake 從不經過此豁免,即使路徑正確也是死碼;(3) auth.py:57-58 的 from typing import Any 確在檔尾,僅供第 31 行 annotation 使用,靠第 9 行 from __future__ import annotations 才不炸,屬同類待清理瑕疵。無安全影響(WS 認證由 routes/ws.py query param 自理),low/simplify 定性恰當,建議修法正確。

### 91. `backend/main.py:49` — fire-and-forget create_task 沒保留 reference;shutdown cancel 後也沒 await

面向:concurrency | finder:routes-app | 驗證信心:high

`asyncio.create_task(bootstrap_symbols_if_missing())` 沒存 reference,event loop 對 task 只持弱參考,CPython 文件明載執行中的 task 可能被 GC 掉(routes/bookmarks.py、watchlist.py、monitor_list.py 的 `create_task(backfill_from_fubon(s))` 同款)。另外 shutdown 段對 overnight/top_gainers/reconcile task 只 cancel 不 await,loop 關閉時會留 "Task was destroyed but it is pending" 噪音且 cancel 的清理不保證跑完。實際踩到機率低,但一旦發生是無聲的(bootstrap 沒跑完 → symbols 表空 → 加股全 404)。

**建議修法**:用模組級 set 收 task reference(done callback discard),或至少 `app.state.bg_tasks` 持有;shutdown 段 cancel 後 `await asyncio.gather(*tasks, return_exceptions=True)`。

**驗證者**:程式碼事實全部核實:main.py:49 與 routes/watchlist.py:90、bookmarks.py:225/278、monitor_list.py:59 的 create_task 確實都沒存 reference,而「event loop 只持弱參考、未被引用的 task 可能執行中被 GC」是 CPython asyncio.create_task 官方文件明文警告(純 Python 行為,不涉富邦 SDK)。實務上 await 中的 task 多半被 future callback 鏈保活所以罕見踩到,但文件明確說不可依賴,finding 自評 low 嚴重度恰當。shutdown 段(main.py:108-111)cancel 後不 await 也屬實;惟其中「loop 關閉時留 Task was destroyed 噪音」一句偏嚴——uvicorn 經 asyncio.run 收尾時 _cancel_all_tasks 會補 cancel+gather,標準路徑下該警告通常不會出現;真正的問題是 cancellation 清理可能在 fubon.shutdown() 之後才跑、順序不保證。建議修法(收 reference + cancel 後 gather)正確。整體判定為真問題、low 嚴重度合理。

### 92. `backend/routes/config_io.py:27` — import 已落盤後 resync 失敗會回 500,client 誤以為匯入失敗

面向:error-handling | finder:routes-app | 驗證信心:high

import_config 先把新 config 寫穿到 config.json(含備份),之後 resync_from_config 若 raise(例如 refresh_active_signals 內部錯誤 — per-symbol subscribe 有 try 包,但 refresh 沒有)→ route 回 500。此時匯入其實已生效且持久化,使用者看到失敗訊息可能重試或以為要重匯,且當下訂閱狀態是「舊訂閱已退、新訂閱不完整」,要到重啟才會由 startup resync 補齊。

**建議修法**:把 resync_from_config 包 try/except:失敗時回 200 但帶 `{"status": "imported", "resync": "failed", "detail": ...}`,明確告知設定已套用、訂閱需重啟或重打 resync;或提供獨立的 POST /api/config/resync 讓前端重試。

**驗證者**:確認為真問題。config_io.py:24 先呼叫 import_config(config_store.py:220-234 內備份+_persist() 已落盤),之後第 27 行 await resync_from_config() 完全沒包 try/except,任何例外即回 500。而 resync 內確有可達的未防護炸點:(1) import_config 只驗 schema_version、不逐列驗證,匯入缺欄位的 active_signals 會在 signal_engine._row_to_active(signal_engine.py:115-122 直接取 r["id"]/r["name"]/r["filter_json"]/r["scope"])KeyError 或 pydantic ValidationError,refresh_active_signals 在 route 和 lifecycle_sync 兩層都沒包;(2) lifecycle_sync.py:32-39 訂閱迴圈只 catch RuntimeError,it["symbol"] 缺欄位的 KeyError 會逸出。前端 api.ts:513 用 fetchJSON,非 2xx 即 throw → user 看到匯入失敗但 config 已持久化;且 resync 先退舊訂閱再訂新,中途炸掉即停在「舊訂閱已退、新訂閱不完整」,grep 確認無獨立 resync 端點,只有 main.py:76 startup 會補,確需重啟。日常用自家匯出檔不易觸發故 low 嚴重度恰當,但手編 JSON 匯入(備份機制即為此設)可達,finding 描述準確。

### 93. `backend/services/local_store/market_cache.py:77` — upsert_daily_ohlc 資料完全相同也判 changed=True,每次 backfill 都全檔重寫

面向:simplify | finder:local-store | 驗證信心:high

cur 存在且 r["date"] >= cur["date"] 就無條件覆寫並標 changed,即使新舊四個欄位一模一樣。camarilla/cdp 的 backfill 拉到的多半是同一批昨日 OHLC,實務上每次 cache miss 重抓都會觸發一次 daily_ohlc.json 的原子全檔重寫(寫的是不變的內容)。檔案小所以只是浪費,但 changed 旗標的本意(有變動才寫檔)沒有被落實。

**建議修法**:組好 new_rec 後與 cur 比較,相等就 continue 不標 changed。

**驗證者**:確認屬實。market_cache.py:77 的 `r["date"] >= cur["date"]` 在 date 相同、欄位完全一致時仍覆寫並標 changed=True,導致 atomic_write_json 全檔重寫不變內容,與 docstring「有變動才寫檔」的意圖不符。實務觸發場景確實存在:cdp.py:227 與 camarilla.py:154 是兩個獨立 service 各自做 per-symbol per-day backfill,同 symbol 同日第二次必為冗餘重寫;程序重啟清空 in-memory 去重表後再寫一次;週末/休市日每天首次請求也重寫相同內容。建議修法(new_rec 與 cur 相等則 continue)正確且不影響任何 caller 或既有測試(test_market_cache.py 只驗較新 date 取代與 miss 回 None)。小偏差:finding 說「每次 cache miss 重抓都觸發」略誇大——backfill 有每 symbol 每日一次的去重,但核心事實與 low/simplify 的嚴重度認定恰當。

### 94. `backend/services/local_store/signals_log.py:12` — _now_iso 在 config_store 與 signals_log 重複定義

面向:simplify | finder:local-store | 驗證信心:high

兩個檔案各自定義了一字不差的 _now_iso()(UTC ISO 字串)。同子系統內的小工具重複,改時間格式時要記得改兩處。

**建議修法**:搬進 paths.py(該檔已是子系統共用工具層)或新增共用 util,一處定義兩處 import。

**驗證者**:已實讀兩檔確認:config_store.py:18 與 signals_log.py:12 的 _now_iso() 定義一字不差(datetime.now(timezone.utc).isoformat()),且兩處都有實際呼叫(config_store 7 處、signals_log 1 處)。config_store.py 本就從 services.local_store.paths import 共用工具(SCHEMA_VERSION/atomic_write_json/read_json),故「搬進 paths.py 一處定義」的建議與既有架構一致、可直接落地。重複屬實、修法可行,為真實的 low/simplify 問題。

### 95. `backend/jobs/top_gainers_scheduler.py:172` — 收盤後最後一輪的 top 50 訂閱掛整夜,佔用共享的 200 檔 ws 額度

面向:rate-limit | finder:analytics-jobs | 驗證信心:high

13:30 後 _in_market_hours() 為 false,loop 只 sleep 不再 refresh,因此最後一輪(約 13:29)訂的最多 50 檔 system:top_gainers 訂閱會留到隔天 9:00 第一輪 refresh 才被 diff 掉。整個盤後與夜間它們持續佔用單連線 200 檔上限(與自選/書籤/監聽共用),8:25 overnight reconnect 還會把這 50 檔原樣重訂一次;若隔天早上使用者要加自選,可被這批已無意義的訂閱擠到 capacity full。

**建議修法**:loop 偵測「從盤中跨入盤後」的第一次迭代時呼叫 _sync_subscriptions(set())(snapshot 可保留供盤後瀏覽,只退訂 ws),隔天開盤再由 refresh 重建。

**驗證者**:逐項核實成立:(1) top_gainers_loop(top_gainers_scheduler.py:170-180)13:30 後只 sleep,_sync_subscriptions 僅由 refresh_top_gainers 呼叫,全 repo 無任何盤後退訂機制,最後一輪最多 50 檔 system:top_gainers 訂閱確實掛到隔天 9:00(週五則掛整個週末);(2) fubon_ws.py:30-33 確認 MAX_CONNS=1 × WS_PER_CONN_CAP=200 為單一共享額度,滿了 subscribe 直接 raise capacity full;(3) overnight.py:40-50 在 8:25 把 _conn_subs 全量(含 stale 50 檔)原樣重訂,且 overnight loop 的存在證明後端設計為長駐跨夜,情境真實。緩解面:隔天 9:00 第一輪 refresh 會 diff 掉 stale,受擠壓窗口僅盤後至開盤前,且要總訂閱量逼近上限才實際撞牆,severity low 恰當。建議修法與 owner/refcount 機制相容,不影響 user 自選訂閱。純本地訂閱簿記邏輯,不涉 SDK 行為假設,毋需查富邦文件。

### 96. `backend/services/cdp.py:190` — backfill 回看區間僅 10 個日曆天,春節長假後抓不到上一交易日

面向:bug | finder:analytics-jobs | 驗證信心:high

from_=today-10 天。台股春節休市加前後週末可超過 10 個日曆天(例如 2021 年 2/6-2/16 休市 11 天),連假後第一個交易日 historical.candles 在此區間內查無任何收盤資料 → rows 為空 → return False,當天 CDP/Camarilla 退回 stale(年前的前一筆)或 None,而且因 _last_backfill_attempt 已標記,當天不會再重試。

**建議修法**:把回看區間放寬到 20-30 天(historical.candles 是區間查詢,加大範圍不增加請求數);camarilla.py L120 同步修。

**驗證者**:確認為真問題。cdp.py L189-190 from_=today-10 天,且 L213-215 過濾今日列,故上一交易日必須落在 [today-10, today) 內;camarilla.py L119-120 同構。台股春節休市間隔可超過 10 個日曆天(2021 年實例:2/5 最後交易日→2/17 開紅盤,間隔 12 天),開紅盤日區間內零個過去交易日 → upserts 空 → return False;且 _last_backfill_attempt 在呼叫前已標記(L136-138),當天不再重試。已照規定 WebFetch 富邦 historical/candles 官方文件:from/to 為嚴格日期區間,無「自動回傳最近一筆」fallback。fallback refresh() 讀 local store 最新一筆,但因 backfill 每天過濾「今天」,年前最後交易日的 OHLC 通常沒存到(除非假期中剛好有查詢),結局是 CDP/prev_close 用到再前一個交易日的資料(off-by-one,prev_close 連帶影響漲停價與漲幅計算),與 finding 描述一致。tests 只覆蓋 once-per-day latch,無覆蓋回看窗寬度。建議修法(放寬到 20-30 天、camarilla 同步)合理且不增加請求數。嚴重度 low 恰當:僅春節等超長假後首日、且為非下單路徑(CDP/Camarilla 顯示與訊號參考)。

### 97. `backend/services/fubon_client.py:87` — 富邦持續離線時背景重試每 5 分鐘發一次 notify_critical 告警洪水

面向:rate-limit | finder:error-handling | 驗證信心:high

_login_with_retry 所有嘗試失敗的收尾都呼叫 alerts.notify_critical;_background_retry 每 5 分鐘以 max_attempts=1 重呼叫它 — 富邦整夜維護或憑證壞掉時,Discord 每 5 分鐘收一則重複 critical,真正的新告警被淹沒。

**建議修法**:notify_critical 只在狀態轉變(ok→error)時發,背景重試的持續失敗改為每 N 次或每小時摘要一次;恢復時發一則 recovered。

**驗證者**:確認為真問題。觸發鏈完整可驗:main.py:47 啟動呼叫 fubon.init(),首次登入 3 次失敗後排程 _background_retry(fubon_client.py:123-131),迴圈在 status=ERROR 期間每 300 秒呼叫 _login_with_retry(max_attempts=1),而 _login_with_retry 的失敗收尾(fubon_client.py:87)無條件呼叫 alerts.notify_critical,完全沒有 ok→error 狀態轉變判斷。alerts.notify_critical(backend/services/alerts.py)本身也無任何去重、cooldown 或節流——每次呼叫都 logger.error 並直接 POST Discord webhook。ALERTS_DISCORD_WEBHOOK_URL 在 backend/.env.example:22 存在且是系統告警專用(memory 中「webhook 退役」僅指訊號推播改走 bot,與此無關)。因此富邦整夜離線時 Discord 每 5 分鐘收一則重複 critical(每小時 12 則)屬實。緩和因素僅有:需 webhook 有設且富邦持續失敗才發生,與 finding 自評 low 嚴重度相符。此為純本地重試/告警邏輯,不涉富邦 SDK 行為假設,無需查官方文件。建議修法(狀態轉變才發 + 持續失敗定期摘要 + recovered 通知)合理。

### 98. `backend/jobs/top_gainers_scheduler.py:75` — snapshot.movers 未過 rate limiter

面向:rate-limit | finder:rate-limit | 驗證信心:high

_fetch_market_movers 直呼叫 snapshot.movers,不消耗 token。量很小(盤中每分鐘 2 次),單獨不會超限,但 snapshot 與 intraday 同屬 300/min 額度(官方 rate-limit 文件),帳外呼叫讓 TokenBucket 的記帳失真;模式上也跟其餘程式碼不一致,之後有人提高頻率時不會有護欄。

**建議修法**:呼叫前 get_rate_limiter().acquire)(本來就在 to_thread 的 sync context,直接 acquire 即可)。

**驗證者**:屬實。backend/jobs/top_gainers_scheduler.py:75 的 snapshot.movers 確實未呼叫 get_rate_limiter().acquire,而 rate_limiter.py:89 的 default bucket docstring 明寫涵蓋「Intraday/Snapshot/Technical」,且 fubon_client.py、ma_service.py、fubon_futures.py 的所有富邦 REST 呼叫都有計帳——此處繞過自家限流設計成立,建議修法(sync context 直接 acquire)可行。兩點修正:(1) 已查官方 rate-limit 文件(market-data/rate-limit.txt),原文「日內行情 300/min」「行情快照 300/min」分列兩條、未說共用額度,finding 的「同屬 300/min 額度」主張無官方背書,跨類別記帳失真的影響存疑;(2)「跟其餘程式碼不一致」不完全準——routes/candles.py:30 的 intraday.candles 也同樣未過 limiter,是同類的另一處缺口。實際風險極小(每分鐘 2 次 vs 300/min),low 嚴重度恰當,本質是護欄一致性問題而非超限風險。

### 99. `backend/services/signal_engine.py:53` — _prev_tick 與 _cooldown 永不清理,24/7 跑會緩慢累積

面向:rate-limit | finder:rate-limit | 驗證信心:high

_cdp/_ma_touch_count、_day_volume、漲停 latch 都有 daily GC,但 _prev_tick(每個出現過 tick 的 symbol 一筆)和 _cooldown(每個 (rule_id, symbol) 一筆)從不清。top_gainers 每分鐘輪換訂閱、preview 換股,長期 24/7(本後端明確設計成不重啟,有 overnight 重連)會累積整個出現過的 symbol 宇宙。單筆很小、上限約全市場 ~2000 檔×規則數,屬慢速有界成長,不會炸但與其他 daily reset 的設計不一致。

**建議修法**:在 _reset_daily_strategy_state()(跨午夜 heartbeat 分支)順手 self._prev_tick.clear(),_cooldown 清掉 last_ts 早於昨日的 key——隔夜 cooldown 本來就無意義。

**驗證者**:屬實。grep 全 backend 確認:_prev_tick 僅 signal_engine.py:232 讀、:271(finally)寫,_cooldown 僅 :252 讀、:255 寫,兩者皆無任何 pop/clear;refresh_active_signals 換規則時也不 prune 已刪規則的 cooldown key。跨午夜 heartbeat 分支(:195-203)確實清了 _day_volume、touch counts、漲停 latch、breakout_armed,field_cache 也有 stale 逐出,唯獨這兩個 dict 漏掉,與其他 daily reset 設計不一致。累積路徑也驗證:tick callback 把所有訂閱 symbol 送進 _evaluate,finally 不分 scope 都寫 _prev_tick,而 top_gainers_scheduler.py:148 每分鐘輪換訂閱、preview.py:58 換股訂閱,長期會累積出現過的 symbol 宇宙。成長有界(約全市場檔數×規則數)、單筆極小,不會炸,low 嚴重度恰當。建議修法(daily 分支清 _prev_tick、淘汰過期 cooldown)合理且附帶修掉「隔日開盤用昨日收盤 tick 當 prev 判方向」的 stale 語義。

### 100. `backend/routes/ma.py:21` — routes/ma.py 重複實作 ma_service 的 _extract_latest/_fetch_sma,service docstring 宣稱共用但 route 沒用它

面向:simplify | finder:dead-code | 驗證信心:high

services/ma_service.py 開頭 docstring 寫「共用給 routes/ma.py 跟 signal_engine」,實際只有 signal_engine 在用;routes/ma.py L21-52 自帶一份 _extract_latest + _fetch_sma(同樣的 rate limiter acquire、tech.sma 呼叫、欄位解析),差異只在 route 版多抽 date 欄位。兩份已開始漂移,之後改 SMA 解析(例如富邦欄位變動)只改一邊就會讓 /api/ma 與訊號引擎的 sma_5/sma_20 對不上。

**建議修法**:把 ma_service.fetch_sma 改回 (value, date)(fetch_sma_5_20 取 [0] 即可),routes/ma.py 改呼叫 ma_service,刪掉 route 內的複本與重複的 fubon status 檢查;tests/test_ma_service.py 同步補 date 斷言。

**驗證者**:查證屬實:(1) ma_service.py docstring 宣稱「共用給 routes/ma.py 跟 signal_engine」,但 grep 全 repo 確認只有 signal_engine.py:13 import 它,routes/ma.py 完全沒用;(2) routes/ma.py:21-52 與 ma_service.py:17-48 是逐行近似複本(同 rate limiter acquire、同 tech.sma 呼叫、同 data[-1]["sma"] 解析、連 warning log 字串都相同);(3) 漂移已發生——route 版多回 date 欄位,service 版只回 float;(4) 設計文件 docs/superpowers/specs/2026-05-19-chart-and-signal-tweaks-design.md:191 明寫「routes/ma.py 改用 ma_service」,plan 檔 L922 還預留 date 的合併方式,證明這是未完成的重構而非刻意分離。兩版錯誤語意差異(route 回 503、service 靜默 None)不構成反駁,修法保留 route 層 503 即可。嚴重度 low/simplify 標得恰當。

### 101. `backend/services/cdp.py:168` — 快取服務散落的死 helper:CdpService.discard/has、CamarillaService.discard、RingBuffer.has、WSPool.total_subscribed/conn_count

面向:simplify | finder:dead-code | 驗證信心:high

全 repo grep 零生產呼叫端:CdpService.discard/has(cdp.py L168-172;bookmarks remove_item 的註解明說刻意不 discard)、CamarillaService.discard(camarilla.py L100;.has 只有測試在用)、RingBuffer.has(ring_buffer.py L84)、WSPool.total_subscribed/conn_count(fubon_ws.py L67-71,startup log 用的是 MAX_CONNS*CAP 常數而非這兩個 method)。單看每項都小,但合計是五處沒人讀的 API 表面,跟「signal_engine.health 無 endpoint」同根:診斷介面做了一半沒接出口。

**建議修法**:整批刪除;若想保留 WSPool 訂閱數等診斷值,跟 health endpoint 一起做成一個 /api/health 再保留,不要留無出口的 getter。

**驗證者**:逐項 grep 全 repo(backend/、frontend/src/、bot/src/、scripts/、tests)驗證屬實:(1) CdpService.discard/has(cdp.py:168-172)零呼叫端,且 bookmarks.py:245-246、watchlist.py:115 註解明說刻意不 discard、留給 lazy eviction;(2) CamarillaService.discard(camarilla.py:100)零呼叫端,.has 僅 test_camarilla.py:81/163 使用、無生產呼叫;(3) RingBuffer.has(ring_buffer.py:84)零呼叫端——finding 並未誤殺有人用的 RingBuffer.discard(fubon_ws.py:116 確有生產呼叫);(4) WSPool.total_subscribed/conn_count(fubon_ws.py:67-71)零呼叫端(含測試),startup log(L80)用的是 MAX_CONNS*WS_PER_CONN_CAP 常數。五處皆為無出口的診斷 API 表面,純本地邏輯不涉 SDK 行為,severity low / simplify 認定恰當。唯一保留意見:CamarillaService.has 有測試在用,整批刪除時需連同 test_camarilla 兩處 assert 一起調整。

### 102. `backend/routes/signals_history.py:50` — today_counts 回傳的 as_of/today_start 欄位無人讀

面向:simplify | finder:dead-code | 驗證信心:high

前端只消費 counts(group by symbol+active_signal_id),as_of 與 today_start 只存在於 frontend/src/lib/api.ts L305-306 的型別宣告,沒有任何元件讀取;bot 不打此 endpoint。route 內 today_start_tw 的計算(L41)只為了組回傳欄位。

**建議修法**:刪 as_of/today_start 兩欄與 today_start_tw 計算,前端型別同步刪;測試 test_signals_history_route.py L65 的斷言一併更新。

**驗證者**:已 grep 全 repo(backend/frontend/bot/scripts)驗證:today_counts 唯一消費者是 frontend/src/hooks/useTodayHits.ts,只讀 r.counts;as_of/today_start 僅存在於 route 回傳組裝(signals_history.py L49-50)、api.ts L305-306 型別宣告、test_signals_history_route.py L64-65 的 shape 斷言,無任何實際讀取者。bot/src 完全不打此 endpoint。route L41 的 today_start_tw 也沒傳進 today_rows()(該函式自算今日範圍),確實只為組回傳欄位而算。測試斷言只驗欄位存在、不編碼業務意圖,刪除時同步更新即可。finding 各項事實主張全部成立,low/simplify 嚴重度恰當。唯一保留意見:as_of 類欄位有時當回應 debug metadata,留著無害,但這不影響「死碼」判定。

### 103. `backend/services/fubon_ws.py:207` — _handle_raw_message 的 float/int 轉換與 ring_buffer.append 在 try 範圍外,異常封包例外外洩到 SDK 執行緒

面向:error-handling | finder:type-safety | 驗證信心:high

try 只包了 json.loads;之後 float(price)/int(size)/float(bid)/float(ask) 與 get_ring_buffer().append 都裸跑在富邦 SDK callback 執行緒。已查官方 WS trades 文件(market-data-channels/trades.txt):price/size/bid/ask 規格皆為 number、price 非必揭示欄位 — 缺 price 已有 None 檢查,但任何規格外封包(型別異常、混入其他 channel 變體)會讓例外打進 SDK dispatch,SDK 對 callback 例外的行為未定義,最壞情況是該連線的訊息分發中斷而 status 仍顯示 OK。這是行情唯一入口的熱路徑,值得整段防護。

**建議修法**:把 _handle_raw_message 整個函式體包進 try/except(log+丟棄該訊息),或至少把欄位轉換納入既有 try。

**驗證者**:確認為真問題(low 嚴重度恰當)。(1) 程式碼描述屬實:fubon_ws.py:190-193 的 try 只包 json.loads,float/int 轉換(207-213)與 ring_buffer.append(216)裸跑在 SDK callback 執行緒。(2) 已照流程查官方 trades channel 文件:price/size/bid/ask 規格皆 number、price 非必揭示——規格內封包不會炸,但文件完全未定義 callback 例外時 SDK 行為,且 websocket_client 在編譯後的 _fubon_neo.pyd(Rust),無法證明 SDK 會吞例外,「行為未定義」無法反駁。(3) 反駁過程中反而找到一條不需規格違反就會炸的路徑:ring_buffer.append 先 _locks.get(symbol) 檢查 None,但隨後 self._buffers[symbol] 在 registry_lock 外裸索引,與 event loop 上 unsubscribe→discard() 有競態,KeyError 會外洩進 SDK 執行緒——盤中取消訂閱時即可觸發。(4) 另 data.get("size", 0) 擋不住 "size": null,int(None) 會 TypeError。削弱點:「混入其他 channel」在本 codebase 不成立(stock ws 全 repo 只訂 trades,期貨走獨立 futopt handle),故非現行高頻風險,但整段 try/except 包裹的建議修法成本低且正確。

## 駁回的誤報(12 項)

- `backend/middleware/auth.py:47` [high] APIKeyMiddleware 在 CORS 之外層,擋掉瀏覽器 preflight,BFF_API_KEY 一設前端全斷
  - 反駁:誤報。finding 的中介層機制描述正確(CORS 先加、APIKey 後加 → APIKey 在外層,OPTIONS 會被 401),但「前端全斷」的前提——5173 對 8000 的跨來源 preflight——在本專案不會發生:frontend/src/lib/api.ts 全部用相對路徑 fetch('/api/...')(檔案第 1 行明寫慣例,grep 全 frontend/src 無任何絕對 :8000 URL),vite.config.ts 的 /api 與 /ws proxy 轉發到 127.0.0.1:8000。瀏覽器只跟 5173 同源通訊,同源請求不發 CORS preflight;X-API-Key 隨實際請求由 Vite node proxy 原樣轉發,驗 key 正常通過。bot 是 server-side Node 直打無 preflight;WS 握手不走 preflight 且 auth.py 已放行 /ws。後端無 StaticFiles mount、前端 build 亦為相對路徑,任何部署都必然前端與 API 同源,跨來源情境無從發生。「後端+前端都設 key 照文件配置會全斷」不成立——照文件配置就是走 proxy 同源。middleware 順序頂多是未來若改直打跨來源時的潛在 footgun,屬低嚴重度 hardening 建議,非 high bug。
- `backend/services/capital_client.py:248` [high] COM 執行緒亡故後佇列中的 future 永不 resolve,真錢寫入請求永久懸掛
  - 反駁:誤報——宣稱的觸發情境在現行程式碼中不可達。_run 的 while 迴圈逃逸路徑逐一檢驗:(1) _pump_once(capital_client.py:171-182)整段 except Exception 吞掉並 log,有 test_pump_once_swallows_exceptions 鎖住,COM 斷線/查詢例外殺不死執行緒;(2) 命令處理(199-204)fn() 的所有 Exception 都被捕捉並透過 call_soon_threadsafe(fut.set_exception) 回傳,awaiter 必定 resolve(test_com_exception_returns_result_and_audited 驗證);(3) queue.get 只拋已捕捉的 Empty,cmd None 的 break 在 production 無人觸發(grep 全 repo 只有測試放 None)。唯一真實逃逸是 call_soon_threadsafe 在 loop 已關閉時拋 RuntimeError——但 loop 只在進程關機時關閉,且 uvicorn/asyncio.run 關 loop 前會先取消所有 pending task,await fut 的請求收到 CancelledError(BaseException,不被 249 行 except Exception 吃掉)而結束,不是永久懸掛;loop 真正 close 時已無存活的 awaiter。「status 檢查與入佇列非原子必踩」同理只存在於關機瞬間,後果是 task 被取消而非懸掛,且關機瞬間的「單送了沒」不確定性是任何架構固有、非此碼引入。作者已意識此風險並取捨:finally 降 status(206-209)+ test_run_thread_exit_drops_status 擋掉之後的請求。建議的 wait_for 對「COM 呼叫本身阻塞(執行緒沒死)」才有價值,但那是 finding 未提出的另一情境,不能用來支撐本 finding 的執行緒亡故主張。
- `backend/services/fubon_futures_ws.py:174` [high] 主動 teardown 的 disconnect 事件會回頭清掉剛建立的新訂閱狀態,引發重複 connect/雙執行緒
  - 反駁:機制前提部分屬實(已讀安裝版 SDK 原始碼 fugle_marketdata/websocket/client.py 確認:disconnect() 即 ws.close(),__on_close 無條件 emit 'disconnect';futopt 是 factory 快取單例),但 finding 宣稱的觸發路徑不存在:(1) determine_current_session 的 day(08:45–13:45)與 night(15:00–05:00)之間隔著 75/225 分鐘 closed,reconcile 每 60s 跑,收盤後 1 分鐘內就在 want=None 路徑單獨 teardown,該次 spurious disconnect 之後 _ensure 看到休市直接 return、無害;到 08:45/15:00 重訂閱時 _ws 早已 None、沒有在途 disconnect 事件,afterHours 旗標不可能 False↔True 直接翻轉。(2)「teardown 後同鎖內立刻 _subscribe」需要活連線時切 symbol 或旗標直翻——grep 全 backend 後 start(symbol) 只在 main.py:65 啟動時呼叫一次(此時 _ws 必為 None),無其他路徑。(3) 後果描述亦錯:websocket-client 1.9.0 的 run_forever 對已開 socket 直接 raise "socket is already opened"(_app.py:343),第二條 thread 立即死亡,不會雙連線/執行緒洩漏;pyee listener 存於以 listener 為 key 的 OrderedDict(base.py:141),bound method 重複 on() 是覆蓋不是累積,不會重複推送。僅剩機器休眠橫跨 13:45–15:00 的極端邊角可能觸發,且後果只是同一活 socket 多送一次 subscribe,與 finding 的 high/每日必發/雙執行緒結論不符。建議修法(closing flag)作為防禦性加固無妨,但所述 bug 在現行程式碼不會發生。
- `backend/services/fubon_futures_ws.py:101` [medium] 每次 _subscribe 都對同一個 SDK singleton 重複 ws.on() 註冊 handler,可能累積出重複廣播
  - 反駁:誤報。finding 的前提「SDK 內部以 list 累積 handler」不成立。實際追到本機安裝的完整鏈(fubon-neo 2.2.8 → fugle-marketdata 2.4.1 → pyee 11.1.1,均為純 Python 原始碼可讀):(1) `sdk.marketdata.websocket_client.futopt` 確實是同一個 cached wrapper(backend/.venv/Lib/site-packages/fubon_neo/adapter.py 的 `_futopt_wrapper` lazy cache),委派到同一個 `WebSocketFutOptClient`;(2) 該 client 的 `on()` 委派給 pyee `EventEmitter.add_listener`,而 pyee 的儲存結構是 `self._events[event][k] = v`(OrderedDict 以 listener 為 key,見 pyee/base.py:152),不是 list append;(3) Python bound method 的 `__eq__`/`__hash__` 以 `__self__`+`__func__` 判等,FuturesWSPool 是 singleton,所以每次 `_subscribe` 取出的 `self._on_message_raw` 雖是新物件但 key 相等 → dict 賦值是「覆蓋」語意,不累積。用該 venv 實測:重複 `ee.on("message", p.handler)` 100 次後 `listeners` 長度為 1、一次 emit 只觸發 1 次 call(a is b: False / a == b: True / hash equal: True)。因此跑數天重複 `ws.on()` 不會造成重複廣播。finding 建議的「加註解說明覆蓋語意」可作為 nice-to-have,但無實際 bug。註:此結論基於安裝套件原始碼+實測,比官方文件(未涵蓋 pyee 內部實作)更權威;唯一前提是 pool 為 singleton(get_futures_ws_pool 保證)且 pyee 版本不變(requirements 鎖在 venv)。
- `backend/routes/bookmarks.py:266` [medium] move_items 不驗 symbols 屬於 from_group 也不驗 symbol 存在;from_group 同時在 to_group_ids 時 move 會把股票從目標群刪掉
  - 反駁:finding 對 route 程式碼的描述正確(move_items 確實不驗 symbols、不擋 from∈to),但宣稱的觸發情境在現有 codebase 不可達:(1) 全 repo grep 後唯一 caller 是 frontend/src/components/MoveCopyDialog.tsx(bot、scripts 都沒打這端點),而該 dialog 第 49 行 `groups.filter((g) => !g.is_system && g.id !== fromGroupId)` 明確把來源群組排除在候選外 — finding 說「UI 勾選含來源群組」會觸發資料遺失,與實際 UI 行為相反,該資料遺失情境不可能經 UI 發生;symbols 也是從 from_group 的 item 列表勾選,必然存在且為真實代碼。(2) 即使送出不在 from_group 的 symbol,config_store.remove_item 對不存在項是 no-op、fubon_ws.unsubscribe 開頭檢查 owner 不在就 return,refcount 不會誤扣,無任何破壞。垃圾 symbol 被 subscribe 只有手刻 curl 打 localhost 單人本機 API 才會發生。實質只剩「API 層不如 add_items 嚴格」的 defense-in-depth 建議,非 medium bug;confidence 給 medium 是因為若未來新增 caller(如 bot)未複製 UI 的過濾邏輯,該缺口會變成真問題,加防呆仍有低優先價值。
- `backend/services/fubon_futures_ws.py:121` [medium] 主動 teardown 的 disconnect 事件與意外斷線無法區分,session 切換觸發多餘的斷線重連循環
  - 反駁:前提半對但情境不可達。已讀本機 fubon-neo 2.2.8 內附原始碼驗證:(1) disconnect() 確會經 WebSocketApp.close() → __on_close emit disconnect 事件,主動 teardown 會觸發 _handle_disconnect——這點 finding 正確。(2) 但「teardown 後緊接 subscribe」在現行呼叫圖不會發生:start(symbol) 只在 startup 呼叫(main.py:65,_ws 必為 None);「已連線但 afterHours 旗標不符」也不可能——day(08:45–13:45)與 night(15:00–05:00)之間隔 75 分鐘以上的 closed 區間,reconcile 每 60 秒一次,收盤邊界必是 teardown-only、開盤邊界從 _ws=None 直接 subscribe。收盤 teardown 引發的 spurious _handle_disconnect 接著重算 want=None 直接 return,純 no-op + 一行多餘 log;stop() 路徑因持鎖期間 _symbol 已設 None 也安全。(3)「再起一條 run_forever 執行緒」claim 錯誤:websocket-client _app.py:342 對已開啟 sock 直接 raise "socket is already opened",新 thread 立即死亡,不會疊執行緒。(4) 重複 ws.on() 也不會累積 listener:pyee _add_event_handler 用 OrderedDict 以 handler 為 key,同 instance bound method 相等去重。故「每次 session 邊界多跑一輪 teardown+subscribe、盤中重複訂閱/執行緒疊加」與實際行為不符。殘餘僅推測性邊角(筆電睡眠跨 closed 區間醒來、未來加盤中換月 runtime 呼叫 start()),屆時建議修法(teardown 前 ws.off 或 generation token)才有意義。
- `backend/main.py:107` [medium] shutdown 序列未停 capital COM 執行緒,佇列中命令與其稽核可能無聲消失
  - 反駁:誤報。寫入命令進 _cmd_q 的唯一來源是 routes/capital.py 的 HTTP handler(grep 全 repo 確認,無背景生產者),佇列有命令必對應一條正在 await fut 的 in-flight request。實際安裝的 uvicorn(backend/.venv/.../uvicorn/server.py:271-316)graceful shutdown 順序是:停收新連線 → 無限等 in-flight 連線/task 完成(start.ps1 未設 timeout-graceful-shutdown)→ 才執行 lifespan shutdown。等待期間 capital daemon 執行緒沒人停、幫浦持續消化佇列,fut resolve 且 _audit_after_send 落地後 request 才結束;因此執行到 main.py:107 時佇列必為空,「關機瞬間佇列還有寫入命令、單出去稽核沒落地」在 graceful 路徑不可能發生。至於稽核真會消失的路徑(二次 Ctrl+C 的 force_exit 會跳過 lifespan shutdown、--reload 硬殺、斷電),lifespan 裡補 put(None)+join 根本執行不到,建議修法對那些情境無效——finding 把損失視窗定位在 lifespan 序列,前提錯誤。
- `backend/services/capital_client.py:197` [medium] capital COM 執行緒無 graceful shutdown:app 關閉時在飛的下單可能已送進群益但稽核/回報遺失
  - 反駁:事實前提成立(sentinel None 僅測試用、main.py lifespan 不停 capital 執行緒),但危害情境不可能在修法能生效的路徑上發生。所有寫入操作唯一生產入口是 routes/capital.py 的 HTTP 端點,「SendStockOrder 已送出、稽核未寫」的窗口期間必有一個 in-flight request 在 await fut;而 uvicorn graceful shutdown(本專案 start.ps1 用預設設定,無 timeout_graceful_shutdown)會先等所有 in-flight request 完成才跑 lifespan shutdown、才退出進程——窗口期間 daemon 執行緒不會被殺,稽核必定寫完。窗口真正存在的場景只有非 graceful 終止(force_exit 連按 Ctrl+C、--reload 在 Windows 的 TerminateProcess、taskkill、斷電),但這些場景 lifespan shutdown 根本不執行,建議的 stop()+join 是死碼。且 _audit_after_send 跑在 asyncio request coroutine 而非 COM 執行緒,join COM 執行緒也保證不了稽核落地。硬殺風險要靠送單前寫 intent 稽核(pre-send journaling)才能防,屬另一設計層級的 finding,與本條描述的機制和修法不符。
- `backend/routes/mxf.py:36` [low] 使用者自帶 symbol 未驗證,搭配 fetch_candles 刻意重拋 ValueError,垃圾輸入會變 500
  - 反駁:誤報。直接讀了 venv 內實際安裝的 SDK 原始碼(fubon-neo 2.2.8 vendor 的 fugle_marketdata REST client):futopt.intraday.candles 只是把 symbol 串進 URL path 打 HTTP,非法 symbol 會讓富邦回 4xx,base_rest.py:42 拋的是 FugleAPIError——而 exceptions.py:1 顯示 FugleAPIError 直接繼承 Exception、不是 ValueError;連 JSON 解析失敗的 ValueError 也在 base_rest.py:52 被重新包成 FugleAPIError,SDK 呼叫不可能漏出 ValueError。因此垃圾 symbol 走的是 fetch_candles._fetch 的 except Exception 分支(backend/services/fubon_futures.py:228-230):log warning 後回空 list,route 回 200 + 空 candles,不會 500。_fetch 裡 except ValueError: raise 真正守的是 rate_limiter.acquire 的 tokens>capacity(寫死參數的呼叫方 bug,與使用者輸入無關);fetch_candles 開頭的 ValueError(unsupported timeframe)也因 route 層先用同一個 SUPPORTED_TIMEFRAMES 驗過而不可達。finding 的核心前提「SDK 對非法參數拋 ValueError」在實際安裝版本下不成立。
- `backend/services/fubon_futures_ws.py:115` [low] fire-and-forget asyncio.create_task 未保留參考,reconnect task 理論上可能被 GC
  - 反駁:誤報(實務上情境不可達)。asyncio 文件的「event loop 只持弱參考」警語針對的是 task 唯一參考只剩 all_tasks WeakSet 的一般情況;但 _schedule_reconnect 這個 task 全程被 CPython 強參考鏈錨住:(1) 剛建立未執行時 loop._ready 的 Handle 強參考 task.__step;(2) finding 所稱的「長 sleep 中」其實是 loop._scheduled 的 TimerHandle → sleep future → done callback(task.__wakeup)→ task 的強參考鏈;(3) 等 self._lock 時 Lock._waiters 強參考 waiter future,而 lock 掛在模組級單例 _pool(fubon_futures_ws.py:204-211)上永遠存活。真正會被 GC 的案例是 await 無外部參考的自訂 awaitable,此處不存在。再者 finding 自承 session_reconcile_loop 每 60 秒會 reconcile 補救,實害為零;_schedule_reconnect 已有 _reconnecting 旗標防累積、stop() 後醒來也會因 _symbol is None 安全 no-op,建議的 cancel 修法無實益。且同檔 133/141 行與 fubon_ws.py、routes/ 多處皆為同一 fire-and-forget 慣例,單修此行不一致。保留點:官方文件確實建議存參考(ruff RUF006 等 lint 會報),屬 hygiene 偏好而非實際缺陷,故信心 medium。
- `backend/services/local_store/market_cache.py:62` [low] search/get_symbol 投影端直接 r["name"]/r["market"],與過濾端 r.get("name", "") 防禦不一致
  - 反駁:誤報(風格不一致存在,但故障情境不可達)。symbols.json 唯一寫入端 backend/routes/symbols.py 的 refresh_symbols() 三個來源都固定產出 symbol/name/market/is_etf/is_active 五欄,且先過 `if code and name` 篩掉缺值;git --follow 證實本機 symbols.json 自 2026-06-01 local-first 首次引入(b97c43c)起寫入端就保證欄位完整——「舊版爬蟲寫入缺欄位 row」的前提不成立,在那之前 symbols 在 Supabase、沒有此檔,且 Supabase 遷移腳本不碰 symbols(本機檔走 bootstrap 重爬)。實測磁碟上 2370 筆 row 缺 name/market 者為 0。此外同檔 load()/replace_symbols 本來就硬取 s["symbol"],「信任機器寫入快取檔」是既有慣例,過濾端的 r.get("name","") 才是孤例;即使手動改壞檔案,POST /api/symbols/refresh 全量重建即復原。唯一觸發路徑是人為手動編輯壞本機快取,不屬正常運作情境。confidence 取 medium 是因為字面上兩種防禦標準並存屬實,若團隊想統一寫法仍可順手改,但不構成可達 bug。
- `backend/routes/mxf.py:20` [low] GET /api/mxf/symbol/active 無任何呼叫端
  - 反駁:「無程式碼呼叫端」屬實(backend/frontend/src、bot/src、scripts 全 grep 過僅 route 自身與 docs),但它不是孤兒:docs/notes/mxf-fubon-api-observations.md L84 觀察 4 明訂一個尚未完成的實測程序(結論欄仍空)——結算日前後要手動 curl /api/mxf/symbol/active 驗證近月換月與 cache 過期邏輯;原始 plan(2026-05-24-mxf-intraday-chart.md L884)驗收步驟也直接 curl 此端點。它是文件記載的維運/診斷探針,能單獨觀測 resolve_active_symbol 的 cache 行為而不夾帶 candles 抓取,刪掉會破壞待執行的觀察 4 程序。route 僅 6 行、零維護負擔,刪除收益趨近於零。confidence 給 medium 是因為若 user 認定觀察 4 永遠不會做,刪掉也無害——屆時應連同該 note 一起清。

## Finder 覆蓋說明(notes)

- fubon-market:五個目標檔案均已完整讀畢。SDK 行為結論均經驗證,非憑印象:(1) 官方 rate-limit 文件確認 300/min intraday、60/min historical、WS 200 訂閱/連線、5 連線/帳號——rate_limiter.py 的註解數字正確;(2) 官方 trades channel 文件確認 isTrial/isContinuous 欄位語意;(3) 官方 Python loginAPIKey 文件確認登入回傳 Result 不 raise;(4) 本地安裝的 fubon_neo 2.2.8 原始碼(adapter.py/sdk.py)與 fugle_marketdata websocket/client.py 確認 marketdata 只在 init_realtime 建一次、stock wrapper 快取、on() 是 pyee 累加。overnight.py、signal_engine.py、main.py 僅作為上下文閱讀,其自身問題未列入(但 overnight 依賴的重登流程缺陷已歸檔到範圍內的 fubon_client.py:107 與 fubon_ws.py 私有狀態外洩)。capital_* 不在本組檔案內,未 review。
- fubon-futures:三個檔案均已完整讀畢,並讀了 main.py 啟動接線、services/fubon_ws.py(訊息格式先例)、backend/tests/test_fubon_futures.py 與 test_fubon_futures_ws.py 作上下文。依專案規定查證了富邦文件:Grep docs/api/fubon-neo-llms.txt 後 WebFetch 期貨 WS candles channel 官方頁(訂閱參數 channel/symbol/afterHours 與推送 envelope {event:\"data\", data:{symbol,date,open,...,average}} 與程式假設相符;Node.js 範例需 JSON.parse 佐證訊息為字串),並查了本地 ~/.claude/skills/neoapi-python/llms-full.txt 的 Python WS 範例。「週五夜盤」finding 依據的是期交所公開交易時間(盤後時段週一至週五 15:00–次日 05:00,含週五夜),屬市場規則而非 SDK 行為;該認知與現有測試 fixture 衝突,修 code 需連測試一起改。未報:國定假日不在 session 判斷內(需要假日行事曆,屬功能增補非 bug)、resolve_active_symbol 並發 cache miss 的少量重複請求(影響極小)。
- signal-engine:六個目標檔案全數完整讀畢(signal_engine.py 743 行、condition.py、signal_writer.py、alerts.py、discord_notifier.py、lifecycle_sync.py),並讀了上下文:fubon_ws.py、ring_buffer.py、cdp.py、ma_service.py、local_store(config_store/signals_log)、routes/active_signals.py、main.py lifespan、相關測試(day_metrics/pre_open/monitor)。signal_writer.py 本身無 finding(純轉發;其下游 append 的 I/O 例外風險歸入 signal_engine 的 loop 無保護 finding)。並發面確認過:SDK thread → call_soon_threadsafe → 單一 event loop,引擎內共用狀態(field_cache/cooldown/day_volume)只在 loop 上讀寫,無跨執行緒競態;ring_buffer 自帶 lock。所有 finding 皆為本地邏輯,未對富邦 SDK 行為下任何結論,故依規未觸發富邦文件 WebFetch 流程。本組檔案無 capital_* 真錢路徑,無 critical 級 finding。
- capital-core:範圍內 7 個檔案全數完整讀畢(capital_client/com/factory/models/mapping + 兩支 scripts),並讀了 capital_safety/store/balance/close/reply/audit、routes/capital.py、main.py startup、test_capital_client.py 作為上下文(其問題未報)。兩支 scripts(capital_smoke/capital_login_probe)讀畢後無獨立 finding:smoke 的 prod 守門邏輯、monkeypatch 時序(patch 在 start 前、setup 讀 instance attr)皆正確。三點保留:(1) 群益 SKCOM 與富邦不同、無本地強制文件工作流程可查,SetAuthority 回傳碼語意、SKReplyLib OnDisconnect 事件簽名是依群益 COM API 慣例與 probe 註解推斷,相關 finding 以「未驗證行為要 fail loud」立論而非斷言 SDK 細節;(2) 市價單(nSpecialTradeType=1)仍帶 bstrPrice=閘用估價,群益是否忽略該欄未經盤中實測(memory 註明 v2 盤中實測未做),因無法查證未列為 finding,建議首次市價實測時確認;(3) capital_mapping 的 enum 對應表註明出自官方範例且首筆 prod 實單已驗,未二次質疑。
- capital-flow:範圍與方法:七個 in-scope 檔案全數逐行讀完;capital_client.py、capital_com.py、capital_mapping.py、capital_models.py、capital_factory.py 與 test_capital_store/balance/safety 作為上下文讀過。富邦文件工作流程未啟動——這組檔案是群益 SKCOM COM,不涉富邦 SDK 行為判斷,符合 CLAUDE.md 的跳過條件。上下文檔案中觀察到但依規則不報的事項:(1) capital_com._ReplyEvents 沒有掛 OnDisconnect,系統無法偵測回報主機斷線;capital_store 的聚合非冪等(docstring 自承「重連重播前必須先 clear()」),若 SKCOM 內部自動重連並重播當日 backlog,成交量會重複累計、活單被誤標全部成交而從面板鎖死不可刪改——store 端假設已自我記載、偵測點在範圍外的 capital_com,故記於此而非 finding;(2) capital_client._status 跨 COM 執行緒與 event loop 讀寫無鎖(str 賦值原子性下實害有限)。正面確認過、不構成 finding 的點:store 全方法持鎖且發布 copy-on-write 物件、BalanceCollector 單(COM)執行緒假設經查屬實、audit 寫入全部發生在 event loop 執行緒無交錯風險、_RANK 終態防亂序降級與 D-before-N 邊界有測試固定、刪單/減量寬鬆放行與 D 無價不採計等保守方向設計皆有明確註解與測試背書。
- routes-app:範圍內 17 檔全數逐行讀完(main.py、middleware/auth.py、routes 之 bookmarks/symbols/watchlist/quote/monitor_list/ma/active_signals/signals_history/preview/candles/ws/config_io/camarilla/cdp/_item_enrich)。為驗證 finding 另讀了 context:services/fubon_ws.py、fubon_client.py、rate_limiter.py、cdp.py、camarilla.py、lifecycle_sync.py、local_store/config_store.py、signals_log.py、ws_broadcaster.py、signal_engine.py(scope 解析段)、tests/test_monitor_list_route.py,以及前端 api.ts/Monitor.tsx 呼叫端。未 WebFetch 富邦官方文件:所有 finding 都是專案內部一致性問題(繞過自家 rate limiter、route 狀態管理),沒有對 SDK 行為下新結論;rate limit 數字(Intraday 300/min、Historical 60/min)直接引自專案 rate_limiter.py 既有註記。context 檔案中觀察到但依規不報的問題:fubon_ws._real_subscribe 吞掉訂閱失敗(pool 記為已訂但富邦端沒訂)、_ensure_handle 回 None 時 subscribe 同樣靜默成功、broadcaster 對慢 client 序列送出——這些屬 services 子系統,建議另輪 review 涵蓋。routes/capital.py 與 routes/mxf.py 不在本次指定清單內,未審。
- local-store:六個目標檔全數完整讀過。並發假設逐一驗證過呼叫端:fubon_ws 的 SDK callback 用 loop.call_soon_threadsafe 回主迴圈、signal_engine 經 asyncio.Queue 在 loop 上消費、camarilla/cdp 的 upsert 在 await to_thread 之後回到 loop 才呼叫、top_gainers_scheduler 與 routes/capital.py 也都在 loop 上同步呼叫——store 的「單 loop 不需鎖」設計成立,未發現跨執行緒呼叫,故無 concurrency finding。migrate 1000 筆分頁 bug 為已知問題(使用者先前決定不補自己的 435 筆),但程式碼本身的缺陷仍在、對任何重跑者都會靜默丟資料,故照報。get_local_store 單例無鎖、atomic_write_json 的 tmp 檔名固定等屬單 process 前提下無實害,未列入。capital_* 與富邦 SDK 行為不在本組範圍,未動用富邦文件流程(純本地儲存邏輯)。
- analytics-jobs:六個指定檔案全部完整讀過。ma_service.py 與 logging_config.py 無 finding:已依專案規定 WebFetch 富邦官方文件確認 Technical SMA 回傳 data 為日期升冪(data[-1] 取最新一筆正確)、Historical Candles 預設降冪且 date 格式 yyyy-MM-dd(cdp/camarilla 的「過濾今日」字串比對格式正確);logging_config 為單次啟動設定,clear root handlers 不影響 uvicorn 自帶 logger(propagate=False),無可辯護問題。compute_cdp/compute_camarilla/limit_up_price/round_to_tick_tw 的 tick 數學已逐一驗算邊界(跨級距、半分尾數、一字板 rng=0)未發現錯誤。範圍外但建議追蹤:fubon_ws._wire_callbacks 在 _reconnect 路徑對同一個 SDK ws singleton 重複 ws.on(...) 掛 listener,若 SDK 的 on() 是累加式會在多次重連後造成 tick 重複放大——此問題在 fubon_ws.py 不在本組檔案,故未列入 findings。
- concurrency:掃描範圍:backend/ 全部非測試 .py(main、services/*、routes/*、jobs/*、ws_broadcaster),聚焦三種執行模型的橋接點(grep call_soon_threadsafe / create_task / threading.Thread 全數逐一核對)。SDK 行為依據:未走 WebFetch 官方文件,改為直接閱讀本專案 .venv 內實際安裝的 fubon_neo 2.2.8(adapter.py/sdk.py)、fugle_marketdata websocket/client.py 與 pyee/base.py 原始碼 — 對「on() 是 append 還是 replace」「主動 disconnect 是否觸發事件」「有無自動重連」這類執行緒/回呼語意,安裝原始碼比文件更權威,且 fubon_ws.py 檔頭註解的單例假設與原始碼一致。已確認無問題而不報的:ring_buffer 主要路徑與 rate_limiter 的鎖正確;CapitalStore 全方法持 threading.Lock,COM 執行緒寫/loop 讀安全;BalanceCollector 文件化為 COM 單執行緒使用且屬實;local_store 為 loop-only 同步存取(檔內自述)成立;CdpService/signal_engine 狀態僅 event loop 觸碰;群益 COM apartment 親和性由專屬執行緒+佇列正確保證。略過未深讀:scripts/(一次性工具)、routes 各 CRUD 的業務邏輯(交給逐模組 reviewer)、bot/ 與 frontend/(不在本切面)、middleware/auth。另有兩個觀察未列為 finding(可辯護性不足):WSPool._status 在 overnight 重連成功後不會從 CIRCUIT_OPEN 復位(目前無 route 對外曝露,僅日誌);signal_engine._fanout 內 Discord HTTP 在 consume loop 序列上 await,極端慢時會墊高 tick lag(有 cooldown 限流,影響有限)。
- error-handling:驗證方法補充:凡涉及富邦 SDK 行為的結論(message 事件 payload 是 JSON 字串、.on() 經 pyee 以 callable 為 key 累積 listener、websocket_client.stock/futopt 為 factory 快取單例、主動 disconnect 也會 emit disconnect 事件),均直接讀取本專案 .venv 內 fubon-neo 2.2.8 實際安裝的 fugle_marketdata 與 pyee 11.1.1 原始碼確認,未憑印象;群益 SKCOM 的「回報斷線事件名」一項無法從本地驗證,finding 已標註需對官方 docx 確認。涵蓋範圍:fubon_client/fubon_ws/fubon_futures(_ws)/overnight/lifecycle_sync/signal_engine/ring_buffer/ws_broadcaster/rate_limiter/alerts/discord_notifier/capital_* 全鏈/main.py/middleware 與 routes(quote/candles/ma/mxf/preview/bookmarks/monitor_list/config_io/symbols/ws/capital)。略過或淺讀:routes/watchlist.py、active_signals.py、signals_history.py、camarilla 與 cdp 的 route 層、scripts/、models/condition.py、tests/(test-gap 面向只間接觸及,未系統盤點)、local_store 的 market_cache.py 細部;富邦官方遠端文件本輪未另行 WebFetch(以安裝版 SDK 原始碼為準,對 2.2.8 而言比文件索引更權威)。另有一項觀察未列 finding:config_store 壞檔時改名 .json.corrupt 後重建屬合理降級,但僅保留一份備份,連續兩次損壞會覆蓋前一份。
- rate-limit:查證紀錄:富邦額度依官方 docs/market-data/rate-limit.txt(WebFetch 實查)——日內/快照 300/min、歷史 60/min、WS 每連線 200 訂閱、每帳號 5 連線、REST 超限 429;SDK listener append 語意直接讀 .venv 內 fugle_marketdata/websocket/client.py(on()=pyee EventEmitter.on)與 fubon_neo/adapter.py(stock/futopt wrapper cache=singleton)確認,非憑印象。訂閱上限有守:WSPool 以 WS_PER_CONN_CAP=200×MAX_CONNS=1 在 subscribe 時硬擋,top_gainers(≤50)+書籤+監聽+preview 都走同一 pool refcount,期貨另用 futopt 連線 1 檔,無繞過路徑。輪詢額度乘算:1Hz 五檔輪詢前端已做 per-symbol 共用 poller 且背景分頁暫停(useQuoteBook),60/min/檔,搭配 30s candles 輪詢在單人使用下距 300/min 尚有餘裕——主要風險是帳外呼叫(candles/movers)與 SMA refill 突波,已列 finding。檔案 handle 全數逐寫逐關(capital_audit/signals_log/market_cache atomic write),無洩漏。已看但未列 finding:rate_limiter 本體(實作正確)、ring_buffer(有 maxlen+時間窗 trim)、capital_store(_orders 按日增長但量級極小)、preview/bookmarks/watchlist/lifecycle_sync(refcount 正確)、alerts/discord_notifier(per-call httpx client,可接受)、signals_log.query 全檔重讀(非熱路徑)。未深查:middleware/auth.py、models/、config_store.py 細節、bot/src 與 frontend(非本切面)、tests。另記:SDK 自身 connect() 是無 sleep 的忙等迴圈直到 auth 完成(.venv 原始碼 client.py:192),登入延遲時會燒一顆核,專案端無法修、僅供知悉。
- dead-code:掃描範圍:backend 全部自寫 .py(main.py、ws_broadcaster.py、routes/、services/、services/local_store/、jobs/、middleware/、models/,約 6.5k 行)逐檔讀畢;每個 finding 都對 frontend/src、bot/src、scripts、backend/tests 四處 grep 過呼叫端(route 用 API 路徑字串、符號用名稱)才認定死碼。未報但確認過的項目:POST /api/bookmarks/top-gainers/refresh 與 POST /api/symbols/refresh 雖無前端/bot 呼叫端,但前者 docstring 明示「手動觸發,給除錯與測試用」、後者被 bootstrap 失敗 log 指名為手動救援入口,屬有意保留的 admin endpoint;fubon_ws 的多連線池機制(MAX_CONNS=1 下仍保留 conn_idx 索引)有完整註解說明 SDK singleton 限制與未來 multi-process 路線,屬documented design 非過度複雜;Condition.days_ago(ge=0,le=0)為 docstring 明示的 v1 保留欄位。未深究面向:backend/scripts/(capital_smoke、capital_login_probe、migrate_supabase 為一次性工具,未列入死碼判定)、tests 本身的冗餘、以及各模組單檔正確性(已有逐模組 reviewer)。本次所有 finding 均為本地邏輯,不涉富邦 SDK 行為判斷,故未走富邦文件 WebFetch 工作流程。
- test-gaps:掃描方式:pytest --collect-only(324 tests)對照 backend 全部 production 檔案,深讀 fubon_ws、fubon_futures_ws、capital_client/com/store/safety/close、ring_buffer、local_store(config_store/signals_log/paths)、rate_limiter、signal_engine(骨架)及對應測試。富邦 SDK 行為依規範查證:grep docs/api/fubon-neo-llms.txt + WebFetch 官方期貨 WS getting-started(確認 message handler 收 JSON 字串),並以本機安裝的 fubon-neo 2.2.8 / fugle_marketdata 原始碼交叉印證(emit 原始字串、factory 單例快取、pyee append 式 on())。覆蓋良好不另報:capital_store(部分成交/亂序/終態鎖死測得很完整)、capital_safety、signal_engine 各策略(limit_up/breakout_retest/pre_open/day_metrics/cooldown 都有測)、capital 稽核鏈。未深讀(讓給逐模組 reviewer):routes/candles、cdp、mxf、preview、quote、ws、jobs/top_gainers_scheduler、overnight.py、ws_broadcaster、discord_notifier、camarilla(有測試)、fubon_client REST 包裝。略過未報:rate_limiter 無單元測試但邏輯簡單且有煙霧腳本(若要補,鎖 300/min 與 historical 60/min 兩個額度意圖即可);signals_log 壞行跳過邏輯未測但風險低(壞行只影響 next_id 推算,極端情境是 id 重複)。
- type-safety:掃描範圍:backend/ 全部非測試原始碼(services、routes、jobs、models、middleware、main.py、ws_broadcaster.py)逐檔讀完;tests/ 與 scripts/(capital_smoke、capital_login_probe、migrate_supabase_to_local)只略讀未深究,scripts 為一次性工具不列 finding。富邦文件工作流程:已 Grep docs/api/fubon-neo-llms.txt 並 WebFetch 查證 technical/sma(data 遞增、最新在尾 → ma_service/routes/ma 取 data[-1] 正確,排除原疑慮)與 WS trades channel(price/size/bid/ask 為 number、price 非必揭示 → fubon_ws 的 None 檢查正確);historical.candles 排序、movers 欄位、futopt tickers 欄位等其餘 envelope 假設與程式內注記的實測紀錄一致且程式已防禦(.get + or []),未逐條 WebFetch。capital_* 整體防禦水準高(NaN 擋、稽核先行、回報欄位寬鬆解析、終態閘),僅列出兩條真錢級缺口;capital 回報/庫存欄位索引依專案內已實測的對照表評斷,未重查群益官方 docx(非富邦工作流程範圍)。已知未報項:overnight 重登入舊 SDK session 未登出(資源洩漏,屬單檔明顯問題留給模組 reviewer)、試撮 tick 污染開盤初期 surge 視窗(程式註解已自承為 spec 開放問題)、dedupe_positions 同檔多種類取大者(註解已自承過渡取捨)。

---

## 修復狀態(2026-06-12)

103 個 confirmed findings 已於分支 `fix/backend-review-1`(疊在 fix/frontend-review-lows 之上)修復,6 個 commit:
59686a8 capital / 2aaabdb fubon-ws / 462a434 futures / 4554fae signals / 289bb44 store / bd03dd2 routes。
驗證:後端 429 測全綠(原 324,+105)、前端 tsc -b + 182 測 + vite build 全綠。

刻意保留未修(3 項,皆 low 級):
1. `OrderResult.seq_no` 恆 null 死欄位 — 乾淨刪除需同步改前端型別;另一解(從 SendStockOrder 回訊 parse 序號)無法離線驗證群益訊息格式,真錢路徑不憑印象猜。
2. `routes/ma.py` 與 ma_service 的重複邏輯 — 正解要改 fetch_sma 回傳簽名,bot 與前端都消費 as_of_date,等有行為需求再動。
3. `routes/ws.py` pong 與 broadcast 理論上可並發寫同 socket — broadcaster 已改單 consumer 序列送出,殘餘風險僅 pong 路徑,單人本機部署影響極低。

待盤中實機驗證:期貨 WS 即時 candle(修好的 JSON 字串解析路徑從未實機觀察過)、群益 OnDisconnect 實際觸發行為、市價單 bstrPrice 欄位是否被群益忽略(capital-core finder 留的疑點)。
