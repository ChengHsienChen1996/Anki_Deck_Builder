# Phase 1 — 骨架與最小可用流程

## 目標

建立專案骨架、設定層、型別、狀態機與 CLI，並打通 **extract → pack** 這段最小流程。本階段完成後，你能手動準備一份含 `raw_text` 的中間 CSV，執行兩個指令，得到一份可被 Anki 記憶引擎正確載入的 ZIP（無圖無音，僅文字卡片）。

本階段同時**預埋後續所有 phase 需要的介面與欄位**，避免後續 phase 修改核心。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] `uv pip install -e ".[dev]"` 可成功安裝，`uv run anki-builder --help` 正常顯示全部子命令
- [ ] `ruff check` 無錯誤
- [ ] `.env` 缺必填變數時，啟動報錯明確指出變數名
- [ ] `CardRow` 涵蓋 architecture.md 定義的**全部三類欄位**（含 Phase 3、4 才會用到的欄位）
- [ ] `clients/protocols.py` 定義**全部四個** Protocol
- [ ] 中間 CSV 讀寫為原子操作，模擬中斷後檔案不損毀
- [ ] `status` 子命令對測試 CSV 輸出正確的各階段統計
- [ ] 以手工準備的 `raw_text` CSV 執行 `extract` → `pack`，產出的 ZIP 可載入記憶引擎並正常複習
- [ ] 非付費模組的 pytest 全數通過
- [ ] `tests/clients/test_llm_client.py` 已撰寫 mock 骨架但未執行，並在改動日誌點名

---

## 子任務拆分

### Task 1.1 — 專案初始化

**產出**
```
pyproject.toml
.gitignore
.no-merge
.env.example
scripts/            # 由使用者提供的通用腳本複製而來
.githooks/          # 同上
src/anki_deck_builder/__init__.py
tests/__init__.py
tests/conftest.py
work/.gitkeep
output/.gitkeep
prompts/.gitkeep
workflows/.gitkeep
```

**要求**
1. **先確認版控隔離機制已就位**（依 [git-workflow.md](git-workflow.md)〈新專案啟用〉）：
   - `.no-merge`、`.gitignore`、`.githooks/`、`scripts/` 已複製進專案根目錄
   - 執行 `scripts/init-project.sh` 建立 `.agent/`、`logs/` 並啟用 pre-push hook
   - 若這些檔案不存在，**停下來向使用者索取**，不要自行撰寫合併腳本或 hook
   - 同時建立本專案的設定備份目錄：`mkdir -p ~/.claude/settings-backups/anki-deck-builder`
2. `pyproject.toml` 宣告套件，設定 console script entry point 為 `anki-builder`
3. **加入 `agent_factory` submodule**
   - 若尚未加入，停下來向使用者詢問 repo 位址與預期路徑（實際路徑 `src/agent_factory/`）
   - 於 `pyproject.toml` 將其設為本地相依，確保 `import agent_factory` 可運作
   - README 指示以 `uv sync` 安裝其依賴，確認與本專案的相依解析無衝突
4. 相依分為主要與 `[dev]` 兩組：
   - 主要：`pydantic`、`pydantic-settings`、`httpx`、`aiofiles`，加上 agent_factory 及其相依（`openai-agents`、`omegaconf`、`aiolimiter`、`tiktoken` 等，實際清單以 submodule 的 `pyproject.toml` 為準）
   - dev：`pytest`、`pytest-asyncio`、`ruff`
   - **本階段不加** FastAPI、Gradio、pypdfium2，各自留到對應 phase
5. ruff 設定寫入 `pyproject.toml`
6. `.gitignore` 須排除 `.env`、`work/`、`output/`、`__pycache__`、`.venv`
   - **注意**：`.gitignore` **不負責** Zone 2 隔離。`CLAUDE.md`、`.claude/`、`.agent/`、`logs/` 須照常在 `dev_ai` commit 以保留本地歷史，其隔離由 `.no-merge` 機制處理
   - `.claude/settings.local.json` 已被使用者的**全域** gitignore 擋住，**不需**也**不應**在專案 `.gitignore` 重複排除
7. `.no-merge` 確認含本專案需隔離的路徑；本專案無額外隔離需求，沿用通用清單即可
8. `.env.example` 依 architecture.md「環境變數清單」全數列出。注意 agent_factory 區塊只有 `OPENAI_API_KEY`、`YAML_SETTINGS_FILE`、`GLOBAL_CONCURRENCY`、`RPM`、`TPM`——**不要自行新增 endpoint 或模型名變數**

