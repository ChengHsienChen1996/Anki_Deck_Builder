# Phase 8 執行計畫 — 抽取任務再拆分與生圖 prompt 的 profile 化

日期：2026-09-01
分支：dev_ai
性質：規格與執行計畫合一（同 Phase 6、7）

> 這不是新功能，是**把一個過載的 LLM 呼叫拆開**，並修正一個資料層的設計缺口：
> 模型專屬的風格字串目前被烤進每一列卡片資料，換模型要改資料而不是改設定。

---

## 目標與非目標

**目標**

1. 把 `extract` 的三件事拆開：**結構化抽取**、**條目補全**、**生圖 prompt 建立**。
2. 生圖 prompt 分成「模型無關的語義層」與「模型專屬的語法層」，
   語法層依 **profile** 產生——換模型只重生語法層，創意工作一次都不重做。
3. 讓「換文生圖模型」變成**換一個 profile**：一份 prompt 檔 ＋ 一份 workflow，
   系統中不殘留任何模型專屬的散裝參數。

**非目標**

- 不動 workflow、模型、步數、尺寸、seed、批次——Phase 7 已定案的參數一律不重議。
- 不動 `image` 階段的生成邏輯。它照舊只讀 `image_prompt` 一欄。
- **不重調統一風格後綴的內容**。`image_prompt_template.md`〈實測否決的調整〉那四項
  仍然有效，本 phase 只改它**住在哪裡**，字串一字不動。
- 不發明 profile 設定檔格式（YAML／TOML）。沿用 `.env` 現行「整組換的區塊」慣例。
- 不改記憶引擎、不改 `pack`、不改 CSV 以外的持久化。

---

## 為什麼要拆（三項證據）

### 1. 規則份量與模型負擔失衡

`prompts/extract_cards.md` 各欄位規則行數：

| 欄位 | 行數 |
|------|------|
| **`## image_prompt`** | **38** ← 全檔最長 |
| `## deck` | 32 |
| `## example` | 13 |
| `## back` | 11 |
| 其餘 14 個欄位 | 各 4～8 |

`image_prompt` 是 17 個欄位裡的 1 個，卻吃掉 17% 的規則篇幅。而抽取模型是 **E4B（8B）**，
一次呼叫要同時做：判定領域、選封閉清單分類、17 欄結構化輸出、憑知識補讀音與例句，
**再加一次跨語言＋跨模態的創意視覺轉譯**（詞條 → 英文可畫場景 → 避開 8 列禁用道具表）。

最後這件事跟前面全部不同型：前面是「照原文填表」，它是「無中生有一個畫面」。
這正是 `EnrichAgent` 當初被拆出去的理由（`agents.yaml` 註解、phase-2 執行計畫 §2.9）：

> 實測 8B 模型在單次呼叫裡同時做「憑知識生成」與「結構化輸出」會顧此失彼——
> 整頁 101 張卡中有連續三段完全沒補例句。

### 2. 實測：規則寫進去了，模型沒照做

2026-08-27 把〈避開會帶出文字的道具〉表格加進 `extract_cards.md`，
`scripts/rewrite-image-prompts.py` 的 docstring 宣稱「新抽取的卡片不會再有這個問題」。
2026-09-01 掃描實際資料，這個宣稱沒有成立（掃描時已扣除統一風格後綴，只看場景描述）：

| 資料 | 場景描述含禁用道具 |
|------|-------------------|
| 規則上線**前** 308 張（`work/cards.csv.bak-2026-08-26`） | 67 張（22%） |
| 規則上線**後** 13 張（`work/cards.csv`，2026-09-01 02:22） | **4 張（31%）** |

後者第一張就是 `a graph showing a steep upward curve of numbers or objects...`——
`graph` 明確在禁用表內，同一句還要求畫 numbers。另三張是 `form`、`map`、`form`。

