# 聯想圖 prompt — FLUX.2-klein-9B ＋ KyoAni Style LoRA

輸入是一句英文的**場景語義**（模型無關）。你的任務：把它改寫成這個模型要的完整 prompt。

輸出**只有一行**。不要任何說明、引號、編號、標題或前後綴文字。

---

## 硬性規定：第一個字必須是 `Anime.`

輸出**必須**以下面這五個字元開頭，一字不差、大小寫照抄、後面接一個半形空格：

```
Anime.
```

這是 KyoAni Style LoRA 的觸發詞。少了它，LoRA 掛了等於沒掛——
2026-08-27 的評估第二輪就是這樣白跑一輪。不要翻譯它、不要改大小寫、
不要換成 `anime` 或 `Anime style`、不要放到句子中間。

## 這個模型要自然語言，不要 tag 串

FLUX.2 系列吃的是**流暢的英文敘述句**，不是 SDXL 那種逗號分隔的關鍵詞串。
把場景寫成一到三句完整的話，像在向人描述一張照片。

| 不要這樣（tag 式） | 要這樣（敘述式） |
|---|---|
| `a blacksmith hammering a glowing bar, forge mood, cinematic lighting, muted palette` | `Anime. A blacksmith brings a hammer down on a glowing bar of metal, sparks scattering across the dim workshop. Soft cinematic light, muted colors, gentle shadows.` |

## 風格

在敘述之後補一句簡短的風格說明，維持整套牌組的視覺一致：
**柔和的電影感光線、低彩度色調、柔和陰影。** 用自然的英文寫，不要寫成 tag 串。

**不要加下列東西**，四種都已實測更差
（見 `prompts/image_prompt_template.md`〈實測否決的調整〉，**不要再試一次**）：

- `masterpiece`、`best quality`、`absurdres` 這類品質標籤 → 模型被拉向單人角色特寫
- `anime illustration`、`clean lineart`、`soft cel shading` → 同上
- `wide establishing shot`、`full scene` → 主體整個消失，只剩空景
- 任何畫幅、解析度、鏡頭指令

## 不要處理文字約束

**不要**寫 `no text`、`no letters`、`no watermark` 這類否定敘述。

兩個理由：這份 workflow 的 cfg 是 1、負向接 `ConditioningZeroOut`，
負向條件根本不參與取樣（2026-08-28 實測，**調負向詞是白調**）；
而在正向裡寫否定敘述，擴散模型只會把那些概念加進條件，幫倒忙。

防文字已經在上游治本了——語義層（`prompts/image_scene.md`）就不會給你
會帶出文字的道具。你只要**不要自己加**螢幕、書封、日曆、招牌、圖表、文件之類的東西。

## 保留原場景

改寫是**換語法，不是換題材**。輸入場景裡的主體、動作、對比關係全部要留著。
不要換成別的畫面、不要加輸入沒有的角色或物件、不要刪掉關鍵對比
（例如「大小差異」「前後落差」正是那張卡要記的東西）。

---

## 範例

輸入：

```
a lighthouse keeper climbing a spiral staircase with a lantern, solitary mood
```

輸出：

```
Anime. A lighthouse keeper carries a lantern up a narrow spiral staircase, the light throwing long curved shadows along the stone wall. Soft cinematic light, muted colors, gentle shadows.
```

輸入：

```
a wilted potted plant beside a thriving one on a windowsill, neglect mood
```

輸出：

```
Anime. On a windowsill, a drooping potted plant sits next to one growing thick and green, the difference between them impossible to miss. Soft cinematic light, muted colors, gentle shadows.
```
