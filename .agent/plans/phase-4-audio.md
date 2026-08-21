# Phase 4 — VOXCPM2 語音生成

## 目標

接上 VOXCPM2，為每張卡生成兩個獨立音檔——`audio_front` 唸單字本身、`audio_back` 唸例句。本階段完成後，pipeline 五個階段全部打通，`run-all` 能從書本圖片產出含圖含音的完整牌組 ZIP。

**核心要求**：兩個音檔的狀態獨立，任一失敗不影響另一個，可各自重跑。

> **開工前**：依 [CLAUDE.md](../CLAUDE.md)〈開工前必做〉完成三項準備（確認 `dev_ai` 分支、備份 `.claude/settings.local.json`、確認外部介面狀態），再將本階段的執行計畫寫入 `.agent/plans/`，交使用者確認後才動手。

---

## 完成標準

- [ ] **`voxcpm` 已寫進 `pyproject.toml`**（目前只裝在 venv，`uv sync` 會連同 torch 一起清掉）
- [ ] `audio_front` 與 `audio_back` 各自生成獨立音檔，狀態獨立追蹤
- [ ] `--side front|back|both` 參數可控制只處理單邊，不觸碰另一邊的狀態
- [ ] 音檔存至 `media/audio/{card_id}_front.wav` 與 `{card_id}_back.wav`，路徑正確回填
- [ ] 單檔失敗記錄於對應 `*_error` 並跳過，另一邊不受影響
- [ ] 沿用 Phase 3 的 `progress.py` 顯示進度，未另寫
- [ ] `run-all` 已串入 audio 階段，順序在 image 之後
- [ ] `pack` 的完整性驗證涵蓋音檔路徑
- [ ] pytest 全數通過
- [ ] 產出的 ZIP 載入記憶引擎後，卡片正反面音訊皆可播放

---

## 前置條件（已於 2026-08-22 實查確認）

VOXCPM2 **不是 HTTP 服務**，是安裝在本機的 Python 套件，推論跑在本專案的行程內。
先前文件把它寫成本地服務、並預留了 endpoint／逾時／語者／語言／語速等變數，**全部有誤**，
已於 architecture.md、project-overview.md、`.env.example`、`config.py`、`protocols.py` 一併更正。

| 項目 | 實際情況 |
|------|----------|
| 呼叫方式 | Python 套件 `voxcpm`，`from voxcpm import VoxCPM` |
| 模型位置 | `/home/jason/disk2/voxcpm2/`（`config.json`、`model.safetensors` 4.58 GB、`audiovae.pth` 377 MB） |
| 建構 | `VoxCPM(voxcpm_model_path=..., enable_denoiser=..., optimize=..., device=...)` |
| 生成 | `generate(text, prompt_wav_path=None, prompt_text=None, reference_wav_path=None, cfg_value=2.0, inference_timesteps=10, normalize=False, denoise=False, retry_badcase=True) -> np.ndarray` |
| 輸出 | float32 一維波形陣列（**不是** bytes、**不是** mp3）；取樣率取自 `model.tts_model.sample_rate` |
| 寫檔 | `soundfile.write(path, audio, sample_rate)`——`soundfile` 已是 voxcpm 的相依，寫 WAV 零新增套件 |
| 語言指定 | **無此參數**。多語模型，語言由文字本身決定 |
| 語速控制 | **無此參數** |
| 語者指定 | **無 speaker id**。音色由參考音檔決定，未提供則每次隨機 |
| 併發 | 本行程內的 GPU 推論，序列化執行。`VOXCPM2_CONCURRENCY=1` |
| GPU 佔用 | 權重約 4.96 GB，推論期另需活動記憶體。**與 ComfyUI 同行程競爭 VRAM**，必須錯開 |

### 音色：兩條獨立路徑

| 設定 | 對應參數 | 說明 |
|------|----------|------|
| `VOXCPM2_REFERENCE_WAV` | `reference_wav_path` | voice cloning，以 ref_audio token 隔離。**不需要逐字稿**，單獨可用 |
| `VOXCPM2_PROMPT_WAV` + `VOXCPM2_PROMPT_TEXT` | `prompt_wav_path` + `prompt_text` | continuation 模式，**必須成對**，缺一方套件直接拋 `ValueError` |

