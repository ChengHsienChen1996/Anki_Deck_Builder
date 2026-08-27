# Phase 7 執行計畫 — 文生圖換成 kyoani（FLUX.2-klein-9B + KyoAni LoRA）

日期：2026-08-28
分支：dev_ai
依據：[logs/2026-08-27_eval_flux2-klein-9b-ab.md](../../logs/2026-08-27_eval_flux2-klein-9b-ab.md)
第三輪〈建議〉與第四輪〈結論〉
使用者已裁示：**步數用 12**

> 這是四輪 A/B 評估的落地，不是新功能。評估已定案的參數不再重議：
> `Anime. ` 前綴、1344×768、12 步、SageAttention `auto`、LoRA strength 1。

---

## 目標與非目標

**目標**：把批次生成從 `card_image_xl.json`（fabricatedXL／SDXL）換成
`card_image_kyoani.json`（FLUX.2-klein-9B + KyoAni Style LoRA），並重生全部 308 張圖。

**非目標**：

- 不動 `prompts/` 的任何 prompt。評估用的就是現行 `cards.csv` 的 `image_prompt`
  （含為 fabricatedXL 調的風格後綴），**測出 6 勝 2 和的就是這個組合**。
  後綴要不要為 klein 重調沒有測過，不憑推測改
- 不刪 `card_image_xl.json`。它是回頭路，也是 `.env.example` 的第二組範例
- 不改 `COMFYUI_NEGATIVE_PROMPT` 的內容。這份 workflow 用不到它（見 Task 7.4）

---

## 換之前要知道的三件事

1. **負向 prompt 對這份 workflow 完全無效**。`CFGGuider`(94) 的 cfg 是 1，
   負向接 `ConditioningZeroOut`(95)，而 95 沒有文字欄位。注入點打的是一個
   **孤兒節點**——它不被輸出節點依賴，ComfyUI 根本不執行它。
   存在的唯一理由是讓 `workflow.validate()` 的必填檢查過關。
   防文字完全靠 2026-08-27 的 prompt 端治本
2. **節點 `100` 的 sage 模式必須是 `auto`**。原 workflow 寫的
   `sageattn_qk_int8_pv_fp16_triton` 在本機裝的套件版本裡不存在，照抄會 ImportError
3. **ComfyUI 常駐從 6.6 GB 變成 17.4 GB**，這會讓 CLAUDE.md 那條
   「VRAM 約束是模型大小的函數」重新成立，並牽動 Task 7.3

---

## 任務清單

一次一個 task，完成後停下來交付。

### Task 7.0 — 把評估產物收進版控（前置）

目前 untracked：`logs/2026-08-27_eval_flux2-klein-9b-ab.md`、
`workflows/image_flux2_text_to_image_9b.json`、
`workflows/image_flux2_text_to_image_9b_with_lora.json`、`workflows/kyoani_fulx2.json`。

前一個 commit（`8bb29ae`）的訊息宣稱含評估紀錄，實際只含了 prompt 改動日誌——
第三、四輪是之後才補的。先把這四個檔案 commit，後面的改動才有乾淨的 diff 基準。

`work/ab-klein-9b/` 底下的圖與一次性腳本**不進版控**（`work/` 本來就不追蹤）。

---

### Task 7.1 — 三個新設定：prompt 前綴與寬高節點 ID

**為什麼**：這份 workflow 有兩處對不上現行注入點模型。

| 問題 | 現況 | 解法 |
|------|------|------|
| 觸發詞 `Anime. ` 要加在每個 `image_prompt` 前 | 沒有這個概念 | 新增 `COMFYUI_PROMPT_PREFIX` |
| 寬高在 `88`／`89` **兩個** PrimitiveInt | 注入點只認單一 `latent_node_id` 的兩個欄位 | 新增 `COMFYUI_WIDTH_NODE_ID`／`COMFYUI_HEIGHT_NODE_ID`，未設定時退回 `latent_node_id` |

寬高那項不能用「在 workflow 裡寫死字面值」了事：`88`／`89` 同時餵給
`EmptyFlux2LatentImage`(87) **與** `Flux2Scheduler`(97)，寫死會讓 `.env` 管不到尺寸
（違反約束 5），而第四輪已證實這份模型對尺寸極度敏感——尺寸必須留在 `.env` 手上。

**改動**

1. `src/anki_deck_builder/config.py`
   - `ComfyUISettings` 新增 `prompt_prefix: str = ""`
   - `ComfyUINodeSettings` 新增 `width_node_id: str = ""`、`height_node_id: str = ""`
2. `src/anki_deck_builder/clients/workflow.py`
   - `injection_points()` 的「latent 寬度」改用 `nodes.width_node_id or nodes.latent_node_id`，
     `id_env` 隨之顯示實際生效的那一個變數名（錯誤訊息要指對變數，否則使用者改錯地方）；
     高度同理。其餘邏輯不動
