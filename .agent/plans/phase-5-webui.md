# Phase 5 — Web UI 與收尾

## 目標

提供本地 Web 介面，解決三個 CLI 難以完成的人工互動場景：**抽取結果逐欄檢視編輯**、**生成圖預覽與單張重生**、**失敗清單批次重跑**。同時補齊使用文件，專案收尾。

**核心約束**：Web UI 是 CLI 的**平行介面**，底層呼叫 `stages/` 的同一組函式，不重複實作任何業務邏輯。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] `uv run anki-builder serve` 啟動本地服務，瀏覽器可存取
- [ ] 設定面板顯示 `.env` 生效值，**金鑰欄位已遮罩**
- [ ] 抽取結果以表格呈現，可逐欄編輯，儲存後該列 `extract_status` 設回 `pending`
- [ ] 生成圖以縮圖牆呈現，可就地編輯 `image_prompt` 並一鍵只重生該列
- [ ] 失敗清單集中顯示所有 `failed` 列與錯誤原因，可一鍵批次重跑
- [ ] 長時間任務（image／audio）有進度顯示，不卡住介面
- [ ] `web/` 未重複實作任何 `stages/` 已有的邏輯（人工複查驗證）
- [ ] `README.md` 與 `docs/usage.md` 完成
- [ ] 後端 pytest 全數通過

---

## 技術選型

| 組件 | 選型 | 說明 |
|------|------|------|
| 後端 | FastAPI | async 原生，與 stages 的 async 介面一致 |
| 介面 | Gradio | 免自寫前端，適合本專案的人工檢視需求 |
| 整合方式 | Gradio 掛載於 FastAPI *(待確認)* | 讓 API 與 UI 共用同一個服務 |

> **待確認**：Gradio 與 FastAPI 的整合 API（掛載函式名稱與用法）在不同版本間有變動，**開工時先查閱 Gradio 官方文件確認當前用法**，不要照記憶寫。
>
> 若整合過於複雜，**允許改為 Gradio 獨立啟動、不掛 FastAPI**——此時 Task 5.1 的 API 層可簡化為純函式呼叫。作此變更前先向使用者說明。

---

## 子任務拆分

### Task 5.1 — 後端服務層

**產出**
```
src/anki_deck_builder/web/__init__.py
src/anki_deck_builder/web/server.py
tests/test_web_server.py
```

**要求**
1. 建立 FastAPI app，提供以下端點：

   | 端點 | 方法 | 用途 |
   |------|------|------|
   | `/api/status` | GET | 回傳各階段統計，等同 CLI `status` |
   | `/api/rows` | GET | 回傳中間 CSV 全部列（支援分頁） |
   | `/api/rows/{card_id}` | PATCH | 更新單列欄位，並將對應階段狀態設回 `pending` |
   | `/api/stages/{stage}/run` | POST | 觸發指定階段，參數含 `force` / `only_failed` / `card_ids` |
   | `/api/stages/{stage}/progress` | GET | 查詢執行中階段的進度 |
   | `/api/failed` | GET | 回傳所有 `failed` 列與錯誤原因 |
   | `/api/config` | GET | 回傳 `.env` 生效值，**金鑰必須遮罩** |
   | `/api/media/img/{card_id}` | GET | 回傳生成圖，供縮圖牆顯示 |

2. **所有端點一律呼叫 `stages/` 既有函式**，不得在 `web/` 內重新實作處理邏輯
3. 階段執行為長時間任務，須以背景任務執行並提供進度查詢，**不得阻塞請求**
4. `/api/config` 的遮罩使用 Phase 1 已實作的 `mask_secret()`

> **新增能力**：`POST /api/stages/{stage}/run` 支援 `card_ids` 參數，只處理指定列。這是 CLI 沒有的能力，需在 `stages/base.py` 加入對應支援。**這是允許的擴充**，但只准加參數，不准改既有行為；若必須改動骨架邏輯，停下來討論。

**測試**：完整單元測試，執行至通過。涵蓋各端點的參數驗證、金鑰遮罩、錯誤回應。以 mock 的 stage 函式驗證「未重複實作邏輯」。

