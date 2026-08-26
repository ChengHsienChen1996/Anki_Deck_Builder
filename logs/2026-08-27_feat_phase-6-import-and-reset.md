# 改動總結 — Phase 6 匯入分頁與工作檔重置

日期：2026-08-27
分支：dev_ai
相關 commit：`2fafaa0`（執行計畫）、`150d994`（Task 6.1）、`45c66b5`（6.2）、
`88374ac`（6.3）、`84a7491`（6.4）、`5f2ef35`（6.5）、`1aba074`（驗收清單）

> Phase 6 是 Phase 5 驗收後由使用者提出的追加範圍：攝入只在 CLI 太不方便，
> 而不同批次的教材混在同一個工作檔裡難以區分。

---

## 變更清單

### 新增程式

1. `src/anki_deck_builder/state/reset.py`：重置與清空的核心。
   `backup_work()`／`reset_stages()`／`clear_rows()`／`count_media()`。
   **任何動作前都先備份** `cards.csv.bak-<時間戳>`；
   要刪哪些媒體目錄**由呼叫端傳入**，狀態層不認得 `media/img` 這種路徑
   （那是流程層的知識，約束 4 不得反向依賴）

### 修改程式

2. `src/anki_deck_builder/cli.py`：新增 `reset` 子命令。`--stages` 非破壞性直接執行；
   `--clear rows|all` 不給 `--yes` 時**什麼都不做**，只印出會刪掉多少東西並回傳 1
3. `src/anki_deck_builder/stages/pack.py`：新增 `media_directories(root)`。
   CLI 與 web 都要知道「媒體檔落在哪些目錄」，各推一次必然漂移，
   因此與 `MEDIA_DIRS` 放在一起——那裡本來就是媒體版面的權威定義處
4. `src/anki_deck_builder/web/service.py`：新增 `reset_work()`、`preview_input()`、
   `ingest_path()`。破壞性重置要求 `confirm` 等於工作檔**檔名**；
   重置與匯入都會寫工作檔，因此**有階段在跑時一律拒絕**（`StageBusyError`）
5. `src/anki_deck_builder/web/server.py`：`POST /api/reset`、`POST /api/ingest`、
   `GET /api/ingest/preview` 三個端點，皆為 service 的薄殼，忙碌回 409
6. `src/anki_deck_builder/web/ui.py`：新增「匯入」分頁（排在狀態總覽之後）與
   設定分頁底部的重置區塊。`_status_tab()` 改為回傳
   `(統計表, 重新整理 callback)`，讓匯入與重置成功後能跨分頁刷新統計

### 文件

7. `docs/usage.md`：新增〈匯入：把書頁收進工作檔〉〈重置工作檔〉兩節、
   CLI 表格補 `reset`，疑難排解補〈不同批次的教材混在一起了〉
8. `README.md`：一行指向 Web UI 的匯入與重置
9. `.agent/plans/phase-6-execution-plan.md`（同時是本 phase 的規格）、
   `.agent/plans/phase-6-acceptance.md`

### 新增測試

10. `tests/state/test_reset.py`：17 項。重點不只是「有沒有刪掉」，
    更是「**沒有**刪到不該刪的」——內容欄位、媒體目錄本身、子目錄裡的檔案
11. `tests/test_web_service.py`：+15 項（重置 7、匯入 8）
12. `tests/test_web_server.py`：+9 項（重置 5、匯入 4）
13. `tests/test_cli.py`：+7 項

## 測試結果

- pytest：**680 passed / 0 failed**（31 deselected 為付費 API 標記）
- ruff check：無錯
- 需人工測試項目：無新增付費 API 呼叫

## 一條被正面推翻的既有禁令

[phase-5-webui.md](../.agent/plans/phase-5-webui.md)〈不要做〉原本寫著：

> **不要**在 UI 提供 CLI 沒有的破壞性操作（例如直接刪除卡片、清空工作檔）

使用者於 2026-08-26 明確要求推翻並知悉此事。當初的顧慮是「一個按鈕毀掉跑了一小時
的成果」，因此推翻的同時把顧慮逐條轉成對策：

| 顧慮 | 對策 |
|------|------|
| 誤觸 | 要**打出工作檔名稱**才啟用，不是「確定嗎？」一鍵。UI 的啟用只是視覺提示，`service` 仍會再驗一次 |
| 不可回復 | 每次動手前自動備份 `cards.csv.bak-<時間戳>` |
| UI 比 CLI 強 | 同步新增 `anki-builder reset`，兩者能力對等（約束 4） |
| 誤刪媒體 | 刪媒體是獨立選項，預設不刪；只刪目錄**第一層**，不遞迴、不刪目錄本身 |
| 與執行中的階段打架 | 有階段在跑時拒絕，理由是 checkpoint 寫入會互相覆蓋 |

## 設計上的三個決定

**重置分三級而不是一顆「清空」按鈕。** `stages` 是非破壞性的（只把狀態設回
`pending`，內容與既有的圖／語音都留著），這才是「想整批重跑圖」時真正要的東西；
真的要換教材才用 `rows`／`all`。

**匯入走路徑而不是上傳。** 伺服器與瀏覽器是同一台機器，上傳等於把檔案複製一份
再讀回來。路徑輸入與 CLI 的 `ocr --input` 是同一件事，行為也完全一致。

**匯入不順便跑 OCR。** 匯入是秒級、OCR 是分鐘級，混在一顆按鈕裡會讓使用者以為
當掉了。匯入完提示去按「① OCR」。

## 備註

- **多工作檔切換仍不做**：`serve` 沒有 `--work`（Phase 1 定案的參數結構不改），
  一個服務綁一個 `WORK_DIR`。要換就重啟指向另一個目錄——這比在 UI 裡維護
  「目前是哪一份」的狀態單純得多。`docs/usage.md` 的疑難排解寫了這條路線
- **刪除單張卡片不做**：那是內容編輯不是重置，語義（連媒體一起刪嗎？
  `card_id` 要回收嗎？）需要先想清楚
- 驗收由使用者於 2026-08-27 執行，七步全數通過
