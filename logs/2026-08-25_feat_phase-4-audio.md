# 改動總結 — Phase 4 VOXCPM2 語音生成

日期：2026-08-25
分支：dev_ai
相關 commit：`52d967e`（執行計畫）、`7697b24`（Task 4.1）、`5c9acfe`（4.2）、`2465cc2`（4.3）、
`f507354`（4.4）、`69fc79f`（測試隔離）、`43800fa`（驗收清單）、`36a63fd`／`9045566`／`2c483f8`／
`2dfcc80`（驗收前的四項修正）、`cfe7b9c`（響度統一）、`d69a566`（prompt 規則）

---

## 變更清單

### 新增程式

1. `src/anki_deck_builder/clients/tts_client.py`：`TTSClientProtocol` 的實作 `VoxCPMClient`。
   `voxcpm` 是**本機套件**不是 HTTP 服務，因此沒有 endpoint／逾時／`language`／`speed`／
   `speaker_id`。模型以 `asyncio.Lock` 保護、只建構一次；生成一律走 `asyncio.to_thread`；
   不自行重試（套件內建 `retry_badcase`）。三種音色來源可並存：Voice Design（文字描述）、
   voice cloning（`reference_wav`）、continuation（`prompt_wav` + `prompt_text`）。
   另含 `normalize_loudness()`／`_measure()`：寫檔前統一響度
2. `src/anki_deck_builder/stages/audio.py`：階段 ④。共用基底 `_AudioStage` 加兩個註冊子類
   `AudioFrontStage`／`AudioBackStage`——狀態層從 Phase 1 就把 audio 拆成 `audio_front`
   與 `audio_back` 兩個獨立階段，`register_stage("audio")` 本來就會拋 `KeyError`。
   採此結構後 `stages/base.py` 一個字都不用改，`--side front` 的隔離性由「根本沒有執行
   另一個階段」保證
3. `scripts/normalize-audio.py`：把**正規化功能上線前**生成的音檔就地調成統一響度。
   重跑 `audio --force` 要十幾分鐘且會換掉已聽過確認的內容，這支腳本只改音量

### 修改程式

4. `src/anki_deck_builder/cli.py`：`audio` 子命令加 `--side front|back|both`（預設 `both`），
   依 `--side` dispatch 到一或兩個階段；`run-all` 串成 ocr → extract → image → audio → pack，
   image 與 audio **不併行**；VRAM 讓渡沿用 Phase 3 的機制，未另設一套
5. `src/anki_deck_builder/config.py`：`TTSSettings` 全面改寫為本機套件的參數；
   新增 `voice_description`、`speak_translation`、`loudness_normalize`／`loudness_target_dbfs`／
   `loudness_peak_dbfs`；`_blank_is_unset` 讓 `.env` 中「有這行但留空」等同未設定
   （否則 pydantic 會把空字串變成 `Path('.')`）；`_prompt_pair_must_be_complete` 驗證成對性
6. `src/anki_deck_builder/stages/pack.py`：`_validate()` 補零位元組媒體檢查。
   原本 `.is_file()` 會讓 0 位元組的壞檔過關，打包進 ZIP 後在記憶引擎裡是「按了沒反應」
7. `src/anki_deck_builder/stages/__init__.py`：匯出 `AudioFrontStage`、`AudioBackStage`
8. `prompts/extract_cards.md`：`tts_front_text` 規則改為分兩種情況——`reading` 是注音系統
   （假名、拼音）時填讀音，是**音標（IPA）時改填 `front` 本身**。原規則「有 reading 時
   填讀音」讓英語詞條的該欄位變成 `ˈæktɪv` 這類符號串
9. `.env.example`：VOXCPM2 區塊改為本機套件版本，補上三種音色來源、`SPEAK_TRANSLATION`
   與 `LOUDNESS_*`；定案音色為 Voice Design 描述

### 新增測試

10. `tests/clients/test_tts_client.py`：32 項。模型以假工廠注入，**完全不載入真實權重、
    也不匯入 voxcpm／torch**。涵蓋 `validate()` 的各種缺件、參數全部取自設定、只建構一次、
    併發首呼只建一次、套件例外轉 `ExternalServiceError`、音色來源組合，以及響度正規化
    8 項（拉高、壓低、峰值優先、停頓不影響量測、近靜音不放大、全靜音、可關閉、參數化）
11. `tests/stages/test_audio.py`：33 項。含指定要求的「`--side front` 執行後
    `audio_back_status` 保持原值不變」