13 張樣本不足以說 31% > 22% 有統計意義，但**「規則加了、比例沒降」的方向是清楚的**。
相對地 `ImagePromptAgent`（單一任務、只輸出一行）做同一件事有效——所以才有那支修補腳本。
**專案已經用實作承認了「分開做比較好」，只是目前放在 pipeline 外。**

### 3. 模型專屬字串烤進資料層

現在存進 CSV 的 `image_prompt` 是三段黏在一起的字串：

```
a tiger standing among a family of cats , taxonomy chart atmosphere , cinematic lighting, ... no watermark
└──── 語義（模型無關）────┘ └── 氛圍 ──┘ └──────── 風格（模型專屬）────────┘
```

第三段是模型專屬的，卻和前兩段一起存進 308 列。約束 5 的理由是
「workflow 由使用者自帶，硬編碼會使工具綁死單一 workflow」——
現在的狀況是**換一份 workflow 要改 308 列資料**。

三個佐證：

- **不對稱**：`Anime. ` 觸發詞是 kyoani 專屬的，Phase 7 做成 `COMFYUI_PROMPT_PREFIX`
  （`.env` 改一行）；同樣模型專屬的後綴卻烤在資料裡。同一件事兩種處理方式。
- **後綴是為 SD 1.5 寫的**（`image_prompt_template.md` 自述），SDXL 沿用，
  現在跑在 FLUX.2 蒸餾模型上。字串串接無法把 tag 式轉成自然語言式——**這是改寫，不是串接**。
- **同一段字串存在四個檔案**：`extract_cards.md`、`extract_cards_vision.md`、
  `image_prompt_template.md`、`image_prompt_rewrite.md`，各自帶「改這裡要一併改那裡」
  的維護注意。靠人力維持的一致性。

---

## 設計

### 三層

| 層 | 欄位／位置 | 內容 | 誰產出 | 換模型時 |
|----|-----------|------|--------|---------|
| **語義層** | `image_scene`（新欄） | 模型無關的場景，英文逗號片語。**無風格詞、無觸發詞、無後綴** | `scene` 階段（`SceneAgent`，每卡一次） | **不動**。人工編修過的也保留 |
| **語法層** | `image_prompt`（既有欄） | 依 profile 改寫成該模型要的語法，**含觸發詞與風格後綴** | `prompt` 階段（profile 指定的 agent） | 重生 |
| **生成** | — | 照舊只讀 `image_prompt` 送 ComfyUI | `image` 階段（不改） | 重生 |

換模型 = 改 `.env` 一個區塊 → `prompt --force` → `image --force`。

### Profile 是什麼

**一個 profile ＝ 一份 prompt 檔 ＋ 一份 workflow 檔。** 模型專屬的三件事——
**目標語法**（自然語言散文 vs 逗號 tag）、**LoRA 觸發詞**、**風格後綴**——
全部寫在那份 prompt 檔裡，`.env` 只留一行指出用哪個 agent：

```bash
# ══ 生效中：FLUX.2-klein-9B + KyoAni Style LoRA ══
IMAGE_PROMPT_AGENT=ImagePromptFluxAgent      # → prompts/image_prompt_flux.md
COMFYUI_WORKFLOW_PATH=workflows/card_image_kyoani.json
（尺寸、節點 ID 等照舊）

# ══ 回頭路：fabricatedXL / SDXL ══
# IMAGE_PROMPT_AGENT=ImagePromptSDXLAgent    # → prompts/image_prompt_sdxl.md
# COMFYUI_WORKFLOW_PATH=workflows/card_image_xl.json
```

**兩個設計決定，都是使用者裁示（2026-09-01）：**

1. **觸發詞與後綴不再是系統參數，寫進 profile 的 prompt 檔。**
   `COMFYUI_PROMPT_PREFIX` **移除**，不新增 `COMFYUI_PROMPT_SUFFIX`。
   理由是使用者提的：同一件事有兩個機制、換模型時參數殘留在系統中，
   會產生無法預期的交互作用。移除後 **CSV 裡的 `image_prompt` 就是送進 ComfyUI 的完整字串**，
   中間沒有任何加工——這同時是可除錯性的淨收益。

   > 我曾主張觸發詞應留在 config（LLM 可能漏字、改大小寫、翻譯掉觸發詞）。
   > 使用者權衡後選擇內聚優先，本計畫照此執行；緩解手段見 Task 8.3 的產出檢查與 Task 8.6 的抽樣驗收。

