# Codex Gateway V2 執行紀錄

> 本 LOG 僅記錄封存後獨立 Codex Web 工具，不改寫 Phase 2 歷史。

## 2026-07-20 08:44 UTC（2026-07-20 16:44 Asia/Taipei）— 範圍裁決與官方契約盤點

- **目的**：依使用者明確要求，建立可直接與 Codex 對話、並可切換角色／
  場景／分鏡工作區的本機 Web Gateway 骨架。
- **執行內容**：
  - 唯讀確認 repo 沒有既有「Codex Gateway」規格，且舊專案已封存、
    Phase 2 未完成；因此將本工作定義為隔離的 V2 工具入口，不冒充產品內
    LLM Planner 或 AIPE Worker Adapter。
  - 依 `openai-docs` skill 取得當日最新 Codex manual，核對 app-server 是
    深度產品整合介面，stdio transport 使用 JSONL，client 必須先
    `initialize`／`initialized`，再使用 thread／turn API。
  - 以本機 `codex-cli 0.144.6` 產生 JSON Schema 到 `/tmp`，核對
    `thread/start`、`turn/start`、`turn/completed`、agent message item
    及 approval request 的實際欄位；未將暫存 schema 寫入 repo。
  - 建立獨立任務單，固定 loopback、read-only sandbox、fail-closed
    approval、typed mock catalog 與三工作區 UI 邊界。
- **修改檔案**：
  - `docs/tasks/CODEX_GATEWAY_V2.md`
  - `docs/tasks/CODEX_GATEWAY_V2_LOG.md`
- **重要命令**：
  - `node .../openai-docs/scripts/fetch-codex-manual.mjs`
  - `codex --version`
  - `codex app-server --help`
  - `codex app-server generate-json-schema --out /tmp/storyboard-codex-app-server-schema`
  - repo／brief／Phase／backend／frontend 唯讀 `rg`、`sed` 與 Git status 稽核。
- **驗證結果**：
  - Codex manual cache 為 current；官方建議深度整合使用 app-server，
    automation／CI 才優先使用 SDK。
  - 本機 CLI 可提供 JSONL stdio app-server 與版本對應 schema；目前
    Python `openai-codex` SDK 未安裝，因此本輪不新增 beta SDK dependency。
  - 既有未提交封存 allowlist 原樣保留：`.gitignore`、`README.md`、
    `PHASE2_LOG.md`、`PHASE2_S7_PLAN.md`、`PROJECT_ARCHIVE_HANDOFF.md`。
- **發現事項**：
  - app-server protocol 會隨 Codex CLI 版本演進；adapter 必須只使用已核對
    的 method subset、對未知 notification 容錯，且以 focused fake
    protocol tests 防止 silent drift。
  - 現有 FastAPI 啟動依賴 live ComfyUI contract，不適合承載本 Gateway；
    必須另建不依賴 GPU／DB／ComfyUI 的 app factory。
- **下一步**：實作獨立 gateway backend、typed mock catalog 與三工作區
  frontend，再執行 fake protocol、API、browser、完整品質矩陣。

## 2026-07-20 09:00 UTC（2026-07-20 17:00 Asia/Taipei）— Gateway 與三工作區首個可執行切片

- **目的**：交付不依賴 ComfyUI 的本機頁面、typed API 及可直接驅動
  Codex app-server 的最小完整垂直切片。
- **執行內容**：
  - 新增獨立 `gateway_main` app factory、Gateway settings、strict DTO、
    mock catalog provider、thread authority service 與四個 HTTP endpoints。
  - 新增 Codex JSONL stdio adapter：lazy child process、initialize handshake、
    thread／turn、terminal agent message、背景 stdout／stderr task ownership、
    timeout、不自動重送與 fail-closed approval response。
  - 新增繁體中文故事工作台：生成角色／生成場景／生成分鏡 tabs、角色
    風格選擇、三組 code-native placeholder 櫥窗及固定／抽屜式 Codex
    對話面板。
  - 修正前後端契約，browser 只送
    `workspace=character|scene|storyboard` 與 typed
    `selected_item_id/prompt_draft/reference_ids`；移除原前端的 opaque
    nested context。
  - 加入 localhost Trusted Host、同源 CSP、`nosniff`、no-referrer 與
    frame deny；Codex response 全以 DOM `textContent` 呈現。
