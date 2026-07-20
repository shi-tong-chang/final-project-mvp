# repro_pack — 人物×場景合成與 4K 放大異機重現補件包

> 產生：2026-07-21，整理自來源機的歷史正本庫。
> 對應彙編文件：
> [`REF_人物場景合成與4K放大.md`](./workflows/REF_人物場景合成與4K放大.md)。
> 本專案目前附有 13 份 workflow JSON、兩份 manifest 與兩組黃金驗證
> 資料（圖片共約 14 MB）。
>
> 原始 manifest 內保留的 `C:\Users\...` 路徑只作 provenance，不是本
> 專案執行路徑。安裝與執行一律由 `.runtime/config.json`、repository
> 相對路徑或後端資產 ID 解析，不得寫死來源機個人路徑。

---

## 1. 環境釘版(repository / commit)

| 組件 | 來源 | 版本 / commit |
|---|---|---|
| ComfyUI 本體 | https://github.com/Comfy-Org/ComfyUI.git | `ab0d8a9203fbad76b0ccca723bbf9ba0c257ddfe`(v0.24.0-11-gab0d8a92) |
| ComfyUI-GGUF（四個主 workflow 唯一必裝的 custom node，提供 `UnetLoaderGGUF`） | https://github.com/city96/ComfyUI-GGUF | `cf0573351ac260d629d460d97f09b09ac17d3726` |
| Gateway Python | — | 3.12.10（獨立 venv） |
| ComfyUI Python | — | 3.12.3（獨立 venv） |
| PyTorch | — | 2.12.0+cu130(CUDA 13.0) |

依賴說明：`wf10`（4K 放大）全部是上述 ComfyUI 的核心節點；
`wf02`（修正版）、`wf_dual_B1`／`wf_dual_B2` 只額外需要
ComfyUI-GGUF。`ComfySwitchNode` 本身也存在於釘定的 ComfyUI core，
但修正版 wf02 已連同 Primitive 快速分支完全移除。來源機另裝的 30+
custom-node 套件與這四個主 workflow 無關，不必安裝。

選用的 `wf03_matte.json` 例外：其 `BiRefNetRMBG` 不在上述 core 或 GGUF
repository，目前仍缺來源 repository／commit 與模型 pin；未補齊前不能
把去背流程列為可重現主流程。

目前 [`runtime-lock.json`](../runtime/runtime-lock.json) 已另外固定
bootstrap uv 的 release asset／SHA-256、Gateway 與 ComfyUI Python、
PyTorch wheel index、`torchvision`／`torchaudio` 版本與 loopback port；
[`comfy-requirements.lock.txt`](../runtime/comfy-requirements.lock.txt) 以
hash 鎖住 ComfyUI／GGUF 的 Python transitive dependencies。這仍不是跨機
bit-for-bit 保證：Windows NVIDIA driver、WSL kernel、GPU 與底層 CUDA
執行差異不由 repository pin。

Clone 後的 canonical controller 是 `scripts/fpmvp_runtime.py`。預設建立
managed ComfyUI code；只有八顆 SHA 全數符合時才唯讀採用 external
models。使用者既有 ComfyUI code 不會自動被 adopted，必須明確使用
`--comfy-mode adopted --comfyui-root <path>`，且所有 code／Python pin
通過、source `extra_model_paths.yaml` 不存在。只想重用既有模型時，
建議保留 managed code，另以 `--models-mode external --model-root <path>`
指定單一 canonical model root。完整契約見
[`CLONE_TO_RUN.md`](./tasks/CLONE_TO_RUN.md)。

## 2. 模型清單(SHA-256 為身分依據)

安裝位置是 model root 下的子資料夾；model root 可以是完整 SHA 驗證後
唯讀採用的 external root，或 `.runtime/models/`。來源機當年未保存
revision，後續已用既有 SHA-256 反查並固定目前可精確對應的下載
revision；SHA-256 仍是檔案身分的最終 authority。`unet/` 與 `clip/` 是
此 ComfyUI commit 仍支援的 legacy 路徑。來源／授權是已知最佳判斷，正式
商用前仍須以實際下載頁確認。

