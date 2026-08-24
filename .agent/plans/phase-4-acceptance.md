# Phase 4 驗收指令清單

日期：2026-08-25 ／ 分支：`dev_ai` ／ 對應 [phase-4-audio.md](phase-4-audio.md)〈驗收流程〉

八個步驟，依序執行。每步都標了「該看到什麼」與「要回報什麼」——
**第 3 步要你挑參考音檔、第 5 步需要 VRAM 實測數據。**

---

## 開始前

| 項目 | 目前狀態 |
|------|----------|
| 工作檔 | `work/cards.csv`，311 列（**308 張卡** + 3 列來源列） |
| `image_status` | 308／308 `done`（你 08-24 19:23–19:32 自己跑的，127 MB） |
| `audio_front_status`／`audio_back_status` | 全部 `pending`，`work/media/audio/` 已清空 |
| `tts_back_text` | 308／308 都有 |
| **`tts_front_text`** | **只有 192／308 有，116 張是空的**（見下方） |
| 參考音檔 | `assets/voice/` 兩支；`.env` 現指向 `ShirokamiFubuki_16_0.wav` |
| 併發 | `VOXCPM2_CONCURRENCY=1` |

先備份：

```bash
cp -p work/cards.csv work/cards.csv.bak
```

---

## 先決定一件事：116 張卡沒有 `tts_front_text`

照現在的實作，這 116 張的 `audio_front` 會全部標成 `failed`——文字來源為空就失敗，
是 phase-4-audio.md 明訂的行為。但這些卡的資料其實不缺：

```
p1_001  front='a/an'    reading=''  tts_front_text=''
p3_001  front='baby'    reading=''  tts_front_text=''
p3_002  front='bathtub' reading=''  tts_front_text=''
```

**116 張全部都是 `reading` 也為空的非日語詞條。** 而 `prompts/extract_cards.md`
對這個欄位的規範本來就寫著：

> - 有 `reading` 時填讀音（例：日語填假名 `ぞくする`）
> - **沒有讀音概念的領域填 `front` 本身**

也就是說規則存在、模型沒照做——與 Phase 2 日誌記的「參數比 prompt 規則有效得多
（對 8B 模型）」是同一個現象。

| 選項 | 代價 |
|------|------|
| **A. 在 audio 階段補退路**（建議）：`tts_front_text` 為空時依序退回 `reading`、`front` | 小改動，不必重跑 extract。語意上正確——「正面要唸的文字」在沒有讀音時本來就該是 `front` |
| B. 照現狀讓 116 張失敗 | 驗收會看到 116 個 failed，得手動補欄位或重跑 extract |
| C. 改 prompt 規範並重跑 extract | 成本最高，且重跑同一頁有 `card_id` 撞號的已知限制 |

**A 與 phase-4-audio.md 的「文字來源為空時標 failed」相牴觸**，所以我沒有自作主張。
你點頭我就改，改完這 116 張會唸英文單字本身。下面的步驟先照現狀寫。

> 另一項觀察，不影響驗收：**181／308 的 `tts_back_text` 含換行**（多個例句串在一起，
> 例 `'A dog\\nAn apple'`）。TTS 會一口氣唸完，聽起來像連續兩句話。要不要處理另行討論。

---

## 1. 單邊生成隔離性

```bash
uv run anki-builder audio --work work/cards.csv --side front
uv run anki-builder status --work work/cards.csv
```

**該看到**：只有「生成語音（單字）」一條進度條，結尾 `audio_front：處理 308 列…`，
**完全不會出現 `audio_back` 的字樣**。

推估耗時：模型載入約 28 秒 + 192 段合成（116 張因缺文字立即失敗，不耗 GPU），
每段約 1～2 秒，合計 **6～8 分鐘**。

**要確認**：

```bash
ls work/media/audio | grep -c _front.wav    # 應為 192（或 308，若採用了選項 A）
ls work/media/audio | grep -c _back.wav     # 應為 0
```

`status` 中 `audio_back` 應仍全部 `pending`。

---

## 2. 補齊另一邊

```bash
ls -la --time-style=full-iso work/media/audio/ > /tmp/front_before.txt

uv run anki-builder audio --work work/cards.csv --side back

ls -la --time-style=full-iso work/media/audio/ | grep _front.wav > /tmp/front_after.txt
diff <(grep _front.wav /tmp/front_before.txt) /tmp/front_after.txt && echo "front 未被重新生成 ✓"
```

**該看到**：只有「生成語音（例句）」的進度條；`diff` 沒有差異，代表已完成的
front 音檔一個都沒被動過。

推估耗時：308 段例句較長，約 **8～12 分鐘**。

---

## 3. 音檔內容檢查與參考音檔定案（★ 需要你的判斷）

先聽階段產物：

```bash
xdg-open work/media/audio/ &
```

**要確認三件事**：

1. `_front.wav` 唸的是單字本身、`_back.wav` 唸的是例句，沒有唸錯邊
2. **每張卡都是同一個聲音**（音色來自 `VOXCPM2_REFERENCE_WAV`）
3. 發音正確、語速合理、**沒有被截斷**

再比對兩支參考音檔。同一句話各生一次：

