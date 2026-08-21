# Phase 4 — VOXCPM2 語音生成

## 目標

接上 VOXCPM2，為每張卡生成兩個獨立音檔——`audio_front` 唸單字本身、`audio_back` 唸例句。本階段完成後，pipeline 五個階段全部打通，`run-all` 能從書本圖片產出含圖含音的完整牌組 ZIP。

**核心要求**：兩個音檔的狀態獨立，任一失敗不影響另一個，可各自重跑。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] `audio_front` 與 `audio_back` 各自生成獨立音檔，狀態獨立追蹤
- [ ] `--side front|back|both` 參數可控制只處理單邊，不觸碰另一邊的狀態
- [ ] 音檔存至 `media/audio/{card_id}_front.mp3` 與 `{card_id}_back.mp3`，路徑正確回填
- [ ] 單檔失敗記錄於對應 `*_error` 並跳過，另一邊不受影響
- [ ] 沿用 Phase 3 的 `progress.py` 顯示進度，未另寫
- [ ] `run-all` 已串入 audio 階段，順序在 image 之後
- [ ] `pack` 的完整性驗證涵蓋音檔路徑
- [ ] pytest 全數通過
- [ ] 產出的 ZIP 載入記憶引擎後，卡片正反面音訊皆可播放

---

## 前置條件

本 phase 需使用者提供 **VOXCPM2 的介面規格**：

| 項目 | 說明 |
|------|------|
| 呼叫方式 | HTTP API 或 Python 套件 |
| API 規格 | 若為 HTTP：endpoint 路徑、method、request / response 格式 |
| 女聲指定 | 參數名稱與可用值（speaker id／voice name／模型檔路徑） |
| 輸入格式 | 文字編碼、是否支援 SSML、單次長度上限 |
| 輸出格式 | 音檔格式（mp3／wav）、取樣率、是否可指定 |
| 語言指定 | 日語的語言代碼寫法 |
| 語速控制 | 參數名稱與數值範圍 |
| 併發限制 | 是否支援批次請求、建議併發上限 |
| GPU 佔用 | 是否為 GPU 推論、VRAM 需求 |

> **重要**：我對 VOXCPM2 的認知可能不準確或過時。**不要依既有印象實作**，一律以使用者提供的規格或實際查證結果為準。若前置條件未備齊，**停下來向使用者索取**。

---

## 子任務拆分

### Task 4.1 — VOXCPM2 client

**產出**
```
src/anki_deck_builder/clients/tts_client.py
tests/clients/test_tts_client.py
```

**要求**
1. 實作 Phase 1 已定義的 `TTSClientProtocol`
2. 以 `.env` 的 `VOXCPM2_*` 系列變數為輸入
3. 逾時控制：超過 `VOXCPM2_TIMEOUT` 拋 `ExternalServiceError`
4. 併發受 `VOXCPM2_CONCURRENCY` 控制
5. 外部錯誤轉為 `ExternalServiceError`，訊息含 endpoint
6. 回傳音檔 bytes，格式依 VOXCPM2 實際輸出；若非 mp3，於此層轉換或記錄實際格式供後續使用

> **開工前**：先確認使用者提供的規格，或以實際 VOXCPM2 服務手動打一次請求確認 request / response 結構。`VOXCPM2_SPEAKER_ID` 的實際型別確認後，回頭調整 `config.py` 的型別宣告與 `.env.example` 的說明。

**測試**：以 mock 回應撰寫**可執行**單元測試，執行至通過。涵蓋正常生成、逾時、HTTP 錯誤、空文字輸入。**測試不得依賴 VOXCPM2 實際啟動**。

---

### Task 4.2 — 階段 ④：audio

**產出**
```
src/anki_deck_builder/stages/audio.py
tests/stages/test_audio.py
```

**要求**
1. 繼承 `BaseStage`，註冊為 `"audio"`
2. **單列須處理兩個獨立子項**，各自有獨立狀態：

   | 子項 | 文字來源 | 輸出檔名 | 回填欄位 | 狀態欄位 |
   |------|----------|----------|----------|----------|
   | front | `tts_front_text` | `{card_id}_front.mp3` | `audio_front` | `audio_front_status` |
   | back | `tts_back_text` | `{card_id}_back.mp3` | `audio_back` | `audio_back_status` |

3. `--side` 參數控制處理範圍：
   - `front`：只處理 front，**完全不觸碰** `audio_back_status`
   - `back`：只處理 back
   - `both`（預設）：兩者皆處理
4. 文字來源為空時，該子項標為 `failed` 並記錄「缺少 tts_*_text」
5. 存檔至 `$WORK_DIR/media/audio/`，回填**相對路徑** `media/audio/{card_id}_front.mp3`
6. 沿用 Phase 3 的 `progress.py`，**不要另寫進度顯示**
7. 併發受 `VOXCPM2_CONCURRENCY` 控制

> **設計注意**：`BaseStage` 的預設骨架是「一列一個狀態」。本階段是首個「一列兩個獨立狀態」的案例。若骨架不支援，**優先以子類覆寫的方式處理，不要修改 `base.py`**。若確實無法在不改骨架的前提下完成，停下來與使用者討論。

**測試**：狀態流轉、`--side` 隔離性、檔名組合、相對路徑回填等純邏輯撰寫**可執行**測試；TTS 呼叫 mock。**必須涵蓋**：`--side front` 執行後 `audio_back_status` 保持原值不變。

---

### Task 4.3 — CLI 接線

**產出**
```
src/anki_deck_builder/cli.py（修改）
tests/test_cli.py（補充）
```

