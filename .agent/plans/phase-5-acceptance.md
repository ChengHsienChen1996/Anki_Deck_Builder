# Phase 5 驗收指令清單

日期：2026-08-26 ／ 分支：`dev_ai` ／ 對應 [phase-5-webui.md](phase-5-webui.md)〈驗收流程〉

八個步驟，依序執行。每步都標了「該看到什麼」與「要回報什麼」——
**第 3、4、5、6 步要你看畫面判斷，第 8 步要你讀文件挑毛病。**

---

## 開始前：先準備一份可以弄壞的工作檔

`work/cards.csv` 是你 Phase 4 驗收完的成品（311 列全 `done`、圖 127 MB、音 126 MB）。
驗收會編輯欄位、重置狀態、重生圖與語音——**不要拿它直接測**。

```bash
# 複製一份 40 列的示範資料到獨立工作目錄
mkdir -p work/demo/media/img work/demo/media/audio
uv run python - <<'EOF'
import asyncio, shutil, pathlib
from anki_deck_builder.state import CardStore
from anki_deck_builder.schemas.status import StageStatus

async def main():
    rows = await CardStore("work/cards.csv").read()
    picked = [r for r in rows if r.card_id][:40]
    for r in picked:
        for field, sub in (("image_front", "img"), ("audio_front", "audio"), ("audio_back", "audio")):
            value = getattr(r, field)
            src = pathlib.Path("work") / value if value else None
            if src and src.is_file():
                shutil.copy2(src, pathlib.Path("work/demo") / "media" / sub / src.name)
    # 造三筆失敗給第 6 步用
    picked[10].image_status = StageStatus.FAILED
    picked[10].image_error = "ExternalServiceError: ComfyUI 佇列逾時（人工造假）"
    picked[11].image_status = StageStatus.FAILED
    picked[11].image_error = "ExternalServiceError: 節點 12 的 class_type 不存在（人工造假）"
    picked[12].audio_back_status = StageStatus.FAILED
    picked[12].audio_back_error = "StageProcessingError: 沒有 tts_back_text（人工造假）"
    await CardStore("work/demo/cards.csv").write(picked)
    print(f"work/demo/cards.csv：{len(picked)} 列，其中 3 筆人工失敗")

asyncio.run(main())
EOF
```

**`serve` 沒有 `--work`**（Phase 1 定案的參數結構不改），所以要用環境變數指定工作目錄：

```bash
WORK_DIR=./work/demo uv run anki-builder serve --port 8080
```

後續每一步的 CLI 指令也一律加 `--work work/demo/cards.csv`。
ComfyUI 要先開著（第 3、5、6、7 步會真的生圖）。

> 放在 `work/` 底下是刻意的：`.gitignore` 已經有 `work/*`，驗收產生的 33 MB 資料
> 不會跑進 `git status` 干擾你。`work/cards.csv` 本身完全不會被動到。

---

## 我已經自測過的部分

這些在開發過程中用瀏覽器實跑驗證過，你可以快速確認就好，不必逐項細查：

| 項目 | 已驗證 |
|------|--------|
| 五個分頁渲染、API 未被 UI 蓋掉 | ✅ |
| 金鑰遮罩顯示 `******` | ✅ |
| force 與 only-failed 互斥時擋下且不啟動階段 | ✅ |
| 編輯 `front` → 只重置 `extract`，其他階段不動 | ✅ |
| 單張重生只動那一張（其餘圖檔 mtime 未變） | ✅ |
| 失敗清單的單列重跑與跨階段背景批次重跑 | ✅ |
| 非付費測試 621 passed、`ruff check` 無錯 | ✅ |

**你要看的是我看不出來的東西**：操作順不順、訊息看不看得懂、有沒有我沒想到的用法。

---

## 1. 啟動服務與五個分頁

```bash
WORK_DIR=./work/demo uv run anki-builder serve --port 8080
```

瀏覽器開 `http://127.0.0.1:8080`。

**該看到**：終端印出 `Web UI：http://127.0.0.1:8080（Ctrl-C 結束）`，
頁面上有五個分頁 `狀態總覽｜抽取結果｜聯想圖｜失敗清單｜設定`，
狀態總覽的表格**自動載入**（不必按任何按鈕）。

順便確認 API 還在：

```bash
curl -s http://127.0.0.1:8080/api/status | head -c 200
```

**要回報**：有沒有哪個分頁開不起來或版面爆掉。

---

## 2. 金鑰遮罩

到「設定」分頁。

**該看到**：`agent_factory.openai_api_key` 顯示 `******`，整份 JSON 裡沒有任何明文金鑰。

```bash
curl -s http://127.0.0.1:8080/api/config | grep -o '"openai_api_key":"[^"]*"'
```

**要回報**：有沒有任何一個欄位露出你不希望被看到的值（金鑰以外的路徑類資訊是刻意顯示的）。

---

## 3. 狀態總覽與觸發（★ 需要你的判斷）