**不要做**
- 不要在此 task 寫任何業務程式碼
- 不要把 `CLAUDE.md`、`.agent/`、`logs/` 加進 `.gitignore`——那會使它們失去本地版控，違反 Zone 2 設計
- **不要執行 `git clean`（尤其帶 `-x`）**——會清掉未追蹤的 `.claude/settings.local.json`，該檔無版控歷史，誤刪即永久遺失。需要清理工作目錄時停下來與使用者確認

---

### Task 1.2 — 設定層

**產出**
```
src/anki_deck_builder/config.py
src/anki_deck_builder/exceptions.py
tests/test_config.py
```

**要求**
1. `exceptions.py` 定義專案例外階層，至少包含：
   ```python
   class AnkiBuilderError(Exception): ...
   class ConfigurationError(AnkiBuilderError): ...
   class ExternalServiceError(AnkiBuilderError): ...
   class StageProcessingError(AnkiBuilderError): ...
   ```
2. `config.py` 以 pydantic-settings 載入 `.env`，**巢狀分組**：
   ```python
   settings.llm.endpoint
   settings.llm.model_name
   settings.comfyui.base_url
   settings.comfyui.nodes.positive_node_id
   settings.comfyui.nodes.positive_field
   settings.tts.speaker_id
   settings.paths.work_dir
   ```
3. 必填缺漏時拋 `ConfigurationError`，訊息須含變數名
4. 提供金鑰遮罩工具函式（例如 `mask_secret("sk-abc123") -> "sk-****23"`）
5. **階段性驗證**：不在載入時檢查 ComfyUI／TTS 可達性或 workflow 檔案存在，那些留給對應階段

**測試**：完整單元測試，執行至通過。涵蓋正常載入、缺必填、型別轉換失敗、遮罩函式。

---

### Task 1.3 — 型別定義

**產出**
```
src/anki_deck_builder/schemas/__init__.py
src/anki_deck_builder/schemas/status.py
src/anki_deck_builder/schemas/card.py
src/anki_deck_builder/schemas/extract_output.py
tests/schemas/test_card.py
tests/schemas/test_status.py
```

**要求**
1. `status.py`：`StageStatus` 列舉，值為 `pending` / `done` / `failed`
2. `card.py`：`CardRow` Pydantic model，**必須涵蓋 architecture.md 定義的全部三類欄位**
   - (a) 引擎 schema 欄位 23 個
   - (b) 製卡中間欄位 6 個
   - (c) 狀態與錯誤欄位 10 個
   - 所有欄位提供合理預設值（字串預設空字串，狀態預設 `PENDING`）
   - **欄位順序必須固定為 (a) → (b) → (c)**，提供 `field_order()` 類方法供 CSV 寫入使用
3. `extract_output.py`：`ExtractOutput` model，定義 LLM structured output 的回傳結構，欄位對應階段 ② 需填寫的內容

> **本階段實作但不使用，為 Phase 3 / 4 預留**：`image_prompt`、`tts_front_text`、`tts_back_text`、`image_status`、`audio_front_status`、`audio_back_status` 等欄位在 Phase 1 不會被填寫，但**必須現在就定義完整**。

**測試**：完整單元測試，執行至通過。涵蓋序列化／反序列化、欄位順序穩定性、預設值。

---

### Task 1.4 — 狀態機層

**產出**
```
src/anki_deck_builder/state/__init__.py
src/anki_deck_builder/state/store.py
src/anki_deck_builder/state/selector.py
tests/state/test_store.py
tests/state/test_selector.py
```

**要求**

`store.py`：
1. 異步讀寫中間 CSV，UTF-8 with BOM，正確處理引號與欄位內 `\n`
2. **原子寫入**：寫入 `<path>.tmp` 後以 `os.replace` rename，不得直接覆寫
3. 檔案不存在時提供 `create_empty()` 建立僅含表頭的檔案

`selector.py`：
1. 依階段名稱與旗標篩選待處理列：
   ```python
   def select_pending(
       rows: list[CardRow],
       stage: str,
       force: bool = False,
       only_failed: bool = False,
   ) -> list[CardRow]: ...
   ```
2. 篩選規則依 architecture.md「篩選規則」表
3. 提供統一的狀態更新介面：
   ```python
   def mark_done(row: CardRow, stage: str) -> None: ...
   def mark_failed(row: CardRow, stage: str, error: str) -> None: ...
   ```
