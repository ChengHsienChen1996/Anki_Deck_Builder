# 改寫聯想圖 prompt

你的任務：把一張卡片既有的 `image_prompt` 改寫成**不會讓圖像模型寫出文字**的版本。

輸入是一張卡的資訊與它目前的 prompt。輸出**只有一行**：改寫後的 prompt，
不要任何說明、引號、編號或前後綴文字。

---

## 為什麼要改

圖像模型看到「螢幕、書封、日曆、招牌、圖表、文件」這類道具，就會自己在上面寫字。
記憶錨點圖一旦出現文字，使用者就變成「讀字」而不是「回憶」，這張卡的圖就失效了。
負向 prompt 擋不乾淨，**唯一有效的做法是一開始就不要求這些東西**。

## 改寫規則

1. **保留原本的核心語義**——改寫後的畫面仍要能讓人想起這個條目。不要換題材。
2. **拿掉所有會帶出文字的道具**，換成等價但無字的畫面：

   | 不要用 | 改用 |
   |--------|------|
   | screen、monitor、phone display、laptop screen | 人物專注的表情與手勢、畫面外的光源 |
   | book cover、open book、magazine、newspaper、notebook | 闔上的書堆、翻頁的手、攤開的空白版面 |
   | calendar、clock、watch face | 窗外的季節變化、光線角度、拉長的影子 |
   | sign、signboard、poster、banner、label、nameplate | 建築物本身的形狀、箭頭形的路徑 |
   | chart、graph、diagram、whiteboard、blackboard | 實物的大小對照、堆疊高度的差異 |
   | document、form、ticket、receipt、certificate | 空白紙張、資料夾、被鎮紙壓住的紙 |
   | symbol、logo、card、badge、stamp、envelope、map、menu | 該概念的實物本身、人的動作、空間的形狀 |

   要表達「資訊」時，畫**人對這個資訊的反應**或**該概念的實物對照**。
3. 若原 prompt 的氛圍片語含 `chart`、`diagram` 這類詞，一併換掉。
4. 全部英文小寫，以半形逗號分隔片語，不使用句號。
5. **結尾必須原樣接上這段統一風格後綴**：

   ```
   , cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
   ```

6. 原 prompt 若本來就沒有這類道具，**原樣輸出即可**，不要為改而改。

## 範例

輸入的條目是 `absent（缺席）`，原 prompt：

```
an empty classroom desk with a calendar marking an absence, quiet and missing mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
```

輸出：

```
an empty classroom desk with sunlight falling on the vacant chair beside occupied ones, quiet and missing mood, cinematic lighting, muted color palette, soft shadows, atmospheric, no text, no letters, no watermark
```

日曆換成了「空位與旁邊坐滿的座位」——同樣傳達「缺席」，但畫面裡沒有任何要寫字的東西。
