# 群益下單 M1 登入 — 跨 session 交接(2026-06-09)

## TL;DR:現在卡在哪
M1 smoke 跑到 `SKCenterLib_Login` 回 **`SK_ERROR_TELNET_LOGINSERVER_FAIL`**(測試環境登入伺服器連線失敗)。
**這不是程式 bug** —— login 流程已跑通到「連登入伺服器」這層;最可能是 `SetAuthority(2)` 測試環境的伺服器目前不可用 / 有開放時段 / 該帳號未啟用測試環境。正是 spec 開放問題 #4(測試環境可用性/時段),M1 正在把它逼出來。

## 已修 / 已驗(都已 commit 在 `feat/capital-order-panel`)
- `55e6028` **CAPITAL_DLL_DIR**:`SkcomCapitalCom` 由 env 指定 `SKCOM.dll` 資料夾 + `os.add_dll_directory`。修 Python 3.13 嚴格 DLL 搜尋載不到 SKCOM 相依(SKWebCALib 等)。
- `8f39542` **reply 事件存參考**:修登入 `SK_WARNING_REGISTER_REPLYLIB_ONREPLYMESSAGE_FIRST`。根因 = `comtypes.client.GetEvents()` 的 sink 與回傳連線被丟掉 → CPython 立即 GC → `Unadvise` → 登入時群益看不到 OnReplyMessage。官方 `order_service/Order.py:581-590` 是把 sink 與 handler 存在長生命變數;已比照存到 `self._reply_sink/_reply_conn`。
- 全後端套件 **219 passed**。

> 這兩個修正讓 login 從「警告」推進到「連線層錯誤」—— 都是 M1 該除的真實整合風險,已除。

## 環境(user 的 Windows 機,已就緒,**不用重做**)
- 群益 API 已開通(`SKCOMVerifyDJ.exe` x64 在**正式**環境驗證通過)。
- SKCOM 已 `regsvr32`(smoke 能 `CreateObject` 成功 = 已註冊)。
- 元件穩定路徑:`C:\Users\USER\CapitalAPI\x64`(從 `CapitalAPI_2.13.58_PythonExample` 的 `元件\x64` 複製,含 SKCOM.dll + 相依 + install.bat)。
- 主 venv `C:\side-project\treading-king\backend\.venv`:已裝 `comtypes 1.4.16` + `pywin32 312`(pythoncom)。
- `backend/.env`(此 worktree,已 gitignored):`CAPITAL_USER_ID`/`CAPITAL_PASSWORD` 已填、`CAPITAL_ENV=test`、`CAPITAL_DLL_DIR=C:\Users\USER\CapitalAPI\x64`、`CAPITAL_ORDER_ENABLED=false`。

## 重跑 M1 smoke
```powershell
cd C:\side-project\treading-king\.claude\worktrees\capital-order-panel\backend
PYTHONUTF8=1 C:\side-project\treading-king\backend\.venv\Scripts\python.exe scripts\capital_smoke.py
```
登入流程(`capital_client.py:_run`):`setup()`(載 SKCOM + 建 center/order/reply + 註冊 reply 事件)→ `set_authority(2 if test else 0)` → `login` → `init_order` → `read_cert`。**目前停在 login**(回 TELNET_LOGINSERVER_FAIL)。

## 下一步(systematic-debugging,一次一個假設)
1. **隔離:是不是「測試環境專屬」的問題** —— 暫時 `CAPITAL_ENV=prod`(→ `SetAuthority(0)` 正式)只跑 login(`ORDER_ENABLED` 維持 `false`、**不送單**)。
   - 正式登入成功 → 證明帳密/連線/憑證都 OK,blocker 純粹是測試環境(下面 H1)。
   - ⚠️ **這會登入 user 的真實帳號**(僅認證、不下單,但仍是對外動作)→ **先問 user 同意再跑**。
2. **查測試環境時段/可用性** —— SDK 根目錄有 `策略王COM元件使用說明_V2.13.58.docx`(docx 非純文字,要用 python-docx 或解壓 XML 才讀得到);或直接問群益客服「SetAuthority(2) 測試環境登入伺服器開放時段/是否需另外申請」。
3. **盤中重試** —— 測試伺服器可能跟著盤(平日約 09:00–13:30)才開;非盤中可能就是連不上。

### 假設清單
- **H1(最可能)** 測試環境(SetAuthority 2)登入伺服器有開放時段 / 目前不可用 / 該帳號未開通測試。官方 .py 範例**完全沒呼叫 SetAuthority**(預設正式 0)→ `SetAuthority(2)` 是我們依 spec line 59 的設計,需驗證它真對應一個可用的測試伺服器。
- **H2** 網路/防火牆擋掉測試環境登入伺服器端點(與正式不同 host/port)。user 能跑 SKCOMVerifyDJ(正式)→ 正式連線 OK,但測試端點未必。
- **H3** SetAuthority 參數值在 2.13.58 是否仍為 2=測試(查 docx 確認)。

## 關鍵檔案
- `backend/services/capital_com.py` — COM 封裝(`setup`/`set_authority`/`login`/`read_cert`/`_resolve_skcom_load`)
- `backend/services/capital_client.py` — COM 專屬執行緒 `_run`(登入序列)
- `backend/services/capital_factory.py` — 從 env 組 `CapitalClient`(讀 CAPITAL_*)
- `backend/scripts/capital_smoke.py` — M1 煙霧腳本
- `backend/tests/test_capital_com.py` — DLL 路徑 + reply 事件防呆測試
- spec:`docs/superpowers/specs/2026-06-05-capital-order-panel-design.md`(開放問題 #4、SetAuthority、M1)
- 官方範例:`C:\Users\USER\Downloads\CapitalAPI_2.13.58_PythonExample (1)\CapitalAPI_2.13.58_PythonExample\`(`PythonExample\order_service\{Global,Order}.py`、`Reply_Service\Reply.py`;SDK 根的使用說明 docx)

## M1 之後
smoke 回 `狀態: ok` = M1 除風險完成 → 接 Plan 2(後端 `/api/capital/*` REST + WS 回報/部位)→ Plan 3(前端面板)。計畫:`docs/superpowers/plans/2026-06-05-capital-order-panel-plan{2,3}-*.md`。
