# Final Project MVP

這是一套只在本機運作的繁體中文故事視覺工作台。你可以從本機圖庫挑選
一個或兩個角色與一個場景，組合成 1–3 張分鏡候選，先挑出真正要的那一
張，再決定是否送去放大成 3840×2160。落選候選不會浪費時間做 4K。

目前角色頁提供二十種同角色風格展示；角色與場景頁都有「確認生成」入口
與持久化本機圖庫。獨立角色生成 agent、場景生成 agent 尚未接入，因此
確認按鈕目前只驗證設定，不會冒充已建立圖片。組員交付 Agent 後，可把
角色四視圖或場景定稿登錄進圖庫；這兩個 pending 插槽不會阻擋手動上傳的
單角色分鏡、圖庫已有素材時的單／雙角色合成，以及「選定後才 4K」流程。
專案已追蹤 `.codex/agents/README.md`，所以日後加入兩份 Agent TOML 並正常
commit 後，GitHub 與其他人的 clone 都會看得到。

> 目前已完成程式、workflow、lock 與自動化測試層的接線；尚未在這台目標
> RTX 5070 Ti 上跑完端到端 GPU 重放。來源機的黃金圖與歷史重放結果保留
> 在 repository，但不能據此宣稱新機輸出已驗證一致。

## 開始前準備

第一個支援目標固定為：

- Windows 11。
- WSL2、Ubuntu 24.04 LTS、x86_64。
- NVIDIA GeForce RTX 5070 Ti 16 GB；Windows NVIDIA driver 必須讓
  Ubuntu 內的 `nvidia-smi` 看得到 GPU。
- 可連線至 GitHub、Hugging Face 與 PyTorch wheel 來源；目前不是離線
  安裝包。
- 建議至少保留 **65 GiB** 可用磁碟空間。八顆模型本身共
  47,266,047,406 bytes，約 47.27 GB／44.02 GiB，另外還需要 ComfyUI、
  Python、PyTorch、cache 與下載中的 `.part`。

建議把 repository clone 到 WSL 的 Linux filesystem，例如
`~/projects/final-project-mvp`。`\\wsl.localhost\Ubuntu-24.04\...` 是
Windows 顯示同一位置的方式；程式內仍使用 `/home/...` 路徑。不要把主要
安裝放在 `/mnt/c`，以免遇到 I/O、權限與 executable bit 問題。

Ubuntu 內至少需要 `git` 與 `python3`。runtime controller 會從固定 URL
下載並驗證釘版的 `uv`，不需要先執行 curl-pipe 安裝器，也不會替你安裝
或更新 Windows driver、WSL kernel。

### 完全從零：先準備 WSL、GPU 與 Codex

已經能在 Ubuntu 24.04 內執行 `nvidia-smi` 與 `codex` 的使用者，可以
直接跳到下一節。