4. 階段名稱與狀態欄位的對應表集中於此，其他模組不得自行組字串

**測試**：完整單元測試，執行至通過。**必須涵蓋**：空檔、單列、全 failed、混合狀態、旗標所有組合、**模擬寫入中斷驗證原檔完整**。

---

### Task 1.5 — Protocol 定義

**產出**
```
src/anki_deck_builder/clients/__init__.py
src/anki_deck_builder/clients/protocols.py
```

**要求**
1. 依 architecture.md「介面契約」章節，定義**全部四個** Protocol
2. 每個 Protocol 加 docstring 說明用途與待確認事項

> **本階段實作但不使用，為 Phase 2 / 3 / 4 預留**：`OCRClientProtocol`、`ImageGenClientProtocol`、`TTSClientProtocol` 在 Phase 1 不會有實作，但介面現在就要定義完成。

**不要做**：不要在此 task 實作任何具體 client。

---

### Task 1.6 — 階段共用骨架

**產出**
```
src/anki_deck_builder/stages/__init__.py
src/anki_deck_builder/stages/base.py
tests/stages/test_base.py
```

**要求**
1. 提供階段共用執行骨架，封裝這段所有階段共通的流程：
   ```
   讀取中間 CSV → 篩選待處理列 → 逐列處理 → 更新狀態 → 原子寫回
   ```
2. 單列處理失敗時**捕捉例外、記錄錯誤、繼續下一列**（約束 3）
3. 提供階段 registry，以裝飾器註冊：
   ```python
   @register_stage("extract")
   class ExtractStage(BaseStage): ...
   ```
4. 各階段子類只需實作「單列如何處理」與「本階段需要哪些設定」
5. 支援併發控制參數，由各階段指定上限

**不要做**：不要在 `base.py` 內加入任何特定階段的業務邏輯。

**測試**：完整單元測試，執行至通過。以假階段驗證失敗跳過、狀態更新、registry 查表。

---

### Task 1.7 — agent_factory 接線與 LLM 適配層

**前置**：先讀 `src/agent_factory/README.md`。本 task 的一切以該 README 為準。

**產出**
```
agents.yaml                                  # 專案根目錄
src/anki_deck_builder/clients/llm_client.py
tests/clients/test_llm_client.py
```

**要求**

1. **確認 submodule 已就位**
   - `git submodule update --init --recursive` 後 `import agent_factory` 可成功
   - 若 submodule 未加入專案，**停下來詢問使用者 repo 位址與預期路徑**，不要自行 clone

2. **撰寫 `agents.yaml`**（置於**專案根目錄**，理由見 architecture.md）
   - 本階段先宣告 `ExtractAgent`（另兩個 agent 於 Phase 2 加入）：
     ```yaml
     model_instruction:
       dynamic_prompt: false
       instruction_file_path: ${__dir__}/prompts/extract_cards.md
     output_schema: anki_deck_builder.schemas.extract_output.ExtractOutput
     ```
   - model 區塊的 `base_url` 與 `model` 由使用者填入，`.env.example` 不預設值
   - `.env` 的 `YAML_SETTINGS_FILE` 指向此檔

3. **`llm_client.py` 只做薄適配**
   ```python
   from agent_factory.core import create_agent_factory
   from agent_factory.limit_runner import LimitAgentRunner
   ```
   - 實作 `LLMClientProtocol.run_agent(agent_name, input_)`
   - factory 實例建立一次後重用，不要每次呼叫都重建
   - 外部例外轉為 `ExternalServiceError`
   - **不要實作**：prompt 載入、structured output 解析、速率限制、重試——這些 agent_factory 都已提供

4. **速率限制配額寫在 `agents.yaml`**（2026-08-21 已決定，見 [phase-1-execution-plan.md](phase-1-execution-plan.md) Q2）
   - 於 `model_params.limits` 宣告 `policy: concurrency_only`（本地 Ollama）
   - **不改 submodule、不注入 `MODEL_LIMITS`** —— YAML 的優先序最高
   - 未登錄 `MODEL_LIMITS` **不會**拋 `KeyError`（舊敘述有誤），只會套 `DEFAULT_POLICY` 並發一次 warning

**測試**：**Mock 骨架，撰寫後不執行**。mock 對象為 `LimitAgentRunner.run`。涵蓋正常 structured output、`KeyError`（agent 名稱查無 → `get_agent_by_name`）、rate limit、timeout。於改動日誌點名需人工驗證。

