# 改動總結 — Phase 3 ComfyUI 聯想圖生成

日期：2026-08-24
分支：dev_ai
相關 commit：`e94fad8`（Task 3.1）、`cc366f1`（3.2）、`da769a8`（3.3）、`0c91e29`（3.4）、
`10eba8a`（3.5）、`db5b261`（驗收清單）

---

## 變更清單

### 新增程式

1. `src/anki_deck_builder/clients/workflow.py`：workflow 的載入、驗證與節點注入。
   `load_workflow()`／`validate()`／`inject()`／`injection_points()`。注入點全部由 `.env`
   決定，模組內無任何字面量節點 ID 或 `class_type`（約束 5）。編輯器格式（頂層為
   `nodes`／`links`）另給指名道姓的錯誤訊息——本次前置作業就踩到這個坑
2. `src/anki_deck_builder/clients/comfyui_client.py`：`ImageGenClientProtocol` 的實作。
   走官方 API 範例方法 2（WebSocket + History），另含 `free_memory()`（`POST /free`）
3. `src/anki_deck_builder/stages/image.py`：階段 ③。`ImageStage` 與 `stable_seed()`
4. `src/anki_deck_builder/stages/progress.py`：通用進度顯示。`ProgressReporter` 與
   `format_duration()`，純標準庫，Phase 4 的 audio 直接沿用

### 修改程式

5. `src/anki_deck_builder/clients/protocols.py`：`ImageGenClientProtocol` 補上 `validate()`。
   workflow 由使用者自帶，節點 ID 打錯必須在第一張送出前擋下，而不是夾在半小時的進度條中間
6. `src/anki_deck_builder/cli.py`：`image` 子命令接上 `ImageStage`；`run-all` 串成
   ocr → extract → image → pack；新增 `_free_vram_for_image()`（image 前全卸 Ollama）與
   `_release_comfyui()`（extract 前依設定請 ComfyUI 讓位）
7. `src/anki_deck_builder/config.py`：尺寸預設 `1024×576` → `768×432`；
   `negative_prompt` 併入 SD 1.5 標準負向詞；新增 `free_before_llm`
8. `src/anki_deck_builder/stages/__init__.py`：匯出 `ImageStage`、`ProgressReporter` 等
9. `pyproject.toml`／`uv.lock`：新增 `websockets>=13`（httpx 不支援 WebSocket）

### 新增測試

10. `tests/clients/test_workflow.py`：23 項。各注入點、缺節點／缺欄位報錯、選填跳過、
    原物件未被修改、編輯器格式辨識
11. `tests/clients/test_comfyui_client.py`：23 項。HTTP 走 `MockTransport`、WS 用假 socket，
    **不依賴 ComfyUI 啟動**。涵蓋正常流程、舊版訊號、雜訊過濾、WS 斷線退回輪詢、
    `execution_error`、400 帶 `node_errors`、500、輸出節點不存在、空圖、逾時
12. `tests/stages/test_image.py`：21 項。狀態流轉、跳過來源列、相對路徑回填、
    媒體根目錄跟隨 CSV、併發上限、進度接線
13. `tests/stages/test_progress.py`：25 項。時間格式、進度條、預估、TTY 與非 TTY 輸出、邊界

### 修改測試

14. `tests/test_cli.py`：新增 `isolate_external_services` autouse fixture 與 image／VRAM
    讓渡的接線測試；`fake_llm` 的 `ExtractedCard` 補上 `image_prompt`
15. `tests/test_config.py`：預設值斷言同步（768×432、`free_before_llm`）

### 設定與文件

16. `.env.example`：尺寸 768×432、合併後的負向 prompt、新增 `COMFYUI_FREE_BEFORE_LLM=false`
17. `prompts/image_prompt_template.md`：版面 1024×576 → 768×432；負向 prompt 段落更新
18. `docs/architecture.md`：範例中的尺寸同步
19. `workflows/card_image.json`：使用者提供的 API 格式 workflow（DreamShaper 8／SD 1.5）
20. `.agent/plans/phase-3-execution-plan.md`、`.agent/plans/phase-3-acceptance.md`：新增
21. `.agent/plans/phase-3-image.md`：驗收第 3 步的解析度同步

