# Final Project MVP 執行紀錄

## 2026-07-20 11:10 UTC（2026-07-20 19:10 Asia/Taipei）— 建立獨立搬移 staging

- **目的**：依使用者要求，將本輪新 Gateway 專案自已封存的舊 Storyboard
  repository 抽離到 `~/project/final-project-mvp/`。
- **執行內容**：
  - 確認目標與其 parent 尚不存在，沒有覆寫風險。
  - 只複製 Gateway backend、二十風格 frontend、對應 tests 與歷史規格；
    排除舊 ComfyUI 引擎、SQLite、migration、workflow、模型與封存資料。
  - 建立獨立 `pyproject.toml`、README、AGENTS、環境範例、產品契約與
    append-only 專案紀錄。
  - 最小 runtime dependencies 僅保留 FastAPI、Pydantic、
    pydantic-settings 與 Uvicorn。
- **修改檔案**：
  - `/tmp/final-project-mvp-stage/` 下的獨立專案結構。
- **重要命令**：
  - 目標路徑 `ls`／`rg --files` 唯讀盤點。
  - Gateway import graph、pyproject 與檔案範圍稽核。
- **驗證結果**：staging 結構已建立；測試與正式搬移尚未執行，本條不提前
  宣稱完成。
- **發現事項**：
  - 舊 `app.schemas.__init__` 會 import 完整 Storyboard schema，不能帶入；
    新專案改用無舊依賴的最小 package initializer。
- **下一步**：修正 standalone branding、產生 lockfile，在 staging 執行
  unit／browser／Ruff／mypy，再搬到正式目標。

## 2026-07-20 12:09 UTC（2026-07-20 20:09 Asia/Taipei）— Staging 獨立性驗證通過

- **目的**：證明抽離後的專案不會透過舊 repo package initializer 或依賴
  偷渡 ComfyUI／DB 程式。
- **執行內容**：
  - 以新 `pyproject.toml` 產生獨立 `uv.lock`，解析 33 個最小 runtime／dev
    packages。
  - 從 staging cwd 執行全部 unit、protocol 與 browser E2E。
  - 執行 Ruff format/check、mypy、compileall、Node syntax、秘密樣式與
    舊引擎依賴掃描。
- **修改檔案**：
  - `uv.lock`
  - `docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `uv lock --python 3.12.10 --no-progress`
  - `pytest`
  - `ruff format --check .`、`ruff check .`
  - `mypy backend tests`
  - `python -m compileall -q backend tests`
  - `node --check frontend/gateway/app.js`
- **驗證結果**：
  - Staging full pytest=`11 passed, 1 known Starlette/httpx warning in 3.85s`。
  - Ruff=`20 files already formatted / All checks passed`。
  - mypy=`20 source files / no issues`；compileall 與 Node syntax PASS。
  - Backend 與最小 pyproject 沒有 aiosqlite、Alembic、SQLAlchemy、Pillow、
    PyYAML、ComfyUI、workflow 或 database 依賴。
- **發現事項**：
  - Catalog／首頁不需 Codex CLI；只有保留的 status/thread/turn API 需要
    本機 Codex。
- **下一步**：將 staging 原子搬到 `~/project/final-project-mvp/`，建立該
  位置自己的 `.venv`，再用新環境重跑驗證並切換 live 8010 服務。

## 2026-07-20 12:11 UTC（2026-07-20 20:11 Asia/Taipei）— 正式路徑環境建立與 Ruff 排除修正

- **目的**：在正式目標建立完全獨立的 Python 環境，並用該環境驗證
  lockfile，而不是依賴舊 repo 的 `.venv`。
- **執行內容**：
  - 將 staging 原子搬到 `/home/oscar0210/project/final-project-mvp`。
  - 依 frozen lockfile 建立專案自己的 Python 3.12.10 `.python` 與
    `.venv`，安裝 31 個 packages。
  - 用新 `.venv` 執行全部 11 個測試、mypy、compileall 與 Node syntax。
  - 首次 `ruff format --check .` 會掃到 uv 放在專案內的 `.python/` 標準
    函式庫，回報 650 個外部檔案；新增 `.python` 明確 exclude，沒有修改
    任何 Python distribution 檔案。
- **修改檔案**：
  - `pyproject.toml`
  - `docs/tasks/PROJECT_LOG.md`
  - `.python/`、`.venv/`（均由 `.gitignore` 排除的本機環境）。
- **重要命令**：
  - `uv sync --dev --python 3.12.10 --frozen --no-progress`
  - `.venv/bin/pytest`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/mypy backend tests`
  - `.venv/bin/python -m compileall -q backend tests`
  - `node --check frontend/gateway/app.js`
