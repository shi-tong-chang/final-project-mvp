# AGENTS.md — final-project-mvp 總控規範

## 1. 專案與知識順序

本專案是 loopback-only 的故事視覺工作台。現階段提供 typed catalog、
二十種同角色風格展示、本機角色／場景素材庫，以及由 FastAPI 受控執行的
單角色或雙角色分鏡候選與「使用者選定後才放大」4K 流程。

知識優先順序：

1. `docs/PROJECT_SPEC.md`
2. `docs/tasks/CLONE_TO_RUN.md`
3. `docs/tasks/STORYBOARD_WORKFLOW_INTEGRATION.md`
4. `docs/tasks/ASSET_LIBRARY_AND_DUAL_STORYBOARD.md`
5. `docs/README_repro.md`
6. `docs/tasks/CODEX_GATEWAY_V2.md`（既有展示里程碑）
7. 本文件

Runtime machine-readable authority 是 `runtime/runtime-lock.json` 與
`runtime/models.lock.json`。文件不得用未釘版的 branch、`latest` URL、
來源機絕對路徑或人工記憶覆蓋 lock。

## 2. 四句使用者命令的固定路由

以下文字是使用者在 **Codex 對話** 中對 repository 下達的維運命令。
Codex 必須從 repository 根目錄使用固定 argv，不得把文字拼成 shell
command，也不得另寫臨時安裝／啟動腳本。

| 使用者輸入 | 固定 CLI |
|---|---|
| `安裝環境` | `python3 scripts/fpmvp_runtime.py --json install`；成功後執行 `python3 scripts/fpmvp_runtime.py --json preflight` |
| `啟動` | `python3 scripts/fpmvp_runtime.py --json start`；完成後執行 `python3 scripts/fpmvp_runtime.py --json status` 對帳 |
| `狀態` | `python3 scripts/fpmvp_runtime.py --json status` |
| `停止` | `python3 scripts/fpmvp_runtime.py --json stop` |

行為規則：

- `--json` 是 global option，必須放在 subcommand 前。
- `安裝環境` 的預設是 managed ComfyUI code 與 auto models；不得暗中改成
  adopted。
- 只有使用者明確指定既有 root 時，才可加入
  `--comfy-mode adopted --comfyui-root <path>`。只有使用者明確指定模型
  ownership 時，才可加入 `--models-mode external|managed` 與
  `--model-root <path>`。
- `啟動` 不隱式安裝；尚未安裝時如實回報並指引使用者先輸入「安裝環境」。
- `狀態` 與 `停止` 不下載、不 checkout、不建立新服務。
- Codex 以 CLI JSON 的 `ok`、`overall`、`checks`、`warnings`、`urls` 回報，
  不用猜測 PID 或只看命令是否有輸出。
- Windows PowerShell 的人工入口是 `scripts/runtime.ps1`；repository 已在
  WSL Codex session 中時，仍以 Python entrypoint 為 canonical。

### 禁止 Browser 變成總控入口

Browser HTTP、WebSocket、表單文字與 prompt **不得**接到上述 CLI、shell
或 Codex thread／turn。不得新增 `/install`、`/start`、`/stop` 類 HTTP
route 來執行本機 process，也不得把網頁上的自然語言當成 Codex 指令。

Browser 只能透過 strict typed FastAPI API 操作 server-owned catalog、
上傳、固定 workflow、候選選片與 4K 任務。Runtime 維運與圖片工作流是
兩條不同的信任邊界。

## 3. Runtime 所有權

- Gateway 固定 Python 3.12.10，使用 repository 的 Git-ignored `.venv/`。
- ComfyUI 固定 Python 3.12.3 與自己的 venv；兩個 process 不共用
  site-packages。
- `--comfy-mode auto` 目前解析為 managed：ComfyUI code、GGUF node、
  venv、logs 與工作資料只可建立在 Git-ignored `.runtime/`。
- `--models-mode auto` 先完整 SHA-256 驗證受控候選 model root；八顆全部
  符合才唯讀採用 external，否則下載到 `.runtime/models/`。
- adopted 必須由使用者明確指定，只做 pin／Python／package 稽核；
  ComfyUI core 的受 Git 追蹤檔必須無修改，其他 untracked custom nodes
  可存在但啟動時全數停用，ComfyUI-GGUF 則維持嚴格 worktree 檢查。禁止
  checkout、pip、安裝 node、補模型、改 YAML 或更新使用者既有 ComfyUI。
  Source `extra_model_paths.yaml` 會使 adopted fail closed；只想重用模型
  時應保留 managed code 並明確指定 external model root。
- managed 不得修改 `$HOME/ai/ComfyUI`、`$HOME/ComfyUI` 或其他外部
  ComfyUI。外部模型也不得被覆寫、改名或補下載。
- `preflight` 預設是 quick，核對模型 exact bytes 與 install SHA
  receipt；只有 `preflight --full` 才重算全部 47.27 GB SHA-256。
- Gateway 與 ComfyUI 固定 `127.0.0.1:8010`／`127.0.0.1:8188`。未知
  process 佔用 port 時 fail closed，不可自動 kill。

## 4. Agent 插槽契約

後續角色與場景 agent 只從以下固定位置接入：

- `.codex/agents/character_generator.toml`
- `.codex/agents/scene_generator.toml`

每個 TOML 至少要有三個非空白 top-level 欄位：

