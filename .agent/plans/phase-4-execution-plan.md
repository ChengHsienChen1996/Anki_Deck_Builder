# Phase 4 執行計畫（待使用者確認）

日期：2026-08-24 ／ 分支：`dev_ai`
依據：[phase-4-audio.md](phase-4-audio.md)、[architecture.md](../../docs/architecture.md)、
[llm-integration.md](../../docs/llm-integration.md)、已安裝的 `voxcpm` 套件原始碼

---

## 0. 開工前檢查結果

| 檢查項 | 結果 |
|--------|------|
| 分支 | ✅ `dev_ai`，工作區乾淨（`7b91e87`） |
| `.claude/settings.local.json` 備份 | ⚠️ 專案內仍無 `.claude/` 目錄（同 Phase 1／2／3），**無檔案可備份** |
| `voxcpm` 相依 | ✅ 已在 `pyproject.toml` 與 `uv.lock`；`soundfile 0.14.0` 隨之安裝 |
| 模型權重 | ✅ `/home/jason/disk2/voxcpm2/`：`model.safetensors` 4.58 GB、`audiovae.pth` 377 MB |
| 參考音檔 | ✅ 使用者已提供兩支（見 §1.2） |
| Phase 3 交付 | ✅ 驗收通過，`ocr` → `extract` → `image` → `pack` 可跑 |

### 套件 API 實查（不依印象）

直接讀 `voxcpm.core.VoxCPM._generate` 的簽章，與 phase-4-audio.md〈前置條件〉**完全相符**：

```python
_generate(text, prompt_wav_path=None, prompt_text=None, reference_wav_path=None,
          cfg_value=2.0, inference_timesteps=10, min_len=2, max_len=4096,
          normalize=False, denoise=False, retry_badcase=True,
          retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0)
```

確實**沒有** `language`／`speed`／`speaker_id`。取樣率取自 `model.tts_model.sample_rate`，
寫檔方式與套件自己的 CLI 一致（`sf.write(path, audio_array, model.tts_model.sample_rate)`）。

---

## 1. 兩件開工前要處理的事

### 1.1 你的 `.env` 還是舊的 HTTP 服務版本（阻塞級）

`.env.example` 在 2026-08-22 已改為套件版，但**你本機的 `.env` 沒有跟著改**：

```bash
# .env 第 51–58 行，現況
VOXCPM2_BASE_URL=http://127.0.0.1:9880     # ← 已不存在的概念
VOXCPM2_SPEAKER_ID=                        # ← 該套件沒有 speaker id
VOXCPM2_LANGUAGE=ja                        # ← 沒有這個參數
VOXCPM2_SPEED=1.0                          # ← 沒有這個參數
VOXCPM2_TIMEOUT=120                        # ← 不是 HTTP 呼叫，沒有連線逾時
VOXCPM2_CONCURRENCY=2                      # ← 會被真的讀進去，與設計相左
```

前五個因為 `extra="ignore"` 會被靜默忽略，**但第六個是真欄位、會生效**——
本行程內的 GPU 推論本就序列化，設 2 只會讓兩份活動記憶體同時佔 VRAM。

更關鍵的是**缺了兩個必要變數**：`VOXCPM2_MODEL_PATH` 沒設 → `model_path` 為 `None`，
階段一開始就會擋下；`VOXCPM2_REFERENCE_WAV` 沒設 → 每張卡都是隨機音色。

**計畫**：Task 4.1 開工時把 `.env` 的 VOXCPM2 區塊整段換成 `.env.example` 的版本，
並填入模型路徑與你選定的參考音檔。舊的五個變數直接刪除。

### 1.2 參考音檔：兩支都符合建議長度，要你選一支

| 檔案 | 長度 | 格式 |
|------|------|------|
| `OuroKronii_02_28.wav` | 13.4s | 22050 Hz、單聲道、PCM 16-bit |
| `ShirokamiFubuki_16_0.wav` | 12.4s | 同上 |

都落在文件建議的 5–15 秒內，格式也乾淨。**音色是主觀選擇，我不代你決定**——
驗收第 3 步會用同一句話各生一次讓你比對，屆時再定 `VOXCPM2_REFERENCE_WAV`。
在那之前先用 `ShirokamiFubuki_16_0.wav` 跑通流程。

另有一個位置問題：它們現在在 `tests/fixtures/voice_cloning_targets/`，屬**測試資料**，
但正式設定要指向其中一支——測試資料兼作正式素材會讓兩者的生命週期綁在一起。
建議另開 `assets/voice/` 放正式用的那支，`tests/fixtures/` 那份留給測試。
你若覺得多此一舉，直接指向 fixtures 也能動。

---

## 2. 與 phase-4-audio.md 不同的三處（都是文件與程式碼對不上）

### 2.1 `@register_stage("audio")` 會直接拋 `KeyError`（Task 4.2 要求 1）

