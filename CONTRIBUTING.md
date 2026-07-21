# 組員開發與版本控制

本專案的產品 runtime 與一般程式開發是兩條不同路徑。修改 API、UI、文件或
測試時，不需要先下載 ComfyUI、CUDA wheel 或 47.27 GB 模型；只有要驗證
真實 GPU workflow 時才執行 README 的「安裝環境」。

## 1. Clone 後建立自己的分支

請勿直接在 `main` 修改或 push。先從最新遠端 `main` 建立短期分支：

```bash
git clone https://github.com/shi-tong-chang/final-project-mvp.git
cd final-project-mvp
git fetch --prune origin
git switch -c feat/short-description origin/main
```

分支名稱使用小寫英文與連字號，前綴依內容選擇：`feat/`、`fix/`、`docs/`、
`test/` 或 `chore/`。同一分支只處理一個可審查主題。

組員必須先取得 repository collaborator 權限才能 push 分支；沒有權限時，
請 fork 後從自己的 remote 開 Pull Request。

## 2. 建立輕量開發環境

Ubuntu 24.04／WSL2 內先準備 `git`、`python3` 與能執行 `node --check` 的
Node.js；輕量 bootstrap 只管理 Python，不會修改系統 Node.js。接著從
repository 根目錄執行：

```bash
python3 scripts/setup_dev.py
```

這個入口只會使用 `runtime/runtime-lock.json` 釘定且驗證 SHA-256 的 uv，
準備 Gateway Python 3.12.10，並依 `uv.lock` 建立 `.venv/` 的開發依賴。
它不建立 `.runtime/`，也不安裝 ComfyUI、不掃描或下載模型、不改
`.local-data/`。`.python/`、`.venv/` 與 cache 都不進 Git。

若只要先查看固定動作而不下載或寫檔：

```bash
python3 scripts/setup_dev.py --dry-run
```

## 3. 驗證

不需要 Browser 的快速迴圈：

```bash
.venv/bin/pytest -m "not browser"
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend runtime scripts tests
node --check frontend/gateway/app.js
git diff HEAD --check
```

完整 pytest 包含 Playwright Chromium E2E。第一次執行前安裝瀏覽器與 Ubuntu
系統相依；`install-deps` 會使用 sudo 修改系統套件：

```bash
sudo .venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
.venv/bin/pytest
```

Pull Request 的 `quality` 與 `browser-e2e` 兩個 CI check 都必須通過。真實
ComfyUI／GPU 重放是另外的 opt-in 驗收，不在一般 CI 下載模型或執行。

## 4. 修改與本機資料安全

- 先讀 `AGENTS.md` 與其列出的知識順序；UI／文件使用繁體中文。
- API、workflow、runtime 或 port 行為不得越過 loopback 與 typed schema
  邊界。
- `.runtime/`、`.venv/` 與 `.local-data/` 在同一 worktree 的不同分支間
  共用。切換會影響 runtime/workflow 的分支前先輸入「停止」。
- `.local-data/` 是不可重建的正式素材；不要執行會刪除 ignored files 的
  `git clean -fdX`，除非已在 repository 外完整備份。
- 不提交 `.env`、憑證、模型、logs、cache、生成圖片或個人絕對路徑。
- `docs/tasks/PROJECT_LOG.md` 每個 Pull Request 只追加一筆，並在同步最新
  `origin/main` 後才寫，降低多人同時追加造成的衝突；不得改寫舊紀錄。

## 5. Commit、push 與 Pull Request

提交前只 stage 本次範圍，檢查 staged 內容與 whitespace；下列
`path/to/changed-file` 請換成實際檔案：

```bash
git status --short
git add path/to/changed-file
git diff --cached --stat
git diff --cached --check
git commit -m "feat: describe the change"
git push -u origin HEAD
```

Commit 使用 Conventional Commits，例如 `feat:`、`fix:`、`docs:`、`test:`、
`refactor:` 或 `chore:`。Push 後開 Pull Request，填完 template、確認沒有
秘密與本機產物，等待 CI 與至少一位組員 review；對話與衝突全部處理後才
合併。禁止 force push 或刪除 `main`。共享分支若要 rebase，必須先與其他
作者協調，不能改寫已被他人依賴的歷史。
