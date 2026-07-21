# REF：人物×場景合成與 4K 放大

> **文件性質**：2026-07-20 彙整的技術快照，記錄「人物×場景合成」與
> 「定稿後 4K 放大」兩條處理鏈。
>
> **範圍**：收錄單人合成、雙人鏈式合成、選用的去背／拆層步驟，以及
> 4K 放大；不收錄單獨生成人物、A-pose、角色多視角或角色資產化流程。
>
> **目前狀態**：現役、選用與歷史附件共 13 份 workflow JSON 已放入
> `docs/workflows/`；環境釘版、模型表、兩份 manifest 與兩組黃金驗證圖
> 也已加入本專案。尚未移入的是舊自動化腳本、部分活文件與非黃金歷史
> 產物。本文件只連結 repository 內確實存在的附件。

## 路徑與狀態標示

- `./檔名`：本文件同資料夾內的實際附件，可直接點擊。
- `../檔名`：位於 `docs/` 內的重現說明、manifest 或黃金驗證圖。
- `server input`：ComfyUI `LoadImage` 使用的執行期檔名，不是 repository
  路徑；正式接線時應由後端上傳並改寫。
- `${COMFYUI_ROOT}`：使用者電腦上的 ComfyUI 根目錄，禁止寫死 Windows
  使用者名稱或磁碟路徑。
- 原始 manifest 內的 `C:\Users\...` 路徑只保留為來源機 provenance，
  不得當成安裝或執行路徑。

## 現役附件速查

| 需求 | 使用順序 | 狀態 |
|---|---|---|
| 單人放入場景（產品接線） | [`wf_dual_B1.json`](./wf_dual_B1.json) | 當通用單角色模板，執行前改寫場景、角色、prompt、seed |
| 單人固定裁框重放 | [`wf02_insert.json`](./wf02_insert.json) | I2 歷史配方，含固定裁框 |
| 雙人放入場景 | [`wf_dual_B1.json`](./wf_dual_B1.json) → [`wf_dual_B2.json`](./wf_dual_B2.json) | JSON 已附 |
| 拆層／去背 | [`wf03_matte.json`](./wf03_matte.json) | 已附，選用 |
| 選定候選後放大至 4K | [`wf10_upscale_opt2.json`](./wf10_upscale_opt2.json) | JSON 已附；比稿階段不執行 |

> **產品接線裁決**：`wf02_insert.json` 的裁框座標與尺寸是 I2 歷史案例，
> 不適合作為任意場景的預設。產品端以沒有固定裁框的 `wf_dual_B1.json`
> 作為單角色通用模板；需要第二位角色時，再接 `wf_dual_B2.json`。每輪先
> 產生約 1MP 候選，使用者選定後才把勝者送進 `wf10_upscale_opt2.json`。
>
> 環境與黃金重放入口見
> [`README_repro.md`](../README_repro.md)。

---

## 處理鏈 A：人物×場景合成（Qwen-Edit-2511 雙圖插入）

### 1. 一句話定位

「把指定角色放進指定場景」= 2511 一次吃兩張參考圖(image1=場景、image2=角色),自己畫光影、接觸陰影、透視融合。使用者說成品「不像」時,常常要的就是這條(不是風格模仿問題)。

### 2. 定案配置（定案值）

| 項目 | 值 |
|---|---|
| 主模型 | `UnetLoaderGGUF(qwen-image-edit-2511-Q6_K.gguf)` |
| Text Encoder | `CLIPLoader(qwen_2.5_vl_7b_fp8_scaled.safetensors, type=qwen_image)` |
| VAE | `qwen_image_vae.safetensors` |
| 取樣前置 | `ModelSamplingAuraFlow(shift=3.1)` + `CFGNorm(1.0)` |
| KSampler | **euler / simple / 20 步 / CFG 2.5 / denoise 1.0**(denoise 必 1.0——編輯模型身份靠 reference latents,低 denoise=輸出複寫輸入) |
| Latent 來源 | 場景圖經縮放後 `VAEEncode`(輸出尺寸跟場景走,~1MP) |
| 負向 | prompt 留空,但 `TextEncodeQwenImageEditPlus` **正負兩顆都要接同樣的 image1/image2** |
| 時間 | ~240 秒/張(雙參考比單參考慢 +35%);冷載首張可到 445s |
| Seeds | 一律 3 seeds 起跳建候選池 |
| 加速（非現役） | Lightning 4 步 LoRA 可另立獨立草稿 workflow（4 步／CFG 1.0，負向失效）；目前附件未接此模式，終稿固定回 20 步 |

### 3. 工作流檔案與節點地圖

#### 3.1 固定裁框重放：[`wf02_insert.json`](./wf02_insert.json)

實讀節點結構(API 格式,POST /prompt 直接跑):

| 節點 | class_type | 作用 / 要改的欄位 |
|---|---|---|
| `41` | LoadImage | **場景圖**(現值 `plate_locked_I2.png`,server input 檔名) |
| `200` | ImageCrop | 場景裁切框(現值 x700,y150,720×506——I2 站姿版框) |
| `170:160` | ImageScale(lanczos) | 裁切後縮放(現值 1216×864;規則=~1.05MP、/16 對齊) |
| `201` | LoadImage | **角色圖**(現值 `clean_master_v1.png`) |
| `202` | FluxKontextImageScale | 角色參考縮放 |
| `170:151` | TextEncodeQwenImageEditPlus | **正向 prompt**(image1=場景、image2=角色) |
| `170:149` | TextEncodeQwenImageEditPlus | 負向(prompt="",同雙圖) |
| `170:147`/`170:148` | FluxKontextMultiReferenceLatentMethod | 正負各一顆,`index_timestep_zero` |
| `170:156` | VAEEncode | 場景縮放後 → latent_image |
| `170:169` | KSampler | **seed** 在這裡；固定 20 步／CFG 2.5／euler／simple／denoise 1.0 |
| `9` | SaveImage | 輸出前綴(現值 `i2i_mix_insert`) |

修正版共 18 個節點，已移除所有 `ComfySwitchNode` 與
`PrimitiveBoolean`／`PrimitiveFloat`／`PrimitiveInt`。原本沒有接
Lightning LoRA 的半成品 4-step 分支不再存在。產品接線仍採
`wf_dual_B1.json` 作通用單角色模板，以避開本檔固定的 I2 裁框。