- **驗證結果**：
  - 正式路徑 full pytest=`11 passed, 1 known warning in 3.78s`。
  - mypy=`20 source files / no issues`；compileall、Node syntax PASS。
  - 首次 Ruff check 因工具範圍誤含 `.python` 而 FAIL；修正後結果待下一步
    重跑，本條不提前宣稱通過。
- **發現事項**：
  - `.python` 是 uv 的 project-local interpreter，不是專案 source；
    必須同時由 Git 與 Ruff 排除。
- **下一步**：覆寫修正後設定，重跑 Ruff；再切換 live 8010 服務並完成
  來源 repo 清理。

## 2026-07-20 12:14 UTC（2026-07-20 20:14 Asia/Taipei）— 正式搬移完成與 live 服務切換

- **目的**：完成使用者指定的專案搬移，確保 live 服務與所有 source 都只
  使用新位置。
- **執行內容**：
  - 修正後 Ruff 全專案 format/check 通過，uv lock check 通過。
  - 正常停止從舊 repo 啟動的 8010 uvicorn，改用新專案自己的 `.venv`
    從 `/home/oscar0210/project/final-project-mvp` 啟動。
  - 以 live OpenAPI、catalog、HTML 與 process cwd 交叉確認服務來源。
  - 從舊 Storyboard repo 刪除本輪新增的 Gateway backend、frontend、
    tests 與獨立 Gateway task docs，只保留搬移前就存在的使用者變更。
  - 清除兩邊測試產生的 Gateway `__pycache__`／`.pyc`；不修改任何舊產品
    source、資料庫、workflow 或使用者檔案。
- **修改檔案**：
  - 新專案 `pyproject.toml`、`docs/tasks/PROJECT_LOG.md`。
  - 舊 repo 中本輪新增的 Gateway 檔案已搬離。
- **重要命令**：
  - `.venv/bin/ruff format --check . && .venv/bin/ruff check .`
  - `uv lock --check`
  - `.venv/bin/uvicorn app.gateway_main:app --app-dir backend
    --host 127.0.0.1 --port 8010 --workers 1`
  - loopback OpenAPI／catalog／HTML `curl`
  - process cwd、來源 Git status 與檔案清單稽核。
- **驗證結果**：
  - Ruff=`20 files already formatted / All checks passed`。
  - Live OpenAPI title=`Final Project MVP`；process cwd=
    `/home/oscar0210/project/final-project-mvp`。
  - Live catalog schema=`storyboard-studio.catalog.v2`、
    `styles=20`、全部 prompt fragment 非空白。
  - 舊 repo 不再包含 Gateway source／frontend／tests／task docs；其
    `git status` 精確回到搬移前既有的五組封存變更與未追蹤
    `最後的最後.txt`，本輪均未修改。
- **發現事項**：
  - 新專案尚未初始化 Git repository；使用者只要求搬移，本輪不代為建立
    commit 或 remote。
  - `.python`、`.venv` 與品質工具 cache 留在新專案供立即使用，且均由
    `.gitignore` 排除。
- **下一步**：
  - 直接開啟 `http://127.0.0.1:8010`。
  - 重新開機後依 README 的 loopback uvicorn 命令啟動。
  - 正式風格圖到位後，依 `docs/PROJECT_SPEC.md` 與 catalog
    `preview_url` 契約逐卡接入。

## 2026-07-20 13:50 UTC（2026-07-20 21:50 Asia/Taipei）— 搬移後 Python 環境完整重建

- **目的**：先修復專案由 `~/project/` 再次搬到 `~/projects/` 後失效的
  project-local Python 與虛擬環境，功能施工暫不開始。
- **執行內容**：
  - 確認舊 `.venv` 的 Python symlink、console-script shebang 與
    `pyvenv.cfg` 全部指向不存在的舊路徑。
  - 發現舊 `.python` 除版本別名為 broken symlink 外，`sysconfig` 的
    prefix、library 與 include build metadata 也仍指向舊路徑；因此沒有
    只修補 `.venv`。
  - 將舊 `.python`、`.venv` 暫存至 `/tmp`，用 uv `0.11.28` 在目前路徑
    重新安裝 CPython `3.12.10`，再依 locked dependency groups 重建
    `.venv` 與 31 個套件。
  - 補正 README：專案搬移後必須重建兩個本機生成目錄，並明列
    Playwright Chromium 的 Ubuntu 系統相依安裝步驟。
- **修改檔案**：
  - `README.md`
  - `docs/tasks/PROJECT_LOG.md`
  - `.python/`、`.venv/`（由 `.gitignore` 排除的本機環境）
- **重要命令**：
  - `uv python install 3.12.10 --install-dir .python --no-bin`
  - `uv sync --locked --all-groups --python <project-local-python>
    --no-python-downloads --link-mode copy`
  - `uv lock --check`
  - `uv sync --locked --all-groups --check`
  - `.venv/bin/pytest`
  - 指定既有 user-space Chromium runtime 的完整 pytest。