---

## 測試結果

- pytest：**465 passed**（31 deselected，付費 API mock 骨架照既有規則跳過）
- `ruff check src tests`：全數通過
- `grep -E '"[0-9]+"' src/**/*.py`：無命中，符合「程式碼零硬編碼節點 ID」
- 需人工測試項目：無新增。ComfyUI client 的測試全部可執行且不依賴服務啟動

---

## 實測數據

### 前置：ComfyUI API 的實際回應結構（0.28.3）

依 phase-3-image.md 的要求先手動打過一次，兩點與原先認知不同：

- **完成訊號有兩種**：`execution_success` 先到、`node` 為 `null` 的 `executing` 後到。
  兩種都接受，舊版 ComfyUI 只送後者
- **WS 上混著大量其他訊息**：單次生成收到 `progress_state` × 24、`progress` × 10、
  `status` × 4 與預覽二進位幀。必須以 `prompt_id` 過濾，否則同一台 ComfyUI 上
  別人的任務會誤觸完成判定

### batch size（8 張／次，768×432）

| `COMFYUI_BATCH_SIZE` | 總耗時 | 每張平均 | VRAM 峰值 | 錯誤 |
|---|---|---|---|---|
| 1 | 15.30s | 1.91s | 3268 MiB | 無 |
| 2 | 14.82s | 1.85s | 3278 MiB | 無 |
| 4 | 14.76s | 1.85s | 3286 MiB | 無 |
| 8 | 14.84s | 1.86s | 3294 MiB | 無 |

**結論：維持預設 4。** 2／4／8 之間差 0.08 秒，在單次測量的雜訊裡；只有 1 落後 3.5%。
VRAM 全程只動 26 MiB。這正是 ComfyUI 佇列序列化執行的結果——併發只影響排隊深度，
GPU 仍是一次跑一張。

### VRAM 讓渡

**image 前卸載 Ollama**（`MODEL_UNLOAD_BEFORE_STAGE`，預設開啟）：卸與不卸皆 13 秒，
E4B 之下沒有差別。維持預設開啟，因為 `agents.yaml` 仍留著切回 31B 的路徑。

**extract 前請 ComfyUI 讓位**（`COMFYUI_FREE_BEFORE_LLM`，預設關閉）：

| 條件 | extract 耗時 | 峰值 VRAM |
|---|---|---|
| ComfyUI 常駐 ① | 61.98s | 11700 MiB |
| ComfyUI 常駐 ② | 62.47s | 11700 MiB |
| 讓位後 ① | 61.46s | 9652 MiB |
| 讓位後 ② | 61.85s | 9652 MiB |

常駐平均 62.23s、讓位平均 61.66s，**差 0.57 秒（0.9%）**，在雜訊裡；逐次呼叫耗時
（6.5s／16.8s／36.0s）四趟差異都在 0.3 秒內。VRAM 差正好 2048 MiB，就是 ComfyUI
讓出的那塊；峰值 11.7 GB／24 GB，**還剩 12.3 GB**。**維持預設關閉。**

> 第一次測量無效：用了只有卡片列的工作檔，`extract.py` 對這種列直接 `return`，
> 一次模型呼叫都沒發出，量到的是 Python 啟動時間。修正為「有 `raw_text` 的來源列」後重測。

---

## 備註

### 一個影響多項既有決策的發現：VRAM 約束是模型大小的函數

CLAUDE.md 記著「ComfyUI 常駐 2.4 GB → 抽取模型只載入 88%、速度剩 1/6」，那是
**31B q4（19.87 GB）** 下的實測：19.87 + 2.3 + 1 ≈ 23.2 GB，塞不進 24 GB。

