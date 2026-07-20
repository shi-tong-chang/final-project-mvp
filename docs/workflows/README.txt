REF_人物場景合成與4K放大 — 附件 graph 打包(2026-07-21)
================================================================

本包 8 份 ComfyUI API 格式 workflow JSON,對應彙編文件
C:\Users\User\ComfyUI_database\REF_人物場景合成與4K放大.md 附錄 A。
資料夾結構 = 原始存放位置(相對 C:\Users\User\ComfyUI_database\)。

--- 人物×場景合成 ---

t2i\scripts_and_workflows\Qwen_Edit_2511_00009__prompt.json
  2511 母本(編輯/插入/轉面共用)。改造成「插入」用途時,
  必須把 Multi-Angle LoRA 節點 170:198 拔掉或強度歸零。
  關鍵節點:170:169=KSampler、170:151=正向 encode、9=SaveImage、41=LoadImage。

t2i\dual_char_scene\wf_dual_A.json
  雙角色「三參考一次插入」版(image1=場景+image2/3=兩角色)。
  ※已判死(2026-07-12:7 張僅 1 張零互染),留檔僅供對照,勿用於量產。
  正解=鏈式兩輪 wf_dual_B1/B2(不在本包,見 t2i\dual_char_scene\)。

--- 4K 放大 ---

wallpaper_4k\WP4KD_workflow_chain\A1_附錄_wf10_upscale_opt2_現行標準版.json
  現行標準 opt2 放大鏈(= i2i_mix\workflows\wf10_upscale_opt2.json 的定版備份)。
  對「新圖」放大 4K 一律用這份;要改的欄位見 WORKFLOWS.md §5.1。

wallpaper_4k\WP4KD_workflow_chain\01_v7_pure+enhanced_雙軌放大.json
  血統站1:pure 軌(anime_6B 4x 零改動)+ enhanced 軌(refine 0.25)同一張 graph。
  歷史教訓:0.25 自發長耳環+髮絲鋼絲感。

wallpaper_4k\WP4KD_workflow_chain\03_v8_soft_x2plus柔化鏈.json
  血統站2:柔化定裝(x2plus + refine 0.20)= v8 柔化版配方原型。

wallpaper_4k\WP4KD_workflow_chain\04_v9_胸口inpaint.json
  血統站3:胸口+右腋區域重繪(Z-image denoise 0.5 tiled + ImageCompositeMasked)。

wallpaper_4k\WP4KD_workflow_chain\05_v10_手臂armfix_inpaint.json
  血統站4:手臂區域重繪(同 v9 手法,小遮罩)。

wallpaper_4k\WP4KD_workflow_chain\06_v13_opt2清晰化定稿.json
  血統站5:opt2 配方原型(refine 0.23 + anime_6B 收尾)→ 產出主版定稿
  WP4KD_FINAL_v2_opt2_crisp.png。
  ※注意:此站假設源圖已是 4K(無放大頭),不能直接拿來放大新圖——新圖用 A1。

--- 重放須知 ---

1. 每份都是 API graph,POST http://127.0.0.1:8188/prompt 即可跑
   (或用 skill 的 comfy_run.py)。
2. graph 內 LoadImage 引用的是 server input 檔名(wp4k_direct_src.png、
   wp4k_opt2_src.png、wp4k_inpaint_src/mask.png、plate/clean_master 等),
   重放前要先把對應輸入圖 POST /upload/image 傳成同名。
3. 04/05 的遮罩 PNG 當年是 PIL 現畫的,server 可能已無,
   需照 WORKFLOWS.md §5.2 重畫(橢圓+GaussianBlur 羽化)。