- **驗證結果**：
  - Python=`3.12.10`、Playwright=`1.61.0`；lock 解析 33 個 packages，
    `.venv` 精確同步 31 個 packages。
  - 新 `.python`／`.venv` 沒有 broken symlink、舊路徑字串或錯誤
    sysconfig metadata。
  - Ruff format/check、mypy 與 Node syntax PASS。
  - 未指定額外 runtime 時，10 個 unit/protocol tests 通過，browser E2E
    因 WSL 缺 `libnspr4.so` 無法啟動 Chromium；使用既有 user-space
    runtime 後完整 pytest=`11 passed, 1 known Starlette/httpx warning`。
- **發現事項**：
  - Python package 環境已修復；目前 WSL 的 Playwright 系統函式庫仍需由
    使用者以 README 的 `sudo ... install-deps chromium` 安裝，Codex
    受管工作區無權修改系統套件。
  - 目前 `.git` 是空的唯讀目錄，不是有效 repository，因此仍無法執行
    `git diff --check`；本輪未假裝該檢查通過，也未自行建立 repository。
- **下一步**：
  - 在一般 WSL shell 安裝 Playwright 系統相依後，以不帶環境 workaround
    的 `.venv/bin/pytest` 重驗。
  - 恢復或初始化有效 Git repository；兩項完成前不開始功能施工。

## 2026-07-20 13:55 UTC（2026-07-20 21:55 Asia/Taipei）— Playwright 系統相依驗收

- **目的**：確認使用者完成 Ubuntu Chromium 系統相依安裝，並在沒有
  user-space workaround 的情況下驗收完整環境。
- **執行內容**：
  - 直接執行標準 `.venv/bin/pytest`。
  - 以 Playwright dry-run 檢查 Chromium 系統相依。
  - 重跑 Ruff format/check、mypy 與 Node syntax。
  - 使用者確認本專案沒有既有 Git 歷史後，嘗試初始化 `main` repository。
- **修改檔案**：
  - `docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `.venv/bin/pytest`
  - `.venv/bin/playwright install-deps --dry-run chromium`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `git init --initial-branch=main`
- **驗證結果**：
  - 標準 pytest=`11 passed, 1 known Starlette/httpx warning`。
  - Playwright=`All system dependencies are installed`。
  - Ruff=`20 files already formatted / All checks passed`；mypy=`20 source
    files / no issues`；Node syntax PASS。
  - Git 初始化未完成：Codex 受管工作區將空 `.git` 掛為唯讀 mount，
    `git init` 回報 `Read-only file system`，`rmdir` 回報
    `Device or resource busy`。
- **發現事項**：
  - Python、套件與 browser 測試環境均已修復；唯一剩餘環境門檻是受管
    workspace 外才能處理的 Git metadata mount。
- **下一步**：
  - 在一般 WSL shell（必要時先關閉目前 Codex workspace）移除空 `.git`
    後執行 `git init -b main`，再重新開啟專案。
  - 確認 `git status` 與 `git diff --check` 可執行後，才開始功能施工。

## 2026-07-20 13:58 UTC（2026-07-20 21:58 Asia/Taipei）— 新 Git repository 驗收

- **目的**：驗收使用者於一般 WSL shell 初始化的全新 Git repository，
  完成環境修復門檻。
- **執行內容**：
  - 確認目前目錄為有效 worktree，預設 branch 為 `main`。
  - 檢查全新 repository、未追蹤 source 與 Git ignore 規則。
  - 再次執行標準 pytest 與 Playwright 系統相依檢查。
- **修改檔案**：
  - `docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `git rev-parse --is-inside-work-tree`
  - `git branch --show-current`
  - `git status --short`
  - `git diff --check`
  - `git fsck --no-progress --full`
  - `git check-ignore -v`
  - `.venv/bin/playwright install-deps --dry-run chromium`
  - `.venv/bin/pytest -q`
- **驗證結果**：
  - Git worktree=`true`、branch=`main`；`git diff --check` 可執行。
  - `.env`、`.venv`、`.python` 與品質工具 cache 均正確忽略；
    `.env.example` 保持可追蹤。
  - Python=`3.12.10`、Playwright 系統相依完整、pytest=`11 passed,
    1 known Starlette/httpx warning`。
- **發現事項**：
  - Repository 尚無 commit，全部 source 仍是 untracked；因此目前
    `git diff --check` 雖可執行，還不能檢查未追蹤內容的 whitespace diff。
  - Git 已有 `user.email`，但尚未設定 `user.name`；本輪不代替使用者猜測
    commit identity，也不自行 stage 或 commit。
