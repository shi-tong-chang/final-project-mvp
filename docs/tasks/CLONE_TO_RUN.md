# Clone-to-run 執行契約與驗收規格

> 狀態：runtime controller、五個命令、lock 與自動化測試已實作；目標
> RTX GPU 的 47.27 GB 真實下載、CUDA 啟動與像素重放仍待 opt-in 實機
> 驗收。本文同時記錄已落地契約與剩餘的硬體驗收，不把自動化測試冒充
> GPU 成功證明。

本契約的目標是讓使用者 clone repository 後，能由 Codex 把「安裝環境」
與「啟動」轉成固定、可重跑且不破壞既有 ComfyUI 的操作。執行依據為
[`runtime-lock.json`](../../runtime/runtime-lock.json)、
[`models.lock.json`](../../runtime/models.lock.json) 與 server-owned
workflow；瀏覽器不能覆寫版本、模型、路徑或啟動參數。

## 1. 支援邊界

首個可驗收平台固定為：

- Windows 的 WSL2。
- WSL distribution：Ubuntu 24.04 LTS、x86_64。
- NVIDIA GPU 能從 WSL 內以 `nvidia-smi` 看到；目前目標機是 16 GB
  VRAM 等級。
- repository 位於 WSL 的 Linux filesystem，建議放在 `$HOME` 下，
  不以 `\\wsl.localhost\...` 或 `/mnt/c/...` 當程式內部路徑。
- 安裝時可連線至 GitHub、Hugging Face 與 PyTorch wheel 來源；本階段
  不承諾離線安裝。

安裝器不得自行安裝或更新 Windows NVIDIA driver、WSL kernel、Ubuntu
系統套件，也不得要求 root 才能完成一般 runtime 安裝。缺少系統前置條件
時應停止並回報，不能擅自修改主機。

### 1.1 兩套 Python 必須分離

| Process | 固定 Python | 用途 |
|---|---:|---|
| FastAPI Gateway | 3.12.10 | API、網站、任務狀態與 ComfyUI adapter |
| ComfyUI | 3.12.3 | ComfyUI、PyTorch 2.12.0+cu130 與 GPU workflow |

兩者以不同 virtual environment、不同 dependency lock 與不同 process
執行。不得因 Gateway 要統一至 3.12.10，就升級既有或 managed ComfyUI
的 Python；也不得讓 Gateway import ComfyUI 的 site-packages。

`scripts/fpmvp_runtime.py` 本身只依賴 Ubuntu 24.04 可用的 Python
standard library，以便先完成 bootstrap。下載 Python 或建立環境時，cache 與
runtime 資產必須留在 Git-ignored `.runtime/` 或 Gateway 專用 `.venv/`。

## 2. 所有權與選擇策略

CLI 把 **ComfyUI code** 與 **models** 分成兩個 ownership 維度。最終
選擇寫入 `.runtime/config.json`，並由 `status` 明確顯示；不能只靠猜測
目前連到哪套 code、哪個模型根目錄。

### 2.1 預設 `auto`：managed code，models 才自動搜尋

`install` 的預設等價於：

```text
--comfy-mode auto    → managed ComfyUI code
--models-mode auto   → external 全量驗證成功，否則 managed download
```

Comfy code 的 `auto` **不會**掃描或自動採用 `$HOME/ai/ComfyUI`。它固定
建立 `.runtime/comfyui/`，確保不受使用者既有安裝與 30+ custom nodes
影響。

Model auto 只唯讀檢查受控候選：明確 `--model-root`、
`FPMVP_MODEL_ROOT`、managed ComfyUI 的 models，以及
`$HOME/ai/ComfyUI`／`$HOME/ComfyUI` 的 canonical `models/`。對這兩個
已知 root，若 machine-local YAML 含絕對 `base_path`，controller 只額外
推導 `base_path/models` 候選；不解析任意 category mapping，也不把來源
YAML 交給 managed ComfyUI。單一候選必須讓八顆模型全部通過完整 SHA-256，
才解析成
`external`；否則改在 `.runtime/models/` 下載 managed copies。不得遞迴
掃描整個 home、Windows 磁碟或任意 mount，也不得修改失敗的候選。
自訂或分散的 category mapping 不由 auto 猜測；使用者必須以
`FPMVP_MODEL_ROOT` 或 `--models-mode external --model-root <path>` 明確
提供符合 lock 目錄結構的單一 root。

