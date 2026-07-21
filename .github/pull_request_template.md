## 目的

<!-- 這個 PR 解決什麼問題？範圍刻意不包含什麼？ -->

## 修改內容

<!-- 列出主要行為、API、UI、文件或測試變更。 -->

## 驗證

- [ ] `.venv/bin/pytest -m "not browser"`
- [ ] `.venv/bin/ruff format --check .`
- [ ] `.venv/bin/ruff check .`
- [ ] `.venv/bin/mypy backend runtime scripts tests`
- [ ] `node --check frontend/gateway/app.js`
- [ ] `git diff HEAD --check`
- [ ] Browser／UI 有變更時，已安裝 Chromium 並執行完整 `.venv/bin/pytest`

## 安全與交接

- [ ] 未提交秘密、`.env`、`.venv`、`.runtime`、`.local-data`、模型、logs、
      cache、生成暫存或個人絕對路徑
- [ ] 未讓 Browser 執行 shell／runtime 維運，也未繞過 typed API 與 4K 選片 gate
- [ ] 已依實際結果追加一筆 `docs/tasks/PROJECT_LOG.md`，未改寫舊紀錄
- [ ] 尚未完成的 GPU／硬體驗證已明示，沒有把 fixture 或自動化測試冒充實機結果

## 畫面或相容性證據

<!-- UI 變更附本機 screenshot；API/契約變更附 request/response 或相容性說明。 -->