12. `tests/stages/test_pack.py`、`tests/test_cli.py`、`tests/test_config.py`：補零位元組檢查、
    `--side` dispatch、`run-all` 順序、新設定欄位

### 資產與文件

13. `assets/voice/`：兩支參考音檔（目前未使用，走 Voice Design；cloning 接口保留）
14. `.agent/plans/phase-4-execution-plan.md`、`.agent/plans/phase-4-acceptance.md`

## 測試結果

- pytest：**549 passed / 0 failed**（31 deselected 為付費 API 標記）
- ruff check：無錯
- 需人工測試項目：無新增付費 API 呼叫。VOXCPM2 為本機 GPU 推論，測試一律 mock

## 實測數據（RTX 3090 24 GB）

| 項目 | 值 |
|------|-----|
| 模型建構 | 首次約 77s（含 `torch.compile` 暖機），之後約 28s |
| 取樣率 | **48000 Hz**（寫檔一律轉 PCM_16，float WAV 部分瀏覽器播不動） |
| 單字（1～3 字） | 約 0.7s／段 |
| 例句（10～49 字） | 約 2s／段 |
| 308 張卡（616 段） | 約 15 分鐘 |
| VRAM：開始前 | 522 MiB（ComfyUI 未啟動） |
| VRAM：模型載入後 | 5995 MiB |
| VRAM：合成期間峰值 | **7490 MiB** |

**不需要任何 VRAM 讓渡機制。** 峰值 7.5 GB／24 GB，即使 ComfyUI（2.3 GB）與 E4B
抽取模型（8.6 GB）都還在，三者合計約 18 GB 仍有餘裕，與執行計畫 §4 的預估一致。
切回 31B 抽取模型（19.87 GB）時才會需要在 audio 前卸載 Ollama。

## 驗收發現與處置

驗收過程查到五個問題，全部已修並重生成受影響的音檔：

| 問題 | 處置 |
|------|------|
| 116／308 張卡的 `tts_front_text` 為空（`reading` 也空的非日語詞條） | 階段讀取時依序退回 `reading`、`front`（`36a63fd`） |
| 177／308 張卡的 `tts_back_text` 混入中文譯文 | 一次性清理只留原文；另加 `VOXCPM2_SPEAK_TRANSLATION` 決定唸哪個欄位（`9045566`）。**是欄位切換不是字串切割**——啟發式猜譯文在日文牌組會整句刪光 |
| 音量時大時小（616 段閘門後 RMS 全距 83 dB、p10→p90 差 16 dB） | 寫檔前統一響度（`cfe7b9c`），詳下 |
| 23／308 張卡的 `tts_front_text` 是 IPA 音標（`ˈæktɪv`），模型唸不出來 | 改填 `front` 本身並重生成；prompt 補規則防止再犯（`d69a566`） |
| 短詞偶發生成幾乎無聲 | `inference_timesteps` 10 → 30 後由約 40% 降到 5／616（0.8%）；重跑即可 |

### 響度統一的做法

增益取兩者**較小值**：「拉到 `LOUDNESS_TARGET_DBFS` 所需倍率」與「不讓峰值超過
`LOUDNESS_PEAK_DBFS` 的倍率」。小聲的拉上來、大聲的壓下去，且任何一段都不削波。

- 響度用**閘門後的 RMS**（50 ms 分框，只算落在最大框 30 dB 內的框），不是峰值：
  峰值只反映最尖的一個取樣，一聲氣音就能讓整段被判定為「夠大聲」而聽感仍小聲。
  閘門也讓有停頓的例句不會因停頓被平均進去而顯得比單字小聲
- 放大以 `MAX_GAIN_DB = 20` 封頂並記 warning——生成失敗的近靜音音檔照算要放大 70 dB，
  放出來的是背景噪音；封頂後這些檔案反而成為「哪幾張要重跑」的偵測器

處理後全庫 616 檔：**rms 中位 -20.0 dBFS、最低 -26.8 dBFS（皆為峰值貼到上限所致）、
峰值最大 -1.0 dBFS**，無任何削波。

## 備註

- **CLI 全流程至此完整可用**：`ocr → extract → image → audio → pack`。
  實產 308 張卡的牌組：圖 127 MB + 音 126 MB，`output/deck.zip` 221 MB
- 已知限制（承 Phase 3）：部分聯想圖與 `image_prompt` 不符，SD 1.5 對多元素構圖的弱點
- Phase 5 僅為介面層加值，不改動 pipeline 行為