### 2.2 明確 `adopted` ComfyUI code

只有使用者指定
`--comfy-mode adopted --comfyui-root <path>` 才可採用既有 ComfyUI。
必要條件為：

- ComfyUI repository 與完整 commit 精確符合 lock，受 Git 追蹤檔無修改；
  其他 untracked custom node 目錄可以存在。
- ComfyUI venv 是 Python 3.12.3，PyTorch／torchvision／torchaudio
  metadata 符合 lock。
- `ComfyUI-GGUF` 位於指定 commit，且 tracked／untracked worktree 都嚴格
  clean。
- source root 不存在 `extra_model_paths.yaml`；釘版 ComfyUI 會先載入
  source YAML，runtime 為避免模型搜尋優先序被覆寫而 fail closed。
- host／port 可維持 `127.0.0.1:8188`，且沒有未知 process 佔用。

模型 ownership 仍由獨立的 `--models-mode` 決定，不要求模型必須位於
adopted root。其他 custom node 可以存在，但啟動時全部停用，只 allowlist
`ComfyUI-GGUF`。任何必要 code pin 不符時，adopted 直接失敗，不可
checkout、`pip install`、安裝 node 或降級成「差不多可用」。

Adopted 安裝資產保持唯讀：不改 Git、venv、custom nodes、models 或既有
YAML；ComfyUI 的 cwd、base、input、output、temp、user、HOME、cache 與
logs 全部導向 `.runtime/`。Runtime 自有 custom-node root 必須為空，
source custom nodes 則全部停用後只 allowlist strict-pinned GGUF。若
8188 已有一個不是本 CLI 啟動的 server，不可只因 HTTP 可連線就假設它
符合 pin，`stop` 也不得終止它。

### 2.3 明確 ownership 模式

- `--comfy-mode managed`：強制使用 `.runtime/comfyui/`。
- `--comfy-mode adopted`：強制唯讀驗證指定 ComfyUI root。
- `--models-mode managed`：不搜尋外部模型，下載至 `.runtime/models/`。
- `--models-mode external --model-root <path>`：只完整驗證指定 root；任一
  模型不符就失敗，絕不補檔。

實際 Git-ignored 配置如下：

```text
.venv/                         # Gateway Python 3.12.10
.runtime/
  tools/                       # 釘版 uv
  comfyui/.venv/               # managed code + Comfy Python 3.12.3
  models/{unet,clip,vae,diffusion_models,upscale_models}/
  comfy-data/{input,output,temp,user}/
  logs/{gateway.log,comfyui.log}
  state/processes.json
  state/models.receipt.json
  config.json
  extra_model_paths.yaml
```

Managed 不得修改使用者在 `$HOME/ai/ComfyUI` 或其他位置的安裝，不呼叫
ComfyUI Manager，也不更新到 branch 最新版本。若 `.runtime/` 或 `.venv/`
未被 Git 排除，安裝器應 fail closed，避免把 venv、log 或數十 GB 模型
加入 commit。

## 3. 模型下載與驗證契約

[`models.lock.json`](../../runtime/models.lock.json) 固定八顆模型的
下載 URL／revision、安裝子目錄、byte size、SHA-256、授權與使用它的
workflow。總大小是 **47,266,047,406 bytes，約 47.27 GB（十進位）或
44.02 GiB**。

安裝與採用模型必須遵守：

1. SHA-256 是檔案身分的最終 authority；檔名相同不算符合。
2. Models auto／external 在採用候選 root 前要完整計算八顆 SHA-256，
   而且不得修補不合格外部檔案。
3. Managed 下載先寫入
   `.runtime/models/<subdir>/<filename>.part`，支援 HTTP Range 續傳。
   Server 對 Range 合法回傳完整 `200` 時，安全 truncate 該 managed
   partial 並自動從零重抓；`206` 的 `Content-Range` 異常才 fail closed，
   絕不可把完整 response 接到舊 partial。