`state/selector.py` 的 `STAGE_FIELDS` 只有五個鍵：
`ocr`、`extract`、`image`、**`audio_front`**、**`audio_back`**。
`register_stage()` 對不在表內的名稱一律拋 `KeyError`，所以 `"audio"` 註冊不起來。

**這不是障礙，反而是解法。** 狀態層從 Phase 1 就把 audio 設計成**兩個獨立階段**
（`selector.py` 模組 docstring 明寫「`audio` 拆成 `audio_front` 與 `audio_back`
兩個獨立階段，字串組合會漏掉這個特例」）。因此：

```python
class _AudioStage(BaseStage):      # 共用邏輯，不註冊
    side: ClassVar[str]

@register_stage("audio_front")
class AudioFrontStage(_AudioStage):
    side = "front"

@register_stage("audio_back")
class AudioBackStage(_AudioStage):
    side = "back"
```

**`BaseStage` 一個字都不用改**，`select_pending`／`mark_done`／`mark_failed` 全部照常運作，
`--side front` 的隔離性由「根本沒有執行另一個階段」保證，而不是靠邏輯避開。
phase-4-audio.md 擔心的「一列兩狀態的骨架限制」在這個結構下不存在。

> 代價：`get_stage("audio")` 查不到東西。CLI 的 `audio` 子命令依 `--side` 決定
> 要跑哪一個或兩個階段，這一層對應寫在 `cli.py`（介面層的接線，本來就是它的職責）。

### 2.2 Task 4.4 大部分已經完成

`stages/pack.py` 現況：

- `MEDIA_FIELDS` 已含 `audio_front`、`audio_back`，`_validate()` 已檢查四個媒體欄位的檔案存在
- `MEDIA_DIRS` 已含 `media/audio/`，`_write_zip()` 已建立該目錄
- 錯誤訊息已含欄位名（`ja_001：audio_back 指向的檔案不存在（…）`），圖音本來就分得開

**真正還缺的只有一項**：零位元組檔目前 `.is_file()` 會通過。Task 4.4 因此縮成
「補零位元組檢查 + 補測試」，不是原本規劃的那一整塊。

### 2.3 `TTSClientProtocol.synthesize()` 的回傳型別

Protocol 宣告 `-> bytes`，而套件回傳 `np.ndarray`。維持 Protocol 不變、由 client
以 `soundfile` 寫成 WAV bytes（`sf.write(io.BytesIO(), audio, sr, format="WAV")`），
與 `ImageGenClientProtocol.generate() -> bytes` 對稱，階段層兩邊寫檔的邏輯一致。
**不需要調整 Protocol。**

---

## 3. 任務執行順序

一次一個 task，完成即停下交付。

### Task 4.1 — VOXCPM2 client

**產出**：`clients/tts_client.py`、`tests/clients/test_tts_client.py`、`.env`（§1.1 的整段替換）

- `VoxCPMClient(settings.tts)`，實作 `TTSClientProtocol`
- **模型只建構一次**：`_model` 快取於實例，4.96 GB 的權重重建等於災難
- `validate()`（比照 Phase 3 的 `ComfyUIClient`）：`VOXCPM2_MODEL_PATH` 與
  參考音檔存在與否，缺少時拋 `ConfigurationError`。**在載入模型之前**做
- `synthesize()` 以 `asyncio.to_thread` 包住 `generate()`——同步且長時間阻塞的 GPU 呼叫
- 音色與生成參數全部自 `settings.tts` 取得，不進簽章（約束 5）
- 套件例外轉 `ExternalServiceError`。**不自行重試**，`retry_badcase` 已內建
- 未設任何音色來源時，首次呼叫記一則 warning（「整套牌組音色不一致」）

**開工第一件事**：以 CLI 手動生成一句日語，記錄首次載入耗時、單句耗時、VRAM 佔用與
實際取樣率。`optimize`（`torch.compile`）的首次成本要在這裡量到。

**測試**：mock 掉 `VoxCPM` 的建構與 `generate`，**不真正載入模型**
（4.96 GB、佔 VRAM、與 ComfyUI 搶資源——llm-integration.md 的要求）。涵蓋正常生成、
空文字、模型路徑不存在、參考音檔不存在、套件拋錯的轉換、**模型只建構一次**。

### Task 4.2 — 階段 ④：audio

**產出**：`stages/audio.py`、`tests/stages/test_audio.py`

- 依 §2.1 的結構：共用基底 + 兩個註冊子類
- 單列處理：讀 `tts_front_text`／`tts_back_text` → 合成 → 寫
  `media/audio/{card_id}_{side}.wav` → 回填 `audio_front`／`audio_back` **相對路徑**
- `card_id` 為空的來源列直接跳過（同 `image.py` 的防護）
- 文字為空 → 拋例外，骨架記為該側的 `failed`
- 媒體根目錄取「中間 CSV 所在目錄」，與 `pack` 的 `media_root` 一致（同 `image.py`）
- 併發取 `VOXCPM2_CONCURRENCY`；`checkpoint_every = 1`
- 沿用 `progress.py`，label 為「生成語音（單字）」／「生成語音（例句）」

