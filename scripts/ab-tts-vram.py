#!/usr/bin/env python
"""A/B：`VOXCPM2_OPTIMIZE` 是不是語音生成 VRAM 越跑越高的原因。

## 要回答的問題

2026-09-12 實測：`serve` 長駐行程跑了 1h13m 之後**單獨佔 22.6 GB／24 GB**，
而 ComfyUI 沒在跑、Ollama 也沒載模型，VOXCPM2 峰值應該只有約 7.5 GB。
那一輪語音幾乎全滅——`audio_front` 425 列、`audio_back` 603 列 CUDA OOM。

**不是 batch size 的問題**：TTS 那側只有 `concurrency`，預設就是 1，
而且 `config.py` 註明「本行程內的 GPU 推論本就序列化，設 1 以外的值不會更快」。

所以顯存是**跨多次生成累積**的。兩個候選機制：

1. PyTorch 的 caching allocator 跨數百次生成不把記憶體還給驅動程式
2. `VOXCPM2_OPTIMIZE=true` 的 `torch.compile` 對**每一種輸入長度**重新編譯，
   編譯產物堆積

兩者的處置完全不同（前者加 `release_gpu_cache()`，後者關掉 optimize），
所以要分開來量。

## 怎麼分辨

本腳本量兩條曲線，每合成一段就取樣一次：

- `torch.cuda.memory_allocated()`：**活著的**張量。模型權重以外不該持續長大
- `torch.cuda.memory_reserved()`：allocator 跟驅動要走的總量。**這才是
  nvidia-smi 看到的數字**，也是 OOM 的判準

判讀：

| 現象 | 結論 |
|---|---|
| `optimize=false` 的 reserved 明顯平坦、true 持續爬 | **是 torch.compile**，關掉即可 |
| 兩邊都持續爬 | **是 allocator**，要在 `audio.py` 迴圈裡定期 `empty_cache()` |
| 兩邊都平坦 | 累積另有來源，本實驗證不出，別急著改 |

`allocated` 平坦而 `reserved` 一路爬，是 allocator 碎片化的典型指紋。

## 為什麼輸入要取自實產資料

`torch.compile` 的重編譯是**依輸入形狀**觸發的，而形狀來自文字長度。
拿同一句話重複跑會讓兩組都平坦，把真正的差異測掉。因此預設從 `work/cards.csv`
的 `tts_back_text` 取樣（長度分布就是實際負載），且**兩組用同一批文字、同一個
順序**——否則差異可能只是輸入不同。

## 不動任何工作檔

只讀 `work/cards.csv` 取文字，不寫。輸出是 `work/ab-tts-vram/` 底下的 CSV。

用法：

    # 兩組都跑（各自獨立行程，編譯快取不互相汙染）
    uv run python scripts/ab-tts-vram.py --samples 40

    # 只跑其中一組（`--report` 之後再合併判讀）
    uv run python scripts/ab-tts-vram.py --samples 40 --optimize true
    uv run python scripts/ab-tts-vram.py --report

⚠️ **跑之前 GPU 要是空的。** 這支腳本會載入 VOXCPM2（約 7.5 GB 峰值），
`serve` 長駐行程若還佔著顯存就會直接 OOM——先停掉它。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

ARMS = ("true", "false")


def sample_texts(work: Path, count: int) -> list[str]:
    """從實產資料取文字，**依長度均勻取樣**。

    照順序取前 N 筆會拿到同一頁的同類句子，長度分布被壓窄——而長度變化正是
    要測的變因。所以先依長度排序再等距抽，確保短句與長句都在裡面。
    """
    with work.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("card_id") or "").strip()]
    texts = sorted({(r.get("tts_back_text") or "").strip() for r in rows} - {""}, key=len)
    if not texts:
        raise SystemExit(f"{work} 裡沒有 tts_back_text，取不到輸入")
    if count >= len(texts):
        return texts
    step = len(texts) / count
    return [texts[int(i * step)] for i in range(count)]


async def run_arm(optimize: bool, texts: list[str], out: Path) -> None:
    """在**本行程**內跑一組，每合成一段取樣一次顯存。"""
    import torch

    from anki_deck_builder.clients.tts_client import VoxCPMClient
    from anki_deck_builder.config import load_settings

    settings = load_settings()
    tts = settings.tts.model_copy(update={"optimize": optimize})
    client = VoxCPMClient(tts)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index", "chars", "allocated_mb", "reserved_mb", "error"])
        for index, text in enumerate(texts, start=1):
            error = ""
            try:
                await client.synthesize(text)
            except Exception as exc:  # noqa: BLE001 - 失敗也要記下來再繼續
                error = f"{type(exc).__name__}: {exc}"[:120]
            allocated = torch.cuda.memory_allocated() / 2**20
            reserved = torch.cuda.memory_reserved() / 2**20
            writer.writerow([index, len(text), f"{allocated:.0f}", f"{reserved:.0f}", error])
            fh.flush()
            mark = f"  ⚠ {error}" if error else ""
            print(f"  [{'optimize' if optimize else 'plain':8}] "
                  f"{index:3}/{len(texts)}  {len(text):3} 字  "
                  f"allocated {allocated:7.0f} MB  reserved {reserved:7.0f} MB{mark}",
                  flush=True)


def report(out_dir: Path) -> int:
    """把兩組的曲線並排，給判讀。"""
    curves: dict[str, list[tuple[int, float, float]]] = {}
    for arm in ARMS:
        path = out_dir / f"optimize-{arm}.csv"
        if not path.is_file():
            print(f"缺少 {path}，先跑那一組", file=sys.stderr)
            return 1
        with path.open(encoding="utf-8", newline="") as fh:
            curves[arm] = [
                (int(r["index"]), float(r["allocated_mb"]), float(r["reserved_mb"]))
                for r in csv.DictReader(fh)
            ]

    print("\n── 顯存曲線（MB）──")
    print(f"{'第幾段':>6} {'optimize=true':>28} {'optimize=false':>28}")
    print(f"{'':6} {'allocated':>13}{'reserved':>15} {'allocated':>13}{'reserved':>15}")
    rows = min(len(curves["true"]), len(curves["false"]))
    marks = sorted({1, rows // 4, rows // 2, rows * 3 // 4, rows} - {0})
    for i in marks:
        t, f = curves["true"][i - 1], curves["false"][i - 1]
        print(f"{i:6} {t[1]:13.0f}{t[2]:15.0f} {f[1]:13.0f}{f[2]:15.0f}")

    print("\n── 判讀 ──")
    verdicts = {}
    for arm in ARMS:
        c = curves[arm]
        first, last = c[0], c[-1]
        grow_alloc = last[1] - first[1]
        grow_res = last[2] - first[2]
        peak_res = max(x[2] for x in c)
        verdicts[arm] = (grow_alloc, grow_res, peak_res)
        print(f"  optimize={arm:5}  reserved 第 1 段 {first[2]:.0f} → 最後一段 {last[2]:.0f} MB"
              f"（成長 {grow_res:+.0f}）　峰值 {peak_res:.0f} MB　"
              f"allocated 成長 {grow_alloc:+.0f} MB")

    gt, gf = verdicts["true"][1], verdicts["false"][1]
    flat = 200.0   # MB。低於此視為平坦——單段生成的暫存本來就有數十 MB 的抖動
    print()
    if gt > flat and gf <= flat:
        print("  → **是 torch.compile。** 關掉 VOXCPM2_OPTIMIZE 即可，不必改程式。")
    elif gt > flat and gf > flat:
        print("  → **是 allocator 累積**，與 optimize 無關。正解是在 audio.py 的迴圈裡")
        print("     每 N 筆呼叫一次 release_gpu_cache()（tts_client.py:267）。")
    elif gt <= flat and gf <= flat:
        print("  → 兩組都平坦，**本實驗證不出累積**。別急著改程式——")
        print("     先確認那 22.6 GB 是不是另有來源（例如同行程還跑過別的階段）。")
    else:
        print("  → optimize=false 反而長得比較快，與假說相反。重跑確認不是雜訊。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=40, help="每組合成幾段")
    parser.add_argument("--work", default="work/cards.csv", help="取輸入文字的工作檔（唯讀）")
    parser.add_argument("--out", default="work/ab-tts-vram", help="輸出目錄")
    parser.add_argument("--optimize", choices=ARMS,
                        help="只跑這一組（內部用；不給就兩組都跑）")
    parser.add_argument("--report", action="store_true", help="只合併判讀既有結果")
    args = parser.parse_args()

    out_dir = REPO / args.out
    if args.report:
        return report(out_dir)

    work = REPO / args.work
    texts = sample_texts(work, args.samples)

    if args.optimize:      # 子行程：實際跑一組
        asyncio.run(run_arm(args.optimize == "true", texts,
                            out_dir / f"optimize-{args.optimize}.csv"))
        return 0

    lengths = [len(t) for t in texts]
    print(f"輸入：{len(texts)} 段，長度 {min(lengths)}–{max(lengths)} 字"
          f"（中位數 {sorted(lengths)[len(lengths) // 2]}）")
    print("兩組用同一批文字、同一個順序。\n")
    for arm in ARMS:
        print(f"── optimize={arm} ──", flush=True)
        # 各自獨立行程：torch.compile 的快取不會跨組汙染，模型也重新載入
        result = subprocess.run(
            [sys.executable, __file__, "--samples", str(args.samples),
             "--work", args.work, "--out", args.out, "--optimize", arm],
            cwd=REPO, env={**os.environ, "VOXCPM2_OPTIMIZE": arm},
        )
        if result.returncode != 0:
            print(f"optimize={arm} 這組失敗，中止", file=sys.stderr)
            return result.returncode
        print()
    return report(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
