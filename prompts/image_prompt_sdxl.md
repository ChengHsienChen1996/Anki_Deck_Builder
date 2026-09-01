# 聯想圖 prompt — fabricatedXL / SDXL（回頭路 profile）

輸入是一句英文的**場景語義**（模型無關）。你的任務：把它接上這個模型要的風格後綴。

輸出**只有一行**。不要任何說明、引號、編號、標題或前後綴文字。

---

## 這份指令刻意什麼都不改

SDXL 吃的就是逗號分隔的片語串，而輸入的場景語義**已經是那個格式**。
所以這裡的工作只有一件：**把場景原樣抄下來，接上固定後綴。**

不要改寫、不要潤飾、不要重排、不要換詞、不要加東西、不要刪東西。
場景寫什麼就抄什麼，一個字都不要動。

## 規則

1. **場景原樣照抄**，作為輸出的開頭。
2. 場景結尾若有逗號，去掉。
3. **結尾原樣接上這段統一風格後綴**，一字不差：

   ```
   , cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
   ```

4. 全部小寫、以半形逗號分隔、不使用句號。
5. 不要加觸發詞——這個 profile 用的是 fabricatedXL，沒有需要觸發詞的 LoRA。

後綴的前四項決定視覺基調，後三項是「不得含文字」的正面約束。
`COMFYUI_NEGATIVE_PROMPT` 會從反面再擋一次；這個 profile 兩邊都要有。

> 這段後綴是為 SD 1.5 寫的，換到動漫 SDXL（fabricatedXL）後**實測仍是最佳解**——
> 改動它的四次嘗試都更差，見 `prompts/image_prompt_template.md`〈實測否決的調整〉。
> **不要再試一次。**

---

## 範例

輸入：

```
a lighthouse keeper climbing a spiral staircase with a lantern, solitary mood
```

輸出：

```
a lighthouse keeper climbing a spiral staircase with a lantern, solitary mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
```

輸入：

```
a wilted potted plant beside a thriving one on a windowsill, neglect mood
```

輸出：

```
a wilted potted plant beside a thriving one on a windowsill, neglect mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
```
