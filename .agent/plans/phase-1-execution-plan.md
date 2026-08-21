# Phase 1 執行計畫（待使用者確認）

日期：2026-08-21 ／ 分支：`dev_ai`
依據：[phase-1-foundation.md](phase-1-foundation.md)、[architecture.md](../../docs/architecture.md)、[project-overview.md](../../docs/project-overview.md)、[agent-factory-cheatsheet.md](../notes/agent-factory-cheatsheet.md)

---

## 0. 開工前檢查結果

| 檢查項 | 結果 |
|--------|------|
| 分支 | ✅ `dev_ai` |
| `scripts/init-project.sh` 已生效 | ✅ `.agent/`、`logs/`、`.no-merge` 皆存在，`core.hooksPath=.githooks`，`pre-push` 可執行 |
| `.claude/settings.local.json` 備份 | ⚠️ **專案內無 `.claude/` 目錄**，無檔案可備份 → 本次略過，待該檔出現後每次開工再快照 |
| `agent_factory` submodule | ✅ 已 init：`src/agent_factory` @ `083cec6`（heads/main），內容完整 |
| 已讀文件 | ✅ submodule README、project-overview.md、architecture.md、phase-1-foundation.md、四份通用規範、`tests/fixtures/README.md` |
| VOXCPM2 / ComfyUI 規格 | ⬜ 仍未提供 —— **不影響 Phase 1**（Phase 1 只定義 Protocol，不實作） |

### 已存在的資產（本計畫不重做）

- `pyproject.toml`（骨架，需大幅補充）、`.python-version`（3.12）、`uv.lock`、`.venv`
- `.gitignore`、`.no-merge`、`scripts/merge-to-main.sh`、`.githooks/pre-push`
- `src/anki_deck_builder/__init__.py`（僅骨架）
- `tests/fixtures/`：4 張書頁 JPG、4 份 `ocr_raw/*.txt`、`expected_cards.csv`（**已確認為 39 欄、UTF-8 with BOM**，可直接當欄位順序的驗證基準）

### 尚缺（Task 1.1 補齊）

`.env.example`、`tests/__init__.py`、`tests/conftest.py`、`work/`、`output/`、`prompts/`、`workflows/`

---

## 1. 文件與現況的落差（需一併修正／確認）

| # | 落差 | 現況 | 處置建議 |
|---|------|------|----------|
| D1 | submodule 路徑 | 文件寫 `vendor/agent_factory/`（標「待確認」），實際在 **`src/agent_factory/`** | 沿用現況 `src/agent_factory/`，**改文件不改 repo**（architecture.md、CLAUDE.md、phase-1-foundation.md 三處） |
| D2 | phase 文件位置 | CLAUDE.md 索引指向 `docs/phase-N-*.md`，實際在 `.agent/plans/` | 修正 CLAUDE.md 連結（phase 文件屬 Zone 2，留在 `.agent/plans/` 正確） |
| D3 | 檔名 | 使用者稱 `phase-1-mvp.md`，實際為 `phase-1-foundation.md` | 以實際檔名為準 |
| D4 | CLI 名稱 | `pyproject.toml` 目前 `anki-deck-builder`，文件驗收要求 `anki-builder` | 改為 **`anki-builder`**（驗收標準寫死） |
| D5 | `MODEL_LIMITS` | architecture.md 說「未登錄拋 `KeyError`」；submodule README 說「未登錄套 `DEFAULT_POLICY` + 一次 warning」 | 見 **Q2** |

---

## 2. 已確認的決定（2026-08-21）

| # | 議題 | 決定 |
|---|------|------|
| Q1 | `agent_factory` 匯入方式 | **裝成 editable 路徑相依**：`[tool.uv.sources]` 指向 `src/agent_factory`，匯入寫法與 README 一致（`agent_factory.core`、`agent_factory.limit_runner`）。Task 1.1 先實測 `import agent_factory` 的 `__file__` 確認命中內層套件，不符再回報 |
| Q2 | 速率限制配額登錄 | **寫在 `agents.yaml` 的 `model_params.limits`**。不改 submodule、不注入 `MODEL_LIMITS`。architecture.md〈`MODEL_LIMITS` 必須先登錄〉一節需一併修正（README 的實際行為是套 `DEFAULT_POLICY` + warning，非 `KeyError`） |
| Q3 | endpoint／模型 | **全部走本地 Ollama**，見下方〈模型設定（已確定）〉 |
| Q4 | deck 分派 | **寫成通則**：prompt 只規定「依詞性分派、階層以 `::` 分隔」，領域名由 `--deck-name` 或 LLM 依內容判定帶入，不寫死日語 N2 |

