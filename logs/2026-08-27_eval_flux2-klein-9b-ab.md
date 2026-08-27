# 評估紀錄 — FLUX.2-klein-9B vs SDXL 的 A/B 比較

日期：2026-08-27 起，2026-08-28 補第三、四、五輪
分支：dev_ai
相關：[2026-08-27 prompt 防文字](2026-08-27_feat_no-text-props-in-image-prompts.md)〈待決事項〉選項 B
五輪：① klein 原版 vs SDXL　② klein + KyoAni LoRA（**LoRA 未觸發，結論作廢**）
　　　③ `kyoani_fulx2.json` ＋ 觸發詞 `Anime`（**選型結論**，見〈建議（第三輪）〉）
　　　④ SageAttention 與解析度補測（**參數結論**，見〈第四輪〉）
　　　⑤ sageattention 升 2.2.0 後重測（**第四輪測項 1 的數字與理由被本輪取代**，見〈第五輪〉）
產出圖：`work/ab-klein-9b/`（未版控；`pair/` 為左 SDXL、右 klein 的並排圖）

> **這是一次評估，不是改動。** 沒有動任何程式、prompt 或 `.env`；
> `COMFYUI_WORKFLOW_PATH` 仍指向 `card_image_xl.json`。

---

## 方法

照 2026-08-26 換 SDXL 那次的做法：8 張**真實 prompt**，兩個模型同一顆種子
（`20260827`）、同一組負向 prompt（`.env` 的 `COMFYUI_NEGATIVE_PROMPT`）、
同一個尺寸（1344×768），只數兩件事——**多元素齊不齊**、**有沒有出字**。

| 組別 | 卡片 | prompt 來源 |
|------|------|------------|
| 多元素構圖 | `p3_084` barbecue、`p3_037` bag、`p1_009` absent、`p3_098` be over | 現行 `work/cards.csv` |
| 含文字道具 | `p1_046` advertisement、`p1_041` advanced、`p1_037` admission、`p1_017` according to | `work/cards.csv.bak-before-rewrite`（**改寫前**，仍含 poster／blackboard／ticket／newspaper） |

文字道具組刻意用改寫前的 prompt——要測的是「模型在被要求畫文字道具時會不會寫字」，
用改寫後的版本等於先幫模型把難題拿掉。

腳本 `work/ab-klein-9b/ab_klein.py`（一次性，不進版控）直接呼叫既有的
`ComfyUIClient`，兩個模型分開跑，避免 ComfyUI 反覆換入換出權重。

## workflow 的處置

使用者提供的 `workflows/image_flux2_text_to_image_9b.json` 可直接送 `POST /prompt`，
不需要像 SDXL 那次修補四個節點。注入點對應：

| 注入點 | 節點 | 欄位 |
|--------|------|------|
| 正向 | `76` PrimitiveStringMultiline | `value` |
| 負向 | `75:67` CLIPTextEncode | `text` |
| seed | `75:73` RandomNoise | `noise_seed` |
| 輸出 | `9` SaveImage | — |
| latent 寬高 | **無法注入** | 見下 |

寬高分別放在 `75:68`／`75:69` 兩個 PrimitiveInt，而注入點只支援「單一 latent 節點的
兩個欄位」——同 2026-08-26 `EmptyLatentImage`(37) 那次的處置，評估時先在副本裡寫死
1344×768。**真要換模型，這一步得比照辦理修一份 `card_image_klein.json`。**

好消息：`clients/workflow.py` 把負向列為必填的前置條件**不成問題**，
這份 workflow 本來就有負向 CLIPTextEncode，`validate()` 直接過。

## 結果一：多元素構圖 — klein 3 勝 1 和，明顯較好