- **修改檔案**：
  - `backend/app/gateway_main.py`
  - `backend/app/core/gateway_settings.py`
  - `backend/app/schemas/api/codex_gateway.py`
  - `backend/app/api/routes/codex_gateway.py`
  - `backend/app/services/codex_gateway/`
  - `frontend/gateway/`
  - `tests/test_codex_gateway_api.py`
  - `tests/test_codex_gateway_client.py`
- **重要命令**：
  - `.venv/bin/pytest -q tests/test_codex_gateway_api.py
    tests/test_codex_gateway_client.py`
  - `.venv/bin/ruff format ...`、`.venv/bin/ruff check ...`
  - `.venv/bin/mypy ...`
  - `node --check frontend/gateway/app.js`
  - dangerous DOM API、workspace drift、`/free` focused `rg`。
- **驗證結果**：
  - focused pytest=`4 passed, 1 known Starlette/httpx deprecation warning`。
  - 新增 Python paths Ruff PASS、mypy=`10 source files` PASS。
  - JavaScript syntax PASS；新增 executable source 無 `innerHTML`、
    `document.write`、外部 asset URL 或 `/free`。
  - fake app-server test 實際走 child process JSONL，並證明 command
    approval request 收到 `decline` 後才完成 turn。
- **發現事項**：
  - 前端原草稿曾以專案名稱當 `workspace` 且傳 nested context，會被 strict
    backend 正確拒絕；已改成三值 enum 與扁平 typed context。
  - 目前為最終文字 response；尚未把 app-server delta 轉成 HTTP streaming，
    但 adapter 已安全收集 delta 作為 final-message fallback。
- **下一步**：補 browser E2E 與真 Codex smoke，修正任何視覺／protocol
  問題後再跑完整 pytest、Ruff、mypy、靜態安全與 diff 稽核。

## 2026-07-20 09:11 UTC（2026-07-20 17:11 Asia/Taipei）— Browser E2E、真實連線限制與 protocol 強化

- **目的**：驗證三工作區在真實 browser 的操作與 responsive 行為，並以
  本機 Codex CLI 做 app-server smoke。
- **執行內容**：
  - 新增 Playwright E2E，涵蓋三 tabs、鍵盤切換、角色風格、三工作區
    context、聊天 thread 延續、desktop／390px 版面與水平溢位。
  - 第一次 browser 執行因環境缺少 `libnspr4.so` 失敗；改用 repo 既有的
    Playwright runtime library／font 設定後通過，沒有安裝或改寫系統套件。
  - 實際啟動 loopback Gateway 後，root／status／catalog 與安全 headers
    正常；建立真 Codex thread 時 child process 因受管沙箱把
    `~/.codex` 掛成唯讀而退出。
  - 直接執行 `codex app-server --listen stdio://` 確認錯誤來源是無法建立
    `~/.codex` SQLite state，不是 HTTP 或 JSONL 欄位錯誤。
  - 依規定提出一次沙箱外 loopback smoke 權限請求，但執行環境拒絕
    unsandboxed child 存取；未使用替代路徑繞過限制。
  - 補上 spawn failure sanitization、malformed JSONL、timeout interrupt
    且不重送、>64 KiB 有界 JSONL、超限錯誤與 late turn notification
    隔離測試；reader liveness 同時檢查 process 與 owned task。
- **修改檔案**：
  - `backend/app/core/gateway_settings.py`
  - `backend/app/services/codex_gateway/client.py`
  - `tests/test_codex_gateway_client.py`
  - `tests/e2e/test_codex_gateway_page.py`
- **重要命令**：
  - `env LD_LIBRARY_PATH=/tmp/playwright-deps/usr/lib/x86_64-linux-gnu
    FONTCONFIG_FILE=/tmp/playwright-fonts.conf .venv/bin/pytest -q
    tests/e2e/test_codex_gateway_page.py`
  - `codex app-server --listen stdio://`
  - loopback `uvicorn`、`curl` root／status／catalog／thread smoke。
