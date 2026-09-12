#!/usr/bin/env python
"""把 `reading` 缺掉的濁點與小寫假名補回來，來源是形態素分析器而不是影像。

## 為什麼不需要重拍照片

這批錯誤全是 OCR 沒讀出小寫假名與濁點（`きゆう` ← `きゅう`、`きよたい` ←
`きょだい`）。2026-09-12 做過兩輪重拍 A/B（見
`.agent/plans/photo-quality-ab.md`），**照片路線的天花板是 9 筆目標修好 2 筆**，
而代價是 41 頁重拍加約 14 小時重跑。

而 `check-card-quality.py` 的 `marks` 檢查一直是這樣判定的：

    auto = analyse(front)                  # SudachiPy 從漢字詞目推出讀音
    if flatten(auto) == flatten(reading):  # 兩者只差濁點／小寫假名
        報告

**正確讀音是分析器算出來的，不是從影像讀的**——答案早就在手上。本腳本就是把
那個答案寫回去。成本是分鐘級，不是 14 小時。

## 判定條件與 `check_marks()` 完全相同，刻意不放寬

只在「分析器讀音與卡片讀音**去掉濁點與小寫假名後完全相同**」時才改。這條件緊到
幾乎不可能誤判：多音字不會進來，因為分析器分析的是 `front` 本身——`清く` 的
分析結果也是 `きよく`，與卡片相同，根本不會被選中。

方向是雙向的：`器用` 卡片寫 `きょう`、分析器給 `きよう`，那是卡片多了小寫假名，
同樣會被修。

## ⚠️ 涵蓋範圍的結構性限制：`marks` 歸零 ≠ 讀音都對了

本工具（與 `check_marks()`）只看得到**分析器剛好選到書上那個讀音**的情況。
教材的詞目常常是**字音詞素**（`hint` 標 `漢造`），而分析器分析單獨一個漢字時
給的是它當獨立詞的訓讀：

| 詞目 | 書上（正確） | 分析器 | 結果 |
|---|---|---|---|
| `球` | `きゅう` | `たま` | 骨架不同 → **看不到**，卡片的 `きゆう` 一直錯著 |
| `今` | `こん` | `いま` | 同上 |
| `空` | `くう` | `そら` | 同上 |
| `歌` | `か` | `うた` | 同上 |
| `家` | `か` | `いえ` | 同上 |

這些在 2026-09-12 是**人工比對同頁鄰居的讀音順序**才找出來的（教材依讀音排序，
`今` 落在 こや／こらえる／ごらん 之間就只能是 `こん`）。

**所以不要把 `marks` 歸零當成「讀音都對了」。** 這不是 bug，是判定條件刻意收緊的
代價——放寬到「只要分析器不同就報」會把多音字全部誤報進來。真要補這個缺口，
該做的是另一項檢查（例如「`hint` 含 `漢造` 而 `reading` 不是字音」），不是放寬這裡。

## 一併同步 `tts_front_text`

`audio_front` 唸的是 `tts_front_text`（退路才是 `reading`，見
`stages/audio.py` 的 `source_fields()`）。只改 `reading` 會讓兩者不一致，
被 `check-card-quality.py` 的 `fields` 檢查抓到，而且音檔還是唸錯的。
實測這 65 列的 `tts_front_text` **全部正好等於錯的 `reading`**，所以一起換。

`--apply` 會把改動列的 `audio_front_status` 設回 `pending`（實測 16 列已產生
音檔，唸的是錯讀音）。音檔路徑是 `audio/{card_id}_front.{ext}`，重跑覆寫同一個
檔案，不會留下孤兒。**`audio_back` 不動**——那唸的是例句，與詞條讀音無關。

## 不重跑任何階段

只改讀音欄位，不動 `raw_text`、不重新抽取、不重生圖片。因此不會撞
`extract` 的 `card_id` 唯一性檢查。

用法：

    uv run python scripts/fix-reading-marks.py work/cards.csv            # 預覽
    uv run python scripts/fix-reading-marks.py work/cards.csv --apply    # 寫回
    uv run anki-builder audio                                            # 重生受影響的音檔

相依：SudachiPy，選配安裝（`uv sync --all-extras`）。重複執行安全。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from pathlib import Path

ENCODING = "utf-8-sig"
KANJI = re.compile(r"[一-鿿々]")
KANA_ONLY = re.compile(r"^[぀-ゟ゠-ヿー]+$")
SEPARATOR = re.compile(r"[/／]")
_KATA2HIRA = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})

#: 符合 `marks` 條件但**不該自動修**的卡片 → 排除的理由。目前是空的。
#:
#: 唯一進過這張表的是 `p38_002`：`front` 是 `これ腰`，正確詞目為 `腰`（こし）
#: ——`これ` 是抽取階段夾帶的雜訊，`example` 也被污染成 `これ腰が抜ける`。
#: 它符合 `marks` 條件只是巧合，真正的缺陷是詞目本身壞掉，把讀音改成 `これこし`
#: 只會讓 `marks` 歸零而把真問題藏起來。**該卡已於 2026-09-12 人工修正源頭**
#: （`front`／`example`／`reading`／`tts_*`／`back` 五個欄位），因此移出本表。
#:
#: 機制保留：這類「符合條件但詞目本身是壞的」還會再出現。
#: **這張表變長就是警訊**——代表判定條件選錯了，而不是該繼續往裡面加。
_CARD_SKIP: dict[str, str] = {}

_SMALL_KANA = str.maketrans("っゃゅょぁぃぅぇぉ", "つやゆよあいうえお")


def flatten(text: str) -> str:
    """去掉濁點與小寫假名的差異，只留下「骨架」。

    與 `check-card-quality.py` 的 `check_marks()` 同一套做法：先 NFD 拆掉
    濁點這類組合字元，再把小寫假名攤成大寫。骨架相同代表兩串只差這兩件事。
    """
    stripped = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.translate(_SMALL_KANA)


def make_analyser(tokenizer, split_mode):  # noqa: ANN001, ANN201
    """回傳 `漢字詞目 → 平假名` 的函式；含漢字的 token 沒有讀音就回 None。

    與 `check-card-quality.py` 的 `_analyser()` 相同，刻意不共用：兩支腳本
    各自獨立可跑，而這個函式只有十行。
    """

    def analyse(text: str) -> str | None:
        parts: list[str] = []
        for token in tokenizer.tokenize(text, split_mode):
            surface = token.surface()
            if not KANJI.search(surface):
                parts.append(surface)
                continue
            reading = token.reading_form()
            if not reading:
                return None
            parts.append(reading.translate(_KATA2HIRA))
        return "".join(parts)

    return analyse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="中間 CSV，例如 work/cards.csv")
    parser.add_argument("--apply", action="store_true", help="實際寫回（預設只預覽）")
    parser.add_argument("--limit", type=int, default=20, help="預覽列出幾筆")
    args = parser.parse_args()

    try:
        from sudachipy import Dictionary, SplitMode
    except ImportError:
        print("需要 SudachiPy：uv sync --all-extras", file=sys.stderr)
        return 1

    if not args.work.exists():
        print(f"找不到 {args.work}", file=sys.stderr)
        return 1

    with args.work.open(encoding=ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    required = ("card_id", "front", "reading", "tts_front_text",
                "audio_front_status", "audio_front_error")
    for column in required:
        if column not in header:
            print(f"CSV 少了 {column} 欄", file=sys.stderr)
            return 1

    analyse = make_analyser(Dictionary().create(), SplitMode.C)
    changed: list[tuple[str, str, str, str, bool]] = []
    skipped: list[tuple[str, str, str]] = []
    desynced: list[tuple[str, str, str]] = []

    for row in rows:
        card_id = (row.get("card_id") or "").strip()
        front = (row.get("front") or "").strip()
        reading_raw = (row.get("reading") or "").strip()
        head, sep, rest = _split_reading(reading_raw)
        if not card_id or not KANJI.search(front) or not KANA_ONLY.match(head):
            continue

        auto = analyse(front)
        if not auto or auto == head or flatten(auto) != flatten(head):
            continue

        if card_id in _CARD_SKIP:
            skipped.append((card_id, front, _CARD_SKIP[card_id]))
            continue

        tts = (row.get("tts_front_text") or "").strip()
        # `tts_front_text` 只在它就是那串錯讀音時才跟著換。內容不同代表它另有
        # 來歷（例如人工改過），那要判斷哪個對——交給 `fields` 檢查，不猜
        sync_tts = tts == head
        if tts and not sync_tts:
            desynced.append((card_id, head, tts))

        stale_audio = row.get("audio_front_status") == "done" and (sync_tts or not tts)
        changed.append((card_id, front, head, auto, stale_audio))

        if args.apply:
            row["reading"] = auto + sep + rest
            if sync_tts:
                row["tts_front_text"] = auto
            if stale_audio:
                row["audio_front_status"] = "pending"
                row["audio_front_error"] = ""

    reset = sum(1 for *_, stale in changed if stale)
    print(f"要改 {len(changed)} 列、排除 {len(skipped)} 列（共 {len(rows)} 列）")
    print(f"其中 {reset} 列的 audio_front 已產生，會設回 pending\n")

    if changed:
        print("── 改動（卡片 → 分析器）──")
        for card_id, front, before, after, stale in changed[: args.limit]:
            mark = "  ♪ 音檔重生" if stale else ""
            print(f"  {card_id:9} {front:12} {before:14} → {after}{mark}")
        if len(changed) > args.limit:
            print(f"  …其餘 {len(changed) - args.limit} 列")
    if skipped:
        print("\n── 排除，需人工處理（見 _CARD_SKIP）──")
        for card_id, front, why in skipped:
            print(f"  {card_id:9} {front:12} {why}")
    if desynced:
        print("\n── tts_front_text 與 reading 本來就不同，未動（交給 fields 檢查）──")
        for card_id, head, tts in desynced:
            print(f"  {card_id:9} reading={head:14} tts_front={tts}")

    if not args.apply:
        print("\n（預覽模式，沒有寫檔。要寫回請加 --apply）")
        return 0

    _write_atomic(args.work, header, rows)
    print(f"\n已寫回 {args.work}。")
    if reset:
        print(f"{reset} 列的 audio_front_status 設回 pending，接著執行："
              "uv run anki-builder audio")
    return 0


def _split_reading(reading: str) -> tuple[str, str, str]:
    """切成 `(第一個讀音, 分隔符, 其餘)`。

    `reading` 可能是 `きゆう／キュウ` 這種多讀音寫法，而 `marks` 只判第一個。
    改寫時要把分隔符後面的部分**原樣接回去**，不能整欄覆蓋掉。
    """
    match = SEPARATOR.search(reading)
    if not match:
        return reading, "", ""
    return reading[: match.start()].strip(), match.group(0), reading[match.end():]


def _write_atomic(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    """原子寫入，比照 `state/store.py` 與 `kana-tts-back.py`：先寫暫存檔再 rename。"""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding=ENCODING, newline="") as fh:
        fh.write(buffer.getvalue())
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


if __name__ == "__main__":
    raise SystemExit(main())