3. `src/anki_deck_builder/clients/comfyui_client.py`
   - `generate()` 送出前 `positive=self._settings.prompt_prefix + positive_prompt`。
     放在 client 而非 `ImageStage`：前綴是「這份 workflow／這個模型要的」，
     與 `negative_prompt`、`image_width` 同一層次，由持有 `ComfyUISettings` 的人負責

**測試**（`tests/clients/test_workflow.py`、`test_comfyui_client.py`、`test_config.py`）

- 寬高各指向不同節點時，兩者都被注入到正確的節點與欄位
- 只設 `latent_node_id`（現行 SDXL 設定）時行為完全不變 ← **回歸保護**
- `width_node_id` 設了卻對不上 workflow → `ConfigurationError`，訊息含 `COMFYUI_WIDTH_NODE_ID`
- `prompt_prefix` 空字串時送出的正向 prompt 一字不差；非空時前綴在最前面
- 預設值不變（`prompt_prefix=""`、兩個新節點 ID 為 `""`）

---

### Task 7.2 — 固化 `workflows/card_image_kyoani.json`

以 `workflows/kyoani_fulx2.json` 為底，把四輪評估打過的補丁寫死進檔案。
評估時是腳本在記憶體裡改的，正式版必須是一份能直接用的檔案。

| # | 節點 | 改什麼 | 為什麼 |
|---|------|--------|--------|
| 1 | `100` PathchSageAttentionKJ | `sage_attention`: `sageattn_qk_int8_pv_fp16_triton` → **`auto`** | 原值在本機套件版本裡不存在，ImportError。`auto` 實測可用、快 5.8%、畫面不變 |
| 2 | `97` Flux2Scheduler | `steps`: 9 → **12** | 第四輪：肉、人臉、黑板算式都更清楚。代價 12.9s → 16.6s |
| 3 | 新增 `999` CLIPTextEncode | `clip` 接 `92`，`text` 空字串 | 孤兒負向注入標的。不被輸出節點依賴＝不執行，但 `validate()` 過得了 |
| 4 | `96` CLIPTextEncode | 把作者的範例 prompt 換成空字串 | 每次生成都會被注入覆蓋，留著只會誤導讀檔的人 |

**不動**：`119` LoRA strength 1（作者建議值）、`84` sampler `euler`、`94` cfg 1、
`109` SaveImage、`88`／`89` 的值（由 `.env` 注入）。
作者另提 `euler_ancestral`／`res_multistep` 可試——**沒測，不改**。

**驗收**：`uv run python -c` 載入該檔跑一次 `workflow.validate()`，
再實際生成一張圖，確認 `100` 沒有噴 ImportError。

---

### Task 7.3 — audio 階段前也要釋放 ComfyUI

**這是換模型逼出來的必要改動，不是順手加的。**

`release_comfyui()` 目前只在 extract 之前呼叫（`cli.py:271`、`cli.py:381`、
`web/service.py:583`）。audio 之前只呼叫 `_free_vram_for_local_gpu()`，
那個只卸載 Ollama 的模型，**不碰 ComfyUI**。

| | ComfyUI 常駐 | ＋VOXCPM2 峰值 7.5 GB | 24 GB 卡 |
|---|---|---|---|
| SDXL（現行） | 7.2 GB | 14.7 GB | 有餘裕，所以一直沒事 |
| **kyoani** | **17.4 GB** | **24.9 GB** | **爆掉** |

**改動**：`cli.py` 的 `_run_audio` 與 `run-all` 的 ④ 之前、`web/service.py` 的
對應位置，各補一次 `await release_comfyui(settings, notify=...)`。

**設定變數的名字怎麼辦**：`COMFYUI_FREE_BEFORE_LLM` 的字面意思是「LLM 之前」，
而 VOXCPM2 不是 LLM。兩條路：

**使用者已裁示：走 (a)。**

- **(a) 沿用同一個變數** ← **採用**。把註解與 docstring 改成
  「在 extract 與 audio 之前釋放」，不動使用者既有的 `.env`
- (b) 改名成 `COMFYUI_FREE_BEFORE_GPU_STAGES`，語意精確但要同步改 `.env`、
  `.env.example`、`docs/usage.md` 與 Web UI 顯示（`service.py:220` 有帶出這個欄位）

**測試**：audio 路徑在 `free_before_llm=true` 時呼叫過 `release_comfyui`、
`false` 時沒有；既有 extract 路徑的測試不受影響。

---

### Task 7.4 — `.env` 與 `.env.example`

`.env.example` 保留 SDXL 那組當作第二組範例（註解掉），新增 kyoani 這組：

