# CLAUDE.md — anki-deck-builder

本地製卡 pipeline：書本影像或純文字 → 五階段處理 → 產出「Anki 記憶引擎」可載入的牌組 ZIP。
專案目的、技術棧、資源預算見 [docs/project-overview.md](docs/project-overview.md)。

---

## 開工前必做（最高優先）

每次開工前，先依 [docs/git-workflow.md](docs/git-workflow.md) 完成四項準備，再開始寫任何程式碼：

1. **確認分支**：確保在 `dev_ai` 分支上。
2. **備份權限設定**：快照 `.claude/settings.local.json`。此檔被全域 gitignore 擋住、**無任何版控歷史**，誤刪即永久遺失。
   ```bash
   mkdir -p ~/.claude/settings-backups/anki-deck-builder
   cp -p .claude/settings.local.json \
      ~/.claude/settings-backups/anki-deck-builder/settings.local.json.$(date +%Y-%m-%d)
   ```
3. **讀 `src/agent_factory/README.md`**：LLM 與 OCR 呼叫皆建立在此 submodule 上。它已提供 agent 建立、prompt 載入、structured output 解析、**速率限制**——這些本專案都不重複實作。不讀就動手，極可能重造已有的輪子。
4. **確認外部介面狀態**：VOXCPM2 與 ComfyUI workflow 尚未提供實際規格，目前以 Protocol 抽象隔離。動手前先確認你要做的 phase 是否受阻。

> 本專案採「不上 main 的檔案隔離」：`CLAUDE.md`、`.claude/`、`.agent/`、`logs/` 在 `dev_ai` 有版控但不合併進 `main`。合併一律用 `scripts/merge-to-main.sh`，勿手動 `git merge`。
>
> **另需先讀** [docs/architecture.md](docs/architecture.md) 的「架構約束」——五條約束定義本專案設計底線，違反會使狀態機與可替換性失效。

---

## 專案文檔索引

| 文檔 | 說明 |
|------|------|
| [docs/project-overview.md](docs/project-overview.md) | 目的、目標與非目標、技術棧、硬體預算、環境工具鏈 |
| [docs/architecture.md](docs/architecture.md) | 架構約束、模組結構、狀態機規格、設定參數化、專案獨有測試規則 |
| [.agent/plans/phase-1-foundation.md](.agent/plans/phase-1-foundation.md) | Phase 1：骨架與最小可用流程（extract → pack） |
| [.agent/plans/phase-2-ocr.md](.agent/plans/phase-2-ocr.md) | Phase 2：OCR 輸入端 |
| [.agent/plans/phase-3-image.md](.agent/plans/phase-3-image.md) | Phase 3：ComfyUI 聯想圖生成 |
| [.agent/plans/phase-4-audio.md](.agent/plans/phase-4-audio.md) | Phase 4：VOXCPM2 語音生成 |
| [.agent/plans/phase-5-webui.md](.agent/plans/phase-5-webui.md) | Phase 5：Web UI 與收尾 |

## 通用規範文檔索引

> 以下為跨專案共用規範，**內容不因本專案而改**。專案文檔中凡涉及這些主題，一律以引用為準。

| 文檔 | 說明 |
|------|------|
| [docs/project-structure.md](docs/project-structure.md) | 專案目錄結構規範（src layout、Zone 1／Zone 2 版控分區） |
| [docs/coding-style.md](docs/coding-style.md) | 編碼風格（async 優先、命名、型別標註、錯誤處理） |
| [docs/testing-strategy.md](docs/testing-strategy.md) | 測試策略（非付費自動測試 vs 付費 API mock 骨架＋人工執行） |
| [docs/git-workflow.md](docs/git-workflow.md) | 版控流程（dev_ai 分支、不上 main 的檔案隔離、Claude Code 設定備份、commit 格式） |
| [docs/change-log-guide.md](docs/change-log-guide.md) | `/logs` 改動總結規範與格式 |
| [docs/llm-integration.md](docs/llm-integration.md) | LLM API 整合（Agent 設定、速率限制、prompt 管理、本地模型特例） |

---

## 協作準則

1. **一次只做一個 phase**。開工前先讀該 phase 文件，列出執行計畫給使用者確認，才動手。計畫書寫入 `.agent/plans/`，不要放 `docs/` 或根目錄。
2. **一次只做一個 task**，完成後停下來交付，不連續往下做。
3. **重大技術選擇有疑義時停下來問**，不擅自決定。文件中標註「待確認」之處尤其如此。
4. **跑不起來先報告**——附錯誤訊息、已嘗試的方法、卡在哪裡，不無限 debug。
5. **不過度工程**。抽象只加在文件明確標示的邊界（Protocol、stage registry），其餘直接寫。
6. **commit 與改動日誌**遵循 [docs/git-workflow.md](docs/git-workflow.md) 與 [docs/change-log-guide.md](docs/change-log-guide.md)。

---

## 首次開工檢查清單

1. 確認專案已執行過 `scripts/init-project.sh`（`.agent/`、`logs/`、`.no-merge` 存在，pre-push hook 已啟用）；未執行則先執行
2. `git branch --show-current` 確認在 `dev_ai`，否則依 git-workflow.md 從 `main` 建立
3. 備份 `.claude/settings.local.json`（見「開工前必做」第 2 項）
4. 讀 [docs/project-overview.md](docs/project-overview.md) 與 [docs/architecture.md](docs/architecture.md)
5. 讀 [.agent/plans/phase-1-foundation.md](.agent/plans/phase-1-foundation.md)
6. 將 Phase 1 執行計畫寫入 `.agent/plans/`，交使用者確認
7. 確認後從 Task 1.1 開始，一次一個 task

---

## 進度追蹤

> 每個 phase 驗收通過後由 AI 更新此表。

| Phase | 狀態 |
|-------|------|
| Phase 1 骨架與最小可用流程 | ✅ 驗收通過（2026-08-21） |
| Phase 2 OCR 輸入端 | ⬜ 未開始 |
| Phase 3 聯想圖生成 | ⬜ 未開始 |
| Phase 4 語音生成 | ⬜ 未開始 |
| Phase 5 Web UI 與收尾 | ⬜ 未開始 |

**外部相依**：agent_factory submodule ✅（README 已提供）／ VOXCPM2 ✅（本機 Python 套件，規格已確認）／ ComfyUI workflow ⬜
