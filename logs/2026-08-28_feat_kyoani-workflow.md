# 改動總結 — 文生圖換成 kyoani（FLUX.2-klein-9B + KyoAni LoRA）

日期：2026-08-28
分支：dev_ai
相關 commit：`db25e86`（Task 7.1，前一日已提交）、`16d08f4`、`44c8542`、`c27eecc`、`80c858e`，
以及本份日誌所屬的文件 commit
執行計畫：[.agent/plans/phase-7-execution-plan.md](../.agent/plans/phase-7-execution-plan.md)
評估依據：[logs/2026-08-27_eval_flux2-klein-9b-ab.md](2026-08-27_eval_flux2-klein-9b-ab.md)（五輪 A/B）

> Phase 7 的落地。這不是新功能——是把四輪 A/B 評估已經定案的模型與參數寫進版控，
> 再把 308 張圖整批重生。第五輪（sageattention 2.2.0 重測）是本日新增的。

---

## 變更清單

### 新增

1. `workflows/card_image_kyoani.json`：正式版 workflow。以作者的 `kyoani_fulx2.json`
   為底，寫死四處：節點 `100` 的 `sage_attention` → `auto`、`97` 的 `steps` 9 → 12、
   新增孤兒負向節點 `999`、清空 `96` 的範例 prompt
2. `logs/2026-08-28_feat_kyoani-workflow.md`：本檔

### 修改（程式）

3. `src/anki_deck_builder/cli.py`：`_run_audio` 與 `run-all` 的 ④ 之前各補一次
   `release_comfyui()`
4. `src/anki_deck_builder/web/service.py`：`_prepare()` 的 audio 分支同上
   （web 的 VRAM 讓渡順序必須與 CLI 完全相同）
5. `src/anki_deck_builder/stages/vram.py`：`release_comfyui()` 的 docstring 改寫——
   說明它現在有兩個呼叫點且理由不同，並更正「ComfyUI 常駐約 2.4 GB」這個 31B 時代的數字
6. `src/anki_deck_builder/config.py`：`free_before_llm` 的欄位註解說明變數名的「LLM」
   是歷史包袱、語意以 `release_comfyui()` 的 docstring 為準

### 修改（設定）

7. `.env`、`.env.example`：ComfyUI 段重新分塊——「連線與共用參數」＋「整組換的 workflow
   區塊」。kyoani 那組生效，SDXL 那組原樣註解在下面當回頭路。三則會踩的註解寫進去了
   （負向 prompt 對 kyoani 無效、尺寸不要往上加、`999` 是刻意的孤兒節點）

### 修改（測試）

8. `tests/conftest.py`：新增 `ENV_PREFIXES` 與 `clear_project_env()`
9. `tests/test_config.py`：改用共用的 `clear_project_env()`，移除重複定義
10. `tests/test_cli.py`：`env` fixture 加上前綴清掃；新增 audio 讓渡的兩條測試
11. `tests/test_web_service.py`：同上；新增 `_prepare` audio／image 兩條測試
12. `tests/clients/test_comfyui_client.py`：`make_settings` 補上 `prompt_prefix` 與
    `width_node_id`／`height_node_id`
13. `tests/clients/test_workflow.py`：`make_nodes` 補上同兩個節點欄位

### 修改（文件）

14. `docs/usage.md`：workflow 清單改成三份並說明「整組換」、`COMFYUI_PROMPT_PREFIX` 與
    `WIDTH/HEIGHT_NODE_ID` 的說明、VRAM 段補 kyoani 的 17.4 GB、新增〈圖的畫風不對〉
    疑難排解、〈生成圖含文字〉補負向 prompt 對 kyoani 無效、修訂多元素構圖的已知限制
15. `CLAUDE.md`：文檔索引補 Phase 7、進度表、外部相依的 workflow 一行、
    VRAM 警語（餘裕消失、`FREE_BEFORE_LLM` 變必開）、移除〈評估中〉整段
16. `.agent/plans/phase-7-execution-plan.md`：依第五輪修正 sage 相關的理由與數字
17. `logs/2026-08-27_eval_flux2-klein-9b-ab.md`：新增〈第五輪〉，並在第四輪測項 1
    上方標明已被取代

---

## 為什麼這樣改

### 節點 `100` 寫 `auto`：理由在本日換過

第四輪（套件 1.0）的理由是「原值 `sageattn_qk_int8_pv_fp16_triton` 會 ImportError，
非改不可」。使用者本日升到 2.2.0 後重測（第五輪），**兩個 fp16 具名變體都能跑了**，
速度與畫面和 `auto` 沒有差別（0.5s 內＝雜訊）。

所以理由換成**失敗模式的嚴重度不對稱**：具名值一旦對不上硬體，
`sageattn_qk_int8_pv_fp8_cuda` 那兩個會**直接打死整個 ComfyUI 行程**（`/prompt` 與
`/history` 一併斷線，靠容器重啟才活），而 `auto` 永遠退回這張卡跑得動的 kernel。
設定會被複製到別台機器，這個防呆零成本。

### audio 前也要釋放 ComfyUI：換模型逼出來的