```
COMFYUI_WORKFLOW_PATH=workflows/card_image_kyoani.json
COMFYUI_PROMPT_PREFIX="Anime. "
COMFYUI_IMAGE_WIDTH=1344
COMFYUI_IMAGE_HEIGHT=768
COMFYUI_FREE_BEFORE_LLM=true

COMFYUI_POSITIVE_NODE_ID=96
COMFYUI_POSITIVE_FIELD=text
COMFYUI_NEGATIVE_NODE_ID=999
COMFYUI_NEGATIVE_FIELD=text
COMFYUI_SEED_NODE_ID=90
COMFYUI_SEED_FIELD=noise_seed
COMFYUI_LATENT_NODE_ID=
COMFYUI_WIDTH_NODE_ID=88
COMFYUI_WIDTH_FIELD=value
COMFYUI_HEIGHT_NODE_ID=89
COMFYUI_HEIGHT_FIELD=value
COMFYUI_OUTPUT_NODE_ID=109
```

要寫進註解的三件事，否則下一個人一定會踩：

1. **`COMFYUI_NEGATIVE_PROMPT` 對這份 workflow 無效**（cfg 1 + ConditioningZeroOut），
   調負向詞是白調
2. **尺寸不要往上加**。第四輪實測 1440×900 與 1600×896 都更暗更糊，
   加步數也修不掉；1600×896 還會糊出假日文
3. **`999` 是刻意的孤兒節點**，不是打錯

**引號不能省**（Task 7.1 已實測）：dotenv 會把未加引號的值尾端空格吃掉——

```
COMFYUI_PROMPT_PREFIX=Anime.      → 'Anime.'    ← 送出 "Anime.a tiger…"
COMFYUI_PROMPT_PREFIX="Anime. "   → 'Anime. '   ← 正確
```

前綴原樣相接、不自動補分隔符（這是刻意的，見 `config.py` 的欄位註解），
所以那個空格必須靠引號保住。

---

### Task 7.5 — 重生 308 張圖並重新打包

```bash
anki-builder image --force      # 約 85 分鐘（16.6s × 308）
anki-builder pack
```

- **必須 `--force` 全部重生**。只補未完成的會讓 fabricatedXL 與京阿尼兩種畫風
  混在同一副牌組
- 冷啟第一張約 100 秒（載 17.4 GB 權重 ＋ torch.compile），之後才進 16.6s 的節奏
- 跑完抽樣看圖：至少看 `p3_084`、`p3_037`、`p3_098`、`p1_046` 這四張——
  它們是 SDXL 失手最重的，也是換模型的理由
- `output/deck.zip` 體積會變（目前 35.8 MB），記下新值

**中止條件**：若前 10 張出現評估中沒見過的問題（畫風不對＝觸發詞沒生效、
大面積文字、記憶體不足），停下來報告，不要讓它跑完 85 分鐘。

---

### Task 7.6 — 文件與改動總結

| 檔案 | 改什麼 |
|------|--------|
| `docs/usage.md` | `.env` 逐項新增三個變數；疑難排解補「圖的畫風不對」→ 檢查 `COMFYUI_PROMPT_PREFIX` |
| `CLAUDE.md` | 進度表；「外部相依」的 workflow 一行改成 kyoani；VRAM 警語更新（ComfyUI 17.4 GB、`FREE_BEFORE_LLM` 重新變成必開）；〈已知限制〉的多元素構圖一項依實跑結果修訂 |
| `logs/2026-08-28_feat_kyoani-workflow.md` | 依 change-log-guide 產出改動總結 |

CLAUDE.md 的〈評估中（2026-08-27 暫停）〉整段要拿掉——評估已結案。

---

## 驗收指令

```bash
uv run pytest -q                      # 全綠（目前 680 passed）
uv run ruff check .
anki-builder status                   # image 全部 done
```

加一項人工檢查：`.env` 切回 `card_image_xl.json` 並清空 `COMFYUI_PROMPT_PREFIX`
後生成一張圖仍正常——**證明新設定沒有把 SDXL 那條路弄壞**。

---

## 回頭路

換回 SDXL 只需要改 `.env` 三個值：`COMFYUI_WORKFLOW_PATH` 指回
`card_image_xl.json`、`COMFYUI_PROMPT_PREFIX` 清空、節點 ID 換回那組，
然後 `anki-builder image --force` 重生。程式碼與 workflow 檔案都不必動。

---

## 風險

| 風險 | 應對 |
|------|------|
| 308 張跑到一半失敗 | 階段本來就逐列 checkpoint，重跑只補未完成的。但**畫風要一致**，中途改任何參數就得整批重來 |
| 12 步讓假文字更清楚 | 第四輪已見（`p1_046` 的投影幕）。依 2026-08-27 的判準修正，出字不列入評分 |
| SageAttention 換版本後 `auto` 也失效 | 節點 `100` 改 `disabled`，或把 `94.model` 接回 `119`。代價只有 5.8% |
| ComfyUI 重啟後首張變慢 | `allow_compile` 的一次性成本，屬預期 |
