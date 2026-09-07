#!/usr/bin/env python
"""把 `tts_back_text` 的日文句子整句轉成平假名，讓 TTS 唸對語言。

## 為什麼需要這個

VOXCPM2 **沒有語言參數**（`core.py:180` 的完整參數清單裡沒有），語言由文字本身
決定，而模型看到漢字就傾向唸中文。Phase 9 的 Task 9.5 已經處理過 front 側，
但那條規則只在「整串都是漢字」時才把 `tts_front_text` 換成 `reading`——
**back 側是例句，漢字與假名混在一起，那條規則一次都不會觸發**
（實測 344 張中「純漢字」的 back 只有 2 張，「漢字＋假名混合」有 292 張）。

例句要唸對，只能整句做形態素解析後轉假名。本工具用 pykakasi 做這件事。

## 為什麼是獨立工具、不接進流程

**它是日文限定的。** 本專案的第一條目標是支援任意學習領域
（見 docs/project-overview.md），把日文專屬的轉換寫進 `audio` 階段會違反那條。
所以放在 `scripts/`，需要時手動跑，相依也是選配（`uv sync --extra kana`）。

## ⚠️ pykakasi 會靜默吞掉不認得的字

實測（2026-09-07）：本牌組的 OCR 留下了簡體字（`爱`、`暧`），pykakasi 不認得，
**直接從輸出消失，而且不報錯**：

    爱情を持つ        → をもつ          （「爱情」整個不見）
    あなたを爱している  → あなたをている
    暧昧な態度        → なたいど

如果不擋，這些卡的語音會變成「少唸幾個詞」——比唸錯語言更難察覺。
所以本工具對每一列做兩道檢查，任何一道不過就**跳過該列並列出來**，不硬轉：

1. **往返比對**：把各段的 `orig` 接回來必須逐字等於原文。這道擋得住「整段消失」
   （`爱情` 的 `情` 連段落都不會出現，只靠下面第 2 道檢查是抓不到的）
2. **空讀音**：任何一段 `orig` 非空而 `hira` 為空，就是沒認出來

用法：

    uv run python scripts/kana-tts-back.py work/cards.csv            # 預覽，不寫檔
    uv run python scripts/kana-tts-back.py work/cards.csv --apply    # 寫回

寫回時會把改動列的 `audio_back_status` 設回 `pending`，接著跑
`anki-builder audio` 就只會重生這些列。重複執行是安全的：已經是純假名的列不動。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path

#: 與 `state/store.py` 一致——工作檔由它產出，寫回的位元格式必須對得上
ENCODING = "utf-8-sig"

#: 需要轉換的字：漢字（含 `々`）。純假名／羅馬字的列不必動
_NEEDS_KANA = re.compile(r"[一-鿿々]")


def to_hiragana(text: str, converter) -> tuple[str, str | None]:  # noqa: ANN001
    """回傳 `(平假名, 失敗原因)`。失敗時平假名為空字串。

    兩道檢查的理由見模組 docstring——pykakasi 對不認得的字是**靜默丟棄**，
    不檢查就會產出少字的音檔。
    """
    segments = converter.convert(text)
    if "".join(seg["orig"] for seg in segments) != text:
        return "", "往返比對不符（有字被丟棄）"
    lost = [seg["orig"] for seg in segments if seg["orig"].strip() and not seg["hira"]]
    if lost:
        return "", f"這些字沒有讀音：{'、'.join(lost)}"
    return "".join(seg["hira"] for seg in segments), None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="中間 CSV，例如 work/cards.csv")
    parser.add_argument("--apply", action="store_true", help="實際寫回（預設只預覽）")
    parser.add_argument("--limit", type=int, default=12, help="預覽列出幾筆（預設 12）")
    args = parser.parse_args()

    try:
        import pykakasi
    except ImportError:
        print("需要 pykakasi：uv sync --extra kana", file=sys.stderr)
        return 1

    if not args.work.exists():
        print(f"找不到 {args.work}", file=sys.stderr)
        return 1

    with args.work.open(encoding=ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    for column in ("tts_back_text", "audio_back_status", "card_id"):
        if column not in header:
            print(f"CSV 少了 {column} 欄", file=sys.stderr)
            return 1

    converter = pykakasi.kakasi()
    changed: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []

    for row in rows:
        text = (row.get("tts_back_text") or "").strip()
        if not row.get("card_id") or not text or not _NEEDS_KANA.search(text):
            continue
        hira, error = to_hiragana(text, converter)
        if error:
            skipped.append((row["card_id"], text, error))
            continue
        if hira == text:
            continue
        changed.append((row["card_id"], text, hira))
        if args.apply:
            row["tts_back_text"] = hira
            row["audio_back_status"] = "pending"
            row["audio_back_error"] = ""

    print(f"可轉換 {len(changed)} 列、跳過 {len(skipped)} 列（共 {len(rows)} 列）\n")
    if changed:
        print("── 轉換 ──")
        for card_id, before, after in changed[: args.limit]:
            print(f"  {card_id:9} {before}\n  {'':9} → {after}")
        if len(changed) > args.limit:
            print(f"  …其餘 {len(changed) - args.limit} 列")
    if skipped:
        print("\n── 跳過（未轉換，內容保持原樣）──")
        for card_id, text, error in skipped:
            print(f"  {card_id:9} {text}   ← {error}")

    if not args.apply:
        print("\n（預覽模式，沒有寫檔。要寫回請加 --apply）")
        return 0

    _write_atomic(args.work, header, rows)
    print(f"\n已寫回 {args.work}，{len(changed)} 列的 audio_back_status 設回 pending。")
    print("接著執行：uv run anki-builder audio")
    return 0


def _write_atomic(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    """原子寫入，比照 `state/store.py`：先寫暫存檔再 rename（約束 2）。"""
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