| 檔名 | 位置 | 大小(MB) | SHA-256 | 來源(已知最佳) | 授權 |
|---|---|---|---|---|---|
| qwen-image-edit-2511-Q6_K.gguf | unet/ | 16072 | FDC28E5B8F7D9CFE0399FD1700C375F25F000FC4159BBDB0D4A809AE898EB759 | unsloth/Qwen-Image-Edit-2511-GGUF（exact blob revision 已釘定） | Apache-2.0 |
| qwen_2.5_vl_7b_fp8_scaled.safetensors | clip/ | 8950 | CB5636D852A0EA6A9075AB1BEF496C0DB7AEF13C02350571E388AEA959C5C0B4 | Comfy-Org/Qwen-Image_ComfyUI(split_files/text_encoders) | Apache-2.0 |
| qwen_image_vae.safetensors | vae/ | 242 | A70580F0213E67967EE9C95F05BB400E8FB08307E017A924BF3441223E023D1F | Comfy-Org/Qwen-Image_ComfyUI(split_files/vae) | Apache-2.0 |
| z_image_turbo_bf16.safetensors | diffusion_models/ | 11740 | 2407613050B809FFDFF18A4AC99AF83EA6B95443ECEBDF80E064A79C825574A6 | Comfy-Org Z-Image 重打包(Tongyi-MAI/Z-Image-Turbo) | Apache-2.0 |
| qwen_3_4b.safetensors | clip/ | 7672 | 6C671498573AC2F7A5501502CCCE8D2B08EA6CA2F661C458E708F36B36EDFC5A | Comfy-Org split_files/text_encoders(Qwen3-4B,Z-Image 文字編碼器) | Apache-2.0 |
| ae.safetensors | vae/ | 320 | AFC8E28272CD15DB3919BACDB6918CE9C1ED22E96CB12C4D5ED0FBA823529E38 | FLUX.1 VAE(black-forest-labs / Comfy-Org 重打包;Z-Image 放大鏈用) | schnell 版=Apache-2.0(以下載頁為準) |
| RealESRGAN_x2plus.pth | upscale_models/ | 64 | 49FAFD45F8FD7AA8D31AB2A22D14D91B536C34494A5CFE31EB5D89C2FA266ABB | github.com/xinntao/Real-ESRGAN releases | BSD-3-Clause |
| RealESRGAN_x4plus_anime_6B.pth | upscale_models/ | 17 | F872D837D3C90ED2E05227BED711AF5671A6FD1C9F7D7E91C911A61F155E99DA | github.com/xinntao/Real-ESRGAN releases | BSD-3-Clause |

合成線用前 3 顆；4K 放大線用後 5 顆，合計 47,266,047,406 bytes，約
47.27 GB。

八顆模型的精確 HTTPS URL、Hugging Face commit／GitHub release tag、
exact bytes、SHA-256、安裝子目錄、授權與 `required_by` 已收錄在
[`models.lock.json`](../runtime/models.lock.json)，可供 installer 續傳
下載並在 atomic publish 前驗證。預設安裝會先對 external model root
完整計算八顆 SHA；全數符合才唯讀採用，否則下載 managed copies，不會
修改外部 ComfyUI。

`Comfy-Org/z_image_turbo` distribution card 本身沒有 license metadata；
表內 Apache-2.0 依各上游 Z-Image-Turbo、Qwen3-4B 與 exact-byte
FLUX.1-schnell VAE 判定，商用前仍應再次核對下載頁。選用去背模型
`BiRefNet-HR-matting` 尚未列入，因其 custom node 與模型來源仍未釘定，
也不屬於目前 clone-to-run 主流程。

## 3. manifests／產品接線

- [`manifest.json`](./manifests/manifest.json) — 壁紙線 v3–v18 全
  stage：每輪 seed、逐字 prompt、配置與勝敗裁決，包含 4K 放大鏈實跑
  參數。
- [`manifest_dual.jsonl`](./manifests/manifest_dual.jsonl) —
  雙角色鏈式兩輪戰役全程紀錄。

這兩份檔案是來源機歷史紀錄；其中的 Windows 絕對路徑不具可移植性，不得
直接用來開檔。重放時以本 README 的相對連結、黃金檔 SHA-256 與 workflow
內的 server input alias 建立對應。

### 產品接線原則

1. 單角色合成使用
   [`wf_dual_B1.json`](./workflows/wf_dual_B1.json)
   作通用模板，執行前改寫兩個 `LoadImage`、正向 prompt、seed 與輸出
   prefix。不得沿用 MARCO 範例內容。
2. [`wf02_insert.json`](./workflows/wf02_insert.json) 保留作 I2
   固定裁框重放；其 x700／y150／720×506 裁框不適合任意場景。