4. 下載完成後先比對 byte size，再串流計算 SHA-256。兩者都符合才以
   atomic rename 發布到模型目錄。
5. 中斷時保留可續傳的 `.part`；hash 錯誤的檔案不可被 ComfyUI 看見，
   也不可改名冒充完成品。不合格 managed final 或完整 bad-SHA `.part`
   以 no-overwrite 的 `.fpmvp-rejected-*` 名稱隔離後才重抓。
6. 重跑 `install` 必須 idempotent：已驗證檔案不重抓，只處理缺少、
   中斷或不合格項目。
7. Install 在完整 SHA 驗證後寫入 receipt，綁定 model-lock digest、root、
   device、inode、size 與 mtime。`preflight` 預設 quick，核對八顆檔案
   exact bytes 與 receipt 未變；`preflight --full` 才逐顆重新計算
   SHA-256。
8. Fresh managed install 建議至少準備 65 GiB；空間不足時停止並保留已
   完成模型與合法 partial，不得發布不完整檔案。

若同一固定 URL 多次下載仍無法符合 lock SHA-256，視為來源漂移或傳輸
異常並停止自動化，不得更新 lock 迎合下載結果。

## 4. CLI 契約

已實作命令如下：

```bash
python3 scripts/fpmvp_runtime.py install
python3 scripts/fpmvp_runtime.py preflight
python3 scripts/fpmvp_runtime.py start
python3 scripts/fpmvp_runtime.py status
python3 scripts/fpmvp_runtime.py stop
```

需要強制策略時：

```bash
python3 scripts/fpmvp_runtime.py install \
  --comfy-mode adopted \
  --comfyui-root "$HOME/ai/ComfyUI"

python3 scripts/fpmvp_runtime.py install --comfy-mode managed
python3 scripts/fpmvp_runtime.py install \
  --models-mode external \
  --model-root "$HOME/ai/ComfyUI/models"
python3 scripts/fpmvp_runtime.py preflight --full
python3 scripts/fpmvp_runtime.py --json status
```

Windows PowerShell 可用 `scripts/runtime.ps1` 傳入相同 subcommand 與
flags。Codex 收到「安裝環境」時，固定執行 `--json install`，成功後執行
`--json preflight`；收到「啟動」時執行 `--json start`，再以
`--json status` 對帳並回報本地網站 URL。「狀態」與「停止」分別固定到
`--json status`／`--json stop`。Codex 不應用臨時 shell 命令繞過 lock。

### 4.1 `install`

- 先驗證兩份 lock 的 schema、唯一性、完整 commit、HTTPS URL 與
  SHA-256 格式；lock 無效時在任何下載前停止。
- 預設建立 managed ComfyUI code；模型 auto 經完整 SHA 驗證後才採用
  external，否則建立 managed models。
- bootstrap Gateway Python 3.12.10 與獨立 venv。
- adopted code 只稽核；managed code 才建立 ComfyUI、venv 與 custom
  node。模型 ownership 另由 `--models-mode` 控制。
- 不啟動長駐 process，也不開瀏覽器。
- 可安全重跑；失敗時保留可驗證、可續傳的進度。
- 成功代表安裝資產符合 lock，不代表 GPU inference 已經重放成功。

### 4.2 `preflight`

`preflight` 預設是 quick，不修改檔案、不啟動 process、不需要網路，並
逐項回報 `PASS`、`WARN` 或 `FAIL`：

- WSL2、Ubuntu 24.04、x86_64。
- `nvidia-smi` 可用、GPU 可見與目標 VRAM 資訊。
- Gateway／ComfyUI Python 與 dependency pins 分離且正確。
- ComfyUI／GGUF commits、package metadata，以及八顆模型的 exact bytes
  與 install SHA receipt。
- `.runtime` Git-ignore 邊界、可用磁碟空間與 state 可寫性。
- 8010／8188 port 狀態及 host 均為 loopback。
- character／scene agent 是否仍是 non-blocking `pending`。

