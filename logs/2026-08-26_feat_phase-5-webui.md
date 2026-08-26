# 改動總結 — Phase 5 Web UI 與收尾

日期：2026-08-26
分支：dev_ai
相關 commit：`cf15856`／`3548998`（執行計畫與四項決定）、`62a0c2a`（Task 5.1）、
`eb209f9`（5.2）、`44722b6`（5.3）、`874c626`（5.4）、`b8a40c8`（5.5）、`ef89853`（5.6）、
`a7e7707`／`064c944`（驗收清單）

---

## 變更清單

### 新增程式：web/

1. `src/anki_deck_builder/web/service.py`：**唯一的膠水層**。FastAPI 端點與 Gradio
   callback 都只呼叫這裡，兩者自己不碰 `stages/`、`state/`。狀態連動表
   `FIELD_CASCADES` 只有這一份；篩選走 `get_status()`、遮罩走 `mask_secret()`。
   `update_rows()` 一次讀寫中間 CSV，一列不合法就整批不寫
2. `src/anki_deck_builder/web/server.py`：FastAPI，八個端點皆為 service 的薄殼。
   Gradio 以 `gr.mount_gradio_app(..., path="/", ssr_mode=False)` 掛在根路徑，
   `/api/*` 因先註冊而不被蓋掉。`allowed_paths` 只放媒體根目錄
3. `src/anki_deck_builder/web/tasks.py`：背景執行的**單一飛行**註冊表。
   GPU 只有一張，兩個階段並行必然搶 VRAM 且互相覆蓋 checkpoint，
   第二個請求一律回 409 不排隊
4. `src/anki_deck_builder/web/ui.py`：五個分頁。狀態總覽（統計、四顆執行按鈕、
   `gr.Timer` 輪詢進度）、抽取結果（篩選／分頁／逐欄編輯）、聯想圖（縮圖牆、
   單張重生）、失敗清單（單列與批次重跑）、設定（唯讀、金鑰遮罩）

### 新增程式：從 cli.py 抽出的共用層

5. `src/anki_deck_builder/stages/vram.py`：VRAM 讓渡（`free_vram_for()`、
   `free_vram_for_local_gpu()`、`release_comfyui()`）。原本在 `cli.py`，
   Web 要跑同樣的階段就得做同樣的事——各寫一份必然漂移
6. `src/anki_deck_builder/stages/factory.py`：階段組裝（哪個階段接哪個 client、
   OCR 要不要接 `on_finish` 卸載）。同上，**這是計畫書之外的追加**，
   理由是只搬 VRAM 的話組裝邏輯仍會在 web 裡被寫第二遍

### 修改程式

7. `src/anki_deck_builder/state/selector.py`：`select_pending()` 加選填 `card_ids`，
   在既有狀態篩選**之後**再過濾。給 `None` 時行為與先前完全相同
8. `src/anki_deck_builder/stages/base.py`／`image.py`／`audio.py`：三個 `run()`
   各加同名選填參數往下傳。`image`／`audio` 為了進度條總數會自己再篩一次，
   那裡也要帶上，否則分母與實際處理數不符
9. `src/anki_deck_builder/cli.py`：`serve` 從 `NOT_IMPLEMENTED` 移除並接上實作；
   VRAM 與階段組裝改為呼叫新模組；`_dispatch` 對沒有 `--work` 的子命令改用
   `getattr`
10. `src/anki_deck_builder/stages/image.py`：`stable_seed()` 上限 2^64 → 2^31
    （見〈實跑才發現的三個問題〉）
11. `pyproject.toml`／`uv.lock`：新增 `fastapi`、`gradio>=6.26,<7`、`uvicorn[standard]`

### 新增測試

12. `tests/test_web_service.py`：膠水層 44 項。狀態連動（含「改背面文字不影響正面」）、
    批次編輯只寫一次檔、路徑穿越、金鑰遮罩、`card_ids` 原封傳給階段
13. `tests/test_web_server.py`：端點 22 項。以 `httpx.ASGITransport` 直接打 app，
    不起真 server。含單一飛行的 409、背景任務例外留在進度上、UI 掛載不蓋掉 API
14. `tests/test_web_ui.py`：6 項。介面組得起來（Gradio 改版會讓元件參數靜靜失效）、
    進度文字格式
15. `tests/test_cli.py`：`serve` 的兩項改寫（原本測的是「未實作」）

### 文件

16. `README.md`：從一行標題擴充為用途、安裝、最小範例、文件索引。
    收尾時補上**資料夾／PDF 批量輸入**的說明——使用者實際問了才發現這件事
    只寫在 usage.md 的表格裡，不夠顯眼
17. `docs/usage.md`：新增。前置需求、`.env` 逐項、CLI 用法與常用組合、
    Web UI 三個核心操作、五類疑難排解
18. `.agent/plans/phase-5-execution-plan.md`、`.agent/plans/phase-5-acceptance.md`

## 測試結果

- pytest：**631 passed / 0 failed**（31 deselected 為付費 API 標記）
- ruff check：無錯
- 需人工測試項目：無新增付費 API 呼叫。Gradio 介面照 phase 文件由人工驗收，
  但「介面組得起來」與進度文字格式已自動化

## 實跑才發現的三個問題

單元測試全綠但實際跑起來才炸的，記在這裡：

| 問題 | 原因 | 處置 |
|------|------|------|
| `anki-builder serve` 一啟動就死 | `uvicorn.run()` 自己開事件迴圈，而 `main()` 早在 `asyncio.run()` 裡 → `asyncio.run() cannot be called from a running event loop`。測試 mock 掉 serve 所以看不出來 | 改用 `uvicorn.Server(config).serve()` 直接 await |
| 三張卡全部生圖失敗 | `stable_seed()` 產生 2^64 級的值，而 `Seed (rgthree)` 節點上限是 2^50，整份 workflow 被 ComfyUI 以 `value_bigger_than_max` 退回。手動測試用固定小 seed 所以沒踩到 | seed 上限收到 2^31，補測試 |
| `convert-media.py` 回寫 CSV 時拋 `ValueError` | 媒體根目錄停在相對路徑，而來源路徑是 resolve 過的絕對路徑 | 根目錄一併 resolve |

另有一類反覆出現的 UI bug：**Gradio 的輸出必須逐一列出，漏掉哪個元件它就停在舊值**。
Task 5.2 的「執行中」提示、5.4 的卡片狀態標題、5.5 的動作訊息各踩過一次，
症狀都是「數字對了但旁邊的文字沒跟上」。

## 備註

- **Web UI 沒有攝入端**：`ocr --input` 只在 CLI。UI 的「① OCR」按鈕只處理工作檔內
  已存在的 `pending` 列，不能從瀏覽器加入新書頁。這是 phase 文件的範圍決定
  （UI 只做「CLI 難做的三件人工互動」），不是遺漏
- **儲存只作用於當前頁**：抽取結果分頁跨頁編輯後才按儲存，前一頁的修改不會寫入
- **serve 期間不要另開 CLI 跑同一份工作檔**：單一飛行的鎖只擋得住 UI 自己
- 驗收由使用者於 2026-08-26 執行，八步全數通過
