# ComfyUI workflow 更新手冊

**適用情境**：你在 ComfyUI 裡改了 workflow（換 LoRA、調參數、加節點），
匯出成 `workflows/kyoani_fulx2.json`，現在要把它變成本專案實際使用的
`workflows/card_image_kyoani.json`。

**為什麼不能直接用匯出的檔案**：本專案對它做了四處固化改動，
匯出檔每次都會把那四處蓋回原值。這份手冊就是那四處的清單與理由。

---

## 兩個檔案的分工

| 檔案 | 誰維護 | 用途 |
|------|--------|------|
| `workflows/kyoani_fulx2.json` | **你**（從 ComfyUI「Save (API Format)」匯出） | 來源檔。改 workflow 時只動這一份 |
| `workflows/card_image_kyoani.json` | 套用下方四個補丁後產生 | **`.env` 實際指向的檔案** |

> 匯出時務必選 **Save (API Format)**，不是一般的 Save。
> 一般 Save 存的是畫布格式（含節點座標與連線），本專案讀不了。

---

## 四個補丁

改完 workflow 後，四個都要重新套用。

### 補丁 1 — 節點 `100` 的 `sage_attention` 改成 `auto`

```json
"100": { "inputs": { "sage_attention": "auto", ... } }
```

**理由是失敗模式的嚴重度不對稱，不是效能。** 具名值（`sageattn_qk_int8_pv_fp16_triton`
等）一旦對不上硬體，`sageattn_qk_int8_pv_fp8_cuda` 那兩個會**直接打死整個 ComfyUI 行程**
（要靠容器重啟才活），而 `auto` 永遠退回這張卡跑得動的 kernel。

2026-08-28 實測（`sageattention` 2.2.0）：`auto` 與兩個具名值熱機速度差距在 0.5 秒內
＝雜訊，畫面也一致。所以這一改不影響產出，改的是「設定被複製到別台機器時會怎麼壞」。

### 補丁 2 — 節點 `97` 的 `steps` 改成 `12`

```json
"97": { "inputs": { "steps": 12, ... } }
```

匯出檔通常是 `9`。第四輪 A/B 實測：12 步的肉、人臉、黑板算式都更清楚，
代價是每張 12.9s → 16.6s。見
[logs/2026-08-27_eval_flux2-klein-9b-ab.md](../logs/2026-08-27_eval_flux2-klein-9b-ab.md)。

### 補丁 3 — 新增孤兒節點 `999`

```json
"999": {
  "inputs": { "text": "", "clip": ["92", 0] },
  "class_type": "CLIPTextEncode",
  "_meta": { "title": "CLIP Text Encode (Negative, 孤兒節點，不執行)" }
}
```

**`999` 不是打錯，是刻意的。** 這份 workflow 的 `CFGGuider`(94) cfg 是 1、
負向接 `ConditioningZeroOut`(95)，負向條件根本不參與取樣——**這組設定下調負向詞是白調**。

但 `.env` 的 `COMFYUI_NEGATIVE_NODE_ID` 是必填（`workflow.validate()` 會檢查），
所以給它一個不被輸出節點依賴的節點當標的。ComfyUI 不會執行它，
存在的唯一理由是讓必填檢查過關。

`clip` 要接 CLIPLoader 的節點 ID（目前是 `92`）——**如果你的 workflow 換了 CLIPLoader
的編號，這裡要跟著改**。

### 補丁 4 — 清空節點 `96` 的 `text`

```json
"96": { "inputs": { "text": "", "clip": ["92", 0] }, ... }
```

匯出檔會帶著你在 ComfyUI 裡最後一次測試用的 prompt。每次生成時
`prompt` 階段產出的 `image_prompt` 都會覆蓋它，留著只會誤導讀檔的人。

---

## 操作步驟

```bash
# 1. 在 ComfyUI 改好 workflow，Save (API Format) 匯出，覆蓋 workflows/kyoani_fulx2.json

# 2. 先看差異：確認只有你預期的改動，以及四個補丁被蓋回原值
python3 - <<'PY'
import json
new = json.load(open("workflows/kyoani_fulx2.json"))
cur = json.load(open("workflows/card_image_kyoani.json"))
print("新增節點:", sorted(set(new) - set(cur)))
print("消失節點:", sorted(set(cur) - set(new)), "（999 應該在這裡，那是補丁 3）")
for k in sorted(set(new) & set(cur), key=int):
    ia, ib = cur[k].get("inputs", {}), new[k].get("inputs", {})
    for f in sorted(set(ia) | set(ib)):
        if ia.get(f) != ib.get(f):
            print(f"[{k}] {new[k]['class_type']}.{f}: {str(ia.get(f))[:50]!r} → {str(ib.get(f))[:50]!r}")
PY

# 3. 套用四個補丁
python3 - <<'PY'
import collections, json
src = json.load(open("workflows/kyoani_fulx2.json"), object_pairs_hook=collections.OrderedDict)
prev = json.load(open("workflows/card_image_kyoani.json"))

src["100"]["inputs"]["sage_attention"] = "auto"   # 補丁 1
src["97"]["inputs"]["steps"] = 12                 # 補丁 2
src["999"] = prev["999"]                          # 補丁 3（沿用上一版的孤兒節點）
src["96"]["inputs"]["text"] = ""                  # 補丁 4

json.dump(src, open("workflows/card_image_kyoani.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
open("workflows/card_image_kyoani.json", "a", encoding="utf-8").write("\n")
print("已固化，節點數:", len(src))
PY

# 4. 驗證注入點對得上
uv run python -c "
from anki_deck_builder.clients.comfyui_client import ComfyUIClient
from anki_deck_builder.config import load_settings
ComfyUIClient(load_settings().comfyui).validate()
print('validate() 通過')"

# 5. 生一張圖確認能跑（用一份暫時的工作檔，不要動 work/cards.csv）
```