- **下一步**：
  - 設定 repository 的 `user.name`，建立第一個 baseline commit。
  - baseline 建立後再開始功能施工，確保後續修改可精確審查與回復。

## 2026-07-20 14:16 UTC（2026-07-20 22:16 Asia/Taipei）— Baseline commit 前置驗證

- **目的**：使用使用者指定的 Git identity `oscar0210` 建立第一個已驗證
  baseline commit，且不混入任何新功能。
- **執行內容**：
  - 盤點 34 個可追蹤檔案，確認沒有 symlink、未忽略的大型 cache 或本機
    環境目錄。
  - 檢查 `.env.example` 與可追蹤內容的常見秘密格式；沒有發現 credential、
    private key 或 token。
  - 重跑完整 pytest、Ruff、mypy 與 Node syntax。
  - 嘗試寫入 repository-local `user.name=oscar0210`。
- **修改檔案**：
  - `docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `git ls-files --others --exclude-standard`
  - `git check-ignore -v`
  - 常見秘密格式 `rg`
  - `.venv/bin/pytest -q`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `git config --local user.name oscar0210`
- **驗證結果**：
  - Secret scan PASS；`.env`、`.python`、`.venv` 與品質工具 cache 仍正確
    排除，`.env.example` 可安全追蹤。
  - pytest=`11 passed, 1 known Starlette/httpx warning`；Ruff、mypy 與
    Node syntax PASS。
  - Codex 受管工作區仍將 `.git` 掛為唯讀，local config 寫入回報
    `Read-only file system`；因此本程序沒有 stage 或假裝完成 commit。
- **發現事項**：
  - Source 已符合建立 baseline 的條件；只剩 Git metadata 寫入必須由一般
    WSL shell 執行。
- **下一步**：
  - 由使用者在一般 WSL shell 設定 `user.name`、stage、執行 staged
    whitespace check，並建立 `chore: establish initial MVP baseline`。
  - 重新開啟 workspace 後驗證 commit 與 clean worktree，再開始功能。

## 2026-07-20 16:04 UTC（2026-07-21 00:04 Asia/Taipei）— 合成與 4K workflow 文件整理

- **目的**：整理使用者提供的「人物×場景合成與 4K 放大」技術快照，
  移除不可移植的舊路徑，並精確盤點同資料夾附件與缺件。
- **執行內容**：
  - 完整讀取 483 行參考文件，盤點 `docs/workflows/` 的六份 workflow
    JSON 與 Windows `Zone.Identifier` 旁檔。
  - 實讀所有 JSON 的節點、模型、server input、seed、尺寸與輸出 prefix；
    區分現役主流程、選用輔助與範圍外工作流。
  - 把現有附件改成同資料夾 Markdown 相對連結；移除舊 monorepo、個人
    home 與 Windows 使用者絕對路徑。
  - 新增路徑規範、現役附件速查、server input、模型、節點版本與缺件
    分級清單；修正「附錄全數存在」的不實敘述。
  - 保留原有配方、逐字 prompt、seed、實測陷阱與歷史時間線，沒有修改
    workflow JSON。
- **修改檔案**：
  - `docs/workflows/REF_人物場景合成與4K放大.md`
  - `docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `find`／`file`／`wc`／`rg` 路徑與附件盤點。
  - Python `json` 解析、node reference 與 Markdown link 驗證。
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `git diff --check`
- **驗證結果**：
  - 六份 JSON 全部解析成功：wf02=26 nodes、wf03=5、wf10=17、
    dual B1=17、dual B2=17、w0=25；node reference 零缺漏。
  - 文件 19 個 Markdown 相對連結全部存在；沒有舊反斜線路徑、個人
    home、`/mnt/c/Users` 路徑或行尾空白。
  - Ruff=`20 files already formatted / All checks passed`；
    mypy=`20 source files / no issues`；Node syntax 與 tracked diff check
    PASS。
- **發現事項**：
  - 單人合成、雙人兩輪、選用去背與 4K 放大的現役 workflow JSON 已齊。
  - 至少缺 8 份歷史／備份 workflow，但都不影響現役主流程；wf02 的
    Lightning 4-step 分支缺 LoRA 與完整接線，不能視為可用快速版。
  - 真正阻擋未來一鍵安裝的是 ComfyUI/custom-node commit 與模型來源、
    revision、SHA-256、授權及安裝位置，不是主 workflow JSON。
  - `wf_w0_spike.json` 是單獨角色生成，明確屬本文範圍外；六個
    `Zone.Identifier` 旁檔不是工作流，本輪未擅自刪除。
- **下一步**：
  - 請使用者優先提供模型／custom-node 安裝資料、兩份 manifest 與各一組
    合成／4K 已驗證輸入輸出。
  - 確認是否保留 Lightning 快速版與完整歷史血統；不需要時可不補歷史
    JSON、舊產物與個人腳本。