2. **不設 `concat`（純字串串接）模式，每個 profile 都走 agent。**
   留 concat 就必須把後綴放回 `.env`，等於留下第 1 點要消除的殘留參數。
   SDXL profile 的 prompt 檔因此是一份「近乎直通」的改寫指令
   （場景原樣保留 ＋ 逐字接上後綴）。代價是回頭路也要付每卡一次呼叫——
   回頭路是回頭路，這個代價可以接受。

### 條目補全（原題「example 補全」）

**不新增階段、不新增欄位。** `EnrichAgent` 已經在 `extract` 前面，只要：

- `extract_cards.md` 的〈補全規則〉整段搬進 `enrich_entries.md`；
- enrich 從「條件觸發」改為**常駐執行**，職責收斂成「只補缺的，原文一律原樣保留」。

要驗證的風險是 `_needs_enrichment()` 當初擋的那件事：
「把已有釋義的教材重寫一遍反而可能覆蓋掉原文」。常駐後這道防線消失，
改由 prompt 的保留規則守住——Task 8.4 排一次 diff 驗證，
`MaterialTypeAgent` 的去留看該次結果。

### 拆完的 pipeline

```
① ocr → ② extract → ③ scene → ④ prompt → ⑤ image → ⑥ audio_front/back → ⑦ pack
            ↑ enrich 常駐於此（不是獨立階段）
```

| 項目 | 變化 |
|------|------|
| CSV 欄位 | 39 → **44**（`image_scene`、`scene_status/error`、`prompt_status/error`） |
| 新階段 | 2 個（`scene`、`prompt`），走既有 `@register_stage` ＋ `STAGE_FIELDS` 機制 |
| `extract_cards.md` | 223 → 約 120 行；`extract_cards_vision.md` 同步縮減 |
| 首次全跑 | 多 308 次 scene ＋ 308 次 prompt 呼叫，估 **+30～50 分鐘** |

`scene` 與 `prompt` **分成兩個階段而不是一個內部兩步**——理由同
`audio_front`／`audio_back` 拆開：**要能獨立重跑**。合成一個的話，
換模型想重生語法層就會連創意層一起洗掉，等於白拆。

**舊工作檔相容性**（2026-09-01 修正，原文寫錯）：`CardStore.read()` 會**逐欄比對表頭**
與 `CardRow.field_order()`，不一致直接拋 `WorkFileError`（`state/store.py:74`）。
這道檢查在 `from_csv_row()` 之前，所以「缺欄回退預設值」那條容錯**幫不上忙**——
Phase 8 之前的 39 欄工作檔加完欄位後會**全部讀不進來**。
因此 Task 8.1 的遷移腳本是**必要的，不是選配**。

---

## 任務清單

一次一個 task，完成後停下來交付。

### Task 8.1 — 資料結構與階段接線

**改動**

1. `schemas/card.py`：`FIELD_ORDER` 與 `CardRow` 新增 `image_scene`
   （置於 `image_prompt` 之前）、`scene_status`／`scene_error`、
   `prompt_status`／`prompt_error`。
2. `state/selector.py`：`STAGE_FIELDS` 依執行順序插入 `scene`、`prompt`
   （在 `extract` 與 `image` 之間）。`STAGE_NAMES` 自動跟著變，
   `status`／`reset` 兩個子命令因此自動支援新階段。