兩者可併用。皆留空時為隨機音色——**整套牌組每張卡的聲音都不一樣**，
所以實務上 `VOXCPM2_REFERENCE_WAV` 應視為必填。成對性已由 `config.py` 在載入時驗證；
檔案存不存在屬階段性驗證，留給本階段檢查。

### 已定案

- **音檔格式為 `.wav`**（2026-08-22 確認）：記憶引擎不限制格式，判準是瀏覽器播不播得動。
  `soundfile` 是 voxcpm 既有相依，直接寫 WAV 零新增套件；改 mp3 只是為了省容量而多一個編碼器。

### 仍待確認

| 項目 | 說明 |
|------|------|

| **參考音檔來源** | 使用者需自備一段乾淨的日語女聲錄音（建議 5–15 秒）。長度與品質對 cloning 效果的影響待實測 |
| `normalize` | 文字正規化預設關閉。日語文本是否需要開啟待實測 |
| `optimize` | `torch.compile` 會拉長首次呼叫時間，實測後決定預設值 |
| 單次長度上限 | `max_len` 預設 4096 token。例句應遠低於此，但仍需確認超長時的行為 |
| `retry_badcase` | 套件內建重試（預設開、最多 3 次）。**本專案不要再包一層重試** |

> **相依尚未宣告**：`voxcpm` 目前已裝在 `.venv`，但**沒有寫進 `pyproject.toml`**——
> 執行 `uv sync` 會把它連同 torch 一起移除。本 phase 的 Task 4.1 必須補上宣告。

---

## 子任務拆分

### Task 4.1 — VOXCPM2 client

**產出**
```
src/anki_deck_builder/clients/tts_client.py
tests/clients/test_tts_client.py
```

**要求**
1. 於 `pyproject.toml` 補上 `voxcpm` 相依（見〈前置條件〉的警告）
2. 實作 Phase 1 已定義的 `TTSClientProtocol`：`synthesize(text) -> bytes`
3. 以 `.env` 的 `VOXCPM2_*` 系列變數為輸入，音色與生成參數**不進簽章**（約束 5）
4. **模型只載入一次後重用**——4.96 GB 的權重，每次呼叫重建等於災難
5. 載入前做階段性驗證：`VOXCPM2_MODEL_PATH` 與參考音檔存在，缺少時拋 `ConfigurationError`
6. `generate()` 是同步且長時間阻塞的 GPU 呼叫，須以 `asyncio.to_thread` 包起來，
   不可直接在事件迴圈上跑
7. 波形以 `soundfile` 寫成音檔 bytes（見〈仍待確認〉的格式決定）
8. 套件例外轉為 `ExternalServiceError`。**不要自行加重試**——`retry_badcase` 已內建
9. 未指定任何音色來源時，於首次呼叫記一則 warning 提醒「整套牌組音色不一致」

> **開工前**：先以 CLI 手動跑一次確認實際行為與耗時：
> `uv run python -m voxcpm.cli --help`，或直接建 `VoxCPM(...)` 生成一句話。

**測試**：mock 掉 `VoxCPM` 的建構與 `generate`，撰寫**可執行**單元測試，執行至通過。
涵蓋正常生成、空文字、模型路徑不存在、參考音檔不存在、套件拋錯的轉換、模型只建構一次。
依 llm-integration.md「本地模型一律 mock 掉模型載入與推理呼叫」，
**測試不得真正載入模型**（4.96 GB、佔 VRAM、與 ComfyUI 搶資源）。

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
   | front | `tts_front_text` | `{card_id}_front.wav` | `audio_front` | `audio_front_status` |
   | back | `tts_back_text` | `{card_id}_back.wav` | `audio_back` | `audio_back_status` |

3. `--side` 參數控制處理範圍：
   - `front`：只處理 front，**完全不觸碰** `audio_back_status`
   - `back`：只處理 back
   - `both`（預設）：兩者皆處理
