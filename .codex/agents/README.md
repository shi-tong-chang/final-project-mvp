# Project agent slots

這個目錄刻意納入 Git，讓 GitHub 與每個 clone 都保留專案層 Agent 的固定
位置。待組員交付後，請把檔案放在：

- `character_generator.toml`
- `scene_generator.toml`

兩份 TOML 到位前，網站上的角色／場景生成維持 `pending`；不要建立假
Agent 或把 code-native 預覽當成生成結果。接線規範以 repository 根目錄的
`AGENTS.md` 與 `docs/PROJECT_SPEC.md` 為準。

Agent 完成正式圖片後，應由受信任的 controller 呼叫
`scripts/register_generated_asset.py`：角色一次登錄前、左、右、後四視圖，
場景登錄一張定稿圖。Browser 不得取得 Agent 設定路徑、developer
instructions、cwd、shell 參數或本機來源圖片路徑。