| 卡片 | prompt 要的 | SDXL | klein | 判定 |
|------|------------|------|-------|------|
| `p3_084` barbecue | 一群人圍著烤架、架上有肉 | **無頭軀幹三具**＋烤架＋詭異油燈，沒有「聚會」 | 後院烤肉全景：烤架、肉、串燈、一群人談笑 | **klein** |
| `p3_037` bag | 塞滿衣物的旅行袋**旁邊**一只行李箱 | 畫成電車車廂裡的女孩，主體全錯 | 塞滿衣物的行李袋＋旁邊的行李箱，完全對題 | **klein** |
| `p3_098` be over | 會議室、眾人收包、從門口離開 | 校園走廊＋一名女學生，會議室與收包都沒有 | 禮堂座席、人群提袋從逆光門口魚貫離開 | **klein** |
| `p1_009` absent | 空桌，**旁邊有人坐著**的對比 | 明亮教室、空桌，但沒有「有人坐著」的對比 | 同樣缺對比，且畫面偏暗、可讀性較差 | 和（SDXL 略易讀） |

**多元素這一項 klein 是真的贏，而且不是小贏**——SDXL 的三次失敗都是
「主體整個畫錯」，不是細節不齊。這與 2026-08-26 SDXL vs SD1.5「互有勝負」
的量級不同。

## 結果二：防文字 — klein **沒有**改善，甚至更糟

| 卡片 | SDXL | klein |
|------|------|-------|
| `p1_017` newspaper | 報頭一行大字亂碼（`10008`…） | **整版擬真報紙**：假標題、四欄假內文、走勢圖、人物照 |
| `p1_037` ticket booth | 海報上小面積模糊假字 | 售票亭門楣清楚寫著 `TIFP EUKI INE ONURIER` ＋兩行假說明 |
| `p1_041` blackboard | 假日文塗鴉＋折線圖 | 滿板擬真假算式（**這是 prompt 要的**，不算失分） |
| `p1_046` advertisement | 沒出字，但畫成動漫少女特寫、**完全脫題** | 演唱會舞台＋LED 抽象光斑，乾淨且對題 → **klein 勝** |

**結論：負向 prompt 在 klein 上一樣壓不住文字。** 這份 workflow 用的是
`CFGGuider`（cfg 5，真 CFG 不是蒸餾 guidance），負向確實有送進去，
但畫面照樣寫字——而且因為 klein 的擬真度高，它寫出來的假字**比 SDXL 更像真的**，
遠看是一份報紙，近看全是亂碼，違和感更強。

→ **不論換不換模型，2026-08-27 的 prompt 端治本都必須留著。**

## 結果三：速度 — klein 慢 6 倍

| 模型 | 冷啟第一張 | 熱機每張 | 308 張推估 |
|------|-----------|---------|-----------|
| SDXL（現行） | 80.8s | **8.5s** | 約 42 分鐘（歷史實跑 41:39） |
| klein-9B fp8 | 153.4s | **50.4s** | **約 4.3 小時** |

## 結果四：VRAM — klein 是 SDXL 的 2.7 倍

以 `POST /free` 清空後各跑一張，量 ComfyUI 的常駐值（扣掉 846 MiB 系統基線）：

| 模型 | ComfyUI 常駐 |
|------|-------------|
| SDXL `card_image_xl.json` | **6.6 GB** |
| klein-9B fp8 ＋ qwen_3_8b_fp8 編碼器 | **17.7 GB** |

這是**唯一一個會影響架構的數字**。24 GB 卡上：

- 現況三模型共存（ComfyUI 7.2 ＋ LLM E4B 11.6 ＋ VOXCPM2 7.5 ≈ 18 GB）成立，
  `COMFYUI_FREE_BEFORE_LLM=false` 才敢是預設
- 換 klein 後光 ComfyUI 就 17.7 GB，**剩不到 6 GB**，E4B 與 VOXCPM2 都塞不進去。
  `COMFYUI_FREE_BEFORE_LLM` 會重新變成必開，且每次階段切換都要重載 17.7 GB 權重

換句話說，CLAUDE.md 那條「VRAM 約束是模型大小的函數」的警語，
在 klein 這邊會**重新成立**。

## 第二輪：KyoAni Style LoRA（使用者於評估中途提供）

`workflows/image_flux2_text_to_image_9b_with_lora.json` 與原版的差別只有四處：
`LoraLoader`(75:104) 掛上 `KyoAni Style.safetensors`（model 1.5／clip 1.2）、
取樣器改 `euler_ancestral`、步數 **20 → 12**、種子預設值不同。
注入點與原版完全相同，腳本不必改。

