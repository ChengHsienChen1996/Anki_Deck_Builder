# 改動總結 — Phase 1：骨架與最小可用流程

日期：2026-08-21
分支：dev_ai
相關 commit：`2295e69` … `ff32b7b`（12 筆，`cab9103` 之後全部）

| commit | Task | 內容 |
|--------|------|------|
| `2295e69` | 1.1 | 專案初始化與 agent_factory 接線設定 |
| `2a9f72d` | 1.2 | 設定層與專案例外階層 |
| `afeeec8` | 1.3 | 型別層 StageStatus、CardRow、ExtractOutput |
| `f1d5cc2` | 1.4 | 狀態機層 CardStore 與 selector |
| `6b9ff4a` | 1.5 | 四個外部服務 Protocol 介面契約 |
| `909d311` | 1.6 | 階段共用骨架與 registry |
| `50355d5` | 1.7 | agents.yaml 與 LLM 薄適配層 |
| `bba0664` | 1.8 | 抽取 prompt 與聯想圖風格模板 |
| `3a7ca78` | 1.9 | 階段 ② extract |
| `b7959a0` | 1.10 | 階段 ⑤ pack |
| `7089b76` | 1.11 | CLI 骨架與八個子命令 |
| `ff32b7b` | — | 驗收回報的本地模型逾時：根因診斷與防護 |

---

## 變更清單

### 專案設定

1. `pyproject.toml`：宣告相依（pydantic、pydantic-settings、httpx、aiofiles，以及以
   `[tool.uv.sources]` editable 路徑相依掛入的 agent_factory submodule）、`[dev]` extra、
   ruff 與 pytest 設定；console script 設為 `anki-builder`；`addopts = ["-m", "not manual"]`
   讓需人工執行的測試預設被 deselect
2. `.env.example`：依 architecture.md 環境變數清單全數列出（agent_factory 五項、
   INGEST_MODE、路徑、ComfyUI、VOXCPM2）
3. `.gitignore`：補上 `work/*` 與 `output/*`（用 `dir/*` 而非 `dir/`，否則 `.gitkeep`
   無法用 `!` 重新納入）
4. `agents.yaml`（新增，專案根目錄）：宣告 `ExtractAgent`，模型
   `gemma4_31b_q4_K_M-optimized`、`base_url` 指向本地 Ollama、
   `limits.policy: concurrency_only`；client 區塊另設 `timeout: 600`、`max_retries: 0`
5. `work/`、`output/`、`prompts/`、`workflows/`、`tests/__init__.py`、`tests/conftest.py`：建立

### 程式碼

6. `src/anki_deck_builder/exceptions.py`：新增。`AnkiBuilderError` 為根，底下
   `ConfigurationError`、`ExternalServiceError`、`StageProcessingError`、`WorkFileError`
7. `src/anki_deck_builder/config.py`：新增。pydantic-settings 巢狀分組
   （`agent_factory` / `ingest` / `paths` / `comfyui.nodes` / `tts`），驗證失敗轉為
   指名環境變數的 `ConfigurationError`，`mask_secret()` 金鑰遮罩；階段性驗證——
   不檢查服務可達性與 workflow 檔案存在
8. `src/anki_deck_builder/schemas/status.py`：新增。`StageStatus`（`StrEnum`）
9. `src/anki_deck_builder/schemas/card.py`：新增。`CardRow` 39 欄與三組欄位常數，
   `field_order()` 為欄位順序唯一真實來源，另有 `engine_field_order()`、
   `to_csv_row()`、`from_csv_row()`
10. `src/anki_deck_builder/schemas/extract_output.py`：新增。`ExtractedCard` 與
    `ExtractOutput`（一段 raw_text 對多張卡，故為 cards 清單），`to_card_fields()`
    集中欄位映射
11. `src/anki_deck_builder/state/store.py`：新增。`CardStore` 異步讀寫中間 CSV，
    UTF-8 with BOM + CRLF，原子寫入（寫 `.tmp` → flush → fsync → `os.replace`），
    讀取時驗證表頭與每列欄位數
12. `src/anki_deck_builder/state/selector.py`：新增。`STAGE_FIELDS` 為階段↔欄位
    對應表的唯一來源，`select_pending()`、`mark_done()`、`mark_failed()`、
    `summarize()`、`failed_rows()`
13. `src/anki_deck_builder/clients/protocols.py`：新增。四個 Protocol 一次定義完成，
    並加 `AgentInput = str | list[dict[str, Any]]` 讓 Phase 2 的影像輸入不必改介面
14. `src/anki_deck_builder/clients/llm_client.py`：新增。`LLMClient` 薄適配層，
    factory 與 runner 建立一次後重用，外部例外轉為專案例外
15. `src/anki_deck_builder/stages/base.py`：新增。`BaseStage.run()` 骨架、失敗跳過、
    一對多（`process_row` 回傳新增列）、`checkpoint_every` 定期原子寫回、
    併發控制、`validate_settings()` 掛勾、`register_stage` registry
16. `src/anki_deck_builder/stages/extract.py`：新增。組任務參數標頭、card_id 前綴
    串頁碼、唯一性跨整份工作檔檢查、agent 與 input 的切換點集中於 `_build_request()`