---

## 節點 ID 變了怎麼辦

`.env` 用到六個節點 ID。第 2 步的差異輸出如果顯示這些節點消失或改編號，
`.env` 要跟著改：

| `.env` 變數 | 目前值 | 對應節點 |
|-------------|--------|----------|
| `COMFYUI_POSITIVE_NODE_ID` | `96` | 正向 `CLIPTextEncode` |
| `COMFYUI_NEGATIVE_NODE_ID` | `999` | 補丁 3 的孤兒節點 |
| `COMFYUI_SEED_NODE_ID` | `90` | `RandomNoise` |
| `COMFYUI_WIDTH_NODE_ID` | `88` | 寬度 `PrimitiveInt` |
| `COMFYUI_HEIGHT_NODE_ID` | `89` | 高度 `PrimitiveInt` |
| `COMFYUI_OUTPUT_NODE_ID` | `109` | `SaveImage` |

`validate()` 失敗時，錯誤訊息會指出是哪個變數對不上，照著改即可。

> **寬高是兩個獨立的 `PrimitiveInt`（`88`／`89`），不是單一 latent 節點的兩個欄位**，
> 所以 `COMFYUI_LATENT_NODE_ID` 留空。這兩個值同時餵給 `EmptyFlux2LatentImage`(87)
> 與 `Flux2Scheduler`(97)，不要在 workflow 裡寫死——尺寸必須留在 `.env` 手上。

---

## 改完之後要重生什麼

換 workflow／LoRA／步數會改變**圖**，但不影響 prompt：

```bash
anki-builder image --force    # 只要這一個
anki-builder pack
```

**不要**跑 `scene --force` 或 `prompt --force`——那兩層與文生圖模型的**權重**無關，
重跑只是浪費時間並可能讓場景變動。

> 只有**換文生圖模型本身**（例如 FLUX 換回 SDXL）才需要 `prompt --force`，
> 因為那會換 `IMAGE_PROMPT_AGENT`，語法要跟著換。見
> [usage.md 的〈語法層 profile〉](usage.md)。

**必須 `--force` 全部重生**：只補未完成的會讓兩種畫風混在同一副牌組。

---

## 不要改的東西（已實測否決）

| 項目 | 為什麼 |
|------|--------|
| 尺寸往上加 | 1440×900 與 1600×896 都更暗更糊，加步數也修不掉；1600×896 還會糊出假日文 |
| 負向 prompt 的內容 | cfg 1 ＋ `ConditioningZeroOut`，這組設定下完全無效 |
| `100` 的 `allow_compile` | 開關對熱機速度量不出差別，只在冷啟多花約 80 秒編譯。中性，沒有偏離原值的必要 |
| 風格後綴 | 四種調整全部實測更差，見 [prompts/image_prompt_template.md](../prompts/image_prompt_template.md)〈實測否決的調整〉 |

作者另提 `euler_ancestral`／`res_multistep` 兩個 sampler 可試——**沒測過**。
要試的話照〈操作步驟〉走，並記得先在少量卡片上比較。

---

## 已知的參數陷阱：修復 LoRA 的 strength

2026-09-02 加入 `klein_fixer_slider.safetensors`（節點 `127`）修四肢畸形。
在 3 張卡上比較四個強度，結果是**單調的**：

| strength | 四肢 | 構圖 |
|----------|------|------|
| 0（不掛） | 手部經常糊成一團 | 主體大、場景清楚 |
| **1** | **明顯改善** | **與不掛時幾乎相同** ← 建議值 |
| 2 | 好 | 鏡頭明顯拉遠，主體變小 |
| 3 | 好 | 鏡頭再拉遠，動作消失（「打包行李」變成「地上一個包」） |

**強度越高，鏡頭拉得越遠。** 四肢變乾淨有一部分是因為手變小了——
這正是本專案已實測否決的失敗模式（`wide establishing shot` → 主體整個消失，
見 `prompts/image_prompt_template.md`〈實測否決的調整〉）。

記憶錨點圖要的是「具體場景裡的具體主體」，所以**這類 slider 型 LoRA 寧可調低**。
比較圖在 `work/ab-klein-fixer/strength_cmp.png`（不進版控）。
