# Phase 6 驗收指令清單

日期：2026-08-27 ／ 分支：`dev_ai` ／ 對應
[phase-6-execution-plan.md](phase-6-execution-plan.md)

七個步驟。**第 2、4、5、6 步要你在畫面上操作並判斷**，第 7 步是讀文件挑毛病。

---

## 開始前

驗收會清空工作檔——**不要拿 `work/cards.csv` 測**。準備一份可以砍的：

```bash
# 一份 6 列的示範工作檔（連圖與語音一起複製）
mkdir -p work/demo/media/img work/demo/media/audio
uv run python - <<'EOF'
import asyncio, pathlib, shutil
from anki_deck_builder.state import CardStore

async def main():
    rows = await CardStore("work/cards.csv").read()
    picked = [r for r in rows if r.card_id][:6]
    for row in picked:
        for field, sub in (("image_front", "img"), ("audio_front", "audio"), ("audio_back", "audio")):
            value = getattr(row, field)
            src = pathlib.Path("work") / value if value else None
            if src and src.is_file():
                shutil.copy2(src, pathlib.Path("work/demo/media") / sub / src.name)
    await CardStore("work/demo/cards.csv").write(picked)
    print(f"work/demo/cards.csv：{len(picked)} 列")

asyncio.run(main())
EOF

# 一個要匯入的「掃描目錄」——刻意亂序命名，順便驗自然排序
mkdir -p work/scans
uv run python -c "
from PIL import Image
import pathlib
d = pathlib.Path('work/scans')
for name in ('page10.jpg', 'page2.jpg', 'page1.jpg', 'page3.jpg'):
    Image.new('RGB', (640, 360), (190, 170, 150)).save(d / name)
(d / 'readme.txt').write_text('這個檔案應該被略過')
print('4 張測試掃描頁 + 1 個非圖片檔')
"

WORK_DIR=./work/demo uv run anki-builder serve --port 8080
```

`work/` 已在 `.gitignore` 裡，驗收產生的東西不會混進 `git status`。

---

## 我已經自測過的部分

| 項目 | 已驗證 |
|------|--------|
| 檢查／匯入、重複匯入不產生重複列、自然排序 | ✅ |
| 匯入後狀態總覽自動更新 | ✅ |
| 階段重置只動狀態、內容與媒體都留著 | ✅ |
| 確認字串正確才啟用兩顆清空按鈕 | ✅ |
| 清空 rows 保留媒體、清空 all 刪媒體且目錄還在 | ✅ |
| 每次操作都留下備份 | ✅ |
| 非付費測試 680 passed、`ruff check` 無錯 | ✅ |

**你要看的是我看不出來的**：操作順不順、訊息看不看得懂、防護會不會擋到正常使用。

---

## 1. 匯入分頁的「檢查」

到「匯入」分頁，路徑填 `work/scans`（相對路徑可用；絕對路徑也行），按**檢查**。

**該看到**：
- `判別為 image_dir，共 4 個項目。`
- 檔案清單依序是 `page1.jpg`、`page2.jpg`、`page3.jpg`、`page10.jpg`
  ——**`page2` 在 `page10` 前面**，而且 `readme.txt` 不在清單裡
- 工作檔**沒有任何變化**（切到狀態總覽看，仍是 6 列）

**要回報**：清單看不看得懂；你會不會想在這裡看到完整路徑而不只是檔名。

---

## 2. 匯入與重複匯入（★ 需要你的判斷）

按**匯入**。

**該看到**：`已匯入 4 列。接著到「狀態總覽」按 ① OCR。`
切到狀態總覽，`ocr` 的 pending 從 0 變成 **4**（自動更新，不必手動重新整理）。

再按一次**匯入**：

**該看到**：`已匯入 0 列（略過 4 個已在工作檔中的來源）。`

三個邊界順手試一下：

| 輸入 | 該看到 |
|------|--------|
| 不存在的路徑 | `⚠️ 輸入路徑不存在：…` |
| 單一檔案 `work/scans/page1.jpg` | 判別為 `image`，共 1 個項目 |
| 空白 | `⚠️ 先輸入路徑。` |

**要回報**：這個流程（檢查 → 匯入 → 去按 ① OCR）順不順；有沒有你預期它會做但它沒做的事。

> 想連 OCR 一起驗的話，把路徑換成**真實的書頁照片**再匯入，然後按 ① OCR。
> 測試用的純色圖辨識不出東西，會得到空的 `raw_text`。

---

## 3. 階段重置（非破壞性）

到「設定」分頁最下方，勾選 **image**，按「重置選定階段」。