同樣 8 張 prompt、同種子、同負向重跑：

| | klein 原版 | klein + LoRA |
|---|---|---|
| 熱機每張 | 50.4s | **30.9s**（步數少 8 步） |
| 308 張推估 | 4.3 小時 | **約 2.6 小時** |
| ComfyUI 常駐 | 17.7 GB | 17.8 GB（LoRA 幾乎不佔額外空間） |
| 構圖對題 | 好 | **一樣好**，8 張沒有一張退步 |
| 亮度／可讀性 | **偏暗**，`p1_009`／`p3_098`／`p1_017` 三張難看清 | **明顯改善**，同樣三張都看得清楚 |
| 畫風 | 寫實電影感 | 柔化的半寫實／動畫電影感，臉部乾淨、無崩壞 |

LoRA 把原版最大的兩個缺點（暗、慢）同時修掉，而且沒有付出構圖代價。
**klein 這一路要用就用這份，原版沒有留下的理由。**

## 判準修正（使用者於 2026-08-27 指定）

第一輪的建議建立在三個扣分項上，使用者看過後把其中兩項移出判準：

| 項目 | 使用者裁示 |
|------|-----------|
| 出字 | **不管什麼模型都去不掉，不必太在意** |
| VRAM | **放得下就好**——五個階段本來就互斥，不需要同時常駐 |
| 聯想是否清晰 | **這才是重點** |

VRAM 那一項成立：`StageRunner` 一次只跑一個階段，ComfyUI 的 17.8 GB 與 LLM／VOXCPM2
不必同時在卡上。代價只是階段切換時要重載權重（`COMFYUI_FREE_BEFORE_LLM=true`），
以及冷啟第一張多花約 170 秒——攤在 308 張的批次裡可以忽略。

## 建議（第二輪，**已作廢**——LoRA 沒被觸發，見第三輪）：批次改用 klein + LoRA

只用「聯想是否清晰」這一把尺重數 8 張：

| 卡片 | SDXL | klein+LoRA | 判定 |
|------|------|-----------|------|
| `p3_084` barbecue | 三具無頭軀幹＋詭異油燈，讀不出「烤肉聚會」 | 後院烤肉、眾人談笑 | **LoRA** |
| `p3_037` bag | 電車車廂裡的女孩，與 bag 無關 | 塞滿衣物的行李袋＋行李箱 | **LoRA** |
| `p3_098` be over | 校園走廊＋一名女學生 | 眾人提袋從逆光門口離場 | **LoRA** |
| `p1_046` advertisement | 動漫少女特寫，完全脫題 | 演唱會舞台＋人群 | **LoRA** |
| `p1_041` advanced | 少女坐在教室，黑板只有塗鴉 | 學生持書研讀滿板算式 | **LoRA** |
| `p1_037` admission | 空無一人的走廊，沒有售票亭 | 博物館大廳、售票櫃檯、排隊人群 | **LoRA** |
| `p1_009` absent | 明亮教室、空桌 | 空桌，光線更聚焦 | 和 |
| `p1_017` according to | 少女讀報、雨雲 | 男子讀報、暴風雲 | 和 |

**6 勝 2 和 0 敗。** 而且 SDXL 的六次失手全是同一個病灶——
**動漫模型把場景 prompt 畫成人物特寫**，正是 `prompts/image_prompt_template.md`
〈實測否決的調整〉裡記過的老問題。klein 沒有這個偏誤。

代價是每張 8.5s → 30.9s，全套 308 張重生約 2.6 小時（一次性）。
既然出字與 VRAM 都已不列入判準，**建議把批次生成換成 klein + LoRA**。

### 要換的話，缺這些

