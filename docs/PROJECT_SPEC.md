# Final Project MVP 產品契約

## 1. 產品目標

建立一個只在本機運作的繁體中文故事視覺工作台。使用者可在角色、場景及
分鏡三個分類間切換；角色頁以同一個角色展示二十種畫風，選取後取得該
風格固定提示詞。分鏡頁可把一張場景圖與一張角色正面參考圖合成候選，
由使用者選定其中一張後，才可送入固定的 4K 放大流程。

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
- 上傳一張場景圖與一張單角色正面參考圖。
- 使用 server-owned `wf_dual_B1` 模板產生 1–3 張分鏡候選。
- 顯示每張候選的任務狀態與 seed，並由使用者明確選定一張。
- 只有已完成且已選定的候選可送入 `wf10_upscale_opt2`。
- 4K 工作流固定輸出 3840×2160，並提供同源預覽與下載。
- ComfyUI 未啟動時，角色風格櫥窗與其他靜態功能仍可使用。

## 3. 不在本階段

- 正式素材上傳與持久化。
- Codex 對話 UI。
- 資料庫、使用者登入或遠端公開。
- 單獨生成角色或場景。
- 雙角色 B1／人工選片／B2 的兩階段合成。
- 任意畫幅的 4K 輸出、任意 workflow 上傳或 node 級控制。

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

## 6. 驗收

- Live catalog 精確提供二十個唯一角色風格及非空白提示詞片段。
- Browser 無對話 panel／chat button，操作不建立 thread 或 turn。
- 二十張卡使用相同角色 DOM layers。
- 所選提示詞能完整複製，內容含角色一致性規則。
- 390px 手機雙欄且無水平溢位。
- 分鏡合成可顯示 1–3 張候選；未選定前 4K 操作維持鎖定。
- 重新選定候選時，4K 只使用目前被 server 確認的單一候選。
- 4K queued／running 時來源素材與選片保持鎖定；完成或失敗後可開始新
  工作。短暫輪詢失敗不得重複排程，應重試並與既有 run 對帳。
- ComfyUI 不可用或任務失敗時顯示安全錯誤，不洩漏本機路徑或 raw payload。
- 成功放大後提供 3840×2160 的同源預覽與下載。
- Unit、browser、Ruff 與 mypy 全部通過。
