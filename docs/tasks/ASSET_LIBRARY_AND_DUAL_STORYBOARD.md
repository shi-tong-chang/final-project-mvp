# 本機素材庫與單／雙角色分鏡路由

> 狀態：2026-07-21 已完成框架與自動化測試層接線；角色／場景 Agent
> TOML 尚待組員交付，目標 RTX 5070 Ti 的實機 GPU 重放仍待執行。

## 1. 使用者流程

```text
角色 Agent 產生前／左／右／後四視圖 ─┐
                                      ├→ trusted registration → 本機圖庫
場景 Agent 產生一張場景定稿 ──────────┘

使用者選 1–2 位角色 + 1 個場景 + 合成描述
  ├─ 1 位角色 → B1
  └─ 2 位角色 → B1(場景 + 角色一) → B2(B1 結果 + 角色二)
        ↓
      1–3 張最終候選
        ↓ 使用者明確選定一張
      可選的 4K 精修；其他候選不放大
```

角色的點選順序就是合成順序。雙角色的每張最終候選各自有一組 B1 與 B2
seed，B1 輸出經 Gateway 下載、正規化並以 server-owned filename 重新上傳，
再成為同一候選的 B2 輸入；Browser 不接觸中間檔名或 ComfyUI output
descriptor。

圖庫尚空時保留既有的「一張場景 + 一張角色正面圖」手動上傳模式。手動
模式只支援單角色，不會把第二位角色悄悄忽略。

## 2. 素材庫契約

正式素材保存在 repository 內、Git-ignored 的：

```text
.local-data/asset-library/
├── characters/char_<32 hex>/
│   ├── metadata.json
│   ├── front.png
│   ├── left.png
│   ├── right.png
│   └── back.png
└── scenes/scene_<32 hex>/
    ├── metadata.json
    └── scene.png
```

- ID 只由 server 產生；Browser 與 Agent 不自行命名目錄。
- metadata 使用 strict schema，包含 schema version、kind、opaque ID、名稱、
  描述與 UTC 建立時間。
- 登錄時解碼圖片、限制尺寸與 bytes、移除 metadata，再寫成 canonical
  RGB PNG。
- 所有圖片與 metadata 先寫入 staging directory、fsync，最後以 directory
  rename 發布；不允許 symlink、額外檔案、路徑穿越或非 regular file。
- 圖庫跨 Gateway 重啟保留；分鏡 run、候選與 4K job 仍是 process-local，
  重啟後不恢復。
- Git clone 不會包含使用者素材；新 clone 從空圖庫開始。
- 素材庫與 `.runtime/` ownership state 分離；先啟動 Gateway 預覽不會讓
  後續 runtime install 因缺少 ownership marker 而 fail closed。
- `STORYBOARD_WORKFLOW_ASSET_LIBRARY_ROOT` 即使由環境覆寫，也只能解析到
  repository 的 `.local-data/` 子樹；不能改指 `.runtime`、`.git`、`.venv`
  或 tracked source 目錄。

角色／場景 Agent 尚未接線，因此網頁的「確認生成」目前仍只確認設定。
未來 Agent controller 完成輸出後，必須呼叫 repository 提供的 trusted
registration CLI；Browser 不提供素材寫入 API，也不能傳入本機路徑。

固定 CLI 介面為：

```bash
.venv/bin/python scripts/register_generated_asset.py character \
  --name "角色名稱" --description "角色描述" \
  --front front.png --left left.png --right right.png --back back.png

.venv/bin/python scripts/register_generated_asset.py scene \
  --name "場景名稱" --description "場景描述" --image scene.png
```

## 3. HTTP 契約

### 3.1 唯讀素材

`GET /api/v1/gateway/assets` 回傳：

