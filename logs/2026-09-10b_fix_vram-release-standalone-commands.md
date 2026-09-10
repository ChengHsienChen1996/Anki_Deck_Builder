# 改動總結 — 開跑前的 GPU 讓渡接到每個單獨子命令

日期：2026-09-10
分支：dev_ai
相關 commit：
- `f6deac5` fix: 開跑前的 GPU 讓渡接到每個單獨子命令，不再只有 run-all
- `<本檔>` docs: 2026-09-10 第二輪改動總結

> 同日第一輪見 [2026-09-10_fix_ocr-chunk-rotation.md](2026-09-10_fix_ocr-chunk-rotation.md)。
> 本輪處理的正是那一輪〈備註〉列為「尚未修」的第一個缺口。

---

## 起因

重跑 28 頁時第一次跑 `ocr`，28 列**全部 CUDA OOM**。ComfyUI 從先前的 `image`
階段留下來抓著 17.97 GB／24 GB 不放，剩下的 6 GB 塞不下 GLM-OCR 加版面偵測器。

## 根因

`release_all_gpu()` 的 docstring 早就描述了這個失效模式（「ComfyUI 只要被用過就
抓著 17.4 GB 不放，第一階段一頭撞上去就是 CUDA OOM」），但它**只接在 `run-all`**
（`cli.py:526`）。

而**每個子命令都是獨立行程，在一個自己沒造成的 GPU 狀態上開跑**——
階段之間的讓渡（`release_comfyui()`、`free_vram_for*()`）假設「這個 pipeline 是
GPU 上唯一的東西」，那個假設在行程開跑時不成立。

`ocr` 更是唯一一個**連 `release_comfyui()` 都沒有**的子命令。
`release_comfyui()` 自己的 docstring 寫著「不在 OCR 前呼叫：OCR 模型只佔 2.2 GB，
沒有同樣的壓力」——那句話在 Phase 9 加了版面偵測器、Phase 7 換上常駐 17.4 GB 的
kyoani workflow 之後已不成立。

Web UI 有同一個缺口：`_prepare()` 只做各階段的讓渡，從不做開跑前的。
它是長駐行程，使用者按下按鈕時 GPU 上可能還留著上次生圖的 ComfyUI。

## 設計決策：讓渡要不要無視 `.env` 的兩個開關

`release_all_gpu()` 原本**刻意無視** `COMFYUI_FREE_BEFORE_LLM` 與
`MODEL_UNLOAD_BEFORE_STAGE`，理由是「整趟開跑前沒有這個取捨」。

接到單獨子命令之後這個理由不再成立：它**每次呼叫都會跑**，於是「開跑前」實質上
等同「每個階段前」，而那正好是那兩個開關管的事。無視它們等於讓
`COMFYUI_FREE_BEFORE_LLM=false` 與 `MODEL_UNLOAD_BEFORE_STAGE=false` 悄悄失效。

**使用者裁示：尊重開關。** 使用者設定的語意優先於「最不容易 OOM」。
代價寫進 docstring 與 CLAUDE.md：把開關關掉又用 kyoani 時單獨跑 `ocr` 仍會 OOM
——但該組態下 `COMFYUI_FREE_BEFORE_LLM` 本來就標示「必開」。

因此 `run-all` 傳 `respect_switches=False`（整趟一次，原語意不變），
單獨子命令與 Web UI 的單一階段傳 `True`。

## 變更清單

1. `src/anki_deck_builder/stages/vram.py`：`release_all_gpu()` 新增兩個參數。
   - `keep`：別清掉這個階段正要用的東西。新增模組常數 `COMFYUI`、`OLLAMA`
     （具名常數而非裸字串，免得打錯字卻安靜地什麼都沒 keep 住）。
     **刻意沒有 `VOXCPM`**：那一側唯一的動作 `release_gpu_cache()` 只把 torch 快取
     還給驅動，不會卸載行程內已載入的模型，連 audio 自己都不需要 keep 它。
   - `respect_switches`：見上一節。
   - `release_comfyui()` 的 docstring 補上「本函式現在只留在 `run-all` 的階段之間」。
2. `src/anki_deck_builder/cli.py`：新增 `GPU_COMMAND_KEEP` 對照表，讓渡移到
   `_dispatch()`（比照既有的 `AGENT_COMMANDS` 寫法——它與「要跑哪個階段」無關，
   處理的是「這個行程開跑前 GPU 上已經有什麼」）。移除 `_run_extract`／`_run_scene`／
   `_run_prompt`／`_run_audio` 中已被涵蓋的 `release_comfyui()`，以及 `_run_image`／
   `_run_audio` 的 `_free_vram_for_local_gpu()`。**`_run_all` 階段之間的呼叫保留**
   ——那是不同的事（ComfyUI 被 image 用過，audio 之前要讓）。
3. `src/anki_deck_builder/web/service.py`：`_prepare()` 最前面加同一個讓渡
   （`_STAGE_KEEP`，`respect_switches=True`），移除隨之失效的各階段呼叫與
   `_free_local_gpu()` 輔助函式。
4. `tests/test_cli.py`：新增三個測試——
   - `test_ocr_asks_comfyui_to_free_vram`：**這次缺口本身**
   - `test_image_does_not_free_the_comfyui_it_is_about_to_use`：`keep` 的反向保證
   - `test_llm_stages_keep_ollama_for_their_own_precise_unload`：LLM 階段不做
     `keep=""` 的粗暴卸載，避免每次呼叫白付一次模型重載

   更新兩個綁在舊訊息字串上的斷言（改成只認「有釋放」與「有 ComfyUI」——
   完整字串會因同輪測試有沒有別人載過 torch 而變動，原寫法在單跑時過、
   全套件跑時失敗）。
5. `tests/test_web_service.py`：更新兩個綁在舊 monkeypatch 目標上的測試，
   新增 `test_image_run_keeps_comfyui`。
6. `CLAUDE.md`：⚠️ 區塊那條「單獨跑子命令沒有 VRAM 讓渡」改寫為已修，
   並記下開關取捨與代價。

## 測試結果

- **pytest：919 passed / 0 failed**（本輪新增 4、修改 4），31 deselected
  （付費 API 骨架，標記 manual，需人工執行）。
- **ruff check：All checks passed**。
- **實地複驗**（真實 GPU、ComfyUI 常駐 18.6 GB）：

  | 指令 | 跑之前 | 跑之後 | 預期 |
  |---|---|---|---|
  | `anki-builder ocr` | 18593 MiB | **967 MiB** | 釋放 ✅ |
  | `anki-builder image` | 18583 MiB | 不變 | 保留 ✅（訊息：「開跑前檢查：GPU 上沒有需要釋放的東西」）|

## 備註

同日第一輪〈備註〉列的其餘待辦**未在本輪處理**：

- `contains_text()` 會讓偵測漏掉的整塊消失（p28 仍掉了 `形／型` 與 `刑事`）
- `audio_front` 429 列、`audio_back` 605 列仍為 pending
- p2、p16、p27 條目數偏低，值得用 `verify` 對照影像查