- **驗證結果**：
  - Browser E2E 最終 PASS；desktop 與 mobile 截圖經人工檢視，結構與
    responsive drawer 正常。
  - Fake app-server 測試證明 thread 參數固定 `cwd`、`read-only`、
    `approvalPolicy=never`、ephemeral，command／file／legacy／permissions
    互動請求全部 fail closed。
  - 真 Codex terminal smoke 未完成，唯一 blocker 是本次受管沙箱無法寫入
    使用者既有 `~/.codex` state；正常本機 shell 啟動命令不受此 repo
    程式限制。
- **發現事項**：
  - `asyncio` subprocess 預設 64 KiB line limit 不足以承載完整 turn
    payload；已改為可設定的有界 8 MiB，超限回穩定 protocol error。
  - waiter 若只以 thread ID 關聯，舊 turn 晚到通知可能污染下一輪；已改用
    `(thread_id, turn_id)` 並清除 retired buffer。
- **下一步**：完成前端慢速 thread race、mobile modal focus、正式
  preview URL 接線與全專案品質矩陣。

## 2026-07-20 09:29 UTC（2026-07-20 17:29 Asia/Taipei）— 前端 race／素材接點修正與全專案驗證

- **目的**：收斂獨立 code review findings，確認 Gateway 不回歸既有
  Storyboard 引擎。
- **執行內容**：
  - `sendTurn()` 起始即固定 workspace，避免建立 thread 途中切換 tab
    造成舊 context 配新分類。
  - browser timeout 改為略長於 server 15 分鐘 deadline，確保由後端先
    interrupt 同一 turn 並回 504，不留下 browser 已放棄的隱藏請求。
  - mobile drawer 補上動態 `dialog`／`aria-modal`、背景 inert、focus
    trap、Escape 關閉與返回原焦點；窄螢幕 tabs 同步改為 horizontal
    orientation。
  - catalog `preview_url` 限制為安全同源路徑，前端角色、場景與分鏡櫥窗
    均已能直接顯示正式圖片；目前 mock provider 仍刻意留空。
  - `reference_ids`、catalog tags 與 protocol JSONL 加入單項／總量上限；
    child spawn error 與 stderr 均不回傳底層敏感內容。
- **修改檔案**：
  - `backend/app/schemas/api/codex_gateway.py`
  - `backend/app/services/codex_gateway/client.py`
  - `frontend/gateway/index.html`
  - `frontend/gateway/styles.css`
  - `frontend/gateway/app.js`
  - `tests/test_codex_gateway_api.py`
  - `tests/test_codex_gateway_client.py`
  - `tests/e2e/test_codex_gateway_page.py`