1. 以系統管理員身分開啟 Windows PowerShell，依
   [Microsoft 的 WSL 安裝說明](https://learn.microsoft.com/windows/wsl/install)
   安裝 Ubuntu 24.04，重新開機後建立 Linux 使用者：

   ```powershell
   wsl --list --online
   wsl --install -d Ubuntu-24.04
   wsl --list --verbose
   ```

   最後一個命令應顯示該 distribution 使用 `VERSION 2`。
2. 安裝支援 WSL 的 Windows NVIDIA driver；在 Ubuntu 終端執行
   `nvidia-smi`，必須能看到 RTX 5070 Ti。可參考
   [Microsoft 的 WSL GPU 指南](https://learn.microsoft.com/windows/wsl/tutorials/gpu-compute)。
   不要另外在 Ubuntu 內安裝一套 Linux 顯示驅動。
3. 在 Ubuntu 內準備基本工具：

   ```bash
   sudo apt update
   sudo apt install -y git python3
   ```

4. 依 [OpenAI Codex CLI 官方入門](https://help.openai.com/en/articles/11096431)
   準備 Node.js／npm 並安裝 Codex；接著登入：

   ```bash
   npm install -g @openai/codex
   codex login
   ```

   若你使用的 Codex 版本直接在首次啟動時引導登入，照畫面完成即可。
   API key 或登入憑證不要寫進 repository。

## 最短使用流程：交給 Codex

先在 Ubuntu 內 clone：

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/shi-tong-chang/final-project-mvp.git
cd final-project-mvp
codex
```

`codex` 必須從 repository 根目錄啟動。進入對話後，依序輸入下列四句話：

| 輸入給 Codex | 預期結果 |
|---|---|
| `安裝環境` | 建立釘版 Gateway／ComfyUI 環境，尋找或下載八顆模型，再以 install SHA receipt 做快速 preflight |
| `啟動` | 完整預檢通過時啟動 loopback ComfyUI 與 Gateway；ComfyUI／GPU 不可用時只保留網站並明示 `degraded`，不能生成 |
| `狀態` | 對帳 PID、health、模型大小與目前 managed／adopted 狀態，不啟動新服務 |
| `停止` | 只停止這個 repository 記錄並擁有的 process |

第一次安裝會下載大型模型與 CUDA wheel，時間取決於網路與磁碟速度。下載
中斷後再次輸入「安裝環境」即可延續合法的 `.part`；模型完成前不會被當成
可用檔案。

網站本身不會把按鈕或 HTTP request 轉成 shell／Codex 命令。上面四句是
使用者在 Codex 對話中下達的本機維運命令；Browser 只透過 typed FastAPI
操作固定 workflow。

## 預設安裝會做什麼

不加參數的 `install` 使用兩個獨立策略：

1. **ComfyUI code 使用 managed。** Runtime 只在 Git-ignored
   `.runtime/comfyui/` 建立釘定的 ComfyUI 與唯一需要的
   `ComfyUI-GGUF`，不會自動採用或修改你原本的 `$HOME/ai/ComfyUI`。
2. **模型使用 auto。** Runtime 會在明確候選位置尋找完整模型根目錄，
   包含 `FPMVP_MODEL_ROOT`、`$HOME/ai/ComfyUI/models` 與
   `$HOME/ComfyUI/models`；若這兩個已知 Comfy root 的 machine-local YAML
   有絕對 `base_path`，也只把它的 canonical `base_path/models` 當候選。
   來源 YAML 不會交給 managed ComfyUI 執行。只有八顆檔案的大小與完整
   SHA-256 全部符合 [`runtime/models.lock.json`](runtime/models.lock.json)，
   才會唯讀採用 external models；否則下載到 `.runtime/models/`。

外部模型是全套採用，不會把「有幾顆相同、幾顆不同」的目錄混進正式
runtime，也不會改名、覆寫或補下載至外部 ComfyUI。

環境彼此隔離：

| Process | Python | 位置與責任 |
|---|---:|---|
| FastAPI Gateway | 3.12.10 | repository 的 Git-ignored `.venv/` |
| managed／adopted ComfyUI | 3.12.3 | 自己的 venv，使用 torch 2.12.0+cu130 |

兩個 process 只用 `127.0.0.1` HTTP 溝通，不共用 site-packages。Gateway
固定是 8010；ComfyUI 固定是 8188。

## 想明確採用既有 ComfyUI

預設不會自動 adopted。若要使用現有的
`$HOME/ai/ComfyUI`，請明確指定：

```bash
python3 scripts/fpmvp_runtime.py install \
  --comfy-mode adopted \
  --comfyui-root "$HOME/ai/ComfyUI" \
  --comfyui-python "$HOME/ai/ComfyUI/.venv/bin/python"
```

adopted 是唯讀契約。只有 ComfyUI core commit 與受 Git 追蹤的檔案未被
修改、ComfyUI-GGUF commit／worktree 嚴格符合、Python 3.12.3 與套件 pin
符合時才可使用。其他 untracked custom node 可以留在原處，但啟動時會
全部停用；runtime 不會 checkout、`pip install`、安裝 node 或更新
Manager。來源 root 不能有實際的 `extra_model_paths.yaml`：釘版 ComfyUI
會在 runtime 設定之前先載入它，為避免同名模型被其他路徑搶先解析，CLI
會明確 fail closed。ComfyUI 的 cwd、base、input／output／temp／user、
HOME、cache 與 logs 全部導向本專案 `.runtime/`。

如果模型在另一個根目錄，可要求只採用該 external root：

```bash
python3 scripts/fpmvp_runtime.py install \
  --comfy-mode adopted \
  --comfyui-root "$HOME/ai/ComfyUI" \
  --models-mode external \
  --model-root "$HOME/ai/ComfyUI/models"
```

模型也可以獨立重用，不必 adopted 既有 ComfyUI code。若你的八顆模型在
其他資料庫位置，通常最穩定的做法是保留預設 managed code，只指定模型
根目錄：

```bash
python3 scripts/fpmvp_runtime.py install \
  --models-mode external \
  --model-root "/你的/模型根目錄"
```

這個根目錄底下必須直接包含 lock 指定的 `unet/`、`clip/`、`vae/`、
`diffusion_models/`、`upscale_models/`；CLI 會先重算 8/8 SHA-256，
不會採用只符合檔名或大小的模型。若路徑位於 Windows 磁碟，請傳入 WSL
看得到的 `/mnt/...` 路徑，不要傳 `\\wsl.localhost\...`。

任一 pin 或任一模型 SHA 不符時，explicit adopted／external 會 fail
closed，並保持原目錄不變。若只是想讓預設模式優先檢查一個模型根目錄：

```bash
FPMVP_MODEL_ROOT="$HOME/ai/ComfyUI/models" \
  python3 scripts/fpmvp_runtime.py install
```

## 手動操作

### 在 WSL／Ubuntu 執行

請從 repository 根目錄執行：

```bash
# 安裝；預設 managed ComfyUI code + auto models
python3 scripts/fpmvp_runtime.py install

# 快速唯讀預檢；核對 exact bytes 與 install SHA receipt
python3 scripts/fpmvp_runtime.py preflight

# 需要時才重新掃描全部 47.27 GB 的 SHA-256
python3 scripts/fpmvp_runtime.py preflight --full

python3 scripts/fpmvp_runtime.py start
python3 scripts/fpmvp_runtime.py status
python3 scripts/fpmvp_runtime.py stop
```

用 `--dry-run` 可先看預定動作；用穩定 JSON 給自動化讀取時，`--json`
放在子命令前：

```bash
python3 scripts/fpmvp_runtime.py install --dry-run
python3 scripts/fpmvp_runtime.py --json status
```

若只想展示不需要 GPU 的首頁與 catalog：

```bash
python3 scripts/fpmvp_runtime.py start --gateway-only
```

### 在 Windows PowerShell 執行

若 repository 是從 Windows PowerShell 開啟，可使用 wrapper 把同一組參數
轉交給 WSL：

```powershell
.\scripts\runtime.ps1 install
.\scripts\runtime.ps1 preflight
.\scripts\runtime.ps1 preflight --full
.\scripts\runtime.ps1 start
.\scripts\runtime.ps1 status
.\scripts\runtime.ps1 stop
```

有多個 WSL distribution 時，可先設定 `FPMVP_WSL_DISTRO`。不要直接用
Windows Python 執行 Linux runtime。

```powershell
$env:FPMVP_WSL_DISTRO = "Ubuntu-24.04"
.\scripts\runtime.ps1 status
```

## 網站怎麼用

Gateway ready 後（包括整體仍為 `degraded`）會 best-effort 開啟
Browser；若 WSL 無法代開，請手動開啟 <http://127.0.0.1:8010>：

1. 在角色風格櫥窗查看二十種同角色媒材，填寫角色描述並確認生成設定；
   角色 Agent 尚待接入，已有的正式角色會以「前／左／右／後」四視圖顯示
   在角色圖庫。
2. 在場景頁填寫地點、光線、尺度與構圖方向並確認設定；場景 Agent 尚待
   接入，已有的正式場景會顯示在場景圖庫。
3. 進入分鏡頁，選擇一個或兩個角色，再選擇恰好一個場景。第一個被選的
   角色是角色一；第二個是角色二，取消後重選即可調整順序。
4. 圖庫沒有素材時，切換到「手動上傳」仍可上傳一張場景圖與一張角色
   **正面**參考圖；這條 fallback 只支援單角色。
5. 輸入合成描述，建立 1–3 張約 1 MP 最終候選。系統會自行判斷：一個
   角色跑 `wf_dual_B1`；兩個角色依序跑
   `wf_dual_B1` → `wf_dual_B2`。網頁不能自行指定 workflow、node 或 seed。
6. 檢視每張候選與各階段 seed，明確選定真正要保留的一張。
7. 需要時填寫 4K 精修描述，再把目前選定候選送入固定
   `wf10_upscale_opt2`；未選與落選候選都不會被放大。

未選定、尚未完成、舊分頁持有的過期 candidate ID 或落選候選，都不能
啟動 4K。4K 固定是 16:9、3840×2160；非 16:9 來源會置中裁切。分鏡任務
與候選由單一 Gateway process 暫存，Gateway 重啟後不保留；已登錄的角色
與場景則保存在 `.runtime/asset-library/`，重啟後仍會出現在圖庫。

「生成角色」與「生成場景」目前是展示／預留入口；對應
`.codex/agents/character_generator.toml` 與
`.codex/agents/scene_generator.toml` 尚未交付，因此不能把示意預覽當成
正式生成結果。「確認生成」只提供未來接線位置，圖庫也不會在尚未產出
圖片時新增假紀錄。這不影響使用者自行上傳角色正面圖與場景圖完成現有
分鏡。

### 把正式圖片登錄進圖庫

這是給未來角色／場景 Agent controller 使用的 trusted CLI，也可用來把
你已經完成的本機圖片匯入圖庫。它不需要把檔案上傳到外站；登錄時會檢查
並重新編碼成安全 PNG，再回傳 server-owned asset ID。

角色包必須一次提供完整四視圖：

```bash
.venv/bin/python scripts/register_generated_asset.py character \
  --name "角色名稱" \
  --description "用來辨識這份角色模板的簡短描述" \
  --front "/path/to/front.png" \
  --left "/path/to/left.png" \
  --right "/path/to/right.png" \
  --back "/path/to/back.png"
```

場景只需一張定稿圖：

```bash
.venv/bin/python scripts/register_generated_asset.py scene \
  --name "場景名稱" \
  --description "場景、時段與光線的簡短描述" \
  --image "/path/to/scene.png"
```

成功時 CLI 會輸出 JSON。重新整理網站後，角色或場景會出現在各自圖庫及
分鏡素材選擇區。原始來源路徑不會寫進 metadata；正式副本保存在
`.runtime/asset-library/`，整個目錄已被 Git 排除。

## 狀態、logs 與常見排錯

先執行：

```bash
python3 scripts/fpmvp_runtime.py status
```

主要本機檔案都被 Git 排除：

- `.runtime/logs/gateway.log`：FastAPI 啟動與錯誤。
- `.runtime/logs/comfyui.log`：ComfyUI、CUDA、模型載入與 custom node。
- `.runtime/config.json`：目前選到的 code／model ownership 與路徑，不含
  secrets。
- `.runtime/state/processes.json`：runtime-owned PID identity。
- `.runtime/state/models.receipt.json`：上次完整 SHA 驗證與檔案 metadata；
  metadata 或 model lock 改變後 quick preflight 會要求重新驗證。
- `.runtime/models/`：managed models；下載中檔案以同目錄 `.part` 保存。
- `.runtime/comfy-data/`：受控的 input、output、temp 與 user data。
- `.runtime/asset-library/`：已登錄的角色四視圖與場景定稿；不會進 Git。

常見情況：

- **`nvidia-smi` 在 Ubuntu 失敗：**先修復 Windows NVIDIA driver 與
  WSL GPU 支援；runtime 不會代裝 driver。
- **磁碟不足：**保留合法 `.part`，釋放空間後重跑 `install`。不要把
  模型加入 Git。
- **external models 驗證失敗：**確認八顆檔案都位於 lock 指定子目錄；
  檔名與大小相同仍不夠，SHA-256 也要相同。
- **adopted 驗證失敗：**既有 ComfyUI 不會被修；改用預設 managed，或
  由使用者在專案外自行整理版本。Tracked 修改、GGUF pin、Python／torch
  pin 或 source `extra_model_paths.yaml` 任一不符都會被拒絕；只想共用
  模型時請改用 managed code 搭配 `--models-mode external`。
- **8010／8188 被占用：**runtime 不會 kill 未知 process。先用 `status`
  確認，再由使用者處理真正的 owner。
- **模型下載中斷：**重跑 `install` 會嘗試安全 Range 續傳；若來源對 Range
  合法回傳完整 `200`，runtime 會 truncate 該 managed `.part` 並自動從零
  重抓。異常 `Content-Range`／大小／SHA 則 fail closed。
- **managed 模型損壞：**不合格 final 或完整但 SHA 錯誤的 `.part` 會在
  同目錄隔離為 `.fpmvp-rejected-*`，再重抓正確檔案；隔離檔不會被發布
  給 ComfyUI，但仍占磁碟，確認不需保留證據後可由人類清理。
- **ComfyUI 未就緒：**首頁與角色風格 catalog 仍可使用；GPU workflow
  顯示安全錯誤，不會把 raw payload 或本機路徑送到 Browser。
- **準備搬動 repository：**務必在舊位置先執行 `stop`，再搬動整個
  repository。舊 `.venv` 與 `.runtime` 含絕對路徑，新位置的 controller
  會 fail closed，不會自動改寫；把兩個 Git-ignored 目錄移到 repository
  外備份，再於新位置重跑 `install`。備份中的 `models/` 可用
  `--models-mode external --model-root <path>` 重新完整驗證並重用。若在
  process 尚運行時就已搬移，先把 repository 還原到原路徑執行 `stop`，
  或由人類另外確認真正 owner；不要期待新路徑的 stale identity 能停止
  它，也不要跨位置直接沿用舊 venv 或 config。

## 安全邊界

- Gateway 與 ComfyUI 只綁 `127.0.0.1`；這套網站沒有登入系統，禁止改成
  `0.0.0.0`。
- Browser 不直接連 ComfyUI，也不能提交 workflow JSON、node ID、模型、
  seed、ComfyUI 路徑、server filename 或 shell command。
- Runtime 不呼叫 `/free`、不全域清 queue、不自動安裝 Manager 套件，
  `stop` 也不終止未由本 repository 擁有的 process。
- API key 與登入憑證只放環境，不進 Git、catalog、logs 或 status JSON。
- adopted 安裝不被修改；managed ComfyUI、模型與執行資料只留在
  Git-ignored `.runtime/`。

## 開發驗證

安裝完成後，從 repository 根目錄執行：

```bash
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend runtime scripts tests
node --check frontend/gateway/app.js
git diff --check
```

Browser E2E 另外需要 Playwright Chromium 與 Ubuntu 系統相依：

```bash
sudo .venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium
.venv/bin/pytest tests/e2e/test_codex_gateway_page.py
```

`install-deps` 會修改 Ubuntu 系統套件，因此只在明確要跑 Browser E2E 時
執行；一般啟動網站不需要 sudo。

更詳細的規格與重現資料：

- [產品契約](docs/PROJECT_SPEC.md)
- [Clone-to-run 契約與驗收](docs/tasks/CLONE_TO_RUN.md)
- [分鏡與選定後 4K 基礎接線](docs/tasks/STORYBOARD_WORKFLOW_INTEGRATION.md)
- [本機圖庫與單／雙角色路由](docs/tasks/ASSET_LIBRARY_AND_DUAL_STORYBOARD.md)
- [工作流、模型與黃金驗證資料](docs/README_repro.md)
