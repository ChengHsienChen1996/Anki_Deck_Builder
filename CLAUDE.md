# CLAUDE.md — anki-deck-builder

本地製卡 pipeline：書本影像或純文字 → 七階段處理 → 產出「Anki 記憶引擎」可載入的牌組 ZIP。
專案目的、技術棧、資源預算見 [docs/project-overview.md](docs/project-overview.md)。

---

## 開工前必做（最高優先）

每次開工前，先依 [docs/git-workflow.md](docs/git-workflow.md) 完成四項準備，再開始寫任何程式碼：

1. **確認分支**：確保在 `dev_ai` 分支上。
2. **備份權限設定——本專案目前無此步驟**。`.claude/` 目錄不存在，git 也從未追蹤過它（2026-09-06 確認），權限設定全走使用者層級的 `~/.claude/`，repo 內沒有可備份的檔案。
   哪天真的出現了 `.claude/settings.local.json`（它被全域 gitignore 擋住、**無任何版控歷史**，誤刪即永久遺失），依 [docs/git-workflow.md](docs/git-workflow.md) 的「Claude Code 權限設定備份」照做：
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
| [docs/comfyui-workflow-patching.md](docs/comfyui-workflow-patching.md) | **改了 ComfyUI workflow 之後怎麼更新** `card_image_kyoani.json`（四個固化補丁與理由） |
| [docs/architecture.md](docs/architecture.md) | 架構約束、模組結構、狀態機規格、設定參數化、專案獨有測試規則 |
| [.agent/plans/phase-1-foundation.md](.agent/plans/phase-1-foundation.md) | Phase 1：骨架與最小可用流程（extract → pack） |
| [.agent/plans/phase-2-ocr.md](.agent/plans/phase-2-ocr.md) | Phase 2：OCR 輸入端 |
| [.agent/plans/phase-3-image.md](.agent/plans/phase-3-image.md) | Phase 3：ComfyUI 聯想圖生成 |
| [.agent/plans/phase-4-audio.md](.agent/plans/phase-4-audio.md) | Phase 4：VOXCPM2 語音生成 |
| [.agent/plans/phase-5-webui.md](.agent/plans/phase-5-webui.md) | Phase 5：Web UI 與收尾 |
| [.agent/plans/phase-6-execution-plan.md](.agent/plans/phase-6-execution-plan.md) | Phase 6：匯入分頁與工作檔重置（規格與執行計畫合一） |
| [.agent/plans/phase-7-execution-plan.md](.agent/plans/phase-7-execution-plan.md) | Phase 7：文生圖換成 kyoani（FLUX.2-klein-9B + KyoAni LoRA） |
| [.agent/plans/phase-8-execution-plan.md](.agent/plans/phase-8-execution-plan.md) | Phase 8：抽取任務再拆分與生圖 prompt 的 profile 化 |
| [.agent/plans/phase-9-execution-plan.md](.agent/plans/phase-9-execution-plan.md) | Phase 9：OCR 分塊輸入、TTS 語言判定、核對檢查點 |

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
3. 備份 `.claude/settings.local.json`——**本專案目前沒有這個檔案，跳過**（見「開工前必做」第 2 項）
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
| Phase 8 抽取拆分與生圖 prompt profile 化 | ✅ 驗收通過（2026-09-01） |
| Phase 9 OCR 分塊、TTS 語言判定、核對檢查點 | ✅ 驗收通過（2026-09-05） |

**外部相依**：agent_factory submodule ✅（README 已提供）／ VOXCPM2 ✅（本機 Python 套件，規格已確認）／ ComfyUI workflow ✅（API 格式，2026-08-28 起為 `card_image_kyoani.json`：FLUX.2-klein-9B ＋ KyoAni Style LoRA，需 KJNodes 與 `sageattention` 套件，2026-09-02 起再掛一組 `klein_fixer_slider` 修四肢；`card_image_xl.json`／fabricatedXL／SDXL 留作回頭路，需 Impact Pack、rgthree、easy-use、LoraManager）／ 記憶引擎 ✅（`engines/anki_engine.html`，JSZip 載入，媒體以 Blob 常駐記憶體）／ 版面偵測 ✅（Phase 9 起，DocLayout-YOLO DocStructBench 權重，**選配相依** `uv sync --all-extras`，預設關閉；解析出的 torch 與專案完全相同，只多 torchvision）

