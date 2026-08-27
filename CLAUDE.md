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
| [docs/usage.md](docs/usage.md) | 使用說明：前置需求、`.env` 逐項、CLI 與 Web UI 操作、疑難排解 |
| [docs/architecture.md](docs/architecture.md) | 架構約束、模組結構、狀態機規格、設定參數化、專案獨有測試規則 |
| [.agent/plans/phase-1-foundation.md](.agent/plans/phase-1-foundation.md) | Phase 1：骨架與最小可用流程（extract → pack） |
| [.agent/plans/phase-2-ocr.md](.agent/plans/phase-2-ocr.md) | Phase 2：OCR 輸入端 |
| [.agent/plans/phase-3-image.md](.agent/plans/phase-3-image.md) | Phase 3：ComfyUI 聯想圖生成 |
| [.agent/plans/phase-4-audio.md](.agent/plans/phase-4-audio.md) | Phase 4：VOXCPM2 語音生成 |
| [.agent/plans/phase-5-webui.md](.agent/plans/phase-5-webui.md) | Phase 5：Web UI 與收尾 |
| [.agent/plans/phase-6-execution-plan.md](.agent/plans/phase-6-execution-plan.md) | Phase 6：匯入分頁與工作檔重置（規格與執行計畫合一） |
| [.agent/plans/phase-7-execution-plan.md](.agent/plans/phase-7-execution-plan.md) | Phase 7：文生圖換成 kyoani（FLUX.2-klein-9B + KyoAni LoRA） |

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
| Phase 2 OCR 輸入端 | ✅ 驗收通過（2026-08-24，`two_stage`） |
| Phase 3 聯想圖生成 | ✅ 驗收通過（2026-08-24，方法 2：WebSocket + History） |
| Phase 4 語音生成 | ✅ 驗收通過（2026-08-25，Voice Design 音色） |
| Phase 5 Web UI 與收尾 | ✅ 驗收通過（2026-08-26） |
| Phase 6 匯入分頁與工作檔重置 | ✅ 驗收通過（2026-08-27） |
| Phase 7 文生圖換 kyoani workflow | ✅ 驗收通過（2026-08-28） |

**外部相依**：agent_factory submodule ✅（README 已提供）／ VOXCPM2 ✅（本機 Python 套件，規格已確認）／ ComfyUI workflow ✅（API 格式，2026-08-28 起為 `card_image_kyoani.json`：FLUX.2-klein-9B ＋ KyoAni Style LoRA，需 KJNodes 與 `sageattention` 套件；`card_image_xl.json`／fabricatedXL／SDXL 留作回頭路，需 Impact Pack、rgthree、easy-use、LoraManager）／ 記憶引擎 ✅（`engines/anki_engine.html`，JSZip 載入，媒體以 Blob 常駐記憶體）

**七個 phase 全部完成。** CLI 與 Web UI 皆可用（含匯入與重置），
實產牌組 308 張卡、`deck.zip` 29.4 MB（Phase 7 換 kyoani 後由 35.8 MB 降下來）。

已明確**不做**的事：多工作檔切換（一個服務綁一個 `WORK_DIR`，要換就重啟）、
瀏覽器上傳（本機工具，路徑輸入更直接）、UI 刪除單張卡片（那是內容編輯不是重置）。

> ⚠️ **動到模型或 workflow 前必讀**：VRAM 約束是**模型大小的函數**，不是固定事實。
> 「ComfyUI 常駐 2.4 GB 讓抽取模型只載入 88%、速度剩 1/6」是 **31B q4（19.87 GB）** 下的實測；
> 換成現行的 E4B（11.64 GB）後實測峰值僅 11.7 GB／24 GB，兩者可共存，該約束不成立
> （`COMFYUI_FREE_BEFORE_LLM` 開與不開差 0.9%）。切回 31B 時它會重新變得必要。
> 完整數據見 [logs/2026-08-24_feat_phase-3-image.md](logs/2026-08-24_feat_phase-3-image.md)。
> Phase 4 的 VOXCPM2 再加約 7.5 GB 峰值，三者同時常駐約 18 GB／24 GB 仍有餘裕
> （[logs/2026-08-25_feat_phase-4-audio.md](logs/2026-08-25_feat_phase-4-audio.md)）。
> 2026-08-26 換 SDXL workflow 後 ComfyUI 常駐約 7.2 GB，仍在餘裕內
> （[logs/2026-08-26_feat_sdxl-workflow-and-media-encoding.md](logs/2026-08-26_feat_sdxl-workflow-and-media-encoding.md)）。
>
> **2026-08-28 起這條餘裕沒了**：kyoani workflow 常駐 **17.4 GB**，加 VOXCPM2 峰值
> 7.5 GB 就是 24.9 GB／24 GB。`COMFYUI_FREE_BEFORE_LLM` 從「最佳化」變成**必開**，
> 而且它現在會在 **extract 與 audio 之前**各釋放一次（變數名的「LLM」是歷史包袱，
> 語意以 `release_comfyui()` 的 docstring 為準）。
> 見 [logs/2026-08-28_feat_kyoani-workflow.md](logs/2026-08-28_feat_kyoani-workflow.md)。
>
> 另有一項**已知限制**：部分聯想圖與 `image_prompt` 不符（多元素構圖畫不齊）。
> CFG 調高已實測否決（cfg 13 還會突破防文字約束）；SD 1.5 與 SDXL 之間只是互有勝負。
> 2026-08-28 換上 `workflows/card_image_kyoani.json`（FLUX.2-klein-9B ＋ KyoAni LoRA）後
> **確有改善但仍未根治**——人臉、人群、桌上物件比 SDXL 齊得多，仍會整張改走另一種解讀。
> 風格後綴的四種調整全部實測更差，記在 `prompts/image_prompt_template.md`
> 〈實測否決的調整〉——**不要再試一次**。
>
> 另兩件**已實測、不要重試**的事：kyoani 那組的**負向 prompt 完全無效**
> （cfg 1 ＋ `ConditioningZeroOut`，注入點打的是刻意的孤兒節點 `999`），防文字只能從
> prompt 端治本；**尺寸不要往上加**（1440×900 與 1600×896 都更暗更糊，加步數也修不掉）。
