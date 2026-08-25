#!/usr/bin/env python
"""把既有牌組的媒體檔轉成 .env 指定的格式，並改寫中間 CSV 的路徑欄位。

新生成的媒體在階段內就已經轉好；這支腳本是給**轉檔功能上線前**做好的牌組用的——
重跑 image／audio 要花數十分鐘，而且會換掉已經看過、確認過的圖與語音。

用法：

    uv run python scripts/convert-media.py work/cards.csv
    uv run python scripts/convert-media.py work/cards.csv --dry-run
    uv run python scripts/convert-media.py work/cards.csv --keep-originals

預設會刪掉轉檔成功的原始檔（PNG／WAV 正是體積的來源，留著就白轉了）；
`--keep-originals` 可保留。已經是目標格式的檔案會被略過，重跑無害。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from anki_deck_builder.config import load_settings
from anki_deck_builder.stages.media_encode import (
    encode_audio,
    encode_image,
)
from anki_deck_builder.state import CardStore

#: CSV 欄位 → 用哪個轉檔函式
FIELDS = {
    "image_front": encode_image,
    "image_back": encode_image,
    "audio_front": encode_audio,
    "audio_back": encode_audio,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description="轉換既有牌組的媒體格式")
    parser.add_argument("work", type=Path, help="中間 CSV 路徑，例如 work/cards.csv")
    parser.add_argument("--dry-run", action="store_true", help="只統計，不寫任何檔案")
    parser.add_argument(
        "--keep-originals", action="store_true", help="保留轉檔前的 PNG／WAV"
    )
    args = parser.parse_args()

    media = load_settings().media
    store = CardStore(args.work)
    if not store.exists():
        print(f"找不到工作檔：{args.work}")
        return 1

    rows = await store.read()
    # 必須 resolve：來源路徑是絕對的，root 若停在相對路徑，
    # 回寫 CSV 時的 relative_to() 會直接拋 ValueError（實測踩到）
    root = Path(store.path).parent.resolve()
    print(
        f"目標格式：影像 {media.image_format}（品質 {media.image_quality}）、"
        f"語音 {media.audio_format}（壓縮 {media.audio_compression}）"
        + ("（dry-run）" if args.dry_run else "")
    )

    converted = skipped = missing = 0
    before = after = 0
    failures: list[str] = []

    for row in rows:
        for field, encode in FIELDS.items():
            value = getattr(row, field)
            if not value:
                continue
            source = (root / value).resolve()
            if not source.is_file():
                missing += 1
                continue

            data, extension = encode(source.read_bytes(), media)
            if source.suffix.lstrip(".").lower() == extension:
                skipped += 1
                continue

            target = source.with_suffix(f".{extension}")
            before += source.stat().st_size
            after += len(data)
            converted += 1
            if args.dry_run:
                continue

            try:
                target.write_bytes(data)
            except OSError as error:
                failures.append(f"{row.card_id} {field}：{error}")
                continue
            setattr(row, field, str(target.relative_to(root)))
            if not args.keep_originals:
                source.unlink()

    if converted and not args.dry_run:
        await store.write(rows)

    print(
        f"轉換 {converted} 個檔案"
        f"（已是目標格式 {skipped}、CSV 有登記但檔案不存在 {missing}）"
    )
    if converted:
        print(
            f"體積 {before / 2**20:.0f} MB → {after / 2**20:.0f} MB"
            f"（{after / before:.1%}）"
        )
    for line in failures:
        print(f"  ⚠️ {line}")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
