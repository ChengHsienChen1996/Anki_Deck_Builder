# Phase 6 執行計畫：匯入分頁與工作檔重置（待使用者確認）

日期：2026-08-26 ／ 分支：`dev_ai`
依據：使用者於 Phase 5 驗收後提出的兩項需求、[architecture.md](../../docs/architecture.md)、
現行 `web/`／`stages/ocr.py`／`state/` 的實際程式碼

> 前五個 phase 各有一份 `docs/` 等級的規格文件；Phase 6 是驗收後新增的範圍，
> **本檔同時是規格與執行計畫**。

---

## 0. 這個 phase 要解決什麼

| 需求 | 現況 | 目標 |
|------|------|------|
| 匯入 | 攝入只在 CLI（`ocr --input`）。UI 的「① OCR」只處理工作檔內已存在的 `pending` 列，無法從瀏覽器加入新書頁 | UI 新增「匯入」分頁，**輸入伺服器路徑**即可建立來源列 |
| 重置 | 沒有任何清空手段。要換一批教材只能手動刪檔或另開 `WORK_DIR` | 三段式重置（階段狀態／清空列／連媒體），CLI 與 UI 對等，且不易誤觸 |

---

## 1. 一條要正面推翻的既有禁令

[phase-5-webui.md](phase-5-webui.md)〈不要做〉寫著：

> **不要**在 UI 提供 CLI 沒有的破壞性操作（例如直接刪除卡片、清空工作檔）

Phase 6 要做的正是這件事。**使用者已於 2026-08-26 明確要求並知悉此事**，
理由是不同批次教材混在同一個工作檔裡難以區分。

當初那條禁令的顧慮是「一個按鈕毀掉跑了一小時的成果」，所以推翻它的同時要把顧慮處理掉：

| 顧慮 | 對策 |
|------|------|
| 誤觸 | **要輸入工作檔名稱**（例：`cards.csv`）才會啟用執行；不是「確定嗎？」的一鍵確認 |
| 不可回復 | 任何破壞性操作前**自動備份** `cards.csv` 成 `cards.csv.bak-<時間戳>` |
| UI 比 CLI 強 | **同步新增 `anki-builder reset`**。UI 不該有 CLI 沒有的能力（約束 4：兩者是平行介面） |
| 誤刪媒體 | 刪媒體是**獨立選項**，預設不刪。清空列但保留媒體，之後仍可用 `convert-media` 之類的工具檢視 |

---

## 2. 設計

### 2.1 重置的三個層級

一個 `reset` 動作，三種強度，由參數決定：

| 層級 | 做什麼 | 用在什麼情況 |
|------|--------|-------------|
| `stages`（預設，**非破壞性**） | 把指定階段的狀態設回 `pending`、清空該階段的 `*_error`，內容欄位一字不動 | 想整批重跑圖或語音，但不想重抽卡 |
| `rows` | 刪掉工作檔內所有列（保留檔案與表頭） | 換一批教材，媒體先留著 |
| `all` | 同 `rows`，另刪 `media/img`、`media/audio` 底下的檔案 | 真的要從零開始 |

三者**一律先備份 `cards.csv`**。`rows` 與 `all` 需要顯式確認（CLI 的 `--yes`／UI 的輸入檔名）。

### 2.2 邏輯放哪一層

重置動的是中間 CSV 與媒體檔，不屬於任何階段，也不該進介面層：

```
state/reset.py          ← 新增。backup_work() / reset_stages() / clear_rows() / clear_media()
   ↑              ↑
cli.py（reset）   web/service.py（reset_work）→ server.py 端點 → ui.py
```

`web/service.py` 一如既往是 UI 與 API 的唯一入口，本身不實作邏輯。

### 2.3 匯入：走路徑，不走上傳

瀏覽器的檔案上傳只能拿到**檔案**，要選整個資料夾得靠 `webkitdirectory`，
而且上傳後還得先落地成暫存檔——本專案是**本機工具**，伺服器與瀏覽器是同一台機器，
繞這一圈沒有意義。

因此匯入分頁就是一個**路徑輸入框**，行為與 `ocr --input` 完全相同：

```
[ /home/jason/scans/n2_book/            ] [檢查] [匯入]
```

- **檢查**（dry-run）：呼叫 `detect_input()`，顯示判別出的類型與**將被匯入的項目清單**
  （前 20 筆），不寫任何東西
- **匯入**：呼叫 `OCRStage.prepare()`，回報 `新增 N 列（略過 M 個已在工作檔中的來源）`，
  並提示「接著到狀態總覽按 ① OCR」

`prepare()` 只建立來源列、不呼叫 OCR 模型，所以匯入本身是秒級操作。

---

## 3. 任務執行順序

一次一個 task，完成即停下交付。

### Task 6.1 — 重置的核心邏輯與 CLI

**產出**：`state/reset.py`、`cli.py`（新增 `reset` 子命令）、`state/__init__.py`、
`tests/state/test_reset.py`、`tests/test_cli.py`（補充）

```bash
uv run anki-builder reset --stages image,audio_back      # 非破壞性，不需 --yes
uv run anki-builder reset --clear rows --yes             # 清空列
uv run anki-builder reset --clear all --yes              # 連媒體一起
```

