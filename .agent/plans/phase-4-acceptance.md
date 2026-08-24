# Phase 4 驗收指令清單

日期：2026-08-25 ／ 分支：`dev_ai` ／ 對應 [phase-4-audio.md](phase-4-audio.md)〈驗收流程〉

八個步驟，依序執行。每步都標了「該看到什麼」與「要回報什麼」——
**第 3 步要你聽音檔內容、第 5 步需要 VRAM 實測數據。**

---

## 開始前

| 項目 | 目前狀態 |
|------|----------|
| 工作檔 | `work/cards.csv`，311 列（**308 張卡** + 3 列來源列） |
| `image_status` | 308／308 `done`（你 08-24 自己跑的，127 MB） |
| `audio_front_status`／`audio_back_status` | 全部 `pending`，`work/media/audio/` 已清空 |
| 可唸的文字 | front 308／308、back 308／308，**沒有任何一張缺文字** |
| 音色來源 | **Voice Design**（文字描述），不使用參考音檔 |
| 生成參數 | `timesteps=30`、`cfg=3.0` |
| 譯文 | `VOXCPM2_SPEAK_TRANSLATION=false`（背面只唸原文） |

推估耗時：模型載入約 28 秒（每次執行一次）＋ front 308 段約 3 分鐘
＋ back 308 段約 11 分鐘 ≈ **15 分鐘**。

先備份：

```bash
cp -p work/cards.csv work/cards.csv.bak
```

---

## 這一輪已經處理掉的三件事

寫這份清單的過程中查到三個資料問題，都已修好，驗收時不會再遇到：

| 問題 | 處理方式 |
|------|----------|
| 116／308 張卡的 `tts_front_text` 為空（全是 `reading` 也空的非日語詞條） | front 側依序退回 `reading`、`front`——那本來就是 `prompts/extract_cards.md` 對該欄位的規定 |
| 177／308 張卡的 `tts_back_text` 混入中文譯文 | 一次性清理，只留原文；譯文完整留在 `example` |
| 要不要唸譯文沒有選擇 | 新增 `VOXCPM2_SPEAK_TRANSLATION`。**這是「讀哪個欄位」的切換**（`tts_back_text` vs `example`），不是字串切割——用啟發式猜譯文在日文牌組會整句刪光 |

---

## 音色：目前走 Voice Design，cloning 的接口留著

`.env` 現在是：

```bash
VOXCPM2_VOICE_DESCRIPTION=(A young woman, clear and steady voice, neutral American accent, calm pace)
VOXCPM2_REFERENCE_WAV=
VOXCPM2_PROMPT_WAV=
VOXCPM2_PROMPT_TEXT=
```

三種來源**不互斥**，日後換路線只要改設定，不必動程式碼：

| 想要的效果 | 怎麼設 |
|-----------|--------|
| 換個音色（現況） | 只改 `VOXCPM2_VOICE_DESCRIPTION` 的描述文字 |
| 改用聲音複製 | `VOXCPM2_VOICE_DESCRIPTION` 留空，`VOXCPM2_REFERENCE_WAV` 指向音檔 |
| 複製＋風格控制 | 兩者都給——描述退為風格控制（README 的 Controllable Voice Cloning） |
| 最高保真複製 | `REFERENCE_WAV` 與 `PROMPT_WAV` 指向**同一個檔**，再加 `PROMPT_TEXT` 逐字稿（README 的 Ultimate Cloning） |

參考音檔的準備目標（`assets/voice/` 現有兩支先留著）：16 kHz 以上（模型內部一律降到
16 kHz，再高沒有加分）、單聲道、無損格式、**只有目標語者的乾淨人聲**（無 BGM／音效／
他人聲音／混響）、平穩朗讀語調、5～15 秒、語言與要唸的內容一致。**順便打一份逐字稿**
就能用最高保真那條路。

---

## 1. 單邊生成隔離性

```bash
uv run anki-builder audio --work work/cards.csv --side front
uv run anki-builder status --work work/cards.csv
```

**該看到**：只有「生成語音（單字）」一條進度條，結尾 `audio_front：處理 308 列…`，
**完全不會出現 `audio_back` 的字樣**。

推估耗時：模型載入約 28 秒 + 308 段單字（每段約 0.7 秒），合計 **約 3～4 分鐘**。

**要確認**：

```bash
ls work/media/audio | grep -c _front.wav    # 應為 308，且 failed 為 0
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

推估耗時：308 段例句較長（每段約 2 秒），約 **10～12 分鐘**。

---

## 3. 音檔內容檢查（★ 需要你的判斷）

```bash
xdg-open work/media/audio/ &
```

**要確認四件事**：

1. `_front.wav` 唸的是單字本身、`_back.wav` 唸的是例句，沒有唸錯邊
2. **背面沒有唸中文譯文**（`tts_back_text` 已清理，`SPEAK_TRANSLATION=false`）
3. **每張卡都是同一個聲音**——Voice Design 的描述固定，音色就該固定
4. 發音正確、語速合理、**沒有被截斷**，短單字沒有出現「幾乎無聲」

短單字是這個模型的弱項（實測 `timesteps=10` 時約 40% 的短詞會生出幾乎聽不見的音檔，
提到 30 之後降到 10% 左右）。抽樣時**優先聽最短的那些**：

```bash
uv run python -c "
import soundfile as sf, pathlib
files = [(sf.info(str(p)).duration, p.name) for p in pathlib.Path('work/media/audio').glob('*_front.wav')]
for d, n in sorted(files)[:15]:
    print(f'{d:.2f}s  {n}')
"
```

**要回報**：有幾個聽起來不對、是哪些。若比例仍高，下一步可試
`VOXCPM2_CFG_VALUE` 再往上，或換一段 Voice Design 描述。

**想換音色**：改 `.env` 的 `VOXCPM2_VOICE_DESCRIPTION` 再重跑即可，例如

```bash
VOXCPM2_VOICE_DESCRIPTION=(A female language teacher, warm and articulate, speaking slowly and clearly)
uv run anki-builder audio --work work/bench.csv --force
```

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

1. 把定案的 Voice Design 描述寫進 `.env.example`
2. 於 `logs/` 產出 Phase 4 改動日誌（含 VRAM、載入與單句耗時、實際取樣率）
4. 更新 CLAUDE.md 進度表與外部介面狀態（VOXCPM2 ✅）
5. **此時 CLI 全流程完整可用**，Phase 5 僅為介面層加值

還原備份：

```bash
mv work/cards.csv.bak work/cards.csv
rm -f work/bench.csv
```
