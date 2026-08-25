# Phase 5 執行計畫（待使用者確認）

日期：2026-08-25 ／ 分支：`dev_ai`
依據：[phase-5-webui.md](phase-5-webui.md)、[architecture.md](../../docs/architecture.md)、
現行 `cli.py`／`stages/`／`state/` 的實際程式碼、Gradio 官方文件（2026-08-25 查證）

---

## 0. 開工前檢查結果

| 檢查項 | 結果 |
|--------|------|
| 分支 | ✅ `dev_ai`，工作區乾淨（`d93e6de`） |
| `.claude/settings.local.json` 備份 | ⚠️ 專案內仍無 `.claude/` 目錄（同 Phase 1–4），**無檔案可備份** |
| Phase 4 交付 | ✅ 驗收通過，`ocr → extract → image → audio → pack` 全流程可跑 |
| `serve` 子命令 | ✅ 已存在於 `cli.py:133`（parser 含 `--host`／`--port`），目前落在 `NOT_IMPLEMENTED` |
| `mask_secret()` | ✅ `config.py:54`，Phase 1 已實作，Task 5.1 直接用 |
| 外部相依 | ✅ 三者皆已確認（agent_factory／ComfyUI／VOXCPM2），本 phase 不新增外部服務 |

### Gradio × FastAPI 整合實查（不依印象）