3. `scripts/migrate-to-image-scene.py`（一次性）：**必要的遷移**，做兩件事——
   (i) 補上五個新欄位，否則舊工作檔讀不進來（見上方相容性）；
   (ii) 把 `image_prompt` 切掉統一風格後綴後寫入 `image_scene`，
   成功的列 `scene_status`／`prompt_status` 一併設 `done`。
   後綴逐字固定（實測 308/308 與 13/13 皆以該串結尾），**是確定性字串處理，不呼叫 LLM**。
   切不開的列一律留 `pending` 並列出，交給 `scene` 階段重生，不臆測、不硬切。
   `.bak` 備份檔**不遷移**——備份被改寫就失去備份的意義；Task 8.5 要用時複製一份再遷移。

**測試**

- 39 欄的舊工作檔被 `CardStore.read()` 擋下，訊息含五個新欄位名（這是遷移腳本存在的理由）
- 寫回後欄位順序符合 `field_order()`
- `stage_fields("scene")`／`("prompt")` 取得正確欄位；未知階段的錯誤訊息含新名稱
- `reset --stages scene,prompt` 能把兩者設回 `pending`
- seed 腳本：含後綴的列被正確切分；不含後綴的列原樣保留並回報

---

### Task 8.2 — `scene` 階段（語義層）

**為什麼**：把創意視覺轉譯從 17 欄結構化呼叫裡移出來，這是〈為什麼要拆〉第 1、2 點的正解。

**改動**

1. `prompts/image_scene.md`（新）：`SceneAgent` 的指令。內容來自
   `image_prompt_template.md` 中**模型無關**的兩節——〈避開會帶文字的道具〉與
   〈抽象條目怎麼畫〉——加上輸出格式（英文小寫、逗號分隔片語、不加句號）。
   **明確禁止**輸出任何風格詞、品質詞、觸發詞、後綴：那是語法層的事。
2. `agents.yaml`：新增 `SceneAgent`，模型同 `extract-llm`，
   `temperature: 0.0`、`max_tokens: 256`、`reasoning_effort: none`，不設 `output_schema`
   （只輸出一行純文字，同 `ImagePromptAgent` 的既有做法）。
3. `stages/scene.py`（新）：`@register_stage("scene")`。輸入是卡片的
   `front`／`back`／`example`／`category`，輸出寫入 `image_scene`。
4. `stages/factory.py`：`build_scene_stage()`。
5. `prompts/extract_cards.md`、`extract_cards_vision.md`：**刪除整個 `## image_prompt` 一節**
   （兩份必須同步，見該檔維護注意）。
6. `schemas/extract_output.py`：`ExtractedCard` 移除 `image_prompt` 欄位。
7. `cli.py`：新增 `scene` 子命令、`run-all` 插入 ③。
8. `web/service.py`：階段清單與進度接線同步（約束 4：兩個介面走同一組函式）。

**產出檢查**（同 `_check_card_quality` 的精神，讓失敗在產生它的階段就浮現）

- 空字串或只有空白 → 失敗
- 含換行 → 失敗（模型多寫了說明，此時第一行是不是答案並無保證）
- 長度低於下限或超過上限 → 失敗
- 混進語法層的風格詞（`cinematic lighting`、`masterpiece` 等）→ 失敗（層次被打破）
- 失敗重試一次，仍失敗則該列 `failed`（約束 3：不中斷整批）

**引號、code fence、標籤前綴改為容忍並拆掉，不算失敗**（與原計畫不同）：
那是這個本地模型已知的無害習慣（`scripts/rewrite-image-prompts.py` 的 `clean()`
早就在處理同一批徵狀），拆掉包裝後語義一字不差。無害的包裝就拆掉，
有疑慮的形狀才重試——理由寫在 `stages/scene.py` 的模組 docstring。

**測試**：以假 client 覆蓋上列每一種產出形態；`extract` 不再產出 `image_prompt`
（回歸保護：既有 extract 測試中對該欄的斷言要改為斷言其為空）。

---

### Task 8.3 — `prompt` 階段（語法層）與 profile 化

**改動**

1. `config.py`：
   - 新增 `ImagePromptSettings`（前綴 `IMAGE_PROMPT_`），欄位 `agent: str`。
   - **移除 `ComfyUISettings.prompt_prefix`**（使用者裁示：不留殘留參數）。