## 2026-07-20 19:07 UTC（2026-07-21 03:07 Asia/Taipei）— 單角色分鏡候選與選定後 4K 接線

- **目的**：把「場景圖＋單一角色正面參考圖」合成分鏡候選，並把只有
  使用者明確選定的單一候選送入 4K，接進既有本機前端。
- **執行內容**：
  - 新增 loopback-only ComfyUI client、固定 workflow adapter、圖片安全
    正規化、strict HTTP schema、同源圖片 endpoint 與單一 GPU worker。
  - 合成採 `wf_dual_B1.json` 的單角色第一輪結構，避開 `wf02_insert`
    針對特定 plate 寫死的裁框；4K 採 `wf10_upscale_opt2.json`。
  - 每次只在 graph 深拷貝中注入 server filename、受控 prompt guard、
    server seed 與唯一 output prefix；沒有修改 workflow 正本。
  - 前端改為上傳兩圖、建立 1–3 張候選、輪詢、人工選片、確認後顯示 4K
    表單，以及同源預覽／下載；未選定時前後端都拒絕放大。
  - 圖片只接受 PNG／JPEG／WebP，限制檔案、尺寸與像素；解碼後重新輸出
    無 metadata RGB PNG，避免原始檔名、EXIF 或內嵌 graph 外洩。
  - 更新產品契約、任務文件、環境範例、README 與 workflow 參考文件，
    封存 Gateway V2 的舊「不接 ComfyUI」里程碑邊界。
- **修改檔案**：
  - `backend/app/gateway_main.py`
  - `backend/app/core/workflow_settings.py`
  - `backend/app/schemas/api/workflows.py`
  - `backend/app/api/routes/workflows.py`
  - `backend/app/services/workflows/`
  - `frontend/gateway/index.html`
  - `frontend/gateway/app.js`
  - `frontend/gateway/styles.css`
  - `tests/test_storyboard_workflows.py`
  - `tests/e2e/test_codex_gateway_page.py`
  - `pyproject.toml`、`uv.lock`、`.env.example`
  - `AGENTS.md`、`README.md`、`docs/PROJECT_SPEC.md`
  - `docs/tasks/CODEX_GATEWAY_V2.md`
  - `docs/tasks/STORYBOARD_WORKFLOW_INTEGRATION.md`
  - `docs/README_repro.md`
  - `docs/workflows/REF_人物場景合成與4K放大.md`
- **重要命令**：
  - `.venv/bin/pytest -q`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `uv lock --check`
  - `uv sync --locked --check`
  - `git diff --check`
  - Playwright desktop／390px workflow visual smoke。
- **驗證結果**：
  - pytest=`18 passed, 1 known Starlette/httpx deprecation warning`。
  - Ruff format/check、mypy（29 files）、Node syntax、uv lock/sync 與
    whitespace check 全部 PASS。
  - Fake Comfy API 完整驗證：上傳兩圖 → 候選 → 未選定 4K 回 409 →
    選定單一候選 → wf10 → 3840×2160 同源圖片與下載。
  - Browser E2E=`2 passed`；驗證三候選只送選定項目進 4K、503 恢復、
    Codex thread／turn 皆為零與 390px 無水平溢位。
  - 外部 ComfyUI 未啟動、未修改；loopback status smoke 正確回
    `unavailable`，Gateway 首頁與 catalog 仍可用。
- **發現事項**：
  - `wf02_insert` 已修正為 18 nodes，但固定裁框仍不適合任意上傳場景；
    產品用 B1 作單角色通用模板是刻意選擇。
  - `wf10` 的 node 4 placeholder 必須逐案注入；adapter 已加入角色、姿勢、
    場景與光線一致性 guard。
  - 任務與圖片目前保存在單一 Gateway process 記憶體，重啟後不保留；
    不屬於本輪的資料庫／資產持久化尚未實作。
  - 真實 GPU 像素重放未在 Codex sandbox 內執行；本輪使用 fake transport、
    API graph assertions、golden 視覺素材與離線 status 驗證接線。
- **下一步**：
  - 使用者啟動釘定的 ComfyUI 後，先以一張 16:9 場景與角色正面圖做
    opt-in 實機 smoke，確認節點／模型與實際顯存時間。
  - 實機通過後再決定是否加入持久化資產庫、工作歷史、取消 UI，以及
    雙角色 B1 選片 → B2 選片的第二階段人工 QA 流程。

## 2026-07-20 19:34 UTC（2026-07-21 03:34 Asia/Taipei）— 選定單圖 4K 與本機邊界硬化