4. 文字來源為空時，該子項標為 `failed` 並記錄「缺少 tts_*_text」
5. 存檔至 `$WORK_DIR/media/audio/`，回填**相對路徑** `media/audio/{card_id}_front.wav`
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
   確認 `audio_front` 為 `done`、`audio_back` 仍為 `pending`，且 `media/audio/` 中只有 `_front.wav`。

2. **補齊另一邊**
   ```bash
   uv run anki-builder audio --work work/cards.csv --side back
   ```
   確認 `audio_back` 完成，且 front 的檔案未被重新生成（比對修改時間）。

3. **音檔內容檢查**
   人工播放數個音檔，確認：
   - 音色與 `VOXCPM2_REFERENCE_WAV` 的參考音檔一致，且**每張卡都是同一個聲音**
     （未設參考音檔時每張卡音色都不同，可據此確認設定確實生效）
   - `_front.wav` 唸的是單字本身，`_back.wav` 唸的是例句
   - 發音正確、語速合理、無截斷

4. **失敗獨立性**
   手動將某列的 `tts_back_text` 清空後執行：
   ```bash
   uv run anki-builder audio --work work/cards.csv
   ```
   確認該列 `audio_back_status` 為 `failed`、`audio_front_status` 為 `done`，兩者互不影響。

5. **VRAM 檢查與階段間讓渡**
   執行 audio 階段時以 `nvidia-smi` 觀察 VRAM 佔用，回報實際數值，並確認：
   - 與 Phase 3 實測的 ComfyUI 佔用相加是否仍在 24 GB 內（預估綽綽有餘，需證實）
   - image 跑完後 ComfyUI 是否仍持有 VRAM，audio 是否因此受影響
   - 若需要讓渡，沿用 Phase 3 決定的機制，**不要另設一套**
   詳見 [project-overview.md](../../docs/project-overview.md)〈階段間的 VRAM 讓渡〉。

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
| ~~VOXCPM2 介面認知不準~~ | 已於 2026-08-22 實查解除，見〈前置條件〉。**但仍不要依印象補參數**——`language`、`speed`、`speaker_id` 這類參數在該套件中並不存在 |
| 相依未宣告被 uv sync 清掉 | `voxcpm` 目前只裝在 venv、不在 `pyproject.toml`。Task 4.1 第一件事就是補上 |
| 模型重複載入 | 4.96 GB 權重，每次呼叫重建會讓整批慢到無法使用。client 必須快取實例 |
| 一列兩狀態的骨架限制 | `BaseStage` 原設計為一列一狀態。優先以子類覆寫處理，不改骨架 |
| 音檔格式 | 實際輸出是 float32 波形陣列，不是任何檔案格式。暫定以 soundfile 寫 WAV（零新增相依）；要 mp3 需引入編碼器，**引入前先問使用者** |
| GPU 資源衝突 | VOXCPM2 在**本行程內**佔 VRAM，沒有「關掉服務」這個選項。與 ComfyUI 必須嚴格錯開；必要時討論用完主動釋放模型 |
| 例句過長被截斷 | 部分 TTS 有單次輸入長度上限。若例句超長，需分段合成或截斷，**發現問題先報告再決定策略** |

### 不要做

- **不要**依既有印象假設 VOXCPM2 的 API 用法——規格見〈前置條件〉，超出的部分先確認
- **不要**自行加重試：`retry_badcase` 已由套件內建
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
2. 於日誌記錄 VOXCPM2 的 VRAM 實測數據、單句生成耗時與實際音檔格式
3. 更新 [CLAUDE.md](../CLAUDE.md) 的進度追蹤表與外部介面狀態（VOXCPM2 ✅）
4. **此時 CLI 全流程已完整可用**，Phase 5 僅為介面層加值

> `logs/` 與 `CLAUDE.md` 皆屬 Zone 2（本地版控、不進 `main`），照常在 `dev_ai` commit 即可，不需另做處理。合併回 `main` 由人工執行 `scripts/merge-to-main.sh dev_ai`，AI 不主動 merge。
