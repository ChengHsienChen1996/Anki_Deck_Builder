#!/usr/bin/env python
"""A/B：重拍的照片能不能修好 OCR 讀不出小寫假名與濁點的問題。

## 要回答的問題

2026-09-11 量到：65 筆讀音錯誤中 **94% 的正確讀音從未出現在 `raw_text`**，
也就是 OCR 就沒讀到。而且錯誤形態高度集中——**小寫假名被讀成大寫**
（`きゆう` ← `きゅう`）與**濁點消失**（`きょきょう` ← `ぎょぎょう`）。

問題是：這是照片的細節不夠，還是 GLM-OCR 在這個尺度的能力上限？
**現有資料證不出來**——41 頁的糊邊比落在 0.48–0.69 的窄帶內，
頁與頁之間的品質差異預測不了錯誤數（r = −0.16 ~ −0.28，而且最銳利的
1/3 平均錯誤還比最模糊的 1/3 多）。資料裡沒有「好照片」可以對照。

所以要人工重拍幾頁，做一次有對照組的實驗。

## 為什麼要有控制組

不能拿新照片的輸出去比**存在 cards.csv 裡的舊 `raw_text`**——那樣會把
「照片變好」與「這次呼叫剛好不同」混在一起。本腳本一律**同時重跑舊照片**，
兩邊都是新鮮的呼叫，差異才只剩照片本身。

## 不動正式工作檔

實驗自己建一份只含受測頁的工作檔（`--out` 目錄底下），`work/cards.csv`
一個位元組都不會改。

## 用法

    # 1. 重拍。條件見 .agent/plans/photo-quality-ab.md
    # 2. 跑實驗（新照片放哪裡就指哪裡）
    uv run python scripts/ab-photo-quality.py \
        --page 22=/path/to/new_p22.jpg \
        --page 19=/path/to/new_p19.jpg

    # 只想先看照片本身有沒有拍得比較好（不呼叫 OCR）
    uv run python scripts/ab-photo-quality.py --page 22=... --measure-only

相依：`uv sync --all-extras`（版面偵測與 OCR 都要）。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

#: 拍攝條件的目標值，取自 2026-09-11 對 41 頁的量測。
#: `p41` 是同一批裡唯一達標的一頁，證明這些數字拍得出來。
TARGET_PAPER = 240.0     # 紙面亮度（0–255）。全批中位數只有 190，偏灰
TARGET_CONTRAST = 170.0  # 紙面亮度 − 筆畫暗度。全批中位數 130，p41 是 199


def parse_pages(values: list[str]) -> dict[int, Path]:
    """`--page 22=/path/to.jpg` → `{22: Path(...)}`。"""
    out: dict[int, Path] = {}
    for item in values:
        page, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"--page 要寫成 頁碼=影像路徑，收到：{item!r}")
        image = Path(path).expanduser()
        if not image.is_file():
            raise SystemExit(f"找不到影像：{image}")
        out[int(page)] = image
    return out


# ── 影像量測（與 2026-09-11 的分析同一套指標）────────────────────


def measure(path: Path) -> dict[str, float]:
    """紙面亮度、筆畫暗度、對比、文字區銳利度。

    只取版心中央 40%：避開頁緣、書溝與入鏡的手指，那些會把統計值拉歪。
    """
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        array = np.asarray(im.convert("L"), dtype=np.float64)
    height, width = array.shape
    crop = array[int(height * 0.30):int(height * 0.70),
                 int(width * 0.30):int(width * 0.70)]
    laplacian = (crop[:-2, 1:-1] + crop[2:, 1:-1]
                 + crop[1:-1, :-2] + crop[1:-1, 2:] - 4 * crop[1:-1, 1:-1])
    paper = float(np.percentile(crop, 95))
    ink = float(np.percentile(crop, 2))
    return {
        "megapixels": width * height / 1e6,
        "paper": paper,
        "ink": ink,
        "contrast": paper - ink,
        "sharpness": float(laplacian.var()),
    }


def print_measurements(pages: dict[int, Path], old: dict[int, str]) -> None:
    print("── 照片本身 ──")
    header = f"{'頁':5} {'':4} {'MP':>5} {'紙面':>6} {'筆畫':>6} {'對比':>6} {'銳利度':>8}"
    print(header)
    for page, new_image in sorted(pages.items()):
        for label, path in (("舊", Path(old[page])), ("新", new_image)):
            m = measure(path)
            flags = ""
            if label == "新":
                if m["paper"] < TARGET_PAPER:
                    flags += f"  ⚠ 紙面未達 {TARGET_PAPER:.0f}"
                if m["contrast"] < TARGET_CONTRAST:
                    flags += f"  ⚠ 對比未達 {TARGET_CONTRAST:.0f}"
            print(f"p{page:<4} {label:4} {m['megapixels']:5.1f} {m['paper']:6.0f} "
                  f"{m['ink']:6.0f} {m['contrast']:6.0f} {m['sharpness']:8.1f}{flags}")
    print()


# ── 目標讀音：這些頁目前錯在哪裡 ─────────────────────────────────


def target_readings(work: Path, pages: set[int]) -> dict[int, list[tuple[str, str, str]]]:
    """跑 `check-card-quality.py`，取出受測頁的 marks 類錯誤。

    回傳 `{頁: [(card_id, 卡片現值, 應為), ...]}`——「應為」就是這次要看
    OCR 讀不讀得出來的目標字串。
    """
    result = subprocess.run(
        ["uv", "run", "python", str(REPO / "scripts" / "check-card-quality.py"), str(work)],
        capture_output=True, text=True, cwd=REPO,
    )
    out: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    section = None
    for line in result.stdout.splitlines():
        head = re.match(r"── (\w+)：", line)
        if head:
            section = head.group(1)
            continue
        if section != "marks":
            continue
        row = re.match(r"\s+(p(\d+)_\d+)\s+\S+\s+卡片 (\S+)\s+分析器 (\S+)", line)
        if row:
            card_id, page, got, want = row.groups()
            if int(page) in pages:
                out[int(page)].append((card_id, got, want))
    return out


# ── 實驗本體 ─────────────────────────────────────────────────────


def build_work_file(path: Path, sources: dict[int, Path]) -> None:
    """建一份只含受測頁 OCR 列的工作檔。"""
    from anki_deck_builder.schemas import CardRow

    fields = list(CardRow.field_order())
    index = {name: i for i, name in enumerate(fields)}
    rows = [fields]
    for page, image in sorted(sources.items()):
        row = [""] * len(fields)
        row[index["source"]] = str(image)
        row[index["ocr_source_page"]] = str(page)
        for stage in ("ocr", "extract", "scene", "prompt",
                      "image", "audio_front", "audio_back"):
            row[index[f"{stage}_status"]] = "pending"
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)


def run_ocr(work: Path) -> None:
    subprocess.run(["uv", "run", "anki-builder", "ocr", "--work", str(work)],
                   cwd=REPO, check=True)


def read_raw_text(work: Path) -> dict[int, str]:
    with work.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    head = {name: i for i, name in enumerate(rows[0])}
    return {int(r[head["ocr_source_page"]]): r[head["raw_text"]] for r in rows[1:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--page", action="append", required=True, metavar="N=PATH",
                        help="受測頁與重拍後的影像，可重複給")
    parser.add_argument("--work", default="work/cards.csv", metavar="PATH",
                        help="基準工作檔（只讀，用來取舊影像路徑與目標讀音）")
    parser.add_argument("--out", default="work/ab-photo-quality", metavar="DIR",
                        help="實驗檔輸出目錄")
    parser.add_argument("--measure-only", action="store_true",
                        help="只量測照片，不呼叫 OCR")
    args = parser.parse_args()

    from anki_deck_builder.state.store import CardStore

    pages = parse_pages(args.page)
    work = REPO / args.work
    out_dir = REPO / args.out

    rows = asyncio.run(CardStore(work).read())
    old_sources = {r.ocr_source_page: r.source for r in rows if r.source}
    missing = sorted(set(pages) - set(old_sources))
    if missing:
        raise SystemExit(f"{work} 裡沒有這些頁：{missing}")

    print(f"受測頁：{sorted(pages)}　基準：{work}\n")
    print_measurements(pages, old_sources)
    if args.measure_only:
        print("（--measure-only：未呼叫 OCR）")
        return 0

    targets = target_readings(work, set(pages))
    total_targets = sum(len(v) for v in targets.values())
    if not total_targets:
        print("這些頁目前沒有 marks 類錯誤，沒有可比的目標。換幾頁再試。")
        return 1

    # 控制組跑舊照片、實驗組跑新照片。兩邊都是新鮮呼叫，差異才只剩照片
    arms = {
        "control": {p: Path(old_sources[p]) for p in pages},
        "treatment": pages,
    }
    raw: dict[str, dict[int, str]] = {}
    for arm, sources in arms.items():
        work_file = out_dir / f"{arm}.csv"
        build_work_file(work_file, sources)
        print(f"── 跑 OCR（{arm}）──")
        run_ocr(work_file)
        raw[arm] = read_raw_text(work_file)
        print()

    print("── 結果：目標讀音有沒有出現在 raw_text ──")
    score: Counter[str] = Counter()
    for page in sorted(targets):
        print(f"  p{page}（舊 {len(raw['control'][page])} 字元 → "
              f"新 {len(raw['treatment'][page])} 字元）")
        for card_id, got, want in targets[page]:
            in_control = want in raw["control"][page]
            in_treatment = want in raw["treatment"][page]
            score[(in_control, in_treatment)] += 1
            mark = {
                (False, True): "✅ 重拍後讀出來了",
                (True, False): "⚠️  重拍後反而不見",
                (True, True): "－ 兩邊都讀得到（本來就不是照片問題）",
                (False, False): "❌ 兩邊都讀不到",
            }[(in_control, in_treatment)]
            print(f"    {card_id} {got} → 應為 {want:10} {mark}")
        print()

    fixed = score[(False, True)]
    broke = score[(True, False)]
    stuck = score[(False, False)]
    print("── 判讀 ──")
    print(f"  重拍後修好      {fixed}/{total_targets}")
    print(f"  重拍後反而壞掉  {broke}/{total_targets}")
    print(f"  兩邊都讀不到    {stuck}/{total_targets}")
    print()
    if fixed > total_targets / 2:
        print("  → 照片是主因。值得把全部 41 頁依同樣條件重拍後重跑 ocr。")
    elif fixed == 0:
        print("  → 照片不是主因，是 GLM-OCR 在這個尺度的上限。**不要重拍**，")
        print("     改從別處補救（verify 對照影像、或在抽取端加假名拼寫檢查）。")
    else:
        print("  → 部分有效。重拍 41 頁的成本對上這個修復率，值不值得由你判斷；")
        print("     也可以只重拍錯誤集中的那幾頁。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
