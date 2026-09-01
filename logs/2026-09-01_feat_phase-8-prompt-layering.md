# 改動總結 — 抽取任務再拆分與生圖 prompt 的 profile 化

日期：2026-09-01
分支：dev_ai
相關 commit：`d0667b2`（8.1）、`de7a572`（8.2）、`a33bb50`（8.3）、`3d41d3f`（8.4）、
`1d6bdce`（8.5）、`34cafe3`（8.6），以及本份日誌所屬的文件 commit
執行計畫：[.agent/plans/phase-8-execution-plan.md](../.agent/plans/phase-8-execution-plan.md)
A/B 評估：[logs/2026-09-01_eval_prompt-layer-ab.md](2026-09-01_eval_prompt-layer-ab.md)

> Phase 8 做兩件事：把一個過載的 LLM 呼叫拆開，以及修正一個資料層的設計缺口
> ——模型專屬的風格字串原本被烤進每一列卡片資料，換模型要改資料而不是改設定。

---

## 起因

使用者檢視抽取 prompt 後提出：這個單一任務的複雜度還可以再切分。掃描實際資料後
確認了三項證據：

1. **規則份量失衡**：`extract_cards.md` 的 `## image_prompt` 佔 38 行，是全檔最長的一節，
   而它只產出 17 個欄位裡的 1 個。抽取模型是 E4B（8B），同一次呼叫要做結構化填表、
   憑知識補全，**再加一次跨語言跨模態的創意視覺轉譯**。
2. **規則寫進去了，模型沒照做**：2026-08-27 把〈避開會帶出文字的道具〉加進 prompt 後，
   違規率沒有下降（規則前 67/308，規則後 4/13）。
3. **模型專屬字串烤進資料層**：`image_prompt` 是「場景 ＋ 氛圍 ＋ 風格後綴」黏成的
   單一字串，第三段是模型專屬的。換一份 workflow 等於要改 308 列資料——
   與硬編碼節點 ID 是同一類錯誤（違反約束 5 的精神）。

---

## 變更清單

### 新增

1. `src/anki_deck_builder/stages/scene.py`：階段 ③ 語義層。讀卡片語義產出
   **模型無關**的 `image_scene`（不含風格詞、觸發詞、後綴）。
2. `src/anki_deck_builder/stages/prompt.py`：階段 ④ 語法層。依 `IMAGE_PROMPT_AGENT`
   指定的 profile 把場景改寫成該模型要的語法，寫進 `image_prompt`。
3. `prompts/image_scene.md`：`SceneAgent` 的指令。
4. `prompts/image_prompt_flux.md`：kyoani profile（自然語言敘述 ＋ `Anime. ` 觸發詞）。
5. `prompts/image_prompt_sdxl.md`：回頭路 profile（場景照抄 ＋ 統一風格後綴）。
6. `scripts/migrate-to-image-scene.py`：一次性遷移。
7. `logs/2026-09-01_eval_prompt-layer-ab.md`：A/B 評估紀錄。
8. `tests/stages/test_scene.py`、`tests/stages/test_prompt.py`、
   `tests/test_migrate_to_image_scene.py`。

### 刪除

9. `scripts/rewrite-image-prompts.py`、`prompts/image_prompt_rewrite.md`：
   功能被 `scene` ＋ `prompt` 兩階段完全涵蓋。

### 修改（程式）

10. `schemas/card.py`：新增 `image_scene` 與 `scene_status/error`、`prompt_status/error`。
    CSV **39 → 44 欄**。
11. `state/selector.py`：`STAGE_FIELDS` 插入 `scene`、`prompt`（`status`／`reset`
    兩個子命令因此自動支援）。
12. `schemas/extract_output.py`：`ExtractedCard` 移除 `image_prompt`；
    新增 `_normalise_example_separator` field validator。
13. `config.py`：新增 `ImagePromptSettings`（`IMAGE_PROMPT_AGENT`）；
    **移除 `ComfyUISettings.prompt_prefix`**。
14. `clients/comfyui_client.py`：`generate()` 不再對 prompt 做任何加工。
15. `stages/factory.py`、`cli.py`、`web/service.py`：兩個新階段的接線與階段重編號。
16. `stages/extract.py`：`_needs_enrichment()` 的 docstring 記錄 Task 8.4 的實測與決策。

### 修改（prompt 與設定）

17. `prompts/extract_cards.md`／`extract_cards_vision.md`：刪除整個 `## image_prompt` 一節
    （38／22 行）。
18. `prompts/enrich_entries.md`：修掉佔位符 bug、「原樣保留」強化為「逐字原樣保留」。
19. `agents.yaml`：新增 `SceneAgent`、`ImagePromptFluxAgent`、`ImagePromptSDXLAgent`；
    移除 `ImagePromptAgent`。
20. `.env`／`.env.example`：`COMFYUI_PROMPT_PREFIX` → `IMAGE_PROMPT_AGENT`。

### 修改（文件）

21. `prompts/image_prompt_template.md`、`docs/architecture.md`、
    `docs/project-overview.md`、`docs/usage.md`、`CLAUDE.md`：見 Task 8.7。

---

## 兩個由使用者裁示、與我的初始建議不同的設計決定

**1. 觸發詞與後綴收進 profile 的 prompt 檔，不留系統參數。**

我原本主張 LoRA 觸發詞應留在 config（LLM 可能漏字、改大小寫、翻譯掉它）。
使用者的理由是內聚：同一件事有兩個機制、換模型時參數殘留在系統中會產生
無法預期的交互作用。照此執行，`COMFYUI_PROMPT_PREFIX` 移除、不新增 SUFFIX。