3. 雙角色先跑 B1，選出第一輪勝者後再套
   [`wf_dual_B2.json`](./workflows/wf_dual_B2.json)。
4. 候選一律維持約 1MP；只有使用者選定的勝者才送進
   [`wf10_upscale_opt2.json`](./workflows/wf10_upscale_opt2.json)
   做 4K，避免對落選圖浪費約 90 秒。

## 4. golden／黃金驗證圖對

### 4a. 合成(雙圖插入,鏈式兩輪)

| 檔案 | 角色 |
|---|---|
| [`insert/scene_content.png`](./golden/insert/scene_content.png) | 場景圖（image1，輪 1） |
| [`insert/ref_marco_front.png`](./golden/insert/ref_marco_front.png) | 角色 A 參考（image2，輪 1；三視圖已裁單視角） |
| [`insert/b1_winner_seed7102.png`](./golden/insert/b1_winner_seed7102.png) | **輪 1 已驗證輸出**（wf_dual_B1，seed 7102），也是輪 2 場景輸入 |
| [`insert/ref_edri_front.png`](./golden/insert/ref_edri_front.png) | 角色 B 參考（image2，輪 2） |
| [`insert/dualB2_winner_seed7202.png`](./golden/insert/dualB2_winner_seed7202.png) | **輪 2 已驗證輸出**（wf_dual_B2，seed 7202，最終推薦成品） |

驗證法：用 `wf_dual_B1.json`（seed 改 7102）重跑，再與 B1 黃金圖
比對；上傳該勝者時使用 server alias `b1_winner.png`，接著以
`wf_dual_B2.json`（seed 7202）重跑並與 B2 黃金圖比對。參數固定為
20 步／CFG 2.5／euler／simple／denoise 1.0／shift 3.1。

### 4b. 4K 放大(wf10 opt2 鏈)

| 檔案 | 角色 |
|---|---|
| [`upscale4k/input_1MP_seiza_seed966869739023339.png`](./golden/upscale4k/input_1MP_seiza_seed966869739023339.png) | 輸入（約 1MP 定稿） |
| [`upscale4k/output_4K_ducksit_FINAL_seed966869739023339.png`](./golden/upscale4k/output_4K_ducksit_FINAL_seed966869739023339.png) | **已驗證輸出**（3840×2160） |

驗證時不能原封送出 `wf10_upscale_opt2.json`，因 node `4` 的
`user_prompt` 是通用佔位文字。必須：

1. 從 `manifest.json` 的
   `stage_v14_ducksit.finish_opt2.refine_prompt` 取得完整鴨子坐描述，
   注入 node `4`；或使用從黃金 PNG 內嵌 `prompt` metadata 取出的固定
   golden fixture graph。
2. 把輸入圖上傳為 `upscale4k_src.png`。
3. 保持 seed 676767676701、denoise 0.23、中繼 2672×1504、8 steps、
   CFG 1.0、`res_multistep`／`simple` 與最終 3840×2160。

**此圖對 2026-07-16 在來源機重放實測 maxdiff=0(像素級一致)。**

跨 GPU、driver 或 torch 版本重放不保證位元級一致。同機可用
`maxdiff=0` 驗證；異機目前只能人工核對低 MSE 與結構一致，正式自動驗收
前仍需定義數值門檻。

### 包內檔案完整性(SHA-256)

