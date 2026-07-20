# 單角色分鏡合成與選定後 4K 接線任務單

> 狀態：2026-07-21 已完成接線與安全硬化；待使用者 opt-in 實機 GPU
> smoke。
>
> 本任務擴充已完成的 Gateway V2 展示頁；保留 loopback-only、strict
> schema、同源資產與無登入的安全邊界。

## 1. 使用者流程

```text
上傳場景圖 + 單一角色正面參考圖 + 合成描述
  → 產生 1–3 張約 1MP 分鏡候選
  → 使用者檢視並明確選定一張
  → 填寫／確認 4K 精修描述
  → 只把選定候選送入 4K
  → 預覽或下載 3840×2160 成品
```

分鏡生成完成不得自動觸發 4K。選定動作是後端持有的狀態，不可只依賴
瀏覽器按鈕是否 disabled。

## 2. 架構

```text
Browser @ 127.0.0.1:8010
  ↓ typed、同源 HTTP
FastAPI Gateway
  ├── in-memory run／candidate authority
  ├── fixed workflow adapters
  └── async ComfyUI client
        ↓ loopback HTTP only
      ComfyUI @ 127.0.0.1:8188
```

- Codex thread／turn 不負責 GPU 排程；現有 Codex transport 繼續維持
  read-only 與 `approval_policy=never`。
- Gateway 未連到 ComfyUI 時仍須能啟動並提供 catalog 與靜態頁面。
- Browser 不接觸 ComfyUI endpoint、prompt ID、server path 或 workflow
  graph。
- 任務與圖片由單一 Gateway process 暫存管理；本階段不提供跨重啟歷史。

## 3. Workflow adapter

### 3.1 單角色分鏡候選

使用 `docs/workflows/wf_dual_B1.json` 作為 server-owned 模板。它與單人
插入鏈使用相同 Qwen Image Edit 核心，但不含 `wf02_insert.json` 中針對
特定測試場景寫死的 `x=700, y=150, 720×506` 裁切框。

每次送單只可改寫：

- `41 / LoadImage.image`：Gateway 產生的場景 server filename。
- `42 / LoadImage.image`：Gateway 產生的角色 server filename。
- `170:151 / TextEncodeQwenImageEditPlus.prompt`：受控骨架加使用者描述。
- `170:169 / KSampler.seed`：server 產生並回報的 seed。
- `9 / SaveImage.filename_prefix`：每個候選唯一 prefix。

角色輸入限定單一正面參考圖；四視圖資產必須先由資產層取出正面圖，不可
直接把整張四視圖餵入本模板。

### 3.2 4K 放大

使用 `docs/workflows/wf10_upscale_opt2.json`。每次送單只可改寫：

- `10 / LoadImage.image`：已完成且已選定候選的 server filename。
- `4 / CLIPTextEncodeLumina2.user_prompt`：本次畫面描述與一致性 guard。
- `22 / KSampler.seed`：server 管理的 seed。
- `26 / SaveImage.filename_prefix`：本次 4K 任務唯一 prefix。

模板固定中繼 2672×1504、最終 3840×2160 並採 center crop，因此 UI
明示本階段只支援 16:9 4K 成品。

## 4. HTTP 與安全契約

- 公開 request／response 使用 `extra="forbid"` 的 Pydantic schema。
- 圖片只接受明確 allowlist 格式與大小；忽略使用者檔名，改用 server
  產生的名稱。
- 公開 ID 一律 opaque；不接受 client 自行指定 run、candidate、Comfy
  prompt 或 output descriptor。
- 預覽與下載只提供 Gateway 同源 URL。
- 只接受 loopback ComfyUI base URL，不跟隨 redirect、不讀 proxy
  environment。
- 禁止呼叫 `/free`、全域 queue 清除或全域 interrupt。
- 錯誤訊息不得包含本機路徑、workflow payload、模型路徑或 Comfy raw
  traceback。
- Mutation 只接受 loopback client，並拒絕跨站 `Origin`／
  `Sec-Fetch-Site`；response 加入 `Cross-Origin-Resource-Policy:
  same-origin`。
- 分鏡 multipart 的總 request body 在 ASGI receive layer 截止，不能先
  spool 完超大 UploadFile 才檢查。
- 兩份固定 JSON 以原始 SHA-256 pin；`.gitattributes` 強制 B1 與 wf10
  checkout 為 LF，Windows clone 不得因 CRLF 造成假性完整性失敗。

## 5. 任務狀態與生命週期

- 合成候選與 4K 任務狀態使用有限集合：
  `queued`、`running`、`completed`、`failed`。
- GPU 工作由 Gateway 序列化，避免同一張 16GB GPU 被多個 workflow
  同時擠壓。
- lifespan 明確擁有 worker、active task、HTTP client 與暫存資產；
  shutdown 必須取消並等待背景工作結束。
- queue、run 數與保留圖片 bytes 均設 hard cap；輸入上傳至 ComfyUI 後
  立即釋放 Gateway 內的來源 bytes。
- 同一 run 可重新選定一張已完成候選，直到 4K 排程為止。4K request
  只回傳 server-issued `expected_candidate_id` 作 optimistic assertion；
  endpoint 仍依 server state 解析來源，不接受 client 另傳任意 asset。
- Gateway 在呼叫 `/prompt` 前先核發 prompt ID；shutdown 先停止 worker，
  再以 captured ID 定向取消，避免 response-boundary 孤兒 GPU 工作。

## 6. 驗收

1. 上傳場景與角色後可建立 1–3 張候選。
2. 候選各有獨立 seed、狀態、同源預覽與下載。
3. 未選定、選到未完成候選或偽造 candidate ID 時，4K 一律拒絕。
4. 只有 server 記錄的目前選定候選會進入 wf10。
5. wf10 的 node 4 不得保留 placeholder。
6. ComfyUI 不在線時首頁／catalog 正常，工作流狀態與送單回安全錯誤。
7. Browser E2E 覆蓋候選、選定、單張 4K、失敗與 390px layout。
8. 整段流程不得建立 Codex thread／turn，也不得從 Browser 直連 8188。
9. 可在送出 4K 前重新選片；多分頁／舊選片 ID 必須回 409，不得放大錯圖。
10. 一次暫時性輪詢失敗可自動恢復，且 4K POST 不會被重送。
11. 跨站 mutation、非 loopback client、超大 body 與資源滿載均 fail
    closed。