只有 `--full` 會重新 hash 全部 47.27 GB 模型。Preflight 只做結構、版本、
裝置可見性與完整性檢查；它不提交 ComfyUI prompt，因此不能當作 GPU
重放證明。

### 4.3 `start`

- 防止重複啟動；若同一 runtime 已健康，回報現況而非再開一組 process。
- ComfyUI 固定綁 `127.0.0.1:8188`，停用全部 custom nodes 後只允許
  `ComfyUI-GGUF`，並把工作資料導向 `.runtime/comfy-data/`。
- ComfyUI ready 後，Gateway 以單 worker 綁
  `127.0.0.1:8010`；不得接受 `0.0.0.0` 設定。
- 等待實際 health response 後才顯示 ready，不能只看到 PID 就成功。
- Gateway ready 後 best-effort 開啟固定本機 URL；Browser 無法代開只產生
  warning，使用者仍可手動開啟。`start --no-open` 可關閉這個動作。
- 若 Gateway 可啟動但 ComfyUI／GPU 不可用，保留首頁與 catalog，
  狀態明示 `degraded`；workflow API 回安全錯誤，不可假裝能生成。
- 呼叫端必須同時檢查 JSON 的 `ok` 與 `overall`；不能只看到 PID、URL
  或 process exit code 就宣稱完整 ready。

### 4.4 `stop`

- 只停止 `.runtime/state/` 記錄、且經 PID／process identity 雙重確認的
  本專案 process。
- 先讓 Gateway 停止接單並定向取消它持有的 prompt，再停 ComfyUI。
- 禁止全域清 queue、呼叫 `/free`、中斷別人的 prompt 或 kill 未知的
  port owner。
- 外部已存在且不是本 CLI 啟動的 adopted ComfyUI 永遠不由 `stop`
  終止。
- 多次執行必須安全；process 已停止時回報 stopped，不當成破壞性錯誤。

### 4.5 `status`

`status` 必須以 PID、process identity 與 HTTP health 三者對帳，而不是
只讀過期 PID file。輸出至少包含：

- selected `comfy_mode` 與 `models_mode`；實際 root 只存在 machine-local
  `.runtime/config.json`，不必暴露在一般 status 摘要。
- 整體 `ready`／`degraded`／`stopped`／`unconfigured`，以及 Gateway、
  ComfyUI 各自的 process ownership、HTTP health 與 Gateway–ComfyUI
  啟動連線 check；不從單一 check 推測不存在的 per-service state machine。
- loopback URL 與模型 `8/8` exact-bytes 摘要；完整 SHA 結果只由 install
  或 `preflight --full` 證明。
- 狀態檢查當下的安全 code／message；目前不持久化「最近一次錯誤」歷史，
  詳細故障由 Git-ignored logs 查閱。

`--json` 提供固定 schema，讓 Codex 能可靠回報而不解析人類文字。
角色與場景 agent 的 pending 狀態由 `preflight` 與 lock 對帳；Python、
package、commit 與 GPU pins 也由 `preflight` 稽核，不由快速 `status`
重做。

## 5. 產品可用性與 non-blocking agent

角色生成 agent 與場景生成 agent 的 TOML 插槽固定為
`.codex/agents/character_generator.toml` 與
`.codex/agents/scene_generator.toml`；目前尚未接入，狀態固定為
`pending`、`blocks_start=false`。未接入時：

- 不阻擋 Gateway 首頁、二十種角色風格櫥窗與靜態場景頁。
- 不阻擋現有的「場景圖＋單角色正面參考圖」分鏡候選流程。
- 不阻擋使用者明確選定候選後的 4K 流程。
- 必須讓尚未接入的角色／場景獨立生成功能顯示為 pending，不可偽裝已
  生成，也不可用 placeholder 當正式資產保存。

分鏡候選完成後不得自動送 4K。只有後端記錄為完成且由使用者目前明確
選定的單一候選，才能進入 `wf10_upscale_opt2`；未選定、舊選定 ID 或
落選圖一律拒絕。

## 6. Loopback 與 process 安全

