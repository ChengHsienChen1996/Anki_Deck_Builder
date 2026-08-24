# Phase 3 執行計畫（待使用者確認）

日期：2026-08-24 ／ 分支：`dev_ai`
依據：[phase-3-image.md](phase-3-image.md)、[architecture.md](../../docs/architecture.md)、
[image_prompt_template.md](../../prompts/image_prompt_template.md)、
[ComfyUI 官方 API 範例](https://docs.comfy.org/development/comfyui-server/api-examples)

---

## 0. 開工前檢查結果

| 檢查項 | 結果 |
|--------|------|
| 分支 | ✅ `dev_ai`。工作區有兩項未提交：`.env.example`（本次合併負向 prompt）、`workflows/card_image.json`（新檔） |
| `.claude/settings.local.json` 備份 | ⚠️ 專案內仍無 `.claude/` 目錄（同 Phase 1／2），**無檔案可備份** → 略過 |
| `agent_factory` submodule | ✅ 本 phase 不經 agent_factory（ComfyUI 是自家 HTTP／WS），僅 VRAM 讓渡需從 agent 物件取 endpoint，沿用 Phase 2 已有的 `agent_endpoint.py` |
| 外部介面狀態 | ✅ ComfyUI 已啟動，`/system_stats` 回報 `comfyui_version: 0.28.3` |
| Phase 2 交付 | ✅ 驗收通過，`ocr` → `extract` → `pack` 可跑 |

---

## 1. 前置條件核對結果

### 1.1 workflow 與注入點：全部對上

`workflows/card_image.json` 已是 **API 格式**（頂層 key 為節點 ID）：

| 節點 | class_type | 用途 |
|------|-----------|------|
| `3` | KSampler | seed／steps／cfg |
| `4` | CheckpointLoaderSimple | DreamShaper_8_pruned（SD 1.5 微調） |
| `5` | EmptyLatentImage | width／height／batch_size |
| `6` | CLIPTextEncode | **正向** prompt |
| `7` | CLIPTextEncode | **負向** prompt |
| `8` | VAEDecode | — |
| `9` | SaveImage | **輸出節點** |

正負向沒有搞反：KSampler 的 `positive` 指向 `["6", 0]`、`negative` 指向 `["7", 0]`。
`.env` 現有的六組注入點（6／7／3／5／9）與上表逐一相符，**不需要自動偵測節點**——
`.env` 已是唯一真實來源，符合約束 5「程式碼零硬編碼節點 ID」。

### 1.2 已完成的設定調整

- `COMFYUI_NEGATIVE_PROMPT`：合併 workflow 內建的 SD 1.5 負向詞與原有的防文字詞，
  去重、重排（畫質 → 解剖 → 肢體 → 文字），並移除四個風格鎖定詞
  `cartoon, anime, 3d render, realistic (non-painting style)`。`.env` 與 `.env.example` 已同步。
  `image_prompt_template.md` 所載的「預設值」因此過時，Task 3.1 一併更新。
- 尺寸定為 `768×432`（§2.1）。

---

## 2. 尺寸與 ComfyUI 讓渡（2026-08-24 已定案）

### 2.1 圖片尺寸：768×432

我先前改成 `512×512` 時尚未讀到 `prompts/image_prompt_template.md`，該檔把
**16:9 橫幅**列為設計要求（對應記憶引擎的卡片版面）。而 DreamShaper 8 是 SD 1.5 底、
原生 512px，寬邊拉到 1024 常出現主體重複（twinning）。

**定案 768×432**：保住 16:9 的原始設計意圖，寬邊 768 又在 SD 1.5 可接受的範圍內。
連帶修改 `.env`、`.env.example`、`config.py` 預設值，以及
`image_prompt_template.md` 中「1024 × 576」的字樣與驗收第 3 步的解析度。

### 2.2 extract 前呼叫 ComfyUI 的 `POST /free`：做，預設關閉

CLAUDE.md 的 Phase 3 警告記著一項實測：**ComfyUI 常駐佔 2.4 GB，就會讓抽取模型
只載入 88%、速度掉到 1/6**。`run-all` 的順序是 ocr → extract → image，所以只要
ComfyUI 開著，extract 就會付這個代價——即使 image 還沒開始跑。

`ensure_room()` 看不到 ComfyUI（它只認 Ollama 的 `/api/ps`），要讓位得主動打
`POST /free`（`{"unload_models": true, "free_memory": true}`）。

**定案**：實作 `comfyui_free()`，由新設定 `COMFYUI_FREE_BEFORE_LLM` 驅動，
**預設關閉**，驗收第 5 步實測 extract 速度差異後再決定要不要改預設值。
理由與 Phase 2 的 `MODEL_UNLOAD_ENABLED` 相同——它是最佳化，開啟有代價
（ComfyUI 下次生成要重載模型），不該未經實測就預設開啟。

---

## 3. 已確認的決定

| # | 議題 | 決定 |
|---|------|------|
| **Q1** | ComfyUI 呼叫方式 | 官方**方法 2（WebSocket + History）**。WebSocket 只用來取得**精確的完成訊號**，圖片仍走 `GET /history/{prompt_id}` → `GET /view`。相對於方法 1 的純輪詢，省下每張最多一個 `COMFYUI_POLL_INTERVAL`（2 秒）的空等；100 張就是 3 分鐘 |
| **Q2** | Seed 策略 | `sha256(card_id)` 前 8 bytes 取為 int，同一張卡重生必得同一張圖。**代價你已知悉**：因為不滿意而重生會拿到一模一樣的結果，要換圖得改 `image_prompt` |
| **Q3** | image 前的 VRAM 讓渡 | 呼叫既有的 `ensure_room(base_url, keep="")`——`keep` 傳空字串即「一個都不留」，**現有程式碼直接支援，不需新增函式**。`base_url` 沿用 `_extract_agent_name()` + `LLMClient.model_endpoint()` 從 `agents.yaml` 推得，維持單一真實來源 |
| **Q4** | 新增相依 | `websockets`（環境中已有 16.1.1，但需在 `pyproject.toml` 顯式宣告）。`httpx` 不支援 WebSocket，HTTP 部分仍走 httpx |

---

## 4. 任務執行順序

一次一個 task，完成即停下交付。

### Task 3.1 — Workflow 載入與節點注入

**產出**：`clients/workflow.py`、`tests/clients/test_workflow.py`、
`config.py`（尺寸／負向 prompt 預設值同步）、`prompts/image_prompt_template.md`（負向 prompt 段落更新）

- `load_workflow(path)`：讀 JSON，檔案不存在／JSON 壞掉 → `ConfigurationError`
- `validate(workflow, nodes)`：**階段開始前**檢查三個必填注入點存在，訊息指名是哪個注入點、哪個節點 ID
- `inject(workflow, positive, negative, seed=None, width=None, height=None)`：
  `copy.deepcopy` 後注入 `workflow[node_id]["inputs"][field]`，**不動原物件**
- 選填注入點（seed、尺寸）在 `.env` 留空時跳過，不報錯

**測試**：可執行單元測試，以假 workflow 驗證各注入點、缺節點報錯、選填跳過、原物件未被修改。

### Task 3.2 — ComfyUI client

**產出**：`clients/comfyui_client.py`、`tests/clients/test_comfyui_client.py`

**開工第一件事**：以實際 ComfyUI 手動送一次 workflow，抄下 `/history` 的巢狀結構與
`/view` 的參數，**依實測結果實作**（phase-3-image.md 明列此項，不照記憶寫）。

實作 `ImageGenClientProtocol.generate()`，流程：

1. `websockets.connect(ws://…/ws?clientId={uuid4})` —— **先連線再送出**，否則會漏掉完成訊息
2. `POST /prompt`，body 含 `prompt` 與 `client_id`；400 的 `node_errors` 一併寫進錯誤訊息
3. 收 WS 訊息至 `{"type":"executing","data":{"node":null,"prompt_id":…}}`（該 prompt_id 執行完畢）；
   `execution_error` 直接轉 `ExternalServiceError`；二進位幀（預覽圖）略過
4. `GET /history/{prompt_id}` → 取 `outputs[COMFYUI_OUTPUT_NODE_ID]["images"]`
5. `GET /view?filename=&subfolder=&type=` → 回傳 bytes

- 全程 `asyncio.timeout(COMFYUI_TIMEOUT)`；逾時、連線失敗、HTTP 錯誤一律 `ExternalServiceError`，訊息含 endpoint
- `COMFYUI_POLL_INTERVAL` 降級為 WS 斷線時的輪詢後備

**測試**：mock HTTP／WS 的**可執行**測試，涵蓋正常流程、`execution_error`、逾時、HTTP 500、
輸出節點不在回應中。**不得依賴 ComfyUI 實際啟動**。

### Task 3.3 — 階段 ③：image

**產出**：`stages/image.py`、`tests/stages/test_image.py`

- 繼承 `BaseStage`，`@register_stage("image")`
- `validate_settings()`：載入 workflow 並跑 Task 3.1 的 `validate()`，**生成開始前就擋下設定錯誤**
- `process_row()`：
  1. **`card_id` 為空 → 直接 return**（來源列的 `image_status` 沿用 `pending` 會被選進來，
     但它不是卡片。`ocr.py` 有同型的防護，這裡照做）
  2. `image_prompt` 為空 → 拋例外，骨架記為 `failed`
  3. 呼叫 client（負向 prompt、尺寸由 client 從設定取得，不進簽章）
  4. 寫 `work_dir/media/img/{card_id}.png`
  5. 回填 `image_front` 為**相對路徑** `media/img/{card_id}.png`（`pack` 以 `media_root` 還原）
- `concurrency = COMFYUI_BATCH_SIZE`。**命名陷阱**：這是「同時在飛的 prompt 數」，
  不是 `EmptyLatentImage.batch_size`（每次生幾張）。ComfyUI 佇列本身序列化執行，
  併發只影響排隊深度
- `checkpoint_every = 1`：單張數十秒，每列寫回一次的成本可忽略，換取中斷後不重跑

**測試**：狀態流轉、跳過非卡片列、缺 prompt、檔名與相對路徑回填為可執行測試；client 用假實作。

### Task 3.4 — 佇列進度顯示

**產出**：`stages/progress.py`、`tests/stages/test_progress.py`

- 格式：`生成聯想圖  [████████░░░░░░░░]  42/100  已耗時 08:15  預估剩餘 11:22`
- 純標準庫，不引入 tqdm；非 TTY 降級為每 10 項印一行
- 接線方式：`ImageStage.process_row()` 以 `try/finally` 遞增計數（失敗也要前進）。
  總數由 `ImageStage.run()` 先 `store.read()` + `select_pending()` 算出——
  多讀一次 CSV，換取**不修改 `BaseStage`**（CLAUDE.md：新增階段時加檔案、不改骨架）
- Phase 4 的 audio 直接沿用，不另寫

### Task 3.5 — CLI 接線

**產出**：`cli.py`（修改）、`tests/test_cli.py`（補充）

- `image` 子命令接上 ImageStage，並在階段前呼叫 `ensure_room(..., keep="")`
- `run-all` 於既有的 `# ③ image` 註解處接入，順序 ocr → extract → **image** → pack
- `audio` 位置維持註解，Phase 4 接
- `comfyui_free()` 依 `COMFYUI_FREE_BEFORE_LLM` 在 extract 前接線（§2.2）

---

## 5. 風險與因應

| 風險 | 因應 |
|------|------|
| `/history`／`/view` 結構與認知不符 | Task 3.2 開工先手動打一次 API，依實測實作 |
| **ComfyUI 常駐拖慢 extract** | 已實測（速度剩 1/6）。§2.2 已定：`COMFYUI_FREE_BEFORE_LLM`，預設關閉 |
| 抽取模型未卸載導致 image OOM | Q3 已定：image 前 `ensure_room(keep="")` |
| 生成圖含文字 | 正向後綴 + 合併後的負向 prompt 兩面夾擊。仍出現則回報討論，不自行改 prompt 模板 |
| batch size 過大 OOM | 驗收第 4 步從 1 開始逐步加大，**不自行決定預設值** |
| SD 1.5 拉寬邊主體重複 | §2.1 定為 768×432；驗收比對實際成圖 |
| 長 prompt 超過 CLIP 77 token | 低風險——ComfyUI 會自動分塊編碼再串接，不會硬截斷。驗收時留意超長 prompt 的成圖是否失焦 |
| 長時間任務中斷 | `checkpoint_every = 1` + Phase 1 的原子寫入，中斷可續作 |

### 不要做（取自 phase-3-image.md）

- 不在程式碼寫死任何節點 ID、`class_type` 或 workflow 結構假設
- 不修改使用者的 workflow 原檔——注入操作副本
- 不自行決定 batch size 預設值
- 不為「讓圖更好看」自行加 prompt 內容
- 不讓 image 與 audio 併行
- 不引入 Gradio／FastAPI

---

## 6. Phase 3 結束時的交付

1. 五個 task 完成，非付費測試全綠、`ruff check` 無錯
2. `grep` 驗證程式碼中不存在字面量節點 ID
3. 驗收流程八步交使用者執行（第 3 步的解析度改為 768 × 432）
4. 驗收通過後：`logs/` 產出改動日誌（含 VRAM 與 batch size 實測數據）、
   更新 CLAUDE.md 進度表與外部介面狀態（ComfyUI workflow ✅）