查 [Sharing Your App](https://www.gradio.app/guides/sharing-your-app)〈Mounting Gradio within FastAPI〉，
現行 API 為：

```python
app = gr.mount_gradio_app(app, demo, path="/gradio")
```

**整合可行，phase 文件的「待確認」可以定案為「掛載於 FastAPI」**，不需退回 Gradio 獨立啟動。

PyPI 現況：`gradio 6.26.0`、`fastapi 0.141.1`、`uvicorn 0.52.4`（皆 requires-python >=3.10）。
⚠️ **Gradio 6 相對 5 有破壞性變更**，其中一項直接打到本 phase：`gr.Dataframe` 的
`row_count`／`col_count` 參數結構改了（Task 5.3 的可編輯表格會用到）。
每個元件的參數在寫之前**逐一查當前文件**，不照記憶寫。

---

## 1. 三個要你先拍板的決定

phase 文件沒有規定、但會決定整個 `web/` 長相的三件事。

### 1.1 UI 與 API 的關係：中間加一層 `web/service.py`（建議）

Task 5.1 要八個 REST 端點，Task 5.2–5.5 要 Gradio 介面。兩者的關係有兩種寫法：

| 做法 | 問題 |
|------|------|
| Gradio 的 callback 透過 HTTP 打自己的 API | 同一個行程內繞一圈 HTTP，錯誤變成 status code 再轉回訊息，除錯困難 |
| Gradio 與 FastAPI **各自**呼叫 `stages/` | 兩邊都要寫「哪些欄位變動要重置哪些狀態」——正是本 phase 最容易犯的重複實作 |

**建議第三種**：

```
web/service.py   ← 唯一的膠水層：呼叫 stages/、state/，回傳純資料結構
   ↑        ↑
server.py    ui.py        ← FastAPI 端點與 Gradio callback 都只是薄殼
```

`server.py` 的端點是 `service.py` 的薄包裝，`ui.py` 的 callback 直接呼叫同一組
service 函式（同行程、async、不繞 HTTP）。八個端點照 phase 文件實作、可用 curl 打，
但 UI 不依賴它們。這樣「未重複實作邏輯」是結構保證，不是靠自律。

### 1.2 VRAM 讓渡邏輯要從 `cli.py` 搬出來（需要你點頭，因為會動到 Phase 3／4 的既有程式）

`cli.py` 目前有三個函式在做「跑 image／audio 前先把 Ollama 或 ComfyUI 的 VRAM 讓出來」：

- `_free_vram_for()`（`cli.py:195`）
- `_release_comfyui()`（`cli.py:213`）
- `_free_vram_for_local_gpu()`（`cli.py:373`）

Web UI 觸發 image／audio 時**必須做同樣的事**，否則同一台機器上 UI 跑出來的結果會
跟 CLI 不一樣（VRAM 不足時的失敗率差異）。三個選項：

| 選項 | 評語 |
|------|------|
| `web/` 直接 `from ..cli import _free_vram_for` | 介面層互相依賴，且引用私有函式。不可 |
| `web/` 自己再寫一次 | 正是約束 4 禁止的重複實作 |
| **搬到 `stages/vram.py`，`cli.py` 與 `web/` 都 import**（建議） | 純搬移，行為不變；`cli.py` 對應行改為呼叫新模組 |

搬移會動到 Phase 3／4 已驗收的檔案，**所以先問你**。同意的話我會：只搬不改邏輯、
既有 CLI 測試（`tests/test_cli.py`）全部保持綠燈作為行為未變的證據。

### 1.3 進度怎麼給 UI：從中間 CSV 反推（建議），不改骨架

`ProgressReporter` 是寫給終端機的（`stages/progress.py`，輸出到 stream）。UI 要的是數字。

**建議做法**：背景任務只在 `web/` 記「哪個階段正在跑、開始時間、結束後的 `StageResult`」，
進度數字則由 `summarize()` 重讀中間 CSV 得出——`image`／`audio` 的 `checkpoint_every = 1`，
每完成一列就落地，所以這個數字是準的。**骨架完全不動。**

代價：`ocr`／`extract` 的 `checkpoint_every` 是 10，進度以 10 列為粒度跳動。
若你覺得不夠細，替代方案是給 `BaseStage.run()` 加一個選填 `on_progress` callback
（phase 文件允許「新增選填參數」）——但那要動 `base.py`，我傾向不動。

---

## 2. 與 phase-5-webui.md 不同的兩處

### 2.1 `card_ids` 擴充點在 `state/selector.py`，不只是 `stages/base.py`

phase 文件說「需在 `stages/base.py` 加入對應支援」。實際看程式碼，篩選規則整條在
`select_pending()`（`state/selector.py:57`），`BaseStage.run()` 只是呼叫它。因此：

```python
# state/selector.py
def select_pending(rows, stage, force=False, only_failed=False,
                   card_ids: Collection[str] | None = None) -> list[CardRow]:
    ...  # 既有規則跑完後，若 card_ids 有給，再依 card_id 過濾
```

`BaseStage.run()`、`ImageStage.run()`、`AudioStage.run()` 三處各加一個同名選填參數往下傳。
**只加參數，不改既有行為**：不給 `card_ids` 時每一行的結果與現在完全相同，
既有測試不必改一個字。

> 注意 `ImageStage.run()`／`_AudioStage.run()` 為了進度條總數會**自己再篩一次**
> （`image.py:118`），那裡也要一併帶上 `card_ids`，否則進度條總數會與實際處理數不符。

### 2.2 媒體端點要防路徑穿越

`/api/media/img/{card_id}` 的檔案路徑來自中間 CSV 的 `image_front` 欄位——那是可編輯的
使用者資料。實作時**只回傳「該 card_id 在 CSV 上登記的檔案」**，且解析後的絕對路徑必須
落在媒體根目錄底下，否則回 404。不接受把路徑當查詢參數傳進來。

---

## 3. 任務執行順序

一次一個 task，完成即停下交付。

### Task 5.1 — 後端服務層

**產出**：`web/__init__.py`、`web/service.py`、`web/server.py`、`web/tasks.py`、
`stages/vram.py`（§1.2 同意的話）、`state/selector.py`（加 `card_ids`）、
`stages/base.py`／`image.py`／`audio.py`（各加一個選填參數）、`cli.py`（`serve` 接線）、
`tests/test_web_server.py`、`tests/test_web_service.py`、`pyproject.toml`

- FastAPI app 提供 phase 文件列的八個端點，全部是 `service.py` 的薄包裝
- `web/tasks.py`：背景任務註冊表。**單一飛行**——同時只允許一個階段在跑
  （GPU 只有一張，兩個階段並行必然搶 VRAM），第二個請求回 409 並附上正在跑的階段名
- `/api/config` 用 `mask_secret()`（`config.py:54`），不自己寫遮罩
- `serve` 子命令從 `NOT_IMPLEMENTED` 移除，改為 `uvicorn.run(app, host, port)`，
  預設仍綁 `127.0.0.1`（本機工具，不對外）

**測試**：`httpx.ASGITransport` 直接打 app，不起真的 server（httpx 已是既有相依）。
涵蓋各端點參數驗證、金鑰遮罩、單一飛行的 409、`card_ids` 只處理指定列、
路徑穿越回 404，以及**以 mock 的 stage 驗證「web 沒有自己實作邏輯」**。

### Task 5.2 — 設定與狀態面板

**產出**：`web/ui.py`、`pyproject.toml`（gradio）

- 五個分頁的骨架：`[狀態總覽] [抽取結果] [聯想圖] [失敗清單] [設定]`
- 狀態總覽：`summarize()` 的統計表 + 每階段一個「執行」按鈕（可勾 `force`／`only_failed`）
- 執行中以 `gr.Timer` 輪詢進度（§1.3），介面不凍結
- 設定分頁唯讀顯示 `.env` 生效值，金鑰遮罩

**測試**：前端人工驗證（phase 文件即如此規定）；service 層的行為在 5.1 已測。

### Task 5.3 — 抽取結果編輯

**產出**：`web/service.py`（擴充）、`web/ui.py`（擴充）、`tests/test_web_service.py`（擴充）

- 可編輯表格（`gr.Dataframe`，⚠️ Gradio 6 參數已變，寫前查文件）
- 依 deck／狀態篩選，分頁載入（308 張卡已經是「數百列」的量級）
- **狀態連動寫在 `service.py`，不寫在 UI callback**：改 `front` → `extract_status` 回
  `pending`；改 `image_prompt` → 連帶 `image_status`；改 `tts_front_text`／`tts_back_text`
  → 連帶對應那一側的 `audio_*_status`，另一側不動
- 欄位 → 要重置哪些階段的對應表放 `service.py` 一處，UI 不得有第二份

**測試**：連動邏輯的可執行單元測試（phase 文件明確要求），含「改背面文字不影響正面狀態」。

### Task 5.4 — 聯想圖預覽與重生

**產出**：`web/ui.py`（擴充）

- `gr.Gallery` 縮圖牆，圖片經 `/api/media/img/{card_id}`
- 點開：大圖 + 可編輯 `image_prompt` + 「重生此圖」
- 「重生此圖」＝ 存 prompt → `image_status` 回 `pending` → 觸發 image 階段並帶
  `card_ids=[該卡]`（5.1 的擴充）→ 完成後換掉縮圖
- 沒有圖的卡片顯示佔位圖示與「生成」按鈕

**測試**：前端人工驗證；單列觸發已於 5.1 測過。

### Task 5.5 — 失敗清單

**產出**：`web/service.py`（擴充）、`web/ui.py`（擴充）

- 集中顯示所有 `failed` 列：`card_id`、失敗階段、錯誤訊息、`front` 摘要
- 資料來源是既有的 `failed_rows()`（`state/selector.py:112`），不自己掃狀態欄位
- 「重跑此列」（帶 `card_ids`）與「重跑全部失敗」（等同 `--only-failed`）
- 錯誤訊息可展開（CSV 內已截到 500 字，見 `base.py:MAX_ERROR_LENGTH`）

### Task 5.6 — 使用文件

**產出**：`README.md`（目前只有一行標題）、`docs/usage.md`（不存在）

- README：用途一段話、uv 安裝、三行最小範例、指向 `docs/usage.md` 與 `CLAUDE.md`
- usage.md：前置需求、`.env` 逐項說明（特別是 ComfyUI 節點 ID 的 `Save (API Format)` 取得方式）、
  五階段 CLI 用法、Web UI 三個核心操作、常見問題排解五項
- **不重抄通用規範文檔**，開發規範一律指向 `CLAUDE.md`
- 排解章節直接引用已實測的數據：VRAM 峰值、短詞生成失敗率、聯想圖多元素構圖的已知限制

---

## 4. 相依新增

```toml
"fastapi>=0.141",
"gradio>=6.26,<7",
"uvicorn[standard]>=0.52",
```

`gradio` 會連帶拉進 pandas、huggingface-hub、starlette 等一票套件，`uv.lock` 會明顯變大。
本專案是本機工具，這個代價可以接受；但**如果你不想讓 gradio 進主要相依**，
可以改放 `[project.optional-dependencies]` 的 `web` 群組，`serve` 時才需要安裝——
說一聲我就這樣做。

---

## 5. 風險與因應

| 風險 | 因應 |
|------|------|
| ~~Gradio × FastAPI 整合 API 變動~~ | ✅ 已查證 `gr.mount_gradio_app`（§0） |
| Gradio 6 元件參數變動（`Dataframe` 首當其衝） | 每個元件寫之前查當前文件；版本鎖 `<7` |
| 邏輯重複實作（本 phase 頭號風險） | §1.1 的 `service.py` 單一膠水層，結構上杜絕 |
| UI 與 CLI 同時跑造成 CSV 互相覆寫 | 單一飛行只擋得住 UI 內部。文件明寫「serve 期間不要另開 CLI 跑同一份工作檔」 |
| 長時間任務阻塞介面 | 背景 task + Timer 輪詢；image 一批可能 30 分鐘 |
| 數百列表格／縮圖牆卡頓 | 分頁載入。**發現效能問題先報告，不自行加快取層** |
| 骨架擴充失控 | `card_ids` 只加選填參數；既有測試不改一字即為證據 |

### 不要做（取自 phase-5-webui.md）

- 不在 `web/` 重新實作狀態判斷、篩選、檔名組合
- 不為了 UI 方便修改 `stages/` 既有函式簽章（只加選填參數）
- 不在 UI 提供 CLI 沒有的破壞性操作（刪卡、清空工作檔）
- 不自行改用其他 UI 框架
- 不在使用文件重抄通用規範
- 不加使用者沒要求的功能（帳號、多專案、雲端同步）

> 一個**沒有**列入計畫的東西：語音試聽端點。phase 文件只寫了 `/api/media/img/`，
> 聯想圖分頁點開時能順便播該卡的語音是很自然的延伸，但那是文件沒要求的功能——
> 你要的話說一聲，我加；不說就不做。

---

## 6. Phase 5 結束時的交付

1. 六個 task 完成，非付費測試全綠、`ruff check` 無錯
2. `uv run anki-builder serve --port 8080` 可啟動，五個分頁可用
3. 驗收流程八步交你執行（含「CLI 與 UI 跑同一階段結果相同」這條關鍵驗證）
4. 驗收通過後：`logs/` 產出改動日誌、CLAUDE.md 進度表**全部 phase 標記完成**、
   回顧三項外部相依的狀態