- `--stages` 接階段名清單，未知名稱直接報錯並列出合法值（沿用 `stage_fields()` 的訊息風格）
- `--clear` 與 `--stages` 互斥
- `--clear` 不給 `--yes` 時**不做事**，印出「這會刪掉 N 列與 M 個媒體檔，加上 --yes 確認」
- 每次都回報備份檔路徑

**測試**：階段重置只動狀態欄位、內容欄位不變；未知階段名報錯；`--clear rows` 保留
表頭與檔案；`--clear all` 刪媒體但不刪工作檔以外的東西；備份確實產生；
缺 `--yes` 時什麼都沒發生。

### Task 6.2 — 重置的 service 與 API 端點

**產出**：`web/service.py`（擴充）、`web/server.py`（`POST /api/reset`）、
`tests/test_web_service.py`／`test_web_server.py`（補充）

- `service.reset_work(store, mode, stages=None, confirm=None)`：`mode` 為
  `stages`／`rows`／`all`；破壞性模式要求 `confirm` 等於工作檔的**檔名**，
  不符就拋 `ServiceError`
- 端點回傳 `{"backup": "...", "cleared_rows": N, "removed_media": M}`
- 重置與階段執行**互斥**：正在跑階段時拒絕重置（沿用 `StageRunner` 的單一飛行判斷），
  否則會與 checkpoint 寫入打架

**測試**：確認字串不符時不動任何東西；階段執行中回 409；三種模式的回傳統計正確。

### Task 6.3 — 匯入的 service 與 API 端點

**產出**：`web/service.py`（擴充）、`web/server.py`（`POST /api/ingest`、
`GET /api/ingest/preview`）、測試

- `service.preview_input(path)`：包 `detect_input()`，回 `{kind, items[:20], total}`
- `service.ingest_path(settings, store, path)`：組 OCR 階段（`stages/factory.py`）
  並呼叫 `prepare()`，回 `{kind, created, skipped}`
- 路徑不存在／格式不支援 → `ServiceError` → 404，訊息沿用 `detect_input()` 既有的
  「支援格式」提示

**測試**：目錄／單檔／PDF／`.txt` 四種判別；重複匯入不產生重複列；不存在的路徑報錯。

### Task 6.4 — 匯入分頁

**產出**：`web/ui.py`（新增分頁）

- 分頁順序：`[狀態總覽] [匯入] [抽取結果] [聯想圖] [失敗清單] [設定]`——
  放在狀態總覽之後，符合「先匯入再處理」的順序
- 路徑輸入框、`檢查` 與 `匯入` 兩顆按鈕、結果訊息、將匯入的項目清單
- 匯入成功後**自動刷新狀態總覽的統計**（新列會讓 `ocr` 的 pending 增加）

### Task 6.5 — 重置區塊與文件

**產出**：`web/ui.py`（設定分頁擴充）、`docs/usage.md`、`README.md`（一行）

- 重置放在**設定分頁底部**，不另開分頁——它不是日常操作，不該與五個常用分頁平起平坐
- 三個層級各一顆按鈕，破壞性的兩顆需先在輸入框打出工作檔名稱才會啟用
- `docs/usage.md` 新增〈匯入〉與〈重置工作檔〉兩節，並在疑難排解補「不同批次教材
  互相混淆怎麼辦」

---

## 4. 非目標

| 不做 | 理由 |
|------|------|
| 多工作檔切換（在 UI 裡挑另一份 `cards.csv`） | `serve` 沒有 `--work`（Phase 1 定案的參數結構不改），一個服務綁一個 `WORK_DIR`。要換就重啟服務指向另一個目錄——這比在 UI 裡維護「目前是哪一份」的狀態單純得多 |
| 瀏覽器上傳檔案 | 見 §2.3。本機工具，路徑輸入更直接 |
| 刪除單張卡片 | 這是**內容編輯**不是重置。抽取結果分頁已能改欄位；真要刪列，先想清楚語義（連媒體一起刪嗎？card_id 要回收嗎？）再談 |
| 匯入時直接跑 OCR | 匯入是秒級、OCR 是分鐘級。混在一顆按鈕裡會讓使用者以為當掉了。匯入後提示去按 ① OCR |

---

## 5. 風險

| 風險 | 因應 |
|------|------|
| 誤觸清空 | 三道防線：輸入檔名、自動備份、預設不刪媒體 |
| 重置與階段執行打架 | 執行中一律拒絕重置（單一飛行） |
| 路徑輸入等於開放整台機器的檔案系統 | 本機工具且綁 `127.0.0.1`，與 CLI 能做的事完全對等，不額外限制；但 `docs/usage.md` 要寫明「不要把 serve 暴露到區網」 |
| 匯入大目錄卡住介面 | `prepare()` 不呼叫模型，308 張圖也是秒級。PDF 逐頁渲染較久——若實測超過數秒，改走背景任務 |

---

## 6. 完成時的交付

1. 五個 task 完成，非付費測試全綠、`ruff check` 無錯
2. UI 六個分頁可用，CLI 多一個 `reset` 子命令
3. 驗收清單交使用者執行（含「輸入錯的檔名時什麼都不會發生」這條）
4. 驗收通過後：`logs/` 改動日誌、CLAUDE.md 進度表新增 Phase 6 一列