```text
Browser → http://127.0.0.1:8010 → FastAPI Gateway
                                      ↓
                                http://127.0.0.1:8188
                                      ↓
                                   ComfyUI
```

- Browser 不能直連 8188，也不能提交 workflow、node ID、模型路徑、
  seed、server filename 或 shell command。
- Gateway 與 ComfyUI 都只接受 IPv4 loopback；`localhost` 解析結果、
  proxy environment 或 redirect 不得繞過限制。
- 任一固定 port 被未知 process 佔用時 fail closed，不能自動 kill。
- PID、log、下載與生成暫存均不得加入 Git；秘密只從環境讀取，不寫入
  log 或 status JSON。
- Gateway shutdown 依既有工作流生命週期定向處理自己持有的任務，不
  影響共享 ComfyUI 的其他任務。

## 7. 驗收矩陣

本表同時列出必要結果與目前證據；「自動化通過」表示本 repository 的
fixture／API／Browser 測試已覆蓋，不表示已在目標 GPU 實際推論。

| ID | 情境 | 預期結果 | 目前證據／剩餘驗收 |
|---|---|---|---|
| C01 | Ubuntu 24.04 x86_64 全新 clone、無 ComfyUI | 預設建立 `.runtime` managed code；無合格 external root 時下載八顆 managed models | 安裝流程自動化通過；全新 WSL 的 47.27 GB 實抓待驗 |
| C02 | 本機已有完全不同的 ComfyUI，但使用預設 install | 不採用其 code；只可能在八顆完整 SHA 通過後唯讀採用其 model root | ownership／候選搜尋測試通過；外部 root 實機 SHA 待驗 |
| C03 | 明確 adopted 的 ComfyUI commit、Python 或 node 任一不符 | adopted fail，既有目錄零修改；不自動 fallback | 自動化通過 |
| C04 | managed 已完整安裝後重跑 install | 不重抓、不重建，結果相同 | 自動化通過 |
| C05 | 模型下載中斷後重跑 | 從合法 `.part` 續傳；完成前不發布模型 | HTTP fixture 通過；大型實網續傳待驗 |
| C06 | 來源回傳錯誤 bytes 或 SHA | 拒絕發布；重複不符時停止，不改 lock | HTTP fixture 通過 |
| C07 | 模型空間不足 | 不發布 partial；保留既有完成品與合法 `.part`，釋放空間後可重跑 | 磁碟判斷已實作；低空間 fault injection／實機待驗 |
| C08 | Gateway 3.12.10、Comfy 3.12.3 | 兩個 PID 的 executable／venv 與 package pins 各自正確 | lock／package 稽核通過；目標機雙 process 待驗 |
| C09 | 8010 或 8188 被未知 process 佔用 | start fail closed，不終止對方 | 自動化通過 |
| C10 | 重複 start／stop 或 stale PID file | 不重複開 process；status 對帳；stop idempotent | 自動化通過 |
| C11 | ComfyUI 或 GPU 不可用 | Gateway 仍只在 8010 提供靜態功能，status 為 degraded | 自動化通過 |
| C12 | 從非 loopback 存取或要求綁 `0.0.0.0` | 拒絕啟動／拒絕 request | API／安全測試通過 |
| C13 | character／scene agent 都 pending | start 不被阻擋；對應未完成入口誠實顯示 pending | API／Browser 通過 |
| C14 | 分鏡候選未選定就要求 4K | 前後端皆拒絕，ComfyUI 不收到 wf10 | API／Browser 通過 |
| C15 | 選定一張已完成候選後要求 4K | 只送目前 server-authoritative 候選，其餘候選不放大 | Fake Comfy API／Browser 通過；GPU smoke 待驗 |
| C16 | stop 面對非本 CLI 啟動的 adopted server | 不終止外部 process，不清其 queue | 自動化通過 |
| C17 | WSL 內看不到 NVIDIA GPU | preflight FAIL；不嘗試安裝 driver，start 僅可 degraded | 判斷邏輯通過；目標 WSL 待驗 |
| C18 | managed runtime 已存在但 repository 被搬移 | 因 marker／config 絕對路徑不符而 fail closed；由人類備份舊 `.venv`／`.runtime` 後在新位置重跑 install，不碰 adopted | Fail-closed 契約已實作；不提供自動破壞性搬移 |