2. `clients/comfyui_client.py`：`generate()` 移除前綴串接，`positive` 原樣送出。
   Phase 7 為 prefix 加的那段註解一併移除，改寫成「完整 prompt 由 `prompt` 階段負責，
   本層不加工」。
3. `prompts/image_prompt_flux.md`（新）：kyoani profile。內容 = 目標語法（自然語言散文）
   ＋ **觸發詞 `Anime. ` 必須原樣置於開頭** ＋ 該模型要的風格描述。
   明確寫入 Phase 7 已實測的三件事：負向 prompt 無效、尺寸不要加、
   `image_prompt_template.md`〈實測否決的調整〉那四項不要重試。
4. `prompts/image_prompt_sdxl.md`（新）：回頭路 profile。
   場景原樣保留 ＋ 逐字接上統一風格後綴（**字串一字不動**）。
5. `agents.yaml`：新增 `ImagePromptFluxAgent`、`ImagePromptSDXLAgent`
   （取代既有的 `ImagePromptAgent`），`max_tokens: 512`、`reasoning_effort: none`。
6. `stages/prompt.py`（新）：`@register_stage("prompt")`。讀 `image_scene`
   （空的話該列失敗，訊息指向 `scene` 階段），依 `settings.image_prompt.agent`
   取得 agent，輸出寫入 `image_prompt`。
7. `stages/factory.py`、`cli.py`（④ 子命令與 run-all）、`web/service.py` 接線。
8. `.env`／`.env.example`：ComfyUI 區塊移除 `COMFYUI_PROMPT_PREFIX`，
   兩組 profile 各加 `IMAGE_PROMPT_AGENT`。

**產出檢查**（這裡是觸發詞風險的緩解點）

- 空字串／換行／圍棧／引號包裹 → 失敗（同 8.2）
- **語義保底**：輸出與 `image_scene` 的內容詞重疊率過低 → 失敗。
  防的是「模型自己重新想了一個場景」，讓語義層形同虛設
- 長度上下限
- 失敗重試一次，仍失敗則該列 `failed`

> 觸發詞是否逐字保留**不做程式檢查**——那需要把觸發詞再變成一個系統參數，
> 正是本 phase 要消除的東西。改以 Task 8.5 的 A/B 與 Task 8.6 的抽樣驗收把關。

**語義保底的門檻改為 0.3（原訂 0.5，實測誤殺）**：`a small pile of coins next to
a much larger overflowing pile` 被改寫成 `...rests beside an enormous, overflowing
mound of currency, illustrating rapid accumulation`——語義完整保留，但比較級與
氛圍片語被換掉，重疊率只有 44%。好的散文改寫本來就會做這些替換；而真正要擋的
「換題材」重疊率接近 0，判別邊界很寬。理由與數據記在 `stages/prompt.py`。

**`scripts/rewrite-image-prompts.py` 與 `prompts/image_prompt_rewrite.md` 提前到
本 task 刪除**（原訂 8.7）：它們用的 `ImagePromptAgent` 在本 task 被兩個 profile
agent 取代，留著就是指向不存在的 agent 的死程式碼。

**測試**

- 依設定選到正確的 agent；agent 名稱設錯 → `ConfigurationError`，訊息含 `IMAGE_PROMPT_AGENT`
- `image_scene` 為空的列失敗，訊息指向 `scene` 階段
- 語義保底檢查的正反例
- `comfyui_client` 不再對 prompt 加工（回歸保護：既有 prefix 測試改為斷言原樣送出）

---

### Task 8.4 — 條目補全（2026-09-01 完成，**結論與原計畫相反**）

原計畫：把〈補全規則〉搬進 `enrich_entries.md`，並讓 enrich 從條件觸發改為常駐。
計畫也預先寫好了退路：「若守不住，`MaterialTypeAgent` 留著」。

**兩項實測都不支持常駐化，已放棄合併。**

**驗證 1 — enrich 跑在已有釋義的教材上會改動原文**（計畫要求的 diff）

