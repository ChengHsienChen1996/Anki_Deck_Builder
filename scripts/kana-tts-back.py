#!/usr/bin/env python
"""把日文例句整句轉成平假名，寫進 `tts_back_text`，讓 TTS 唸對語言。

## 為什麼需要

VOXCPM2 **沒有語言參數**，語言由文字本身決定，模型看到漢字就傾向唸中文。
Phase 9 的 Task 9.5 已處理 front 側，但那條規則只在「整串都是漢字」時觸發——
**例句是漢字與假名混合，那條規則一次都不會碰到**（實測 344 張中混合 292、
純漢字僅 2）。例句要唸對，只能整句做形態素解析後轉假名。

## 為什麼是 SudachiPy 而不是 pykakasi（2026-09-07 實測）

初版用 pykakasi。它是**字典式**轉換、沒有上下文模型，多音字只能猜。
拿 334 張例句做兩個引擎的差異比對，**54 筆結果不同**：

- **SudachiPy 對、pykakasi 錯：約 24 筆。** `ご飯→ごはん`（pyk 給 `ごめし`）、
  `凡そ→およそ`（`ぼんそ`）、`神輿→みこし`（`しんよ`）、`各々→おのおの`
  （`かくかく`）、`園芸家→えんげいか`（`えんげいいえ`）、`人→ひと`（`にん`，4 處）
- **pykakasi 對、SudachiPy 錯：5 筆。** `言う→ゆう`、`卵→らん`、`就く→づく`
  ——由下方 `_OVERRIDES` 收掉
- **片假名保留：13 筆。** `シャワー` 不轉平假名。對 TTS 無害且更自然

> **這次比對還推翻了一個方法**：先前是靠人工列「多音漢字清單」去掃，
> 而那份清單漏掉了 `飯`、`名`、`凡`、`輿`、`各`、`芸`、`脅`、`植`、`降`…
> ——**兩個引擎的差異比對不依賴事先想得到什麼**，比列清單可靠。

## 來源是 `example` 的第一行，不是 `tts_back_text`

`tts_back_text` 一旦轉成假名就回不去了，重跑會拿假名再轉一次假名。
`example` 的第一行才是**日文原句**（第二行是中文釋義），所以以它為準。
實測 344 張的第一行都非空。

⚠️ **這個欄位有兩種換行寫法**：332 列是真換行、12 列是字面的 `\n`（兩個字元）。
只切真換行的話，那 12 列會把中文釋義一起送進轉換，產出
`うったえ說痛く。` 這種東西——所以 `_japanese_line()` 兩種都切。

## 為什麼不接進流程

日文限定，而通用性是專案的第一條目標（見 docs/project-overview.md）。
相依是選配：`uv sync --extra kana`。

用法：

    uv run python scripts/kana-tts-back.py work/cards.csv            # 預覽
    uv run python scripts/kana-tts-back.py work/cards.csv --apply    # 寫回
    uv run anki-builder audio                                       # 重生受影響的音檔

`--apply` 會把改動列的 `audio_back_status` 設回 `pending`。重複執行安全。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path

ENCODING = "utf-8-sig"
KANJI = re.compile(r"[一-鿿々]")
_KATA2HIRA = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})

#: SudachiPy 對這些 token 的讀音在本教材的語境下是錯的（實測，2026-09-07）。
#: **鍵是 token 的 surface**，值是正確的平假名。
#:
#: - `言う`：SudachiPy 回口語的 `ユウ`，標準讀法是 `いう`
#: - `卵`：`温泉卵` 裡回 `ラン`，應為 `たまご`
#: - `就く`：回 `ヅク`，連濁在這個語境不成立
_OVERRIDES = {"言う": "いう", "卵": "たまご", "就く": "つく"}

#: 分析器在這幾列上是錯的，**而且沒有可推廣的規則能修**——所以逐卡覆寫。
#:
#: `p2_003 幕が開く`：SudachiPy 給 `ひらく`，正確是 `あく`。不能用 surface 覆寫
#: 收掉，因為 `開く` 本身就是多音：`運河を開く` 的 `ひらく` 才是對的。
#:
#: **這張表變長就是警訊**——代表該換分析器或改用別的策略，而不是繼續往裡面加。
_CARD_OVERRIDES = {"p2_003": "まくがあく"}


def to_hiragana(text: str, tokenizer, split_mode) -> tuple[str, str | None]:  # noqa: ANN001
    """回傳 `(平假名, 失敗原因)`。失敗時平假名為空字串。

    **不含漢字的 token 原樣保留**——片假名、既有假名、標點、數字都照抄。
    片假名不轉平假名是刻意的：對 TTS 沒有差別，而保留原文更接近教材的樣子。

    兩道檢查（同 `check-card-quality.py` 的精神：寧可跳過也不要靜默產出壞資料）：

    1. **往返比對**：各 token 的 surface 接回來必須逐字等於原文
    2. **空讀音**：含漢字的 token 沒有讀音就整列放棄
    """
    parts: list[str] = []
    surfaces: list[str] = []
    for token in tokenizer.tokenize(text, split_mode):
        surface = token.surface()
        surfaces.append(surface)
        if not KANJI.search(surface):
            parts.append(surface)
            continue
        if surface in _OVERRIDES:
            parts.append(_OVERRIDES[surface])
            continue
        reading = token.reading_form()
        if not reading:
            return "", f"「{surface}」沒有讀音"
        parts.append(reading.translate(_KATA2HIRA))
    if "".join(surfaces) != text:
        return "", "往返比對不符（有字被丟棄）"
    return "".join(parts), None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="中間 CSV，例如 work/cards.csv")
    parser.add_argument("--apply", action="store_true", help="實際寫回（預設只預覽）")
    parser.add_argument("--limit", type=int, default=12, help="預覽列出幾筆")
    args = parser.parse_args()

    try:
        from sudachipy import Dictionary, SplitMode
    except ImportError:
        print("需要 SudachiPy：uv sync --extra kana", file=sys.stderr)
        return 1

    if not args.work.exists():
        print(f"找不到 {args.work}", file=sys.stderr)
        return 1

    with args.work.open(encoding=ENCODING, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    for column in ("card_id", "example", "tts_back_text", "audio_back_status"):
        if column not in header:
            print(f"CSV 少了 {column} 欄", file=sys.stderr)
            return 1

    tokenizer = Dictionary().create()
    changed: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str, str]] = []

    for row in rows:
        japanese = _japanese_line(row.get("example") or "")
        if not row.get("card_id") or not japanese:
            continue
        override = _CARD_OVERRIDES.get(row["card_id"])
        hira, error = (override, None) if override else to_hiragana(
            japanese, tokenizer, SplitMode.C
        )
        if error:
            skipped.append((row["card_id"], japanese, error))
            continue
        if hira == (row.get("tts_back_text") or "").strip():
            continue
        changed.append((row["card_id"], row.get("tts_back_text") or "", hira))
        if args.apply:
            row["tts_back_text"] = hira
            row["audio_back_status"] = "pending"
            row["audio_back_error"] = ""

    print(f"要改 {len(changed)} 列、跳過 {len(skipped)} 列（共 {len(rows)} 列）\n")
    if changed:
        print("── 改動（舊 → 新）──")
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


def _japanese_line(example: str) -> str:
    """取 `example` 的第一行——**真換行與字面 `\\n` 都要切**（見模組 docstring）。"""
    return re.split(r"\n|\\n", example, maxsplit=1)[0].strip()


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