- **目的**：依「不是所有分鏡都放大，只有確定要的才放大」完成最後選片
  契約，並補齊 loopback 無登入服務在長時間執行與瀏覽器操作下的安全、
  競態及復原邊界。
- **執行內容**：
  - 4K request 新增 server-issued `expected_candidate_id`；後端在同一把
    排程鎖內核對目前選片，拒絕舊分頁、偽造 ID、重複排程與排程後換片。
  - UI 支援確認 A 後改選並確認 B；只有最後確認的 B 會送入 4K。
    queued／running 與狀態不明時鎖定來源，完成後可開始下一個分鏡。
  - 輪詢遇暫時網路／5xx／409 時採 1、2、4、8、15 秒重試，不重送 POST；
    超過五次仍保留畫面與 run，提供手動重新查詢。
  - ASGI layer 拒絕非 loopback client 與跨站 mutation，加入同源 CORP；
    multipart 在 receive layer 設總 body hard cap。
  - queue、run 數與保留圖片 bytes 設上限；兩張來源上傳 ComfyUI 後立即
    釋放 Gateway 內 copy，滿載安全回 429。
  - Comfy client 精確核對 upload／prompt identity；由 Gateway 先核發
    prompt ID，shutdown 停止 worker 後再定向取消，避免孤兒 GPU 任務。
  - 兩份 workflow 以 SHA-256 fail closed，並新增 `.gitattributes` 固定
    B1 與 wf10 為 LF，避免 Windows CRLF 造成假性 hash 失敗，同時保留
    其他歷史 workflow 的原始換行。
  - Comfy readiness 從兩個節點擴為兩份 graph 的 21 個 distinct
    `class_type`；UI 明示非 16:9 來源會被 wf10 置中裁切。
- **修改檔案**：
  - `.gitattributes`、`.env.example`、`AGENTS.md`、`README.md`
  - `backend/app/gateway_main.py`
  - `backend/app/core/workflow_settings.py`
  - `backend/app/schemas/api/workflows.py`
  - `backend/app/api/routes/workflows.py`
  - `backend/app/services/workflows/`
  - `frontend/gateway/index.html`
  - `frontend/gateway/app.js`
  - `frontend/gateway/styles.css`
  - `tests/test_storyboard_workflows.py`
  - `tests/e2e/test_codex_gateway_page.py`
  - `docs/PROJECT_SPEC.md`
  - `docs/tasks/STORYBOARD_WORKFLOW_INTEGRATION.md`
- **重要命令**：
  - `.venv/bin/pytest -q`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend tests`
  - `node --check frontend/gateway/app.js`
  - `git diff --check`
  - `git check-attr text eol -- docs/workflows/wf_dual_B1.json
    docs/workflows/wf10_upscale_opt2.json`
  - `sha256sum docs/workflows/wf_dual_B1.json
    docs/workflows/wf10_upscale_opt2.json`
  - loopback Uvicorn 啟動、首頁／workflow status／跨站 POST smoke。
- **驗證結果**：
  - pytest=`25 passed, 1 known Starlette/httpx deprecation warning`；
    workflow unit／API=`13 passed`，Browser E2E=`2 passed`。
  - Ruff format/check、mypy（29 files）、Node syntax 與 whitespace check
    全部 PASS。
  - E2E 驗證 A → B 重新選片、只有 B 的 expected ID 進 4K、POST 409
    加一次 GET 503 後自動完成，create／upscale POST 皆只有一次。
  - workflow SHA-256 仍為
    `ceefd5844cab5f10368f8999d6362551b43edd92743ac36000fb365c6ae5c1c8`
    與
    `a141d9988a617680c282a1c3df5fb93e3d49e4b311ce36b448e6fbc3dd81756e`；
    Git attribute 對兩檔皆為 `text: set`、`eol: lf`。
  - ComfyUI 離線時 Gateway 啟動成功，首頁回 200、workflow status 安全回
    `unavailable`；外站 Origin mutation 回 403。
  - 最終 shell 沒有可執行的 `uv`，因此未重跑 `uv lock --check`／
    `uv sync --locked --check`；依賴與 lockfile 在前一輪整合驗證已通過，
    本次硬化未再修改依賴宣告。
- **發現事項**：
  - 唯讀核對現有 WSL ComfyUI：core=`ab0d8a92`、
    ComfyUI-GGUF=`cf05733`、Python 3.12.3、torch 2.12.0+cu130；
    八顆固定模型均能由既有 `extra_model_paths.yaml` 指向的位置找到。本輪
    未重新掃描 43GB SHA-256。
  - status 現在驗全部必要 node，但尚未解析 loader option 以驗證八個模型
    名稱；本機不受影響，其他電腦的安裝器／preflight 後續應補。
  - Gateway 不會擅自刪除共用 ComfyUI 的 input／output；長期磁碟清理與
    正式資產持久化仍屬後續資產層。
  - 真實模型載入、16GB VRAM offload、生成像素與黃金圖比對仍未執行。
- **下一步**：
  - 使用 README 的 custom-node whitelist 啟動釘定 ComfyUI，再以一張
    16:9 場景與角色正面圖做 opt-in GPU smoke：產生候選、選定一張、
    確認只建立一個 wf10 工作並下載 3840×2160 成品。
  - 安裝腳本階段加入八模型名稱／SHA-256 preflight，以及 owned ComfyUI
    與既有共用 ComfyUI 的明確採用策略。

## 2026-07-20 19:48 UTC（2026-07-21 03:48 Asia/Taipei）— GitHub 首次發布與持續版控

- **目的**：將目前通過驗證的 MVP 推送至指定 GitHub repository，並把
  後續交付一律提交與推送的要求寫入專案開發規範。
- **執行內容**：
  - 在 `AGENTS.md` 加入持續版本控制規則：使用 Conventional Commit、
    交付前執行品質檢查、推送 `origin/main`、禁止 force push／任意改寫
    歷史，且秘密、模型權重、虛擬環境與機器專屬絕對路徑不得提交。
  - 新增 `.gitattributes`，只固定 runtime adapter 實際使用的 B1 與
    wf10 為 LF；既有 manifests 與歷史 workflow 保留原始換行及
    SHA-256 身分。
  - 因本次 Codex sandbox 將來源 `.git` 掛載為唯讀，使用
    `git clone --no-hardlinks` 建立暫時可寫 clone，保留原始 root commit，
    再逐檔覆蓋並比對目前 70 個應發布檔案。
  - 設定遠端為
    `https://github.com/shi-tong-chang/final-project-mvp.git`，建立
    `39e23b8 feat: integrate storyboard and selected 4k workflows` 並成功
    首次推送至 `main`。