```
workflows/wf02_insert.json                                    1FFCFB07B752DB10E93543B102FE3C06F84500F20F78F1F944EC812BA5AC4378
workflows/wf03_matte.json                                     EAF3AD51FA26BC6AEBDD01349631770CFD663DF102A6AAD77AAE3F8890465EBE
workflows/wf10_upscale_opt2.json                              A141D9988A617680C282A1C3DF5FB93E3D49E4B311CE36B448E6FBC3DD81756E
workflows/wf_dual_B1.json                                     CEEFD5844CAB5F10368F8999D6362551B43EDD92743AC36000FB365C6AE5C1C8
workflows/wf_dual_B2.json                                     D6E1E051D801E60E4CA6F8AC0607294E3C83FC72204676835086BAD8D1DF1CB2
workflows/t2i/dual_char_scene/wf_dual_A.json                  DE7E6196816F5EF06A79141C63E28569C84A2D32FCD7D09AEA77979BC899EC3C
workflows/t2i/scripts_and_workflows/Qwen_Edit_2511_00009__prompt.json A3334C1E4F444E5DBC022B04BCA3AB5BA640776B002DFABFADA8B68FE200CAF4
workflows/wallpaper_4k/WP4KD_workflow_chain/01_v7_pure+enhanced_雙軌放大.json 1998C9E7FDCEAD2E0CBEDBB118EE5097B7AC9223EA09955AA1EA37F77AE64BD9
workflows/wallpaper_4k/WP4KD_workflow_chain/03_v8_soft_x2plus柔化鏈.json D50AFA50C25F799D4DC47B35C48508914AD5E21302F86B23301A49D886A76EDA
workflows/wallpaper_4k/WP4KD_workflow_chain/04_v9_胸口inpaint.json EA396FEB45500B02DDE55E914B4F91F21F5D57A4DF7BCCE51CBA2270502AD009
workflows/wallpaper_4k/WP4KD_workflow_chain/05_v10_手臂armfix_inpaint.json 29B88876418287FD27908BC1EF683827A704A1E3C2B099ECBF8FA1313AB23C8C
workflows/wallpaper_4k/WP4KD_workflow_chain/06_v13_opt2清晰化定稿.json 5CF10877614657E62B09578EB5BF295300EB5818924BA935599B774BBC766F8F
workflows/wallpaper_4k/WP4KD_workflow_chain/A1_附錄_wf10_upscale_opt2_現行標準版.json A141D9988A617680C282A1C3DF5FB93E3D49E4B311CE36B448E6FBC3DD81756E
manifests/manifest.json                                       19AAB969AEBD0EC3F68A27B022BF87419015D2A0F98E533D58A42A7CD9A122A3
manifests/manifest_dual.jsonl                                 A1D674B2CEF03353C408AC42814C664F9F17CAD31506BFF22E0325946E80D239
golden/insert/scene_content.png                               224D90734D3B52C6862F9A6281CCF54C7B88615FEB2A63E7786B484659CDC046
golden/insert/ref_marco_front.png                             71DD2A1AE298B63D1B75FB662ECDDE61C2CE1E5F26C3967762BFAC2B4AC1A1E5
golden/insert/ref_edri_front.png                              A5806B53A02F97045BA869A3AD6E4AF952833BA51C4765F3F6C73A5EDED55D22
golden/insert/b1_winner_seed7102.png                          A2A8DA5E651EC9B71DFC73A067B9E5F5B691AF4F0F705186DDB394A1E6AC878D
golden/insert/dualB2_winner_seed7202.png                      F4D315B713EDBCE05E8F6D14C22C8C569166F24F23356781C86A5965E28D39BF
golden/upscale4k/input_1MP_seiza_seed966869739023339.png      068A28269EA2ECC74E6E8A18DC18CF4458DE791C02A5B67BB963708FF459FCFE
golden/upscale4k/output_4K_ducksit_FINAL_seed966869739023339.png  D902EADA7839740BA5D011E64F5F461176BEC6BE23A5B50585DC9558C98EDA99
```

## 5. Lightning 4-step 裁決 =(b)拔除

[`workflows/wf02_insert.json`](./workflows/wf02_insert.json) 是
2026-07-21 修正版，應取代先前的 26-node wf02：

- 原版的布林「快檔開關」是半成品——切 true 只改 4 步/CFG 1.0,graph 內**沒有** Lightning LoRA 節點,開了必出壞圖。
- 修正版共 18 nodes，把 Switch／Primitive 節點整組拔除，KSampler 固定
  **20 步／CFG 2.5**。`ComfySwitchNode` 在釘定 ComfyUI 中本來就是
  core node；移除原因是刪除錯誤分支，不是為了解決第三方套件依賴。
- 日後若要 4 步草稿模式,正確接法:`UnetLoaderGGUF → LoraLoaderModelOnly(Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors, 強度 1.0) → ModelSamplingAuraFlow(3.1)`,KSampler 改 4 步 / CFG 1.0(此時負向失效);終稿仍回 20 步無 LoRA。

## 附:重放共同須知

1. graph 內 `LoadImage` 引用 **server input 檔名**,跑前先 `POST /upload/image`(multipart,`overwrite=true`)上傳同名。
2. 中文 prompt 一律寫在 UTF-8 JSON 檔內直接 POST,不要經命令列傳參。
3. denoise 必須 1.0(編輯模型);正負兩顆 TextEncodeQwenImageEditPlus 都要接同樣的參考圖。