---

### Task 5.2 — 設定與狀態面板

**產出**
```
src/anki_deck_builder/web/ui.py
```

**要求**
1. 建立 Gradio 介面骨架，分頁結構：
   ```
   [ 狀態總覽 ] [ 抽取結果 ] [ 聯想圖 ] [ 失敗清單 ] [ 設定 ]
   ```
2. **狀態總覽** 分頁：
   - 各階段的 pending / done / failed 統計表
   - 每個階段一個「執行」按鈕，可帶 `force` / `only_failed` 選項
   - 執行中顯示進度
3. **設定** 分頁：
   - 唯讀顯示 `.env` 生效值
   - 金鑰欄位遮罩
   - 標示哪些服務目前不可達（可選，若實作簡單）
4. 加入 `fastapi`、`gradio` 至 `pyproject.toml` 主要相依

**測試**：前端不強制單元測試，人工驗證。

---

### Task 5.3 — 抽取結果編輯

**產出**
```
src/anki_deck_builder/web/ui.py（擴充）
```

**要求**
1. **抽取結果** 分頁以表格呈現全部卡片，欄位至少含：
   `card_id` `deck` `front` `back` `example` `image_prompt` `tts_front_text` `tts_back_text` `difficulty` `tags`
2. 支援逐欄編輯
3. 儲存後：
   - 更新對應列的欄位
   - 將 `extract_status` 設回 `pending`
   - 若編輯的是 `image_prompt`，**額外**將 `image_status` 設回 `pending`
   - 若編輯的是 `tts_*_text`，**額外**將對應的 `audio_*_status` 設回 `pending`
4. 提供篩選：依 deck、依狀態
5. 大量卡片時須分頁，避免一次載入數百列造成介面卡頓

**測試**：狀態連動邏輯（改 `image_prompt` 連帶重置 `image_status`）須在後端實作並撰寫**可執行**單元測試。

---

### Task 5.4 — 聯想圖預覽與重生

**產出**
```
src/anki_deck_builder/web/ui.py（擴充）
```

**要求**
1. **聯想圖** 分頁以縮圖牆呈現所有已生成的圖
2. 每張縮圖下方顯示 `card_id` 與 `front`
3. 點擊縮圖展開：
   - 大圖預覽
   - `image_prompt` 可編輯文字框
   - 「重生此圖」按鈕
4. 「重生此圖」行為：
   - 儲存修改後的 `image_prompt`
   - 將該列 `image_status` 設回 `pending`
   - 呼叫 `POST /api/stages/image/run` 並帶 `card_ids=[該卡]`
   - 完成後更新縮圖
5. 尚未生成圖的卡片顯示佔位圖示，並提供「生成」按鈕

**測試**：前端人工驗證；後端的單列觸發邏輯已於 Task 5.1 測試。

---

### Task 5.5 — 失敗清單

**產出**
```
src/anki_deck_builder/web/ui.py（擴充）
```

**要求**
1. **失敗清單** 分頁集中顯示所有 `failed` 列
2. 表格欄位：`card_id`、失敗階段、錯誤訊息、`front` 摘要
3. 依階段分組或提供階段篩選
4. 提供兩種重跑：
   - 「重跑此列」——單列
   - 「重跑全部失敗」——依階段批次，等同 CLI `--only-failed`
5. 錯誤訊息過長時可展開查看完整內容

**測試**：前端人工驗證。

---

### Task 5.6 — 使用文件

**產出**
```
README.md
docs/usage.md
```

**要求**

`README.md`（簡潔，指向詳細文件）：
1. 一段話說明專案用途與與記憶引擎的關係
2. 安裝步驟（uv）
3. 最小可用範例：三行指令從圖片到 ZIP
4. 指向 `docs/usage.md` 與 `CLAUDE.md`

`docs/usage.md`（完整）：
1. 前置需求：ComfyUI、VOXCPM2、串接套件的準備方式
2. `.env` 設定逐項說明，特別是 ComfyUI 節點 ID 的取得方式（含 `Save (API Format)` 步驟）
3. 五階段的 CLI 用法與常用參數組合
4. Web UI 的三個核心操作說明
5. 常見問題排解：
   - 節點 ID 找不到
   - VRAM 不足
   - OCR 辨識率低
   - 生成圖含文字
   - 中斷後如何續作