**不要做**
- 不要用 httpx 自己打 OpenAI-compatible endpoint——那會繞過 agent_factory 的速率限制
- 不要在 `.env` 新增 `LLM_ENDPOINT` / `LLM_MODEL_NAME` 之類的變數——endpoint 與模型名屬 `agents.yaml`
- 不要修改 submodule 內的任何檔案

---

### Task 1.8 — Prompt 設計

**產出**
```
prompts/extract_cards.md          # agents.yaml 以 ${__dir__}/prompts/... 引用
prompts/image_prompt_template.md
```

> 這兩份是 **agent_factory 的靜態 prompt 檔**，由 `agents.yaml` 的 `instruction_file_path` 指向，程式不直接讀取。

**要求**

**對齊樣板**：`tests/fixtures/` 已備有四頁真實書頁的 OCR 文字（`ocr_raw/`）與理想抽取結果（`expected_cards.csv`）。prompt 的品質判準：餵入 `ocr_raw/` 能否產出接近 `expected_cards.csv` 的結果，逐項比對表見 `tests/fixtures/README.md`。

`extract_cards.md`：
1. 輸入為 `raw_text`，輸出為結構化卡片欄位
2. 明確要求：缺失資訊（讀音、例句）由模型知識補全
3. 明確要求：系統欄位（`created_at` 等）一律留空
4. 明確要求：同時產出 `image_prompt`、`tts_front_text`、`tts_back_text`
5. **領域不預設**——prompt 須能處理任何學習領域，領域由呼叫時的參數指定

`image_prompt_template.md`：
1. 統一風格前後綴，確保整套卡片視覺一致
2. **必含 `no text` 類約束**——記憶錨點圖不得含任何文字
3. 輸出為英文 prompt

**測試**：不需要。人工檢視內容合理性。

---

### Task 1.9 — 階段 ②：extract

**產出**
```
src/anki_deck_builder/stages/extract.py
tests/stages/test_extract.py
```

**要求**
1. 繼承 `BaseStage`，註冊為 `"extract"`
2. 讀 `raw_text` → 呼叫 LLM client → 寫回 (a)(b) 類欄位 → 更新 `extract_status`
   - **本階段只實作文字路徑**（`ExtractAgent`）。影像直送路徑（`VisionExtractAgent`）於 Phase 2 加入
   - 但**現在就要把「用哪個 agent、送什麼 input」設計成可切換**，Phase 2 只需補分支不需重構
3. 併發受 `GLOBAL_CONCURRENCY` 控制
4. `card_id` 唯一性檢查，重複時該列標為 `failed` 並記錄原因
5. 支援 `--deck-name` 指定牌組名稱前綴，未指定時由 LLM 依內容判定

**測試**：純邏輯部分（唯一性檢查、狀態更新、併發控制、agent 選擇分支）撰寫**可執行**測試；LLM 呼叫部分 mock **不執行**。mock 回應的內容直接取材 `tests/fixtures/expected_cards.csv`，確保測試資料貼近真實形狀。

> **本階段實作但不使用，為 Phase 2 預留**：agent 名稱與輸入型態的切換點。Phase 2 會在此接上 `vision_direct` 模式。

---

### Task 1.10 — 階段 ⑤：pack

**產出**
```
src/anki_deck_builder/stages/pack.py
tests/stages/test_pack.py
```

**要求**
1. 移除 (b) 製卡中間欄位與 (c) 狀態欄位，還原為引擎 schema 的 23 欄
2. 組 `media/img/`、`media/audio/` 目錄結構（Phase 1 時目錄可為空）
3. 完整性驗證：
   - 必填欄位 `card_id` `deck` `card_type` `front` `back` 非空
   - `card_id` 無重複
   - 系統欄位確實為空
   - `image_front` / `audio_*` 若非空，對應檔案須實際存在
4. 存在 `failed` 列時**預設中止**並列出清單；`--allow-failed` 可略過
5. 輸出 ZIP，結構依 architecture.md「輸出 ZIP 結構」

**測試**：完整單元測試，執行至通過。純本地檔案操作，無外部相依。

---

### Task 1.11 — CLI 骨架

**產出**
```
src/anki_deck_builder/cli.py
tests/test_cli.py
```

