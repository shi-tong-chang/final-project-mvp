# Final Project MVP

一個只在本機執行的繁體中文故事視覺工作台。角色風格櫥窗以同一個角色
構圖提供二十種不同媒材，每種風格都與一段 typed prompt fragment
一對一綁定。分鏡工作區可把一張場景圖與一張角色正面參考圖送入本機
ComfyUI，產生候選並在人工選定後只放大該張至 4K。

## 現在可用的功能

- 「生成角色／生成場景／生成分鏡」三個可用鍵盤操作的工作區分頁。
- 二十種角色風格展示，包括漫畫、線稿、黑暗童話、油畫、電影與寫實。
- 所有模擬預覽共用相同的臉、髮型及服裝幾何，只改變媒材與畫面質感。
- 選取風格後同步更新大型預覽、說明、標籤與實際風格提示詞。
- 將角色描述、固定一致性規則與所選風格片段組成完整提示詞並複製。
- 上傳場景與單一角色正面圖，產生 1–3 張單角色分鏡候選。
- 檢視候選 seed、選定其中一張，再決定是否放大。
- 只有後端確認為已選定的候選可送入固定 3840×2160 的 4K 工作流。
- 以 Gateway 同源 URL 預覽或下載候選及 4K 成品。
- 桌面與手機 responsive 版面；手機風格櫥窗固定雙欄。

角色風格櫥窗與場景頁不需要 ComfyUI；ComfyUI 未啟動時仍可使用。網站沒有
Codex 對話框，保留的受限 thread／turn API 不會被目前前端呼叫。

## 環境

- WSL2 Ubuntu 24.04
- Gateway 使用獨立 Python 3.12.10 虛擬環境
- 釘定的 ComfyUI 可繼續使用自己的 Python 3.12.3／torch
  2.12.0+cu130 虛擬環境；兩個 process 不必共用 Python patch 版本
- 只綁 `127.0.0.1`
- 分鏡合成另連本機 ComfyUI `127.0.0.1:8188`
- 不需要資料庫或雲端服務

## 安裝

在專案根目錄執行：

```bash
uv sync --dev --python 3.12.10
```

如果尚未安裝 uv，可先依 Astral 官方方式安裝，再執行上面的同步命令。

`.python` 與 `.venv` 都是含有絕對路徑的本機生成物，不可隨專案目錄搬移。
若專案已改名或移動，請刪除這兩個 Git-ignored 目錄，再於新位置重新執行
安裝命令。

## 啟動

```bash
.venv/bin/uvicorn app.gateway_main:app \
  --app-dir backend \
  --host 127.0.0.1 \
  --port 8010 \
  --workers 1
```

瀏覽器開啟 <http://127.0.0.1:8010>。

本服務沒有登入系統，不得改綁 `0.0.0.0`。

## 啟動 ComfyUI 分鏡工作流

角色風格櫥窗不依賴 ComfyUI。需要產生分鏡時，另外在釘定的 ComfyUI
環境啟動 loopback server：

```bash
cd "$COMFYUI_ROOT"
venv/bin/python main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --disable-all-custom-nodes \
  --whitelist-custom-nodes ComfyUI-GGUF
```

`COMFYUI_ROOT` 是使用者自己的 ComfyUI 根目錄，只存在本機設定，不寫進
Git。這個啟動方式只載入本流程唯一需要的 `ComfyUI-GGUF`，不會修改或
更新使用者既有的其他 custom nodes。Gateway 只透過 HTTP 連線，所以
ComfyUI 可以安裝在任意路徑。預設連線設定如下，可在 `.env` 覆寫：

```dotenv
STORYBOARD_WORKFLOW_COMFYUI_BASE_URL=http://127.0.0.1:8188
```

啟動 Gateway 後可用下列 endpoint 確認工作流連線：

```text
GET http://127.0.0.1:8010/api/v1/gateway/workflows/status
```

分鏡頁目前使用 `wf_dual_B1` 的單角色第一輪結構，避免
`wf02_insert` 針對特定測試 plate 寫死的裁切框；4K 使用
`wf10_upscale_opt2`。兩份 JSON 都由 server 固定載入，Browser 不會也
不能提交 workflow、node ID、模型、seed 或 ComfyUI 路徑。

目前工作流限制：

- 場景圖加一張角色正面參考圖；不要直接上傳整張四視圖。
- 每次建立 1–3 張候選，GPU 任務依序執行。
- 分鏡候選不會自動放大；必須先人工選定。
- 4K 固定為 16:9、3840×2160；非 16:9 來源會由工作流置中裁切。
- 任務與生成資產是單一 Gateway process 的暫存資料，重啟後不保留歷史。
- Gateway 的 queue、run 數與記憶體圖片總量都有上限；滿載時安全回
  `429`，不會無界累積。

Gateway 不會刪除共用 ComfyUI 的 input／output 檔案，以免誤傷使用者既有
資料。長時間反覆測試後，可由使用者自行清理 ComfyUI 內
`final-project-mvp/` 子資料夾；正式持久化與受控清理由後續資產層負責。

## 測試

```bash
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend tests
node --check frontend/gateway/app.js
```

Browser E2E 需要本機已有 Playwright Chromium 與 Ubuntu 系統相依：

```bash
sudo .venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
.venv/bin/pytest tests/e2e/test_codex_gateway_page.py
```

`install-deps` 會透過 Ubuntu 套件管理器安裝 Chromium 所需的動態函式庫與
字型，因此需要系統管理權限；只啟動本機網站時不需要這一步。

## 正式風格圖接線

目前二十張圖是 HTML／CSS 原生模擬。收到正式素材後，將圖片放在受控的
同源靜態路徑，並在
`backend/app/services/codex_gateway/catalog.py` 對應項目填入
`preview_url`。既有 `item_id` 與 `prompt_fragment` 預設保持不變，確保
使用者原有選擇仍可重現。

詳細契約見 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md)。