### 模型設定（已確定）

共用 `base_url: http://localhost:11434/v1`（Ollama），兩者皆為本地模型 → `limits.policy: concurrency_only`。

| Agent | 模型 | 用於 | Phase |
|-------|------|------|-------|
| `ExtractAgent` | `gemma4_31b_q4_K_M-optimized` | 文字 → 結構化卡片 | **1** |
| `OCRAgent` | `glm-ocr-optimized:latest` | 影像 → 文字 | 2（值先記錄，Phase 1 不宣告） |

`agents.yaml` 的 model 區塊（Phase 1 只放 ExtractAgent）：

```yaml
ollama: &ollama
  client:
    api_key: ${oc.env:OPENAI_API_KEY, 'ollama'}   # Ollama 不驗證，但 AsyncOpenAI 要求非空字串
    base_url: ${oc.env:OLLAMA_BASE_URL, 'http://localhost:11434/v1'}

models:
  extract-llm: &model_extract
    <<: *ollama
    model: gemma4_31b_q4_K_M-optimized
    params:
      temperature: 0.0
    limits:
      policy: concurrency_only
```

#### 由此確定的三件事

1. **無 API 費用**，但依 testing-strategy.md 與 llm-integration.md，本地模型仍**一律 mock 掉推理呼叫**，Task 1.7／1.9 的 LLM 測試維持「撰寫但不執行」。理由不變：載入 31B 模型佔 VRAM，且會與 Phase 3 的 ComfyUI 搶資源。
2. **Ollama 不回報 usage** → agent_factory 會走全額退款路徑並記一則 error log。這是 README 明載的**已知行為非錯誤**，Phase 1 驗收時看到不必追。
3. `concurrency_only` 下 TPM／RPM 不生效，實際節流只剩 `GLOBAL_CONCURRENCY`（預設 6）。31B q4 在單張 3090 上是序列化執行，Task 1.9 的併發預設值建議先設 **1–2**，實測後再調。

### Task 1.3 衍生的設計決定：extract 是一對多

`ExtractOutput` 定為 `{cards: [ExtractedCard, ...]}` 而非單張卡。依據：
`tests/fixtures/` 四頁 OCR 文字對應 16 張以上卡片，且驗收流程第 3 步明寫
「以任一頁文字作為 `raw_text`（**每頁一列**）」——一列 `raw_text` 必然切出多張卡。

**對 Task 1.9 的影響**：extract 階段不是「就地填欄位」，而是
「讀入 N 列 `raw_text` → 產出 M 列卡片（M > N）」。原始的 `raw_text` 列處理完後標為
`done` 並保留（供追溯與重跑），新卡片列 append 到同一份中間 CSV。
`stages/base.py`（Task 1.6）的骨架因此必須允許單列處理**回傳新增列**，不能只回傳
「更新後的同一列」。此點在 Task 1.6 動手前需再確認一次。

### 仍待查（不擋 Phase 1）

- `gemma4_31b_q4_K_M-optimized` 是否支援影像輸入 —— 決定 Phase 2 的 `vision_direct` 能否用此模型。Phase 1 只走文字路徑，不受影響。

## 3. 任務執行順序

嚴格依 CLAUDE.md「一次一個 task，完成後停下來交付」。每個 task 結束時我會回報產出與測試結果，等你放行才進下一個。