### 7.1 尚待 opt-in 的 GPU 驗收

在目標 RTX GPU 上，至少要另外執行：

1. `preflight --full`，保存不含秘密的版本與 SHA 摘要。
2. 以固定 `wf_dual_B1` 和黃金輸入產出約 1 MP 候選。
3. 證明候選完成不會自動排程 4K。
4. 明確選定一張後，僅該張進入 `wf10_upscale_opt2`，輸出
   3840×2160。
5. 記錄 seed、workflow digest、耗時、峰值 VRAM、成功／失敗與成品
   檢查結果。

跨 GPU、driver 或 torch 的輸出不保證位元級一致；異機影像品質門檻仍需
另行定義。**本文沒有執行上述 GPU 重放，也不宣稱黃金圖 maxdiff=0 能在
新機成立。**

## 8. 失敗復原

| 失敗 | 安全復原 |
|---|---|
| 網路中斷 | 保留 `.part`，網路恢復後重跑同一個 `install` 續傳 |
| Range request 回合法完整 `200` | Runtime 自動 truncate 該 managed partial 並從零重抓；不動其他已驗證模型 |
| Range metadata 異常 | Fail closed 並保留證據；不得把 response 接到 partial 或發布模型 |
| SHA 持續不符 | 停止使用該來源並回報 lock/source 漂移；不得自動接受新 hash |
| adopted pin 不符 | 保持原目錄不變；改選 managed，或由使用者在專案外自行整理 |
| managed 檔案損壞 | 只隔離並重抓不合格項目，再完整 hash；不全量重裝 |
| 磁碟不足 | stop、釋放空間後重跑 install；完成模型與合法 partial 可沿用 |
| NVIDIA 在 WSL 不可見 | 修復 Windows driver／WSL GPU 支援後重跑 preflight；CLI 不代裝 driver |
| port 被佔用 | 由使用者確認並停止真正 owner，再重跑；CLI 不 kill 未知 PID |
| ComfyUI crash／GPU OOM | 該任務安全標記 failed，Gateway 保持可用；確認 log 摘要後 stop/start |
| Gateway 重啟 | 接受目前 MVP 的暫存 run／選片遺失；不得據此自動重送 4K |
| stale state | status 唯讀回報 `PROCESS_NOT_OWNED`；若固定 port 確認為 free，下一次 start 只在新 process identity 建立後覆寫該 record，不依 stale PID 終止任何 process |
| repository 搬移 | 搬移前在舊路徑先 stop；新路徑的 controller 因 ownership marker／config 路徑不符而 fail closed。把舊 `.venv`／`.runtime` 移到 repository 外備份，再重跑 install；備份模型可經 explicit external 全 SHA 重用。若 process 運行中就已搬移，先還原原路徑再 stop，或由人類另外確認 owner；新路徑不以 stale identity 停止它 |

任何復原路徑都不得修改 adopted 安裝、全域清 ComfyUI queue、刪除使用者
既有 output，或把服務改綁公開介面。

## 9. Clone-to-run 完成條件

只有以下條件全部成立，才可把此契約標記為已落地：

1. 五個 CLI command 與 `--json` schema 有自動化測試。
2. 預設 managed code、models auto、明確 adopted／external 的所有權邊界
   與零修改檢查通過。
3. 八顆模型可續傳、可全量 SHA 驗證，且錯誤來源 fail closed。
4. 兩套 Python／package／commit pins 可由 preflight 證明；status 可證明
   process identity、health 與 Gateway–ComfyUI 連線。
5. 所有 HTTP listener 維持 loopback，未知 process 不被終止。
6. agent pending 不阻擋既有分鏡；未選定候選不能排程 4K。
7. 至少一次經使用者允許的目標 WSL／NVIDIA GPU smoke 完成並留下不含
   秘密的結果；若未完成，文件與 UI 必須繼續標示「待實機驗證」。