1. **`workflows/card_image_klein.json`**：以 LoRA 版為底，處理寬高注入。
   目前寬高分別放在 `75:68`／`75:69` 兩個 PrimitiveInt，
   而 `ComfyUINodeSettings` 只有單一個 `latent_node_id` 配 `width_field`／`height_field`。
   兩條路：
   - **(a) 寫死字面值**，`.env` 不再管得到 klein 的尺寸——最省事但違反約束 5
   - **(b) 加 `COMFYUI_WIDTH_NODE_ID`／`COMFYUI_HEIGHT_NODE_ID` 兩個選填設定**，
     未設定時退回 `latent_node_id`。約 15 行＋測試，SDXL 那份完全不受影響。
     **建議走 (b)**
   另注意 `Flux2Scheduler`(75:62) 也吃同兩個尺寸節點，改成字面值會讓它與實際 latent 脫鉤
2. **`.env`／`.env.example`**：`COMFYUI_WORKFLOW_PATH`、六個節點 ID、
   `COMFYUI_FREE_BEFORE_LLM=true`
3. **重生 308 張**（約 2.6 小時）——不重生會出現兩種畫風混在同一副牌組
4. **風格後綴要不要跟著改**：現行後綴是為 fabricatedXL 調的。
   〈實測否決的調整〉那四項是 **SDXL 特有的失敗**（`masterpiece`／`absurdres`
   把動漫模型拉去畫立繪），klein 未必適用——但**這次沒測，不要憑推測改**

## 第三輪：`kyoani_fulx2.json` ＋ 觸發詞（2026-08-28）

