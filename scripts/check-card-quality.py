#!/usr/bin/env python
"""掃描中間 CSV 的資料品質問題，**只報告、不修改**。

這支工具把 2026-09-07 那次人工排查用過的五個一次性檢查收攏成可重跑的東西。
那次全部的發現都來自臨時寫的 inline script，換一批教材就得重寫一遍——
而每一項的實際範圍都是初估的兩到三倍，靠隨機抽樣是估不出來的
（隨機 40 筆全對，實際錯誤率 5%）。**低頻錯誤要按「可能出錯的結構」分層掃。**

## 檢查項目

| 檢查 | 抓什麼 | 可自動修嗎 |
|------|--------|-----------|
| `glyph` | 只存在於簡體的字形（`爱`、`暧`、`现`…） | **否**——正確形態取決於欄位是日文還是中文 |
| `bytes` | 轉義失敗的無效位元組（`<0xE9><0xA3><0xA3>`） | 多半是（原字的 UTF-8 編碼被印成字面） |
| `marks` | `reading` 缺濁點或小寫假名（`いつち` 應為 `いっち`） | 建議人工確認 |
| `fields` | `reading` 與 `tts_front_text` 都是假名卻不一致 | **否**——要判斷哪個對 |
| `reading` | 例句假名裡找不到詞條的 `reading` | **否**——多音字要看語境 |

## 兩個掃描時容易誤判的地方（實測踩過）

1. **`那` 是正常繁體字**（`那裡`、`那麼`），不是簡體
2. **`学`／`国`／`医` 是日文新字體**——在日文欄位是正確的，只有在中文釋義欄位
   才該是 `學`／`國`／`醫`

因此本工具的簡體字表**只收「日文與繁中都不用」的字**，寧可漏抓不要誤報。

用法：

    uv run python scripts/check-card-quality.py work/cards.csv
    uv run python scripts/check-card-quality.py work/cards.csv --only glyph fields

`marks` 與 `reading` 兩項需要形態素分析器（`uv sync --all-extras`），
沒裝就自動跳過並說明。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ENCODING = "utf-8-sig"

#: 只存在於簡體、日文與繁中都不用的字 → 正確形態
#: **刻意保守**：寧可漏抓也不要把 `那`、`学`、`国`、`医` 這類誤報進來
SIMPLIFIED = {
    "爱": "愛", "暧": "曖", "顽": "頑", "现": "現", "诉": "訴", "遗": "遺",
    "马": "馬", "圆": "圓", "员": "員", "东": "東", "车": "車", "书": "書",
    "问": "問", "题": "題", "时": "時", "说": "說", "语": "語", "读": "讀",
    "边": "邊", "过": "過", "进": "進", "还": "還", "关": "關", "门": "門",
}

#: 日文側該用的字形（繁中字形出現在日文欄位時）
JP_GLYPH = {"雨戶": "雨戸"}

TEXT_FIELDS = ("front", "back", "hint", "example", "tts_front_text", "tts_back_text", "note")
KANJI = re.compile(r"[一-鿿々]")
_KATA2HIRA = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})


def _to_hira(text: str) -> str:
    """片假名轉平假名，只為了比對用（`アウト` 與 `あうと` 是同一個音）。"""
    return text.translate(_KATA2HIRA)

KANA_ONLY = re.compile(r"^[぀-ゟ゠-ヿー]+$")
BAD_BYTES = re.compile(r"<0x[0-9A-Fa-f]{2}>")
SEPARATOR = re.compile(r"[/／]")


def check_glyph(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        for field in TEXT_FIELDS:
            value = row.get(field) or ""
            bad = sorted({c for c in value if c in SIMPLIFIED})
            if bad:
                fix = "".join(SIMPLIFIED[c] for c in bad)
                out.append(f"{row['card_id']:9} {field:15} [{''.join(bad)}→{fix}] {value[:56]}")
            for wrong, right in JP_GLYPH.items():
                if wrong in value:
                    out.append(f"{row['card_id']:9} {field:15} [{wrong}→{right}] {value[:56]}")
    return out


def check_bytes(rows: list[dict]) -> list[str]:
    return [
        f"{row['card_id']:9} {field:15} {(row.get(field) or '')[:60]}"
        for row in rows
        for field in TEXT_FIELDS
        if BAD_BYTES.search(row.get(field) or "")
    ]


def check_fields(rows: list[dict]) -> list[str]:
    """`reading` 與 `tts_front_text` 都是純假名卻不同。

    兩者由 extract 各自產生、互不驗證，而 `audio` 的救援只在文字「全是漢字」時
    觸發——`tts_front_text` 已是假名但唸錯時沒有任何機制會發現。
    """
    out = []
    for row in rows:
        tts = (row.get("tts_front_text") or "").strip()
        reading = SEPARATOR.split((row.get("reading") or "").strip(), maxsplit=1)[0].strip()
        if not tts or not reading:
            continue
        if KANA_ONLY.match(tts) and KANA_ONLY.match(reading) and tts != reading:
            out.append(f"{row['card_id']:9} {row['front']:12} reading={reading:14} tts_front={tts}")
    return out


def check_marks(rows: list[dict], analyse) -> list[str]:  # noqa: ANN001
    """`reading` 與分析器只差濁點／小寫假名 → 幾乎必然是卡片這側錯（OCR 讀漏）。"""
    import unicodedata

    small = str.maketrans("っゃゅょぁぃぅぇぉ", "つやゆよあいうえお")

    def flatten(text: str) -> str:
        stripped = unicodedata.normalize("NFD", text)
        stripped = "".join(c for c in stripped if not unicodedata.combining(c))
        return stripped.translate(small)

    out = []
    for row in rows:
        front = (row.get("front") or "").strip()
        reading = SEPARATOR.split((row.get("reading") or "").strip(), maxsplit=1)[0].strip()
        if not KANJI.search(front) or not KANA_ONLY.match(reading):
            continue
        auto = analyse(front)
        if not auto or auto == reading:
            continue
        if flatten(auto) == flatten(reading):
            out.append(f"{row['card_id']:9} {front:12} 卡片 {reading:14} 分析器 {auto}")
    return out


def check_reading(rows: list[dict], analyse) -> list[str]:  # noqa: ANN001
    """詞條的 `reading` 應該出現在例句的假名裡；找不到就是多音字選錯的候選。

    只在「詞條本身出現在例句中」時才驗——否則詞條可能根本不在那句裡。
    """
    out = []
    for row in rows:
        front = (row.get("front") or "").strip()
        back = (row.get("tts_back_text") or "").strip()
        reading = SEPARATOR.split((row.get("reading") or "").strip(), maxsplit=1)[0].strip()
        example = (row.get("example") or "").split("\n")[0].strip()
        if not (front and back and reading and example):
            continue
        if not KANA_ONLY.match(reading) or front not in example:
            continue
        if KANJI.search(back):        # 還沒轉成假名的列不驗
            continue
        # **比對前要把片假名正規化成平假名。** `reading` 常保留片假名
        # （`アウト`），而轉換後的例句是平假名（`あうと`）——那是同一個音，
        # 不正規化就會產生一批純粹的雜訊，把真正的多音字錯誤淹掉
        if _to_hira(reading) not in _to_hira(back):
            out.append(f"{row['card_id']:9} {front:12} 期望 {reading:14} 例句 {back[:34]}")
    return out


def _analyser():
    """回傳 `front → 平假名` 的函式；沒裝分析器時回傳 None。"""
    try:
        from sudachipy import Dictionary, SplitMode
    except ImportError:
        return None
    tokenizer = Dictionary().create()
    kata2hira = str.maketrans({chr(c): chr(c - 0x60) for c in range(0x30A1, 0x30F7)})

    def analyse(text: str) -> str | None:
        parts = []
        for token in tokenizer.tokenize(text, SplitMode.C):
            surface = token.surface()
            if not KANJI.search(surface):
                parts.append(surface)
                continue
            reading = token.reading_form()
            if not reading:
                return None
            parts.append(reading.translate(kata2hira))
        return "".join(parts)

    return analyse


CHECKS = ("glyph", "bytes", "marks", "fields", "reading")
TITLES = {
    "glyph": "簡體／異體字形（正確形態視欄位是日文還是中文而定，不要盲目取代）",
    "bytes": "轉義失敗的無效位元組",
    "marks": "reading 缺濁點或小寫假名（卡片這側幾乎必然是錯的）",
    "fields": "reading 與 tts_front_text 不一致（要判斷哪個對）",
    "reading": "例句假名裡找不到詞條的 reading（多音字選錯的候選）",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="中間 CSV，例如 work/cards.csv")
    parser.add_argument("--only", nargs="+", choices=CHECKS, help="只跑這幾項")
    args = parser.parse_args()

    if not args.work.exists():
        print(f"找不到 {args.work}", file=sys.stderr)
        return 1

    with args.work.open(encoding=ENCODING, newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("card_id")]

    wanted = args.only or list(CHECKS)
    analyse = _analyser() if {"marks", "reading"} & set(wanted) else None

    total = 0
    for name in wanted:
        if name in ("marks", "reading"):
            if analyse is None:
                print(f"── {name}：跳過，需要形態素分析器（uv sync --all-extras）\n")
                continue
            found = (check_marks if name == "marks" else check_reading)(rows, analyse)
        else:
            found = {"glyph": check_glyph, "bytes": check_bytes, "fields": check_fields}[name](rows)
        total += len(found)
        print(f"── {name}：{len(found)} 筆　{TITLES[name]}")
        for line in found:
            print(f"   {line}")
        print()

    print(f"共 {len(rows)} 張卡，{total} 筆待確認。**本工具只報告，不修改任何東西。**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