- **修改檔案**：
  - `AGENTS.md`
  - `.gitattributes`
  - `docs/tasks/PROJECT_LOG.md`
  - 其餘功能與素材檔案詳見 commit `39e23b8`。
- **重要命令**：
  - 完整 Python／frontend／Git 品質工具鏈。
  - 來源與暫時 clone 的逐檔 `cmp`。
  - 全部 docs JSON／JSONL parse。
  - staged secret／environment／model path scan。
  - 對程式碼與一般文件執行 `git diff --cached --check`；釘定原始位元組的
    CRLF 封存檔改以 JSON parse 與 SHA-256／逐位元比對驗證。
  - `git push -u origin main`。
- **驗證結果**：
  - 完整測試=`25 passed, 1 known Starlette/httpx deprecation warning`；
    workflow regression=`13 passed`。
  - Ruff format/check、mypy（29 files）、Node syntax 與來源
    `git diff --check` 全部 PASS。
  - 14 份 JSON 與 1 份 JSONL 全部 parse PASS；70 個發布檔案與來源逐位元
    相同。
  - staging 未包含秘密 `.env`、虛擬環境、模型權重或
    `Zone.Identifier`；公開設定範本 `.env.example` 刻意保留。
  - GitHub 回報新建 `main -> main`，feature commit 已到達指定遠端。
- **發現事項**：
  - 本次 sandbox 無法直接更新來源 `.git/config`、HEAD 與 index；遠端
    發布內容完整，但來源工作區的本機 Git metadata 需在可寫環境中與
    `origin/main` 對齊。
  - manifests 與部分歷史 workflow 使用 CRLF；直接套用 whitespace
    自動修正會改變已記錄 SHA-256，因此不可機械式正規化。
- **下一步**：
  - 後續每個可交付功能都依 `AGENTS.md` 先驗證、建立小而清楚的 commit，
    再推送 `origin/main`。
  - 在一般使用者 shell 將來源 repository 的本機 `main` 與遠端一次性
    對齊後，即可直接沿用標準 Git 工作流。

## 2026-07-20 21:33 UTC（2026-07-21 05:33 Asia/Taipei）— Clone-to-run managed ComfyUI runtime 發布

- **目的**：補齊任何新使用者 clone 後可由 Codex 執行「安裝環境」、
  「啟動」、「狀態」、「停止」的固定框架，包含釘版 ComfyUI、GGUF、
  兩套 Python、八顆模型、process ownership 與人類操作 README；角色與
  場景 agent 維持 non-blocking pending。