#### 3.2 2511 母本

歷史母本
[`Qwen_Edit_2511_00009__prompt.json`](./t2i/scripts_and_workflows/Qwen_Edit_2511_00009__prompt.json)
已作為參考附件附上。它是 2511 編輯／插入／轉面共用母本；拿它改造成
插入用途時，必須把 Multi-Angle LoRA 節點 `170:198` 拔掉或將強度歸零
（D3:E7/E8 GGUF 靜默殘留教訓，直接移除比調成 0 保險）。關鍵節點：
`170:169`=KSampler、`170:151`=正向 encode、`9`=SaveImage、
`41`=LoadImage。

#### 3.3 雙角色：[`wf_dual_B1.json`](./wf_dual_B1.json) /
[`wf_dual_B2.json`](./wf_dual_B2.json)

鏈式兩輪用（見 §6）。已判死的三參考版
[`wf_dual_A.json`](./t2i/dual_char_scene/wf_dual_A.json)
只供失敗案例對照，不用於產品。完整 seed 紀錄
[`manifest_dual.jsonl`](../manifests/manifest_dual.jsonl) 已附；歷史配套
`dual_runner.py`（跑批）與 `make_panel_dual.py`（交付面板）尚未附上。

#### 3.4 驅動腳本（尚未附上）

- `comfy_run.py`：通用一條龍（上傳→送單→輪詢→抓圖→manifest；
  `--dry-run`／`--upload`）。
- `build_and_queue_wf02.py`：wf02 建圖與排 queue（plate、裁切框
  `x y w h`、seeds、批名參數化；自動上傳、計算生成尺寸、把座標寫入
  manifest）。
- `extract_meta.py`：從成品 PNG 抽出內嵌 graph，作為重現／微調起點。

這些是歷史配套腳本名稱，不再保留舊個人目錄或舊 monorepo 絕對位置。

### 4. 提示詞（逐字紀錄）

#### 4.1 插入骨架（通用）

```
將第二張圖中的角色完整放進第一張圖的場景裡:【位置與動作:逐肢體描述+與場景物件的接觸關係】。
【角色特徵逐項點名:髮型/髮色/瞳色/服裝各件/配件…】,完全以第二張圖為準,不做任何更改。
第一張圖的場景與構圖保持一致:【場景元素逐項列出】。
用場景的【光源】為角色打光,在【接觸面】投下自然的接觸陰影。
整體畫面統一為精緻動漫插畫風格,細節豐富。
```

#### 4.2 歷史逐字實例

**① insert prompt v2 坐姿版**(I1 批,2026-07-08;v1 站姿版作廢——使用者更正是坐在床上):

> 將第二張圖中的角色完整放進第一張圖的場景裡:她優雅地坐在床墊上的白色絲綢床單上,身體與臉朝向畫面左前方,黑色長裙裙襬在床單上自然鋪開。她的髮型、髮色、五官、紅色眼睛、黑金哥德禮服、黑色半透明面紗、紅玫瑰頭飾、高跟鞋完全以第二張圖為準,不做任何更改。第一張圖的場景、家具、透視、視角、光線完全保持不變,只加入這位角色。用場景的溫暖燭光為角色打光,在床單上投下自然的接觸陰影。精緻動漫插畫風格。

**② wf02 現內建版**(I2 站姿,2026-07-09):

> 將第二張圖中的角色完整放進第一張圖的場景裡:她站在床邊的猩紅色天鵝絨地毯上,身體與臉朝向畫面左側,姿態優雅沉靜,黑色長裙裙襬自然垂落在地毯上。她的髮型、髮色、五官、紅色眼睛、黑金哥德禮服、黑色半透明面紗、紅玫瑰頭飾、高跟鞋完全以第二張圖為準,不做任何更改。第一張圖的場景、家具、透視、視角、光線完全保持不變,只加入這位角色。用場景的溫暖燭光為角色打光,在地毯上投下自然的陰影。精緻動漫插畫風格。

**③ 壁紙線 v5 版**(WP4KC,2026-07-10;WORKFLOWS §4.2 的範例原型):

> 將第二張圖中的角色完整放進第一張圖的場景裡:她優雅地坐在大床邊緣的正中央,面向鏡頭,雙腿自然下垂,黑色長裙裙襬與暗紅色襯裡在床沿自然鋪開。她的髮型、銀白色長直髮、紅色眼睛、五官、黑金哥德禮服、金色刺繡、暗紅色襯裡、紅玫瑰頭飾、黑色蕾絲頭紗、金色垂墜耳環、大腿帶、金飾黑色高跟鞋,完全以第二張圖為準,不做任何更改。第一張圖的場景與構圖保持一致:黑色緞面大床與紅色荷葉邊、紅色緞面枕頭、金色圓柱、點燃的蠟燭與燭台、床頭燈、紅玫瑰花瓶、窗外深夜城市燈火、床底透出的紅色氛圍光、黑色大理石地板。用場景的溫暖燭光為角色打光,在床單上投下自然的接觸陰影。整體畫面統一為精緻動漫插畫風格,細節豐富。

#### 4.3 雙角色重寫骨架（歷史 `PLAN_dual_char_insert.md` §5）

```
[編號指令段] 把參考圖中的兩位角色加入場景,場景本身的結構、光源、視角保持不變:
             1. <角色A>站在畫面左側,<站位/動作>  2. <角色B>站在畫面右側,<站位/動作>  3. <互動>
[比例段]     相對身高寫死(如:A 比 B 高半個頭)
[識別段]     A:<髮/瞳/服裝/飾品+不對稱特徵側位>;B:同格式——兩組特徵分開成段,只寫各自「有什麼」
[保持清單]   場景關鍵元素列舉保持不變
[風格尾綴]   整體統一為精緻動漫插畫風格。
```

- 空間用**畫面座標**(畫面左/右),不用角色自身左右;不對稱特徵歸屬才用「她自己的右側」錨定。
- 姿勢類指令不混跑(初插入輪只做「放進去+站位+朝向」,細姿勢留後續單獨輪)。

#### 4.4 產品端提示詞策略（2026-07-21 補記）

Qwen-Image-Edit-2511 使用自然語言的「編輯指令」，不使用
Stable Diffusion 式的逗號關鍵字堆疊。官方雙圖範例也是直接說明
角色的左右位置、場景與互動關係：