- **重要命令**：
  - `env ... .venv/bin/pytest`
  - `.venv/bin/ruff format --check . && .venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `git diff --check`、`/free` 與秘密樣式靜態掃描。
  - `.venv/bin/alembic check`
- **驗證結果**：
  - full pytest=`1214 passed, 34 warnings in 140.46s`。
  - Ruff=`144 files already formatted / All checks passed`。
  - mypy=`139 source files / no issues`；Node syntax、`git diff --check`、
    新增 executable source `/free` 掃描均 PASS。
  - `alembic check` FAIL：現有 `data/app.db` 尚未升到 repo migration head；
    本任務沒有修改 model／migration，為避免擴張範圍未自行升級使用者
    資料庫。
- **發現事項**：
  - 全套測試的既有 warnings 為 Starlette/httpx deprecation、SQLite
    migration FK cycle 與 Python 3.12 sqlite datetime adapter；本輪未新增
    failure。
  - Headless 環境沒有 CJK 字型，因此截圖中文字顯示方框；CSS 已依序指定
    `Noto Sans TC`、PingFang TC 與 Microsoft JhengHei，Windows 實機會走
    本機繁中字型，不引入 CDN。
- **下一步**：重跑最終 focused/browser checks、補 session handoff；正式
  風格圖到位後只替換 catalog 資料與同源 preview assets。

## 2026-07-20 09:35 UTC（2026-07-20 17:35 Asia/Taipei）— 最終驗證與 session 交接

- **目的**：確認交付狀態、測試結果與下一位 agent 可直接延續的邊界。
- **執行內容**：
  - 關閉會依賴外部 CDN 的 Swagger UI，保留同源
    `/api/openapi.json`。
  - UI 將「找到 Codex CLI」與「child 已連線」分開顯示，避免 status
    在尚未驗證登入／state 時誤稱已連線。
  - 重跑最終 focused、browser、完整 pytest、Ruff、mypy、Node syntax、
    `/free`、秘密樣式與 diff 檢查。
- **修改檔案**：
  - 本 session 新增／修改的 Gateway backend、frontend、tests、
    `CODEX_GATEWAY_V2.md` 與本 LOG。
  - 未修改 `workflows/**/*.json`、既有 DB model／migration、ComfyUI
    adapter 或封存文件的既有內容。
- **重要命令**：
  - `.venv/bin/pytest -q tests/test_codex_gateway_api.py
    tests/test_codex_gateway_client.py`
  - `env ... .venv/bin/pytest -q tests/e2e/test_codex_gateway_page.py`
  - `env ... .venv/bin/pytest`
  - `.venv/bin/ruff format --check . && .venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`、`git diff --check`、安全 `rg`。
- **驗證結果**：
  - Gateway focused=`10 passed, 1 known warning`；browser E2E=`1 passed`。
  - 最終 full pytest=`1214 passed, 34 warnings in 140.94s`。
  - Ruff、mypy（139 source files）、Node syntax、`git diff --check`、
    `/free` 與秘密樣式掃描 PASS。
  - `alembic check` 維持 FAIL，原因是既有 `data/app.db` 落後 migration
    head；本任務無 schema 變更且未擅自 migration。
- **發現事項**：
  - 真 Codex round-trip 仍需在非受管唯讀沙箱的使用者 shell 驗收；
    app-server fake protocol、實際 CLI schema、root／status／catalog 與
    browser HTTP round-trip 均已驗證。
  - 當前 branch=`phase/2`，HEAD=
    `f6a67434af3b69cf36aaa37e039b072862418c01`；未建立 commit。
  - 工作目錄非乾淨：除本 Gateway 新檔外，session 開始前即存在
    `.gitignore`、`README.md`、`PHASE2_LOG.md`、
    `PHASE2_S7_PLAN.md`、`PROJECT_ARCHIVE_HANDOFF.md` 變更；均原樣保留，
    未混改或刪除。
- **下一步**：
  - 使用者在一般 WSL shell 執行任務單 §9 命令，開啟
    `http://127.0.0.1:8010` 後送一則訊息完成真 Codex 驗收。
  - 使用者提供正式風格／場景／分鏡圖後，新增同源 asset endpoint 或
    靜態資產，將安全 `preview_url` 填入 typed catalog provider。
  - 需要 commit 時只 stage 本 Gateway allowlist，避免把既有封存變更
    混入同一 commit。

## 2026-07-20 10:54 UTC（2026-07-20 18:54 Asia/Taipei）— 取消對話介面並建立二十種角色風格契約

- **目的**：依使用者最新明確指示移除右側 Codex 對話框，將角色頁改成
  同一示範角色、二十種媒材與固定提示詞片段一對一綁定的展示櫥窗。
- **執行內容**：
  - 移除桌面對話面板、手機聊天抽屜、連線狀態及三個「送交 Codex」入口；
    場景與分鏡改成沒有假按鈕的功能預留說明。
  - 新增 strict `CharacterStyleItem.prompt_fragment`，mock catalog 由四項
    擴充為二十項，涵蓋漫畫、線稿、黑暗童話、油畫、電影、寫實等方向。
  - 角色風格卡改由 catalog 動態建立；每張卡使用完全相同的角色 DOM
    幾何，只允許 preview class 改變媒材、色盤、紋理與光線。
  - 選取卡片後同步更新大型預覽、說明、標籤及原始風格提示詞；複製功能
    會組合使用者角色描述、不可改變角色識別的一致性規則與所選風格片段，
    且明示目前不啟動圖片生成。
  - 重寫 browser regression，要求二十張卡、無任何對話 DOM、選取同步、
    clipboard 內容、鍵盤 tabs、desktop／mobile 版面及零 thread／turn 呼叫。
- **修改檔案**：
  - `backend/app/schemas/api/codex_gateway.py`
  - `backend/app/services/codex_gateway/catalog.py`
  - `frontend/gateway/index.html`
  - `frontend/gateway/app.js`
  - `tests/test_codex_gateway_api.py`
  - `tests/e2e/test_codex_gateway_page.py`
  - `docs/tasks/CODEX_GATEWAY_V2.md`
  - `docs/tasks/CODEX_GATEWAY_V2_LOG.md`