**要求**
1. argparse 建立**全部八個**子命令（`ocr` `extract` `image` `audio` `pack` `run-all` `status` `serve`）
2. 本階段只有 `extract`、`pack`、`status` 實際可用；其餘印出「此功能於 Phase N 實作」後結束，**不得報錯崩潰**
3. `asyncio.run()` 為統一進入點
4. `--force` 與 `--only-failed` 同時指定時於解析階段報錯
5. `status` 輸出格式：
   ```
   work/cards.csv — 共 100 列

   階段            pending   done   failed
   ─────────────────────────────────────────
   ocr                   0    100        0
   extract               0    100        0
   image               100      0        0
   audio_front         100      0        0
   audio_back          100      0        0

   失敗明細（extract）
     ja_n2_034  card_id duplicated
   ```

> **本階段實作但不使用，為 Phase 2–5 預留**：`ocr` `image` `audio` `serve` 四個子命令的參數定義現在就要完整，後續 phase 只接上實作，不改參數結構。

**測試**：完整單元測試，執行至通過。涵蓋參數解析、互斥檢查、status 統計正確性。

---

## 驗收流程

請使用者依序手動驗證：

1. **安裝**
   ```bash
   uv venv && uv pip install -e ".[dev]"
   uv run anki-builder --help
   ```
   確認八個子命令皆列出。

2. **設定驗證**
   ```bash
   # 故意移除 .env 中的 YAML_SETTINGS_FILE
   uv run anki-builder status
   ```
   確認報錯訊息明確指出缺少 `YAML_SETTINGS_FILE`。

3. **準備測試輸入**
   直接使用 `tests/fixtures/ocr_raw/` 的任一頁文字作為 `raw_text`（每頁一列），其餘欄位留空、狀態為 `pending`。

4. **執行抽取**
   ```bash
   uv run anki-builder extract --work work/cards.csv
   uv run anki-builder status --work work/cards.csv
   ```
   確認 `extract` 列顯示 `done`，且產出與 `tests/fixtures/expected_cards.csv` 逐項比對（判準見 `tests/fixtures/README.md`）——詞條切分、讀音、deck 分派、`image_prompt` 風格後綴皆須對齊。

5. **執行打包**
   ```bash
   uv run anki-builder pack --work work/cards.csv --output output/test.zip
   ```

6. **載入記憶引擎**
   用瀏覽器開啟記憶引擎 HTML，上傳 `output/test.zip`，確認卡片正常顯示、可翻牌、可評分。

7. **驗證續作**
   再次執行 `extract`，確認狀態為 `done` 的列被跳過，不重複呼叫 LLM。

8. **驗證原子寫入**
   執行 `extract` 過程中按 Ctrl-C 中斷，確認 `work/cards.csv` 未損毀、可正常讀取。

---

## 已知風險與注意

### 風險

| 風險 | 說明與因應 |
|------|-----------|
| 重造 agent_factory 已有的輪子 | **本 phase 最易犯的錯**。prompt 載入、structured output 解析、速率限制、重試皆已由 submodule 提供。動手前先讀 README |
| 速率限制配額宣告遺漏 | 未在 YAML 宣告 `limits` 時會靜默套 `DEFAULT_POLICY` + warning，不報錯，容易忽略。`agents.yaml` 每個模型都要明寫 `policy: concurrency_only` |
| LLM structured output 格式不穩 | 不同模型對 structured output 的支援差異大。若解析失敗率高，停下來與使用者討論改用 JSON mode 或後處理解析 |
| CSV 欄位數量多（39 欄） | 欄位順序一旦寫錯，後續所有階段都會錯位。Task 1.3 的 `field_order()` 是唯一真實來源，其他地方不得自行列舉欄位 |

### 不要做

- **不要**實作 OCR、圖生成、語音生成的任何邏輯——那是 Phase 2/3/4
- **不要**為了「先做起來放」而簡化 `CardRow` 欄位。後續 phase 需要的欄位現在就要定義完整
- **不要**在 `stages/base.py` 加入任何特定階段的業務邏輯
- **不要**在 `cli.py` 實作業務邏輯，一律呼叫 `stages/`
- **不要**引入 FastAPI、Gradio、pypdfium2 等後續 phase 才需要的相依
- **不要**自行決定 agent_factory 的 API 用法——README 已完整說明，照著用；有疑義先問
- **不要**修改 `src/agent_factory/` 內的任何檔案

---

## 完成後

**暫停，等使用者完成上述驗收流程並確認**，才進入 Phase 2。

驗收通過後：
1. 依 [change-log-guide.md](change-log-guide.md) 於 `logs/` 產出改動日誌
2. 在日誌「測試結果」明確點名需人工執行的測試：`tests/clients/test_llm_client.py`、`tests/stages/test_extract.py` 的 LLM 相關案例
3. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