**該看到**：`已把 image 設回 pending（改動 N 列）。（已備份 cards.csv.bak-…）`

覆核**內容沒有被動到**：

```bash
uv run anki-builder status --work work/demo/cards.csv
uv run python -c "
import csv
r = next(csv.DictReader(open('work/demo/cards.csv', encoding='utf-8-sig')))
print('image_status:', r['image_status'], '| image_front:', r['image_front'], '| front:', r['front'])"
ls work/demo/media/img | head -3
```

**該看到**：`image_status` 是 `pending`，但 `image_front` 的路徑、`front` 的內容、
`media/img` 底下的檔案**全都還在**——重置狀態不等於丟掉成果。

---

## 4. 確認字串防護（★ 需要你的判斷）

在「清空（不可逆）」的輸入框裡：

| 輸入 | 該看到 |
|------|--------|
| 空白 | 兩顆按鈕**暗的**（不可點） |
| `cards` | 仍然暗的 |
| `cards.csv` | 兩顆按鈕**亮起來** |
| 再刪掉一個字 | 立刻又變暗 |

繞過 UI 直接打 API，確認**後端也擋**（不是只有畫面上的把戲）：

```bash
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"mode":"rows","confirm":"確定"}' http://127.0.0.1:8080/api/reset
uv run anki-builder status --work work/demo/cards.csv | head -3
```

**該看到**：`確認字串不符：要清空請輸入工作檔名稱 'cards.csv'`，而且**列數沒變**。

**要回報**：這道防護會不會擋到你正常使用；你覺得夠不夠、或太囉唆。

---

## 5. 執行中拒絕重置（★ 需要你的判斷）

到狀態總覽按 **③ 聯想圖**（會真的生圖，每張約 8 秒），**趁它還在跑**時：

1. 切到設定分頁，打好 `cards.csv`，按「清空所有列」
2. 切到匯入分頁，按「匯入」

**該看到**：兩者都回 `⚠️ 另一個階段（image）正在執行，請等它結束再試`，
且工作檔沒有任何變化。等它跑完再按就正常。

**要回報**：這個限制在實際使用上會不會很煩。

---

## 6. 清空（★ 需要你的判斷）

先記下現況：

```bash
ls work/demo/media/img | wc -l ; ls work/demo/media/audio | wc -l
```

**先按「清空所有列（保留媒體）」**：

**該看到**：`已清空 N 列。（已備份 …）`；狀態總覽全部歸零，
但 `media/img`、`media/audio` 底下的檔案**還在**。

**再按「清空所有列並刪除媒體」**（此時已經沒有列了，但媒體還在）：

**該看到**：`已清空 0 列、刪除 M 個媒體檔。`

```bash
ls work/demo/media/img | wc -l          # 應為 0
ls -d work/demo/media/img               # 目錄本身還在
ls work/demo/*.bak-* | wc -l            # 每次操作各一個備份
```

**要回報**：兩段式（先清列、再清媒體）合不合你的用法；還是你希望有一顆「全部清掉」就好。

---

## 7. CLI 與 UI 對等，以及文件

CLI 該有同樣的能力：

```bash
uv run anki-builder reset --work work/demo/cards.csv --stages image      # 直接執行
uv run anki-builder reset --work work/demo/cards.csv --clear rows        # 只說會刪什麼
uv run anki-builder reset --work work/demo/cards.csv --stages audio      # 錯誤示範
```

**該看到**：第二行**什麼都不做**並提示加 `--yes`（退出碼 1）；
第三行報 `未知的階段名稱 'audio'，合法值：ocr, extract, image, audio_front, audio_back`。

最後讀一遍文件的三處新增，挑毛病：

1. [docs/usage.md](../../docs/usage.md)〈匯入：把書頁收進工作檔〉
2. 同檔〈重置工作檔（設定分頁底部）〉
3. 同檔疑難排解〈不同批次的教材混在一起了〉——**這是你提這個需求的原因**，
   看看兩條建議路線（做完就清空／分成不同工作目錄）有沒有講到你要的

**要回報**：看不懂的句子、你知道但文件沒寫的事。

---

## 收尾

驗收通過後我會：

1. 於 `logs/` 產出 Phase 6 改動日誌
2. 更新 [CLAUDE.md](../../CLAUDE.md) 進度表新增 Phase 6 一列，並把「後續待辦」那兩項移除

清掉驗收用的資料：

```bash
rm -rf work/demo work/scans
```

`work/cards.csv`（311 列的正式牌組）與 `output/deck.zip` 全程不會被動到。