**測試**：**必須涵蓋**「`--side front` 執行後 `audio_back_status` 保持原值不變」。
另涵蓋狀態流轉、跳過來源列、缺文字、檔名與相對路徑、TTS 呼叫以假 client 替換。

### Task 4.3 — CLI 接線

**產出**：`cli.py`（修改）、`tests/test_cli.py`（補充）

- `audio` 子命令加 `--side front|back|both`（預設 `both`），依 §2.1 dispatch 到一或兩個階段
- `run-all` 於 `# ④ audio` 註解處接入，順序 ocr → extract → image → **audio** → pack
- **不併行**：兩個階段依序 `await`，image 與 audio 之間也不重疊
- VRAM 讓渡沿用 Phase 3 的機制，不另設一套（見 §4）

### Task 4.4 — pack 零位元組檢查

**產出**：`stages/pack.py`（修改）、`tests/stages/test_pack.py`（補充）

- `_validate()` 對四個媒體欄位補上「檔案大小為 0 視為失敗」
- 測試涵蓋缺檔、零位元組、圖音混合缺失

---

## 4. VRAM：預估綽綽有餘，但要實測

Phase 3 已量到的數字，加上本階段的預估：

| 佔用者 | 實測／預估 |
|--------|-----------|
| VOXCPM2 權重 | 約 4.96 GB（推論期另需活動記憶體） |
| ComfyUI 常駐 | 約 2.3 GB（Phase 3 實測） |
| E4B 抽取模型 | 約 8.6 GB（Phase 3 實測，11.7 GB 峰值扣掉 ComfyUI 與桌面） |
| 桌面 | 約 1 GB |
| **三者全載的最壞情況** | **約 17 GB / 24 GB** |

即使 `run-all` 跑完 extract、image 後三個模型都還在，仍有約 7 GB 餘裕。
**因此本階段預設不新增任何讓渡機制**——`MODEL_UNLOAD_BEFORE_STAGE` 對 audio 也適用
（audio 不需要任何 Ollama 模型，`ensure_room(keep="")` 的呼叫方式與 image 相同）。
驗收第 5 步實測後若發現不足，再沿用 Phase 3 的機制討論，不另設一套。

> 這與 Phase 3 的結論一致：VRAM 約束是**模型大小的函數**。若你切回 31B（19.87 GB），
> 19.87 + 4.96 就已經爆掉，屆時 audio 前必須卸載 Ollama。

---

## 5. 風險與因應

| 風險 | 因應 |
|------|------|
| ~~VOXCPM2 介面認知不準~~ | ✅ 已讀套件原始碼確認（§0） |
| ~~相依被 uv sync 清掉~~ | ✅ 已在 `pyproject.toml` 與 `uv.lock` |
| 模型重複載入 | client 快取實例，測試明確涵蓋「只建構一次」 |
| ~~一列兩狀態的骨架限制~~ | ✅ §2.1 的兩階段結構讓它不存在，`base.py` 不動 |
| `optimize=True` 的首次成本 | `torch.compile` 會拉長首次呼叫。Task 4.1 開工實測，若首次過久則討論改預設 |
| `normalize` 對日語的影響 | 預設關閉。驗收第 3 步聽發音，異常再開啟比對 |
| 例句超過 `max_len=4096` | 例句遠低於此，但 Task 4.1 會記錄實際 token 數量級。**真的遇到先報告再決定策略**，不自行截斷 |
| 參考音檔取樣率不符 | 兩支都是 22050 Hz，模型內部會重採樣（`librosa.load(sr=...)`）。驗收聽音色是否失真 |
| 音檔格式 | `.wav`（已定案）。**不引入 ffmpeg／pydub**；要 mp3 先問 |

### 不要做（取自 phase-4-audio.md）

- 不依印象假設 VOXCPM2 的 API——`language`／`speed`／`speaker_id` 都不存在
- 不自行加重試（`retry_badcase` 已內建）
- 不修改 `stages/base.py`
- 不另寫進度顯示
- 不讓 image 與 audio 併行
- 不在未經同意下引入 ffmpeg、pydub
- 不引入 Gradio、FastAPI

---

## 6. Phase 4 結束時的交付

1. 四個 task 完成，非付費測試全綠、`ruff check` 無錯
2. 測試**不真正載入模型**，`VoxCPM` 的建構與 `generate` 全部 mock
3. 驗收流程八步交使用者執行（含兩支參考音檔的音色比對）
4. 驗收通過後：`logs/` 產出改動日誌（含 VRAM、首次載入與單句耗時、實際取樣率），
   更新 CLAUDE.md 進度表與外部介面狀態（VOXCPM2 ✅）
5. **此時 CLI 全流程完整可用**，Phase 5 僅為介面層加值
