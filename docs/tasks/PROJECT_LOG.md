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