條目數、釋義、例句都保住了，但：詞性標記被重排成 `[詞性] 名・他サ`（模型把
佔位符的**名稱**當成值）、釋義被擅自轉繁體、OCR 留下的零散假名行被整批刪掉。
修掉 prompt 的佔位符 bug 後前兩項改善（詞性標記與日文原文逐字保留），
**但刪行仍然發生**。

**驗證 2 — 實產資料上沒有「例句缺漏」這個問題**

掃描 308 張卡：`example` 空的有 **0 張**。真正的缺口是**已有例句的分隔格式不合規**
——176/308（57%）用 `／` 或 ` / ` 而非規定的換行，而且退化是分頁單調的
（p1 合規 102/102、p2 30/91、p3 **0/115**）。prompt 早就寫了要換成換行，模型沒照做。

**實際做的事**

1. `prompts/enrich_entries.md`：修掉佔位符 bug（附正確／錯誤對照），
   把「原樣保留」強化成「逐字原樣保留」並列出實測到的三種違反。
2. `schemas/extract_output.py`：新增 `_normalise_example_separator` field validator，
   把 `原文／譯文` 正規化成 `原文\n譯文`。**分隔符轉換是確定性的字串操作**，
   用規則做一次就穩；靠模型服從度做，每跑一批就重擲一次骰子。
   只認全形 `／` 與前後有空格的 ` / `——裸斜線會誤切 `I like a/b testing.`（實際踩到）。
3. `stages/extract.py`：`_needs_enrichment()` 的 docstring 記下上述證據與決策，
   免得下一個人憑推測再改一次。

**沒有做、且刻意不做的事**

- `MaterialTypeAgent` **留著**，enrich 維持條件觸發。
- `extract_cards.md` 的〈補全規則〉**留在原處**：教材已有釋義時 enrich 不會跑，
  補缺口的就是抽取本身；搬走等於把那條路的補全能力刪掉。

---

### Task 8.5 — A/B：FLUX 自然語言 vs 現行 tag 式

**這是鋪開前的閘門，不是選配。** 過去四次「看起來更對」的 prompt 調整全部被實測否決
（`image_prompt_template.md`〈實測否決的調整〉），這次不重蹈。

- 取 **12～15 張**卡，含四張 SDXL 失手最重的（`p3_084`、`p3_037`、`p3_098`、`p1_046`，
  在 `work/cards.csv.bak-2026-08-26`），其餘涵蓋名詞／動詞／副詞／抽象概念。
- 同一批 `image_scene`，兩種語法層各生一次，**seed 相同**（`stable_seed(card_id)` 本來就穩定）。
- 判準沿用 2026-08-27 評估：語義符合度、構圖完整度、有無假文字。**出字不列入扣分以外的評分**。
- 對照組是「現行 kyoani 產出」——即 `image_prompt_sdxl.md` 的後綴式輸出 ＋ `Anime. ` 開頭，
  等價於 Phase 7 的實際行為。
- **一併檢查觸發詞**：兩組輸出逐張確認 `Anime. ` 逐字保留、位置在開頭。
- 結果寫入 `logs/2026-09-XX_eval_prompt-layer-ab.md`。

**中止條件**：自然語言組明顯較差，或觸發詞出現漏字／改寫 → 停下來報告，
不要進 Task 8.6。此時的退路是 `IMAGE_PROMPT_AGENT` 指向 SDXL 那份
（純後綴式），**三層架構的其餘收益全部保留**。

---

### Task 8.6 — 全量重跑與打包

```bash
anki-builder scene --force
anki-builder prompt --force
anki-builder image --force      # 約 78 分鐘（Phase 7 實測）
anki-builder pack
```

- **抽樣驗收觸發詞**：`grep -c` 確認全部 `image_prompt` 以觸發詞開頭，
  對不上就是 Task 8.3 的產出檢查有漏，回頭補。
- 抽驗上述四張硬卡，與 Phase 7 的產出並排比較。
- 記下新的 `deck.zip` 體積（Phase 7 為 29.4 MB）。