| # | Task | 產出重點 | 測試 |
|---|------|----------|------|
| 1.1 | 專案初始化 | 補齊 `pyproject.toml`（相依、ruff、entry point `anki-builder`）、`.env.example`、缺漏目錄與 `tests/conftest.py`；實測 Q1 的 import 方案 | 安裝可過、`ruff check` 乾淨 |
| 1.2 | 設定層 | `exceptions.py`（4 個例外）、`config.py`（pydantic-settings 巢狀分組、`mask_secret`、階段性驗證） | ✅ 完整單元測試，執行至通過 |
| 1.3 | 型別定義 | `schemas/status.py`、`schemas/card.py`（**39 欄 = 23+6+10**，`field_order()` 為唯一真實來源）、`schemas/extract_output.py` | ✅ 執行至通過；**額外**以 `expected_cards.csv` 表頭比對欄位順序 |
| 1.4 | 狀態機層 | `state/store.py`（BOM、原子寫入 `.tmp`+`os.replace`、`create_empty()`）、`state/selector.py`（篩選規則、`mark_done`/`mark_failed`、階段↔欄位對應表） | ✅ 執行至通過，含模擬中斷驗證原檔完整 |
| 1.5 | Protocol 定義 | `clients/protocols.py` 四個 Protocol 一次到位 | 無（型別宣告） |
| 1.6 | 階段共用骨架 | `stages/base.py`：讀→篩→逐列處理→更新狀態→原子寫回；失敗跳過；`@register_stage` registry；併發上限參數 | ✅ 以假階段測試，執行至通過 |
| 1.7 | agent_factory 接線 | 根目錄 `agents.yaml`（先只宣告 `ExtractAgent`）、`clients/llm_client.py`（薄適配、factory 重用、例外轉 `ExternalServiceError`） | ⚠️ **mock 骨架，撰寫後不執行**，日誌點名人工驗證 |
| 1.8 | Prompt 設計 | `prompts/extract_cards.md`、`prompts/image_prompt_template.md`（含 no-text 約束與統一風格後綴） | 無自動測試；以 `tests/fixtures/README.md` 的九項判準人工檢視 |
| 1.9 | 階段②extract | `stages/extract.py`：文字路徑、`card_id` 唯一性檢查、併發受 `GLOBAL_CONCURRENCY` 控制、**預留 agent／input 型態切換點**供 Phase 2 接 `vision_direct` | 純邏輯 ✅ 執行；LLM 呼叫部分 mock **不執行**（mock 資料取自 `expected_cards.csv`） |
| 1.10 | 階段⑤pack | `stages/pack.py`：還原 23 欄、`media/` 結構、五項完整性驗證、有 `failed` 預設中止（`--allow-failed` 略過）、輸出 ZIP | ✅ 完整測試，執行至通過 |
| 1.11 | CLI 骨架 | `cli.py`：八個子命令全部定義，Phase 1 只有 `extract`/`pack`/`status` 可用，其餘印提示不崩潰；`--force`/`--only-failed` 互斥在解析階段報錯；`status` 表格輸出 | ✅ 完整測試，執行至通過 |

### 全程遵守的紅線

- 不改 `src/agent_factory/` 內任何檔案
- 不自行實作 prompt 載入／structured output 解析／速率限制／重試
- 不引入 FastAPI、Gradio、pypdfium2
- 不在 `stages/base.py` 放特定階段邏輯；不在 `cli.py` 放業務邏輯
- 不執行 `git clean`（尤其 `-x`）
- 不把 `CLAUDE.md`、`.claude/`、`.agent/`、`logs/` 加進 `.gitignore`
- 不主動 merge 進 `main`

---

## 4. 風險與因應

| 風險 | 因應 |
|------|------|
| 兩層 `agent_factory` 造成 import 歧義 | Task 1.1 先做最小實測（實際 `import` 並印 `__file__`）確認解析結果，再定案 |
| 39 欄順序寫錯 → 後續階段全錯位 | `field_order()` 為唯一真實來源；另加一條測試直接比對 `expected_cards.csv` 表頭 |
| structured output 在目標模型上不穩 | 不自行加後處理，先回報實測失敗率並與你討論（改 JSON mode 或換模型） |
| `.env.example` 誤加 endpoint／模型名變數 | 嚴格照 architecture.md：agent_factory 區塊只有 5 個變數，其餘寫在 `agents.yaml` |
| `expected_cards.csv` 只有 16 張代表性卡 | 只當形狀與風格基準，不當覆蓋率基準 |

---

## 5. Phase 1 結束時的交付

1. 十一個 task 全數完成，非付費模組 pytest 全綠
2. 依驗收流程八步交由你手動驗證（安裝 → 缺變數報錯 → 準備 CSV → extract → pack → 載入記憶引擎 → 續作跳過 → Ctrl-C 原子寫入）
3. 驗收通過後：`logs/` 產出改動日誌（點名 `tests/clients/test_llm_client.py`、`tests/stages/test_extract.py` 的 LLM 案例需人工執行）、更新 CLAUDE.md 進度表
4. commit 全程用 `<tag>: <描述> [commit by ai]` 格式
