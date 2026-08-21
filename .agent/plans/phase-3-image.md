# Phase 3 — ComfyUI 聯想圖生成

## 目標

接上 ComfyUI，依階段 ② 產出的 `image_prompt` 批量生成無文字的視覺記憶錨點圖。本階段完成後，產出的 ZIP 含完整聯想圖，且支援「只改某列 prompt 重生單張圖」。

**核心要求**：節點注入完全由 `.env` 驅動，程式碼零硬編碼節點 ID，讓工具能對應任何使用者自帶的 workflow。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] workflow JSON 由 `.env` 指定路徑載入，節點注入依 `.env` 的節點 ID 與欄位名
- [ ] 程式碼中**不存在任何字面量節點 ID**（以 grep 驗證）
- [ ] 圖片生成後存至 `media/img/{card_id}.png`，`image_front` 正確回填
- [ ] 依 `COMFYUI_BATCH_SIZE` 分批送出，佇列進度可見
- [ ] 單張逾時或失敗記錄於 `image_error` 並跳過，整批繼續
- [ ] 修改某列 `image_prompt` 並將 `image_status` 設回 `pending` 後重跑，**只處理該列**
- [ ] `run-all` 已串入 image 階段
- [ ] pytest 全數通過（含 ComfyUI client 的 mock HTTP 測試）
- [ ] 生成圖確認**不含任何文字**

---

## 前置條件

本 phase 需使用者提供：

1. **ComfyUI workflow JSON**，置於 `workflows/`
2. **節點注入點對照**，填入 `.env`：

| 注入點 | 環境變數 | 必要性 |
|--------|----------|--------|
| 正向 prompt | `COMFYUI_POSITIVE_NODE_ID` + `COMFYUI_POSITIVE_FIELD` | ✅ |
| 負向 prompt | `COMFYUI_NEGATIVE_NODE_ID` + `COMFYUI_NEGATIVE_FIELD` | ✅ |
| 輸出節點 | `COMFYUI_OUTPUT_NODE_ID` | ✅ |
| Seed | `COMFYUI_SEED_NODE_ID` + `COMFYUI_SEED_FIELD` | ⬜ |
| Latent 尺寸 | `COMFYUI_LATENT_NODE_ID` + `COMFYUI_WIDTH_FIELD` / `COMFYUI_HEIGHT_FIELD` | ⬜ |

**取得方式**：ComfyUI 中以 `Save (API Format)` 匯出，JSON 頂層 key 即節點 ID。

若前置條件未備齊，**停下來向使用者索取**，不要自行猜測節點結構。

---

## 子任務拆分

### Task 3.1 — Workflow 載入與節點注入

**產出**
```
src/anki_deck_builder/clients/workflow.py
tests/clients/test_workflow.py
```

**要求**
1. 載入 `.env` 指定的 workflow JSON
2. 提供注入函式，回傳注入後的 workflow 副本（**不修改原物件**）：
   ```python
   def inject(
       workflow: dict,
       positive: str,
       negative: str,
       seed: int | None = None,
       width: int | None = None,
       height: int | None = None,
   ) -> dict: ...
   ```
3. 注入路徑為 `workflow[node_id]["inputs"][field_name]`
4. 節點 ID 或欄位不存在時拋 `ConfigurationError`，訊息須指出是哪個注入點、哪個節點 ID
5. 選填注入點（seed、尺寸）在 `.env` 未指定時**跳過注入**，不報錯
6. 載入時驗證必填注入點確實存在於 workflow 中，**在階段開始前就報錯**，而非跑到一半才失敗

**測試**：完整單元測試，執行至通過。以假 workflow JSON 驗證各注入點、缺節點報錯、選填跳過、原物件未被修改。

**不要做**：不要假設任何特定的 workflow 結構或 class_type，一切依 `.env` 指定。

---

### Task 3.2 — ComfyUI client

**產出**
```
src/anki_deck_builder/clients/comfyui_client.py
tests/clients/test_comfyui_client.py
```