| | ComfyUI 常駐 | ＋VOXCPM2 峰值 7.5 GB | 24 GB 卡 |
|---|---|---|---|
| SDXL | 7.2 GB | 14.7 GB | 有餘裕，所以 Phase 4～6 一直沒事 |
| kyoani | **17.4 GB** | **24.9 GB** | **爆掉** |

audio 之前原本只有 `_free_vram_for_local_gpu()`，那個只卸載 Ollama 的模型，
完全不碰 ComfyUI。沿用 `COMFYUI_FREE_BEFORE_LLM` 不改名（使用者裁示），
語意寫進 docstring 與欄位註解。

### `.env` 改成「整組換」的區塊

workflow 路徑、觸發詞前綴、尺寸、VRAM 讓渡與全部節點 ID 是綁在一起的，
只改 `COMFYUI_WORKFLOW_PATH` 一行必定對不上節點 ID。原本這些值散在兩節，
改成前後兩塊之後，換 workflow 就是「一塊註解掉、另一塊解開」。

---

## 測試結果

- `uv run pytest -q`：**695 passed、31 deselected**（Phase 6 收尾時為 680，
  Task 7.1 加 11 條、Task 7.3 加 4 條）
- `uv run ruff check .`：全過
- 新增測試都驗證過「把被測的呼叫移掉就會紅」，不是只會綠的假測試
- 需人工執行：`-m manual` 的 31 條照舊（本地模型推論）

### 順帶修掉的測試隔離漏洞

改 `.env` 之後有 **35 個既有測試變紅**，原因不是本次改動壞掉：`agent_factory` 匯入時
就呼叫 `load_dotenv()`，開發機的 `.env` 在 `monkeypatch.chdir()` 之前就進了
`os.environ`，chdir 擋不住。那些測試一直是靠「`.env` 剛好沒設這些變數」在過。

`test_config.py` 早就遇過同一件事並用前綴掃解決過（註解裡寫著
`VOXCPM2_VOICE_DESCRIPTION` 漏過一次）。本次把那段抽到 `tests/conftest.py` 的
`clear_project_env()`，`test_cli` 與 `test_web_service` 一併採用；兩個「每個欄位都明給」
的 builder 補上 Task 7.1 新增的三個欄位——**那是 Task 7.1 的疏漏**，只是當時 `.env`
沒有那些鍵所以沒炸。

驗證方式：故意帶著 `COMFYUI_IMAGE_WIDTH=99 COMFYUI_PROMPT_PREFIX="ZZZ "
COMFYUI_WIDTH_NODE_ID=777 COMFYUI_FREE_BEFORE_LLM=true VOXCPM2_CFG_VALUE=9.9`
跑整套，仍 695 passed。

---

## 實跑結果

```
anki-builder image --force   → 311/311，成功 311、失敗 0，耗時 1:17:59
anki-builder pack            → 308 張卡、924 個媒體檔（略過 3 列 raw_text 來源列）
```

| | 換之前（SDXL） | 換之後（kyoani） |
|---|---|---|
| `deck.zip` | 35.8 MiB | **29.4 MiB（−17.9%）** |
| `work/media/img` | 25 MB | 19 MB |
| 每張耗時 | 約 8s | 約 15s |
| 整批耗時 | 約 42 分（由 8s/張推算，未實測） | **1 小時 18 分（實測）** |

抽驗四張 SDXL 失手最重的（也是換模型的理由）：

- `p3_084` barbecue：舊的是一整排生肉配剪影路人，新的是後院烤肉聚會，
  人臉、烤爐、餐桌、食物都齊
- `p3_098` be over：舊的是空走廊配一個剪影，新的畫出一屋子收拾東西準備離場的人
- `p3_037` bag：兩者解讀不同（舊為拉行李箱的旅人，新為牆邊行李與塞滿衣物的旅行袋），
  新的把袋內細節畫出來了
- `p1_046`：抽象光爆，兩版都沒有假文字

前 10 張出來就先抽驗過畫風、文字與記憶體三項中止條件，都不成立才讓它跑完。

---

## 備註

- **速度比預估快**：計畫寫 85 分鐘（16.6s × 308 序列），實際 78 分鐘。
  `COMFYUI_BATCH_SIZE=4` 讓四張併發送件，攤掉了每張的 WebSocket 連線與 history
  輪詢固定成本；第五輪量到的 16.4s 是單張序列的數字
- **回頭路**：`.env` 把 kyoani 那塊註解掉、SDXL 那塊解開，再
  `anki-builder image --force` 重生即可，程式與 workflow 檔案都不必動。
  換回去的那組已實測 `validate()` 通過
- **已實測、不要重試**：kyoani 的負向 prompt 完全無效（cfg 1 ＋ `ConditioningZeroOut`）；
  尺寸不要往上加（1440×900 與 1600×896 都更暗更糊）；`sageattn3` 與兩個 fp8 變體在
  sm_86 上是硬體天花板，升版也不會變
- **仍未根治**：多元素構圖比 SDXL 齊得多，但仍會有整張改走另一種解讀的情形