```json
{
  "characters": [
    {
      "asset_id": "char_<32 hex>",
      "name": "角色名稱",
      "description": "角色描述",
      "created_at": "UTC timestamp",
      "views": {
        "front": "/api/v1/gateway/assets/...",
        "left": "/api/v1/gateway/assets/...",
        "right": "/api/v1/gateway/assets/...",
        "back": "/api/v1/gateway/assets/..."
      }
    }
  ],
  "scenes": [
    {
      "asset_id": "scene_<32 hex>",
      "name": "場景名稱",
      "description": "場景描述",
      "created_at": "UTC timestamp",
      "image_url": "/api/v1/gateway/assets/..."
    }
  ]
}
```

圖片 endpoint 只接受符合 pattern 的 opaque ID 與固定角色 view enum，回傳
重新驗證的同源 PNG，不接受檔案路徑或 URL。

### 3.2 從圖庫建立分鏡

`POST /api/v1/gateway/workflows/storyboards/from-library` 只接受：

```json
{
  "prompt": "角色一在左側，角色二在右側……",
  "candidate_count": 3,
  "character_asset_ids": ["char_<32 hex>", "char_<32 hex>"],
  "scene_asset_id": "scene_<32 hex>"
}
```

角色 ID 必須是 1–2 個且不可重複，場景恰好一個；未知欄位、偽造 ID、空白
prompt 或不存在的素材都 fail closed。回應的 `workflow_route` 只可能是：

- `single_character_b1`
- `dual_character_b1_b2`

這個欄位是 server 決策的結果，不是 request 選項。每張候選另回傳
`stage_seeds.b1` 與雙角色時的 `stage_seeds.b2`；Browser 不能指定 seed。

## 4. 固定 workflow adapter

### 單角色

`wf_dual_B1.json`：場景寫入 node `41`、角色一正面圖寫入 node `42`，
server guard prompt 寫入 `170:151`，server seed 寫入 `170:169`，從 node
`9` 取得最終輸出。

### 雙角色

先執行上述 B1，再以 B1 正規化輸出作為 `wf_dual_B2.json` node `41`，
角色二正面圖作為 node `42`。B2 prompt guard 要求場景與既有角色一的
身份、外觀、服裝、姿勢及位置保持不變，只新增角色二；seed 與 output
prefix 仍由 server 管理，最終輸出仍是 node `9`。

兩份 workflow 原始 bytes 都以 SHA-256 pin 並固定 LF checkout。B2 與 B1
使用相同三顆模型，不增加 custom node 或模型下載。

## 5. 安全與失敗行為

- Gateway 與 ComfyUI 仍固定 loopback；Browser 不直接連 8188。
- request schema 為 `extra="forbid"`；Browser 不提交 workflow、node、
  model、seed、server filename、cwd 或 shell command。
- GPU 工作由單 worker 序列化。雙角色候選的兩輪不可並行搶 GPU。
- 任一階段失敗，只將該最終候選標記失敗；不把 B1 中間圖當成可選定稿。
- 輸入圖片在上傳 ComfyUI 後釋放；只保留完成的最終候選與使用者要求的
  4K 成品，並受既有 retained-byte hard cap。
- 只有 server 記錄的已選最終候選可以排入 4K；雙角色 B1 中間圖永遠不
  能繞過選片 gate。

## 6. 驗收

1. 空庫明示空狀態，不建立示意歷史。
2. 登錄後，角色頁顯示四視圖、場景頁顯示場景圖；重啟 Gateway 後仍在。
3. 分鏡頁最多選兩位角色且只選一個場景，並保留點選順序。
4. 一位角色只送一輪 B1；兩位角色每張候選精確送 B1、B2 各一輪。
5. 實際 workflow route 與各階段 seed 顯示於 UI，但不能由 Browser 控制。
6. 無圖庫時的既有單角色 multipart 上傳仍可使用。
7. 未選最終候選前不能 4K；落選候選與 B1 中間圖不會被放大。
8. corrupt／symlink 素材、未知 ID、跨站 mutation 與非 loopback client
   均安全拒絕，不洩漏本機路徑。
9. Unit、browser regression、Ruff、mypy、Node syntax 與 Git whitespace
   檢查全部通過。
