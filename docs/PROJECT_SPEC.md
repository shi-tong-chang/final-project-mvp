# Final Project MVP 產品契約

## 1. 產品目標

建立一個只在本機運作的繁體中文故事視覺工作台。使用者可在角色、場景及
分鏡三個分類間切換；角色頁以同一個角色展示二十種畫風，角色與場景頁
都可確認一筆待送往對應 Agent 的生成設定，並顯示已登錄的本機生成資產。
分鏡頁可從圖庫選擇一個或兩個角色與一個場景，後端依角色數量自動選擇
固定的單角色或雙角色工作流；使用者選定其中一張候選後，才可送入固定的
4K 放大流程。

## 2. 本階段功能

- 角色描述輸入，長度上限 1200 字元。
- 二十個 typed 角色風格項目。
- 同角色、不同媒材的 code-native placeholder。
- 選取風格後更新大型預覽、說明、標籤與提示詞。
- 角色與場景頁提供「確認生成」CTA；目前只驗證並確認頁面設定，Agent
  未接入前不送出 HTTP、Codex thread／turn 或圖片生成工作流。
- 角色頁提供本機角色圖庫；每筆角色模板包含前、左、右、後四視圖。
- 場景頁提供本機場景圖庫；每筆場景模板包含一張定稿圖。
- 圖庫資產以 opaque ID 與安全同源 URL 提供，持久化在 Git-ignored
  `.local-data/asset-library/`；空狀態不得冒充正式生成結果，且不得先行
  建立或污染 `.runtime/` ownership state。
- 場景與分鏡版面預覽。
- Desktop／mobile responsive 與鍵盤 tabs。
- 從圖庫選擇一個或兩個角色與一個場景；圖庫尚空時保留一張場景加一張
  角色正面參考的手動上傳 fallback。
- 一個角色由 server 固定使用 `wf_dual_B1`；兩個角色由 server 依選擇
  順序執行 `wf_dual_B1` → `wf_dual_B2`，Browser 不能指定工作流。
- 使用 prompt guard 保留角色模板身份與場景結構，產生 1–3 張最終候選。
- 顯示每張候選的任務狀態與 seed，並由使用者明確選定一張。
- 只有已完成且已選定的候選可送入 `wf10_upscale_opt2`。
- 4K 工作流固定輸出 3840×2160，並提供同源預覽與下載。
- ComfyUI 未啟動時，角色風格櫥窗與其他靜態功能仍可使用。

## 3. 不在本階段

- 角色／場景 Agent 的正式生成與任務狀態（Agent TOML 尚待組員交付）。
- Codex 對話 UI。
- 資料庫、使用者登入或遠端公開。
- 單獨生成角色或場景。
- 雙角色中途 B1 人工選片；本階段每張最終候選自動完成自己的 B1→B2
  兩輪，再由使用者選最終候選。
- 任意畫幅的 4K 輸出、任意 workflow 上傳或 node 級控制。

## 4. Clone-to-run 與 runtime 契約

- 第一個支援目標是 Windows 11、WSL2 Ubuntu 24.04 x86_64 與 NVIDIA
  RTX 5070 Ti 16 GB；安裝需要網路，建議至少 65 GiB 可用空間。
- `安裝環境`、`啟動`、`狀態`、`停止` 只由 repository 外層的 Codex
  對話映射到 `scripts/fpmvp_runtime.py` 固定 CLI。Browser HTTP 不得接成
  shell、runtime CLI 或 Codex turn。
- Gateway 固定使用 Python 3.12.10；ComfyUI 固定使用獨立 Python
  3.12.3／torch 2.12.0+cu130，兩者不共用 site-packages。
- 預設 `--comfy-mode auto` 解析為 Git-ignored `.runtime/` 內的 managed
  ComfyUI code；不自動採用或修改使用者既有 ComfyUI。
- 預設 `--models-mode auto` 只在八顆模型完整 SHA-256 全數符合
  [`runtime/models.lock.json`](../runtime/models.lock.json) 時唯讀採用
  external root，否則續傳下載 managed copies。總模型大小約 47.27 GB。
- 既有 ComfyUI 只有在使用者明確選擇 adopted，且 core／GGUF commit、
  core 受 Git 追蹤檔無修改、GGUF worktree 嚴格通過、Python 與 package
  pins 符合、source `extra_model_paths.yaml` 不存在時才可採用；其他
  untracked custom nodes 可存在但不載入。Adopted 資產不得被 installer
  修改；ComfyUI base、工作資料、HOME、cache 與 logs 全導向 `.runtime/`。
- `preflight` 預設 quick，核對模型 exact bytes 與 install SHA receipt；
  `preflight --full` 才重算八顆 SHA-256。
