# Storyboard Gateway V2 本機工作室任務單

> 狀態：角色風格櫥窗與自動驗證完成；正式風格圖片待使用者提供。
> 本文件記錄已完成的展示里程碑；其中「不接 ComfyUI／不生成圖片」的
> 邊界只適用於該里程碑。2026-07-21 起的單角色分鏡合成與選定後 4K
> 接線，以 `STORYBOARD_WORKFLOW_INTEGRATION.md` 與 `PROJECT_SPEC.md`
> 為準。
> 2026-07-21 後續前端已把「複製完整提示詞」改為角色／場景「確認生成」
> 接點，並加入空的歷史軌；本文件其餘複製流程文字保留為展示里程碑
> 紀錄，不再是目前 UI 契約。
> 同日後續里程碑已把歷史軌接成本機持久化圖庫，並加入一或兩位角色的
> B1／B1→B2 自動路由；現行契約見
> `ASSET_LIBRARY_AND_DUAL_STORYBOARD.md`。
> 使用者於 2026-07-20 明確啟動此封存後獨立工具里程碑。
> 本任務不是 Phase 2 的 LLM Planner、不是 AIPE Worker Adapter，也不恢復
> 已封存的 ComfyUI 產品施工。
> 使用者其後明確取消右側 Codex 對話框；本頁不建立 thread 或 turn。

## 1. 目標

建立一個只綁本機的繁體中文 Web 工作室，讓使用者可以：

1. 在「生成角色／生成場景／生成分鏡」三個工作區間切換。
2. 在角色工作區瀏覽二十種風格；每張卡以同一個角色構圖展示不同媒材。
3. 每種風格與一段 typed prompt fragment 一對一綁定，選取後可連同角色
   描述與一致性規則複製成完整提示詞。
4. 讓後續提供的風格圖、角色、場景與分鏡資料可經 typed catalog provider
   接入，不必重寫頁面或 Codex transport。

## 2. 架構邊界

```text
瀏覽器 @ 127.0.0.1:8010
  ↓ 同源 JSON API
獨立 FastAPI gateway_main
  └── typed mock catalog provider
```

- 本頁只呼叫 catalog；不顯示對話框、不讀 Codex status，也不建立
  thread／turn。
- 已完成的 thread／turn application service 與 Codex app-server adapter
  暫時保留為後續自動規劃接點，但不是本輪前端功能。
- Gateway 不接資料庫、ComfyUI、Workflow Compiler、Planner 或既有
  `backend/app/main.py` runtime。
- Gateway 是獨立入口；既有 Storyboard MVP UI 與 API 保持不變。
- 本輪 catalog 是展示資料，不冒充使用者尚未提供的正式風格或生成資產。

## 3. 保留的 Codex transport 契約

- 採目前安裝之 Codex CLI 所提供的 stable app-server method subset：
  `initialize`、`initialized`、`thread/start`、`turn/start`，以及 turn
  逾時時只用於中止既有請求的 `turn/interrupt`。
- transport 固定為本機 child process 的 JSONL stdio，不開未驗證的
  WebSocket listener。
- thread 的工作目錄固定在本 repo，sandbox 預設 `read-only`，
  approval policy 預設 `never`；Web UI 本輪不提供執行命令或寫檔核准。
- model 未由設定提供時沿用使用者 Codex 設定，不在程式碼猜 model ID。
- child process、stdout reader、stderr drainer 與 pending turn 都必須由
  lifespan／client 明確擁有並在關閉時回收。
- Codex server 主動提出的副作用核准要求一律 fail closed；不得因 Web UI
  沒有 approval surface 而靜默核准。
- thread ID 視為 opaque；API 只接受本次 gateway process 核發的 ID。
- 本輪瀏覽器 E2E 必須證明沒有建立 thread 或 turn。

## 4. HTTP API

所有 endpoint 位於 `/api/v1/gateway`：