**九個 phase 全部完成。** CLI 與 Web UI 皆可用（含匯入與重置），
實產牌組 **335 張卡、`deck.zip` 34.2 MB**（2026-09-05 以分塊 OCR ＋ 核對重跑，七階段零失敗）。

流程自 Phase 8 起是**七階段**：`ocr → extract → scene → prompt → image → audio → pack`。
Phase 9 另加一個**不是階段**的 `verify`（對照影像核對，跑在 `extract` 與 `scene` 之間）
——它的單位是「一塊影像 → 動好幾張卡」，`BaseStage` 的一列一狀態裝不下，
比照 `pack` 做成獨立子命令。
`scene`（語義層，模型無關）與 `prompt`（語法層，依 profile）是從 `extract` 拆出來的
——**換文生圖模型只要改 `.env` 的 `IMAGE_PROMPT_AGENT` 一行再 `prompt --force`，
`image_scene` 一個字都不動**。

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
> **兩個效能基準已過期（2026-09-05 實測）**，不要拿舊數字做規劃：
> `image` 從 15 秒/張變成 **30.6 秒/張**（335 張 2:50:29），差額對應 2026-09-02
> 加掛的 `klein_fixer_slider`。
>
> `extract` 曾在 2026-09-02～09-05 之間慢到約 15 分鐘/頁——`agents.yaml` 的抽取
> 模型被換成 **29.26 GB** 的 `gemma4_26b-a4b-it-q8_0-128K`，而顯卡只有 24 GB，
> **只有 70% 上卡**（模型本身超標，`COMFYUI_FREE_BEFORE_LLM` 救不了）。
> **2026-09-05 已改用 `gemma4_31b_q4_K_M-16K`**：19.71 GB、**100% 上卡**、
> 一頁 6.7~8.1 分鐘，而兩頁對照實測**品質打平**（p11 的 `reading` 13/16 完全相同、
> 連錯的那筆都一樣）。
> ⚠️ **「E4B 優於 31B」這個舊結論已於 2026-09-05 實測推翻。** 2026-08-23 那次
> 量到的是兩個混淆變數而非模型能力：ComfyUI 常駐 2.4 GB 把 31B 擠出 VRAM
> （只載入 85~88%、速度崩到 1/6），以及當時抽取是**整頁送**的（Phase 8 之後已改成
> 分段）。兩個前提都已消失。完整對照數據在 `agents.yaml` 的註解裡。
>
> **舊實測要標明前提，否則會被當成永久事實而擋住正確的選擇。**
> 這次沒有重測 E4B，所以能下的結論是「31B q4 不劣於 26B q8 且明顯更快」，
> 不是「參數量越大越好」。26B 也有 Q4_K_M 版本（本機尚未 pull），
> 那是想兼顧品質與 VRAM 時下一個該試的。
>
> 另有一項**已知限制**：部分聯想圖與場景不符（多元素構圖畫不齊）。
> CFG 調高已實測否決（cfg 13 還會突破防文字約束）；SD 1.5 與 SDXL 之間只是互有勝負。
> 2026-08-28 換上 `workflows/card_image_kyoani.json`（FLUX.2-klein-9B ＋ KyoAni LoRA）後
> **確有改善但仍未根治**——人臉、人群、桌上物件比 SDXL 齊得多，仍會整張改走另一種解讀。
> 風格後綴的四種調整全部實測更差，記在 `prompts/image_prompt_template.md`
> 〈實測否決的調整〉——**不要再試一次**。
>
> **文字的判準是「壓低出現率」，不是「零出現」**（2026-09-01 使用者裁示）。
> 零出現做不到：三種手段的實測結果記在 `image_prompt_template.md`〈已實測的天花板〉，
> 唯一有效的是「不在場景裡要求會帶字的道具」（違規率 19% → 8%），
> 但模型仍會在乾淨場景上自發加道具再寫字。實測隨機 24 張中 4 張（17%）含假文字，
> **這是目前的正常水準，不要再往這個方向投入**。
>
> 另兩件**已實測、不要重試**的事：kyoani 那組的**負向 prompt 完全無效**
> （cfg 1 ＋ `ConditioningZeroOut`，注入點打的是刻意的孤兒節點 `999`），壓低文字只能從
> 場景端做（見上一段）；**尺寸不要往上加**（1440×900 與 1600×896 都更暗更糊，加步數也修不掉）。
>
> **`OCR_CHUNK_PAGE_ROTATION` 預設已於 2026-09-10 改為 `none`，不要改回 `auto`。**
> 舊的 `auto` 評分用「文字框聯集面積」，而偵測失敗時會吐一個佔全頁一半的低信心
> 兜底框，**失敗的分數贏過成功**（p35：0° 是 54 框／0.428，cw 是 1 框／0.537）。
> 41 頁實測只對 13 頁（32%），錯的 28 頁被切成 628 px 寬、**穿過每一行文字**的直條，
> 左右半截各自送 OCR 再串接——`構造を分析する` 變成 `構造を分`，只含中文的碎片
> 被抽取模型判成中文教材而生出 80 張拼音卡。重跑後卡片密度從 20.8 收斂到
> 14.8 張/頁、拼音卡歸零。評分已改用**文字框數**（正常 33–67 vs 崩掉 0–8，
> 這是唯一分得開的訊號），但正確性仍不押在自動判斷上——**自己把影像擺正**。
>
> 同一次**實測否決兩道候選防線，不要再試**：「切線切穿文字框的比例」（壞計畫
> 是拿僅有的 1 個框算出 100%，而正確計畫在 p8 就有 52%）與「塊形／長寬比」
> （正確 0.06–0.28 對錯誤 0.14–1.00，區間重疊）。理由記在
> `clients/layout_detector.py` 的 `_best_rotation`。
>
> **p28 掉 `形／型` 與 `刑事` 的原因不是 `contains_text()`**——那是 2026-09-10
> 未經驗證的推論，隔日實證推翻：p28 只丟掉 37 px 寬的頁緣，兩條詞目都落在
> **有送出去**的塊裡。真正的原因是 `looks_degenerate()` 把整塊丟掉，
> 而退化的塊前面往往完全正確（塊 0 前 12 行是完整的 `形／型`，之後 ``` 重複
> 1332 次；塊 4 前 8 行是完整的 `刑事`，之後 `.....` 重複 1327 次）。
> 已改為 `truncate_runaway()` 先砍尾巴再判退化，兩條詞目都救回來了。
>
> **推論要標成推論。** 那條錯誤歸因寫進了 CLAUDE.md 與 log，差點讓下一個人去改
> 一個沒有壞的函式——**沒實測過的根因不要寫得像結論**。
>
> **單獨子命令的 VRAM 讓渡已於 2026-09-10 修好**（`cli.py` 的 `GPU_COMMAND_KEEP`、
> `web/service.py` 的 `_STAGE_KEEP`）。原本 `release_all_gpu()` 只接在 `run-all`，
> 而 `ocr` 是唯一一個連 `release_comfyui()` 都沒有的子命令——跑完 `image` 之後
> 單獨跑 `ocr` 必定 CUDA OOM（實測 28/28 全滅）。現在每個用到 GPU 的子命令
> 開跑前都會讓渡，並以 `keep` 保住它自己正要用的東西（`image` 不會清掉 ComfyUI）。
>
> 這個讓渡**受 `COMFYUI_FREE_BEFORE_LLM` 與 `MODEL_UNLOAD_BEFORE_STAGE` 管**
> （2026-09-10 使用者裁示）：它每次呼叫都會跑，「開跑前」實質等同「每個階段前」，
> 無視開關等於讓設定悄悄失效。**代價**：把開關關掉又用 kyoani 時仍會 OOM——
> 但該組態下這個開關本來就標示「必開」。
>
> **「重拍照片修 OCR 讀不出小寫假名與濁點」已於 2026-09-12 實測否決，不要重拍。**
> 兩輪重拍 A/B（p19、p22 共 9 筆目標，控制組同時重跑舊照片）天花板都是
> **修好 2/9**，而代價是 41 頁重拍加約 14 小時重跑。三件實測結論：
> **對焦（銳利度）是唯一有效的變因**；**後製不要動對比與 levels**——把對比拉到
> 最高會截掉灰階過渡（純黑截斷 0% → 2.6%），注音假名筆畫細被吃掉最多，
> 結果比不後製那組還差；**紙面 240／對比 170 那兩個門檻是假的**，它們只是
> 「同批的 p41 碰巧達到過」，而達標的那一輪反而最差。
> 另外 GLM-OCR 在同一影像上**近乎決定性**（同影像跑兩次，讀音行字數、小寫假名數、
> 濁點數完全相同），所以這類對照不必做多次重複測量。
>
> **正解是 `scripts/fix-reading-marks.py`**：那批錯誤的正確讀音是 SudachiPy 從
> 漢字詞目算出來的，**不是從影像讀的**——答案一直在手上。2026-09-12 實跑，
> 605 張卡的待確認筆數 109 → 30（`marks` 65→1、`reading` 24→9）。
> 完整數據見 [.agent/plans/photo-quality-ab.md](.agent/plans/photo-quality-ab.md)。
>
> **VOXCPM2 的「7.5 GB」量錯了東西（2026-09-12 實測更正）。** 那是
> `torch.cuda.empty_cache()` **之後**的常駐量（實測 7386 MB），而它跑起來的
> **工作集是 16.5 GB**（`reserved`，也就是 nvidia-smi 看到的數字；`allocated`
> 只有 5.4 GB，中間約 9 GB 是 allocator 抓著的空閒區塊）。
> 所以「三者同時常駐約 18 GB／24 GB 仍有餘裕」這句話是**用常駐量算的，不成立**：
> kyoani 的 17.4 GB ＋ TTS 的 16.5 GB ＝ 33.9 GB，遠超過 24 GB。
> `COMFYUI_FREE_BEFORE_LLM` 在 audio 之前那次釋放不是最佳化，是**前提**。
>
> 三件**已實測**的事：
> **① `VOXCPM2_OPTIMIZE` 與顯存無關**——開與關的曲線幾乎重疊（reserved 成長
> +1510 vs +1516 MB），關掉不會省，不要再試。
> **② 單一次執行內不會越跑越高**，也因此**不要在 `audio.py` 迴圈裡定期
> `empty_cache()`**：40 段實測 reserved 在前十幾段就穩定（一次 +1508 MB 的跳升
> 後完全平坦），`allocated` 全程不動。`empty_cache()` 能要回 9.1 GB，但**下一次
> 合成立刻全部拿回去**——那是工作集，不是洩漏。
> **③ 但「跨執行」曾經會疊加，那是 bug，2026-09-12 已修。**
> Web UI 每按一次語音按鈕就多載入一份 5.4 GB 的模型而舊的不走
> （`allocated` 5432 → 10856 MB），**第三次必定 OOM**（實測 22.59 GiB／24 GB，
> 與使用者回報的數字一致）。根因是 `release_gpu_cache()` 只做 `empty_cache()`，
> 而**它只還得了「已經沒人用」的區塊**——上一輪的 client 帶著參照循環
> （client ↔ stage ↔ settings），在循環回收器跑到之前那些權重仍是活著的張量。
> 修法是在 `empty_cache()` **之前**加 `gc.collect()`（`clients/tts_client.py`），
> **順序不可顛倒**。修後同樣三輪每輪都回到 8 MB。
> CLI 感覺不到這個 bug，因為每個指令是獨立行程——**長駐行程才是這類問題的現場**。
> 腳本與完整曲線：`scripts/ab-tts-vram.py`、`work/ab-tts-vram/`。
