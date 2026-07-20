# Final Project MVP 產品契約

## 1. 產品目標

建立一個只在本機運作的繁體中文故事視覺工作台。使用者可在角色、場景及
分鏡三個分類間切換；角色頁以同一個角色展示二十種畫風，選取後取得該
風格固定提示詞。

## 2. 本階段功能

- 角色描述輸入，長度上限 1200 字元。
- 二十個 typed 角色風格項目。
- 同角色、不同媒材的 code-native placeholder。
- 選取風格後更新大型預覽、說明、標籤與提示詞。
- 複製完整提示詞：
  1. 使用者角色描述。
  2. 固定角色一致性規則。
  3. 所選風格的 `prompt_fragment`。
- 場景與分鏡版面預覽。
- Desktop／mobile responsive 與鍵盤 tabs。

## 3. 不在本階段

- 圖片生成、GPU 排程或 ComfyUI。
- 正式素材上傳與持久化。
- Codex 對話 UI。
- 資料庫、使用者登入或遠端公開。
- 場景與分鏡的正式生成或規劃 submit。

## 4. Catalog 契約

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

## 5. 安全

- Server 只綁 `127.0.0.1`。
- CSP：資源與連線同源，禁止被 iframe 嵌入。
- 不在前端或日誌暴露 Codex auth、API key 或環境內容。
- 保留的 Codex adapter 固定 read-only、never approve、repo-scoped cwd。
- Browser 不直接連 child process。

## 6. 驗收

- Live catalog 精確提供二十個唯一角色風格及非空白提示詞片段。
- Browser 無對話 panel／chat button，操作不建立 thread 或 turn。
- 二十張卡使用相同角色 DOM layers。
- 所選提示詞能完整複製，內容含角色一致性規則。
- 390px 手機雙欄且無水平溢位。
- Unit、browser、Ruff 與 mypy 全部通過。