**要求**
1. 實作 Phase 1 已定義的 `ImageGenClientProtocol`
2. 完整生成流程：
   1. `POST /prompt` 提交注入後的 workflow，取得 `prompt_id`
   2. 輪詢 `GET /history/{prompt_id}`，間隔 `COMFYUI_POLL_INTERVAL`
   3. 完成後從 history 回應中取出 `COMFYUI_OUTPUT_NODE_ID` 對應的圖片檔名資訊
   4. `GET /view` 下載圖片，回傳 bytes
3. 逾時控制：超過 `COMFYUI_TIMEOUT` 拋 `ExternalServiceError`
4. HTTP 錯誤、連線失敗一律轉為 `ExternalServiceError`，訊息含 endpoint

> **待確認**：ComfyUI 的 `/history` 回應結構與 `/view` 的 query 參數格式，我的認知可能與當前版本有出入。**開工時先以實際 ComfyUI 手動打一次 API 確認回應結構**，再依實測結果實作。

**測試**：以 mock HTTP 回應撰寫**可執行**單元測試，執行至通過。涵蓋正常流程、輪詢中途、逾時、HTTP 500、輸出節點不存在於回應中。**測試不得依賴 ComfyUI 實際啟動**。

---

### Task 3.3 — 階段 ③：image

**產出**
```
src/anki_deck_builder/stages/image.py
tests/stages/test_image.py
```

**要求**
1. 繼承 `BaseStage`，註冊為 `"image"`
2. 單列處理流程：
   1. 讀 `image_prompt`；為空則標 `failed` 並記錄「缺少 image_prompt」
   2. 組合完整 prompt：`image_prompt` + `.env` 的 `COMFYUI_NEGATIVE_PROMPT`
   3. 呼叫 ComfyUI client 生成
   4. 存檔至 `$WORK_DIR/media/img/{card_id}.png`
   5. 回填 `image_front` 為 `media/img/{card_id}.png`（**相對路徑**）
3. 依 `COMFYUI_BATCH_SIZE` 分批送出
4. 階段開始前驗證 workflow 必填注入點（呼叫 Task 3.1 的驗證）
5. Seed 策略：以 `card_id` 的雜湊產生穩定 seed，讓同一張卡重生時結果可重現 *(待確認：是否需要每次重生產生不同結果？若使用者希望重生得到不同圖，改為隨機 seed)*

**測試**：狀態流轉、批次切分、檔名組合、相對路徑回填等純邏輯撰寫**可執行**測試；ComfyUI 呼叫 mock。

---

### Task 3.4 — 佇列進度顯示

**產出**
```
src/anki_deck_builder/stages/progress.py
tests/stages/test_progress.py
```

**要求**
1. 提供進度顯示工具，適用於所有長時間階段（本 phase 用於 image，Phase 4 將用於 audio）
2. 輸出格式：
   ```
   生成聯想圖  [████████░░░░░░░░]  42/100  已耗時 08:15  預估剩餘 11:22
   ```
3. 以標準庫實作，**不引入 tqdm 等額外相依**
4. 提供非 TTY 環境的降級輸出（每完成 10 項印一行），避免 log 檔充斥控制字元

> **本階段實作，Phase 4 將重複使用**：`progress.py` 設計為通用工具，Phase 4 的 audio 階段直接沿用，不再另寫。

**測試**：完整單元測試，執行至通過。涵蓋百分比計算、時間預估、非 TTY 降級。

---

### Task 3.5 — CLI 接線

**產出**
```
src/anki_deck_builder/cli.py（修改）
tests/test_cli.py（補充）
```

**要求**
1. 將 `image` 子命令接上 Task 3.3 的實作
2. 於 `run-all` 的預留位置接入 image 階段，順序為 `ocr` → `extract` → **`image`** → `pack`
3. `audio` 位置仍保留註解，Phase 4 接入

**測試**：完整單元測試，執行至通過。