- [Qwen-Image-Edit-2511 官方模型頁](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)
- [Qwen-Image 官方 repository](https://github.com/QwenLM/Qwen-Image)

目前產品的 Browser 只要請使用者補充「位置、朝向、動作、與場景
物件的接觸關係、人物尺度與入鏡範圍」。FastAPI adapter 會在送出前
加入 server-owned guard，固定第一張圖是場景、第二張圖是角色，
並要求角色身份與場景結構保持不變。Browser 不必把整段 guard
重複輸入。

使用者欄位的推薦骨架：

```text
角色位於【畫面左／中／右側的具體場景物件旁】，
身體朝向【方向】，臉看向【方向】。
【逐項描述雙手、雙腳、身體動作與接觸面】。
角色【全身／半身】入鏡，高度約占畫面【比例】，
不遮住【場景中必須保留的主體】。
```

產品與未來 prompt-refinement Agent 共用的原則：

1. 用「畫面左／右」表示畫面座標；只有不對稱特徵才寫「角色自己的
   左／右側」，必要時同時標出對應的畫面方位。
2. 複雜姿勢要拆成關節、肢體與接觸關係，不只使用「帥氣姿勢」或
   特定姿勢名詞。
3. 只寫各角色「有什麼」，少用「沒有 X／不穿 Y」的長串否定清單，
   避免被否定的概念仍進入 attention 後造成特徵互染。
4. 道具要寫形狀、材質與使用方式，不只寫名稱。
5. 一輪集中一個主要編輯目標；換裝、搬位、改姿勢、改燈光與
   改鏡頭不要同一輪混跑。
6. 正式成品使用 3 seeds 建候選池後做 QA；不對稱配件的側位與
   複雜手腳不能只靠 prompt 保證。

Qwen 官方 repository 對 image edit 提供過影像感知的 prompt
enhancement，因此未來可加「AI 優化提示詞」選用入口。該層不可
靜默改變使用者意圖：應顯示改寫結果供使用者確認，並在後端的
server-owned guard 之前執行。Agent 不得覆寫圖片順序、角色／場景
保持要求、workflow 路由或其他安全邊界。

4K 欄位屬於另一條 `wf10_upscale_opt2` 處理鏈，不沿用 2511 的
人物插入提示詞。該欄只描述要保留或加強的髮絲、布料、
材質、邊緣與光影細節；後端會另行加入「只精修、不改構圖」
的 guard。

### 5. 陷阱與教訓（全部實測）

1. **E13 構圖引力**:image2 的構圖會拉扯輸出。角色參考永遠**單參考一張 clean master**;三視圖 sheet **必裁單視角**再餵(整張直餵=實測憑空長樹、丟服裝);同角色第二視角禁餵。
2. **風格單向性**:寫實場景+動漫角色 → 加「整體統一為精緻動漫插畫風格」可行(3/3 過 QA,場景質感保留、角色一致性極高);**反向(二次元→真人)不行**。
3. **E10 透明底變黑**:去背 RGBA 參考圖必先 PIL 壓平**淺灰 240** 再上傳。
4. **三參考一次插入禁用**(2026-07-12 判死):image2/3=兩角色,7 張只 1 張零互染;同性別+同髮色+深色系互染不可控(特徵互換、融合、一人消失、複製第三人)。image3 槽能跑,只有外觀互異性極強的組合可小測。
5. **否定式/對比式識別段禁用**(3/3 全滅):「A 不穿紫」「B 無毛領」讓被否定概念照樣進注意力,互染惡化。只寫各自有什麼、分開成段。
6. **姿勢/方位指令永遠單獨一輪**(鐵律 4):與換裝/飾品/燈色混跑 0/3 全滅;搬位+服裝混跑 0/3(2/3 丟原姿勢)。
7. **結構切換成本**:雙↔單參考圖(或雙↔三)切換的批次交界一次性成本最高 **22 分鐘** → 同結構同批連跑,不要交錯。
8. **道具寫形狀不寫名稱**:「伐木斧」3/3 畫成木棒彎刀;「長木柄+寬大楔形鐵製斧刃+劈向樹幹」才穩。「木屑飛濺」類飛散詞會被錯譯成火花/水花。
9. 每輪檢查**不對稱特徵**(玫瑰頭飾=她的右側)有沒有被隨機翻側——prompt 控不住,靠 QA+重 roll。
10. 髒狀態前科:跑完 28GB AIO 級巨模後直接排 2511 → 全批 `CUDABFloat16Type/CPUBFloat16Type` 死(v5 前一批 3 job 全滅);重啟後首發乾淨 3/3。

### 6. 雙角色 × 同場景（鏈式兩輪 R2b）

#### 6.1 協定（2026-07-12 定案，6/6 全合格）

- **輪 1**:場景+角色A(**prompt 加一句留空位**,逐字見 §6.3)→ 3 seeds → QA 挑勝者。
- **輪 2**:勝者當場景+角色B(**保持清單寫死角色A 全部特徵+場景全列**)→ 3 seeds → QA。
- 2511 位置錨定特性=輪 2 幾乎不動輪 1 的人。~4 分/張,每輪 3 seeds 充分。
- **三參考一次插入(image1=場景+image2/3=雙角色)已判死**(§5-4),`wf_dual_A.json` 留檔勿用。
- 三個以上角色:未驗證❓,推論=繼續逐輪插入+保持清單累加,每輪 QA 過了才進下一輪。

#### 6.2 工作流檔與節點（實讀）

[`wf_dual_B1.json`](./wf_dual_B1.json) /
[`wf_dual_B2.json`](./wf_dual_B2.json) 的結構是 wf02 去掉場景裁切段
（場景圖直接進 `FluxKontextImageScale`）；其餘接線、模型堆疊、參數
完全同 §2（20 步／CFG 2.5／euler／simple／denoise 1.0／shift 3.1，
正負 encode 都接雙圖）：

| 節點 | B1(輪 1) | B2(輪 2) |
|---|---|---|
| `41` LoadImage(場景) | `scene_content.png` | `b1_winner.png`(輪1 勝者) |
| `42` LoadImage(角色參考) | `ref_marco_front.png`(三視圖裁單視角) | `ref_edri_front.png` |
| `170:151` | 正向 prompt(逐字見 §6.3) | 同 |
| `170:169` KSampler | seed 起始 7101 | seed 起始 7201 |
| `9` SaveImage | 前綴 `dual_char/dualB1` | 前綴 `dual_char/dualB2` |

#### 6.3 逐字 prompt（實戰版）

**輪 1(MARCO 砍樹)**:

> 把參考圖中的MARCO公爵加入這片奇幻山谷場景。場景的地形、樹木、溪流、岩壁、金色陽光和鏡頭視角保持不變。MARCO公爵(黑色後梳短髮、絡腮山羊鬍、粗獷嚴肅的面孔、黑色毛皮領大氅、暗紅色內襯、金色鎧甲肩片、黑手套黑長靴)站在畫面左側的大樹旁,雙手緊握一把長木柄的巨大伐木斧頭,斧柄頂端有一片寬大鋒利的楔形鐵製斧刃,他正掄起斧頭把斧刃劈向樹幹,身體前傾發力。畫面中只有他一個人,畫面右側的溪流與草地保持空曠無人。他的全身完整入鏡,大小符合場景透視與真實人體比例。整體統一為精緻奇幻動漫插畫風格,他的光影與場景的金色陽光方向一致。

**輪 2(EDRI 澆水)**:

> 把參考圖中的EDRI公爵加入這個場景的右側草地。畫面左側正在揮斧砍樹的MARCO公爵完全保持不變:他的臉、山羊鬍、黑色毛皮領大氅、暗紅色內襯、大斧頭、揮砍姿勢和位置都一絲不動。場景的地形、樹木、溪流、岩壁、金色陽光和鏡頭視角也完全保持不變。EDRI公爵(黑色微捲中長髮、年輕俊美、下巴光滑完全沒有鬍鬚、深紫黑色帶鳶尾花紋刺繡的及膝長外袍與兜帽披風、白色襯衫、紫色馬甲、黑手套黑靴)在畫面右側溪流旁的草地上單膝蹲下,面向一株剛種下的小樹苗,一手輕扶樹苗,另一手用金屬澆水壺往樹苗根部澆水,壺嘴流出清水,樹苗周圍有新翻的深色泥土。他的全身完整入鏡,大小符合場景透視與真實人體比例。整體風格與畫面一致的精緻奇幻動漫插畫,他的光影與金色陽光方向一致。

四個關鍵手法都在裡面:①輪1 末段「畫面中只有他一個人,畫面右側…保持空曠無人」=**留空位句**;②輪2 開頭整段寫死 MARCO 全特徵=**保持清單**;③斧頭寫「長木柄+寬大楔形鐵製斧刃+劈向樹幹」=**道具形狀描述**(只寫「伐木斧」3/3 畫成木棒彎刀);④識別段**只寫各自有什麼、分開成段**(否定式禁用)。

#### 6.4 實績與紀錄

- 輪1 seeds 7101–7103 → 勝者 **7102**;輪2 seeds 7201–7203 → 推薦 **7202**;兩輪 **6/6 全合格**。
- 黃金驗證圖已附：
  [`B1 勝者`](../golden/insert/b1_winner_seed7102.png)、
  [`B2 最終勝者`](../golden/insert/dualB2_winner_seed7202.png)，連同
  [`場景`](../golden/insert/scene_content.png)、
  [`角色 A`](../golden/insert/ref_marco_front.png) 與
  [`角色 B`](../golden/insert/ref_edri_front.png) 可重放兩輪。
- 完整 seed 紀錄已附：
  [`manifest_dual.jsonl`](../manifests/manifest_dual.jsonl)。
- 歷史交付面板 `panel_dual_char.png` 與
  `PLAN_dual_char_insert.md` 尚未附。
- 歷史配套腳本：`dual_runner.py`（跑批）、`make_panel_dual.py`
  （面板）；目前未附。

#### 6.5 雙圖結構的他用：服裝參考換裝（v17 實績）

同 wf02 雙圖結構,image1=要改的成品圖、image2=**服裝參考圖**(產品圖可用),prompt 描述換裝+保持清單。實績:鴨子坐成品換白色半透雪紡襯衫,3 seeds 勝者 801318846899852;已知偏差=單腕紅髮圈被詮釋成雙袖口滾邊(3/3 一致)。

### 7. QA 檢查表（插入特化）

1. 特徵互染(雙角色第一殺手)——A 的元素出現在 B 身上即淘汰。
2. 逐角色對參考圖核對:髮型/髮色/瞳色/服裝/飾品/**不對稱特徵側位**(以角色自身為準)。
3. **手部逐指**(使用者重點盯)。
4. 場景保持:結構/光源/視角沒被改掉;角色受光方向與場景一致、影子同向。
5. 相對比例、遮擋處乾淨、角色數量恰確(多參考可能複製出第三人)。
6. 交付一律做並排面板或逐張列點,不叫使用者翻資料夾。

### 8. 使用紀錄時間線

| 日期 | 批次/事件 | 配置與結果 |
|---|---|---|
| 2026-07-08 | **I1 坐姿插入**(i2i_mix 分鏡軌) | 裁切 (500,120,704,456)→1280×832,3 seeds 全過第 0 項;勝者 seed **104831663146131**(朝向✔玫瑰右側✔)→ 拆層貼回=`frame01`;使用者 QA:人物偏大→compose_v2 **0.72× 定版** |
| 2026-07-09 | **I2 站姿插入**(ext4 熱集後首批) | 裁切 (700,150,720,506)→1216×864;勝者 run6 seed **215098382934622**(唯一達成身體朝畫面左)→`frame02`(0.80×+差分陰影);穩態 265/241/234s,零龜速=9p 病理三症狀全消 |
| 2026-07-10 | **v5 壁紙線插入**(WP4KC) | image1=ChatGPT 場景圖、image2=canon 角色;3 seeds,勝者 **640200685988679**(canon 服裝忠實度最高:金十字+鎖鏈、玫瑰方位正確);前一批 3 job 死於 AIO 髒狀態,重啟後 3/3 乾淨 |
| 2026-07-11 | v17 服裝參考換裝 | 雙圖變體(image2=服裝圖),勝者 801318846899852 |
| 2026-07-12 | **雙角色對決**(PLAN_dual_char_insert) | 甲路三參考判死(7 張 1 乾淨,否定式加固 3/3 反惡化);乙路鏈式兩輪 **6/6 全合格**→定案 R2b |

**軌道狀態備註**：歷史 i2i_mix「2D 分鏡」產品軌於 2026-07-08 因
跨格一致性被小組會議駁回，後轉向「文本→分鏡」網站新主線（歷史文件
`WEB_MVP_PLAN.md` 未附）。wf02 的固定裁框配方仍可重放；產品通用接線
改用沒有固定裁框的 `wf_dual_B1.json`。壁紙線 v5 與雙角色線都是駁回後
的成功使用案例。

---

## 處理鏈 B：4K 放大（→3840×2160）

### 9. 一句話定位

把定稿的 ~1–2MP 圖放大成 4K 桌布。**放大時機鐵則(2026-07-16 使用者裁決):比稿不放大**——候選挑選一律 ~1MP 工作解析度,定稿確認後才對勝者跑放大鏈(~90s 只付一次、保住候選 PNG 內嵌 graph 可回收性)。

### 10. 現行預設：opt2 鏈

工作流：[`wf10_upscale_opt2.json`](./wf10_upscale_opt2.json)

**鏈**:`RealESRGAN_x2plus 2x → lanczos 2672×1504 → Z-image tiled refine denoise 0.23 → RealESRGAN_x4plus_anime_6B 4x → lanczos 3840×2160`
= enhanced 級清晰(anime_6B 收尾)+柔筆觸(先柔化 refine)的折衷。全程 ~90 秒。
2026-07-16 實跑驗證：重放 v14 定案成品**像素級一致
（maxdiff=0）**。同內容的歷史備份副本
[`A1_附錄_wf10_upscale_opt2_現行標準版.json`](./wallpaper_4k/WP4KD_workflow_chain/A1_附錄_wf10_upscale_opt2_現行標準版.json)
也已附上；產品執行只使用根目錄的現行主檔，避免維護兩份來源。

> **黃金重放注意**：現役 `wf10_upscale_opt2.json` 的 node `4`
> `user_prompt` 是通用佔位文字，不可「原封」得到 v14 黃金輸出。重放時
> 必須從
> [`manifest.json`](../manifests/manifest.json) 的
> `stage_v14_ducksit.finish_opt2.refine_prompt` 注入 node `4`，或使用
> 從黃金 PNG 內嵌 `prompt` metadata 取出的固定 fixture graph。

#### 節點地圖（實讀）

| 節點 | class_type | 內容 / 要改的欄位 |
|---|---|---|
| `10` | LoadImage | **輸入圖**(server input 檔名,現值 `upscale4k_src.png`) |
| `30`/`31` | UpscaleModelLoader+ImageUpscaleWithModel | 前段放大器 `RealESRGAN_x2plus.pth` |
| `20` | ImageScale(lanczos, crop=center) | **中繼尺寸** 2672×1504(非 16:9 必改:源比例×2 後 /16 對齊) |
| `21` | VAEEncodeTiled | tile 512 / overlap 64 |
| `1`/`2` | UNETLoader+ModelSamplingAuraFlow | `z_image_turbo_bf16.safetensors`(fp8_e4m3fn_fast)、shift 3.0 |
| `3` | CLIPLoader | `qwen_3_4b.safetensors`,type=lumina2 |
| `4` | CLIPTextEncodeLumina2 | **refine 畫面描述**(system_prompt=superior;規則見 §12) |
| `5` | ConditioningZeroOut | 當負向(CFG 1.0 負向無效) |
| `22` | KSampler | **seed**(慣用 676767676701)/ 8 步 / CFG 1.0 / res_multistep / simple / **denoise 0.23** |
| `23` | VAEDecodeTiled | 同 tile 參數 |
| `11`/`24` | UpscaleModelLoader+ImageUpscaleWithModel | 尾段放大器 `RealESRGAN_x4plus_anime_6B.pth` |
| `25` | ImageScale(lanczos, crop=center) | **最終尺寸** 3840×2160(crop=center 只在比例一致時無損) |
| `26` | SaveImage | 輸出前綴(現值 `upscale4k_opt2`) |
| `9` | VAELoader | `ae.safetensors` |

### 11. 銳度光譜（由柔到銳，依驗收反應逐級切換）

| 配方 | 鏈 | 質感 | 用時 |
|---|---|---|---|
| 純放大(內容零改動) | anime_6B 4x → lanczos crop-center 3840×2160 | 100% 不動;源圖柔軟處微糊 | ~30s |
| v8 柔化版 | x2plus → lanczos 2672×1504 → Z-refine **0.20** → x2plus → lanczos 4K | 髮絲柔順如絲 | ~90s |
| **opt2 ★現行預設** | x2plus → 2672×1504 → Z-refine **0.23** → anime_6B → lanczos 4K | enhanced 級清晰+柔筆觸 | ~90s |
| USM 秒調 | PIL `UnsharpMask(radius=2.5, percent=70~130, threshold=2)` | 純像素銳化,零風險 | 1s |

清晰度旋鈕本質=**選放大器**:anime_6B=線稿銳化型(硬邊,使用者嫌「鋼絲感」)、x2plus=柔和通用型。

### 12. Refine 段規則（節點 `4` 的 prompt）

1. **具體描述當前畫面內容**(主體、姿勢、服裝變體、飾品狀態、場景、光線)——大幅降低細節重生走樣。
2. 柔化導向詞:「柔順如絲」「筆觸柔和細膩」。
3. **必加「沒有戴耳環」**——Z-image refine 自發長耳環前科 2/4;完成後必檢耳部。
4. 單側飾品成品要加防對稱化 guard(v18 教訓):「只有左手腕戴水晶串珠,右手腕沒有任何飾品」,QA 必驗另一側。
5. denoise 階梯:0.20=柔化 / 0.23=opt2 / **0.25 會自發加小飾品且偏銳(v7 實測),不要用**。

**Refine prompt 逐字範本**(v14 鴨子坐定稿實例,照這密度寫):

> 高精緻半寫實動漫插畫,2.5D 厚塗質感,線條柔和自然。一位銀白色長直髮貓耳少女,白色貓耳,紅色眼睛,柔順如絲的銀白長髮,安靜的表情,沒有戴耳環。頭髮右側有一朵紅玫瑰與黑色緞帶,穿黑色蕾絲吊帶睡裙、暗紅色荷葉邊、胸前紅色緞帶,單隻手腕戴紅色髮圈,鴨子坐在大床正中央的床墊上:雙膝併攏朝向鏡頭,小腿向外折在大腿兩側,赤腳的腳掌露在臀部兩旁,雙手輕放在身前大腿上。黑色緞面床單泛柔和光澤,紅色緞面荷葉邊枕頭,暗紅色綢緞床罩垂落床沿,床底透出溫暖的橙黃色氛圍燈光。金色圓柱、燭台燭光、床頭暖黃檯燈、紅玫瑰花瓶,窗外深夜城市燈火。溫暖燭光主光源,深邃陰影,電影感打光,柔和細膩的皮膚與緞面高光,整體筆觸柔和精緻。

### 13. 配方演進史：WP4KD 血統鏈

歷史血統共 6 站；五份 ComfyUI graph 已附：

- [`01_v7_pure+enhanced_雙軌放大.json`](./wallpaper_4k/WP4KD_workflow_chain/01_v7_pure+enhanced_雙軌放大.json)
- [`03_v8_soft_x2plus柔化鏈.json`](./wallpaper_4k/WP4KD_workflow_chain/03_v8_soft_x2plus柔化鏈.json)
- [`04_v9_胸口inpaint.json`](./wallpaper_4k/WP4KD_workflow_chain/04_v9_胸口inpaint.json)
- [`05_v10_手臂armfix_inpaint.json`](./wallpaper_4k/WP4KD_workflow_chain/05_v10_手臂armfix_inpaint.json)
- [`06_v13_opt2清晰化定稿.json`](./wallpaper_4k/WP4KD_workflow_chain/06_v13_opt2清晰化定稿.json)

歷史詳版 `WP4KD_workflow_chain/README.md` 尚未附上。

對應成品:`WP4KD_FINAL_v2_opt2_crisp.png`(主版定稿)。每站 graph 已從成品 PNG 內嵌 chunk 抽出存檔於同資料夾。

| 站 | 日期 | 配方 | 裁決 |
|---|---|---|---|
| 00 | 07-10 | ChatGPT 外部模板圖 1672×941(上傳為 `wp4k_direct_src.png`) | 非 ComfyUI 產物,無工作流 |
| 01 v7 | 07-10 15:38 | 雙軌:pure=anime_6B 4x→4K(零改動);enhanced=anime_6B→2672×1504→Z-refine **0.25**(seed 881234567001)→anime_6B→4K | enhanced 滿意,但**左耳自發長耳環+髮絲鋼絲感**→催生 v8 |
| 03 v8 | 07-10 15:48 | **x2plus** 取代 anime_6B、0.25→**0.20**(seed 424242424301,柔順 prompt、不提耳環) | 耳環未再生成、髮絲柔化=「柔化版」配方誕生 |
| 04 v9 | 07-10 16:11 | (支線:胸口 inpaint,Z-image 0.5 @4K tiled)seeds:**777000111222 勝**/888…淘汰/999…淘汰 | 區域重繪工作流,列入血統供重放 |
| 05 v10 | 07-10 16:22 | (支線:手臂 armfix inpaint,標記點 [1665,955][2110,925])勝 313131313101 | 同上 |
| 06 v13 | 07-10 19:03 | armfix→lanczos **降到** 2672×1504→Z-refine **0.23**(seed 232323232301,柔髮+無耳環)→anime_6B 4x→4K | **★opt2 配方原型定稿**;QA:構圖 MSE 81 與 v2 一致、無耳環、修正保留 |

衍生備選：`WP4KD_FINAL_v2_sharp70.png`、`WP4KD_FINAL_v2_sharp130.png`
= 定稿的 PIL USM 純像素銳化版。
重放注意:06 假設源已是 4K(無放大頭);**對新圖跑 4K 一律用 wf10**(x2plus 開頭完整鏈)。

### 14. 使用紀錄（逐 stage，含放大線之前的歷史配方）

| Stage | 源 | 放大配置 | 產出 |
|---|---|---|---|
| v3(SDXL 壁紙線) | WAI v140 文生圖勝者(B 軌 seed 844639784021753) | ESRGAN→2688×1536→**WAI 自 refine denoise 0.40**(30 步/CFG 5.5)→anime_6B→4K,60s | `WP4K_final_*.png` |
| v4 | Z-image/AIO 文生圖勝者 | 同構,refine **denoise 0.35**(8 步/CFG 1.0) | `WP4KZ_final_*` / `WP4KQ_final_*` |
| v5(插入線) | WP4KC 插入勝者 | anime_6B 4x→4K(主)+x2plus(柔備選)雙版 | `WP4KC_anime6B_*` / `WP4KC_x2plus_*` |
| v6(編輯線) | WP4KE 姿勢編輯勝者 190780138285712 | anime_6B 4x→4K | `WP4KE_final_*` |
| v7–v13 | (WP4KD 血統鏈,見 §13) | 0.25 enhanced → 0.20 柔化 → 0.23 opt2 演進 | `WP4KD_FINAL_v2_opt2_crisp.png` 等 |
| v10 sexy | 色氣版編輯勝者 932771875613823 | v8 柔化配方(seed 515151515101)+耳部點修二輪(0.55,seed 101010101011) | `WP4KS_sexy_FINAL_v2_earfix.png` |
| v12 apron | 圍裙版勝者 585464282576075 | **opt2**(refine 寫圍裙描述,seed 454545454501) | `WP4KA_apron_FINAL_*.png` |
| v14 ducksit | 鴨子坐勝者 966869739023339 | **opt2**(seed 676767676701;refine prompt=§12 範本) | `WP4KP_ducksit_FINAL_*.png` |
| v15 split | 一字馬勝者 469120791622121 | **opt2**(同 seed) | `WP4KP5_split_FINAL_*.png` |
| v16 shift | 搬位+裙襬勝者 519285246679031 | **opt2** | `WP4KP6_shift_FINAL_*.png` |
| v17 shirt | 換裝勝者 801318846899852 | **opt2** | `WP4KP7_shirt_FINAL_*.png` |
| v18 collar | 項圈拼接版(C2×B2 腕部 PIL 拼接) | **opt2**(refine guard:只有左腕串珠) | `WP4KP8_collar_FINAL_assembly.png` |
| 2026-07-16 | wf10 獨立化驗證 | 重放 v14 → **maxdiff=0**,~90s | wf10 定版 |

觀察:v12 起所有定稿一律走 opt2,refine seed 固定 676767676701 成慣例;v3/v4 的「同模自 refine 0.35–0.40」屬 SDXL/Z-image 文生圖線的舊法,現行對任意圖放大一律用 opt2/柔化鏈(Z-image 0.20–0.23)。

### 15. 陷阱與教訓

1. **anime_6B 鋼絲感**(v7):使用者不喜線稿硬銳——前段一律 x2plus;anime_6B 只放尾段(opt2)或純放大。
2. **refine 0.25 會自發加飾品**(v7 左耳耳環)+線條偏銳 → 定案停在 0.20–0.23,且 prompt 必帶「沒有戴耳環」,完工必檢耳部。
3. **非 16:9 圖必改節點 `20`/`25`**:中繼=源比例×2 後 /16 對齊、最終=目標尺寸;crop=center 只在比例一致時無損。
4. 中繼尺寸必取 **/16 整除**(VAE 對齊)。
5. **比稿不放大**(§9);放大只做定稿勝者。
6. 精修後單側飾品會被對稱化回來——refine guard 詞+QA 驗另一側(v18)。
7. 銳度不滿意先走 USM(1 秒、零風險),再考慮換級;逐級切換不跳級。
8. 06 站 graph 不能拿來放大新圖(它假設源已 4K)——新圖一律 wf10。

---

## 共用營運規則（速查）

| 項目 | 規則 |
|---|---|
| 伺服器 | ComfyUI @ WSL Ubuntu-24.04,API `http://127.0.0.1:8188`;健康檢查 `GET /system_stats` |
| 重啟（唯一清狀態手段） | 歷史腳本 `restart_comfyui.sh` 尚未附。正式接線時由設定值 `${COMFYUI_ROOT}` 組合路徑，不寫死個人絕對路徑；重啟後輪詢 `/system_stats`（<60s） |
| **`POST /free` 永久禁用** | 實測兩次弄死 CUDA context;連預防性呼叫都不行 |
| `CUDABFloat16Type/CPUBFloat16Type` | TE 卡 CPU=髒狀態,唯一解=重啟;禁瞎重試(v5 前批 3 job 全滅實例) |
| CUDA 死亡鏈 | 共同前提=VRAM 高壓下 offload/重載。路徑①BiRefNet/去背大驅逐後的下個大模型生成;②爬行中跑完的批次,下一發秒死。預防:**去背與生成相位隔離**(或去背走 CPU isnetis);**爬行中完成的批次視同髒 context,下批前重啟** |
| 取樣爬行診斷 | VRAM>95%+Util>90% 但**功耗僅 60–80W**=WDDM 換頁(桌面 App 搶 VRAM),不是卡死;關掉 GPU 大戶立即回魂,佇列不用清。跑批前 `nvidia-smi` 確認桌面乾淨(<30W) |
| 圖檔進出 | Windows 看不到 WSL output;輸出 `GET /view?filename=...&type=output`、輸入 `POST /upload/image`(overwrite=true) |
| CJK 提示詞 | 一律寫進 UTF-8 JSON 再送 API;PowerShell 命令列傳中文=亂碼 |
| Seed 紀律 | 新內容 randomize 3–4 seeds 建候選池;固定 seed 只用於單變因 A/B;全部登記 manifest |
| 攢批紀律 | 同模型家族攢批連跑(2511↔Z-image 交替=每次多付 1–2 分重載);多輪編輯先連跑完 2511 輪,精修/放大集中最後做 |
| 產出紀律 | 成品 PNG 保留內嵌 workflow+另存 graph JSON;批次寫 manifest;歸檔到 `${COMFYUI_ROOT}` 下受控的對應子資料夾 |
| 輪詢韌性 | 30GB 級載入期 HTTP 會被重置(WinError 10054)——poller 帶 15s 退避重試;超時未抓到=用 prompt_id 從 /history 補抓,勿重送 |
| 時間常數 | 2511 插入 ~240s/張(冷載 445s、VRAM 被搶最慢 48 分);opt2 全鏈 ~90s;排程估算 ~5 分/張(全包);長批熱節流 +17% |

---

## 附錄：附件盤點與缺件清單

### A. 本資料夾已附內容

| 檔案 | 節點數 | 用途 | 狀態 |
|---|---:|---|---|
| [`wf02_insert.json`](./wf02_insert.json) | 18 | I2 固定裁框單人合成 | 現役參考／重放 |
| [`wf_dual_B1.json`](./wf_dual_B1.json) | 17 | 通用單角色模板／雙人鏈第 1 輪 | 產品主力 |
| [`wf_dual_B2.json`](./wf_dual_B2.json) | 17 | 雙人鏈第 2 輪 | 現役主力 |
| [`wf03_matte.json`](./wf03_matte.json) | 5 | BiRefNet 去背／mask | 選用輔助 |
| [`wf10_upscale_opt2.json`](./wf10_upscale_opt2.json) | 17 | opt2 4K 放大 | 現役主力 |

另有 8 份歷史／備份 JSON：

| 檔案 | 用途 |
|---|---|
| [`Qwen_Edit_2511_00009__prompt.json`](./t2i/scripts_and_workflows/Qwen_Edit_2511_00009__prompt.json) | 2511 共用母本 |
| [`wf_dual_A.json`](./t2i/dual_char_scene/wf_dual_A.json) | 已判死的三參考實驗，只供對照 |
| [`A1_附錄_wf10_upscale_opt2_現行標準版.json`](./wallpaper_4k/WP4KD_workflow_chain/A1_附錄_wf10_upscale_opt2_現行標準版.json) | wf10 同內容備份 |
| [`01_v7_pure+enhanced_雙軌放大.json`](./wallpaper_4k/WP4KD_workflow_chain/01_v7_pure+enhanced_雙軌放大.json) | v7 雙軌歷史 graph |
| [`03_v8_soft_x2plus柔化鏈.json`](./wallpaper_4k/WP4KD_workflow_chain/03_v8_soft_x2plus柔化鏈.json) | v8 柔化鏈 |
| [`04_v9_胸口inpaint.json`](./wallpaper_4k/WP4KD_workflow_chain/04_v9_胸口inpaint.json) | v9 胸口 inpaint |
| [`05_v10_手臂armfix_inpaint.json`](./wallpaper_4k/WP4KD_workflow_chain/05_v10_手臂armfix_inpaint.json) | v10 手臂 inpaint |
| [`06_v13_opt2清晰化定稿.json`](./wallpaper_4k/WP4KD_workflow_chain/06_v13_opt2清晰化定稿.json) | v13 opt2 原型 |

合計 13 份 JSON，全部可以解析且沒有 unresolved node reference。
`wf_w0_spike.json` 與 Windows `Zone.Identifier` 旁檔目前都不在本專案。

### B. 重現紀錄與黃金資料

- 環境 commit、模型表與重放說明：
  [`README_repro.md`](../README_repro.md)
- 壁紙線使用紀錄：
  [`manifest.json`](../manifests/manifest.json)
- 雙角色使用紀錄：
  [`manifest_dual.jsonl`](../manifests/manifest_dual.jsonl)
- 雙角色黃金資料：
  [`docs/golden/insert/`](../golden/insert/)
- 4K 黃金資料：
  [`docs/golden/upscale4k/`](../golden/upscale4k/)

原始 manifest 為歷史紀錄，裡面的 Windows 絕對路徑只表示來源機當時位置。
產品不得直接開啟這些路徑；後端以 repository fixture、資產 ID 與
`${COMFYUI_ROOT}` 解析實際檔案。

### C. Workflow 內的 server input（使用時替換）

這些是 JSON 現值，不是應放在 `docs/workflows/` 的固定路徑：

| Workflow | `LoadImage` 現值 | 意義 |
|---|---|---|
| `wf02_insert.json` | `plate_locked_I2.png`、`clean_master_v1.png` | 場景、角色參考 |
| `wf03_matte.json` | `matte_input_215098382934622.png` | 待去背圖片 |
| `wf_dual_B1.json` | `scene_content.png`、`ref_marco_front.png` | 場景、角色 A |
| `wf_dual_B2.json` | `b1_winner.png`、`ref_edri_front.png` | 第 1 輪勝者、角色 B |
| `wf10_upscale_opt2.json` | `upscale4k_src.png` | 待放大定稿 |

正式系統應先把使用者選定的資產上傳至 ComfyUI，再在送單前改寫上述欄位。
產品的單角色路徑以 `wf_dual_B1.json` 為通用模板，同時改寫兩個
`LoadImage`、正向 prompt、seed 與輸出前綴；不得沿用 MARCO 範例文字。
雙角色第二輪再套 `wf_dual_B2.json`。黃金 fixture 已放在
[`docs/golden/`](../golden/)。

### D. 外部模型依賴（不應提交到本資料夾）

| 處理鏈 | JSON 實際引用 |
|---|---|
| 人物×場景 | `qwen-image-edit-2511-Q6_K.gguf`、`qwen_2.5_vl_7b_fp8_scaled.safetensors`、`qwen_image_vae.safetensors` |
| 4K 放大 | `z_image_turbo_bf16.safetensors`、`qwen_3_4b.safetensors`、`ae.safetensors`、`RealESRGAN_x2plus.pth`、`RealESRGAN_x4plus_anime_6B.pth` |
| 去背 | `BiRefNet-HR-matting` |

主流程 8 個模型的大小、SHA-256、已知來源、授權與安裝子資料夾已記錄在
[`README_repro.md`](../README_repro.md)。模型本體不進 Git。仍缺精確
下載 URL／revision，所以目前可依 SHA-256 驗證身分，但還不能完全自動
下載。選用去背的 `BiRefNet-HR-matting` 仍缺來源、hash 與安裝資料。

### E. ComfyUI 與節點版本資料

[`README_repro.md`](../README_repro.md) 已釘定：

- ComfyUI commit
  `ab0d8a9203fbad76b0ccca723bbf9ba0c257ddfe`
- ComfyUI-GGUF commit
  `cf0573351ac260d629d460d97f09b09ac17d3726`
- Python 3.12.3 與 PyTorch 2.12.0+cu130

`TextEncodeQwenImageEditPlus`、`FluxKontextImageScale`、
`FluxKontextMultiReferenceLatentMethod`、`CFGNorm`、
`CLIPTextEncodeLumina2` 與 tiled VAE 節點都在上述 ComfyUI core；
主合成流程唯一 custom node 是 `UnetLoaderGGUF`。`ComfySwitchNode`
也存在於釘定 core，但修正版 wf02 已完全移除它與 Primitive 快速分支。

選用 `wf03_matte.json` 的 `BiRefNetRMBG` 仍缺 repository／commit。
此外，ComfyUI 與 GGUF 的 Python transitive dependencies 尚無完整 lock，
所以目前是 source-level pin，不是完整 bit-for-bit 環境鎖。

### F. 尚未附上的配套材料

#### 優先補：支援一鍵安裝與自動驗收

1. 主流程模型的精確下載 URL／revision，並轉成 machine-readable lock。
2. ComfyUI／GGUF 的完整 Python dependency lock、PyTorch wheel 來源與
   GPU driver／啟動參數紀錄。
3. `BiRefNetRMBG` repository／commit 與 `BiRefNet-HR-matting` 模型資料；
   若 MVP 不使用去背，可明確排除 `wf03_matte.json`。
4. 黃金驗收 manifest：直接綁定 workflow hash、resolved prompt／seed、
   input hashes、output hash 與異機容許門檻。
5. `wf02_insert.json` 固定裁框路徑與 `wf03_matte.json` 的專屬黃金案例。

Lightning 4-step 已從現役 wf02 拔除，不是待補的半成品功能。日後若另立
草稿模式，必須以獨立 workflow 與完整 LoRA lock 導入。

#### 視後續框架需要再補：自動化腳本

- `comfy_run.py`
- `build_and_queue_wf02.py`
- `dual_runner.py`
- `make_panel_dual.py`
- `compose_v2.py`
- `compose_cpu.py`
- `matte_and_compose.py`
- `poll_download.py`
- `extract_meta.py`
- `restart_comfyui.sh`
- `pixel_hash.py`

#### 只在要保存完整歷史時補：文件與舊產物

- `WORKFLOWS.md`
- `PROMPT_TEMPLATES.md`
- `recipes.md`
- `PLAN_dual_char_insert.md`
- `WP4KD_workflow_chain/README.md`
- `MISSION_LOG.md`
- `WEB_MVP_PLAN.md`
- 歷史候選圖、交付面板、canon／角色／場景素材庫