- **重要命令**：
  - `.venv/bin/pytest -q tests/test_codex_gateway_api.py`
  - `.venv/bin/ruff check tests/test_codex_gateway_api.py
    tests/e2e/test_codex_gateway_page.py backend/app/schemas/api/codex_gateway.py
    backend/app/services/codex_gateway/catalog.py`
  - `node --check frontend/gateway/app.js`
  - HTML parser、Codex/chat DOM 與危險 DOM API focused `rg`。
- **驗證結果**：
  - Gateway API focused=`3 passed, 1 known Starlette/httpx warning`。
  - Focused Ruff 與 JavaScript syntax PASS；HTML 可解析。
  - Catalog regression 證明二十個 ID 唯一、每項都有非空白
    `prompt_fragment`，且必要的六種代表風格均存在。
  - Browser E2E、完整 pytest 與全專案品質矩陣尚待新版 CSS 完成後執行，
    本條不提前宣稱最終驗收。
- **發現事項**：
  - 使用者取消的是網站對話 surface；既有受限 app-server backend 暫時保留
    為未來自動規劃接點，但本頁只呼叫 catalog，E2E 必須證明沒有建立
    thread／turn。
  - 正式風格圖片仍未提供；本輪只提供可替換的 code-native 展示預覽，
    不冒充生成結果。
- **下一步**：完成二十種媒材 CSS、執行並人工檢視 desktop／mobile browser
  截圖，再跑 focused、完整 pytest、Ruff、mypy、安全掃描與 diff 稽核。

## 2026-07-20 11:01 UTC（2026-07-20 19:01 Asia/Taipei）— 風格櫥窗驗收與 session 交接

- **目的**：完成二十種風格的 responsive 視覺層、browser 驗收與全專案
  回歸，確認移除對話 UI 沒有破壞既有 Storyboard 引擎。
- **執行內容**：
  - 工作台由三欄改為 tool rail＋workbench 兩欄，完整刪除對話、狀態與
    mobile drawer CSS；二十個 preview class 共用同一人物幾何，僅改色盤、
    紋理、光線與媒材效果。
  - 桌面風格區使用 auto-fill 展示卡，390px 手機固定兩欄；補上選取勾選、
    大型預覽、標籤、風格提示詞、clipboard 成功／失敗狀態。
  - 為 breaking catalog shape 將 schema version 更新為
    `storyboard-studio.catalog.v2`，避免已取消的 Codex branding 出現在頁面。
  - 實際執行 Playwright，人工檢視 desktop／mobile full-page 截圖；確認
    二十張卡均可讀、人物構圖一致、右側對話欄不存在、手機沒有水平溢位。
  - 首次 Ruff format check 找到新 E2E 尚未格式化，已用專案 Ruff 修正後
    重跑通過；兩次組合式安全掃描最初因預期 no-match 與過寬 `turn`
    pattern（誤中 `return`）回 exit 1，修正命令語意後最終掃描通過。
- **修改檔案**：
  - `backend/app/gateway_main.py`
  - `backend/app/schemas/api/codex_gateway.py`
  - `backend/app/services/codex_gateway/catalog.py`
  - `frontend/gateway/index.html`
  - `frontend/gateway/styles.css`
  - `frontend/gateway/app.js`
  - `tests/test_codex_gateway_api.py`
  - `tests/e2e/test_codex_gateway_page.py`
  - `docs/tasks/CODEX_GATEWAY_V2.md`
  - `docs/tasks/CODEX_GATEWAY_V2_LOG.md`
