# 聯想圖設計原則

本檔記錄聯想圖的**設計判準與實測結論**，是給人讀的參考，**不是任何模型的指令檔**。

> **Phase 8 起本檔不再是「權威定義處」。** 聯想圖分成兩層，各有自己的指令檔：
>
> | 層 | 指令檔 | 內容 |
> |----|--------|------|
> | 語義層（`scene` 階段） | `prompts/image_scene.md` | 模型無關的場景。本檔的〈避開會帶文字的道具〉與〈抽象條目怎麼畫〉就是它的來源 |
> | 語法層（`prompt` 階段） | `prompts/image_prompt_flux.md`／`image_prompt_sdxl.md` | 目標語法、LoRA 觸發詞、風格後綴，**一個 profile 一份** |
>
> 統一風格後綴現在**只存在於 SDXL profile 那一份檔案裡**。Phase 8 之前它同時
> 抄在四個檔案中、靠人力維持一致，那個同步負擔已經消失——改風格就是改那一份。

---

## 設計目標

聯想圖是**記憶錨點**，不是插圖。判準只有一個：看到圖能不能想起這張卡的內容。

| 要求 | 理由 |
|------|------|
| 盡量**壓低文字出現率** | 圖上有字就變成「讀字」而非「回憶」，記憶錨點失效；且模型寫出的外文字幾乎都是亂碼。**做不到零出現**（見〈已實測的天花板〉），目標是壓低比例 |
| 具體場景，不要抽象符號 | 具體畫面才記得住，抽象色塊與幾何圖形無法對應到語義 |
| 整套卡片風格一致 | 風格跳動會分散注意力；一致的視覺基調讓牌組看起來像同一套教材 |
| 16:9 橫幅構圖 | 對應記憶引擎的卡片版面（1344 × 768，SDXL 的 16:9 官方建議尺寸） |

---

## 兩層結構

CSV 有兩欄，各由一個階段產生：

| 欄位 | 誰產生 | 換文生圖模型時 |
|------|--------|----------------|
| `image_scene` | `scene` 階段 | **不動**。它是模型無關的場景語義，人工編修過的也保留 |
| `image_prompt` | `prompt` 階段 | 重生。它是送進 ComfyUI 的**完整**字串，中間沒有任何加工 |

語義層長這樣（英文小寫、逗號分隔片語、無句號、**不含任何風格詞與觸發詞**）：

```
a tiger standing among a family of housecats, taxonomy mood
```

語法層依 profile 而異：

| profile | 產出 |
|---------|------|
| `ImagePromptFluxAgent` | `Anime. A tiger stands among a family of housecats… Soft cinematic light, muted colors, gentle shadows.` |
| `ImagePromptSDXLAgent` | `a tiger standing among a family of housecats, taxonomy mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark` |

SDXL 那串後綴是為 SD 1.5 寫的，換到動漫 SDXL（`fabricatedXL`）後**實測仍是最佳解**
——改動它的四次嘗試都更差，見下方〈實測否決的調整〉。**它現在只住在
`prompts/image_prompt_sdxl.md` 一個地方。**

---

## 負向 prompt

> ⚠️ **只對 SDXL 那組有效。** kyoani workflow 的 cfg 是 1、負向接
> `ConditioningZeroOut`，負向條件根本不參與取樣——在那組**調負向詞是白調**
> （2026-08-28 實測）。

由 `.env` 的 `COMFYUI_NEGATIVE_PROMPT` 提供，預設值：

```
bad quality, worst quality, worst detail, low quality, lowres, jpeg artifacts, blurry,
sketch, bad anatomy, bad hands, extra fingers, fused fingers, missing fingers, deformed,
disfigured, mutated, bad proportions, extra limbs, missing limbs, long neck, cropped, error,
text, letters, words, caption, subtitle, signature, watermark, username, artist name
```

前段是畫質與解剖負向詞，後段刻意涵蓋各種文字相關詞彙。要調整風格可以改這個變數，
但 `text, letters, words, caption, subtitle, signature, watermark` **這七個詞不要移除**。

> 這串由 `fabricatedXL` 這類動漫 SDXL 慣用的負向詞（`worst detail`、`sketch`）
> 與本專案的防文字詞合併而來，已去重。合併時刻意**不收**風格鎖定詞
> （`cartoon, anime, 3d render` 等）——記憶錨點圖常常誇張、卡通化更好記，
> 把那些鎖進負向會斷掉這條路。

---

---

## 避開會帶文字的道具

**這是「畫面不得含文字」的治本做法。** 負向 prompt 只能從反面壓，而
FLUX 系列這類蒸餾模型根本沒有真正的負向可用；靠 `no text` 這種否定敘述
在任何擴散模型上都不可靠。真正有效的是**一開始就別要求會寫字的東西**。