| Method | Path | 用途 |
|---|---|---|
| GET | `/status` | 保留的 Gateway／Codex binary 可用狀態 |
| GET | `/catalog` | 三工作區及 mock 展示櫥窗 |
| POST | `/threads` | 保留的 Codex thread 接點；目前 UI 不呼叫 |
| POST | `/threads/{thread_id}/turns` | 保留的 turn 接點；目前 UI 不呼叫 |

外部輸入一律由 strict Pydantic schema 驗證。工作區只允許
`character`、`scene`、`storyboard`；context 只接受明確欄位，例如目前
選取項目、prompt 草稿與 reference IDs，不傳遞結構不明的巢狀 payload。

## 5. 前端契約

- 三個 tabs 必須支援滑鼠與鍵盤。
- 角色頁提供二十張同角色、不同媒材的風格卡；所有 preview 使用相同的
  角色幾何，避免把髮型、服裝或配色差異誤認為風格差異。
- 角色風格 DTO 必須包含非空白 `prompt_fragment`；選取卡片後同步更新
  大型預覽、風格說明、標籤及提示詞。
- 「複製完整提示詞」必須組合角色描述、固定角色一致性規則與所選風格
  片段；目前不得冒充已送出圖片生成。
- 場景頁提供場景方向／描述與待補素材櫥窗。
- 分鏡頁提供鏡頭方向／描述與待補素材櫥窗。
- 桌面與手機版均不得顯示 Codex 對話、狀態或開啟聊天按鈕。
- 不使用外部 CDN、遠端字型或未授權圖片；placeholder 由 HTML／CSS
  原生繪製。
- UI 不宣稱 mock 卡已能送 ComfyUI 生成。

## 6. 後續資料接線

正式資料到位後只替換 catalog provider 與資產 URL：

- 風格：`id`、繁中名稱、描述、prompt fragment、preview asset、狀態。
- 角色／場景／分鏡展示：`id`、名稱、描述、preview asset、tags、狀態。
- URL 必須由本機受控 endpoint 或經明確核准的來源提供；不得讓任意
  client path 直接映射本機檔案。
- 未定欄位先擴充 typed schema 與 regression tests，再接資料；不得用
  opaque `dict` 偷渡。

## 7. 安全與非目標

- 啟動命令必須綁 `127.0.0.1`，建議 port `8010`，避免與既有 FastAPI
  `8000` 衝突。
- 不新增、複製或記錄 API key；沿用本機 Codex CLI 已有的安全登入狀態。
- 不提供任意 cwd、sandbox、approval、model provider、shell command、
  node ID 或 workflow graph 的瀏覽器輸入面。
- 不實作 ComfyUI 生成、正式素材上傳、聊天 UI、多人登入或遠端公開。
- 不修改任何 `workflows/**/*.json`，也不呼叫 ComfyUI `/free`。

## 8. Definition of Done

- 無 ComfyUI 時仍可啟動 Gateway、載入頁面及讀取 mock catalog。
- Fake Codex 測試覆蓋 thread／turn happy path、未知 thread、strict schema、
  process／protocol failure 與秘密不洩漏。
- catalog 提供二十個唯一角色風格，且每個風格都有非空白提示詞片段。
- 三個 tabs、二十張同角色風格卡、選取狀態、完整提示詞複製與
  desktop／mobile 兩欄櫥窗有 browser regression coverage。
- Browser E2E 證明頁面沒有對話 DOM，且操作期間 thread／turn 呼叫數為零。
- focused 與完整 pytest、Ruff format/check、mypy、靜態安全掃描及
  `git diff --check` 全部通過；任何環境限制如實寫入獨立 LOG。

## 9. 本機啟動

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.gateway_main:app \
  --host 127.0.0.1 --port 8010
```

瀏覽器開啟 `http://127.0.0.1:8010`。角色風格櫥窗不需要 Codex 登入或
ComfyUI，即可選擇風格並複製提示詞。