- **重要命令**：
  - `env LD_LIBRARY_PATH=... FONTCONFIG_FILE=... .venv/bin/pytest -q
    tests/e2e/test_codex_gateway_page.py`
  - `.venv/bin/pytest -q tests/test_codex_gateway_api.py
    tests/test_codex_gateway_client.py`
  - `env LD_LIBRARY_PATH=... FONTCONFIG_FILE=...
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest`
  - `.venv/bin/ruff format --check . && .venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `.venv/bin/python -m compileall -q backend tests alembic`
  - `node --check`、`/free`、對話 DOM、危險 DOM API、外部 URL、秘密樣式、
    `git diff --check` 與 Git 狀態稽核。
  - `.venv/bin/alembic check`
- **驗證結果**：
  - Browser E2E=`1 passed in 3.44s`；角色頁二十張卡、黑暗童話選取、
    完整 clipboard 提示詞、鍵盤 tabs、desktop／mobile、零
    thread／turn 呼叫全部通過。
  - Gateway focused=`10 passed, 1 known warning`。
  - Full pytest=`1214 passed, 34 known warnings in 168.09s`。
  - Ruff=`144 files formatted / All checks passed`；mypy=`139 source files /
    no issues`；compileall、Node syntax、靜態安全與 `git diff --check` PASS。
  - `alembic check` 維持 FAIL：`Target database is not up to date`；本輪沒有
    修改 model／migration，依範圍與安全要求未擅自升級既有 `data/app.db`。
- **發現事項**：
  - 正式風格圖片仍待使用者提供；catalog 的同源 `preview_url` 接點已保留，
    屆時可逐卡原位替換而不改選取 ID 或提示詞契約。
  - 場景與分鏡目前仍是可互動版面預覽，沒有生成或規劃 submit button；
    UI 已明確標示，不冒充已接線功能。
  - 當前 branch=`phase/2`，HEAD=
    `f6a67434af3b69cf36aaa37e039b072862418c01`，相對 upstream=
    `0 behind / 42 ahead`；本 session 未建立 commit。
  - 工作目錄非乾淨：Gateway 新檔尚未追蹤；session 前既有的
    `.gitignore`、`README.md`、`PHASE2_LOG.md`、`PHASE2_S7_PLAN.md`、
    `PROJECT_ARCHIVE_HANDOFF.md` 變更均保留。另發現未追蹤
    `最後的最後.txt`，本輪未讀寫、未刪除、未納入任何範圍。
- **下一步**：
  - 使用者可依任務單 §9 啟動 `127.0.0.1:8010`，直接使用角色風格櫥窗；
    不需要 Codex 登入或 ComfyUI。
  - 使用者提供二十張正式風格圖後，只新增受控同源資產並填入各 catalog
    item 的 `preview_url`；不得改變既有 item ID 或 prompt fragment，
    除非使用者同時要求修改該風格契約。
  - 若要接正式圖片生成，須另立後續里程碑，經 Validator／Compiler／
    ComfyUI 既有鐵律接線；本 session 不自行擴張。

## 2026-07-20 11:03 UTC（2026-07-20 19:03 Asia/Taipei）— 重啟本機展示服務至新版 catalog

- **目的**：避免使用者開啟 `8010` 時，HTML 雖已更新但長駐 Python process
  仍回傳舊四風格 catalog。
- **執行內容**：
  - 唯讀確認 `127.0.0.1:8010` 的既有服務啟動於 18:20，API 仍回
    `codex-gateway.catalog.v1` 與四個角色風格。
  - 對已辨識的舊 uvicorn PID `469190` 發送 `SIGTERM`，確認 port 釋放後，
    以目前工作樹重新啟動 loopback-only Gateway。
- **修改檔案**：
  - `docs/tasks/CODEX_GATEWAY_V2_LOG.md`（本環境操作紀錄）。
- **重要命令**：
  - 沙箱外唯讀 `ps -eo pid,lstart,cmd`
  - `kill -TERM 469190`
  - `.venv/bin/uvicorn app.gateway_main:app --app-dir backend
    --host 127.0.0.1 --port 8010`
  - loopback root／catalog／static JavaScript `curl` 驗證。
- **驗證結果**：
  - 新服務已在 `http://127.0.0.1:8010` 正常監聽。
  - Live catalog schema=`storyboard-studio.catalog.v2`、
    `character_styles=20`、`all_prompts=True`。
  - Live HTML 包含「角色風格櫥窗」與「20 種風格」，沒有舊對話面板或
    open-chat button；JavaScript 沒有 thread／turn 呼叫。
- **發現事項**：StaticFiles 會即時讀取 HTML／CSS／JS，但 Python catalog
  provider 需重啟 process 才會載入新版；本次已同步完成。
- **下一步**：使用者可直接開啟 `http://127.0.0.1:8010`；若 WSL 或電腦
  重啟，再依任務單 §9 執行同一個 loopback uvicorn 命令。