```toml
name = "..."
description = "..."
developer_instructions = """
...
"""
```

接線規則：

- 缺檔時狀態維持 `pending`、`blocks_start=false`；不建立內容空泛的假
  agent，也不把 placeholder 當成功結果。
- TOML 到位後先 strict parse、拒絕未知或缺少必備欄位，才可啟用對應的
  角色／場景生成能力。
- Agent prompt、工具 allowlist、輸入與輸出必須由 server 端固定；Browser
  不得提供 agent path、developer instructions、cwd、sandbox、approval、
  provider 或 shell command。
- 單一 agent 缺失或設定錯誤，只能讓其專屬功能保持 pending／unavailable；
  不得阻擋首頁、catalog、使用者自行上傳素材的現有分鏡，以及選定候選後
  的 4K。
- 目前既有分鏡與 4K 直接由 typed FastAPI／ComfyUI adapter 執行，不建立
  Codex thread／turn。
- Agent 產生正式角色或場景後，必須透過 repository 提供的 trusted
  registration CLI 寫入 `.local-data/asset-library/`；不得自行拼接公開 URL、
  直接改 metadata，或把生成資產提交進 Git。角色資產需有前／左／右／後
  四視圖，場景資產需有一張定稿圖。

## 5. 不可破壞的產品與安全邊界

1. FastAPI 只綁 `127.0.0.1`；沒有登入系統，禁止綁 `0.0.0.0`。
2. API key、登入憑證與秘密只進環境，不進 Git、日誌、status 或 catalog。
3. Codex app-server adapter 固定 read-only sandbox、
   `approval_policy=never`、repo-scoped cwd；所有副作用核准要求 fail
   closed。
4. Catalog 與 HTTP payload 使用 strict Pydantic schema，拒絕未知欄位。
5. 角色風格有唯一 `item_id` 與非空白 `prompt_fragment`。
6. 二十種 code-native preview 共用相同人物幾何；風格只能改變媒材、色盤、
   紋理與光線，不可冒充二十個不同角色。
7. 圖片生成只能經 typed FastAPI API；Browser 不得直接連 ComfyUI。
8. 未選定分鏡候選前，後端與 UI 都必須拒絕 4K；4K request 必須帶
   server-issued expected candidate ID，並在排程鎖內重新核對目前選片。
9. 正式預覽 URL 只允許安全的同源絕對路徑，不接受外站或本機檔案 URL。
10. Workflow JSON 只由 server 固定 allowlist 載入；Browser 不得提交
    workflow、node ID、模型、seed、ComfyUI 路徑或 server filename。
    Browser 只提交 1–2 個角色 asset ID 與一個場景 asset ID；server 必須
    依角色數量固定選擇 B1 或 B1→B2，不接受 client 覆寫路由。
11. ComfyUI 只允許 loopback HTTP；禁止 `/free`、全域清 queue、Manager
    自動安裝／更新，以及修改 adopted ComfyUI。
12. 所有 mutation 拒絕跨站 Browser request；圖片 body、GPU queue、run
    數與記憶體圖片總量都有 hard cap。
13. 本階段不引入資料庫或舊 Storyboard 引擎；角色／場景資產持久化在
    Git-ignored `.local-data/asset-library/`，分鏡 run／candidate 狀態仍只由
    單一 Gateway process 暫存，重啟後不保留工作進度。
14. 不得聲稱已在目標 RTX 5070 Ti 完成端到端重放，除非有本次實跑記錄、
    版本摘要與結果證據。來源機黃金圖不等於新機驗證。

## 6. 程式碼與測試

- UI 與文件使用繁體中文；程式碼識別符使用英文。
- Gateway 目標 Python 3.12.10；ComfyUI runtime 的 3.12.3 是刻意隔離的
  例外。
- Ruff、mypy、pytest 為 Python 品質工具鏈。
- 對外輸入使用 Pydantic；新函式提供 type hints，公開介面提供 docstring。
- async 路徑不得使用阻塞 I/O；背景 task 有明確生命週期與回收責任。
- 前端不使用 `innerHTML`、外部 CDN、遠端字型或未授權圖片。
- Runtime subprocess 一律使用固定 argv 與 `shell=False`；下載只接受
  lock 內 HTTPS URL，完整 SHA 通過後才發布。
- 修改功能時同步更新 unit／browser regression。

完成修改至少執行：

```bash
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend runtime scripts tests
node --check frontend/gateway/app.js
git diff --check
```

## 7. 執行紀錄與版本控制

- 具實質影響的動作追加到 `docs/tasks/PROJECT_LOG.md`，不得重寫歷史；每則
  包含目的、執行內容、修改檔案、重要命令、驗證結果、發現與下一步，不
  寫入秘密。
- 所有可交付修改納入 Git；開始前檢查 working tree，保留使用者既有變更。
- 預設 branch 是 `main`，遠端是
  `https://github.com/shi-tong-chang/final-project-mvp.git`。
- 完成一組可交付修改後，依使用者授權以清楚的 Conventional Commit 訊息
  commit／push；權限、網路或本回合範圍不允許時如實回報。
- 禁止未經明確要求 force push、重寫已發布歷史或丟棄使用者變更。
- Commit 前確認沒有秘密、`.venv`、`.runtime`、模型、logs、生成暫存或
  個人絕對路徑被納入。