**要求**
1. 將 `audio` 子命令接上 Task 4.2 的實作，加入 `--side` 參數
2. 於 `run-all` 的預留位置接入 audio 階段，最終順序為：
   ```
   ocr → extract → image → audio → pack
   ```
3. **audio 必須排在 image 之後且不併行**——兩者皆可能佔用 GPU，併行會導致 VRAM 衝突

**測試**：完整單元測試，執行至通過。涵蓋 `--side` 參數解析與 `run-all` 的階段順序。

---

### Task 4.4 — pack 媒體驗證補強

**產出**
```
src/anki_deck_builder/stages/pack.py（修改）
tests/stages/test_pack.py（補充）
```

**要求**
1. 完整性驗證補上音檔檢查：
   - `audio_front` / `audio_back` 非空時，對應檔案須實際存在於 `work/media/audio/`
   - 檔案大小為 0 時視為失敗，列入錯誤清單
2. ZIP 打包時納入 `media/audio/` 目錄
3. 驗證失敗清單須區分「缺圖」與「缺音」，訊息明確指出是哪張卡的哪個檔案

**測試**：完整單元測試，執行至通過。涵蓋缺檔、零位元組檔、圖音混合缺失。

---

## 驗收流程

1. **單邊生成隔離性**
   準備已完成 extract 的 CSV：
   ```bash
   uv run anki-builder audio --work work/cards.csv --side front
   uv run anki-builder status --work work/cards.csv
   ```
   確認 `audio_front` 為 `done`、`audio_back` 仍為 `pending`，且 `media/audio/` 中只有 `_front.mp3`。

2. **補齊另一邊**
   ```bash
   uv run anki-builder audio --work work/cards.csv --side back
   ```
   確認 `audio_back` 完成，且 front 的檔案未被重新生成（比對修改時間）。

3. **音檔內容檢查**
   人工播放數個音檔，確認：
   - 為**女聲**
   - `_front.mp3` 唸的是單字本身，`_back.mp3` 唸的是例句
   - 發音正確、語速合理、無截斷

4. **失敗獨立性**
   手動將某列的 `tts_back_text` 清空後執行：
   ```bash
   uv run anki-builder audio --work work/cards.csv
   ```
   確認該列 `audio_back_status` 為 `failed`、`audio_front_status` 為 `done`，兩者互不影響。

5. **VRAM 檢查**
   執行 audio 階段時以 `nvidia-smi` 觀察 VRAM 佔用，確認與 Phase 3 的實測值相加不會超過 24GB（若兩者不慎併行）。回報實際數值。

6. **只重跑失敗**
   ```bash
   uv run anki-builder audio --work work/cards.csv --only-failed
   ```
   確認只處理 `failed` 的子項。

7. **pack 驗證**
   手動刪除 `work/media/audio/` 中一個檔案後執行 `pack`，確認驗證報錯並明確指出缺少哪張卡的哪個音檔。

8. **全流程端到端**
   ```bash
   uv run anki-builder run-all --input path/to/book.pdf --output output/deck.zip
   ```
   確認：
   - 階段順序為 ocr → extract → image → audio → pack
   - image 與 audio **未併行**
   - ZIP 內含 `cards.csv`、`media/img/`、`media/audio/`
   - 載入記憶引擎後，卡片正面顯示圖與單字音訊、背面顯示例句音訊，皆可播放

---

## 已知風險與注意

### 風險

| 風險 | 說明與因應 |
|------|-----------|
| VOXCPM2 介面認知不準 | **這是本 phase 最大風險**。不要依既有印象實作，一律以使用者規格或實測為準 |
| 一列兩狀態的骨架限制 | `BaseStage` 原設計為一列一狀態。優先以子類覆寫處理，不改骨架 |
| 音檔格式非 mp3 | 若 VOXCPM2 輸出 wav，需決定是在 client 層轉檔或直接改用 wav。轉檔會引入額外相依（ffmpeg／pydub），**引入前先問使用者** |
| GPU 資源衝突 | 若 VOXCPM2 為 GPU 推論且常駐佔用，可能與 ComfyUI 衝突。實測後回報，必要時討論服務啟停策略 |
| 例句過長被截斷 | 部分 TTS 有單次輸入長度上限。若例句超長，需分段合成或截斷，**發現問題先報告再決定策略** |

### 不要做

- **不要**依既有印象假設 VOXCPM2 的 API 用法，一律先確認
- **不要**修改 `stages/base.py` 的骨架來遷就一列兩狀態。優先用子類覆寫，真的不行就停下來討論
- **不要**另寫進度顯示——沿用 Phase 3 的 `progress.py`
- **不要**讓 image 與 audio 併行執行
- **不要**在未經使用者同意下引入 ffmpeg、pydub 等音訊處理相依
- **不要**修改 Phase 1–3 已完成的欄位定義或 client 介面。若需調整 `TTSClientProtocol` 簽章以符合實際規格，**這是允許的**（依架構約束 1：調整 Protocol 而非上層），但需在改動日誌說明
- **不要**引入 Gradio、FastAPI 等 Phase 5 才需要的相依

---

## 完成後

**暫停，等使用者完成驗收流程並確認**，才進入 Phase 5。

驗收通過後：
1. 依 [change-log-guide.md](change-log-guide.md) 於 `logs/` 產出改動日誌
2. 於日誌記錄 VOXCPM2 的 VRAM 實測數據與實際音檔格式
3. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表與外部介面狀態（VOXCPM2 ✅）
4. **此時 CLI 全流程已完整可用**，Phase 5 僅為介面層加值

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