換成現行的 `gemma4-e4b-optimized`（11.64 GB）後：11.7 GB 峰值、剩 12.3 GB，
**兩者可以共存，這條約束不再成立**。Phase 2／3 中因它而生的兩個開關都維持保守預設，
但實際上在 E4B 路徑下都不需要。切回 31B 時它們會重新變得必要。

### 已知限制：部分圖片與 `image_prompt` 不符

驗收第 3 步發現。「畫面不含任何文字」通過；「畫面與 `image_prompt` 描述相符」部分不通過。

**不是 prompt 長度問題**：308 條 prompt 中位 32 字、最長 42 字（約 42～55 個 CLIP token），
**0 條超過 77**，沒有截斷或分塊稀釋。

兩個實際原因：

1. **SD 1.5 的多元素構圖弱點（主因）**。場景描述含 `" and "` 的有 **138/308（45%）**。
   例：`p1_001 a/an → a single dog and a single apple on a simple wooden table`
   實際成圖是三顆蘋果、沒有狗。多義詞更極端：
   `p1_007 above → a thermometer … and a building with an apartment above a shop`
   只畫了大樓，溫度計完全沒有
2. **prompt 混進解釋性子句**。含 `representing`／`symbolizing`／`conveying` 的有
   **123/308（40%）**；11 張（3.6%）直接要求 `overlapping circles`、`diagram` 等抽象符號，
   與 `image_prompt_template.md` 自訂的「具體場景，不要抽象符號」相牴觸。
   根因在 `prompts/extract_cards.md` 的規範沒有禁止這類寫法

**CFG 不是解法（已實測否決）**。同一張卡同一個 seed 跑 cfg 8／9／11／13：

| | cfg 8 | cfg 9 | cfg 11 | cfg 13 |
|---|---|---|---|---|
| `p1_001` 的狗 | 沒有 | 沒有 | 沒有 | 沒有 |
| `p1_007` 的溫度計 | 沒有 | 沒有 | 沒有 | 沒有 |
| 畫面文字 | 無 | 無 | 無 | **生出「NTO」「ILO」招牌與直立字母** |

加強遵循度不會讓缺席的元素長出來，而 cfg 13 反而突破了正負向雙重的防文字約束。
**cfg 維持 8，不得上調。**

### 後續待辦

- **換 workflow 的模型**（建議優先）：SDXL 之後的模型對多物件構圖與文字抑制都遠優於
  SD 1.5，可同時解掉上述兩個問題，且不必重跑 extract。換模型後 512 原生解析度的限制解除，
  尺寸可考慮回到 1024×576。屬使用者的 workflow 決策，換後需重測 VRAM 與速度
- **修 `prompts/extract_cards.md` 的 `image_prompt` 規範**（治本，成本高）：限制單一主體、
  單一動作，多義詞挑一個義項，禁止解釋性子句。代價是要重跑 extract 才會產生新的
  `image_prompt`，且重跑同一頁有 `card_id` 撞號的已知限制（見 Phase 1 的限制說明）
- Phase 4 的 audio 直接沿用 `stages/progress.py`，不需另寫

### 作業過程中的一次意外副作用（已還原）

`image` 接上 CLI 後，既有的 `test_unimplemented_commands_report_without_crashing`
少掛了 `env` fixture，在專案根目錄以真實 `.env` 執行，實際把 221 張圖生成到開發者的
`work/` 裡並改寫了 `work/cards.csv` 的 `image_*` 欄位。已刪除 `work/media/` 並把 234 列
（221 張卡 + 13 列來源列）的 `image_status` 還原為 `pending`、`image_front` 清空；
逐欄比對確認只有這兩欄有差異，`ocr_status`／`extract_status` 各 311 列不受影響。

根因已修：`tests/test_cli.py` 新增 `isolate_external_services` autouse fixture，
攔下 `ensure_room`／`unload_model`／`free_memory`／`ComfyUIClient`，CLI 測試不再碰任何
真實服務（整套測試耗時也回到 8.5 秒）。