- **執行內容**：
  - 新增 stdlib runtime controller 與五個 typed CLI command，固定
    Gateway Python 3.12.10、ComfyUI Python 3.12.3、ComfyUI／GGUF commit、
    torch 2.12.0+cu130、loopback ports 與完整 dependency hash lock。
  - 預設採 managed ComfyUI code；模型 auto 只在單一候選 root 的八顆
    exact bytes 與完整 SHA-256 全數通過後唯讀 external reuse，否則以
    可續傳、hard-cap、隔離壞檔與 no-replace publish 的流程下載 managed
    copies。
  - Adopted code 嚴格驗證 commit、worktree、Python、packages、GGUF 與
    source YAML；不修改外部 ComfyUI。ComfyUI 的 base、cwd、input、
    output、temp、user、HOME、cache、logs 與 runtime custom nodes 均隔離
    至 `.runtime`。
  - Process state 加入 PID group、boot ID、start ticks、executable、
    argv digest 與 Gateway–ComfyUI link 身分；未知 port owner、stale PID、
    stop 次序、spawn rollback 與 state write 均 fail closed。
  - 修正最終稽核發現的三條分支：Gateway health timeout 不再出現未初始化
    變數、`--gateway-only` 永遠使用 disabled Comfy URL 並記錄
    `comfy_enabled=false`、只剩 owned ComfyUI 運行時 status 回
    `degraded` 而非 `stopped`。
  - README 改為人類優先教學，加入 Windows WSL2／GPU／Codex 從零準備、
    clone、四句操作、manual／PowerShell、managed／adopted／external
    模型策略、只放大選定候選、logs、搬移復原與常見錯誤。
  - 新增 clone-to-run 契約與驗收矩陣，清楚區分自動化證據與仍待 opt-in
    的 47.27 GB 真實下載、CUDA 啟動及 RTX 5070 Ti GPU 重放。
- **修改檔案**：
  - `runtime/`、`scripts/fpmvp_runtime.py`、`scripts/runtime.ps1`
  - `tests/test_runtime_*.py`
  - `.gitignore`、`AGENTS.md`、`README.md`
  - `docs/PROJECT_SPEC.md`、`docs/README_repro.md`
  - `docs/tasks/CLONE_TO_RUN.md`、`docs/tasks/PROJECT_LOG.md`
- **重要命令**：
  - `.venv/bin/pytest`
  - `.venv/bin/ruff format --check .`
  - `.venv/bin/ruff check .`
  - `.venv/bin/mypy backend runtime scripts tests`
  - `node --check frontend/gateway/app.js`
  - `uv lock --check`
  - 五個 CLI 的 `--json --dry-run` 與零 state write 驗證
  - JSON／JSONL parse、Markdown link、workflow SHA、staged
    secret／symlink／大檔／個人路徑與逐位元來源比對
  - `git push origin main`
- **驗證結果**：
  - pytest=`71 passed, 1 known Starlette TestClient/httpx2 deprecation
    warning`；runtime 專項獨立複核=`36 passed`。
  - Ruff format/check、mypy（41 files）、Node syntax、uv lock、Git
    whitespace check 全部 PASS。
  - 五個 JSON dry-run 全部可解析且 state dir 保持不存在；所有
    repository-relative Markdown links 可解析。
  - 8 個模型 lock 總大小=`47,266,047,406 bytes`；B1／wf10 workflow
    SHA-256 分別維持
    `ceefd5844cab5f10368f8999d6362551b43edd92743ac36000fb365c6ae5c1c8`
    與
    `a141d9988a617680c282a1c3df5fb93e3d49e4b311ce36b448e6fbc3dd81756e`。
  - Feature commit
    `44859912fc3915bb6e9657fd4f5058b5966636db` 已成功推送至指定
    GitHub `main`，沒有 force push。
- **發現事項**：
  - 本機受控候選搜尋可找到既有 canonical external model root；八顆
    檔案大小已符合，但本輪刻意沒有重新讀取 47.27 GB 計算完整 SHA，
    正式採用時仍由 install 全量驗證。
  - 既有 ComfyUI code 目前有 tracked 修改與 source
    `extra_model_paths.yaml`，因此不符合 adopted 唯讀契約；建議使用
    managed code 搭配 external models。
  - 目前環境沒有 PowerShell executable，因此 wrapper 只完成 source
    review，尚未在 Windows PowerShell 實際執行。
  - 真實模型下載、RTX 5070 Ti CUDA／VRAM、ComfyUI 完整生命週期與
    黃金圖重放仍未執行，文件及 UI 持續明示待實機驗證。
- **下一步**：
  - 在目標 WSL 主機輸入「安裝環境」，讓 install 完整 SHA 驗證並重用
    external models；接著執行 `preflight --full`。
  - 取得使用者 opt-in 後完成 B1 候選、確認未自動 4K、只選定一張送
    wf10 的 GPU smoke，記錄耗時、VRAM、版本與結果。
  - 待組員交付
    `.codex/agents/character_generator.toml` 與
    `.codex/agents/scene_generator.toml` 後，再依 strict agent 插槽契約
    接入角色四視圖與單張場景生成。
