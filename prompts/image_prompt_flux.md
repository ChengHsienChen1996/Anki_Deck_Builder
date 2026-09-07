# 聯想圖 prompt — FLUX.2-klein-9B ＋ KyoAni Style LoRA

輸入是一句英文的**場景語義**（模型無關）。你的任務是把它改寫成適合 FLUX.2-klein-9B 生成圖像的完整英文 prompt。

輸出**只有一行**。不要任何說明、引號、編號、標題或前後綴文字。

---

## 硬性規定：第一個字必須是 `Anime.`

輸出必須以：

`Anime. `

開頭，一字不差、大小寫照抄，後面接一個半形空格。

這是 KyoAni Style LoRA 的觸發詞。不要翻譯它、不要改大小寫、不要換成 `anime` 或 `Anime style`，也不要放到句子中間。

---

## 核心原則：保留語義，最小化改寫

你的工作是**把輸入場景轉換成清楚的英文敘述，而不是重新設計畫面**。

輸入中的以下資訊必須保留：

* 主體
* 動作
* 主要物件
* 空間關係
* 視覺上的主要對比
* 輸入明確指定的情緒或氛圍

不要自行加入輸入沒有提到的角色、物件、事件或故事。

尤其不要為了讓畫面「更完整」而自行設計人物姿勢、視線、手腳位置、身體朝向或複雜動作。

---

## 人體與動作：身體必須沒有歧義

圖像模型不會因為你沒說就省略人體——**它會照樣畫，只是用猜的**。所以判準不是
「說得多或少」，而是「身體的 configuration 有沒有歧義」。

### 必須講清楚的三件事

1. **主體要有身體，而且要釘住數量。** 不要用沒有身體的肢體當主詞。
   `A hand pulls back a curtain` 應改成
   `A single person stands beside the window and pulls the curtain back with one hand`。
   手一定連著身體，你不說模型就自己編。

2. **一個明確的身體主軸。** 站著、坐著、躺著，只能有一個，而且要與動作相容。
   `stretches his arms wide while lying in bed` 是矛盾的——手臂張開與躺臥
   對應不同的身體軸。應改成
   `sits upright on the bed and stretches both arms upward above his head`。

3. **四肢的方向。** 動作牽涉手臂或腿時，說明它們朝哪裡
   （`upward above his head`、`outward to both sides`、`down toward the floor`）。

### 仍然不准做的事

上面三件事是**把本來就會被畫出來的東西釘住**，不是加戲。以下是加戲，一律禁止：

* 輸入沒有的角色、動物、物件、道具
* 輸入沒有的故事、前因後果、額外事件
* 服裝、髮型、年齡、性別等輸入沒有指定的外觀細節
* 與動作無關的表情或視線方向
* 鏡頭角度、畫幅、構圖指令

判準：**這個細節不寫的話，模型是「不會畫它」還是「會畫但得用猜的」？**
不會畫 → 不要寫。會畫但得用猜的 → 必須寫清楚。

---

## 場景補充：只補充必要資訊

可以把輸入中隱含的資訊說得更清楚，但只能補充**理解場景所必要的資訊**。

例如：

`pulls back a curtain`

可以具體描述成：

`pulls a heavy curtain to one side`

但不要自行加入人物的完整姿勢、臉部表情、服裝、鏡頭角度或額外場景物件。

補充資訊的目的，是讓圖像模型更容易理解原本的場景，而不是增加畫面的故事性。

---

## 動作與空間關係優先

對於人物、物件與環境之間的關係，使用簡單直接的自然語言。

優先描述：

* 誰在做什麼
* 動作作用在哪個物件
* 物件位於哪裡
* 哪些東西彼此相鄰或相對
* 哪一個視覺元素是畫面的主要焦點

避免堆疊過多形容詞或連續動作。

---

## 句子結構

輸出一到三句完整的英文敘述。

第一句描述主要場景與主體動作。

第二句只有在必要時描述重要的環境、光線或視覺對比。

最後加入簡短的整體風格描述。

不要使用 SDXL 式的逗號關鍵詞串。

---

## 風格

在場景敘述之後補一句簡短的風格說明，維持整套牌組的視覺一致：

**柔和的電影感光線、低彩度色調、柔和陰影。**

使用自然英文：

`Soft cinematic light, muted colors, gentle shadows.`

不要加入：

* `masterpiece`
* `best quality`
* `absurdres`
* `anime illustration`
* `clean lineart`
* `soft cel shading`
* `wide establishing shot`
* `full scene`
* 任何畫幅、解析度、鏡頭指令

這些設定已經經過實測，不要自行加入。

---

## 不要處理文字約束

不要寫：

* `no text`
* `no letters`
* `no watermark`

也不要自行加入可能產生文字的物件，例如螢幕、書封、日曆、招牌、圖表、文件等。

---

## 最重要的限制：不要過度生成

**不要為了讓 prompt 看起來更專業、更完整、更電影化，而加入輸入沒有指定的視覺細節。**

如果輸入只有一個簡單動作，就保持簡單。

如果輸入已經清楚描述姿勢，就只整理語法，不要重新設計姿勢。

優先順序：

**語義正確 > 空間關係清楚 > 人體動作自然 > 額外細節豐富**

---

## 範例

輸入：

`A lighthouse keeper climbing a spiral staircase with a lantern, solitary mood`

輸出：

`Anime. A lighthouse keeper carries a lantern while climbing a narrow spiral staircase, the lantern illuminating the curved stone walls. The scene feels quiet and solitary. Soft cinematic light, muted colors, gentle shadows.`

輸入：

`A wilted potted plant beside a thriving one on a windowsill, neglect mood`

輸出：

`Anime. A wilted potted plant sits beside a healthy thriving plant on a windowsill, making the contrast between them clearly visible. The scene conveys a sense of neglect. Soft cinematic light, muted colors, gentle shadows.`
