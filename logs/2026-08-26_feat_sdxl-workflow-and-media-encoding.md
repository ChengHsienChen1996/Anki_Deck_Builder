# 改動總結 — 改用 SDXL workflow 與牌組媒體轉檔

日期：2026-08-26
分支：dev_ai
相關 commit：`1cb59bb`（SDXL workflow）、`f427684`（WebP／MP3 轉檔）、
`82f39e4`（convert-media 路徑修正）

> 與同日的 [Phase 5 日誌](2026-08-26_feat_phase-5-webui.md) 是兩件獨立作業：
> 這一份是使用者提供新 workflow 後的替換與連帶的體積問題。

---

## 變更清單

### 新增

1. `workflows/card_image_xl.json`：以使用者提供的 `Standard_V37.json`
   （fabricatedXL／SDXL ＋ FaceDetailer）為底修補而成，見〈為什麼要修補〉
2. `workflows/Standard_V37.json`：使用者的原始匯出，一併納入版控——
   留著才知道 `card_image_xl.json` 改了什麼
3. `src/anki_deck_builder/stages/media_encode.py`：`encode_image()`／`encode_audio()`。
   寫檔前轉格式，`png`／`wav` 即原樣輸出
4. `scripts/convert-media.py`：補轉既有牌組並改寫 CSV 的四個路徑欄位
5. `engines/anki_engine.html`：使用者提供的記憶引擎，納入版控作為轉檔決策的依據
6. `tests/stages/test_media_encode.py`：10 項

### 修改

7. `src/anki_deck_builder/config.py`：新增 `MediaSettings`（`MEDIA_` 前綴）；
   ComfyUI 預設值改為 XL workflow、1344×768、動漫 SDXL 的負向詞
8. `src/anki_deck_builder/stages/image.py`／`audio.py`：寫檔前呼叫轉檔，
   副檔名由設定決定；`stable_seed()` 上限 2^64 → 2^31
9. `prompts/image_prompt_template.md`：版面尺寸更新；負向 prompt 段落改寫；
   **新增〈實測否決的調整〉**——四種風格後綴的嘗試全部更差，記下來免得再走一次
10. `.env`／`.env.example`：ComfyUI 區塊整段換掉，新增 `MEDIA_*` 四項
11. `pyproject.toml`：`pillow>=10`（本來就被 gradio 帶進來，現在明著寫）
12. `docs/usage.md`、`CLAUDE.md`：兩份 workflow 的對照表、媒體格式段落、
    已知限制改寫
13. `tests/`：`test_cli.py`、`test_image.py`、`test_audio.py` 的假媒體位元組改為
    **真的能被解開的 PNG／WAV**——轉檔上線後假資料等於在測轉檔會不會失敗

## 測試結果

- pytest：**631 passed / 0 failed**
- ruff check：無錯

## 為什麼要修補原始 workflow

原檔直接送進 `POST /prompt` 會失敗，四處必改：

| 節點 | 問題 | 處置 |
|------|------|------|
| `49` WidgetToString | 靠**前端送的 `extra_pnginfo`** 讀節點 widget，API 呼叫拿不到 → `TypeError 'NoneType' object is not subscriptable` | 刪除，並把它餵的 `54.modelname` 改成字面值 |
| `54` Image Saver／`64` Image Comparer | 每張額外寫一份 1.3 MB PNG 進 ComfyUI 的 output 目錄 | 刪除；另加專用的 `PreviewImage`(100) 當輸出節點 |
| `1` Width／`12` Height | 尺寸來自**兩個不同節點**，而注入點只支援單一 latent 節點的兩個欄位 | `EmptyLatentImage`(37) 改吃字面值，`.env` 才控制得到 |
| `27` FaceDetailer | 依偵測到的臉數做額外細修，耗時不可預測 | 輸出改接 `VAEDecode`(39) 預設跳過；要開就把 `100.images` 改回 `["75", 0]` |

## 實測數據

### 生成速度（熱機，RTX 3090）

| workflow | 每張 | 308 張推估 |
|----------|------|-----------|
| SD 1.5（`card_image.json`） | 1.6s | 約 13 分鐘 |
| SDXL 跳過 FaceDetailer（現行） | **8.2s** | **約 42 分鐘**（實跑 41:39） |
| SDXL 含 FaceDetailer | 8～69s，平均約 32s | 約 2.7 小時 |

FaceDetailer 的成本完全取決於畫面裡有幾張臉：同一批 3 張卡，跳過是 25 秒、
打開是 95 秒，其中一張獨佔 69 秒。

### 體積

| 項目 | 之前 | 之後 |
|------|------|------|
| 聯想圖 | 127 MB（PNG／768×432） | **25 MB**（WebP q92／1344×768） |
| 語音 | 124 MB（WAV 48 kHz） | **15 MB**（MP3） |
| `output/deck.zip` | 221 MB | **35.7 MB** |
| 引擎載入 | — | 1.2 秒、JS heap 83 MB |

轉檔用**既有相依**：Pillow 寫 WebP、soundfile（libsndfile 1.2.2）**直接寫 MP3**，
因此 Phase 4「不引入 ffmpeg／pydub」的約束沒有被打破。

體積為什麼重要：`engines/anki_engine.html` 用 JSZip 把 ZIP 內**每個媒體檔解成 Blob
再 `createObjectURL`**，整包媒體同時常駐記憶體。443 MB 的牌組在手機上很可能直接被
系統殺掉，35.7 MB 則毫無壓力。

## 風格 prompt：四種調整全部實測否決

換動漫模型後「看起來更對」的調整，實測都更差：

| 嘗試 | 結果 |
|------|------|
| 加 `masterpiece, best quality, absurdres` | 畫面被拉成**角色特寫**：「桌前完成任務的人」變少女大頭照，「老虎站在貓群中」變一排上班族 |
| 動漫標籤精簡版 | 同上，老虎變虎頭人 |
| 加 `wide establishing shot, full scene` | **主體整個消失**，只剩空教室、空走廊 |
| 負向加 `close-up portrait, headshot` | 同上 |

推測 `masterpiece / absurdres` 在動漫模型的訓練資料裡與單人立繪高度相關。
**結論：風格後綴一字未改**，`extract_cards.md` 與黃金樣本 CSV 都不必動；
真正該換的是負向 prompt 與解析度。

## 備註

- **多元素構圖仍未根治**：抽測 4 張，XL 贏 1、SD 1.5 贏 1、平手 2。
  換模型只能改善不能解決，要那張圖對就到 Web UI 改 prompt 重生
- **MP3 的實際播放未被自動化驗證**：自動化 Chrome 下連原本的 WAV 也卡在
  `readyState=0`（環境問題）。檔案本身可被 soundfile 正確解回 48 kHz，
  `canPlayType('audio/mpeg')` 回 `probably`；仍需人工點一次播放鍵確認
- 舊的 308 張 PNG 與 616 個 WAV 已在轉檔後刪除；
  `work/cards.csv.bak-2026-08-26` 保留重生前的狀態