先比對數字：

```bash
uv run anki-builder status --work work/demo/cards.csv
```

**該看到**：CLI 的統計表與「狀態總覽」的表格**完全一致**（image 應為 done 38 / failed 2）。

接著觸發一個階段。勾 `only-failed`，按 **③ 聯想圖**：

**該看到**：
- 按鈕下方出現 `已開始執行 image。`
- 進度列每秒更新，形如 `image：done 38／40（failed 2）— 執行中 3s`
- **執行中介面沒有凍結**——可以切到其他分頁、捲動、點東西
- 跑完變成 `— 已完成（image 成功 2／失敗 0）`，表格的 failed 歸零

執行期間再按 **④ 語音（兩側）**：

**該看到**：`⚠️ 另一個階段（image）正在執行，請等它結束再試`——**不排隊、直接回絕**。

> 若進度看起來不動：確認瀏覽器分頁在前景。背景分頁會被 Chrome 節流到約 40 秒才 tick 一次，
> 這是瀏覽器行為不是程式問題（實測 75 秒內 tick 2 次）。

**要回報**：進度更新的頻率與訊息看不看得懂；有沒有卡頓。

---

## 4. 抽取結果編輯連動（★ 需要你的判斷）

到「抽取結果」分頁，三個編輯各做一次，每次都按「儲存變更」：

| 改什麼 | 該看到的訊息 | 之後用 CLI 覆核 |
|--------|-------------|----------------|
| 某列的 `front` | `已更新 1 列（…），重置階段：extract。` | 該列 `extract_status=pending`，`image_status` **仍是** `done` |
| 某列的 `image_prompt` | `重置階段：extract、image。` | 兩者都 `pending` |
| 某列的 `tts_back_text` | `重置階段：extract、audio_back。` | `audio_back_status=pending`，`audio_front_status` **不變** |

覆核指令（把 `p1_0XX` 換成你改的那張）：

```bash
uv run python -c "
import csv
for r in csv.DictReader(open('work/demo/cards.csv', encoding='utf-8-sig')):
    if r['card_id'] == 'p1_003':
        print({k: v for k, v in r.items() if k.endswith('_status')})
"
```

再試三件邊界：

1. **改 `card_id`**：改不動（欄位鎖著，標題有鎖頭圖示）
2. **按儲存但什麼都沒改**：訊息是 `沒有任何變動。`，狀態不會被重置
3. **篩選**：deck 下拉選一個值 → 自動回到第 1 頁、筆數變少；階段＋狀態一起選才會依狀態篩

**已知限制（不是 bug，但你要知道）**：「儲存變更」**只作用於當前頁**。
跨頁編輯後才按儲存，前一頁的修改不會寫入。要不要加「有未儲存變更」的提示，你決定。

**要回報**：連動規則符不符合你實際修卡的習慣；有沒有你會想改、但目前鎖住不能改的欄位。

---

## 5. 單張重生（★ 需要你的判斷）

先記下所有圖的修改時間：

```bash
ls -la --time-style=full-iso work/demo/media/img/ > /tmp/img_before.txt
```

到「聯想圖」分頁，點任一張縮圖 → 改 `image_prompt`（例如把場景換掉）→ 按「生成／重生此圖」。

**該看到**：
- 數秒後訊息 `已重生 p1_0XX。`，**大圖與縮圖都換成新圖**，標題的 `image：` 狀態是 `done`
- 其他縮圖完全沒變

```bash
ls -la --time-style=full-iso work/demo/media/img/ > /tmp/img_after.txt
diff /tmp/img_before.txt /tmp/img_after.txt
```

**該看到**：`diff` 只有你重生的那一張有差異，其餘一行都沒動。

再找一張**還沒生成**的（灰底佔位圖、caption 標「（未生成）」）按同一顆按鈕。
demo 資料裡若沒有，自己造一張：

```bash
uv run python -c "
import asyncio
from anki_deck_builder.state import CardStore
from anki_deck_builder.schemas.status import StageStatus
async def m():
    s = CardStore('work/demo/cards.csv'); rows = await s.read()
    rows[5].image_front = ''; rows[5].image_status = StageStatus.PENDING
    await s.write(rows); print('已把', rows[5].card_id, '的圖清掉')
asyncio.run(m())
"
```
（清掉後在 UI 按「重新載入」。）

**要回報**：重生一張要等多久你覺得可接受嗎；新圖與你改的 prompt 對不對得上
（SD 1.5 對多元素構圖的已知限制見 `docs/usage.md`〈生成圖含文字〉下方）。

---

## 6. 失敗清單批次重跑（★ 需要你的判斷）

到「失敗清單」分頁。準備階段造的 3 筆人工失敗應該都在（若第 3 步已把 image 的兩筆重跑掉，
就只剩 audio_back 那筆；要補的話重跑一次準備腳本）。

