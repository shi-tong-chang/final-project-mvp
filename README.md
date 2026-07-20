# Final Project MVP

一個只在本機執行的繁體中文故事視覺工作台。目前主功能是角色風格櫥窗：
同一個角色構圖提供二十種不同媒材，每種風格都與一段 typed prompt
fragment 一對一綁定。

## 現在可用的功能

- 「生成角色／生成場景／生成分鏡」三個可用鍵盤操作的工作區分頁。
- 二十種角色風格展示，包括漫畫、線稿、黑暗童話、油畫、電影與寫實。
- 所有模擬預覽共用相同的臉、髮型及服裝幾何，只改變媒材與畫面質感。
- 選取風格後同步更新大型預覽、說明、標籤與實際風格提示詞。
- 將角色描述、固定一致性規則與所選風格片段組成完整提示詞並複製。
- 桌面與手機 responsive 版面；手機風格櫥窗固定雙欄。

目前沒有圖片生成。場景與分鏡頁是可互動的版面預覽，不會建立 GPU 任務。
網站也沒有 Codex 對話框；保留的受限 thread／turn API 不會被目前前端呼叫。

## 環境

- WSL2 Ubuntu 24.04
- Python 3.12.10
- 只綁 `127.0.0.1`
- 不需要資料庫、ComfyUI 或雲端服務即可使用風格櫥窗

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