第二輪的 LoRA **根本沒生效**——使用者指出輸出全是寫實風，不是京阿尼。
查 [LoRA 頁面](https://civitai.com/models/2530366/kyoto-animation-style-lora-flux2-klein-9b)
才知道原因：

| 作者說明 | 我們第二輪的做法 |
|----------|-----------------|
| **觸發詞 `Anime`** | prompt 裡完全沒有這個詞 |
| 純自然英文，不要 tag | 相符 |
| strength **1** | 用了 1.5／clip 1.2 |
| 6–12 步 | 12 步，相符 |
| 1440×900 以上（16:9 多人場景） | 1344×768 |

第二份 workflow 掛了 LoRA 卻沒給觸發詞，等於白掛。**這是第二輪的結論失效的原因，
不是 LoRA 不好。**

### 觸發詞試打

用 `p3_084`（烤肉）與 `p1_046`（演唱會）各試三種寫法：

| 寫法 | 結果 |
|------|------|
| 原樣（對照組） | **仍是寫實風**，證實觸發詞是唯一變因 |
| **`Anime. ` 前綴** | 京阿尼畫風完整出現，構圖不受影響 ← **採用** |
| `Anime screenshot of ` 開頭 | 畫風同樣出現，但演唱會那張頂端糊出一大片假日文 |

### workflow 的兩處處置

| 問題 | 處置 |
|------|------|
| `100` PathchSageAttentionKJ → `ModuleNotFoundError: sageattention` | **繞過**，`94.model` 直接吃 `119`（LoRA 輸出）。它是注意力核心最佳化，裝了只會更快 |
| 負向接 `95` ConditioningZeroOut（**沒有文字欄位**），cfg 1 | 在副本裡補一個**孤兒** CLIPTextEncode(`999`)。它不被輸出節點依賴，ComfyUI 不執行它，而注入點的必填檢查能過——**這正是第一輪建議的做法，實測可行** |

### 結果：8 張全部重跑（`Anime.` 前綴、1344×768、同種子）

| | SDXL | kyoani_fulx2 |
|---|---|---|
| 熱機每張 | 8.5s | **13.5s**（9 步；SageAttention 沒裝，裝了更快） |
| 308 張推估 | 42 分鐘 | **約 69 分鐘** |
| ComfyUI 常駐 | 6.6 GB | 17.9 GB |
| 畫風 | fabricatedXL 動漫 | **京阿尼動畫風**，與現有牌組同屬動漫路線 |
| 聯想清晰度 | — | **6 勝 2 和 0 敗** |

| 卡片 | SDXL | kyoani | 判定 |
|------|------|--------|------|
| `p3_084` barbecue | 三具無頭軀幹＋詭異油燈 | 後院烤肉，十餘名角色圍著烤架 | **kyoani** |
| `p3_037` bag | 電車車廂裡的女孩 | 房間裡塞滿衣物的行李袋＋行李箱 | **kyoani** |
| `p3_098` be over | 校園走廊＋一名女學生 | 會議室、眾人提袋走向逆光門口 | **kyoani** |
| `p1_046` advertisement | 動漫少女特寫，完全脫題 | 演唱會舞台、投影幕、歡呼人群 | **kyoani** |
| `p1_041` advanced | 少女坐教室，黑板只有塗鴉 | 學生群聚黑板前研讀算式 | **kyoani** |
| `p1_037` admission | 空無一人的走廊 | 建築入口、售票亭、排隊人群 | **kyoani** |
| `p1_009` absent | 明亮教室、空桌 | 同樣是空桌，光線更暖 | 和 |
| `p1_017` according to | 少女讀報、雨雲 | 男子讀報、暴風雲 | 和 |

比數與第二輪相同，但這次**畫風對了、而且只慢 1.6 倍**（不是 3.6 倍）。
第二輪那個「2.6 小時、畫風不一致」的顧慮到這裡都消失了。

## 建議（第三輪，取代前兩輪）：批次換成 `kyoani_fulx2.json`

理由：聯想清晰度 6 勝 2 和 0 敗、畫風與牌組調性相符、全套重生只要約 69 分鐘、
VRAM 17.9 GB 在階段互斥的前提下放得下。

### 要換的話，缺這些

1. **觸發詞怎麼進 prompt**。`Anime. ` 必須加在每個 `image_prompt` 前面。三條路：
   - **(a) 新增 `COMFYUI_PROMPT_PREFIX` 設定**（預設空字串），由 `ImageStage` 在送出前
     串上去。prompt 本身保持模型無關，換回 SDXL 只要清空這個變數 ← **建議**
   - (b) 寫進 `prompts/extract_cards.md` 的風格後綴——會污染 308 張既有 prompt，
     且換回 SDXL 要再改一次
   - (c) 改寫 CSV 的 `image_prompt` 欄——最髒，等於把模型細節寫進資料
2. **`workflows/card_image_kyoani.json`**：以 `kyoani_fulx2.json` 為底，
   把上述兩處處置（繞過 SageAttention、補孤兒負向節點）固化進檔案，
   並處理寬高注入（同前一輪的 (b) 方案：新增 `COMFYUI_WIDTH_NODE_ID`／
   `COMFYUI_HEIGHT_NODE_ID` 兩個選填設定）
3. **`.env`／`.env.example`**：workflow 路徑、節點 ID、prompt 前綴、
   `COMFYUI_FREE_BEFORE_LLM=true`。
   注意 `COMFYUI_NEGATIVE_PROMPT` 對這份 workflow **完全無效**（cfg 1＋ZeroOut），
   要在 `.env` 註解寫明，否則下次有人調負向詞會白調
4. **重生 308 張**（約 69 分鐘）
5. **可選**：裝 `sageattention` 套件把節點 `100` 接回去；
   解析度是否改用作者建議的 1440×900 以上——**這兩項都沒測**

## 前兩輪的建議均已作廢

- 第一輪（選項 C：只當單張重生後備）：基於「出字沒改善＋慢 6 倍＋VRAM 2.7 倍」，
  前兩項判準已被使用者移除、第三項在有觸發詞的 9 步設定下降到 1.6 倍
- 第二輪（換 klein+LoRA，2.6 小時）：LoRA 沒被觸發，該輪的畫風與耗時數據都不作數

## 備註

- 產出圖全部留在 `work/ab-klein-9b/`：`full/` 原圖 38 張、`pair/` SDXL vs klein 8 張、
  `trio/` 三方並排 8 張（SDXL／klein／klein+LoRA）、`kyoani/` SDXL vs kyoani 8 張、
  `trigger/` 觸發詞三種寫法 2 張、`zoom/` 局部放大 8 張。`work/` 不進版控，要保留請自行搬移
- 三份一次性腳本一併放在該目錄（`ab_klein.py`、`kyoani.py`）與三份打過補丁的 workflow 副本
- klein 的三個權重檔（`flux-2-klein-base-9b-fp8`、`qwen_3_8b_fp8mixed`、`flux2-vae`）
  都已在 ComfyUI 的模型目錄裡，不需另外下載
- 本次沒有跑 pytest——沒有動到任何原始碼

---

## 第四輪：SageAttention 與解析度補測（2026-08-28）

第三輪〈可選〉列的兩項未測項目。使用者已在 ComfyUI 容器裡裝好 `sageattention` 並重啟。
腳本 `work/ab-klein-9b/sage_res.py`（一次性），同 8 張 prompt、同種子 `20260827`、
同 `Anime. ` 前綴。產出圖在 `work/ab-klein-9b/ab2/`，並排圖在 `cmp2/`。

### 測項 1：SageAttention — 可用，但只有 `auto` 模式，快 5.8%

> ⚠️ **本節已被〈第五輪〉取代**。這裡的 ImportError 與 5.8% 都是 `sageattention` **1.0**
> 下的結果；2.2.0 起兩個 fp16 具名變體都能跑，代價數字也更正為 4.1%。
> 結論（節點 `100` 寫 `auto`）不變，但**理由變了**。

workflow 原本指定的 `sageattn_qk_int8_pv_fp16_triton` **在這個版本的套件裡不存在**：

```
ImportError cannot import name 'sageattn_qk_int8_pv_fp16_triton' from 'sageattention'
(/usr/local/lib/python3.13/site-packages/sageattention/__init__.py)
```

KJNodes 的下拉選單有七個值，但那是寫死的清單，不代表套件真的匯出。逐一試打：

| 模式 | 結果 |
|------|------|
| `auto` | **可用** ← 唯一能跑的 |
| `sageattn_qk_int8_pv_fp16_triton`（workflow 預設值） | ImportError |
| `sageattn_qk_int8_pv_fp16_cuda` | ImportError |
| `sageattn_qk_int8_pv_fp8_cuda` | ImportError |

裝進去的版本只匯出了 dispatcher，沒有具名變體。
**要用就得把節點 `100` 的 `sage_attention` 改成 `auto`**，照抄原 workflow 會直接失敗。

同一場次的對照（1344×768、9 步，避免跨日的機器狀態差異）：

| | 熱機每張 | 308 張推估 |
|---|---------|-----------|
| 不接 sage（第三輪的做法） | 13.7s | 70 分鐘 |
| **接上 sage（`auto` + `allow_compile`）** | **12.9s** | **66 分鐘** |

**快 5.8%**，比預期小。ComfyUI 重啟後第一張要付 torch.compile 的錢
（實測 100.4s，含冷啟載模型），攤在 308 張裡可忽略。

畫面有沒有被 int8 量化改掉？8 張逐張比對：

| 卡片 | 平均絕對差（0–255） | 卡片 | 平均絕對差 |
|------|-------------------|------|-----------|
| p3_084 | 1.56 | p1_046 | 0.83 |
| p3_037 | 1.87 | p1_041 | 3.39 |
| p1_009 | 3.73 | p1_037 | 1.06 |
| p3_098 | 1.29 | p1_017 | 0.84 |

差異最大的 `p1_009`／`p1_041` 並排看過，構圖、光線、人物全部一致，
只有局部像素的細微擾動。**結論：純加速，不改畫面。**

### 測項 2：作者建議的解析度 — 實測**否決**

作者原話：「For 16:9 aspect ratio or scenes with multiple characters, I strongly
recommend using a latent resolution of **1440×900 or higher** to preserve fine details」

測兩種：`1440×900`（作者的字面值，16:10）與 `1600×896`（維持現行 7:4 比例、
面積 1.43 MP > 1440×900 的 1.30 MP）。兩者都開 sage。

| 解析度 | 熱機每張 | 308 張推估 | 畫面 |
|--------|---------|-----------|------|
| **1344×768（現行）** | 12.9s | 66 分鐘 | 明亮、乾淨 |
| 1440×900 | 15.5s | 80 分鐘 | **明顯變暗、變糊** |
| 1600×896 | 16.9s | 87 分鐘 | **更暗**，`p1_046` 還糊出一整條假日文橫幅 |

八張裡沒有一張因為解析度變高而變好，變差的有：

- `p1_009` absent、`p1_037` admission、`p3_098` be over：暗到細節難辨，
  `p1_037` 的售票亭在 1344 看得一清二楚，到 1440／1600 整個沒入陰影
- `p1_046` advertisement：1344 是乾淨的舞台＋投影幕；1440 變成人群疊羅漢、
  上緣人物解剖崩壞；1600 頂端多出一整條假日文——**解析度拉高反而破了防文字**

#### 排除混淆：不是步數不夠

9 步是為 1344×768 調的，解析度拉高需要更多步數是常見情況，所以補測 12 步
（作者建議 6–12，12 是上限）：

| 設定 | 熱機每張 | 結果 |
|------|---------|------|
| 1344×768 / 9 步 | 12.9s | 基準 |
| 1344×768 / 12 步 | 16.6s | **更清楚**（見下） |
| 1440×900 / 9 步 | 15.5s | 暗 |
| 1440×900 / 12 步 | 19.8s | 一樣暗，只是細節多一點 |

**加步數修不掉暗化。** 1440×900 在 12 步下依舊比 1344×768 的 9 步暗。
暗化跟著解析度走，合理的解釋是 `Flux2Scheduler`(97) 的 shift 隨寬高變動
——它跟 `EmptyFlux2LatentImage` 吃同一組 `88`／`89`。

→ **維持 1344×768。作者的解析度建議在我們這組 prompt 上不成立。**

### 附帶發現：9 步 → 12 步值得考慮

補測步數時看到的，不在原訂測項內，但影響換模型的參數選擇：

| 卡片 | 9 步 | 12 步 |
|------|------|-------|
| `p3_084` barbecue | 烤架上的肉糊成一團 | 肉、餐盤、人臉都分得出來 |
| `p3_037` bag | 行李袋輪廓可辨 | 袋口衣物、行李箱拉桿更清楚 |
| `p3_098` be over | 人臉略糊 | 人臉乾淨、離場動線更明確 |
| `p1_041` advanced | 黑板算式可讀 | 更可讀（**這是 prompt 要的**） |

代價：每張 12.9s → 16.6s，308 張 66 分鐘 → **85 分鐘**。
反效果：`p1_046` 的投影幕假字從模糊光斑變成勉強可辨的字母——
但依 2026-08-27 的判準修正，出字不列入評分。

### VRAM

| 情境 | 總用量 | 扣 846 MiB 系統基線 |
|------|--------|-------------------|
| 跑完閒置（常駐） | 18664 MiB | 17.4 GB |
| 1600×896 生成期間峰值 | 19996 MiB | 18.7 GB |
| sage 首張 torch.compile 期間 | 22472 MiB | 21.1 GB |

最高的一次是 compile，一次性。24 GB 卡在階段互斥的前提下都放得下。

### 第四輪結論

| 測項 | 判定 |
|------|------|
| 接回 SageAttention | **採用**，但節點 `100` 的模式必須改成 `auto`（原值 ImportError）。快 5.8%，畫面不變 ← 數字與理由見〈第五輪〉修正 |
| 改用 1440×900 以上 | **否決**。變暗變糊，加步數也修不掉；`1600×896` 還破了防文字 |
| （附帶）9 步 → 12 步 | **待使用者決定**。畫面更清楚，代價是 66 → 85 分鐘 |

第三輪〈要換的話，缺這些〉第 5 項的兩個「沒測」到此都測完了。
`workflows/card_image_kyoani.json` 固化時，節點 `100` 記得寫 `auto`。

---

## 第五輪：sageattention 2.2.0 重測（2026-08-28）

第四輪那三個具名變體的 ImportError 是**套件版本**造成的——當時容器裡裝的是 1.0。
使用者升到 2.2.0 後要求重驗。腳本 `work/ab-klein-9b/sage22.py`（一次性），
同 8 張 prompt、同種子 `20260827`、同 `Anime. ` 前綴，但**步數改成已定案的 12**、
尺寸 1344×768。產出圖在 `work/ab-klein-9b/sage22/`。

### 測項 1：七個模式逐一試打

| 模式 | 套件 1.0 | 套件 2.2.0 |
|------|----------|------------|
| `auto` | ✅ | ✅ |
| `sageattn_qk_int8_pv_fp16_triton`（workflow 原值） | ImportError | **✅ 可用** |
| `sageattn_qk_int8_pv_fp16_cuda` | ImportError | **✅ 可用** |
| `sageattn_qk_int8_pv_fp8_cuda` | ImportError | ⛔ **整個 ComfyUI 行程被打死** |
| `sageattn_qk_int8_pv_fp8_cuda++` | 未測 | ⛔ 同上，行程死掉 |
| `sageattn3` | 未測 | ✗ `cudaErrorNoKernelImageForDevice`（乾淨失敗） |
| `sageattn3_per_block_mean` | 未測 | ✗ 同上 |

後四個的失敗是**硬體天花板，不是版本問題**：3090 是 sm_86，沒有 fp8 kernel，
`sageattn3` 是 Blackwell 專用。再升版也不會變。

fp8 那兩個的失敗方式要特別記住：**不是節點報錯，是行程直接消失**——
`/prompt` 與 `/history` 一併斷線，靠容器的重啟策略才活回來
（實測兩次都自己回來，約一分鐘內）。

### 測項 2：三個能跑的模式，速度與畫面

每組 8 張，取熱機平均（去掉第一張）。`auto` 跑兩輪確認不是漂移。

| 設定 | 熱機平均 |
|------|----------|
| 不接 sage（節點 `100` 移除，`94.model` 接回 `119`） | 17.1s |
| `auto` ＋ `allow_compile` | **16.4s／16.7s**（兩輪） |
| `auto`，不 compile | 16.4s／16.5s（兩輪） |
| `sageattn_qk_int8_pv_fp16_triton` ＋ `allow_compile` | 16.7s |
| `sageattn_qk_int8_pv_fp16_cuda` ＋ `allow_compile` | 16.9s |

- **三個能跑的模式差距在 0.5s 內＝雜訊**，選哪個都不影響產出速度
- **sage vs 不接 sage 是 4.1%**（16.4 vs 17.1）。第四輪記的 5.8% 是 9 步、
  套件 1.0 下的數字，**已作廢**，但「接 sage 值得」的判定不變
- **`allow_compile` 對熱機速度量不出差別**。它只在冷啟多花約 80 秒
  （`auto` 冷啟首張 96.3s，權重載入＋編譯合計；之後即 16.7s）
- 同一組設定重跑 sha256 完全一致——每個設定各自可重現

畫面：`p3_084`、`p1_046`、`p3_098` 三組並排看過，`auto`／`triton`／`cuda`
構圖用色一致，只有像素級差異（`auto` 的 dispatcher 在 sm_86 上選的
累加精度與直接指名不同，所以 sha 不同）。

### 第五輪結論

| 項目 | 判定 |
|------|------|
| 節點 `100` 寫 `auto` | **維持**，但理由改了——不再是「原值 ImportError」，而是**失敗模式的嚴重度不對稱**：具名值一旦對不上硬體，fp8 那兩個會打死整個 ComfyUI 行程；`auto` 永遠退回這張卡跑得動的 kernel。設定會被複製到別台機器，防呆值得 0 成本 |
| `allow_compile` | **維持 workflow 原本的 `true`**。實測中性，沒有偏離作者原值的必要 |
| 接 sage | **維持採用**，代價數字更正為 4.1% |
| 12 步的每張耗時 | 16.4〜16.7s，第四輪預估的 16.6s 準確，308 張仍約 85 分鐘 |

對 Phase 7 的影響：`phase-7-execution-plan.md` 的〈換之前要知道的三件事〉第 2 點、
Task 7.2 改動 #1 與〈風險〉表已依本輪同步改寫。**Task 7.2 的四項改動一項都沒少**。