**該看到**：
- 三筆各自成列，欄位是 `card_id｜階段｜front｜錯誤訊息`
- 長訊息在表格裡截成一行結尾 `…`，**點該列**後在「完整錯誤訊息」看到全文
- 上方「共 N 筆失敗。」

依序試兩顆按鈕：

| 動作 | 該看到 |
|------|--------|
| 點一列 → 「重跑此列」 | 跑完才回來，訊息 `已重跑 p1_0XX 的 image。`，該列從清單消失、筆數減一 |
| 「重跑全部失敗」 | **立刻**回 `已在背景重跑：image、audio_back。進度看「狀態總覽」。`，切到狀態總覽看進度 |

背景批次跑完後：

```bash
curl -s http://127.0.0.1:8080/api/failed
uv run anki-builder status --work work/demo/cards.csv
```

**該看到**：`{"failed":[]}`，status 五個階段 failed 全為 0。

> 語音那筆會觸發 VOXCPM2 模型載入（約 28 秒），所以批次重跑會比你預期久一點。
> 兩側都有失敗時程式會合併成一次載入，不會付兩次。

**要回報**：「跑完才回來」與「背景跑」這兩種行為的分工，在你實際用起來合不合理。

---

## 7. CLI 與 UI 行為一致（關鍵驗證）

這一步在驗「Web 沒有重複實作邏輯」。準備兩份相同的輸入：

```bash
rm -rf work/demo_cli && cp -r work/demo work/demo_cli
uv run python -c "
import asyncio
from anki_deck_builder.state import CardStore
from anki_deck_builder.schemas.status import StageStatus
async def m():
    for path in ('work/demo/cards.csv', 'work/demo_cli/cards.csv'):
        s = CardStore(path); rows = await s.read()
        for r in rows[:5]:
            r.image_status = StageStatus.PENDING
        await s.write(rows)
    print('兩份工作檔的前 5 列都設回 pending')
asyncio.run(m())
"
```

一邊用 CLI、一邊用 UI（狀態總覽按「③ 聯想圖」，不勾任何選項）：

```bash
uv run anki-builder image --work work/demo_cli/cards.csv
```

比對結果：

```bash
uv run anki-builder status --work work/demo/cards.csv
uv run anki-builder status --work work/demo_cli/cards.csv
uv run python -c "
import csv
def cols(path):
    return [(r['card_id'], r['image_front'], r['image_status'])
            for r in csv.DictReader(open(path, encoding='utf-8-sig'))]
ui, cli = cols('work/demo/cards.csv'), cols('work/demo_cli/cards.csv')
diff = [(a, b) for a, b in zip(ui, cli) if a != b]
print('完全一致 ✓' if not diff else f'{len(diff)} 列不同：{diff[:5]}')
"
```

（用 csv 模組讀而不是 `cut`——`example` 等欄位內含逗號且有引號，按逗號切會錯位。）

**該看到**：兩邊的統計數字相同、上面的 `diff` 沒有差異。

> 圖檔本身的 md5 相同更好，但**不同不算失敗**——ComfyUI 的取樣在 GPU 上並非完全決定性。
> 這一步要驗的是狀態流轉與欄位寫入一致，不是位元級相同。

**要回報**：有沒有任何一欄兩邊不一樣。

---

## 8. 文件可用性（★ 需要你的判斷）

phase 文件原本要求「請一位未參與開發的人從零跑通」。若手邊沒有這樣的人，退而求其次：
**你自己照 [README.md](../../README.md) 與 [docs/usage.md](../../docs/usage.md) 從頭走一次**，
特別是這四段（它們最容易漏東西）：

1. 〈前置需求〉的四項——照著做能不能把環境備齊？`agents.yaml` 與 `.env` 的分工講清楚了嗎？
2. 〈ComfyUI 節點注入點〉——一個沒看過本專案的人，能靠這段把自己的 workflow 接上嗎？
3. 〈CLI〉的常用組合——有沒有你常用但文件沒寫的參數組合？
4. 〈疑難排解〉——你 Phase 1–4 實際踩過的坑，有沒有哪個沒被收進去？

**要回報**：卡住的地方、看不懂的句子、你知道但文件沒寫的事。這些我直接補進文件。

---

## 收尾

驗收通過後我會：

1. 於 `logs/` 產出 Phase 5 改動日誌（六個 task、新增的 `web/` 與骨架擴充、實測數據）
2. 更新 [CLAUDE.md](../../CLAUDE.md) 進度表——**五個 phase 全部標記完成**
3. 回顧三項外部相依（agent_factory / ComfyUI workflow / VOXCPM2）的狀態，
   把仍未解決的待辦明確列在日誌裡

清掉驗收用的資料：

```bash
rm -rf work/demo work/demo_cli /tmp/img_before.txt /tmp/img_after.txt
```

`work/cards.csv` 與 `output/deck.zip` 全程沒有被動到，仍是 Phase 4 驗收完的狀態。

> 合併回 `main` 由你人工執行：`scripts/merge-to-main.sh dev_ai`。AI 不主動 merge。
