# 聯想圖 prompt 風格模板

本檔定義所有卡片聯想圖共用的**視覺風格規範**，是風格後綴與負向 prompt 的權威定義處。

> **同步注意**：`prompts/extract_cards.md` 的 `image_prompt` 一節內嵌了下方的統一風格後綴
> ——抽取模型只讀得到那一份檔案，所以必須把字串複製過去。**改這裡就要一併改那裡**，
> 兩處不一致會讓同一批卡片的圖風格分裂。

---

## 設計目標

聯想圖是**記憶錨點**，不是插圖。判準只有一個：看到圖能不能想起這張卡的內容。

| 要求 | 理由 |
|------|------|
| 畫面**不含任何文字** | 圖上有字就變成「讀字」而非「回憶」，記憶錨點失效；且模型寫出的外文字幾乎都是亂碼 |
| 具體場景，不要抽象符號 | 具體畫面才記得住，抽象色塊與幾何圖形無法對應到語義 |
| 整套卡片風格一致 | 風格跳動會分散注意力；一致的視覺基調讓牌組看起來像同一套教材 |
| 16:9 橫幅構圖 | 對應記憶引擎的卡片版面（1024 × 576） |

---

## Prompt 結構

```
<場景描述>, <氛圍片語（選填）>, <統一風格後綴>
```

- 全部小寫英文，以半形逗號分隔片語，不使用句號。
- **場景描述**：能畫出來的具體畫面，用來承載條目的核心語義。
- **氛圍片語**：一到兩個詞的情境提示，例如 `taxonomy chart atmosphere`、
  `clinical precision mood`、`motion blur`。可省略。

### 統一風格後綴（權威定義）

```
, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
```

前四項決定視覺基調，後三項是**不得含文字**的正面約束。
`COMFYUI_NEGATIVE_PROMPT` 會從反面再擋一次；兩邊都要有，單靠一邊擋不乾淨。

### 完整範例

| 條目 | image_prompt |
|------|--------------|
| 属する（屬於） | `a tiger standing among a family of cats, taxonomy chart atmosphere, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark` |
| 続々（紛紛） | `a crowd of people streaming continuously through a stadium entrance gate, motion blur, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark` |
| 測定（測量） | `a fitness examiner measuring an athlete with instruments in a gym, clinical precision mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark` |

---

## 負向 prompt

由 `.env` 的 `COMFYUI_NEGATIVE_PROMPT` 提供，預設值：

```
text, watermark, signature, letters, words, caption, subtitle
```

刻意涵蓋各種文字相關詞彙。要調整風格可以改這個變數，但**上列七個詞不要移除**。

---

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