```bash
uv run python - <<'PY'
import asyncio, pathlib
from anki_deck_builder.config import load_settings
from anki_deck_builder.clients.tts_client import VoxCPMClient

TEXT = "虎はネコ科に属する。観客が続々と会場に入ってきた。"
OUT = pathlib.Path("/tmp/voice_compare"); OUT.mkdir(exist_ok=True)

async def main():
    s = load_settings()
    for wav in sorted(pathlib.Path("assets/voice").glob("*.wav")):
        s.tts.reference_wav = wav
        data = await VoxCPMClient(s.tts).synthesize(TEXT)
        (OUT / f"{wav.stem}.wav").write_bytes(data)
        print("已生成", OUT / f"{wav.stem}.wav")

asyncio.run(main())
PY
xdg-open /tmp/voice_compare &
```

**要回報**：選定哪一支。我會寫進 `.env` 與 `.env.example` 的註解，
另一支可以從 `assets/voice/` 移除。

> 若聽起來音色不像參考音檔，第一個要試的是 `VOXCPM2_NORMALIZE=true`
> （文字正規化，目前關閉），這是 phase-4-audio.md 列的待確認項之一。

---

## 4. 失敗獨立性

手動把某一列的 `tts_back_text` 清空（試算表或編輯器皆可），並把該列的
`audio_back_status` 改回 `pending`，然後：

```bash
uv run anki-builder audio --work work/cards.csv --only-failed
uv run anki-builder status --work work/cards.csv
```

**該看到**：該列 `audio_back_status` 為 `failed`、錯誤訊息寫著「沒有 tts_back_text」，
而**同一列的 `audio_front_status` 仍是 `done`**，音檔也還在。

---

## 5. VRAM 實測（★ 需要你的數據）

開一個終端機持續取樣：

```bash
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv -l 2
```

另一個終端機重跑一小批：

```bash
uv run python -c "
import asyncio
from anki_deck_builder.state import CardStore
async def m():
    rows = await CardStore('work/cards.csv').read()
    await CardStore('work/bench.csv').write([r for r in rows if r.card_id][:8])
asyncio.run(m())
"
uv run anki-builder audio --work work/bench.csv --force
```

**要回報**：

| 時點 | VRAM |
|------|------|
| audio 開始前（ComfyUI 是否常駐？） | |
| 模型載入後 | |
| 合成期間峰值 | |

參考值：我這邊量到載入後 **+6.3 GB**、推論期再約 +1 GB；ComfyUI 常駐約 2.3 GB。
三者（E4B 8.6 GB + ComfyUI 2.3 GB + VOXCPM2 6.3 GB + 桌面 1 GB）預估約 **18 GB／24 GB**。

**另需確認**：image 跑完後 ComfyUI 是否仍持有 VRAM、audio 有沒有因此變慢或 OOM。
若確實需要讓渡，沿用 Phase 3 的機制（`MODEL_UNLOAD_BEFORE_STAGE` 對 audio 已生效），
**不另設一套**。

---

## 6. 只重跑失敗

```bash
uv run anki-builder audio --work work/cards.csv --only-failed
```

**要確認**：只處理 `failed` 的子項，`done` 的音檔修改時間不變。

---

## 7. pack 驗證

```bash
# 缺檔
mv work/media/audio/p1_002_back.wav /tmp/
uv run anki-builder pack --work work/cards.csv --output /tmp/t.zip

# 零位元組
mv /tmp/p1_002_back.wav work/media/audio/
: > work/media/audio/p1_002_front.wav
uv run anki-builder pack --work work/cards.csv --output /tmp/t.zip
```

**該看到**：兩次都中止，訊息分別是

```
p1_002：語音 audio_back 指向的檔案不存在（…）
p1_002：語音 audio_front 指向的檔案是空的，0 位元組（…）
```

修好再繼續：

```bash
uv run anki-builder audio --work work/cards.csv --force --side front   # 或手動還原
```

---

## 8. 全流程端到端

```bash
uv run anki-builder run-all --work work/cards.csv --output output/deck.zip
unzip -l output/deck.zip | head -20
```

（工作檔已存在，不必給 `--input`；ocr／extract 的列都是 `done`，會直接跳過。）

**該看到**：`① ocr` → `② extract` → `③ image` → `④ audio_front` → `④ audio_back` → `⑤ pack`
依序出現，image 與 audio **未併行**。

**要確認**：

- ZIP 內含 `cards.csv`、`media/img/`、`media/audio/`
- 載入記憶引擎後，卡片正面顯示聯想圖與單字音訊、背面顯示例句音訊，**皆可播放**

推估總容量：圖 127 MB + 音約 60～90 MB。

---

## 收尾

驗收通過後我會：

1. 依你的決定處理 `tts_front_text` 的 116 張（若選 A 就改 audio 階段）
2. 把選定的參考音檔寫進 `.env.example` 的註解，移除另一支
3. 於 `logs/` 產出 Phase 4 改動日誌（含 VRAM、載入與單句耗時、實際取樣率）
4. 更新 CLAUDE.md 進度表與外部介面狀態（VOXCPM2 ✅）
5. **此時 CLI 全流程完整可用**，Phase 5 僅為介面層加值

還原備份：

```bash
mv work/cards.csv.bak work/cards.csv
rm -f work/bench.csv
```
