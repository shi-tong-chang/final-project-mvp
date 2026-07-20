# AGENTS.md — final-project-mvp 開發規範

## 1. 專案範圍

本專案是 loopback-only 的故事視覺工作台。現階段提供 typed catalog、
二十種同角色風格展示與提示詞複製，以及由 FastAPI 受控執行的單角色
分鏡合成候選與「選定後才放大」4K 流程。

知識優先順序：

1. `docs/PROJECT_SPEC.md`
2. `docs/tasks/STORYBOARD_WORKFLOW_INTEGRATION.md`
3. `docs/tasks/CODEX_GATEWAY_V2.md`（已完成的展示里程碑）
4. 本文件

## 2. 不可破壞的邊界

1. FastAPI 只綁 `127.0.0.1`；沒有登入系統，禁止綁 `0.0.0.0`。
2. API key、登入憑證與秘密只進環境，不進 Git、日誌或 catalog。
3. 瀏覽器不得提供任意 cwd、sandbox、approval、model provider 或 shell
   command 輸入。
4. Codex app-server 固定 read-only sandbox 與 `approval_policy=never`；
   所有副作用核准要求 fail closed。
5. catalog 與 HTTP payload 必須使用 strict Pydantic schema，拒絕未知欄位。
6. 角色風格必須有唯一 `item_id` 與非空白 `prompt_fragment`。
7. 二十種 code-native preview 共用相同人物幾何；風格只能改變媒材、色盤、
   紋理與光線，不可冒充二十個不同角色。
8. 圖片生成只能經 typed FastAPI API；瀏覽器不得直接連 ComfyUI。未選定
   分鏡候選前，後端與 UI 都必須拒絕 4K 放大。
9. 正式預覽 URL 只允許安全的同源絕對路徑，不接受外站或本機檔案 URL。
10. workflow JSON 只可由 server 端固定 allowlist 載入；瀏覽器不得提交
    workflow、node ID、模型、seed、ComfyUI 路徑或 server filename。
11. ComfyUI 只允許 loopback HTTP，禁止 `/free`、全域清 queue、Manager
    自動安裝／更新，以及修改使用者既有 ComfyUI。
12. 本階段不引入資料庫或舊 Storyboard 引擎依賴；生成資產與任務狀態為
    單一 Gateway process 管理的暫存資料。
13. 所有 mutation 必須拒絕跨站瀏覽器請求；圖片 request body、GPU queue、
    run 數與記憶體圖片總量必須有 hard cap。
14. 4K request 必須帶 server-issued 的預期候選 ID；後端需在排程鎖內再次
    核對目前選片，禁止因多分頁競態放大另一張候選。

## 3. 程式碼與測試

- UI 與文件使用繁體中文；程式碼識別符使用英文。
- Python 3.12.10；Ruff、mypy、pytest 為唯一品質工具鏈。
- 對外輸入使用 Pydantic；新函式提供 type hints，公開介面提供 docstring。
- async 路徑不得使用阻塞 I/O；背景 task 必須有明確生命週期與回收責任。
- 前端不使用 `innerHTML`、外部 CDN、遠端字型或未授權圖片。
- 修改功能時同步更新 unit／browser regression。
- 完成修改至少執行：

```bash
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend tests
node --check frontend/gateway/app.js
git diff --check
```

## 4. 執行紀錄

具實質影響的動作追加到 `docs/tasks/PROJECT_LOG.md`，不得重寫過去紀錄。
每則包含目的、執行內容、修改檔案、重要命令、驗證結果、發現事項與下一步。
不得在紀錄中寫入秘密。

## 5. 版本控制

- 所有可交付的實質修改都必須納入 Git；開始前檢查 working tree，保留
  使用者既有變更，完成後先驗證再 commit。
- 預設使用 `main`，遠端為
  `https://github.com/shi-tong-chang/final-project-mvp.git`。每次完成一組
  可交付修改後，以清楚的 Conventional Commit 訊息提交並 push
  `origin/main`；若權限或網路阻擋，必須如實回報。
- 禁止未經明確要求使用 force push、重寫已發布歷史或丟棄使用者變更。
- commit 前至少執行適用的品質檢查、`git diff --check`，並確認沒有秘密、
  本機虛擬環境、模型本體或個人絕對路徑被納入。