實測 308 張卡的 prompt，**61 張（20%）**含下列道具：

| 道具 | 出現次數 | 換成 |
|------|----------|------|
| screen（螢幕） | 10 | 人物專注的表情與手勢，畫面外的光源 |
| book（書、書封） | 8 | 闔上的書堆、翻頁的手、書脊朝內 |
| clock（時鐘） | 8 | 窗外光線的角度、拉長的影子 |
| calendar（日曆） | 6 | 窗外的季節變化、桌上的節令物件 |
| paper／document（文件） | 10 | 空白紙張、資料夾、被壓在鎮紙下的紙 |
| sign（招牌、標示） | 5 | 建築物本身的形狀、箭頭形的路徑 |
| newspaper（報紙） | 4 | 攤開的空白版面、捲起的紙筒 |
| chart（圖表） | 4 | 實物的大小對照、堆疊高度的差異 |
| board（看板、白板） | 3 | 空白板面配上手勢，或改畫該概念的實物 |

原則：**要表達「資訊」時，畫出資訊的載體會逼模型寫字**。改畫「人對這個資訊的反應」
或「該概念的實物對照」，兩者都不需要一個字。

> 寧可場景更抽象一點，也不要出現字。圖上有字就變成「讀字」而非「回憶」。

## 已實測的天花板（2026-09-01）

**零出現做不到，不要再往這個方向投入。** 三種手段都試過：

| 手段 | 結果 |
|------|------|
| 正向後綴寫 `no text, no letters, no watermark` | SDXL 有部分效果；kyoani（cfg 1）**完全無效** |
| 負向 prompt 加滿防文字詞 | kyoani 的負向接 `ConditioningZeroOut`，根本不參與取樣 |
| **不要求會帶字的道具**（本檔的做法） | **唯一有效的**：同 13 張卡實測，違規場景由 4/13 降到 1/13 |

即使做到第三項，模型仍會**憑空加上場景沒要求的道具再往上寫字**——
2026-09-01 的 A/B 中，`amusing` 的場景只寫「舞台上的喜劇演員」，
兩組都自己生出一面投影幕並寫上亂碼。那不是 prompt 端能控制的。

所以判準改為**降低出現率**：A/B 實測 2/13 的卡片含假文字，兩種語法完全相同。
再往下壓需要改模型或改 workflow，不是改 prompt。

## 實測否決的調整（2026-08-26，fabricatedXL / SDXL）

換 workflow 時試過四種「看起來更對」的調整，**四種都更差**，記在這裡免得再走一次：

| 嘗試 | 加了什麼 | 實測結果 |
|------|----------|----------|
| 動漫品質標籤（完整） | `anime illustration, clean lineart, soft cel shading, masterpiece, best quality, absurdres` | 模型被拉向**角色特寫**：「在桌前完成任務的人」變成少女大頭照；「老虎站在貓群中」變成一排上班族。且偶發 91 秒的異常慢速 |
| 動漫品質標籤（精簡） | `anime illustration, masterpiece, best quality` | 同上，仍是特寫人像；老虎變成虎頭人 |
| 廣角指令 | `wide establishing shot, full scene` | **主體整個消失**：只剩空教室、空走廊，人與動物都不見了 |
| 負向加 `close-up portrait, headshot` | — | 同上，把主體推出畫面 |

原因推測：`masterpiece / best quality / absurdres` 在動漫模型的訓練資料裡與**單人立繪**
高度相關，加了等於在要求角色圖；而 `wide shot` 類的詞會把主體縮到看不見。
記憶錨點圖要的是「**具體場景裡的具體主體**」，兩邊都不能偏。

**結論：後綴維持原樣。** 換模型要調的是負向 prompt 與解析度，不是這段風格後綴。

## 抽象條目怎麼畫

抽象條目最容易產出無意義的圖。轉換方式：

| 條目性質 | 轉法 | 例 |
|----------|------|-----|
| 抽象關係 | 畫出關係的具體實例 | 「屬於」→ 老虎站在貓科動物之中 |
| 程度／數量 | 畫出對比 | 「増大」→ 同一容器前後水位落差 |
| 動作 | 畫出正在執行該動作的人 | 「測量」→ 檢測員拿儀器量身體數值 |
| 情態副詞 | 畫出符合該情態的動作瞬間 | 「そうっと」→ 有人踮腳輕推一扇門 |
| 邏輯連接 | 畫出兩個場景的因果對照 | 「それでも」→ 大雨中仍撐傘前行的人 |

畫不出來時，寧可畫一個**與該條目典型例句對應的場景**，也不要退回抽象符號。