- 所有 listener 固定 loopback，runtime 只停止 PID identity 可證明由本
  repository 擁有的 process。

完整命令、ownership 與失敗復原見
[`CLONE_TO_RUN.md`](./tasks/CLONE_TO_RUN.md)。目前尚未完成目標 RTX
5070 Ti 的端到端 GPU 重放，不得把 lock／單元測試或來源機黃金圖描述成
新機實測成功。

### 4.1 Agent 插槽

角色與場景 agent 預留於
`.codex/agents/character_generator.toml`、
`.codex/agents/scene_generator.toml`。兩者到位前維持
`pending`、`blocks_start=false`；不阻擋現有首頁、catalog、使用者自行
上傳素材的單角色分鏡、已登錄素材的單／雙角色分鏡，以及選定候選後的
4K。每份 TOML 接線時至少嚴格驗證非空白 `name`、`description`、
`developer_instructions`。

## 5. Catalog 契約

Schema version：`storyboard-studio.catalog.v2`。

角色風格最少包含：

- `item_id`
- `title`
- `description`
- `status`
- `preview_kind`
- `preview_url`
- `tags`
- `prompt_fragment`

`preview_url` 為選填；存在時必須是安全同源路徑。正式圖片到位前，
`preview_kind` 必須對應前端 allowlist 中的 code-native preview class。

## 6. 安全

- Server 只綁 `127.0.0.1`。
- CSP：資源與連線同源，禁止被 iframe 嵌入。
- 不在前端或日誌暴露 Codex auth、API key 或環境內容。
- 保留的 Codex adapter 固定 read-only、never approve、repo-scoped cwd。
- Browser 不直接連 child process。
- Browser 不得透過 HTTP、WebSocket 或自然語言欄位觸發 runtime CLI、
  shell 或 Codex thread／turn。
- Browser 不直接連 ComfyUI，也不能提供 workflow、node ID、模型、seed、
  ComfyUI 路徑或 server filename。
- FastAPI 只連 loopback ComfyUI；任務使用 server-generated opaque ID、
  input filename 與 output prefix。
- Mutation 拒絕跨站 `Origin`／`Sec-Fetch-Site`；即使誤綁公開介面，
  ASGI layer 仍拒絕非 loopback client。
- 圖片 multipart 在 ASGI receive layer 設總 body hard cap；GPU queue、
  run 數與記憶體圖片總量皆有上限。
- 4K endpoint 必須在 server 排程鎖內再次驗證來源候選已完成、已選定，
  且仍等於 Browser 回傳的 server-issued 預期候選 ID。
- 固定 workflow 以 SHA-256 fail closed，Git checkout 強制保留 LF，
  避免 graph 漂移或 Windows CRLF 改變固定資產。
- Managed ComfyUI、models、logs 與生成工作資料只寫入 Git-ignored
  `.runtime/`；素材庫獨立寫入 Git-ignored `.local-data/`，adopted ComfyUI
  保持唯讀。

## 7. 驗收

- Live catalog 精確提供二十個唯一角色風格及非空白提示詞片段。
- Browser 無對話 panel／chat button，操作不建立 thread 或 turn。
- 二十張卡使用相同角色 DOM layers。
- 角色與場景頁都有可持久化的本機圖庫；沒有正式生成資產時顯示明確空
  狀態，登錄後分別顯示四視圖角色與單圖場景。
- 「確認生成」通過表單驗證後明示 Agent 尚待接入，不冒充已建立圖片，
  也不建立 thread、turn 或其他 mutation request。
- 390px 手機雙欄且無水平溢位。
- 分鏡頁只能選 1–2 位角色及恰好 1 個場景；server 回報並執行實際路由，
  單角色為 B1、雙角色為 B1→B2。分鏡可顯示 1–3 張最終候選；未選定前
  4K 操作維持鎖定。
- 重新選定候選時，4K 只使用目前被 server 確認的單一候選。
- 4K queued／running 時來源素材與選片保持鎖定；完成或失敗後可開始新
  工作。短暫輪詢失敗不得重複排程，應重試並與既有 run 對帳。
- ComfyUI 不可用或任務失敗時顯示安全錯誤，不洩漏本機路徑或 raw payload。
- 成功放大後提供 3840×2160 的同源預覽與下載。
- 預設 install 使用 managed ComfyUI code；完全不相符的既有 ComfyUI
  不被探測、修改或自動 adopted。
- External model root 只有在八顆完整 SHA-256 全部符合時採用；中斷下載
  可由 managed `.part` 安全續傳，驗證前不發布。
- 角色／場景 agent 缺席時維持 pending，但既有分鏡與選定後 4K 可用。
- Unit、browser、Ruff 與 mypy 全部通過。
