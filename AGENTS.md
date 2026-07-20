# AGENTS.md — final-project-mvp 開發規範

## 1. 專案範圍

本專案是 loopback-only 的故事視覺工作台。現階段提供 typed catalog、
二十種同角色風格展示與提示詞複製；不執行圖片生成。

知識優先順序：

1. `docs/PROJECT_SPEC.md`
2. `docs/tasks/CODEX_GATEWAY_V2.md`
3. 本文件

## 2. 不可破壞的邊界

1. FastAPI 只綁 `127.0.0.1`；沒有登入系統，禁止綁 `0.0.0.0`。
2. API key、登入憑證與秘密只進環境，不進 Git、日誌或 catalog。
3. 瀏覽器不得提供任意 cwd、sandbox、approval、model provider 或 shell
   command 輸入。
4. Codex app-server 固定 read-only sandbox 與 `approval_policy=never`；
   所有副作用核准要求 fail closed。
5. catalog 與 HTTP payload 必須使用 strict Pydantic schema，拒絕未知欄位。
6. 角色風格必須有唯一 `item_id` 與非空白 `prompt_fragment`。
7. 二十種 code-native preview 共用相同人物幾何；風格只能改變媒材、色盤、
   紋理與光線，不可冒充二十個不同角色。
8. UI 不得聲稱已能生成圖片；場景與分鏡在接線前必須明示為預覽。
9. 正式預覽 URL 只允許安全的同源絕對路徑，不接受外站或本機檔案 URL。
10. 不引入資料庫、ComfyUI、workflow JSON 或舊 Storyboard 引擎依賴。

## 3. 程式碼與測試

- UI 與文件使用繁體中文；程式碼識別符使用英文。
- Python 3.12.10；Ruff、mypy、pytest 為唯一品質工具鏈。
- 對外輸入使用 Pydantic；新函式提供 type hints，公開介面提供 docstring。
- async 路徑不得使用阻塞 I/O；背景 task 必須有明確生命週期與回收責任。
- 前端不使用 `innerHTML`、外部 CDN、遠端字型或未授權圖片。
- 修改功能時同步更新 unit／browser regression。
- 完成修改至少執行：

```bash
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy backend tests
node --check frontend/gateway/app.js
git diff --check
```

## 4. 執行紀錄

具實質影響的動作追加到 `docs/tasks/PROJECT_LOG.md`，不得重寫過去紀錄。
每則包含目的、執行內容、修改檔案、重要命令、驗證結果、發現事項與下一步。
不得在紀錄中寫入秘密。