**中止條件**：前 10 張出現 A/B 中沒見過的問題（畫風不對＝觸發詞沒生效、
大面積文字、記憶體不足）→ 停下來報告，不要跑完 78 分鐘。

---

### Task 8.7 — 文件與改動總結

| 檔案 | 改什麼 |
|------|--------|
| `prompts/image_prompt_template.md` | 改寫成〈場景設計原則〉：保留〈避開會帶文字的道具〉與〈抽象條目怎麼畫〉（模型無關），移除統一風格後綴的「權威定義」角色與四檔同步注意——後綴現在只存在於各 profile 的 prompt 檔 |
| `scripts/rewrite-image-prompts.py` | **刪除**。功能被 `scene` ＋ `prompt` 兩階段完全涵蓋 |
| `docs/architecture.md` | 狀態機規格補兩個階段；約束 5 的推論補一句「模型專屬的 prompt 字串同樣不得進入資料層」 |
| `docs/usage.md` | 新增兩個階段的說明、`IMAGE_PROMPT_AGENT`、移除 `COMFYUI_PROMPT_PREFIX`、疑難排解補「換模型後畫風不對 → 忘了 `prompt --force`」 |
| `CLAUDE.md` | 進度表加 Phase 8；文檔索引加本計畫；〈已知限制〉依 8.5／8.6 結果修訂 |
| `logs/2026-09-XX_feat_phase-8-*.md` | 依 change-log-guide 產出改動總結 |

---

## 驗收指令

```bash
uv run pytest -q
uv run ruff check .
anki-builder status                   # scene、prompt 全部 done
```

人工檢查兩項：

1. `.env` 切到 SDXL profile（`IMAGE_PROMPT_AGENT=ImagePromptSDXLAgent` ＋ workflow 換回），
   跑 `prompt --force` 後抽看幾列，確認輸出＝場景 ＋ 統一風格後綴，且能正常生圖。
   **證明回頭路沒被弄壞。**
2. 隨機抽 5 張，把 CSV 的 `image_prompt` 與 ComfyUI 實際收到的 prompt 對照，
   確認**完全一致**（移除 prefix 加工後應如此）。

---

## 回頭路

| 想退回什麼 | 怎麼做 |
|-----------|--------|
| 自然語言語法層 | `IMAGE_PROMPT_AGENT` 指回 SDXL 那份，`prompt --force`。三層架構保留 |
| 換文生圖模型 | 換 `.env` 的 profile 區塊，`prompt --force` → `image --force`。**不動任何一列語義層資料** |
| 整個 Phase 8 | 三個 commit 區塊（8.1–8.3 資料與階段、8.4 補全、8.6 產出）可分別 revert；舊 CSV 因 `from_csv_row` 的缺欄回退而仍可讀 |

---

## 風險

| 風險 | 應對 |
|------|------|
| **8B 模型寫不好自然語言散文** | Task 8.5 的 A/B 是閘門；不過關就退回後綴式，其餘收益保留 |
| **觸發詞由 LLM 輸出而漏字／改寫** | 使用者已裁示採內聚設計。緩解：prompt 檔把它寫成硬性首句、8.5 逐張檢查、8.6 全量 grep 驗收。若實際發生，退路是把觸發詞併進 workflow 的 `96` 節點預設值——仍不回到系統參數 |
| **enrich 常駐覆蓋原文** | Task 8.4 動手前先做 diff 驗證，守不住就窄化而非硬上 |
| **兩個新階段讓 run-all 更長** | 使用者已接受（+30～50 分鐘換品質）。`scene` 是一次性成本，換模型不重跑 |
| **`extract_cards.md` 與 vision 版脫節** | 兩份要刪的是同兩節，一次改完並跑既有的一致性檢查 |
| **A/B 用的四張硬卡在 `.bak` 裡** | 先用 seed 腳本把 `.bak` 的 308 張轉出 `image_scene` 作為 A/B 素材，不必重跑 extract |