**不要做**：不要在使用文件中重抄通用規範文檔的內容，開發規範一律指向 `CLAUDE.md`。

---

## 驗收流程

1. **啟動服務**
   ```bash
   uv run anki-builder serve --port 8080
   ```
   瀏覽器開啟，確認五個分頁皆可存取。

2. **金鑰遮罩**
   於「設定」分頁確認 `LLM_API_KEY` 等欄位已遮罩，未明文顯示。

3. **狀態總覽與觸發**
   準備一份部分完成的 CSV，確認統計數字與 CLI `status` 一致。按「執行」觸發某階段，確認進度顯示且介面未凍結。

4. **抽取結果編輯連動**
   - 編輯某列的 `front`，儲存 → 確認 `extract_status` 變為 `pending`
   - 編輯某列的 `image_prompt`，儲存 → 確認 `extract_status` **與** `image_status` 皆變為 `pending`
   - 編輯某列的 `tts_back_text`，儲存 → 確認 `audio_back_status` 變為 `pending`、`audio_front_status` **不變**

5. **單張重生**
   於「聯想圖」分頁點擊某張圖，修改 prompt 後按「重生此圖」，確認：
   - 只有該張圖被重新生成
   - 其他圖檔案修改時間未變
   - 縮圖已更新

6. **失敗清單批次重跑**
   人工製造數個失敗（例如清空某些 `image_prompt`），於「失敗清單」確認皆列出、錯誤訊息正確，按「重跑全部失敗」確認只處理失敗項。

7. **CLI 與 UI 行為一致**
   同一份 CSV，分別用 CLI 與 UI 執行同一階段，確認結果相同——這驗證了「未重複實作邏輯」。

8. **文件可用性**
   請一位未參與開發的人依 `README.md` 與 `docs/usage.md` 從零跑通一次，記錄卡住的地方並補充文件。

---

## 已知風險與注意

### 風險

| 風險 | 說明與因應 |
|------|-----------|
| Gradio 與 FastAPI 整合 API 變動 | **開工時先查官方文件**。若整合困難，允許改為 Gradio 獨立啟動，但需先向使用者說明 |
| 長時間任務阻塞介面 | 階段執行須為背景任務。若 Gradio 的非阻塞機制不足，回報討論 |
| 大量卡片時介面卡頓 | 數百列表格與縮圖牆須分頁。發現效能問題先報告，不要自行加快取層 |
| 邏輯重複實作 | 這是本 phase 最容易犯的錯。任何「在 web/ 裡寫 if 判斷狀態」的行為都要警覺，該邏輯應該在 `stages/` 或 `state/` |
| 單列觸發的骨架擴充 | Task 5.1 需要 `stages/base.py` 支援 `card_ids` 參數。只准加參數，不准改既有行為 |

### 不要做

- **不要**在 `web/` 內重新實作狀態判斷、篩選、檔名組合等 `stages/` 或 `state/` 已有的邏輯
- **不要**為了 UI 方便而修改 `stages/` 的既有函式簽章。需要新能力時以**新增選填參數**的方式擴充
- **不要**在 UI 中提供 CLI 沒有的破壞性操作（例如直接刪除卡片、清空工作檔）
- **不要**自行決定改用其他 UI 框架。若 Gradio 不敷使用，停下來討論
- **不要**在使用文件中重抄通用規範文檔內容
- **不要**加入使用者未要求的功能（帳號系統、多專案管理、雲端同步等）——參見 [project-overview.md](project-overview.md) 的非目標

---

## 完成後

**暫停，等使用者完成驗收流程並確認**。

驗收通過後：
1. 依 [change-log-guide.md](change-log-guide.md) 於 `logs/` 產出改動日誌
2. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表，全部 phase 標記完成
3. 回顧外部相依狀態（agent_factory / ComfyUI workflow / VOXCPM2），若 `MODEL_LIMITS` 登錄等待辦事項仍未解決，於日誌明確列出

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