17. `src/anki_deck_builder/stages/pack.py`：新增。還原 23 欄、media 目錄結構、
    四類完整性驗證（一次列出全部問題）、failed 列預設中止、輸出 ZIP
18. `src/anki_deck_builder/cli.py`：新增。八個子命令全部定義，Phase 1 可用
    extract／pack／status，其餘印提示後正常結束；`--force` 與 `--only-failed`
    於解析階段互斥

### Prompt

19. `prompts/extract_cards.md`：新增。領域不預設（由輸入的任務參數區塊帶入），
    逐欄位規則對齊 `tests/fixtures/expected_cards.csv`，deck 依條目性質分類
20. `prompts/image_prompt_template.md`：新增。統一風格後綴的權威定義處、
    負向 prompt 說明、抽象條目的具象化轉法

### 測試

21. `tests/test_config.py`（23）、`tests/schemas/test_card.py`（22）、
    `tests/schemas/test_status.py`（5）、`tests/schemas/test_extract_output.py`（9）、
    `tests/state/test_store.py`（17）、`tests/state/test_selector.py`（17）、
    `tests/stages/test_base.py`（20）、`tests/stages/test_extract.py`（21+1）、
    `tests/stages/test_pack.py`（26）、`tests/test_cli.py`（34）、
    `tests/test_prompts.py`（3）：新增
22. `tests/clients/test_llm_client.py`：新增，13 項 mock 骨架，標記 `manual`
23. `tests/conftest.py`：新增，`fixtures_dir` 與 `expected_cards_csv` 兩個路徑 fixture

### 文檔

24. `CLAUDE.md`：submodule 路徑 `vendor/` → `src/`；phase 文件連結由 `docs/` 改指
    `.agent/plans/`（原本五條全是壞連結）
25. `docs/architecture.md`：模組結構樹改為實際的 `src/agent_factory/`；
    〈`MODEL_LIMITS` 必須先登錄〉整節改寫為〈速率限制配額寫在 `agents.yaml`〉
    （原敘述「未登錄會拋 KeyError」有誤，實測 `DEFAULT_POLICY` 為
    `concurrency_only`，只發一次 warning）
26. `docs/project-overview.md`：新增〈Ollama 模型的 `num_ctx` 會決定成敗〉，含實測數據
27. `.agent/plans/phase-1-foundation.md`：Task 1.2 的 `settings.llm.*` 範例改為
    `settings.agent_factory.*`（原範例與 architecture.md 及 Task 1.7 互相矛盾）；
    Task 1.7 第 4 點改為「配額寫在 agents.yaml」
28. `.agent/plans/phase-1-execution-plan.md`：新增。本 phase 的執行計畫與決策紀錄
29. `.agent/notes/agent-factory-cheatsheet.md`：新增。submodule README 的速查摘要

---

## 測試結果

- `uv run pytest`：**202 passed / 14 deselected**，`ruff check` 無錯誤
- 需人工執行的測試（已於本次驗收由使用者執行，**全部通過**）：
  - `tests/clients/test_llm_client.py`（13 項，mock `LimitAgentRunner.run`）
  - `tests/stages/test_extract.py::test_real_extract_against_golden_page`
    （真實呼叫 `ExtractAgent` 抽取 `fixtures/ocr_raw/page_01.txt`）

---

## 備註

### 驗收過程發現並排除的問題

真實 LLM 測試首次執行時逾時失敗（`APITimeoutError`，耗時 30 分鐘）。根因**不在本專案**：
`gemma4_31b_q4_K_M-optimized` 的 Modelfile 寫死 `PARAMETER num_ctx 262144`，
256K context 的 KV cache 預留把權重擠出 VRAM（只有 11.4 / 24.1 GB 在 GPU），
輸出速度剩 2.5 tok/s。以 `num_ctx=8192` 重載後 100% 進 VRAM、34.2 tok/s（13.7 倍）。
使用者重建 Modelfile 後測試通過。

本專案端的防護：`agents.yaml` 加 `timeout: 600`、`max_retries: 0`——預設的
「逾時後再重試兩次」會把 10 分鐘的失敗放大成 30 分鐘。

### 已知限制

- **`--force` 重跑同一頁 extract 會撞 card_id**：重新抽取會產出與上次相同的 id。
  根本解法需要骨架支援「移除來源列上次產出的列」，屬骨架改動；經確認**維持現狀**，
  失敗訊息會提示先移除舊卡片列或改用不同前綴。Phase 3 的「改 prompt 重生單張圖」
  會遇到同類需求，屆時一併處理。
- **`vision_direct` 未實作**：切換點已留在 `stages/extract.py` 的 `_build_request()`，
  目前走到會回報「於 Phase 2 實作」。
- **`gemma4_31b_q4_K_M-optimized` 是否支援影像輸入未查證**，這決定 Phase 2 的
  `vision_direct` 能否沿用此模型。

### 後續待辦

- Phase 2 開工前先查證上述多模態支援
- `tests/fixtures/ocr_raw/` 目前是人工轉寫，取得真實 GLM-OCR 輸出後應直接覆蓋