---

## 驗收流程

1. **設定驗證**
   ```bash
   # 故意把 COMFYUI_POSITIVE_NODE_ID 改成不存在的節點
   uv run anki-builder image --work work/cards.csv
   ```
   確認**在生成開始前**就報錯，訊息指出是哪個注入點、哪個節點 ID。

2. **小批量生成**
   準備 3 列已完成 extract 的 CSV：
   ```bash
   uv run anki-builder image --work work/cards.csv
   ```
   確認進度條顯示正常、3 張圖產生於 `work/media/img/`。

3. **圖片內容檢查**
   人工開啟生成的圖片，確認：
   - 畫面與 `image_prompt` 描述相符
   - **不含任何文字、浮水印、字母**
   - 解析度為 1024 × 576

4. **VRAM 與吞吐實測**
   分別以 `COMFYUI_BATCH_SIZE` 設為 1 / 2 / 4 / 8 執行，記錄 VRAM 佔用（`nvidia-smi`）與每張平均耗時，決定 24GB RTX 3090 的最佳預設值，回報使用者確認後寫入 `.env.example`。

5. **失敗跳過**
   於生成過程中暫停 ComfyUI 服務，確認該列標為 `failed`、記錄錯誤，指令不崩潰。

6. **針對性重生**
   ```bash
   # 手動修改 CSV 中某一列的 image_prompt，並將該列 image_status 改為 pending
   uv run anki-builder image --work work/cards.csv
   ```
   確認**只有該列**被重新生成，其餘 `done` 的列未被觸碰（可比對檔案修改時間）。

7. **只重跑失敗**
   ```bash
   uv run anki-builder image --work work/cards.csv --only-failed
   ```
   確認只處理 `failed` 列。

8. **全流程**
   ```bash
   uv run anki-builder run-all --input path/to/book.pdf --output output/deck.zip
   ```
   確認 ZIP 內含 `media/img/`，載入記憶引擎後卡片正面顯示聯想圖。

---

## 已知風險與注意

### 風險

| 風險 | 說明與因應 |
|------|-----------|
| ComfyUI API 回應結構與認知不符 | **開工時先手動打一次 API 確認**，不要照記憶實作。`/history` 的巢狀結構與 `/view` 的參數格式是最容易出錯的地方 |
| VRAM 不足導致 OOM | batch size 過大時 ComfyUI 可能回傳錯誤或直接崩潰。實測時從 1 開始逐步加大 |
| 生成圖含文字 | SDXL 類模型仍可能生出類文字紋理。若負向 prompt 效果不足，回報使用者討論是否調整 prompt 模板或 workflow |
| 長時間任務中斷 | 100 張圖可能耗時 30 分鐘以上。依賴 Phase 1 的原子寫入保護工作檔，中斷後可續作 |
| Seed 策略未定 | 穩定 seed 讓重生結果可重現，但若使用者是因為不滿意才重生，會得到同樣的圖。**此點需向使用者確認** |

### 不要做

- **不要**在程式碼中寫死任何節點 ID、`class_type` 或 workflow 結構假設
- **不要**修改使用者提供的 workflow JSON 原檔——注入時操作副本
- **不要**自行決定 batch size 預設值，須以實測數據回報使用者確認
- **不要**為了「讓圖更好看」而自行加入 prompt 內容——prompt 模板屬於 Phase 1 的 `prompts/`，要改先討論
- **不要**與 audio 階段併行執行——VRAM 會衝突，`run-all` 須維持依序
- **不要**引入 Gradio、FastAPI 等 Phase 5 才需要的相依

---

## 完成後

**暫停，等使用者完成驗收流程並確認**，才進入 Phase 4。

驗收通過後：
1. 依 [change-log-guide.md](change-log-guide.md) 於 `logs/` 產出改動日誌
2. 於日誌記錄 VRAM 實測數據與建議的 batch size
3. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表與外部介面狀態（ComfyUI workflow ✅）

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
