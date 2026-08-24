# Phase 3 驗收指令清單

日期：2026-08-24 ／ 分支：`dev_ai` ／ 對應 [phase-3-image.md](phase-3-image.md)〈驗收流程〉

九個步驟，依序執行。每步都標了「該看到什麼」與「要回報什麼」——
**第 4 步的 batch size 與第 5 步的 VRAM 讓渡需要你的實測數據才能定案**
（紅線：不自行決定 batch size 預設值）。

---

## 開始前

| 項目 | 目前狀態 |
|------|----------|
| 工作檔 | `work/cards.csv`，311 列（**308 張卡** + 3 列來源列） |
| `image_prompt` | 308／308 都有，沒有空的 |
| `image_status` | 全部 `pending`（先前誤生成的 221 張圖已還原） |
| 尺寸 | `768×432`（`COMFYUI_IMAGE_WIDTH/HEIGHT`） |
| 併發 | `COMFYUI_BATCH_SIZE=4` |
| ComfyUI 讓位 | `COMFYUI_FREE_BEFORE_LLM=false`（預設關閉） |
| Ollama 讓位 | `MODEL_UNLOAD_BEFORE_STAGE` 未設 → 預設**開啟** |

單張實測約 **1.7 秒**（768×432、SD 1.5），308 張在併發 4 下推估 **9～10 分鐘**。

先備份，後面幾步會反覆改工作檔：

```bash
cp -p work/cards.csv work/cards.csv.bak
```

---

## 1. 設定驗證：錯誤要在生成開始前擋下

```bash
COMFYUI_POSITIVE_NODE_ID=99 uv run anki-builder image --work work/cards.csv
```

**該看到**：立刻失敗，訊息指名注入點、環境變數與節點 ID，並列出 workflow 現有節點：

```
錯誤：注入點「正向 prompt」（COMFYUI_POSITIVE_NODE_ID=99）在 workflow 中找不到
節點 '99'。現有節點：3, 4, 5, 6, 7, 8, 9
```

**要確認**：`work/media/img/` 沒有被建立，一張圖都沒生。

---

## 2. 小批量生成

先切出 3 張卡的工作檔，不動正式的那份：

```bash
uv run python -c "
import asyncio
from anki_deck_builder.state import CardStore
async def m():
    rows = await CardStore('work/cards.csv').read()
    cards = [r for r in rows if r.card_id][:3]
    await CardStore('work/small.csv').write(cards)
    print('已建立', len(cards), '列 →', [r.card_id for r in cards])
asyncio.run(m())
"
uv run anki-builder image --work work/small.csv
```

**該看到**：進度條逐格前進，最後一行 `image：處理 3 列（成功 3、失敗 0）`

```
生成聯想圖  [████████████████]  3/3  已耗時 00:05  預估剩餘 00:00
```

**要確認**：`ls work/media/img/` 有三張 PNG，檔名等於 `card_id`。

---

## 3. 圖片內容檢查（人工）

```bash
xdg-open work/media/img/ &
uv run python -c "
import struct, pathlib
for p in sorted(pathlib.Path('work/media/img').glob('*.png')):
    d = p.read_bytes()
    w, h = struct.unpack('>II', d[16:24])
    print(f'{p.name}  {w}x{h}  {len(d)//1024} KB')
"
```

**要確認三件事**：

1. 畫面與該列的 `image_prompt` 描述相符
2. **不含任何文字、字母、浮水印**（這是記憶錨點的硬性要求）
3. 解析度為 **768 × 432**

**要回報**：若出現文字或類文字紋理，把該張圖與它的 `image_prompt` 貼給我——
負向 prompt 不夠力的話要討論怎麼調，不會自行改 prompt 模板。

---

## 4. batch size 實測（★ 需要你的數據）

每種併發各跑一份乾淨的 8 張卡，量「總耗時」與「VRAM 峰值」。

**開一個終端機持續取樣 VRAM**：

```bash
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv -l 1
```

**另一個終端機依序跑**（每次都重建工作檔，確保起點一致）：

```bash
for n in 1 2 4 8; do
  uv run python -c "
import asyncio
from anki_deck_builder.state import CardStore
async def m():
    rows = await CardStore('work/cards.csv').read()
    await CardStore('work/bench.csv').write([r for r in rows if r.card_id][:8])
asyncio.run(m())
"
  rm -rf work/media
  echo \"=== COMFYUI_BATCH_SIZE=$n ===\"
  COMFYUI_BATCH_SIZE=$n /usr/bin/time -f '總耗時 %e 秒' \
    uv run anki-builder image --work work/bench.csv
done
```

**要回報**這張表（VRAM 峰值取 `nvidia-smi` 那一側的最大值）：

| BATCH_SIZE | 總耗時 | 每張平均 | VRAM 峰值 | 有無錯誤／OOM |
|-----------|--------|----------|-----------|--------------|
| 1 | | | | |
| 2 | | | | |
| 4 | | | | |
| 8 | | | | |

定案後我寫進 `.env.example` 與 `config.py` 的預設值。

---