**實測支持這個決定**：A/B 13/13、全量 308/308，觸發詞都逐字保留在開頭。
副作用是可除錯性的淨收益——查 ComfyUI history 的 200 筆正向 prompt，
**0 筆在 CSV 裡找不到一字不差的對應**。

**2. 文字判準從「不得出現」改為「壓低出現率」。**

零出現做不到，不再往那個方向投入。`image_scene.md` 因此新增
〈條目本身就是帶字的東西時〉的三段式出路，`image_prompt_template.md` 新增
〈已實測的天花板〉記下三種手段的結果。

---

## 實跑結果（308 張英文牌組全量重跑）

| 階段 | 結果 |
|------|------|
| `scene`／`prompt`／`image`／`audio_front`／`audio_back` | 各 **311/311，0 失敗** |
| 觸發詞 | **308/308** 在 `image_prompt` 開頭 |
| `pack` | 308 張卡、924 個媒體檔 |

`output/deck.zip`：**30.5 MiB**（Phase 7 為 29.4 MiB）。

| 指標 | 拆分前 | 拆分後 |
|------|--------|--------|
| 場景含禁用道具 | 59/308（19%） | **26/308（8%）** |
| 圖含假文字（隨機 24 張） | — | 4/24（17%） |
| `extract_cards.md` 行數 | 223 | 184 |

A/B（13 張，同場景同 seed，唯一變數是語法層）：自然語言組**語義與構圖 9 勝 1 敗 3 和**、
假文字**兩組完全相同**。

---

## 測試結果

```
uv run pytest -q     → 787 passed, 31 deselected
uv run ruff check .  → All checks passed
```

新增 3 個測試檔；`test_prompts.py` 改為守新架構的不變式
（兩份抽取 prompt 的欄位規則逐字一致、抽取端不再提到聯想圖任何一層）。

---

## 備註

### 沿路修掉的三個既有缺陷

1. **兩份抽取 prompt 已經漂移**。檔案自己寫明〈欄位規則〉以下必須逐字一致，
   實際上 vision 版缺了 `tts_front_text` 的 IPA 規則（實測音標會生出幾乎無聲的音檔）。
   已同步，並加測試機械性釘住這個不變式。
2. **例句分隔格式 176/308（57%）不合規**。prompt 早就要求換行，模型沒照做，
   而且退化是分頁單調的（p1 102/102 合規、p2 30/91、p3 **0/115**）。
   分隔符轉換改由程式做——確定性操作用規則做一次就穩，靠模型服從度做則每批重擲骰子。
3. **`enrich_entries.md` 的佔位符 bug**：模型把 `[<分類>]` 的**名稱**當成值，
   輸出 `[詞性] 名・他サ`。Task 8.4 的驗證發現的。

### 移除了語義保底檢查——5 次誤殺、0 次真陽性

`prompt` 階段原本用「場景與產出的內容詞重疊率」擋語義偏離。門檻從 0.5 調到 0.3
再到 0.15 都擋不住誤殺，最後移除。三個缺陷是詞袋比對的本質問題：

- **同義替換**：`gesturing` 寫成 `hand outstretched as if presenting`
- **短詞被排除**：`ant` 只有三個字母不算內容詞，畫螞蟻的卡重疊率 0%
- **詞形變化**：`tasting` 與 `tastes` 在字面比對下是兩個詞

五次誤殺全部留成回歸測試。要真的判斷語義偏離需要語意向量，
為一個沒抓到過真陽性的檢查不值得。

### 兩件計畫寫錯、實作時修正的事

1. 計畫說「舊 CSV 可直接讀、不需要遷移腳本」——**錯的**。`CardStore.read()` 會逐欄
   比對表頭，這道檢查在 `from_csv_row()` 的容錯之前，舊檔加完欄位後全部讀不進來。
   遷移是必要的，並加了測試把這件事釘住。
2. 計畫的 Task 8.4 要把〈補全規則〉併進 `EnrichAgent` 並讓它常駐。計畫自己要求的
   diff 驗證推翻了它：常駐會改動原文（詞性標記重排、擅自轉繁體、刪掉 OCR 雜訊行），
   而且實產資料上 `example` 空欄是 **0 張**，「例句補全」根本沒有需求。
   走計畫預先寫好的退路：`MaterialTypeAgent` 留著、enrich 維持條件觸發。

### 驗證方法上踩到的一個坑

第一次驗證 FLUX profile 時，我寫在 `image_prompt_flux.md` 裡的範例直接用了測試卡的
真實場景，**模型逐字照抄範例**，3 張裡有 2 張的「產出」其實是範例回音。
範例換成不會出現在真實資料裡的場景後重跑才拿到獨立證據。
寫 few-shot 範例時要避開會實際出現的輸入。

### 下次重生時順手做、但不值得為它單獨重跑的事

`prompts/image_scene.md` 的禁用道具表補 `thermometer`（刻度儀器招數字）、
`projection`（表裡只有 `screen`），並強化 `calendar` 那一條——實測它被違反了兩次。

### 一個環境上的坑

兩副牌組的 `card_id` 前綴相同（都是 `p1_xxx`）又共用同一個 `WORK_DIR` 時，
後跑的會覆蓋前一副的媒體檔。這是「一個服務綁一個 `WORK_DIR`」那個決定的實際代價。
本次重跑前已把日文那 13 張連同媒體備份到 `work/backup-japanese-2026-09-01/`。