## 5. extract → image 的 VRAM 讓渡（★ 需要你的數據）

本專案有兩個開關，方向相反，這步各測一次。

### 5a. image 前卸載 Ollama（`MODEL_UNLOAD_BEFORE_STAGE`，預設開啟）

```bash
# 先讓抽取模型進 VRAM
uv run anki-builder extract --work work/cards.csv --only-failed
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 記下殘留佔用
curl -s http://localhost:11434/api/ps                      # 確認模型還在

# 預設（開啟）：image 應該先卸載它
uv run anki-builder image --work work/bench.csv --force
```

**該看到**：`（已卸載 <模型名> 以騰出 VRAM）`，接著正常生成。

**對照組**（關閉讓渡，看看差多少）：

```bash
curl -s http://localhost:11434/api/generate -d '{"model":"<你的抽取模型>","keep_alive":-1}' >/dev/null
MODEL_UNLOAD_BEFORE_STAGE=false uv run anki-builder image --work work/bench.csv --force
```

**要回報**：兩者的耗時差異，以及關閉時是否 OOM 或明顯變慢。

### 5b. extract 前請 ComfyUI 讓位（`COMFYUI_FREE_BEFORE_LLM`，預設關閉）

CLAUDE.md 記著的實測是「ComfyUI 常駐 2.4 GB → 抽取模型只載入 88%、速度剩 1/6」。
我這邊量到 `/free` 可釋放約 2 GB（3008 MiB → 960 MiB），但**沒有量過它對 extract 的實際影響**。

```bash
# 對照組：ComfyUI 常駐著跑 extract
uv run anki-builder image --work work/bench.csv --force   # 先讓 ComfyUI 載入模型
nvidia-smi --query-gpu=memory.used --format=csv,noheader
time uv run anki-builder extract --work <某一頁的工作檔> --force

# 實驗組：extract 前先請 ComfyUI 讓位
uv run anki-builder image --work work/bench.csv --force
COMFYUI_FREE_BEFORE_LLM=true time uv run anki-builder extract --work <同一份> --force
```

**要回報**：兩者的 extract 耗時。差距明顯就把 `COMFYUI_FREE_BEFORE_LLM` 的預設改成開啟。

---

## 6. 失敗跳過：整批不中斷

生成途中把 ComfyUI 停掉（Ctrl-C 它的視窗，或 `pkill -f comfyui`）：

```bash
rm -rf work/media
uv run anki-builder image --work work/cards.csv    # 跑幾秒後停掉 ComfyUI
```

**該看到**：指令**不崩潰**，繼續跑完剩下的列，最後回報 `成功 N、失敗 M` 並提示看 `status`。

```bash
uv run anki-builder status --work work/cards.csv
```

**要確認**：失敗的列 `image_status=failed`、`image_error` 寫著連線失敗或逾時，
且**已完成的列沒有被回退**（`checkpoint_every=1`，每列寫回一次）。

重新啟動 ComfyUI 後接第 7 步。

---

## 7. 只重跑失敗

```bash
uv run anki-builder image --work work/cards.csv --only-failed
```

**要確認**：只處理上一步失敗的那些列，已 `done` 的列**檔案修改時間不變**：

```bash
ls -la --time-style=full-iso work/media/img/ | head
```

---

## 8. 針對性重生：只動我改的那一列

手動改工作檔中某一列的 `image_prompt`，並把該列的 `image_status` 改回 `pending`
（用試算表或編輯器都可以），然後：

```bash
# 先記下所有圖的修改時間
ls -la --time-style=full-iso work/media/img/ > /tmp/before.txt

uv run anki-builder image --work work/cards.csv

ls -la --time-style=full-iso work/media/img/ > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

**該看到**：`diff` 只列出你改的那一張。

**順帶驗證穩定 seed**：不改 `image_prompt`、只把 `image_status` 設回 `pending` 再跑一次，
**應該得到位元完全相同的圖**：

```bash
md5sum work/media/img/<某個 card_id>.png    # 重跑前後比對
```

---

## 9. 全流程

```bash
uv run anki-builder run-all --work work/cards.csv --output output/deck.zip
```

（工作檔已存在，不必給 `--input`；ocr 與 extract 的列都是 `done`，會直接跳過。）

**該看到**：`① ocr` → `② extract` → `③ image` → `⑤ pack` 依序出現，
image 那一段有進度條。

```bash
unzip -l output/deck.zip | head -20
```

**要確認**：ZIP 內含 `media/img/` 與 308 張圖，載入記憶引擎後卡片正面顯示聯想圖。

---

## 收尾

驗收通過後我會：

1. 依實測數據把 batch size 與 `COMFYUI_FREE_BEFORE_LLM` 的預設值寫進 `.env.example` 與 `config.py`
2. 於 `logs/` 產出 Phase 3 改動日誌（含 VRAM 與 batch size 實測數據）
3. 更新 CLAUDE.md 進度表與外部介面狀態（ComfyUI workflow ✅）

還原備份：

```bash
mv work/cards.csv.bak work/cards.csv
rm -f work/small.csv work/bench.csv
```
